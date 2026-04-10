# cogs/ask.py
import json
import logging

import aiohttp
from discord.ext import commands
from ddgs import DDGS

from config import (LEAGUE_NAME_MAP, LEAGUE_SLUG_MAP, LLM_API_KEY,
                    LLM_BASE_URL, LLM_MODEL, LLM_SYSTEM_PROMPT, build_league_slugs)
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
        if not LLM_API_KEY:
            return "⚠️ LLM_API_KEY is not set. Add it to your .env file."

        messages = [
            {"role": "system", "content": LLM_SYSTEM_PROMPT},
            {"role": "user",   "content": question},
        ]
        headers = {
            "Authorization": f"Bearer {LLM_API_KEY}",
            "Content-Type": "application/json",
        }

        try:
            async with aiohttp.ClientSession() as session:
                for _ in range(5):  # max 5 tool-call rounds to prevent infinite loops
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
                    if not msg.get("tool_calls"):
                        return msg["content"]  # final answer — no more tool calls

                    messages.append(msg)
                    for tc in msg["tool_calls"]:
                        args = tc["function"].get("arguments", "{}")
                        if isinstance(args, str):
                            args = json.loads(args)
                        result = await self._execute_tool(session, tc["function"]["name"], args)
                        messages.append({
                            "role": "tool",
                            "content": result,
                            "tool_call_id": tc["id"],
                        })

            return "⚠️ Too many tool calls — try rephrasing your question."
        except aiohttp.ServerTimeoutError:
            logger.warning("ask: Mistral API timed out after 60s")
            return "⚠️ Mistral API timed out (>60s). Try again."
        except Exception as e:
            logger.warning(f"ask: LLM error: {type(e).__name__}: {e}")
            return f"⚠️ LLM error ({type(e).__name__}): {e or 'check logs'}"

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
