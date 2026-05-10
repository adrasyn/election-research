"""Per-seat JSON writer.

Builds the single JSON file the frontend fetches when the user clicks
into a seat. Schema is intentionally tight — the chart generators
(lib/waterfall.js etc.) consume slices of this directly.

The seat-level TCP block is derived from the DOP final round (canonical
AEC count including postals/absents), not from per-booth ordinary
totals. For tight contests like Bean 2025 the two disagree, and DOP
matches the declared winner.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def tcp_from_waterfall(waterfall: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive the seat-level TCP block from the DOP final round."""
    rounds = waterfall.get("rounds") or []
    if not rounds:
        return []
    final = rounds[-1]
    cand_meta = waterfall.get("_candidate_meta", {}) or {}
    finalists = [(k, int(v)) for k, v in final.items() if k != "exhausted"]
    finalists.sort(key=lambda kv: kv[1], reverse=True)
    tcp_total = sum(v for _, v in finalists)
    out: list[dict[str, Any]] = []
    for idx, (cid, votes) in enumerate(finalists):
        meta = cand_meta.get(cid, {})
        out.append(
            {
                "candidateId": cid,
                "surname": meta.get("_surname"),
                "givenName": meta.get("_givenName"),
                "party": meta.get("party", "oth"),
                "partyAb": meta.get("displayShort", "IND"),
                "votes": votes,
                "pct": round(votes / tcp_total * 100, 2) if tcp_total else 0.0,
                "elected": idx == 0,
            }
        )
    return out


def _previous_winner_party_ab(history: dict[str, Any] | None, current_year: int) -> str | None:
    """Return the canonical partyAb that won this seat at the prior
    election, derived from the history trend block. None if no prior
    data exists (newly-created seat) or the year just before is missing.
    """
    if not history:
        return None
    trend = history.get("trend") or {}
    years = trend.get("years") or []
    series = trend.get("series") or []
    if not years or not series:
        return None
    target = str(current_year)
    if target not in years:
        return None
    cur_idx = years.index(target)
    if cur_idx == 0:
        return None
    prev_idx = cur_idx - 1
    best_party: str | None = None
    best_pct: float = -1.0
    for s in series:
        pts = s.get("points") or []
        if prev_idx >= len(pts):
            continue
        pt = pts[prev_idx]
        if pt is None:
            continue
        if pt > best_pct:
            best_pct = float(pt)
            best_party = s.get("label") or s.get("id")
    return (best_party or "").upper() or None


def build_seat_json(
    *,
    meta: dict[str, Any],
    primary: list[dict[str, Any]],
    tcp: list[dict[str, Any]],
    booths: list[dict[str, Any]],
    waterfall: dict[str, Any],
    informal: dict[str, Any],
    turnout: dict[str, Any],
    history: dict[str, Any] | None,
    demographics: dict[str, Any] | None,
    bio: dict[str, Any] | None,
    year: int,
) -> dict[str, Any]:
    """Assemble the full per-seat payload."""
    # Override the booth-derived TCP (ordinary votes only) with the DOP
    # final round, which includes postals/absents. The booth-level rows
    # are still useful for the booth table; just not for the panel header.
    tcp_canonical = tcp_from_waterfall(waterfall)
    if tcp_canonical:
        tcp = tcp_canonical

    # Strip private keys from waterfall before emit.
    waterfall_clean = {k: v for k, v in waterfall.items() if not k.startswith("_")}

    total_formal = waterfall["totalFormal"]
    if len(tcp) >= 2:
        tcp_winner = tcp[0]
        tcp_runner = tcp[1]
        margin = round(tcp_winner["pct"] - tcp_runner["pct"], 2)
    else:
        tcp_winner = tcp[0] if tcp else None
        tcp_runner = None
        margin = None

    prev_party = _previous_winner_party_ab(history, year)
    return {
        "schema": 1,
        "year": year,
        "seat": {
            "id": meta["name"].upper().replace(" ", "_"),
            "divisionId": meta["divisionId"],
            "name": meta["name"],
            "state": meta["state"],
            "totalFormal": total_formal,
            "informalVotes": informal["informalVotes"],
            "informalRate": informal["informalRate"],
            "enrolled": turnout.get("enrolled"),
            "votesCounted": turnout.get("votesCounted"),
            "turnoutPct": turnout.get("turnoutPct"),
            "turnoutSwing": turnout.get("turnoutSwing"),
            "winner": meta["winner"],
            "previousWinnerParty": prev_party,
            "tcpMargin": margin,
            "result": _result_label_with_history(meta, prev_party),
        },
        "primary": primary,
        "tcp": tcp,
        "booths": booths,
        "preferences": waterfall_clean,
        "history": history,
        "demographics": demographics,
        "bio": bio,
    }


def write_seat_json(payload: dict[str, Any], out_dir: Path) -> Path:
    """Write to `<out_dir>/<seat-id>.json` and return the path."""
    seat_id = payload["seat"]["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{seat_id}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _result_label_with_history(
    meta: dict[str, Any], prev_party_ab: str | None
) -> str:
    """Render 'NAT RETAIN' / 'ALP GAIN' / 'IND WIN' style label.

    A seat is RETAIN if the same party held it at the prior election —
    even if the individual MP changed (e.g. Parkes 2025: Mark Coulton
    retired, Jamie Chaffey replaced him, NAT retained the seat). This
    comes from the history trend block; for newly-created seats with
    no prior-election data we fall back to a plain 'WIN'.

    Coalition partner alignment is treated naively: LIB / LNP / NAT /
    CLP are distinct parties for this calculation. State-by-state
    Coalition mergers / splits create some edge cases — acceptable for
    v1; can refine later.
    """
    winner = meta.get("winner") or {}
    party_ab = (winner.get("partyAb") or "IND").upper()
    if not party_ab:
        return "RESULT TBC"
    if prev_party_ab is None:
        return f"{party_ab} WIN"
    return f"{party_ab} {'RETAIN' if prev_party_ab == party_ab else 'GAIN'}"
