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
    g09a = _read_csv(files.table("G09A"))
    g09b = _read_csv(files.table("G09B"))
    g09c = _read_csv(files.table("G09C"))
    g14 = _read_csv(files.table("G14"))
    g37 = _read_csv(files.table("G37"))   # Tenure type × dwelling structure
    g49b = _read_csv(files.table("G49B")) # Non-school qualification (Persons)

    # Per-CED, per-country/-religion vote tallies.
    cob_per_ced = _country_of_birth_lookup(g09a, g09b, g09c)
    religion_per_ced = _religion_lookup(g14)
    cob_national = _country_of_birth_national(cob_per_ced, g01)
    religion_national = _religion_national(religion_per_ced, g14)

    # National anchors — computed from AUS-level row across all CEDs.
    nat_aus = _aus_row(g01, g02, g37=g37, g49b=g49b, cob_national=cob_national)
    nat_aus["countryOfBirth"] = cob_national
    nat_aus["religion"] = religion_national

    out: dict[str, dict[str, Any]] = {}
    for code, name_lc in code_to_name.items():
        g01_row = g01.filter(pl.col("CED_CODE_2021") == code)
        g02_row = g02.filter(pl.col("CED_CODE_2021") == code)
        g37_row = g37.filter(pl.col("CED_CODE_2021") == code)
        g49_row = g49b.filter(pl.col("CED_CODE_2021") == code)
        if g01_row.is_empty() or g02_row.is_empty():
            continue
        g01r = g01_row.row(0, named=True)
        g02r = g02_row.row(0, named=True)
        g37r = g37_row.row(0, named=True) if not g37_row.is_empty() else {}
        g49r = g49_row.row(0, named=True) if not g49_row.is_empty() else {}

        tot = int(g01r.get("Tot_P_P") or 0)
        # Born-overseas + country-of-birth share the same denominator —
        # bp_known = people who answered the country-of-birth question
        # (G01.Birthplace_Australia + G01.Birthplace_Elsewhere). G01
        # captures everyone who answered, so bp_known is more complete
        # than summing G09 buckets (G09 only itemises the top ~60
        # countries individually). Australia% + Born overseas% = 100%.
        bp_aus = int(g01r.get("Birthplace_Australia_P") or 0)
        bp_else = int(g01r.get("Birthplace_Elsewhere_P") or 0)
        bp_known = bp_aus + bp_else
        born_overseas_pct = (bp_else / bp_known * 100) if bp_known else None
        cob_bucket = cob_per_ced.get(code, {})

        # Indigenous % (Aboriginal + Torres Strait Is + Both)
        indig = int(g01r.get("Indigenous_P_Tot_P") or 0)
        indigenous_pct = (indig / tot * 100) if tot else None

        # Bachelor+ rate: bachelor + grad dip/cert + postgrad over total
        # qualification-eligible (P_Tot_Total = persons aged 15+).
        pgrad = int(g49r.get("P_PGrad_Deg_Total") or 0)
        gdip = int(g49r.get("P_GradDip_and_GradCert_Total") or 0)
        bach = int(g49r.get("P_BachDeg_Total") or 0)
        qual_total = int(g49r.get("P_Tot_Total") or 0)
        bachelor_plus_pct = ((pgrad + gdip + bach) / qual_total * 100) if qual_total else None

        # Tenure: renter share of all dwellings (incl. social housing).
        rent_total = int(g37r.get("R_Tot_Total") or 0)
        all_dwell = int(g37r.get("Total_Total") or 0)
        renter_pct = (rent_total / all_dwell * 100) if all_dwell else None

        out[name_lc] = {
            "totalPopulation": tot,
            "medianAge": _to_int(g02r.get("Median_age_persons")),
            "medianHouseholdIncomeWeekly": _to_int(g02r.get("Median_tot_hhd_inc_weekly")),
            "medianRentWeekly": _to_int(g02r.get("Median_rent_weekly")),
            "averageHouseholdSize": _to_float(g02r.get("Average_household_size")),
            "bornOverseasPct": _round(born_overseas_pct, 1),
            "indigenousPct": _round(indigenous_pct, 1),
            "bachelorPlusPct": _round(bachelor_plus_pct, 1),
            "renterPct": _round(renter_pct, 1),
            # COB uses its own bucket-total denominator (people who
            # answered the question) so percentages sum to ~100% and
            # match the born-overseas% above.
            # Denominator is bp_known so Australia% + Born overseas% = 100%.
            # Listed top-5 will not sum to 100% (the long tail of unlisted
            # countries is implicit in the "100% − Australia − top4" gap).
            "countryOfBirth": _top_n_with_anchor(cob_bucket, bp_known, cob_national, n=5),
            "religion": _top_n_with_anchor(religion_per_ced.get(code, {}), tot, religion_national, n=5),
            "national": nat_aus,
        }
    return out


