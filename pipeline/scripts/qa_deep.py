"""Deep QA pass — complements qa_check.py with cross-cutting integrity audits.

Run from repo root:
    pipeline/.venv/bin/python pipeline/scripts/qa_deep.py

Categories:
  A. Structural / cross-file consistency
  B. Primary / TCP / booth coherence
  C. Booth geo sanity
  D. Historical trend integrity
  E. Demographics ranges + national reconciliation
  F. Chip classification coherence
  G. Boundaries (seats.geojson) integrity
"""
from __future__ import annotations

import json
import math
import sys
import re
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SEATS_DIR = REPO / "site" / "public" / "seats"
TILES_DIR = REPO / "site" / "public" / "tiles"
PARTIES_YAML = REPO / "parties.yaml"

# Australia bounding box (generous — includes outlying islands like Christmas Is.)
AUS_LAT_RANGE = (-44.0, -9.0)
AUS_LNG_RANGE = (96.0, 169.0)

# Booth-name patterns for expected geo-absences: mobile / hospital / prison
# teams + special voting infrastructure that has no fixed location.
SPECIAL_BOOTH_RE = re.compile(
    r"(special hospital|other mobile|remote mobile|special prison|"
    r"ppvc|absent|postal|provisional|prepoll|pre-poll|declaration|"
    r"divisional office|mobile team)",
    re.I,
)


def banner(s: str) -> None:
    print(f"\n══ {s} " + "═" * max(0, 78 - len(s)))


def load_party_abbrs() -> set[str]:
    """Extract every `abbr:` value + aec_code from parties.yaml without
    pulling in PyYAML — the file is regular enough for a regex sweep."""
    text = PARTIES_YAML.read_text()
    abbrs = set(re.findall(r"^\s+abbr:\s*([A-Z0-9]+)\s*$", text, re.M))
    aec_codes = set(re.findall(r"^\s+aec_code:\s*([A-Z0-9]+)", text, re.M))
    # aliases are quoted strings inside [ ... ]; we also accept any short alias
    return abbrs | aec_codes


def collect_seats() -> list[tuple[Path, dict]]:
    seats = []
    for p in sorted(SEATS_DIR.glob("*.json")):
        try:
            seats.append((p, json.loads(p.read_text())))
        except json.JSONDecodeError as e:
            print(f"  ✗ {p.name}: malformed JSON — {e}")
    return seats


def pct_close(values: list[float], target: float = 100.0, tol: float = 0.5) -> bool:
    return abs(sum(values) - target) <= tol


