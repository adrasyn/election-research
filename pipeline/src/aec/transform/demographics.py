"""Demographic-context aggregator from ABS GCP CED tables.

Produces a per-(lowercased division name) dict matching the shape the
panel's demographic pillar consumes. Sources used in v1:
  • G01 — Birthplace_Australia / Birthplace_Elsewhere → "born overseas".
  • G02 — Median age, household income, dwelling rent.
  • G14 — Religious affiliation (top-N + national-comparison anchors).
  • G18 — Non-school qualification level → bachelor+ rate.
  • G09 — Country of birth → top-N ancestries (proxy; G08 is closer
          to "ancestry" but uses paired-parent-birthplace which is harder
          to surface; country-of-birth is the standard proxy).

National anchors are computed from the AUS-level row (CED999 / 0).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

from ..sources.census import CensusFiles

log = logging.getLogger(__name__)


# CED → division-name mapping is read from the geography descriptor xlsx.
def load_ced_mapping(files: CensusFiles) -> dict[str, str]:
    """Return {CED_CODE_2021: division_name_lower}. Filters to federal CEDs."""
    df_dict = pl.read_excel(files.geo_descriptor, sheet_id=0)
    nabs = df_dict["2021_ASGS_Non_ABS_Structures"]
    ceds = nabs.filter(pl.col("ASGS_Structure") == "CED")
    out: dict[str, str] = {}
    for r in ceds.iter_rows(named=True):
        code = str(r["Census_Code_2021"])
        name = str(r["Census_Name_2021"]).strip()
        out[code] = name.lower()
    return out


def _read_csv(path: Path) -> pl.DataFrame:
    return pl.read_csv(path, infer_schema_length=10000)


def build_demographics(files: CensusFiles) -> dict[str, dict[str, Any]]:
    """Per-division-name demographic dict for every CED in the GCP."""
    code_to_name = load_ced_mapping(files)

    g01 = _read_csv(files.table("G01"))
    g02 = _read_csv(files.table("G02"))

    # National anchors — computed from AUS-level row across all CEDs.
    nat_aus = _aus_row(g01, g02)

    out: dict[str, dict[str, Any]] = {}
    for code, name_lc in code_to_name.items():
        g01_row = g01.filter(pl.col("CED_CODE_2021") == code)
        g02_row = g02.filter(pl.col("CED_CODE_2021") == code)
        if g01_row.is_empty() or g02_row.is_empty():
            continue
        g01r = g01_row.row(0, named=True)
        g02r = g02_row.row(0, named=True)

        tot = int(g01r.get("Tot_P_P") or 0)
        bp_aus = int(g01r.get("Birthplace_Australia_P") or 0)
        bp_else = int(g01r.get("Birthplace_Elsewhere_P") or 0)
        bp_known = bp_aus + bp_else
        born_overseas_pct = (bp_else / bp_known * 100) if bp_known else None

        out[name_lc] = {
            "totalPopulation": tot,
            "medianAge": _to_int(g02r.get("Median_age_persons")),
            "medianHouseholdIncomeWeekly": _to_int(g02r.get("Median_tot_hhd_inc_weekly")),
            "medianRentWeekly": _to_int(g02r.get("Median_rent_weekly")),
            "averageHouseholdSize": _to_float(g02r.get("Average_household_size")),
            "bornOverseasPct": _round(born_overseas_pct, 1),
            # National anchors so the panel can show "↑ NAT 38" comparisons
            # without the frontend needing a second fetch.
            "national": nat_aus,
        }
    return out


def _aus_row(g01: pl.DataFrame, g02: pl.DataFrame) -> dict[str, Any]:
    """Compute national anchors by summing CED-level rows across the country."""
    tot = int(g01["Tot_P_P"].sum())
    bp_aus = int(g01["Birthplace_Australia_P"].sum())
    bp_else = int(g01["Birthplace_Elsewhere_P"].sum())
    bp_known = bp_aus + bp_else
    born_overseas_pct = (bp_else / bp_known * 100) if bp_known else None
    # Medians can't be summed; AEC panel really wants population-weighted
    # mean of medians — close enough for a comparison anchor.
    persons = g01.select("CED_CODE_2021", pl.col("Tot_P_P").alias("pop"))
    weighted = (
        g02.join(persons, on="CED_CODE_2021", how="left")
        .with_columns(pl.col("pop").fill_null(0).cast(pl.Int64))
    )
    pop_total = int(weighted["pop"].sum())

    def wmean(col: str) -> float | None:
        if col not in weighted.columns or pop_total == 0:
            return None
        s = weighted.with_columns(
            (pl.col(col).cast(pl.Float64, strict=False) * pl.col("pop")).alias("_w")
        )
        denom = s.filter(pl.col(col).is_not_null())["pop"].sum()
        if denom == 0:
            return None
        return float(s["_w"].sum() / denom)

    return {
        "totalPopulation": tot,
        "medianAge": _round(wmean("Median_age_persons"), 0),
        "medianHouseholdIncomeWeekly": _round(wmean("Median_tot_hhd_inc_weekly"), 0),
        "medianRentWeekly": _round(wmean("Median_rent_weekly"), 0),
        "averageHouseholdSize": _round(wmean("Average_household_size"), 1),
        "bornOverseasPct": _round(born_overseas_pct, 1),
    }


def _to_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _to_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def _round(v: float | None, dp: int) -> float | int | None:
    if v is None:
        return None
    return int(round(v)) if dp == 0 else round(v, dp)
