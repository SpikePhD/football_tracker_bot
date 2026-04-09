# cogs/ask.py
import logging

import aiohttp
from discord.ext import commands
from ddgs import DDGS

from config import (LEAGUE_NAME_MAP, LEAGUE_SLUG_MAP, OLLAMA_MODEL,
                    OLLAMA_SYSTEM_PROMPT, OLLAMA_URL, build_league_slugs)
from utils.time_utils import parse_utc_to_italy
from modules.discord_poster import post_new_message_to_context
from utils.espn_client import (fetch_all_leagues, fetch_next_team_fixture_espn,
                                search_team_espn)

logger = logging.getLogger(__name__)

_TRACKED_SLUGS = set(LEAGUE_SLUG_MAP.values())

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for up-to-date football news, scores, or any current information.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query"},
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

    @commands.command(
        name="ask",
        help="Ask the bot a question. The LLM can search the web and check fixtures.",
    )
    async def ask(self, ctx: commands.Context, *, question: str):
        async with ctx.typing():
            reply = await self._run_llm(question)
        await post_new_message_to_context(ctx, content=reply)

    async def _run_llm(self, question: str) -> str:
        messages = [
            {"role": "system", "content": OLLAMA_SYSTEM_PROMPT},
            {"role": "user",   "content": question},
        ]
        try:
            async with aiohttp.ClientSession() as session:
                # Health check — fast ping to catch ollama being down before the long request
                try:
                    async with session.get(
                        f"{OLLAMA_URL}/api/tags",
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as ping:
                        if ping.status != 200:
                            return f"⚠️ ollama is not responding (HTTP {ping.status}). Is it running? `sudo systemctl start ollama`"
                except Exception:
                    return "⚠️ Cannot reach ollama. Is it running? Try: `sudo systemctl start ollama`"

                for _ in range(5):  # max 5 tool-call rounds to prevent infinite loops
                    payload = {
                        "model": OLLAMA_MODEL,
                        "stream": False,
                        "messages": messages,
                        "tools": TOOLS,
                    }
                    async with session.post(
                        f"{OLLAMA_URL}/api/chat",
                        json=payload,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp:
                        data = await resp.json()

                    msg = data["message"]
                    if not msg.get("tool_calls"):
                        return msg["content"]  # final answer — no more tool calls

                    messages.append(msg)
                    for tc in msg["tool_calls"]:
                        result = await self._execute_tool(
                            session,
                            tc["function"]["name"],
                            tc["function"].get("arguments", {}),
                        )
                        messages.append({"role": "tool", "content": result})

            return "⚠️ Too many tool calls — try rephrasing your question."
        except aiohttp.ServerTimeoutError:
            logger.warning("ask: LLM request timed out after 120s")
            return "⚠️ The LLM took too long to respond (>120s). The Pi may be under load — try again in a moment."
        except Exception as e:
            logger.warning(f"ask: LLM error: {type(e).__name__}: {e}")
            return f"⚠️ LLM error ({type(e).__name__}): {e or 'no details — check `sudo journalctl -u marco_van_botten -n 20`'}"

    async def _execute_tool(self, session: aiohttp.ClientSession, name: str, args: dict) -> str:
        if name == "web_search":
            try:
                results = DDGS().text(args.get("query", ""), max_results=3)
                return "\n".join(f"{r['title']}: {r['body']}" for r in results) or "No results found."
            except Exception as e:
                return f"Search failed: {e}"

        if name == "get_todays_fixtures":
            try:
                matches = await fetch_all_leagues(session, LEAGUE_SLUG_MAP)
                if not matches:
                    return "No fixtures found for today."
                lines = []
                for m in matches[:15]:
                    home = m["teams"]["home"]["name"]
                    away = m["teams"]["away"]["name"]
                    status = m["fixture"]["status"]["short"]
                    gh = m["goals"]["home"]
                    ga = m["goals"]["away"]
                    league = LEAGUE_NAME_MAP.get(m["league"]["id"], "Unknown")
                    score = f"{gh}–{ga}" if gh is not None else "vs"
                    lines.append(f"{home} {score} {away} [{status}] ({league})")
                return "\n".join(lines)
            except Exception as e:
                return f"Fixture fetch failed: {e}"

        if name == "get_next_match":
            try:
                team = args.get("team_name", "")
                result = await search_team_espn(session, team, _TRACKED_SLUGS)
                if not result:
                    return f"Could not find team: {team}"
                team_id, primary_slug = result
                slugs = build_league_slugs(primary_slug)
                fixture = await fetch_next_team_fixture_espn(session, team_id, slugs)
                if not fixture:
                    return f"No upcoming fixture found for {team}."
                home = fixture["teams"]["home"]["name"]
                away = fixture["teams"]["away"]["name"]
                date_utc = fixture["fixture"]["date"]
                date_italy = parse_utc_to_italy(date_utc).strftime("%A, %B %d, %Y at %H:%M (Italy Time)")
                league = LEAGUE_NAME_MAP.get(fixture["league"]["id"], "Unknown")
                return f"{home} vs {away} — {date_italy} ({league})"
            except Exception as e:
                return f"Next match fetch failed: {e}"

        return f"Unknown tool: {name}"


async def setup(bot: commands.Bot):
    await bot.add_cog(Ask(bot))
    logger.info("✔ cogs.ask loaded")