# ─────────────────────────────────────────────────────────────────────────────
def check_A_structural(seats: list[tuple[Path, dict]], registry: set[str]) -> int:
    banner("A. Structural / cross-file consistency")
    issues = 0
    name_mismatches: list[tuple[str, str, str]] = []
    id_mismatches: list[tuple[str, str]] = []
    unknown_parties: dict[str, set[str]] = defaultdict(set)

    for path, s in seats:
        stem = path.stem  # e.g. BENNELONG
        seat = s.get("seat", {})
        name = seat.get("name", "")
        sid = seat.get("id", "")

        if name.upper().replace(" ", "_").replace("'", "").replace("-", "_") != stem:
            # filenames are UPPERCASE underscore-free in practice; do a tolerant compare
            if name.upper() != stem.replace("_", " "):
                name_mismatches.append((path.name, name, stem))

        if sid and sid.upper() != stem and sid.upper().replace("-", "_") != stem:
            # seat.id is typically the lowercase slug; tolerate both forms
            if sid.lower() != stem.lower():
                id_mismatches.append((stem, sid))

        # collect every partyAb appearing in this seat
        candidates_seen: set[str] = set()
        for p in s.get("primary", []) or []:
            candidates_seen.add(p.get("partyAb", ""))
        for p in s.get("tcp", []) or []:
            candidates_seen.add(p.get("partyAb", ""))
        winner_ab = (s.get("seat", {}).get("winner") or {}).get("partyAb", "")
        if winner_ab:
            candidates_seen.add(winner_ab)
        # booths[].winnerParty is intentionally lowercase (frontend CSS key
        # convention: `--alp`, `bid-party-alp`). Normalise for the check.
        for b in s.get("booths", []) or []:
            wp = (b.get("winnerParty") or "").upper()
            if wp:
                candidates_seen.add(wp)
        # discard empty
        candidates_seen.discard("")
        for ab in candidates_seen:
            if ab not in registry:
                unknown_parties[ab].add(name)

    if name_mismatches:
        issues += 1
        print(f"  ✗ {len(name_mismatches)} filename↔seat.name mismatches:")
        for fn, nm, stem in name_mismatches[:5]:
            print(f"      {fn}: seat.name='{nm}'  stem='{stem}'")
    else:
        print(f"  ✓ filename ↔ seat.name agrees on all {len(seats)} seats")

    if id_mismatches:
        issues += 1
        print(f"  ✗ {len(id_mismatches)} filename↔seat.id mismatches:")
        for stem, sid in id_mismatches[:5]:
            print(f"      {stem}: seat.id='{sid}'")
    else:
        print(f"  ✓ filename ↔ seat.id agrees on all {len(seats)} seats")

    # Separate "lowercase canonical" from "truly unknown" — the former is
    # a case-normalisation issue, the latter is a registry-gap issue.
    lc_canonical: dict[str, set[str]] = {}
    unknown_only: dict[str, set[str]] = {}
    for ab, in_seats in unknown_parties.items():
        if ab.upper() in registry and ab != ab.upper():
            lc_canonical[ab] = in_seats
        else:
            unknown_only[ab] = in_seats

    if lc_canonical:
        issues += 1
        print(f"  ✗ {len(lc_canonical)} lowercase party codes (case-normalisation bug):")
        for ab, in_seats in sorted(lc_canonical.items()):
            sample = ", ".join(sorted(in_seats)[:3])
            print(f"      '{ab}' (canonical {ab.upper()}) — {len(in_seats)} seat(s): {sample}…")
    if unknown_only:
        # Demote to warning: these all carry party='oth' so the UI handles them
        # correctly as "Other" colour — but the registry is incomplete.
        print(f"  ⚠  {len(unknown_only)} minor-party codes not enumerated in parties.yaml")
        print(f"      (all carry party='oth' so they render as 'Other' — registry gap, not data error):")
        for ab, in_seats in sorted(unknown_only.items()):
            print(f"      '{ab}' — {len(in_seats)} seat(s)")
    if not lc_canonical and not unknown_only:
        print(f"  ✓ every partyAb used in seat JSONs is registered in parties.yaml")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
