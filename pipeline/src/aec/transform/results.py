"""Booth + TCP + primary results transforms.

Reads the three AEC CSVs that describe per-booth results and produces the
denormalised tables the frontend consumes. Phase A focuses on a single
seat at a time; the same code paths handle "all seats" by running once
without the division filter.
"""
from __future__ import annotations

from typing import Any

import polars as pl

from ..parties import css_key, display_short
from ..sources.mediafeed import SKIP_ROWS


def load_first_prefs(paths: list) -> pl.DataFrame:
    """Concatenate per-state First Preferences CSVs into one frame."""
    frames = [
        pl.read_csv(p, skip_rows=SKIP_ROWS, infer_schema_length=10000)
        for p in paths
        if p.exists()
    ]
    return pl.concat(frames, how="vertical_relaxed")


def load_tcp(path) -> pl.DataFrame:
    return pl.read_csv(path, skip_rows=SKIP_ROWS, infer_schema_length=10000)


def load_candidates(path) -> pl.DataFrame:
    return pl.read_csv(path, skip_rows=SKIP_ROWS, infer_schema_length=10000)


def primary_for_division(first_prefs: pl.DataFrame, division_id: int) -> list[dict[str, Any]]:
    """Aggregate first-pref votes across all booths in a division.

    The AEC first-prefs CSV interleaves "Informal" rows alongside real
    candidates — strip those out so percentages are of formal votes only.
    """
    seat = first_prefs.filter(
        (pl.col("DivisionID") == division_id) & (pl.col("PartyAb").is_not_null())
    )
    grouped = (
        seat.group_by("CandidateID", "Surname", "GivenNm", "PartyAb")
        .agg(pl.col("OrdinaryVotes").sum().alias("votes"))
        .sort("votes", descending=True)
    )
    total = int(grouped["votes"].sum())
    out = []
    for row in grouped.iter_rows(named=True):
        votes = int(row["votes"])
        out.append(
            {
                "candidateId": f"C{int(row['CandidateID'])}",
                "surname": row["Surname"],
                "givenName": row["GivenNm"],
                "party": css_key(row["PartyAb"]),
                "partyAb": display_short(row["PartyAb"]),
                "votes": votes,
                "pct": round(votes / total * 100, 2) if total else 0.0,
            }
        )
    return out


def informal_for_division(first_prefs: pl.DataFrame, division_id: int) -> dict[str, Any]:
    """Return the informal-vote total + rate for a division."""
    seat = first_prefs.filter(pl.col("DivisionID") == division_id)
    informal_rows = seat.filter(pl.col("PartyAb").is_null())
    formal_rows = seat.filter(pl.col("PartyAb").is_not_null())
    informal = int(informal_rows["OrdinaryVotes"].sum())
    formal = int(formal_rows["OrdinaryVotes"].sum())
    total = informal + formal
    return {
        "informalVotes": informal,
        "formalVotes": formal,
        "totalCounted": total,
        "informalRate": round(informal / total * 100, 2) if total else 0.0,
    }


def tcp_for_division(tcp: pl.DataFrame, division_id: int) -> list[dict[str, Any]]:
    """Aggregate TCP votes across all booths for the two finalists.

    Note: this CSV is ordinary-votes-only; for tight seats the elected
    candidate may sit second on these counts. We honour AEC's declared
    winner via the Elected flag rather than the running ordinary tally.
    """
    seat = tcp.filter(pl.col("DivisionID") == division_id)
    grouped = (
        seat.group_by("CandidateID", "Surname", "GivenNm", "PartyAb", "Elected")
        .agg(pl.col("OrdinaryVotes").sum().alias("votes"))
        .with_columns((pl.col("Elected") == "Y").cast(pl.Int8).alias("_elected_rank"))
        .sort(["_elected_rank", "votes"], descending=[True, True])
    )
    total = int(grouped["votes"].sum())
    out = []
    for row in grouped.iter_rows(named=True):
        votes = int(row["votes"])
        out.append(
            {
                "candidateId": f"C{int(row['CandidateID'])}",
                "surname": row["Surname"],
                "givenName": row["GivenNm"],
                "party": css_key(row["PartyAb"]),
                "partyAb": display_short(row["PartyAb"]),
                "votes": votes,
                "pct": round(votes / total * 100, 2) if total else 0.0,
                "elected": (row["Elected"] or "").strip() == "Y",
            }
        )
    return out


