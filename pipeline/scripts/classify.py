"""Electorate classification — post-processing step.

Reads every per-seat JSON in ``site/public/seats/``, computes a tag set
along eight axes, and writes a ``classification`` block back into each
file. Not part of the AEC build pipeline — this is a derived calculation
that runs locally on already-built artefacts.

Run from the repo root:
    pipeline/.venv/bin/python pipeline/scripts/classify.py

Add ``--dry`` to print without writing.

Axes (in display order):
  1. income        — quartile of medianHouseholdIncomeWeekly
  2. age           — quartile of medianAge
  3. migrant       — quartile of bornOverseasPct
  4. education     — quartile of bachelorPlusPct
  5. tenure        — quartile of renterPct (proxy for owner/renter mix)
  6. indigenous    — absolute thresholds; chip only emitted if >=5%

Marginality and TCP-shape are intentionally not chips — that info is
already legible on the panel header (the result tag, the TCP bar, the
margin cell), and re-tagging it as a chip is just noise.

Quartile cuts are computed from the 150-seat distribution, so the labels
are *relative* to the national field. "Affluent" means top quartile of
electorates, not above some absolute pay packet.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SEATS_DIR = REPO / "site" / "public" / "seats"

QUARTILE_LABELS: dict[str, tuple[str, str, str, str]] = {
    "income":    ("Working-class",       "Mid-income",   "Comfortable",  "Affluent"),
    "age":       ("Young",                "Younger",      "Established",  "Greying"),
    "migrant":   ("Australian-born",      "Mixed origin", "Diverse",      "Multicultural"),
    "education": ("Vocation-heavy",       "Mixed",        "Tertiary",     "Graduate-heavy"),
    "tenure":    ("Home owner-dominant",  "Mortgage-belt","Mixed tenure", "Renter-heavy"),
}

QUARTILE_RANKS = ("low", "mid-low", "mid-high", "high")

AXIS_FIELDS: dict[str, str] = {
    "income":    "medianHouseholdIncomeWeekly",
    "age":       "medianAge",
    "migrant":   "bornOverseasPct",
    "education": "bachelorPlusPct",
    "tenure":    "renterPct",
}

# Plain-English description of each axis used in the chip tooltips.
AXIS_DESCRIPTIONS: dict[str, str] = {
    "income":    "median household income (weekly)",
    "age":       "median age",
    "migrant":   "share of residents born overseas",
    "education": "share holding a bachelor's degree or higher",
    "tenure":    "share of households renting",
}



def quartile_cuts(values: list[float]) -> tuple[float, float, float]:
    """Return (q1, median, q3) cuts for a value list."""
    qs = statistics.quantiles(values, n=4, method="inclusive")
    return qs[0], qs[1], qs[2]


def quartile_index(value: float, cuts: tuple[float, float, float]) -> int:
    """Map a value to a 0..3 quartile bucket using inclusive cuts."""
    q1, q2, q3 = cuts
    if value <= q1:
        return 0
    if value <= q2:
        return 1
    if value <= q3:
        return 2
    return 3


def indigenous_tag(pct: float | None) -> dict[str, Any] | None:
    """Three absolute tiers — the distribution is heavily right-skewed
    (most electorates <3% First Nations, only ~30 above 5%), so
    quartiles don't apply.

      - >=30%: "Plurality First Nations" (Lingiari only).
      - >=10%: ">10% First Nations" (~8 electorates).
      - >=5%:  ">5% First Nations" (~30 electorates, broadly regional).
    """
    if pct is None:
        return None
    p = float(pct)
    if p >= 30:
        label = "Plurality First Nations"
        meaning = ">30% of the electorate is First Nations"
    elif p >= 10:
        label = ">10% First Nations"
        meaning = ">10% of the electorate is First Nations"
    elif p >= 5:
        label = ">5% First Nations"
        meaning = ">5% of the electorate is First Nations"
    else:
        return None
    return {"axis": "indigenous", "label": label, "value": round(p, 1), "meaning": meaning}


def _quartile_meaning(axis: str, rank_idx: int) -> str:
    """One-line tooltip: which quartile + which metric. The seat's value
    is already visible in the demographic pillar of the panel, so the
    chip just needs to define the label."""
    descr = AXIS_DESCRIPTIONS[axis]
    band = "Top quartile" if rank_idx == 3 else "Bottom quartile"
    return f"{band} of electorates by {descr}"




def build_quartile_lookup(seats: list[dict[str, Any]]) -> dict[str, tuple[float, float, float]]:
    """Compute quartile cuts per demographic axis across all seats with
    a demographics block present.
    """
    cuts: dict[str, tuple[float, float, float]] = {}
    for axis, field in AXIS_FIELDS.items():
        values: list[float] = []
        for s in seats:
            d = s.get("demographics")
            if not d:
                continue
            v = d.get(field)
            if v is None:
                continue
            values.append(float(v))
        if len(values) < 4:
            continue
        cuts[axis] = quartile_cuts(values)
    return cuts


def classify_seat(
    seat: dict[str, Any],
    cuts: dict[str, tuple[float, float, float]],
) -> dict[str, Any]:
    """Build the classification block for a single seat payload."""
    tags: list[dict[str, Any]] = []

    demo = seat.get("demographics") or {}
    for axis, field in AXIS_FIELDS.items():
        value = demo.get(field)
        if value is None or axis not in cuts:
            continue
        idx = quartile_index(float(value), cuts[axis])
        # Only emit a chip for the top and bottom quartiles. Middle two
        # quartiles ("average for the field") get no chip — every visible
        # chip means the seat is genuinely atypical on that axis.
        if idx not in (0, 3):
            continue
        tags.append(
            {
                "axis": axis,
                "label": QUARTILE_LABELS[axis][idx],
                "rank": QUARTILE_RANKS[idx],
                "value": value,
                "meaning": _quartile_meaning(axis, idx),
            }
        )

    i_tag = indigenous_tag(demo.get("indigenousPct"))
    if i_tag:
        tags.append(i_tag)

    return {"version": 1, "tags": tags}


def main(argv: list[str]) -> int:
    dry = "--dry" in argv

    files = sorted(SEATS_DIR.glob("*.json"))
    if not files:
        print(f"NO seat JSONs at {SEATS_DIR}", file=sys.stderr)
        return 2

    seats: list[tuple[Path, dict[str, Any]]] = []
    for p in files:
        try:
            seats.append((p, json.loads(p.read_text())))
        except json.JSONDecodeError as exc:
            print(f"  ✗ {p.name}: malformed JSON — {exc}", file=sys.stderr)
            return 2

    payloads = [s for _, s in seats]
    cuts = build_quartile_lookup(payloads)

    print(f"Quartile cuts (across {len(payloads)} seats):")
    for axis in AXIS_FIELDS:
        if axis in cuts:
            q1, q2, q3 = cuts[axis]
            print(f"  {axis:9s}  Q1={q1:>8.2f}  median={q2:>8.2f}  Q3={q3:>8.2f}")
        else:
            print(f"  {axis:9s}  (insufficient data)")

    written = 0
    no_demo = 0
    for path, payload in seats:
        block = classify_seat(payload, cuts)
        payload["classification"] = block
        if not (payload.get("demographics") or {}):
            no_demo += 1
        if not dry:
            path.write_text(json.dumps(payload, indent=2))
        written += 1

    if no_demo:
        print(f"  note: {no_demo} seat(s) have no demographics block "
              "— they get an empty classification block.")

    print(f"\n{'Would write' if dry else 'Wrote'} classification to {written} seat JSON file(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