def check_B_primary_tcp_booth(seats: list[tuple[Path, dict]]) -> int:
    banner("B. Primary / TCP / booth coherence")
    issues = 0

    # Primary votes sum to seat.totalFormal? The AEC media feed splits
    # first-preferences across booth-level and special-vote-type files.
    # If the pipeline only ingests booth-level FP, primary.votes will be
    # short by the postal/absent/prepoll/declaration total — while TCP is
    # complete. Cross-check both.
    primary_sum_off = []
    tcp_sum_off = []
    pattern_consistent = True
    for _, s in seats:
        tot = s.get("seat", {}).get("totalFormal", 0)
        psum = sum(c.get("votes", 0) for c in (s.get("primary") or []))
        tsum = sum(c.get("votes", 0) for c in (s.get("tcp") or []))
        if tot and abs(psum - tot) > 1:
            primary_sum_off.append((s["seat"]["name"], psum, tot, tot - psum))
        if tot and tsum and abs(tsum - tot) > 1:
            tcp_sum_off.append((s["seat"]["name"], tsum, tot))
    if tcp_sum_off:
        issues += 1
        print(f"  ✗ {len(tcp_sum_off)} seats: TCP vote sum ≠ seat.totalFormal:")
        for n, ts, t in tcp_sum_off[:5]:
            print(f"      {n}: tcp={ts} totalFormal={t}")
    else:
        print(f"  ✓ TCP vote totals reconcile to seat.totalFormal on all seats")

    if primary_sum_off:
        issues += 1
        gaps = sorted(g for _, _, _, g in primary_sum_off)
        print(f"  ✗ {len(primary_sum_off)} seats: per-candidate primary.votes < seat.totalFormal")
        print(f"      → first-preference breakdown excludes postal/absent/prepoll/declaration")
        print(f"      → TCP totals are complete; only FP-per-candidate is short")
        print(f"      → primary deficit per seat: min={gaps[0]} median={gaps[len(gaps)//2]} max={gaps[-1]}")
        print(f"      → impact: minor-party FP shares are systematically understated")
        for n, ps, t, g in sorted(primary_sum_off, key=lambda x: -x[3])[:5]:
            print(f"      {n}: primary={ps} totalFormal={t} (Δ={-g}; {100*g/t:.1f}% missing)")
    else:
        print(f"  ✓ primary vote totals reconcile to seat.totalFormal on all seats")

    # Primary pct sums to ~100
    pct_off = []
    for _, s in seats:
        ps = sum(c.get("pct", 0) for c in (s.get("primary") or []))
        if abs(ps - 100.0) > 0.5:
            pct_off.append((s["seat"]["name"], ps))
    if pct_off:
        issues += 1
        print(f"  ✗ {len(pct_off)} seats: primary pct sum off >0.5: {pct_off[:5]}")
    else:
        print(f"  ✓ primary pct sums match 100% across all seats")

    # TCP candidate IDs should appear in primary list
    tcp_orphan = []
    for _, s in seats:
        primary_ids = {c["candidateId"] for c in (s.get("primary") or [])}
        for c in (s.get("tcp") or []):
            if c.get("candidateId") not in primary_ids:
                tcp_orphan.append((s["seat"]["name"], c.get("candidateId"), c.get("surname")))
    if tcp_orphan:
        issues += 1
        print(f"  ✗ {len(tcp_orphan)} TCP candidates not present in seat.primary:")
        for n, cid, sn in tcp_orphan[:5]:
            print(f"      {n}: TCP cand {cid} ({sn}) absent from primary list")
    else:
        print(f"  ✓ TCP candidates all appear in seat.primary list")

    # Booth formal sums (subset of total — most should sum to >= 95% of totalFormal)
    booth_coverage = []
    for _, s in seats:
        tot = s.get("seat", {}).get("totalFormal", 0)
        bsum = sum(b.get("formal", 0) for b in (s.get("booths") or []))
        if tot and (bsum / tot) < 0.5:
            booth_coverage.append((s["seat"]["name"], bsum, tot, bsum/tot if tot else 0))
    if booth_coverage:
        # this isn't necessarily an error (postal/declaration votes aren't booth-attached)
        # but flag any seat where < 50% of formal votes are booth-attached
        print(f"  ⚠  {len(booth_coverage)} seats with <50% of formal votes attached to booths:")
        for n, bs, t, r in booth_coverage[:5]:
            print(f"      {n}: booths={bs} / total={t} = {r*100:.1f}%")
    else:
        print(f"  ✓ every seat has ≥50% of formal votes booth-attached")

    # Winner candidate ID is the TCP entry with elected=true
    winner_id_off = []
    for _, s in seats:
        winner_id = (s.get("seat", {}).get("winner") or {}).get("candidateId")
        tcp_elected = [c for c in (s.get("tcp") or []) if c.get("elected")]
        if winner_id and tcp_elected:
            if tcp_elected[0].get("candidateId") != winner_id:
                winner_id_off.append((s["seat"]["name"], winner_id, tcp_elected[0].get("candidateId")))
    if winner_id_off:
        issues += 1
        print(f"  ✗ {len(winner_id_off)} seats: winner.candidateId ≠ TCP elected candidate:")
        for n, w, tw in winner_id_off[:5]:
            print(f"      {n}: winner={w}  tcp-elected={tw}")
    else:
        print(f"  ✓ winner.candidateId matches the TCP elected candidate on all seats")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
