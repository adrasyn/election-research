"""Wikipedia bio fetcher — one short intro per electorate.

Uses the public REST `page/summary/{title}` endpoint which returns a
clean lead-paragraph extract plus the canonical page URL. No auth or
key required; rate limit is generous enough for a one-shot 150-seat
build (we cache hits to disk so re-runs don't hit the network).
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

_BASE = "https://en.wikipedia.org/api/rest_v1/page/summary/"
# Per Wikimedia API best practices, the UA should identify the
# software and a contact, even for personal/research use. The bot is
# anonymous so we throttle to be polite.
_UA = (
    "aec-elections-research/0.1 "
    "(https://github.com/wilson-james-AEC; non-commercial research)"
)

# A handful of divisions whose Wikipedia article uses a non-default
# disambiguation suffix or alternate spelling. Extend as the pipeline
# surfaces more cases.
_TITLE_OVERRIDES: dict[str, str] = {
    # AEC name → Wikipedia article title
    # (example: "Some Division" → "Division of Some Division (federal)")
}


def _title_for(division_name: str) -> str:
    if division_name in _TITLE_OVERRIDES:
        return _TITLE_OVERRIDES[division_name]
    return f"Division of {division_name}"


def fetch_bio(
    division_name: str,
    cache_root: Path,
    *,
    refresh: bool = False,
    sleep_between: float = 1.0,
) -> dict[str, Any] | None:
    """Return {text, source, retrievedAt} for one division, or None.

    Cache semantics:
      • on a successful fetch → cache the payload
      • on a definitive 404   → cache `null` (no need to retry)
      • on a transient error  → don't cache (so subsequent runs retry)
    Pass `refresh=True` to bust the cache regardless.
    """
    cache_dir = cache_root / "wikipedia"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / (division_name.replace(" ", "_") + ".json")

    if cache_path.exists() and not refresh:
        raw = cache_path.read_text().strip()
        if raw == "null":
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass  # fall through to refetch

    title = _title_for(division_name)
    url = _BASE + urllib.parse.quote(title.replace(" ", "_"), safe="")

    payload: dict[str, Any] | None = None
    fetch_status = "transient_error"
    try:
        with httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"User-Agent": _UA, "Accept": "application/json"},
        ) as client:
            resp = client.get(url)
            if resp.status_code == 404:
                log.info("wiki 404 for %s", title)
                fetch_status = "missing"
            elif resp.status_code == 429:
                log.warning("wiki 429 for %s — backing off", title)
                fetch_status = "transient_error"
            else:
                resp.raise_for_status()
                data = resp.json()
                extract = (data.get("extract") or "").strip()
                if extract:
                    payload = {
                        "text": extract,
                        "source": data.get("content_urls", {})
                        .get("desktop", {})
                        .get("page")
                        or url,
                        "retrievedAt": data.get("timestamp"),
                    }
                    fetch_status = "ok"
                else:
                    fetch_status = "missing"
    except httpx.HTTPError as exc:
        log.warning("wiki fetch failed for %s: %s", title, exc)
        fetch_status = "transient_error"

    # Only cache successes and definitive misses. Transient errors are
    # re-tried on the next run.
    if fetch_status in {"ok", "missing"}:
        cache_path.write_text(json.dumps(payload) if payload else "null")
    if sleep_between > 0:
        time.sleep(sleep_between)
    return payload


def fetch_all_bios(
    division_names: list[str],
    cache_root: Path,
    *,
    refresh: bool = False,
) -> dict[str, dict[str, Any]]:
    """Bulk fetch with on-disk caching. Keyed by lowercased division name."""
    out: dict[str, dict[str, Any]] = {}
    for name in division_names:
        bio = fetch_bio(name, cache_root, refresh=refresh)
        if bio:
            out[name.lower()] = bio
    return out