# ── Country of birth (G09 A/B/C) ──

def _country_of_birth_lookup(
    g09a: pl.DataFrame, g09b: pl.DataFrame, g09c: pl.DataFrame
) -> dict[str, dict[str, int]]:
    """Per-CED dict of {country_label: persons-count}."""
    male_cols = _country_total_cols(g09a, "M") + _country_total_cols(g09b, "M")
    female_cols = _country_total_cols(g09c, "F")
    out: dict[str, dict[str, int]] = {}
    for code in g09a["CED_CODE_2021"].to_list():
        bucket: dict[str, int] = {}
        for sex_df, prefix, cols in (
            (g09a, "M", male_cols),
            (g09b, "M", male_cols),
            (g09c, "F", female_cols),
        ):
            row = sex_df.filter(pl.col("CED_CODE_2021") == code)
            if row.is_empty():
                continue
            r = row.row(0, named=True)
            for col, country in cols:
                if col in r:
                    bucket[country] = bucket.get(country, 0) + int(r[col] or 0)
        out[code] = bucket
    return out


def _country_total_cols(df: pl.DataFrame, sex_prefix: str) -> list[tuple[str, str]]:
    """Return [(column_name, display_country_name)] for every <sex>_<country>_Tot col."""
    out = []
    for c in df.columns:
        if c.startswith(f"{sex_prefix}_") and c.endswith("_Tot"):
            mid = c[len(sex_prefix) + 1 : -len("_Tot")]
            country = _humanise_country(mid)
            out.append((c, country))
    return out


_COUNTRY_FIXES = {
    "Bosnia_Herzegov": "Bosnia & Herzegovina",
    "Hong_Kong_SAR_Ch": "Hong Kong (SAR)",       # ABS short-header truncates
    "Hong_Kong_SAR_China": "Hong Kong (SAR)",
    "Korea_South": "South Korea",
    "Korea_North": "North Korea",
    "South_Africa": "South Africa",
    "United_Kingdom_CnI": "United Kingdom",
    "United_Kingdom_Channel_Is_IoM": "United Kingdom",
    "USA": "United States",
    "FYROM": "North Macedonia",
    "Hmong_SE_Asia_nec": "Hmong (SE Asia)",
    "SE_Europe_nfd": "Other SE Europe",
    "South_Eastern_Eur_nfd": "Other SE Europe",
    "Tot_resp": None,
    "BP_NS": None,
    "Tot": None,
}


def _humanise_country(token: str) -> str:
    """Convert an underscored ABS country code to a readable label."""
    if token in _COUNTRY_FIXES:
        v = _COUNTRY_FIXES[token]
        return v if v is not None else "_skip"
    return token.replace("_", " ")


def _country_of_birth_national(
    per_ced: dict[str, dict[str, int]], g01: pl.DataFrame
) -> dict[str, dict[str, Any]]:
    """National per-country totals + persons-percent.

    Denominator is bp_known (G01 Australia + Elsewhere), matching the
    per-CED Country-of-Birth bar denominator. Listed top-N won't sum
    to 100% (G09 only itemises the top ~60 countries).
    """
    sums: dict[str, int] = {}
    for ced_counts in per_ced.values():
        for country, n in ced_counts.items():
            sums[country] = sums.get(country, 0) + n
    nat_bp_aus = int(g01["Birthplace_Australia_P"].sum())
    nat_bp_else = int(g01["Birthplace_Elsewhere_P"].sum())
    nat_bp_known = (nat_bp_aus + nat_bp_else) or 1
    return {
        country: {
            "count": n,
            "pct": round(n / nat_bp_known * 100, 2),
        }
        for country, n in sums.items()
    }


