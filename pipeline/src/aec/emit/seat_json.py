"""Per-seat JSON writer.

Builds the single JSON file the frontend fetches when the user clicks
into a seat. Schema is intentionally tight — the chart generators
(lib/waterfall.js etc.) consume slices of this directly.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


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