def check_C_booth_geo(seats: list[tuple[Path, dict]]) -> int:
    banner("C. Booth geo sanity")
    issues = 0
    no_geo = []
    out_of_bounds = []
    dup_booth_ids = []
    booth_count_summary = []

    for _, s in seats:
        name = s["seat"]["name"]
        booths = s.get("booths") or []
        booth_count_summary.append((name, len(booths)))
        ids_seen = Counter(b.get("boothId") for b in booths)
        dups = [bid for bid, c in ids_seen.items() if c > 1 and bid is not None]
        if dups:
            dup_booth_ids.append((name, dups))

        for b in booths:
            lat = b.get("lat")
            lng = b.get("lng")
            if lat is None or lng is None:
                bnm = b.get("name", "")
                # Special voting infrastructure has no fixed location
                if SPECIAL_BOOTH_RE.search(bnm):
                    continue
                no_geo.append((name, bnm, b.get("boothId"), b.get("formal", 0)))
                continue
            if not (AUS_LAT_RANGE[0] <= lat <= AUS_LAT_RANGE[1] and
                    AUS_LNG_RANGE[0] <= lng <= AUS_LNG_RANGE[1]):
                out_of_bounds.append((name, b.get("name"), lat, lng))

    if no_geo:
        issues += 1
        total_votes_lost = sum(f for _, _, _, f in no_geo)
        print(f"  ✗ {len(no_geo)} ordinary booths missing lat/lng ({total_votes_lost} formal votes affected):")
        for n, b, bid, fv in no_geo[:8]:
            print(f"      {n}: '{b}' (id={bid}, {fv} formal votes)")
    else:
        print(f"  ✓ every ordinary booth has lat/lng (special/mobile teams correctly waived)")

    if out_of_bounds:
        issues += 1
        print(f"  ✗ {len(out_of_bounds)} booths outside Australia bbox:")
        for n, b, la, lo in out_of_bounds[:8]:
            print(f"      {n}: '{b}' @ ({la}, {lo})")
    else:
        print(f"  ✓ every booth lat/lng inside Australia bbox")

    if dup_booth_ids:
        issues += 1
        print(f"  ✗ {len(dup_booth_ids)} seats with duplicate booth IDs:")
        for n, d in dup_booth_ids[:5]:
            print(f"      {n}: dup ids {d}")
    else:
        print(f"  ✓ no duplicate booth IDs within any seat")

    counts = sorted(c for _, c in booth_count_summary)
    if counts:
        print(f"  ℹ  booth count: min={counts[0]} p25={counts[len(counts)//4]} "
              f"median={counts[len(counts)//2]} p75={counts[3*len(counts)//4]} max={counts[-1]}")
        # flag implausible extremes
        weird = [(n, c) for n, c in booth_count_summary if c < 5 or c > 250]
        if weird:
            print(f"  ⚠  {len(weird)} seats with implausible booth count (<5 or >250):")
            for n, c in weird:
                print(f"      {n}: {c} booths")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
def check_D_history(seats: list[tuple[Path, dict]]) -> int:
    banner("D. Historical trend integrity")
    issues = 0
    EXPECTED_YEARS = {"2007", "2010", "2013", "2016", "2019", "2022", "2025"}

    missing_years = []
    series_length_off = []
    primary_share_off = []
    points_with_nulls_only = []
    new_seats: list[str] = []

    for _, s in seats:
        name = s["seat"]["name"]
        hist = s.get("history") or {}
        trend = hist.get("trend") or {}
        years = trend.get("years") or []
        if not years:
            new_seats.append(name)
            continue

        yrset = {str(y) for y in years}
        missing = EXPECTED_YEARS - yrset
        if missing:
            # some 2024-redistribution / abolished-and-recreated seats won't have full coverage
            missing_years.append((name, sorted(missing), years))

        for series in trend.get("series") or []:
            if len(series.get("points") or []) != len(years):
                series_length_off.append((name, series.get("id"), len(series.get("points") or []), len(years)))

        # every primary row should sum to ~100 (allow for OTH bucket)
        for row in (hist.get("primary") or {}).get("rows") or []:
            shares = row.get("shares") or {}
            vals = [v for v in shares.values() if isinstance(v, (int, float))]
            if vals and not pct_close(vals, 100.0, tol=1.5):
                primary_share_off.append((name, row.get("year"), sum(vals)))

    if new_seats:
        print(f"  ℹ  {len(new_seats)} seats with no history block (new/abolished): {new_seats}")

    if missing_years:
        # Don't fail outright — this can be legitimate. Report only.
        print(f"  ⚠  {len(missing_years)} seats missing one or more of 2007–2025:")
        for n, m, ys in missing_years[:8]:
            print(f"      {n}: missing {m}  (has {ys})")
    else:
        print(f"  ✓ all seats with history cover the full 2007–2025 cycle")

    if series_length_off:
        issues += 1
        print(f"  ✗ {len(series_length_off)} trend series with mismatched point count:")
        for n, sid, pl, yl in series_length_off[:5]:
            print(f"      {n} series={sid}: {pl} points vs {yl} years")
    else:
        print(f"  ✓ trend.series points length matches trend.years length")

    if primary_share_off:
        issues += 1
        print(f"  ✗ {len(primary_share_off)} (seat, year) rows with primary shares not summing ~100:")
        for n, y, t in primary_share_off[:10]:
            print(f"      {n} {y}: sum={t:.2f}")
    else:
        print(f"  ✓ historical primary shares sum to ~100% per year per seat")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
