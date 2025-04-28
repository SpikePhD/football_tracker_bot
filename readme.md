Marco Van Botten – Server Management Guide
SSH Access

    Connect to the Raspberry Pi:

    ssh lucac@raspberry.local

    If Pi IP changes or .local doesn't work, use the IP address instead.

Running Status

    Check if the bot is running:

    ps aux | grep football_tracker_bot.py

    You should see a running python process.

Manually Updating the Bot

Whenever you push an update to GitHub:

    SSH into Raspberry Pi.

    Navigate to bot folder:

cd ~/football_tracker_bot

Update from GitHub:

git fetch
git reset --hard origin/main

Restart the bot manually (optional, since Pi auto starts it on reboot):

    pkill -f football_tracker_bot.py
    source .venv/bin/activate
    python football_tracker_bot.py

Rebooting the Raspberry Pi

    Simple command:

    sudo reboot

    The bot auto-starts after reboot.

Manual Start if Needed

If the bot isn’t running (after crash or manual stop):

cd ~/football_tracker_bot
source .venv/bin/activate
python football_tracker_bot.py

Installing New Dependencies

If you add a new library to requirements.txt:

cd ~/football_tracker_bot
source .venv/bin/activate
pip install -r requirements.txt

Clean Reinstall (only if needed)

If you ever need to fully reset the folder:

rm -rf ~/football_tracker_bot
git clone https://github.com/SpikePhD/football_tracker_bot.git
cd football_tracker_bot
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python football_tracker_bot.py

Important Reminders:

    You are running inside a .venv environment (good practice ✅).

    The bot auto starts on reboot.

    Always push your updates cleanly from your development machine (Visual Studio / GitHub Desktop / Git).

    Passwords and tokens are inside .env, keep it private and safe.