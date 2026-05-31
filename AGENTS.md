# Agent Guide - Football Tracker Bot

Read this file first. Keep context light: open deeper docs only when the task needs them.

## Purpose

Discord bot for one channel:

- Football and tracked tennis updates
- ESPN primary provider
- API-Football fallback/enrichment
- Raspberry Pi + `systemd` deployment

## Read Routing

- `DEVELOPER.md` - architecture, coding rules, extension notes, validation
- `OPERATIONS.md` - Pi service, logs, update, troubleshooting workflows
- `README.md` - user-facing setup, commands, project map
- `CHANGELOG.md` - release-note or `!changelog` work only

Do not preload `docs/archive/`, `bot_memory/`, `inject_memory/`, `__pycache__/`, logs, dumps, or generated exports unless the task explicitly requires them.

## Configuration Contract

- `.env` - secrets only
- `config.json` - committed non-secret behavior knobs
- `.env.deploy` - deployment script variables

Do not place secrets in `config.json`.

## Hard Rules

- Route command replies through `post_new_message_to_context(...)`.
- Route proactive posts through `modules/discord_poster.py` helpers.
- Do not create new `aiohttp` sessions; use the shared bot session.
- Prefer `modules/api_provider.py` for fixture data access paths.
- Keep runtime persistence in `bot_memory/` only.
- Keep `inject_memory/` read-only from runtime logic.
- Use module loggers (`logging.getLogger(__name__)`); avoid `print()` in production code.
- Do not duplicate updater shell logic in Python; `update.sh` is canonical.

## Extension Notes

- Add a competition by updating tracked IDs/slugs in `config.json` and `config.example.json`; change `config.py` only if schema validation changes.
- Add a command as a small cog under `cogs/`, with `async def setup(bot): await bot.add_cog(...)`.
- Add runtime state through `modules/storage.py`, and make deploy/update defaults safe and non-overwriting.

## Architecture Snapshot

```text
football_tracker_bot.py  loads cogs, starts loops, owns shared HTTP session
modules/scheduler.py     daily orchestration
modules/live_loop.py     live updates + dedup
modules/ft_handler.py    final result tracking/posting
modules/tennis_loop.py   tennis polling/announcements
modules/api_provider.py  ESPN primary, fallback/enrichment policy
modules/discord_poster.py unified message sending
modules/storage.py       runtime JSON state in bot_memory/
```
