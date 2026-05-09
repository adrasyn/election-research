"""Distribution-of-preferences transform.

Takes the AEC `HouseDopByDivisionDownload` CSV and reshapes it into the
exact structure the frontend's `lib/waterfall.js` consumes — one entry
per round, mapping CandidateID → vote count, plus an `exhausted` key
when applicable.
"""
from __future__ import annotations

from typing import Any

import polars as pl

from ..parties import css_key, display_name
from ..sources.mediafeed import SKIP_ROWS

# In the DOP CSV, each candidate has multiple rows per round indexed by
# CalculationType. We only care about cumulative vote totals.
_CALC_PREFERENCE_COUNT = "Preference Count"


def load_dop(path) -> pl.DataFrame:
    """Read the AEC DOP CSV with the standard header skip."""
    return pl.read_csv(path, skip_rows=SKIP_ROWS, infer_schema_length=10000)


def waterfall_for_division(dop: pl.DataFrame, division_id: int) -> dict[str, Any]:
    """Build the dict that `renderWaterfall(host, data)` consumes for one seat.

    Schema:
        {
          totalFormal: int,
          candidates: [{ id, party, displayShort, displayLong }],
          rounds: [
            { <candidateId>: votes, ..., exhausted?: votes },
            ...
          ]
        }
    """
    seat = dop.filter(
        (pl.col("DivisionID") == division_id)
        & (pl.col("CalculationType") == _CALC_PREFERENCE_COUNT)
    )
    if seat.is_empty():
        raise ValueError(f"No DOP rows for division {division_id}")

    # Candidate metadata (from primary round = CountNumber 0).
    primary = seat.filter(pl.col("CountNumber") == 0)
    candidates = []
    for row in primary.iter_rows(named=True):
        cid = _candidate_id(row["CandidateID"])
        candidates.append(
            {
                "id": cid,
                "party": css_key(row["PartyAb"]),
                "displayShort": (row["PartyAb"] or "IND").upper(),
                "displayLong": (
                    f"{(row['PartyAb'] or 'IND').upper()} — {_titlecase(row['Surname'])}"
                ),
                "_partyName": display_name(row["PartyAb"]),
                "_surname": row["Surname"],
                "_givenName": row["GivenNm"],
            }
        )

    # AEC encodes "Exhausted" as a row with empty/missing CandidateID and
    # PartyAb. We need to detect those and roll them into an `exhausted`
    # round key. Inspect the first non-zero round to identify the schema.
    rounds: list[dict[str, Any]] = []
    for round_num in sorted(seat["CountNumber"].unique().to_list()):
        round_df = seat.filter(pl.col("CountNumber") == round_num)
        bucket: dict[str, Any] = {}
        exhausted_total = 0
        total = 0
        for row in round_df.iter_rows(named=True):
            votes = int(row["CalculationValue"] or 0)
            total += votes
            cid_raw = row["CandidateID"]
            surname = (row["Surname"] or "").strip().upper()
            if not cid_raw or surname in {"EXHAUSTED", "EXHAUSTED VOTES"}:
                exhausted_total += votes
                continue
            # AEC sometimes lists a candidate with 0 votes once they're
            # excluded from the count. Drop them — they shouldn't appear
            # in subsequent rounds.
            if votes <= 0:
                continue
            bucket[_candidate_id(cid_raw)] = votes
        if exhausted_total > 0:
            bucket["exhausted"] = exhausted_total
        rounds.append(bucket)

    # Conservation check: every round should sum to the same total.
    totals = {sum(int(v) for v in r.values()) for r in rounds}
    if len(totals) > 1:
        raise ValueError(
            f"Distribution-of-preferences not conserved for division {division_id}: "
            f"round totals were {sorted(totals)}"
        )
    total_formal = totals.pop()

    return {
        "totalFormal": total_formal,
        "candidates": [
            {k: v for k, v in c.items() if not k.startswith("_")} for c in candidates
        ],
        "rounds": rounds,
        # Stash candidate metadata for callers that need surnames etc.
        "_candidate_meta": {c["id"]: c for c in candidates},
    }


def _candidate_id(raw: int | str) -> str:
    """Stable string id for use as a JSON key (matches frontend convention)."""
    return f"C{int(raw)}"


def _titlecase(s: str | None) -> str:
    if not s:
        return ""
    # Surnames in AEC are uppercased; "MCMAHON" → "McMahon" needs care, but
    # standard Title-case works for most. Special-cases can be added later.
    return s.title()
