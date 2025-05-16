# utils/time_utils.py

from datetime import datetime, timedelta
import pytz

# Timezone for Italy
italy_tz = pytz.timezone("Europe/Rome")

def italy_now():
    """Returns current datetime in Italy timezone."""
    return datetime.now(italy_tz)

def parse_utc_to_italy(utc_str):
    """Takes a UTC time string and returns Italy-localized datetime."""
    utc_time = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
    return utc_time.astimezone(italy_tz)

def get_italy_date_string():
    """Returns today’s date as YYYY-MM-DD in Italy timezone."""
    return italy_now().strftime("%Y-%m-%d")

def time_until(dt):
    """Returns a timedelta until the given datetime (assumed localized)."""
    return dt - italy_now()

def get_current_season_year() -> int:
    """
    Determines the current football season year.
    Assumes European league timing (e.g., season 2024 runs from mid-2024 to mid-2025).
    """
    now = datetime.now()
    
    if now.month >= 8:  # August
        return now.year
    else:
        return now.year - 1