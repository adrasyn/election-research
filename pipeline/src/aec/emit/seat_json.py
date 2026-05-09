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


def build_seat_json(
    *,
    meta: dict[str, Any],
    primary: list[dict[str, Any]],
    tcp: list[dict[str, Any]],
    booths: list[dict[str, Any]],
    waterfall: dict[str, Any],
    informal: dict[str, Any],
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
            "winner": meta["winner"],
            "tcpMargin": margin,
            "result": _result_label(meta, tcp_winner),
        },
        "primary": primary,
        "tcp": tcp,
        "booths": booths,
        "preferences": waterfall_clean,
    }


def write_seat_json(payload: dict[str, Any], out_dir: Path) -> Path:
    """Write to `<out_dir>/<seat-id>.json` and return the path."""
    seat_id = payload["seat"]["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{seat_id}.json"
    path.write_text(json.dumps(payload, indent=2))
    return path


def _result_label(meta: dict[str, Any], tcp_winner: dict[str, Any] | None) -> str:
    """Render 'ALP RETAIN' / 'ALP GAIN' / 'IND HOLD' style label.

    For Phase A we don't have prior-election data joined yet, so this is
    a placeholder that just shows the winning party. Phase D fills in the
    proper RETAIN/GAIN logic against the previous election's holder.
    """
    winner = meta.get("winner")
    if not winner:
        return "RESULT TBC"
    party_ab = (winner.get("partyAb") or "IND").upper()
    return f"{party_ab} {'RETAIN' if winner.get('incumbent') else 'WIN'}"