# ── Religion (G14) ──

# Map raw G14 column → display label. Christianity sub-types collapse
# into "Christianity (total)" since the panel surfaces broad categories.
_RELIGION_DISPLAY = {
    "Christianity_Tot_P": "Christianity",
    "Buddhism_P": "Buddhism",
    "Hinduism_P": "Hinduism",
    "Islam_P": "Islam",
    "Judaism_P": "Judaism",
    "SB_OSB_NRA_NR_P": "No religion",
    "Religious_affiliation_ns_P": "Not stated",
    "Other_Religions_Tot_P": "Other religions",
}


def _religion_lookup(g14: pl.DataFrame) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for r in g14.iter_rows(named=True):
        bucket: dict[str, int] = {}
        for col, label in _RELIGION_DISPLAY.items():
            if col in r:
                bucket[label] = int(r[col] or 0)
        out[str(r["CED_CODE_2021"])] = bucket
    return out


def _religion_national(
    per_ced: dict[str, dict[str, int]], g14: pl.DataFrame
) -> dict[str, dict[str, Any]]:
    nat_pop = int(g14["Tot_P"].sum()) if "Tot_P" in g14.columns else None
    sums: dict[str, int] = {}
    for ced in per_ced.values():
        for k, v in ced.items():
            sums[k] = sums.get(k, 0) + v
    return {
        label: {
            "count": n,
            "pct": round(n / nat_pop * 100, 2) if nat_pop else 0.0,
        }
        for label, n in sums.items()
    }


def _top_n_with_anchor(
    counts: dict[str, int],
    tot: int,
    national: dict[str, dict[str, Any]],
    n: int,
) -> list[dict[str, Any]]:
    """Top-n sorted by local count desc, with national pct anchor each."""
    if not counts or not tot:
        return []
    items: list[tuple[str, int]] = [
        (label, c) for label, c in counts.items() if label != "_skip" and c > 0
    ]
    items.sort(key=lambda x: x[1], reverse=True)
    out = []
    for label, c in items[:n]:
        nat_entry = national.get(label, {})
        out.append(
            {
                "label": label,
                "count": c,
                "pct": round(c / tot * 100, 2) if tot else 0.0,
                "natPct": nat_entry.get("pct", 0.0),
            }
        )
    return out


def _aus_row(g01: pl.DataFrame, g02: pl.DataFrame, g37: pl.DataFrame | None = None,
             g49b: pl.DataFrame | None = None,
             cob_national: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compute national anchors by summing CED-level rows across the country.

    born_overseas_pct uses the sum of country-of-birth bucket counts
    (the same denominator the per-CED bars use) so the per-seat born-
    overseas% and the AUS-anchor born-overseas% are computed the same way.
    """
    tot = int(g01["Tot_P_P"].sum())
    bp_aus = int(g01["Birthplace_Australia_P"].sum())
    bp_else = int(g01["Birthplace_Elsewhere_P"].sum())
    bp_known = bp_aus + bp_else
    born_overseas_pct = (bp_else / bp_known * 100) if bp_known else None
    indig = int(g01["Indigenous_P_Tot_P"].sum())
    indigenous_pct = (indig / tot * 100) if tot else None

    bachelor_plus_pct = None
    if g49b is not None:
        try:
            bach_n = int(g49b["P_BachDeg_Total"].sum())
            grad_n = int(g49b["P_GradDip_and_GradCert_Total"].sum())
            pgrd_n = int(g49b["P_PGrad_Deg_Total"].sum())
            qual_d = int(g49b["P_Tot_Total"].sum())
            if qual_d:
                bachelor_plus_pct = (bach_n + grad_n + pgrd_n) / qual_d * 100
        except Exception:  # noqa: BLE001
            pass

    renter_pct = None
    if g37 is not None:
        try:
            rent_n = int(g37["R_Tot_Total"].sum())
            dwell_d = int(g37["Total_Total"].sum())
            if dwell_d:
                renter_pct = rent_n / dwell_d * 100
        except Exception:  # noqa: BLE001
            pass

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
        "indigenousPct": _round(indigenous_pct, 1),
        "bachelorPlusPct": _round(bachelor_plus_pct, 1),
        "renterPct": _round(renter_pct, 1),
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
