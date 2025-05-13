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