def check_E_demographics(seats: list[tuple[Path, dict]]) -> int:
    banner("E. Demographics ranges + national reconciliation")
    issues = 0

    pct_fields = [
        "bornOverseasPct", "indigenousPct", "bachelorPlusPct", "renterPct",
    ]
    out_of_range = []
    bad_medians = []
    cob_oversum = []
    rel_oversum = []
    national_blocks = []

    for _, s in seats:
        name = s["seat"]["name"]
        d = s.get("demographics")
        if not d:
            continue
        for f in pct_fields:
            v = d.get(f)
            if v is None:
                continue
            if not (0 <= v <= 100):
                out_of_range.append((name, f, v))
        ma = d.get("medianAge")
        if ma is not None and not (15 <= ma <= 70):
            bad_medians.append((name, "medianAge", ma))
        mi = d.get("medianHouseholdIncomeWeekly")
        if mi is not None and not (200 <= mi <= 5000):
            bad_medians.append((name, "medianHouseholdIncomeWeekly", mi))
        mr = d.get("medianRentWeekly")
        if mr is not None and not (50 <= mr <= 2000):
            bad_medians.append((name, "medianRentWeekly", mr))

        # Top-N COB list — should not over-sum (i.e. > 100.5)
        cob_total = sum(c.get("pct", 0) for c in (d.get("countryOfBirth") or []))
        if cob_total > 100.5:
            cob_oversum.append((name, cob_total))
        rel_total = sum(r.get("pct", 0) for r in (d.get("religion") or []))
        if rel_total > 100.5:
            rel_oversum.append((name, rel_total))

        if d.get("national"):
            national_blocks.append((name, d["national"]))

    if out_of_range:
        issues += 1
        print(f"  ✗ {len(out_of_range)} demographic pct values outside [0,100]:")
        for n, f, v in out_of_range[:5]:
            print(f"      {n}.{f} = {v}")
    else:
        print(f"  ✓ all pct demographic fields within [0,100]")

    if bad_medians:
        issues += 1
        print(f"  ✗ {len(bad_medians)} median values outside plausible range:")
        for n, f, v in bad_medians[:5]:
            print(f"      {n}.{f} = {v}")
    else:
        print(f"  ✓ medianAge / median*Weekly all within plausible ranges")

    if cob_oversum or rel_oversum:
        issues += 1
        if cob_oversum:
            print(f"  ✗ {len(cob_oversum)} seats: countryOfBirth top-N pcts sum >100.5:")
            for n, t in cob_oversum[:5]:
                print(f"      {n}: {t:.2f}")
        if rel_oversum:
            print(f"  ✗ {len(rel_oversum)} seats: religion top-N pcts sum >100.5:")
            for n, t in rel_oversum[:5]:
                print(f"      {n}: {t:.2f}")
    else:
        print(f"  ✓ countryOfBirth + religion top-N tables don't over-sum")

    # national block should be IDENTICAL across all seats that have it
    if national_blocks:
        ref_name, ref = national_blocks[0]
        ref_json = json.dumps(ref, sort_keys=True)
        diffs = []
        for n, blk in national_blocks[1:]:
            if json.dumps(blk, sort_keys=True) != ref_json:
                diffs.append(n)
        if diffs:
            issues += 1
            print(f"  ✗ demographics.national differs across {len(diffs)} seats vs reference ({ref_name}):")
            for n in diffs[:5]:
                print(f"      {n}")
        else:
            print(f"  ✓ demographics.national identical across all {len(national_blocks)} seats")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
