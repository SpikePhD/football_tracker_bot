# Legacy Changelog Archive

This archive keeps older release history out of root `CHANGELOG.md`, which is posted by the Discord `!changelog` command. The root changelog should stay focused on the current release.

## Football Tracker Bot v3.3.0

Local LLM integration for `!ask`.

- Added `!ask <question>` backed by a local LLM endpoint.
- Added tool calling for web search, today's live fixtures, and next-match lookup.
- Added typing indicator and graceful unavailable-service responses.
- Moved LLM behavior into configuration.
- Added `ddgs` dependency for DuckDuckGo search.

## Football Tracker Bot v3.2.0

Commands cleanup and code quality.

- Removed Milan-specific next-match commands in favor of `!next AC Milan`.
- Added dynamic `!commands` / `!cmds` / `!help`.
- Extracted shared event formatting into `utils/event_formatter.py`.
- Fixed FT message punctuation consistency.
- Replaced assert-based mode validation with `ValueError`.
- Switched season-year logic to Italy-aware time.
- Fixed duplicate live updates after restart.
- Removed dead/stale code and comments.

## Football Tracker Bot v3.1.0

UX polish, persistent memory, and deployment improvements.

- Grouped `!matches` output by competition.
- Added automatic morning fixture broadcast.
- Improved restart/startup messaging.
- Added verbose/silent broadcast modes backed by `bot_memory/state.json`.
- Added gitignored `bot_memory/` runtime state and repo-controlled `inject_memory/`.
- Added `modules/storage.py`.
- Added `update.sh` and `update_bot.bat`.
- Fixed ESPN goal-time formatting.
- Fixed missing scorer follow-ups when ESPN score changes before event details.
- Centralized league naming in `config.py`.

## Football Tracker Bot v3.0.0

ESPN integration and reliability overhaul.

- Made ESPN the primary football data source.
- Kept API-Football as automatic fallback.
- Added `!api` / `!apistatus` / `!provider`.
- Improved full-time detection from cached ESPN scoreboards.
- Extracted shared FT posting logic.
- Fixed provider, timeout, lifecycle, and team-detection bugs.
- Added `README.md`, `AGENTS.md`, and `.env.example`.
- Updated `aiohttp` to resolve security alerts.

## Football Tracker Bot v2.0.0

Core logic and reliability improvements.

- Added smart live-message editing to reduce spam.
- Hardened scheduler task management.
- Reworked API client error handling and shared-session usage.
- Improved full-time result handling.
- Migrated major modules to standard logging.
- Removed redundant league filtering from scheduler/live modules.
- Improved kickoff and polling-loop scheduling.

## Football Tracker Bot v1.1.0

- Fixed scheduler behavior when the bot starts during live matches.
- Corrected minor text issues.

## Football Tracker Bot v1.0.0

- Initial field-test release.
- Posted startup greeting and daily fixtures.
- Polled live scores every eight minutes.
- Posted goal/red-card updates and full-time results.
- Added `!matches`, `!competitions`, `!hello`, and `!hi`.
