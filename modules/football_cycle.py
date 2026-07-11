"""One provider snapshot shared by all consumers in an awake football cycle."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from modules import api_provider, match_lifecycle
from utils.time_utils import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FootballCycleSnapshot:
    """Fixed fixture containers for one scheduler cycle.

    Fixture dictionaries remain provider records and must be treated as read-only
    by cycle consumers. The tuples prevent consumers from adding, removing, or
    reordering fixtures for another consumer.
    """

    now_utc: datetime
    relevant_matches: tuple[dict, ...]
    live_matches: tuple[dict, ...]

    def relevant_by_id(self) -> dict[str, dict]:
        return {
            fixture_id: match
            for match in self.relevant_matches
            if (fixture_id := match_lifecycle.fixture_identity(match)) is not None
        }


async def build_football_cycle_snapshot(session, now_utc: datetime | None = None) -> FootballCycleSnapshot:
    """Fetch the rolling window once, then derive the cycle's live fixtures."""
    now_utc = now_utc or utc_now()
    relevant = await api_provider.fetch_relevant_football(session, now_utc)
    live = await api_provider.fetch_live(
        session,
        now_utc=now_utc,
        relevant_matches=relevant,
    )
    snapshot = FootballCycleSnapshot(
        now_utc=now_utc,
        relevant_matches=tuple(relevant),
        live_matches=tuple(live),
    )
    logger.debug(
        "Football cycle snapshot prepared: relevant=%d live=%d at=%s.",
        len(snapshot.relevant_matches),
        len(snapshot.live_matches),
        now_utc.isoformat(),
    )
    return snapshot