def booths_for_division(
    first_prefs: pl.DataFrame, tcp: pl.DataFrame, division_id: int
) -> list[dict[str, Any]]:
    """Per-booth row: summary fields + full primary + TCP breakdowns.

    The summary fields drive the booth table's collapsed row; the
    `candidates` and `tcp` arrays power the click-to-expand detail view.
    Informal rows (PartyAb null) are excluded from formal vote totals.
    """
    fp_seat = first_prefs.filter(
        (pl.col("DivisionID") == division_id) & (pl.col("PartyAb").is_not_null())
    )
    tcp_seat = tcp.filter(pl.col("DivisionID") == division_id)

    # Summary fields per booth
    formal_per_booth = (
        fp_seat.group_by("PollingPlaceID", "PollingPlace")
        .agg(pl.col("OrdinaryVotes").sum().alias("formal"))
    )
    tcp_winner = (
        tcp_seat.sort(["PollingPlaceID", "OrdinaryVotes"], descending=[False, True])
        .group_by("PollingPlaceID", "PollingPlace", maintain_order=True)
        .agg(
            pl.col("Surname").first().alias("winner_surname"),
            pl.col("PartyAb").first().alias("winner_party"),
            pl.col("OrdinaryVotes").first().alias("winner_votes"),
            pl.col("OrdinaryVotes").sum().alias("tcp_total"),
            pl.col("Swing").first().alias("winner_swing"),
        )
    )
    joined = formal_per_booth.join(
        tcp_winner, on=["PollingPlaceID", "PollingPlace"], how="left"
    )

    # Per-booth primary breakdown (every candidate)
    primary_per_booth: dict[int, list[dict[str, Any]]] = {}
    for row in fp_seat.iter_rows(named=True):
        bid = int(row["PollingPlaceID"])
        primary_per_booth.setdefault(bid, []).append(
            {
                "surname": row["Surname"],
                "partyAb": display_short(row["PartyAb"]),
                "party": css_key(row["PartyAb"]),
                "votes": int(row["OrdinaryVotes"] or 0),
            }
        )

    # Per-booth TCP breakdown (the two finalists)
    tcp_per_booth: dict[int, list[dict[str, Any]]] = {}
    for row in tcp_seat.iter_rows(named=True):
        bid = int(row["PollingPlaceID"])
        tcp_per_booth.setdefault(bid, []).append(
            {
                "surname": row["Surname"],
                "partyAb": display_short(row["PartyAb"]),
                "party": css_key(row["PartyAb"]),
                "votes": int(row["OrdinaryVotes"] or 0),
                "elected": (row.get("Elected") or "").strip() == "Y",
            }
        )

    out: list[dict[str, Any]] = []
    for row in joined.sort("formal", descending=True).iter_rows(named=True):
        bid = int(row["PollingPlaceID"])
        winner_votes = row["winner_votes"]
        tcp_total = row["tcp_total"] or 0
        pct = (winner_votes / tcp_total * 100) if winner_votes and tcp_total else None
        formal = int(row["formal"])

        # Sort primary by votes desc, attach per-candidate pct of formal.
        primary = sorted(primary_per_booth.get(bid, []), key=lambda c: c["votes"], reverse=True)
        for c in primary:
            c["pct"] = round(c["votes"] / formal * 100, 2) if formal else 0.0

        # TCP — winner first, then runner-up. Add pct of TCP total.
        tcp_rows = sorted(
            tcp_per_booth.get(bid, []),
            key=lambda c: (0 if c["elected"] else 1, -c["votes"]),
        )
        tcp_t = sum(c["votes"] for c in tcp_rows)
        for c in tcp_rows:
            c["pct"] = round(c["votes"] / tcp_t * 100, 2) if tcp_t else 0.0

        out.append(
            {
                "boothId": bid,
                "name": row["PollingPlace"],
                "formal": formal,
                "winnerParty": css_key(row["winner_party"]),
                "winnerSurname": row["winner_surname"],
                "winnerPct": round(pct, 2) if pct is not None else None,
                "swing": float(row["winner_swing"]) if row["winner_swing"] is not None else None,
                "candidates": primary,
                "tcp": tcp_rows,
            }
        )
    return out


def division_meta(
    candidates: pl.DataFrame, division_id: int
) -> dict[str, Any]:
    """Pull division name, state, winner candidate metadata."""
    rows = candidates.filter(pl.col("DivisionID") == division_id)
    if rows.is_empty():
        raise ValueError(f"No candidates found for division {division_id}")
    sample = rows.row(0, named=True)
    winner_rows = rows.filter(pl.col("Elected") == "Y")
    winner = winner_rows.row(0, named=True) if not winner_rows.is_empty() else None
    return {
        "divisionId": division_id,
        "name": sample["DivisionNm"],
        "state": sample["StateAb"],
        "winner": (
            {
                "candidateId": f"C{int(winner['CandidateID'])}",
                "surname": winner["Surname"],
                "givenName": winner["GivenNm"],
                "party": css_key(winner["PartyAb"]),
                "partyAb": display_short(winner["PartyAb"]),
                "incumbent": (winner["HistoricElected"] or "").strip() == "Y",
            }
            if winner
            else None
        ),
    }
