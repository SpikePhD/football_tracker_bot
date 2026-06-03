from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from config import OPERATIONS_TIMEZONE

bot_tz = ZoneInfo(OPERATIONS_TIMEZONE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def bot_now() -> datetime:
    return utc_now().astimezone(bot_tz)


def parse_provider_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def to_bot_tz(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = parse_provider_utc(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(bot_tz)


def get_bot_local_date_string() -> str:
    return bot_now().strftime("%Y-%m-%d")


def get_current_season_year() -> int:
    now = bot_now()
    return now.year if now.month >= 8 else now.year - 1
