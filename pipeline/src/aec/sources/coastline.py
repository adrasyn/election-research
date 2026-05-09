"""Coastline / land-polygon fetcher.

We clip the AEC electorate polygons against an actual land mask before
tiling, so divisions don't sweep over open water (Sydney Harbour and
the offshore extensions are the most visible artefacts). Natural Earth
10 m physical/land is the source: free, no registration, ships Sydney
Harbour and the major island groups (Tassie, Christmas, Cocos, Norfolk).

If finer resolution is wanted later (e.g. small inlets), swap to
OpenStreetMap-derived land polygons from osmdata.openstreetmap.de.
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

COASTLINE_URL = "https://naciscdn.org/naturalearth/10m/physical/ne_10m_land.zip"


def fetch_land(cache_root: Path, *, refresh: bool = False) -> Path:
    """Download + extract the Natural Earth 10 m land polygon shapefile.

    Returns the path to the .shp file. Idempotent — cached by default.
    """
    cache_dir = cache_root / "coastline"
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "ne_10m_land.zip"
    extract_dir = cache_dir / "ne_10m_land"

    if not zip_path.exists() or refresh:
        log.info("downloading %s", COASTLINE_URL)
        with httpx.Client(
            timeout=180.0,
            follow_redirects=True,
            headers={"User-Agent": "aec-elections-research/0.1 (personal-research)"},
        ) as client:
            resp = client.get(COASTLINE_URL)
            resp.raise_for_status()
            zip_path.write_bytes(resp.content)

    if not extract_dir.exists() or refresh:
        log.info("extracting %s", zip_path.name)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

    shp = extract_dir / "ne_10m_land.shp"
    if not shp.exists():
        raise FileNotFoundError(f"NE land shapefile not found at {shp}")
    return shp
