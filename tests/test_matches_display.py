import os
import asyncio
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")

from tests.regression_helpers import espn_match


def _fixture(fixture_id: str, kickoff_utc: str, status: str = "NS", elapsed: int | None = None) -> dict:
    match = espn_match(fixture_id=fixture_id, league_id=135)
    match["fixture"]["date"] = kickoff_utc
    match["fixture"]["status"] = {"short": status}
    if elapsed is not None:
        match["fixture"]["status"]["elapsed"] = elapsed
    match["teams"]["home"]["name"] = f"Home {fixture_id}"
    match["teams"]["away"]["name"] = f"Away {fixture_id}"
    return match


class MatchesDisplayTests(unittest.TestCase):

    def test_default_public_snapshot_excludes_future_local_matchday_football(self):
        from cogs import matches

        now_utc = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
        today = _fixture("today", "2026-06-09T19:00:00Z")
        future = _fixture("future", "2026-06-11T19:00:00Z")

        filtered = matches.filter_football_for_local_matchday([future, today], now_utc)
        content = matches.build_combined_matches_message(filtered, [], now_utc=now_utc)

        self.assertIn("Tracked sports (2026-06-09)", content)
        self.assertIn("Home today vs Away today", content)
        self.assertNotIn("Home future vs Away future", content)

    def test_yesterday_kickoff_live_after_midnight_is_public_carryover(self):
        from cogs import matches

        now_utc = datetime(2026, 6, 9, 22, 15, tzinfo=timezone.utc)  # 2026-06-10 00:15 Europe/Rome
        live = _fixture("late-live", "2026-06-09T21:30:00Z", status="2H", elapsed=74)

        filtered = matches.filter_football_for_local_matchday([live], now_utc)
        content = matches.build_football_section(filtered)

        self.assertEqual([m["fixture"]["id"] for m in filtered], ["late-live"])
        self.assertIn("LIVE [74']", content)
        self.assertIn("Home late-live", content)

    def test_empty_daily_football_message_says_today(self):
        from cogs import matches

        self.assertEqual(
            matches.build_football_section([]),
            "**Football**\nNo tracked football matches today.",
        )

    def test_upcoming_football_view_groups_future_fixtures_by_local_date(self):
        from cogs import matches

        now_utc = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
        today = _fixture("today", "2026-06-09T19:00:00Z")
        future = _fixture("future", "2026-06-11T19:00:00Z")

        upcoming = matches.filter_upcoming_football_fixtures([today, future], now_utc)
        content = matches.build_upcoming_football_message(upcoming)

        self.assertEqual([m["fixture"]["id"] for m in upcoming], ["future"])
        self.assertIn("Upcoming football fixtures", content)
        self.assertIn("2026-06-11", content)
        self.assertIn("21:00 - Home future vs Away future", content)
        self.assertNotIn("Home today vs Away today", content)

    def test_tennis_today_section_behavior_remains_unchanged(self):
        from cogs import matches

        tennis_live = {
            "player_a": "Tracked Player",
            "player_b": "Opponent",
            "event_name": "Test Open",
            "tour": "ATP",
            "start_time": "2026-06-11T19:00:00Z",
            "status": {"short": "LIVE"},
        }

        content = matches.build_tennis_section([tennis_live])

        self.assertIn("Tracked Player vs Opponent", content)
        self.assertNotIn("No tracked tennis", content)

    def test_fetch_combined_snapshot_filters_broad_provider_window_to_today(self):
        from cogs import matches

        now_utc = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
        today = _fixture("today", "2026-06-09T19:00:00Z")
        future = _fixture("future", "2026-06-11T19:00:00Z")

        async def run():
            with (
                patch.object(matches, "utc_now", return_value=now_utc),
                patch.object(matches.api_provider, "fetch_day", AsyncMock(return_value=[future, today])),
                patch.object(matches.api_provider, "fetch_tennis_day", AsyncMock(return_value=[])),
            ):
                return await matches.fetch_combined_matches_snapshot(None)

        football_fixtures, _tennis, content = asyncio.run(run())

        self.assertEqual([m["fixture"]["id"] for m in football_fixtures], ["today"])
        self.assertIn("Tracked sports (2026-06-09)", content)
        self.assertIn("Home today vs Away today", content)
        self.assertNotIn("Home future vs Away future", content)

    def test_upcoming_api_view_uses_wide_provider_window_grouped_by_future_date(self):
        from cogs import matches

        now_utc = datetime(2026, 6, 9, 10, 0, tzinfo=timezone.utc)
        today = _fixture("today", "2026-06-09T19:00:00Z")
        future = _fixture("future", "2026-06-11T19:00:00Z")

        async def run():
            with (
                patch.object(matches, "utc_now", return_value=now_utc),
                patch.object(matches.api_provider, "fetch_day", AsyncMock(return_value=[future, today])),
            ):
                return await matches.build_upcoming_football_message_from_api(None)

        content = asyncio.run(run())

        self.assertIn("2026-06-11", content)
        self.assertIn("Home future vs Away future", content)
        self.assertNotIn("Home today vs Away today", content)


if __name__ == "__main__":
    unittest.main()
