**Football Tracker Bot v3.3.0**
Author: SpikePhD
Local LLM Integration (`!ask`):

• New `!ask <question>` command:
  • Routes questions through a local LLM running on the Raspberry Pi via ollama.
  • Supports tool calling — the LLM can invoke tools to answer questions it can't answer
    from training data alone.
  • Three built-in tools: web search (DuckDuckGo), today's live fixtures, and next match
    for any team (reuses ESPN client functions already used by `!next`).
  • Discord shows a "typing…" indicator while the LLM processes the request.
  • Gracefully returns an error message if ollama is unavailable.
• Fully configurable via `.env` — no code changes needed to customise the bot:
  • `BOT_NAME` — the bot's display name used in the default persona prompt.
  • `OLLAMA_MODEL` — the ollama model to use (default: `qwen2.5:3b`).
  • `OLLAMA_URL` — ollama server URL (default: `http://localhost:11434`).
  • `OLLAMA_SYSTEM_PROMPT` — full system prompt / persona for the LLM.
• New dependency: `ddgs` — DuckDuckGo search library (no API key required).
• Recommended model: `qwen2.5:3b` — best tool-calling quality at ~2 GB on ARM64.

**Football Tracker Bot v3.2.0**
Author: SpikePhD
Commands Cleanup & Code Quality:

• Removed `!milan` / `!nextmilan` / `!acmilan`:
  • Functionally identical to `!next AC Milan` — use that instead.
  • `AC_MILAN_TEAM_ID` and `AC_MILAN_ESPN_TEAM_ID` constants removed from config.py.
• New `!commands` command (aliases: `!cmds`, `!help`):
  • Dynamically lists every registered bot command with its aliases and description.
  • Replaces discord.py's built-in `!help` (which is now disabled).
• Code quality (no behaviour changes):
  • Extracted shared event-formatting logic into utils/event_formatter.py — eliminates
    duplicate goal/red-card rendering across matches.py, live_loop.py, ft_handler.py.
  • Fixed en-dash vs hyphen inconsistency in FT result messages.
  • bot_mode.set_mode() now raises ValueError instead of using assert.
  • get_current_season_year() uses italy_now() instead of naive datetime.now().
  • Removed ~200 lines of dead code and stale comments throughout.
• Bug Fix:
  • Fixed duplicate live updates posted on bot restart: in-progress matches are now
    pre-seeded into already_posted so the first poll after startup doesn't re-post
    scores already shown in the startup message.

**Football Tracker Bot v3.1.0**
Author: SpikePhD
UX Polish, Persistent Memory & Deployment Improvements:

• !matches Grouped by Competition:
  • Fixtures now grouped under bold competition headers, sorted by first kick-off time.
  • FT matches show full scorer details inline (e.g. FT: Milan 2-1 Inter (30' - Leao (H); 77' - Giroud (H))).
  • Live matches show current minute and full event list (e.g. LIVE [67']: Milan 1-0 Inter (30' - Leao (H))).
• Morning Fixture Broadcast:
  • Bot automatically posts a greeting + today's grouped fixture list at 06:30 AM (Italy time) every day.
• Improved Startup Message:
  • On restart, bot posts the full grouped fixture list alongside "I am back Online".
  • Removed duplicate FT announcements that previously appeared separately after startup.
• Silent / Verbose Mode:
  • !silent (aliases: !Silent, !SILENT) — pauses automatic broadcasts (startup message, morning list).
  • !verbose (aliases: !Verbose, !VERBOSE) — resumes automatic broadcasts.
  • Live match updates, FT results, and all commands always work regardless of mode.
  • Mode persists across restarts via bot_memory/state.json.
• Persistent Bot Memory:
  • New bot_memory/ folder (Pi-owned, gitignored) for runtime state that survives updates.
  • New inject_memory/ folder (GitHub-controlled) for reference data (Milan calendar, etc.).
  • New modules/storage.py — thin JSON read/write wrapper for bot_memory/.
• Deployment Improvements:
  • New update.sh script replaces manual update steps: pulls code, initialises missing bot_memory/
    files with safe defaults, restarts the service.
  • New update_bot.bat — one double-click on Windows to deploy the latest version to the Pi.
• Bug Fixes:
  • Fixed ESPN goal times displayed as raw seconds (e.g. 1800') instead of minutes (30').
  • Fixed missing goal scorers when ESPN reports score changes before populating event details
    (deduplication key now includes event count, not just scoreline).
  • Fixed LEAGUE_NAME_MAP duplicated between cogs — moved to config.py as single source of truth.

**Football Tracker Bot v3.0.0**
Author: SpikePhD
ESPN Integration & Major Reliability Overhaul:

• ESPN as Primary Data Source:
  • Replaced API-Football as primary polling source with the ESPN public API (no auth, no rate limits).
  • Bot now polls all 18 tracked leagues simultaneously every 60 seconds via concurrent requests.
  • API-Football retained as automatic fallback if ESPN becomes unavailable.
• Automatic Provider Fallback:
  • After 3 consecutive ESPN failures, bot silently switches to API-Football (polling every 480 seconds).
  • Retry window of 10 minutes before probing ESPN again; switches back transparently on recovery.
  • All provider transitions are logged loudly for easy diagnosis.
