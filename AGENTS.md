# Agent Guide - Football Tracker Bot (Marco Van Botten)

Guidance for coding agents working in this repository.

## Purpose

A Discord bot that posts football and tracked tennis updates into one channel.

- Primary provider: ESPN
- Secondary provider: API-Football (fallback/enrichment)
- Deployment target: Raspberry Pi + systemd service

## Configuration Contract

Use the 3-file split:

- `.env` -> secrets only
- `config.json` -> non-secret behavior knobs
- `.env.deploy` -> deployment script variables

Do not place secrets in `config.json`.

## Architecture Snapshot

```text
football_tracker_bot.py
  -> loads cogs
  -> starts scheduler/task loops
  -> manages shared HTTP session

modules/
  scheduler.py      daily orchestration
  live_loop.py      live updates + dedup
  ft_handler.py     final result tracking/posting
  tennis_loop.py    tennis polling/announcements
  api_provider.py   ESPN primary, fallback/enrichment policy
  discord_poster.py unified message sending
  storage.py        runtime JSON state in bot_memory/
```

## Hard Rules

- Route command replies through `post_new_message_to_context(...)`.
- Route proactive posts through `discord_poster` helpers.
- Do not create new `aiohttp` sessions; use the shared bot session.
- Prefer `modules/api_provider.py` for fixture data access paths.
- Keep runtime persistence in `bot_memory/` only.
- Keep `inject_memory/` read-only from runtime logic.

## Data/State Notes

- `bot_memory/` is gitignored and persists across deployments.
- `config.json` is committed and intentionally non-secret.
- `.env` and `.env.deploy` are gitignored.

## Logging Expectations

- Use module loggers (`logging.getLogger(__name__)`).
- Keep logs structured and grep-friendly.
- Avoid `print()` in production code.

## When Extending

### Add a competition

- Update tracked IDs/slugs in config surfaces (`config.json` + loader validation as needed).
- Keep naming centralized; avoid per-cog constants.

### Add a new command cog

- Add file in `cogs/`
- Keep command behavior small and explicit
- Validate with local compile/import checks

### Add new runtime state

- Use `modules/storage.py`
- Ensure defaults are safely created during deploy/update flows

## What to Avoid

- Direct low-level provider calls in random modules/cogs when provider orchestration exists
- Duplicated configuration constants in multiple files
- Backward-compat shims unless explicitly requested
- Destructive repo operations unless explicitly requested
