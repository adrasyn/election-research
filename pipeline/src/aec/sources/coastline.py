"""Coastline / land-polygon fetcher.

We clip the AEC electorate polygons against an actual land mask before
tiling, so divisions don't sweep over open water (Sydney Harbour and
the offshore extensions are the most visible artefacts).

Source: GADM 4.1 admin-0 polygon for Australia. Roughly 1:1M scale,
which is ~100 m/pixel — sharp enough to cut Sydney Harbour out cleanly.
Single ~10 MB zip, free, no registration. Includes Tasmania, Christmas,
Cocos, Norfolk so legitimate island parts of electorates survive.
Earlier passes used Natural Earth 10 m which was too coarse for the
harbour (smudged shut at 1.1 km/px).
"""
from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

GADM_URL = "https://geodata.ucdavis.edu/gadm/gadm4.1/shp/gadm41_AUS_shp.zip"


def fetch_land(cache_root: Path, *, refresh: bool = False) -> Path:
    """Download + extract GADM Australia admin-0 polygon.

    Returns the path to the .shp file. Idempotent — cached by default.
    """
    cache_dir = cache_root / "coastline"
    cache_dir.mkdir(parents=True, exist_ok=True)
    zip_path = cache_dir / "gadm41_AUS.zip"
    extract_dir = cache_dir / "gadm41_AUS"

    if not zip_path.exists() or refresh:
        log.info("downloading %s", GADM_URL)
        with httpx.Client(
            timeout=300.0,
            follow_redirects=True,
            headers={"User-Agent": "aec-elections-research/0.1 (personal-research)"},
        ) as client:
            resp = client.get(GADM_URL)
            resp.raise_for_status()
            zip_path.write_bytes(resp.content)

    if not extract_dir.exists() or refresh:
        log.info("extracting %s", zip_path.name)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_dir)

    # Admin level 0 = country boundary as a single multi-polygon.
    shp = extract_dir / "gadm41_AUS_0.shp"
    if not shp.exists():
        raise FileNotFoundError(f"GADM admin-0 shapefile not found at {shp}")
    return shp
