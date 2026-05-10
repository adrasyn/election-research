"""Historical pillar aggregator (1996–2025 stretch goal; 2007–2025 v1).

For each (year, division) we want two compact records:
  • TPP  — Labor vs Coalition Two-Party Preferred percentage. Canonical
           for the trend chart line — always defined.
  • Primary — per-party first-preference share, used for the stacked
              primary-vote bar.

Cross-year joining is by lowercased division name. Redistribution-aware
joining (notional 2PP on current boundaries) is a Phase D refinement.
"""
from __future__ import annotations

import logging
from typing import Any

import polars as pl

from ..parties import css_key, display_short
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
    tpp_frames: list[pl.DataFrame],
    primary_frames: list[pl.DataFrame],
) -> dict[str, dict[str, Any]]:
    """Per-division-name history payload, ready to drop into seat JSON.

    Returns dict keyed by lowercased division name → {trend, primary},
    where:
      trend.years    = ['1996', '1998', ...]
      trend.alp[i]   = ALP TPP % for year i (None if seat absent that year)
      trend.coa[i]   = Coalition TPP % for year i
      primary.rows   = [{year, shares: {ALP:39.4, LIB:36.5, GRN:..., OTH:..}}]
    """
    if not tpp_frames:
        return {}
    tpp_all = pl.concat(tpp_frames, how="diagonal_relaxed").sort("year")
    primary_all = pl.concat(primary_frames, how="diagonal_relaxed").sort("year")

    years_sorted = sorted({int(y) for y in tpp_all["year"].unique().to_list()})
    out: dict[str, dict[str, Any]] = {}
    for div_lc in tpp_all["_div_lc"].unique().to_list():
        seat_tpp = tpp_all.filter(pl.col("_div_lc") == div_lc).sort("year")
        seat_pri = primary_all.filter(pl.col("_div_lc") == div_lc)
        # Trend: line per major-bloc.
        alp_series: list[float | None] = []
        coa_series: list[float | None] = []
        for y in years_sorted:
            row = seat_tpp.filter(pl.col("year") == y)
            if row.is_empty():
                alp_series.append(None)
                coa_series.append(None)
            else:
                r = row.row(0, named=True)
                alp_series.append(round(float(r["alpPct"]), 2))
                coa_series.append(round(float(r["coalitionPct"]), 2))
        # Primary stack: one row per year, with party shares.
        rows: list[dict[str, Any]] = []
        for y in years_sorted:
            this_year = seat_pri.filter(pl.col("year") == y)
            if this_year.is_empty():
                continue
            shares: dict[str, float] = {}
            other = 0.0
            for r in this_year.iter_rows(named=True):
                short = display_short(r["PartyAb"])
                # Roll fringe parties (<2%) into OTH bucket so the bar
                # doesn't fragment into invisible slivers.
                if (r["PartyAb"] is None) or (r["pct"] < 2.0 and short not in {"ALP", "LIB", "LNP", "NAT", "GRN", "ON", "CA"}):
                    other += float(r["pct"])
                    continue
                key = "LIB" if short in {"LIB", "LNP"} else short  # collapse Coalition Liberals
                shares[key] = round(shares.get(key, 0.0) + float(r["pct"]), 2)
            if other > 0:
                shares["OTH"] = round(shares.get("OTH", 0.0) + other, 2)
            rows.append({"year": str(y), "shares": shares})

        out[str(div_lc)] = {
            "trend": {
                "years": [str(y) for y in years_sorted],
                "alp": alp_series,
                "coalition": coa_series,
            },
            "primary": {"rows": rows},
        }
    return out