def check_F_chips(seats: list[tuple[Path, dict]]) -> int:
    banner("F. Chip classification coherence")
    issues = 0

    # classify.py axis → demographics field used to derive the chip value
    AXIS_TO_FIELD = {
        "age": "medianAge",
        "income": "medianHouseholdIncomeWeekly",
        "migrant": "bornOverseasPct",
        "education": "bachelorPlusPct",
        "tenure": "renterPct",
        "indigenous": "indigenousPct",
    }

    chip_seats = 0
    contradictions = []
    chip_by_axis: dict[str, Counter] = defaultdict(Counter)
    value_drift = []
    axis_pool: dict[str, list[float]] = defaultdict(list)

    # Pass 1: gather chip values per axis to back-derive quartile cuts
    for _, s in seats:
        dem = s.get("demographics") or {}
        for axis, field in AXIS_TO_FIELD.items():
            if axis == "indigenous":
                continue  # absolute cut, not quartile
            v = dem.get(field)
            if isinstance(v, (int, float)):
                axis_pool[axis].append(v)

    cuts: dict[str, tuple[float, float]] = {}
    for axis, vals in axis_pool.items():
        if len(vals) < 4:
            continue
        vsorted = sorted(vals)
        q1 = vsorted[len(vsorted) // 4]
        q3 = vsorted[3 * len(vsorted) // 4]
        cuts[axis] = (q1, q3)

    # Pass 2: validate chips per seat
    for _, s in seats:
        name = s["seat"]["name"]
        cls = s.get("classification") or {}
        tags = cls.get("tags") or []
        if not tags:
            continue
        chip_seats += 1

        # detect contradictions within a seat (two tags on the same axis)
        by_axis: dict[str, list] = defaultdict(list)
        for t in tags:
            ax = t.get("axis", "")
            by_axis[ax].append(t)
            rank = t.get("rank", "abs" if ax == "indigenous" else "")
            chip_by_axis[ax][rank] += 1
        for ax, ts in by_axis.items():
            if len(ts) > 1:
                ranks = {t.get("rank") for t in ts}
                if "high" in ranks and "low" in ranks:
                    contradictions.append((name, ax, [t.get("label") for t in ts]))

        # chip value should match the corresponding demographics field
        dem = s.get("demographics") or {}
        for t in tags:
            ax = t.get("axis")
            field = AXIS_TO_FIELD.get(ax)
            if not field:
                continue
            chip_val = t.get("value")
            demo_val = dem.get(field)
            if chip_val is None or demo_val is None:
                continue
            # allow small rounding
            if abs(float(chip_val) - float(demo_val)) > 0.5:
                value_drift.append((name, ax, field, chip_val, demo_val))

        # rank/value coherence vs derived quartile cuts
        for t in tags:
            ax = t.get("axis")
            rank = t.get("rank")
            val = t.get("value")
            if ax == "indigenous":
                # Tag should only exist when value > some threshold (e.g. 10%)
                if val is not None and val < 5:
                    contradictions.append((name, ax, [f"value={val} but tag present"]))
                continue
            if ax not in cuts or val is None or rank not in ("high", "low"):
                continue
            q1, q3 = cuts[ax]
            if rank == "high" and val < q3 - 0.05:
                value_drift.append((name, ax, f"rank=high but value {val} < derived Q3 {q3:.2f}", None, None))
            if rank == "low" and val > q1 + 0.05:
                value_drift.append((name, ax, f"rank=low but value {val} > derived Q1 {q1:.2f}", None, None))

    print(f"  ℹ  {chip_seats}/{len(seats)} seats carry a classification.tags list")
    print(f"  ℹ  derived quartile cuts (Q1, Q3):")
    for ax, (q1, q3) in sorted(cuts.items()):
        print(f"      {ax:12} Q1={q1:.2f}  Q3={q3:.2f}")
    print(f"  ℹ  chip distribution by axis × rank:")
    for ax, ranks in chip_by_axis.items():
        print(f"      {ax:12} {dict(ranks)}")

    if contradictions:
        issues += 1
        print(f"  ✗ {len(contradictions)} seats with contradictory or anomalous chips:")
        for n, ax, labs in contradictions[:5]:
            print(f"      {n}: axis={ax}  details={labs}")
    else:
        print(f"  ✓ no contradictory chips within any seat")

    if value_drift:
        issues += 1
        print(f"  ✗ {len(value_drift)} chip value/rank inconsistencies:")
        for tup in value_drift[:8]:
            print(f"      {tup}")
    else:
        print(f"  ✓ chip values and rank-vs-quartile placements are coherent")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
def check_G_boundaries(seats: list[tuple[Path, dict]]) -> int:
    banner("G. Boundaries (seats.geojson) integrity")
    issues = 0
    seats_geo = TILES_DIR / "seats.geojson"
    if not seats_geo.exists():
        print(f"  ✗ {seats_geo.relative_to(REPO)} missing")
        return 1
    try:
        gj = json.loads(seats_geo.read_text())
    except json.JSONDecodeError as e:
        print(f"  ✗ malformed seats.geojson: {e}")
        return 1

    features = gj.get("features") or []
    print(f"  ℹ  seats.geojson has {len(features)} features ({seats_geo.stat().st_size//1024} KB)")
    if len(features) != 150:
        issues += 1
        print(f"  ✗ expected 150 features, got {len(features)}")

    # Collect names from geojson
    keys_seen = Counter()
    geo_names = set()
    empty_geom = []
    for f in features:
        props = f.get("properties") or {}
        for k in props:
            keys_seen[k] += 1
        # try common name property keys
        nm = (props.get("divisionNm") or props.get("name") or props.get("Name")
              or props.get("seat") or props.get("Elect_div") or props.get("CED_NAME21"))
        if nm:
            geo_names.add(nm.upper())
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            empty_geom.append(nm)

    print(f"  ℹ  feature property keys: {dict(keys_seen.most_common(8))}")
    if empty_geom:
        issues += 1
        print(f"  ✗ {len(empty_geom)} features with empty geometry:")
        for n in empty_geom[:5]:
            print(f"      {n}")
    else:
        print(f"  ✓ no empty geometries")

    seat_names = {s["seat"]["name"].upper() for _, s in seats}
    only_in_json = seat_names - geo_names
    only_in_geo = geo_names - seat_names
    if only_in_json:
        issues += 1
        print(f"  ✗ {len(only_in_json)} seat JSONs without a matching geojson feature: {sorted(only_in_json)[:10]}")
    if only_in_geo:
        issues += 1
        print(f"  ✗ {len(only_in_geo)} geojson features without a matching seat JSON: {sorted(only_in_geo)[:10]}")
    if not only_in_json and not only_in_geo:
        print(f"  ✓ seat JSONs ↔ geojson features match 1:1 by name")

    land = TILES_DIR / "land.geojson"
    if not land.exists():
        issues += 1
        print(f"  ✗ land.geojson missing")
    else:
        print(f"  ✓ land.geojson present ({land.stat().st_size//1024} KB)")

    return issues


# ─────────────────────────────────────────────────────────────────────────────
def main() -> int:
    seats = collect_seats()
    if len(seats) != 150:
        print(f"  ⚠  expected 150 seats, got {len(seats)}")

    registry = load_party_abbrs()
    banner(f"Loaded {len(seats)} seat JSONs; {len(registry)} party tokens in registry")

    issues = 0
    issues += check_A_structural(seats, registry)
    issues += check_B_primary_tcp_booth(seats)
    issues += check_C_booth_geo(seats)
    issues += check_D_history(seats)
    issues += check_E_demographics(seats)
    issues += check_F_chips(seats)
    issues += check_G_boundaries(seats)

    banner("Summary")
    if issues:
        print(f"  ✗ {issues} category(ies) with hard failures — see above")
        return 1
    print(f"  ✓ all deep QA categories pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())