• New `!api` Command:
  • Shows the currently active data provider (ESPN or API-Football fallback).
  • Displays poll interval, consecutive failure count, and ESPN retry time when in fallback mode.
  • Aliases: `!apistatus`, `!provider`.
• Full-Time Detection Improvements:
  • In ESPN mode, FT results are detected directly from the cached scoreboard — no extra API call per match.
  • Shared FT posting logic extracted to eliminate duplication between ESPN and fallback paths.
• Bug Fixes:
  • Fixed `command_name` NameError in discord_poster.py (variable defined inside try block but referenced in except).
  • Fixed `aiohttp.ClientTimeout` being passed as a plain int in api_client.py.
  • Fixed bot lifecycle using fragile `bot.close` monkey-patch; replaced with `asyncio.run(main())` pattern.
  • Fixed home/away team detection in `!milan` command to use team ID instead of fragile string matching.
  • Removed dead `modules/track_leagues.py` (never called).
• Documentation:
  • Added README.md with full setup, deployment, and configuration reference.
  • Added AGENTS.md as an AI assistant guide covering architecture, conventions, and extension patterns.
  • Added .env.example template.
• Dependency Update:
  • Bumped aiohttp from 3.9.5 to 3.13.5, resolving 20 CVE security alerts.

**Football Tracker Bot v2.0.0**
Major Core Logic Enhancements & Bug Fixes:

• Implemented Smart Message Editing:
  • Live updates will now edit the bot's previous message if fewer than 30 messages have been posted in the channel by other users since the bot's last update.
  • Posts a new message if the chat is active (30+ messages) or for the first update of a match/session, significantly reducing spam. (Fixes original primary issue).
• Robust Scheduler Task Management:
    • Ensured that only one instance of the daily scheduling logic (schedule_day) runs at any time, preventing duplicate polling loops and redundant API calls. Correctly handles bot restarts and the 11 AM daily trigger.
• Overhauled API Client (utils/api_client.py):
  • Implemented a shared aiohttp.ClientSession across the bot for efficient connection pooling and resource management.
  • Added comprehensive error handling for API requests, including HTTP status code checks, API-specific error messages in JSON responses, network exceptions (aiohttp.ClientError), and request timeouts.
  • API functions now provide clearer return types (e.g., empty list on error for fixture lists).
  • Centralized league filtering within the API client for Workspace_day_fixtures and Workspace_live_fixtures.
• Improved Full-Time (FT) Result Handling:
  • Integrated post_initial_fts into the main scheduler flow, ensuring that matches already completed at the time of daily fixture fetching are announced.
  • ft_handler.py now correctly uses the shared aiohttp.ClientSession and benefits from improved API error handling.
  • Added more robust checks and logging within ft_handler.py for API responses and match statuses.
• Code Quality & Stability Improvements:
    • Standardized Logging (Ongoing/Partial):
      • Initialized Python's standard logging framework with basicConfig in the main bot file.
      • Converted utils/api_client.py, modules/scheduler.py, modules/live_loop.py, modules/ft_handler.py, and modules/power_manager.py to use the standard logging module, replacing the custom verbose_logger.py. (Note: football_tracker_bot.py main file also updated).
• modules/verbose_logger.py can now be deprecated/removed.
• Code Refinements & Cleanups:
    • Updated all API calling modules (scheduler, live_loop, ft_handler, cogs/matches) to correctly pass and use the shared aiohttp.ClientSession.
    • Removed redundant league filtering in modules/live_loop.py and modules/scheduler.py as this is now handled by utils/api_client.py.
    • Corrected minor import errors (e.g., log_error in live_loop.py, discord in ft_handler.py).
    • Improved logging messages across several modules for better diagnostics.
    • Scheduler Logic (modules/scheduler.py):
    • Refined logic for determining when to sleep for the first Kick-Off, ensuring it correctly handles days with no "Not Started" matches but possibly ongoing or FT-pending games.
    • Ensured the 8-minute polling loop for run_live_loop and Workspace_and_post_ft runs reliably if there are any relevant fixtures for the day.

Previous v1.1.0 Fixes (Incorporated & Maintained):
• The scheduler fix to catch live matches if the bot started mid-game is inherently improved by the more robust schedule_day management and immediate run_live_loop calls.

**Football Tracker Bot v1.1.0**
Author: SpikePhD
• Fixed an issue with the scheduler, that would not catch live matches if the bot started in the middle of these matches
• minor grammatical errors spotted and corrected  

**Football Tracker Bot v1.0.0**
Author: SpikePhD 
• Ready for field test
• Posts a startup greeting  
• Fetches and prints today’s fixtures on startup and at 11:00 (Italy time)  
• Sleeps until the first kickoff, then polls live scores every 8 minutes  
• Every 8 minutes, sends goal & red‐card updates for all tracked competitions
• Avoid sending repetead messages to reduce spam  
• Detects “Full-Time” and posts final score + scorer events  
• Commands:
  • `!matches` – lists today’s tracked fixtures  
  • `!competitions` – lists all competitions being tracked  
  • `!hello` / `!hi` – simple alive check & greeting  
  • `!changelog` – version tracking documentation
