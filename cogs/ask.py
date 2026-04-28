# cogs/ask.py
import json
import logging
import re
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
from modules.discord_poster import post_new_message_to_context
from utils.espn_client import (
    fetch_all_leagues,
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
]


class Ask(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._history: dict[int, deque] = {}

    @commands.command(
        name="ask",
        help="Ask the bot a question. The LLM uses web search and includes sources.",
    )
    async def ask(self, ctx: commands.Context, *, question: str):
        history = self._history.setdefault(ctx.channel.id, deque(maxlen=_HISTORY_MAXLEN))
        async with ctx.typing():
            reply = await self._run_llm(question, history)
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": reply})
        await post_new_message_to_context(ctx, content=reply)

    async def _run_llm(self, question: str, history: deque) -> str:
        if not LLM_API_KEY:
            return "⚠️ LLM_API_KEY is not set. Add it to your .env file."

        today = italy_now().strftime("%A, %B %d, %Y")
        system_content = (
            f"{LLM_SYSTEM_PROMPT}\n"
            f"Today's date is {today}.\n"
            "Rules: perform web_search before final answer; do not make factual claims without retrieved evidence; "
            "always end factual answers with a 'Sources:' line using 1-3 links/domains from retrieved results."
        )

        messages = [
            {"role": "system", "content": system_content},
            *list(history),
            {"role": "user", "content": question},
        ]
        headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        search_performed = False
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
                    if not search_performed:
                        forced = await self._execute_tool(
                            session,
                            "web_search",
                            {"query": question, "domain_mode": "trusted_first"},
                        )
                        search_performed = True
                        collected_sources.extend(forced.get("sources", []))
                        messages.append({
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [{
                                "id": "forced_web_search_1",
                                "type": "function",
                                "function": {
                                    "name": "web_search",
                                    "arguments": json.dumps({"query": question, "domain_mode": "trusted_first"}),
                                },
                            }],
                        })
                        messages.append({
                            "role": "tool",
                            "content": forced["content"],
                            "tool_call_id": "forced_web_search_1",
                        })
                        continue

                    content = msg.get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(b["text"] for b in content if b.get("type") == "text")
                    content = re.sub(r'\s*\{["\w][^{}]*\}\.?', '', (content or "")).strip()

                    if not collected_sources:
                        return (
                            "⚠️ I couldn't verify this with web sources right now, "
                            "so I prefer not to give potentially inaccurate information."
                        )

                    return self._attach_sources(content, collected_sources)

                messages.append(msg)
                for tc in tool_calls:
                    args = tc["function"].get("arguments", "{}")
                    if isinstance(args, str):
                        args = json.loads(args)
                    result = await self._execute_tool(session, tc["function"]["name"], args)
                    if tc["function"]["name"] == "web_search":
                        search_performed = True
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
            href = src.get("href")
            domain = src.get("domain") or "source"
            source_tokens.append(f"{domain}: {href}" if href else domain)

        if not source_tokens:
            return content

        base = content.strip() if content else ""
        if "Sources:" in base:
            return base
        return f"{base}\n\nSources: " + " | ".join(source_tokens)

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
                return self._web_search(query, domain_mode)
            except Exception as e:
                return {"content": f"Search failed: {e}", "sources": []}

        if name == "get_todays_fixtures":
            try:
                matches = await fetch_all_leagues(session, LEAGUE_SLUG_MAP)
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

        return {"content": f"Unknown tool: {name}", "sources": []}


async def setup(bot: commands.Bot):
    await bot.add_cog(Ask(bot))
    logger.info("cogs.ask loaded")
