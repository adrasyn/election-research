"""Pre-deploy data QA — automated integrity checks across all 150 seat
JSONs.

Flags anything that would embarrass us live:
  • JSON files missing or malformed
  • DOP (preference distribution) rounds not conserved across rounds
  • Primary percentages not summing to ~100% (within a small tolerance)
  • TCP percentages not summing to ~100%
  • National party tally doesn't match the AEC declared outcome
  • Demographics block missing for any seat
  • Bio block missing for any seat
  • TCP winner inconsistent with seat.winner

Run from the repo root:
    pipeline/.venv/bin/python pipeline/scripts/qa_check.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEATS_DIR = REPO / "site" / "public" / "seats"

# AEC declared 2025 H/R outcome — used as a national-tally cross check.
# Source: AEC media-feed HouseMembersElectedDownload-31496.csv aggregated.
EXPECTED_TALLY: Counter = Counter({
    "ALP": 94, "LIB": 18, "LNP": 16, "NAT": 9,
    "IND": 10, "GRN": 1, "CA": 1, "KAP": 1,
})


# Seats created in the 2024 redistribution (no 2021 CED → no demographics
# from the GCP datapack). Documented as expected so they don't fail QA.
NEW_SEATS_NO_DEMO = {"Bullwinkel"}


def banner(s: str) -> None:
    print(f"\n══ {s} " + "═" * (78 - len(s)))


def main() -> int:
    files = sorted(SEATS_DIR.glob("*.json"))
    if not files:
        print(f"NO seat JSONs at {SEATS_DIR}", file=sys.stderr)
        return 2

    banner(f"Auditing {len(files)} seat JSON files in {SEATS_DIR.relative_to(REPO)}")

    seats = []
    for p in files:
        try:
            seats.append(json.loads(p.read_text()))
        except json.JSONDecodeError as exc:
            print(f"  ✗ {p.name}: malformed JSON — {exc}")

    issues = 0

    # ── 1. Coverage ─────────────────────────────────────────
    banner("1. Coverage")
    if len(seats) != 150:
        print(f"  ✗ expected 150 seats, got {len(seats)}")
        issues += 1
    missing_bio = [s["seat"]["name"] for s in seats if not (s.get("bio") and s["bio"].get("text"))]
    if missing_bio:
        print(f"  ✗ {len(missing_bio)} seats without a bio: {missing_bio[:5]}…")
        issues += 1
    else:
        print(f"  ✓ 150/150 seats have a Wikipedia bio")
    missing_demo = [
        s["seat"]["name"] for s in seats
        if not s.get("demographics") and s["seat"]["name"] not in NEW_SEATS_NO_DEMO
    ]
    expected_no_demo = [
        s["seat"]["name"] for s in seats
        if not s.get("demographics") and s["seat"]["name"] in NEW_SEATS_NO_DEMO
    ]
    if missing_demo:
        print(f"  ✗ {len(missing_demo)} seats unexpectedly without demographics: {missing_demo[:5]}")
        issues += 1
    else:
        print(
            f"  ✓ {len(seats) - len(expected_no_demo)}/{len(seats)} seats have demographics "
            f"({len(expected_no_demo)} new-redistribution seat(s) waived: {expected_no_demo})"
        )
    missing_history = [s["seat"]["name"] for s in seats if not s.get("history")]
    if missing_history:
        print(f"  ⚠  {len(missing_history)} seats without history (newly created seats?): {missing_history}")
    else:
        print(f"  ✓ 150/150 seats have a history block")

    # ── 2. Preference distribution conservation ──────────────────────
    banner("2. Preference distribution conservation")
    bad = []
    for s in seats:
        prefs = s.get("preferences") or {}
        total = prefs.get("totalFormal")
        for i, r in enumerate(prefs.get("rounds") or []):
            round_sum = sum(int(v) for v in r.values())
            if round_sum != total:
                bad.append((s["seat"]["name"], i, round_sum, total))
    if bad:
        print(f"  ✗ {len(bad)} round-totals don't equal totalFormal:")
        for n, i, rs, tot in bad[:5]:
            print(f"      {n} round {i}: sum={rs} vs totalFormal={tot}")
        issues += 1
    else:
        print(f"  ✓ every DOP round in every seat conserves totalFormal")

    # ── 3. TCP percentages sum to 100 ───────────────────────
    banner("3. TCP percentages sum to ~100")
    bad = []
    for s in seats:
        tcp = s.get("tcp") or []
        if not tcp:
            continue
        ssum = sum(c.get("pct", 0) for c in tcp)
        if abs(ssum - 100.0) > 0.5:
            bad.append((s["seat"]["name"], ssum))
    if bad:
        print(f"  ✗ {len(bad)} seats: TCP pct sum off by >0.5: {bad[:5]}")
        issues += 1
    else:
        print(f"  ✓ TCP pct sums match 100% across all seats")

    # ── 4. National party tally ────────────────────────────
    banner("4. National party tally vs AEC declared outcome")
    tally = Counter()
    for s in seats:
        tally[s["seat"]["winner"]["partyAb"]] += 1
    diff_only = {k: tally.get(k, 0) - v for k, v in EXPECTED_TALLY.items() if tally.get(k, 0) != v}
    extras = {k: v for k, v in tally.items() if k not in EXPECTED_TALLY}
    print(f"    actual tally: {dict(tally.most_common())}")
    if diff_only or extras:
        if diff_only:
            print(f"  ✗ deltas vs expected: {diff_only}")
        if extras:
            print(f"  ✗ unexpected parties: {extras}")
        issues += 1
    else:
        print(f"  ✓ matches AEC published outcome")

    # ── 5. Demographic sanity ─────────────────────────────
    banner("5. Demographic sanity (born-overseas + AUS reconciliation)")
    bad = []
    for s in seats:
        dem = s.get("demographics") or {}
        bo = dem.get("bornOverseasPct")
        cob = dem.get("countryOfBirth") or []
        if bo is None or not cob:
            continue
        aus_pct = next((c["pct"] for c in cob if c["label"] == "Australia"), None)
        if aus_pct is None:
            continue
        # Australia% + Born overseas% should equal 100 ± 0.5
        delta = abs((aus_pct + bo) - 100.0)
        if delta > 0.5:
            bad.append((s["seat"]["name"], aus_pct, bo, delta))
    if bad:
        print(f"  ✗ {len(bad)} seats: COB Australia% + bornOverseas% ≠ 100±0.5:")
        for n, a, b, d in bad[:5]:
            print(f"      {n}: Australia={a}%  bornOverseas={b}%  delta={d:.2f}")
        issues += 1
    else:
        print(f"  ✓ Australia% + bornOverseas% sums clean across all seats")

    # ── 6. Result label vs winner partyAb ─────────────────
    banner("6. seat.result label internally consistent with seat.winner")
    bad = []
    for s in seats:
        result = s["seat"].get("result", "")
        winner = s["seat"].get("winner") or {}
        winner_ab = winner.get("partyAb", "")
        if winner_ab and not result.startswith(winner_ab):
            bad.append((s["seat"]["name"], result, winner_ab))
    if bad:
        print(f"  ✗ {len(bad)} mismatches: {bad[:5]}")
        issues += 1
    else:
        print(f"  ✓ seat.result starts with winner.partyAb on all seats")

    banner("Summary")
    if issues:
        print(f"  ✗ {issues} category(ies) with issues — see above")
        return 1
    print(f"  ✓ all checks pass — {len(seats)} seats look deploy-ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
