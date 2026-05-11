"""AEC Media Feed CSV fetcher.

Downloads public AEC Media Feed CSVs (2007–2025 federal events) into a
local cache so transforms can run without re-fetching. Each CSV is small
(KB–single-MB) so we just store the raw file as-is.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Federal House of Reps election event IDs (from results.aec.gov.au index).
EVENT_IDS: dict[int, int] = {
    2025: 31496,
    2022: 27966,
    2019: 24310,
    2016: 20499,
    2013: 17496,
    2010: 15508,
    2007: 13745,
    2004: 12246,
}

# AEC restructured the Tally Room URL between 2004 and 2007. Modern events
# live under `/Website/Downloads/`; the 2004 archive uses `/results/Downloads/`.
_URL_PATH_OVERRIDES: dict[int, str] = {
    12246: "results",
}

STATES: tuple[str, ...] = ("NSW", "VIC", "QLD", "WA", "SA", "TAS", "ACT", "NT")


@dataclass(frozen=True)
class FeedFiles:
    """Paths (cached on disk) for one election event's House results."""

    event_id: int
    cache_root: Path

    @property
    def candidates(self) -> Path:
        return self._path(f"HouseCandidatesDownload-{self.event_id}.csv")

    @property
    def members_elected(self) -> Path:
        return self._path(f"HouseMembersElectedDownload-{self.event_id}.csv")

    @property
    def dop_by_division(self) -> Path:
        return self._path(f"HouseDopByDivisionDownload-{self.event_id}.csv")

    @property
    def tcp_by_polling_place(self) -> Path:
        return self._path(f"HouseTcpByCandidateByPollingPlaceDownload-{self.event_id}.csv")

    @property
    def tpp_by_division(self) -> Path:
        """Two-Party Preferred (Labor vs Coalition) per division.

        This is the canonical line for the historical trend chart —
        always defined regardless of who reached the actual TCP.
        """
        return self._path(f"HouseTppByDivisionDownload-{self.event_id}.csv")

    @property
    def turnout_by_division(self) -> Path:
        """Per-division Enrolment + Turnout + TurnoutPercentage."""
        return self._path(f"HouseTurnoutByDivisionDownload-{self.event_id}.csv")

    @property
    def polling_places(self) -> Path:
        """Master polling-places list (PollingPlaceID → lat/lng + premises)."""
        return self._path(f"GeneralPollingPlacesDownload-{self.event_id}.csv")

    @property
    def first_prefs_by_vote_type(self) -> Path:
        """Per-candidate FP split across Ordinary/Absent/Provisional/PrePoll/
        Postal with a TotalVotes column. This is the canonical FP figure
        AEC publishes — the by-polling-place file is ordinary-only."""
        return self._path(
            f"HouseFirstPrefsByCandidateByVoteTypeDownload-{self.event_id}.csv"
        )

    @property
    def informal_by_division(self) -> Path:
        """Per-division formal + informal totals (matches AEC's published
        InformalPercent). The booth-level CSV only has ordinary informal."""
        return self._path(f"HouseInformalByDivisionDownload-{self.event_id}.csv")

    @property
    def tcp_by_vote_type(self) -> Path:
        """Per-candidate TCP split across all vote types; TotalVotes column
        gives the canonical seat-level TCP figure (matches AEC's published
        margins). The by-polling-place file is ordinary-votes-only."""
        return self._path(f"HouseTcpByCandidateByVoteTypeDownload-{self.event_id}.csv")

    def first_prefs_by_polling_place(self, state: str) -> Path:
        return self._path(
            f"HouseStateFirstPrefsByPollingPlaceDownload-{self.event_id}-{state}.csv"
        )

    def _path(self, name: str) -> Path:
        return self.cache_root / str(self.event_id) / name


def fetch_event(year: int, cache_root: Path, *, refresh: bool = False) -> FeedFiles:
    """Download (cache-hit-friendly) every CSV needed for a House event.

    Returns a `FeedFiles` whose properties resolve to local paths.
    """
    if year not in EVENT_IDS:
        raise ValueError(f"Unknown federal election year: {year}. Known: {sorted(EVENT_IDS)}")

    event_id = EVENT_IDS[year]
    files = FeedFiles(event_id=event_id, cache_root=cache_root)
    files.candidates.parent.mkdir(parents=True, exist_ok=True)

    with httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "aec-elections-research/0.1 (personal-research)"},
    ) as client:
        targets: list[Path] = [
            files.candidates,
            files.members_elected,
            files.dop_by_division,
            files.tcp_by_polling_place,
            files.tpp_by_division,
            files.turnout_by_division,
            files.polling_places,
            files.first_prefs_by_vote_type,
            files.informal_by_division,
            files.tcp_by_vote_type,
            *(files.first_prefs_by_polling_place(s) for s in STATES),
        ]
        for target in targets:
            _fetch_one(client, event_id, target, refresh=refresh)
    return files


def fetch_event_lite(year: int, cache_root: Path, *, refresh: bool = False) -> FeedFiles:
    """Lighter fetch used for historical years — only the CSVs needed
    for the trend chart + primary stack (no booth-level data)."""
    if year not in EVENT_IDS:
        raise ValueError(f"Unknown federal election year: {year}. Known: {sorted(EVENT_IDS)}")
    event_id = EVENT_IDS[year]
    files = FeedFiles(event_id=event_id, cache_root=cache_root)
    files.candidates.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(
        timeout=60.0,
        follow_redirects=True,
        headers={"User-Agent": "aec-elections-research/0.1 (personal-research)"},
    ) as client:
        for target in (files.candidates, files.dop_by_division, files.tpp_by_division):
            _fetch_one(client, event_id, target, refresh=refresh)
    return files


def _fetch_one(client: httpx.Client, event_id: int, target: Path, *, refresh: bool) -> None:
    if target.exists() and not refresh:
        log.debug("cached: %s", target.name)
        return
    path_segment = _URL_PATH_OVERRIDES.get(event_id, "Website")
    url = f"https://results.aec.gov.au/{event_id}/{path_segment}/Downloads/{target.name}"
    log.info("fetching %s", url)
    resp = client.get(url)
    resp.raise_for_status()
    target.write_bytes(resp.content)


# AEC CSVs have a one-line "version comment" header above the column header.
# Polars' read_csv handles this with skip_rows=1.
SKIP_ROWS = 1
