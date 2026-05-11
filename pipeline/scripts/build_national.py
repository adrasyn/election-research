"""Build the national-view aggregate.

Reads every per-seat JSON under site/public/seats/ and emits a single
slim file at site/public/national.json containing only the fields the
nationwide-insights view needs. Keeps the national tab to one HTTP
request rather than 150.

Run from the repo root:
    pipeline/.venv/bin/python pipeline/scripts/build_national.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SEATS_DIR = REPO / "site" / "public" / "seats"
OUT = REPO / "site" / "public" / "national.json"
CACHE = REPO / "pipeline" / "data" / "raw"

# AEC event IDs we care about for the national bar charts.
EVENT_2025 = 31496
EVENT_2022 = 27966


def _winner_primary_pct(s: dict[str, Any]) -> float | None:
    """First-pref % of the winning candidate."""
    winner_id = (s.get("seat", {}).get("winner") or {}).get("candidateId")
    if not winner_id:
        return None
    for c in s.get("primary") or []:
        if c.get("candidateId") == winner_id:
            return c.get("pct")
    return None


# Primary-vote partyAb → bucket. Aggregated buckets are stable across
# every seat so the cross-tab can compare like-for-like.
_PRIMARY_BUCKETS = {
    "ALP": "alp",
    "LIB": "coa", "LP": "coa", "LNP": "coa", "NAT": "coa", "NP": "coa", "CLP": "coa",
    "GRN": "grn",
    "ON": "on",
    "IND": "ind",
}


def _primary_buckets(s: dict[str, Any]) -> dict[str, float]:
    """Sum primary pct into stable party buckets (alp/coa/grn/on/ind/oth)."""
    out = {"alp": 0.0, "coa": 0.0, "grn": 0.0, "on": 0.0, "ind": 0.0, "oth": 0.0}
    for c in s.get("primary") or []:
        ab = (c.get("partyAb") or "").upper()
        bucket = _PRIMARY_BUCKETS.get(ab, "oth")
        out[bucket] += float(c.get("pct") or 0.0)
    return {k: round(v, 2) for k, v in out.items()}


def _alp_2pp_swing(s: dict[str, Any]) -> float | None:
    """ALP 2PP swing from the prior election, derived from the history
    trend block. Positive = ALP gained, negative = Coalition gained.
    None for newly-created seats with no prior data."""
    trend = ((s.get("history") or {}).get("trend") or {})
    series = trend.get("series") or []
    alp = next((x for x in series if (x.get("label") or "").upper() == "ALP"), None)
    if not alp:
        return None
    pts = alp.get("points") or []
    if len(pts) < 2 or pts[-1] is None or pts[-2] is None:
        return None
    return round(float(pts[-1]) - float(pts[-2]), 2)


def _slim_seat(s: dict[str, Any]) -> dict[str, Any]:
    seat = s.get("seat") or {}
    winner = seat.get("winner") or {}
    dem = s.get("demographics") or {}
    return {
        "id": seat.get("id"),
        "name": seat.get("name"),
        "state": seat.get("state"),
        "divisionId": seat.get("divisionId"),
        "winnerPartyAb": winner.get("partyAb"),
        "winnerParty": winner.get("party"),
        "winnerSurname": winner.get("surname"),
        "tcpMargin": seat.get("tcpMargin"),
        "winnerPrimaryPct": _winner_primary_pct(s),
        "alp2ppSwing": _alp_2pp_swing(s),
        "turnoutPct": seat.get("turnoutPct"),
        "primaryBuckets": _primary_buckets(s),
        "result": seat.get("result"),
        "previousWinnerParty": seat.get("previousWinnerParty"),
        # Demographic scalars only — no top-N lists, no national block.
        "demographics": (
            {
                "medianHouseholdIncomeWeekly": dem.get("medianHouseholdIncomeWeekly"),
                "medianAge": dem.get("medianAge"),
                "medianRentWeekly": dem.get("medianRentWeekly"),
                "bornOverseasPct": dem.get("bornOverseasPct"),
                "bachelorPlusPct": dem.get("bachelorPlusPct"),
                "renterPct": dem.get("renterPct"),
                "indigenousPct": dem.get("indigenousPct"),
            }
            if dem
            else None
        ),
    }


# Antony Green's notional 2022 ALP TPP for seats where the AEC's
# published TPP swing is itself an artefact (the 2022 election didn't
# have an ALP-vs-Coalition 2CP in that electorate). Source: ABC Election
# Unit pre-2025 electorate listing — FED2025_ElectorateListing.pdf.
# Values are the notional 2022 ALP-vs-Coalition TPP % after redistribution.
GREEN_NOTIONAL_2022_ALP_TPP: dict[str, float] = {
    "Brisbane": 54.4,    # Green: ALP 4.4 2-Party (post-redist, 2022 GRN-won)
    "Bendigo":  61.2,    # Green: ALP 11.2 2-Party (post-redist boundary change)
    "Nicholls": 34.2,    # Green: NAT 15.8 2-Party  → ALP TPP = 34.2
}


def _tpp_swing_by_division(event_id: int) -> dict[int, dict[str, Any]]:
    """Per-division ALP TPP percent + swing from AEC.

    Returns {divisionId: {alpTpp, swing, swing_source}}. For the three
    seats where the AEC's Swing column is an artefact (2022 had no
    ALP-vs-Coalition 2CP), the swing is recomputed using Antony Green's
    notional 2022 baseline.
    """
    csv = CACHE / str(event_id) / f"HouseTppByDivisionDownload-{event_id}.csv"
    out: dict[int, dict[str, Any]] = {}
    if not csv.exists():
        return out
    import csv as csvmod
    with csv.open(newline="") as f:
        next(f, None)
        reader = csvmod.DictReader(f)
        for row in reader:
            try:
                div_id = int(row["DivisionID"])
                alp_pct = float(row.get("Australian Labor Party Percentage") or 0.0)
                aec_swing = float(row.get("Swing") or 0.0)
                name = (row.get("DivisionNm") or "").strip()
            except (TypeError, ValueError):
                continue
            # AEC's Swing column is the artefact "swing from zero" when
            # |swing| ≈ alp_pct — detect and patch from Green.
            if name in GREEN_NOTIONAL_2022_ALP_TPP and abs(abs(aec_swing) - alp_pct) < 0.05:
                notional = GREEN_NOTIONAL_2022_ALP_TPP[name]
                out[div_id] = {
                    "alpTpp": round(alp_pct, 2),
                    "swing": round(alp_pct - notional, 2),
                    "swingSource": "green-notional",
                }
            else:
                out[div_id] = {
                    "alpTpp": round(alp_pct, 2),
                    "swing": round(aec_swing, 2),
                    "swingSource": "aec",
                }
    return out


# For non-traditional 2CP seats where AEC's published Swing is an
# artefact (TCP composition changed), the right "swing to winner" is
# the winner's notional 2CP swing as published by Antony Green's
# pre-2025 PDF (2-Candidate column). Positive = winner gained.
# Source: FED2025_ElectorateListing.pdf, computed as
#   2025_actual_TCP_winner_pct − Green_notional_2022_winner_pct.
GREEN_NOTIONAL_SWING_TO_WINNER: dict[str, float] = {
    # Calare 2025 TCP: IND (Gee) 56.78 vs NAT. Green 2022 notional
    # "NAT 9.7 v IND" → IND 45.15.
    "Calare":   11.63,
    # Mayo 2025 TCP: CA (Sharkie) 64.89 vs LIB. Green 2022 notional
    # "CA 12.3 v LIB" → CA 62.30.
    "Mayo":      2.59,
    # Nicholls 2025 TCP: NAT 64.38 vs IND. Green 2022 notional
    # "NAT 3.4 v IND" → NAT 53.40.
    "Nicholls": 10.98,
}


def _winner_tcp_swing_by_division(event_id: int) -> dict[int, dict[str, float]]:
    """Per-division: {swing, winnerTcpPct} for the elected candidate.

    Both come from the AEC's per-vote-type TCP file. The TCP percent is
    needed to detect the AEC's "swing from zero" artefact, which has
    the unique signature `abs(swing) ≈ winnerTcpPct` — i.e. AEC
    reports the new candidate's TCP value as the swing because they
    have no prior baseline for that candidate."""
    csv = CACHE / str(event_id) / f"HouseTcpByCandidateByVoteTypeDownload-{event_id}.csv"
    out: dict[int, dict[str, float]] = {}
    if not csv.exists():
        return out
    # We need the elected row plus the total TCP across both finalists
    # to compute pct. Two-pass.
    import csv as csvmod
    rows_by_div: dict[int, list[dict[str, Any]]] = {}
    with csv.open(newline="") as f:
        next(f, None)
        reader = csvmod.DictReader(f)
        for row in reader:
            try:
                div_id = int(row["DivisionID"])
            except (TypeError, ValueError):
                continue
            rows_by_div.setdefault(div_id, []).append(row)
    for div_id, rows in rows_by_div.items():
        total = sum(int(r.get("TotalVotes") or 0) for r in rows) or 1
        winner = next((r for r in rows if (r.get("Elected") or "").strip() == "Y"), None)
        if winner is None:
            continue
        try:
            swing = float(winner.get("Swing") or 0.0)
            votes = int(winner.get("TotalVotes") or 0)
        except (TypeError, ValueError):
            continue
        pct = votes / total * 100 if total else 0.0
        out[div_id] = {"swing": round(swing, 2), "pct": round(pct, 2)}
    return out


def _national_primary_from_csv(event_id: int) -> dict[str, float] | None:
    """Aggregate per-candidate TotalVotes across the country, bucket by
    party. Returns share-of-formal pct per bucket, plus the absolute
    `_totalFormal` count under that key for downstream sanity."""
    csv = CACHE / str(event_id) / f"HouseFirstPrefsByCandidateByVoteTypeDownload-{event_id}.csv"
    if not csv.exists():
        return None
    import csv as csvmod
    totals: dict[str, int] = {k: 0 for k in ("alp", "coa", "grn", "on", "ind", "oth")}
    grand = 0
    with csv.open(newline="") as f:
        # Skip the leading version-comment line; the second line is the
        # column header.
        next(f, None)
        reader = csvmod.DictReader(f)
        for row in reader:
            ab = (row.get("PartyAb") or "").upper()
            if not ab:
                continue  # informal row
            bucket = _PRIMARY_BUCKETS.get(ab, "oth")
            v = int(row.get("TotalVotes") or 0)
            totals[bucket] += v
            grand += v
    if not grand:
        return None
    out = {k: round(totals[k] / grand * 100, 2) for k in totals}
    out["_totalFormal"] = grand
    return out


def _national_aggregates(seats: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the `national` block — primary share by party (2025), the
    2022 baseline for swing context, and a per-bucket swing."""
    p25 = _national_primary_from_csv(EVENT_2025)
    p22 = _national_primary_from_csv(EVENT_2022)
    out: dict[str, Any] = {}
    if p25:
        out["primary2025"] = p25
    if p22:
        out["primary2022"] = p22
    if p25 and p22:
        swing = {}
        for k in ("alp", "coa", "grn", "on", "ind", "oth"):
            swing[k] = round(p25.get(k, 0) - p22.get(k, 0), 2)
        out["primarySwing"] = swing
    out["totalSeats"] = len(seats)
    return out


