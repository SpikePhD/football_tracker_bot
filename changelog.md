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