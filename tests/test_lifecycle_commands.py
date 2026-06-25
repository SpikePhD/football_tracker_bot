import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

os.environ.setdefault("BOT_TOKEN", "test-token")
os.environ.setdefault("API_KEY", "test-api-key")
os.environ.setdefault("CHANNEL_ID", "123456789")


class LifecycleCommandFormatterTests(unittest.TestCase):

    def test_match_state_detail_includes_prunable_due_and_local_kickoff(self):
        from cogs import football_lifecycle

        fixture = {
            "fixture_id": "abc-1",
            "provider": "api-football",
            "kickoff_utc": "2026-06-03T21:30:00+00:00",
            "expected_ft_utc": "2026-06-03T23:22:00+00:00",
            "last_status": "2H",
            "last_score": {"home": 1, "away": 0},
            "last_seen_utc": "2026-06-03T22:15:00+00:00",
            "terminal_utc": None,
            "ft_announced": False,
            "memory_updated": False,
            "live_message_id": 12345,
        }

        content = football_lifecycle.build_match_state_detail(
            fixture,
            now_utc=datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc),
        )

        self.assertIn("Fixture `abc-1`", content)
        self.assertIn("Provider: api-football", content)
        self.assertIn("Kickoff UTC: 2026-06-03T21:30:00+00:00", content)
        self.assertIn("Kickoff local: 2026-06-03 23:30 Europe/Rome", content)
        self.assertIn("Expected FT due: yes", content)
        self.assertIn("Prunable now: no", content)
        self.assertLessEqual(len(content), 1900)

    def test_lifecycle_summary_counts_pending_and_provider_settings(self):
        from cogs import football_lifecycle

        state = {
            "fixtures": {
                "live": {
                    "fixture_id": "live",
                    "last_status": "2H",
                    "expected_ft_utc": "2026-06-03T23:22:00+00:00",
                    "ft_announced": False,
                    "memory_updated": False,
                    "kickoff_utc": "2026-06-03T21:30:00+00:00",
                    "last_seen_utc": "2026-06-03T22:15:00+00:00",
                },
                "ft": {
                    "fixture_id": "ft",
                    "last_status": "FT",
                    "expected_ft_utc": "2026-06-03T22:52:00+00:00",
                    "terminal_utc": "2026-06-03T22:50:00+00:00",
                    "ft_announced": True,
                    "memory_updated": False,
                },
            },
        }

        with patch.object(
            football_lifecycle.api_provider,
            "get_status",
            return_value={"espn_healthy": False, "poll_interval": 45},
        ), patch.object(
            football_lifecycle.scheduler,
            "get_football_scheduler_status",
            return_value={
                "mode": "sleeping",
                "next_football_check_utc": datetime(2026, 6, 4, 3, 0, tzinfo=timezone.utc),
                "next_schedule_refresh_utc": datetime(2026, 6, 4, 3, 0, tzinfo=timezone.utc),
                "next_planned_kickoff_utc": datetime(2026, 6, 4, 8, 0, tzinfo=timezone.utc),
                "next_planned_wake_utc": datetime(2026, 6, 4, 6, 0, tzinfo=timezone.utc),
                "wake_reason": None,
                "wake_reason_detail": None,
                "sleep_reason": "next_fixture_wake",
                "sleep_reason_detail": (
                    "kickoff=2026-06-04T08:00:00+00:00 "
                    "wake=2026-06-04T06:00:00+00:00"
                ),
            },
        ), patch.object(
            football_lifecycle.scheduler,
            "get_tennis_scheduler_status",
            return_value={
                "mode": "awake",
                "next_tennis_check_utc": datetime(2026, 6, 4, 0, 1, tzinfo=timezone.utc),
                "next_schedule_refresh_utc": None,
                "next_planned_start_utc": None,
                "next_planned_wake_utc": None,
            },
        ):
            content = football_lifecycle.build_lifecycle_summary(
                state,
                now_utc=datetime(2026, 6, 3, 23, 30, tzinfo=timezone.utc),
            )

        self.assertIn("Tracked fixtures: 2", content)
        self.assertIn("Active/live: 1", content)
        self.assertIn("Awaiting FT post: 1", content)
        self.assertIn("Awaiting memory: 2", content)
        self.assertIn("Provider: API-Football fallback", content)
        self.assertIn("Poll interval: 45s", content)
        self.assertIn("Scheduler: sleeping", content)
        self.assertIn("Next football check: 2026-06-04T03:00:00+00:00", content)
        self.assertIn("Next schedule refresh: 2026-06-04T03:00:00+00:00", content)
        self.assertIn("Next planned kickoff: 2026-06-04T08:00:00+00:00", content)
        self.assertIn("Next planned wake: 2026-06-04T06:00:00+00:00", content)
        self.assertIn("Wake reason: n/a", content)
        self.assertIn("Sleep reason: next_fixture_wake", content)
        self.assertIn("Sleep detail: kickoff=2026-06-04T08:00:00+00:00", content)
        self.assertIn("Tennis scheduler: awake", content)
        self.assertIn("Next tennis check: 2026-06-04T00:01:00+00:00", content)
        self.assertIn("Timezone: Europe/Rome", content)
        self.assertIn("Display lookup: +/-", content)
        self.assertLessEqual(len(content), 1900)

    def test_match_state_list_is_concise_and_discord_safe(self):
        from cogs import football_lifecycle

        state = {
            "fixtures": {
                str(i): {
                    "fixture_id": str(i),
                    "provider": "espn",
                    "last_status": "NS",
                    "last_score": {"home": None, "away": None},
                    "expected_ft_utc": "2026-06-04T20:52:00+00:00",
                }
                for i in range(12)
            }
        }

        content = football_lifecycle.build_match_state_list(
            state,
            now_utc=datetime(2026, 6, 4, 10, 0, tzinfo=timezone.utc),
            limit=5,
        )

        self.assertIn("Tracked fixture state: 12 fixture(s)", content)
        self.assertIn("showing first 5", content)
        self.assertLessEqual(len(content), 1900)


if __name__ == "__main__":
    unittest.main()
