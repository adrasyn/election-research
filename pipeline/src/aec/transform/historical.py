"""Historical pillar aggregator (1996–2025 stretch goal; 2007–2025 v1).

For each (year, division) we extract:
  • TCP pairing — the two finalists in the DOP final round. Captures
                  the *actual* contest each year (handles non-classic
                  seats like Warringah where the runner-up changed
                  from ALP to IND in 2019).
  • Primary    — per-party first-preference share, used for the
                  stacked primary-vote bar.

Cross-year joining is by lowercased division name. Redistribution-aware
joining (notional TCP on current boundaries) is a Phase D refinement.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import polars as pl

from ..parties import css_key, display_name, display_short
from ..sources.mediafeed import SKIP_ROWS

log = logging.getLogger(__name__)

_PREF_COUNT = "Preference Count"


def load_tpp(path) -> pl.DataFrame:
    """Read the TPP-by-division CSV (Labor vs Coalition per seat)."""
    return pl.read_csv(path, skip_rows=SKIP_ROWS, infer_schema_length=10000)


def tpp_by_division(tpp: pl.DataFrame, year: int) -> pl.DataFrame:
    """Tidy TPP frame: one row per (year, divisionNm, alp_pct, coalition_pct)."""
    # Column names contain spaces and need normalising.
    cols = {c.strip(): c for c in tpp.columns}
    alp_pct = cols.get("Australian Labor Party Percentage")
    coa_pct = cols.get("Liberal/National Coalition Percentage")
    if alp_pct is None or coa_pct is None:
        raise ValueError(f"Unexpected TPP schema for {year}: {tpp.columns}")
    return (
        tpp.select(
            pl.lit(year).alias("year"),
            pl.col("DivisionID").alias("divisionId"),
            pl.col("DivisionNm").alias("divisionNm"),
            pl.col("StateAb").alias("state"),
            pl.col(alp_pct).cast(pl.Float64).alias("alpPct"),
            pl.col(coa_pct).cast(pl.Float64).alias("coalitionPct"),
        )
        .with_columns(pl.col("divisionNm").str.to_lowercase().alias("_div_lc"))
    )


def tcp_pairings_by_division(dop: pl.DataFrame, year: int) -> pl.DataFrame:
    """Per (year, division), the two finalists in the DOP final round.

    Returned columns: year, divisionId, divisionNm, _div_lc, partyAb,
    party, surname, votes, pct, rank (1=winner, 2=runner-up).
    """
    pref = dop.filter(pl.col("CalculationType") == _PREF_COUNT)
    max_rounds = pref.group_by("DivisionID").agg(
        pl.col("CountNumber").max().alias("_mx")
    )
    final = pref.join(max_rounds, on="DivisionID").filter(
        pl.col("CountNumber") == pl.col("_mx")
    )
    # Sum votes by (division, party, surname) — for the rare seat with
    # multiple candidates from the same party in TCP, we still want
    # individual rows; surname disambiguates them downstream.
    agg = (
        final.group_by("DivisionID", "DivisionNm", "PartyAb", "Surname")
        .agg(pl.col("CalculationValue").cast(pl.Int64).sum().alias("votes"))
    )
    rows: list[dict[str, Any]] = []
    for div_id in agg["DivisionID"].unique().to_list():
        seat = agg.filter(pl.col("DivisionID") == div_id).sort("votes", descending=True)
        if len(seat) < 2:
            continue
        top2 = seat.head(2)
        total = int(top2["votes"].sum())
        for rank, r in enumerate(top2.iter_rows(named=True), start=1):
            rows.append(
                {
                    "year": year,
                    "divisionId": int(r["DivisionID"]),
                    "divisionNm": r["DivisionNm"],
                    "_div_lc": str(r["DivisionNm"]).lower(),
                    "partyAb": display_short(r["PartyAb"]),
                    "party": css_key(r["PartyAb"]),
                    "surname": r["Surname"],
                    "votes": int(r["votes"]),
                    "pct": round(int(r["votes"]) / total * 100, 2) if total else 0.0,
                    "rank": rank,
                }
            )
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def primary_by_division(dop: pl.DataFrame, year: int) -> pl.DataFrame:
    """Per-(year, division, partyAb) primary-vote share derived from DOP round 0.

    Multiple candidates from the same party (rare federally) are summed.
    """
    primary = dop.filter(
        (pl.col("CountNumber") == 0) & (pl.col("CalculationType") == _PREF_COUNT)
    )
    grouped = (
        primary.group_by("DivisionID", "DivisionNm", "StateAb", "PartyAb")
        .agg(pl.col("CalculationValue").cast(pl.Int64).sum().alias("votes"))
    )
    totals = (
        grouped.group_by("DivisionID")
        .agg(pl.col("votes").sum().alias("total"))
    )
    joined = grouped.join(totals, on="DivisionID", how="left").with_columns(
        (pl.col("votes") / pl.col("total") * 100).round(2).alias("pct"),
        pl.lit(year).alias("year"),
    )
    return joined.with_columns(pl.col("DivisionNm").str.to_lowercase().alias("_div_lc"))


def history_blocks(
    tcp_frames: list[pl.DataFrame],
    primary_frames: list[pl.DataFrame],
) -> dict[str, dict[str, Any]]:
    """Per-division-name history payload, ready to drop into seat JSON.

    The trend block is a multi-series chart — every party that ever
    reached the TCP gets a line, drawn only across the years it was
    in the final round. For Warringah this surfaces three lines: LIB
    throughout, ALP up to 2016, IND from 2019 onwards.

    Returns dict keyed by lowercased division name → { trend, primary }.
    """
    tcp_frames = [f for f in tcp_frames if not f.is_empty()]
    if not tcp_frames:
        return {}
    tcp_all = pl.concat(tcp_frames, how="diagonal_relaxed").sort("year")
    primary_all = pl.concat(primary_frames, how="diagonal_relaxed").sort("year")

    years_sorted = sorted({int(y) for y in tcp_all["year"].unique().to_list()})
    out: dict[str, dict[str, Any]] = {}
    for div_lc in tcp_all["_div_lc"].unique().to_list():
        seat_tcp = tcp_all.filter(pl.col("_div_lc") == div_lc).sort(["year", "rank"])
        seat_pri = primary_all.filter(pl.col("_div_lc") == div_lc)

        # ── Trend: build {partyAb → year → pct} sparse matrix ──
        by_party: dict[str, dict[int, float]] = defaultdict(dict)
        # Track the css `party` key + a representative label per partyAb.
        meta_for: dict[str, dict[str, str]] = {}
        for r in seat_tcp.iter_rows(named=True):
            pab = str(r["partyAb"])
            by_party[pab][int(r["year"])] = float(r["pct"])
            meta_for.setdefault(pab, {"party": str(r["party"]), "label": pab})

        # Order series so the most-frequent / most-recent stay top of
        # the legend. Sort by (years-present desc, latest-year desc).
        def sort_key(p: str) -> tuple[int, int]:
            yrs = by_party[p]
            return (-len(yrs), -max(yrs))

        series: list[dict[str, Any]] = []
        for pab in sorted(by_party.keys(), key=sort_key):
            points = [by_party[pab].get(y) for y in years_sorted]
            series.append(
                {
                    "id": pab,
                    "party": meta_for[pab]["party"],
                    "label": meta_for[pab]["label"],
                    "points": points,
                }
            )

        # ── Primary stack: one row per year ──
        rows: list[dict[str, Any]] = []
        for y in years_sorted:
            this_year = seat_pri.filter(pl.col("year") == y)
            if this_year.is_empty():
                continue
            shares: dict[str, float] = {}
            other = 0.0
            for r in this_year.iter_rows(named=True):
                short = display_short(r["PartyAb"])
                if (r["PartyAb"] is None) or (
                    r["pct"] < 2.0
                    and short not in {"ALP", "LIB", "LNP", "NAT", "GRN", "ON"}
                ):
                    other += float(r["pct"])
                    continue
                key = "LIB" if short in {"LIB", "LNP"} else short
                shares[key] = round(shares.get(key, 0.0) + float(r["pct"]), 2)
            if other > 0:
                shares["OTH"] = round(shares.get("OTH", 0.0) + other, 2)
            rows.append({"year": str(y), "shares": shares})

        out[str(div_lc)] = {
            "trend": {
                "years": [str(y) for y in years_sorted],
                "series": series,
            },
            "primary": {"rows": rows},
        }
    return out
