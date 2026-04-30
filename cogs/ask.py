# cogs/ask.py
import json
import logging
import re
import asyncio
from collections import deque
from urllib.parse import urlparse

import aiohttp
from discord.ext import commands
from ddgs import DDGS

from config import (
    LEAGUE_NAME_MAP,
    LEAGUE_SLUG_MAP,
    LLM_API_KEY,
    LLM_BASE_URL,
    LLM_MODEL,
    LLM_SYSTEM_PROMPT,
    TRUSTED_SPORT_DOMAINS,
    WEB_SEARCH_MIN_TRUSTED_RESULTS,
    build_league_slugs,
)
from utils.time_utils import italy_now, parse_utc_to_italy
from modules import api_provider
from modules.discord_poster import post_new_message_to_context
from modules.football_memory import (
    load_memory,
    check_memory_staleness,
    get_league_standings,
    get_team_info,
)
from utils.espn_client import (
    fetch_next_team_fixture_espn,
    search_team_espn,
)

logger = logging.getLogger(__name__)

_TRACKED_SLUGS = set(LEAGUE_SLUG_MAP.values())
_HISTORY_MAXLEN = 10
_MAX_TOOL_ROUNDS = 6
_MAX_WEB_RESULTS = 5

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for up-to-date football information. "
                "Use trusted football news domains first, then broad web if needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
                    "domain_mode": {
                        "type": "string",
                        "enum": ["trusted_first", "trusted_only", "broad_only"],
                        "description": "Domain strategy for search. Prefer trusted_first.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_todays_fixtures",
            "description": "Get today's tracked football fixtures (live and upcoming) from the bot's live feed.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_match",
            "description": "Find the next scheduled match for any football team.",
            "parameters": {
                "type": "object",
                "properties": {
                    "team_name": {"type": "string", "description": "Name of the team, e.g. 'AC Milan'"},
                },
                "required": ["team_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory",
            "description": "Retrieve stored facts (standings, team stats, players) from bot memory. Use this FIRST for factual questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_type": {
                        "type": "string",
                        "enum": ["league", "team"],
                        "description": "Type of entity to retrieve from memory.",
                    },
                    "entity_name": {
                        "type": "string",
                        "description": "Name of the league or team, e.g. 'Serie A' or 'AC Milan'.",
                    },
                },
                "required": ["entity_type", "entity_name"],
            },
        },
    },
]