def main() -> int:
    files = sorted(SEATS_DIR.glob("*.json"))
    if not files:
        print(f"NO seat JSONs at {SEATS_DIR}", file=sys.stderr)
        return 2

    winner_tcp = _winner_tcp_swing_by_division(EVENT_2025)
    tpp_data = _tpp_swing_by_division(EVENT_2025)

    # Coalition party codes — for orienting TPP swing toward the
    # winner's bloc on traditional ALP-vs-Coalition contests.
    COALITION = {"LIB", "LNP", "NAT", "CLP"}

    year: int | None = None
    seats: list[dict[str, Any]] = []
    for p in files:
        data = json.loads(p.read_text())
        if year is None:
            year = data.get("year")
        seat = _slim_seat(data)
        div_id = (data.get("seat") or {}).get("divisionId")
        winner_ab = (seat.get("winnerPartyAb") or "").upper()
        winner_tcp_info = winner_tcp.get(div_id) if div_id is not None else None
        if winner_tcp_info is not None:
            seat["winnerTcpSwing"] = winner_tcp_info["swing"]
        if div_id is not None and div_id in tpp_data:
            tpp = tpp_data[div_id]
            seat["alpTpp"] = tpp["alpTpp"]
            seat["tppSwing"] = tpp["swing"]
            seat["tppSwingSource"] = tpp["swingSource"]

        # Unified "swing to winner" for the Results strip. Positive =
        # winner gained ground. Priority cascade:
        #   1. Clean 2CP swing (AEC) — when the 2CP party composition
        #      is the same as 2022, AEC's published Swing is a genuine
        #      same-pair year-on-year delta.
        #   2. Green's notional 2-Candidate (3 hand-curated seats where
        #      the 2025 2CP is non-traditional).
        #   3. TPP swing oriented to winner's bloc (traditional 2CP
        #      seats whose 2CP changed since 2022 — e.g. ALP-vs-GRN in
        #      2022 to ALP-vs-LIB in 2025).
        nm = seat.get("name")
        wts = seat.get("winnerTcpSwing")
        # The AEC's "swing from zero" artefact has a unique signature:
        # the published Swing equals the winner's TCP percent (because
        # AEC has no 2022 baseline for that candidate or party in 2CP).
        is_artefact = (
            winner_tcp_info is not None
            and wts is not None
            and abs(abs(wts) - winner_tcp_info["pct"]) < 0.1
        )
        if wts is not None and not is_artefact:
            seat["swingToWinner"] = wts
            seat["swingSource"] = "tcp"
        elif nm in GREEN_NOTIONAL_SWING_TO_WINNER:
            seat["swingToWinner"] = GREEN_NOTIONAL_SWING_TO_WINNER[nm]
            seat["swingSource"] = "green-2candidate"
        elif seat.get("tppSwing") is not None:
            ts = seat["tppSwing"]
            if winner_ab == "ALP":
                seat["swingToWinner"] = ts
            elif winner_ab in COALITION:
                seat["swingToWinner"] = round(-ts, 2)
            else:
                # Crossbench winner with artefact 2CP swing AND no Green
                # 2-Candidate entry. Fall back to TPP (ALP-positive).
                seat["swingToWinner"] = ts
            seat["swingSource"] = "tpp-oriented"
        else:
            seat["swingToWinner"] = None
            seat["swingSource"] = None

        seats.append(seat)

    national = _national_aggregates(seats)

    out = {
        "year": year,
        "seatCount": len(seats),
        "national": national,
        "seats": seats,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, separators=(",", ":")))
    size_kb = OUT.stat().st_size / 1024
    print(f"Wrote {OUT.relative_to(REPO)} — {len(seats)} seats, {size_kb:.1f} KB")
    if national.get("primary2025"):
        p = national["primary2025"]
        print(f"  2025 national primary: ALP {p['alp']}  COA {p['coa']}  GRN {p['grn']}  ON {p['on']}  IND {p['ind']}  OTH {p['oth']}  (total {p['_totalFormal']:,} formal)")
    if national.get("primarySwing"):
        s = national["primarySwing"]
        print(f"  swing vs 2022:         ALP {s['alp']:+.2f}  COA {s['coa']:+.2f}  GRN {s['grn']:+.2f}  ON {s['on']:+.2f}  IND {s['ind']:+.2f}  OTH {s['oth']:+.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