class Ask(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._history: dict[int, deque] = {}

    @commands.command(
        name="ask",
        help="Ask the bot a question. The LLM uses web search and includes sources.",
    )
    @commands.cooldown(3, 60, commands.BucketType.user)
    async def ask(self, ctx: commands.Context, *, question: str):
        history = self._history.setdefault(ctx.channel.id, deque(maxlen=_HISTORY_MAXLEN))
        async with ctx.typing():
            reply = await self._run_llm(question, history)
        reply = self._suppress_preview_links(reply)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": reply})
        await post_new_message_to_context(ctx, content=reply)

    @commands.command(
        name="refresh_memory",
        help="Admin: Force update all football memory (standings, teams, players).",
    )
    @commands.is_owner()
    async def refresh_memory(self, ctx: commands.Context):
        from modules.football_memory import update_all_memory
        async with ctx.typing():
            await update_all_memory(self.bot.http_session)
        await post_new_message_to_context(ctx, content="✅ Football memory refreshed.")

    @commands.command(
        name="dump_memory",
        help="Admin: Export football memory to a file and post it.",
    )
    @commands.is_owner()
    async def dump_memory(self, ctx: commands.Context):
        import discord
        from pathlib import Path
        memory = load_memory()
        dump_path = Path("bot_memory/football_memory_dump.json")
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dump_path, "w", encoding="utf-8") as f:
            import json
            json.dump(memory, f, indent=2, ensure_ascii=False)
        await ctx.send(file=discord.File(dump_path, filename="football_memory.json"))
        dump_path.unlink(missing_ok=True)  # Clean up

    async def cog_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandOnCooldown):
            await post_new_message_to_context(
                ctx,
                content=f"`!ask` is on cooldown. Try again in {error.retry_after:.0f}s.",
            )
            return
        raise error

    async def _run_llm(self, question: str, history: deque) -> str:
        if not LLM_API_KEY:
            return "⚠️ LLM_API_KEY is not set. Add it to your .env file."

        # --- Memory Integration ---
        memory = load_memory()
        staleness_warning = check_memory_staleness(memory)
        memory_context = self._format_memory_context(question, memory)

        today = italy_now().strftime("%A, %B %d, %Y")
        system_content = (
            f"{LLM_SYSTEM_PROMPT}\n"
            f"Today's date is {today}.\n"
            f"{memory_context}\n"
            "Rules: "
            "1. Use memory data FIRST if available (prefer memory over web_search for standings, team stats, player stats). "
            "2. If memory is missing, use web_search or other tools. "
            "3. Always end factual answers with a 'Sources:' line using 1-3 links/domains from retrieved results. "
            "4. If memory is stale, warn the user in your answer. "
            "5. If you see 'FINAL ANSWER FROM MEMORY' in a tool response, STOP immediately and use that data as your final answer. DO NOT CALL ANY MORE TOOLS."
        )

        # Add staleness warning to system prompt if needed
        if staleness_warning:
            system_content += f"\n\n{staleness_warning}"

        messages = [
            {"role": "system", "content": system_content},
            *list(history),
            {"role": "user", "content": question},
        ]
        headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        collected_sources: list[dict] = []

        try:
            session = self.bot.http_session
            for round_num in range(_MAX_TOOL_ROUNDS):
                payload = {
                    "model": LLM_MODEL,
                    "messages": messages,
                    "tools": TOOLS,
                }
                async with session.post(
                    f"{LLM_BASE_URL}/chat/completions",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 401:
                        return "⚠️ LLM API key is invalid. Check LLM_API_KEY in your .env."
                    if resp.status == 429:
                        return "⚠️ LLM rate limit hit. Try again in a moment."
                    if resp.status != 200:
                        text = await resp.text()
                        return f"⚠️ LLM API error (HTTP {resp.status}): {text[:200]}"
                    data = await resp.json()

                msg = data["choices"][0]["message"]
                tool_calls = msg.get("tool_calls") or []

                if not tool_calls:
                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(b["text"] for b in content if b.get("type") == "text")
                    content = re.sub(r'\s*\{["\w][^{}]*\}\.?', '', (content or "")).strip()

                    if collected_sources:
                        return self._attach_sources(content, collected_sources)
                    return content

                messages.append(msg)
                for tc in tool_calls:
                    args = tc["function"].get("arguments", "{}")
                    if isinstance(args, str):
                        args = json.loads(args)
                    result = await self._execute_tool(session, tc["function"]["name"], args)
                    if tc["function"]["name"] == "web_search":
                        collected_sources.extend(result.get("sources", []))
                    messages.append({
                        "role": "tool",
                        "content": result["content"],
                        "tool_call_id": tc["id"],
                    })

            return "⚠️ Too many tool-call rounds. Try rephrasing your question."
        except aiohttp.ServerTimeoutError:
            logger.warning("ask: LLM API timed out after 60s")
            return "⚠️ LLM API timed out (>60s). Try again."
        except Exception as e:
            logger.warning(f"ask: LLM error: {type(e).__name__}: {e}")
            return f"⚠️ LLM error ({type(e).__name__}): {e or 'check logs'}"

    def _attach_sources(self, content: str, sources: list[dict]) -> str:
        # Don't attach sources for jokes, personal advice, or opinions
        content_lower = (content or "").strip().lower()
        
        # Skip sources for non-factual content
        skip_patterns = [
            "scherzi", "scherzo",  # jokes
            "dai",  # casual "come on"
            "secondo me", "penso che", "credere",  # opinions
            "consiglio", "aiuto",  # advice
        ]
        
        if any(pattern in content_lower for pattern in skip_patterns):
            return content
        
        unique = []
        seen = set()
        for src in sources:
            href = src.get("href", "")
            domain = src.get("domain", "")
            key = href or domain
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(src)
            if len(unique) >= 3:
                break

        source_tokens = []
        for src in unique:
            href = (src.get("href") or "").strip()
            domain = src.get("domain") or "source"
            source_tokens.append(f"{domain}: <{href}>" if href else domain)

        if not source_tokens:
            return content

        base = content.strip() if content else ""
        if "Sources:" in base:
            return base
        return f"{base}\n\nSources: " + " | ".join(source_tokens)

    def _suppress_preview_links(self, text: str) -> str:
        """
        Wrap raw http(s) links as <...> so Discord does not generate embeds.
        Leaves links already wrapped in angle brackets unchanged.
        """
        if not text:
            return text

        pattern = re.compile(r'(?<!<)(https?://[^\s<>]+)')

        def repl(match):
            url = match.group(1)
            trailing = ""
            while url and url[-1] in ".,);!?":
                trailing = url[-1] + trailing
                url = url[:-1]
            return f"<{url}>{trailing}"

        return pattern.sub(repl, text)

    def _normalize_search_results(self, results) -> list[dict]:
        normalized = []
        for r in results:
            href = r.get("href") or r.get("url") or ""
            domain = ""
            if href:
                try:
                    domain = (urlparse(href).netloc or "").lower()
                    domain = domain[4:] if domain.startswith("www.") else domain
                except Exception:
                    domain = ""
            normalized.append({
                "title": (r.get("title") or "").strip(),
                "body": (r.get("body") or "").strip(),
                "href": href,
                "domain": domain,
            })
        return normalized

    def _format_search_payload(self, trusted: list[dict], fallback: list[dict], mode: str) -> str:
        lines = [f"search_mode={mode}"]

        if trusted:
            lines.append("trusted_results:")
            for r in trusted[:_MAX_WEB_RESULTS]:
                lines.append(f"- {r['title']} | {r['domain']} | {r['href']} | {r['body']}")
        else:
            lines.append("trusted_results: none")

        if fallback:
            lines.append("fallback_results:")
            for r in fallback[:_MAX_WEB_RESULTS]:
                lines.append(f"- {r['title']} | {r['domain']} | {r['href']} | {r['body']}")

        return "\n".join(lines)

    def _web_search(self, query: str, domain_mode: str) -> dict:
        domain_mode = (domain_mode or "trusted_first").strip().lower()
        if domain_mode not in {"trusted_first", "trusted_only", "broad_only"}:
            domain_mode = "trusted_first"

        trusted_results: list[dict] = []
        fallback_results: list[dict] = []

        with DDGS() as ddgs:
            if domain_mode in {"trusted_first", "trusted_only"} and TRUSTED_SPORT_DOMAINS:
                trusted_query = f"{query} ({' OR '.join(f'site:{d}' for d in TRUSTED_SPORT_DOMAINS)})"
                trusted_raw = list(ddgs.text(trusted_query, max_results=_MAX_WEB_RESULTS))
                trusted_results = self._normalize_search_results(trusted_raw)

            need_fallback = domain_mode == "broad_only" or (
                domain_mode == "trusted_first" and len(trusted_results) < WEB_SEARCH_MIN_TRUSTED_RESULTS
            )
            if need_fallback:
                fallback_raw = list(ddgs.text(query, max_results=_MAX_WEB_RESULTS))
                fallback_results = self._normalize_search_results(fallback_raw)

        payload = self._format_search_payload(trusted_results, fallback_results, domain_mode)
        sources = trusted_results + fallback_results
        return {"content": payload if sources else "No results found.", "sources": sources}

    async def _execute_tool(self, session: aiohttp.ClientSession, name: str, args: dict) -> dict:
        if name == "web_search":
            try:
                query = args.get("query", "")
                domain_mode = args.get("domain_mode", "trusted_first")
                if not query.strip():
                    return {"content": "No query provided for web search.", "sources": []}
                return await asyncio.to_thread(self._web_search, query, domain_mode)
            except Exception as e:
                return {"content": f"Search failed: {e}", "sources": []}

        if name == "get_todays_fixtures":
            try:
                matches = await api_provider.fetch_day(session)
                matches = await api_provider.enrich_fixtures(session, matches)
                if not matches:
                    return {"content": "No fixtures found for today.", "sources": []}
                lines = []
                for m in matches[:15]:
                    home = m["teams"]["home"]["name"]
                    away = m["teams"]["away"]["name"]
                    status = m["fixture"]["status"]["short"]
                    gh = m["goals"]["home"]
                    ga = m["goals"]["away"]
                    league = LEAGUE_NAME_MAP.get(m["league"]["id"], "Unknown")
                    score = f"{gh}-{ga}" if gh is not None else "vs"
                    lines.append(f"{home} {score} {away} [{status}] ({league})")
                return {"content": "\n".join(lines), "sources": []}
            except Exception as e:
                return {"content": f"Fixture fetch failed: {e}", "sources": []}

        if name == "get_next_match":
            try:
                team = args.get("team_name", "")
                result = await search_team_espn(session, team, _TRACKED_SLUGS)
                if not result:
                    return {"content": f"Could not find team: {team}", "sources": []}
                team_id, primary_slug = result
                slugs = build_league_slugs(primary_slug)
                fixture = await fetch_next_team_fixture_espn(session, team_id, slugs)
                if not fixture:
                    return {"content": f"No upcoming fixture found for {team}.", "sources": []}
                home = fixture["teams"]["home"]["name"]
                away = fixture["teams"]["away"]["name"]
                date_utc = fixture["fixture"]["date"]
                date_italy = parse_utc_to_italy(date_utc).strftime("%A, %B %d, %Y at %H:%M (Italy Time)")
                league = LEAGUE_NAME_MAP.get(fixture["league"]["id"], "Unknown")
                return {"content": f"{home} vs {away} - {date_italy} ({league})", "sources": []}
            except Exception as e:
                return {"content": f"Next match fetch failed: {e}", "sources": []}

        if name == "get_memory":
            try:
                entity_type = args.get("entity_type", "")
                entity_name = args.get("entity_name", "")
                if not entity_type or not entity_name:
                    return {"content": "Missing entity_type or entity_name for get_memory.", "sources": []}

                if entity_type == "league":
                    # Find league by name
                    for league_id, league_name in LEAGUE_NAME_MAP.items():
                        if league_name.lower() == entity_name.lower():
                            standings = get_league_standings(league_id)
                            if standings:
                                lines = [f"{league_name} Standings:"]
                                for team in standings:
                                    lines.append(
                                        f"  {team['position']}. {team['name']} - {team['points']}pts "
                                        f"(P{team['played']} W{team['won']} D{team['drawn']} L{team['lost']}) "
                                        f"GF{team['goals_for']} GA{team['goals_against']}"
                                    )
                                return {
                                    "content": f"FINAL ANSWER FROM MEMORY:\n" + "\n".join(lines) + "\n\nDO NOT CALL ANY MORE TOOLS.",
                                    "sources": [{"href": "", "domain": "Bot Memory"}],
                                }
                            else:
                                return {
                                    "content": f"No standings found in memory for {entity_name}.",
                                    "sources": [],
                                }

                elif entity_type == "team":
                    # Find team by name (case-insensitive)
                    memory = load_memory()
                    for team_id, team_data in memory.get("teams", {}).items():
                        if team_data.get("name", "").lower() == entity_name.lower():
                            lines = [f"{team_data['name']} Info:"]
                            lines.append(f"  Coach: {team_data.get('coach', 'Unknown')}")
                            if "stats" in team_data:
                                stats = team_data["stats"]
                                lines.append(
                                    f"  Stats: W{stats.get('wins', 0)} D{stats.get('draws', 0)} "
                                    f"L{stats.get('losses', 0)} GF{stats.get('goals_for', 0)} "
                                    f"GA{stats.get('goals_against', 0)}"
                                )
                            # Top 3 scorers
                            players = team_data.get("players", {})
                            if players:
                                sorted_players = sorted(
                                    players.items(),
                                    key=lambda x: x[1].get("goals", 0),
                                    reverse=True,
                                )[:3]
                                if sorted_players:
                                    lines.append("  Top Scorers:")
                                    for pname, pdata in sorted_players:
                                        lines.append(
                                            f"    - {pname}: {pdata.get('goals', 0)} goals, "
                                            f"{pdata.get('assists', 0)} assists, "
                                            f"{pdata.get('yellow_cards', 0)} yellow cards, "
                                            f"{pdata.get('red_cards', 0)} red cards"
                                        )
                            return {
                                "content": f"FINAL ANSWER FROM MEMORY:\n" + "\n".join(lines) + "\n\nDO NOT CALL ANY MORE TOOLS.",
                                "sources": [{"href": "", "domain": "Bot Memory"}],
                            }
                    return {
                        "content": f"No team found in memory for {entity_name}.",
                        "sources": [],
                    }

            except Exception as e:
                logger.error(f"get_memory tool failed: {e}")
                return {"content": f"Memory lookup failed: {e}", "sources": []}

        return {"content": f"Unknown tool: {name}", "sources": []}

    def _format_memory_context(self, question: str, memory: dict) -> str:
        """
        Extract relevant entities from the question and format memory context for LLM.
        Returns a string to inject into the system prompt.
        """
        lines = ["Memory Context (use this FIRST for facts):"]

        # Extract entities from question
        question_lower = question.lower()
        entities = {"leagues": [], "teams": []}

        # Check for league names
        for league_id, league_name in LEAGUE_NAME_MAP.items():
            if league_name.lower() in question_lower:
                entities["leagues"].append(league_name)

        # Check for team names (simple list of common teams)
        common_teams = [
            "AC Milan", "Inter", "Juventus", "Roma", "Lazio", "Napoli",
            "Atalanta", "Fiorentina", "Bologna", "Torino", "Sassuolo",
            "Monza", "Lecce", "Salernitana", "Empoli", "Udinese",
            "Arsenal", "Chelsea", "Liverpool", "Man City", "Man United",
            "Tottenham", "Aston Villa", "Newcastle", "Brighton", "West Ham",
            "Real Madrid", "Barcelona", "Atletico Madrid", "Real Sociedad", "Villarreal",
            "Bayern Munich", "Dortmund", "Leverkusen", "RB Leipzig", "Union Berlin",
            "PSG", "Lyon", "Marseille", "Lille", "Monaco",
        ]
        for team in common_teams:
            if team.lower() in question_lower:
                entities["teams"].append(team)

        # Add league standings if requested
        for league_name in entities["leagues"]:
            for league_id, lname in LEAGUE_NAME_MAP.items():
                if lname == league_name:
                    standings = get_league_standings(league_id)
                    if standings:
                        lines.append(f"\n{league_name} Standings (from memory):")
                        for team in standings[:5]:  # Top 5
                            lines.append(
                                f"  {team['position']}. {team['name']} - {team['points']}pts "
                                f"(P{team['played']} W{team['won']} D{team['drawn']} L{team['lost']})"
                            )
                        break

        # Add team info if requested
        for team_name in entities["teams"]:
            team_info = None
            for team_id, team_data in memory.get("teams", {}).items():
                if team_data.get("name", "").lower() == team_name.lower():
                    team_info = team_data
                    break

            if team_info:
                lines.append(f"\n{team_name} Info (from memory):")
                lines.append(f"  Coach: {team_info.get('coach', 'Unknown')}")
                if "stats" in team_info:
                    stats = team_info["stats"]
                    lines.append(
                        f"  Stats: W{stats.get('wins', 0)} D{stats.get('draws', 0)} "
                        f"L{stats.get('losses', 0)} GF{stats.get('goals_for', 0)} "
                        f"GA{stats.get('goals_against', 0)}"
                    )
                # Top 3 scorers
                players = team_info.get("players", {})
                if players:
                    sorted_players = sorted(
                        players.items(),
                        key=lambda x: x[1].get("goals", 0),
                        reverse=True,
                    )[:3]
                    if sorted_players:
                        lines.append("  Top Scorers:")
                        for pname, pdata in sorted_players:
                            lines.append(
                                f"    - {pname}: {pdata.get('goals', 0)} goals, "
                                f"{pdata.get('assists', 0)} assists"
                            )

        return "\n".join(lines) if len(lines) > 1 else ""


async def setup(bot: commands.Bot):
    await bot.add_cog(Ask(bot))
    logger.info("cogs.ask loaded")
