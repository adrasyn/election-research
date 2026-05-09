"""AEC electoral boundary shapefile fetcher.

Downloads and unzips the national federal electoral-boundary shapefile.
The 2024 redistribution (used for the 2025 election) is the default; older
boundary sets exist for joining historical elections in Phase D.
"""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

# Map "election year" → URL of the boundary set used at that election.
# Earlier years (2019, 2022) used different boundary sets — Phase D will
# add those URLs.
BOUNDARY_URLS: dict[int, str] = {
    2025: "https://www.aec.gov.au/Electorates/files/2025/AUS-March-2025-esri.zip",
}


@dataclass(frozen=True)
class BoundaryFiles:
    """Resolved local paths for one boundary set."""

    year: int
    cache_root: Path

    @property
    def zip_path(self) -> Path:
        return self.cache_root / f"boundaries-{self.year}.zip"

    @property
    def extract_dir(self) -> Path:
        return self.cache_root / f"boundaries-{self.year}"

    @property
    def shapefile(self) -> Path:
        """Resolve the .shp inside the extracted directory.

        AEC zips contain a few siblings (the same boundary in multiple
        formats); we pick the polygon shapefile by extension.
        """
        candidates = sorted(self.extract_dir.rglob("*.shp"))
        if not candidates:
            raise FileNotFoundError(f"No .shp under {self.extract_dir}")
        # Prefer the file whose name does not contain 'point' or 'line'.
        for c in candidates:
            n = c.stem.lower()
            if "point" not in n and "line" not in n:
                return c
        return candidates[0]


def fetch_boundaries(year: int, cache_root: Path, *, refresh: bool = False) -> BoundaryFiles:
    if year not in BOUNDARY_URLS:
        raise ValueError(
            f"No boundary URL configured for {year}. Known: {sorted(BOUNDARY_URLS)}"
        )
    files = BoundaryFiles(year=year, cache_root=cache_root)
    files.cache_root.mkdir(parents=True, exist_ok=True)

    if not files.zip_path.exists() or refresh:
        url = BOUNDARY_URLS[year]
        log.info("downloading %s", url)
        with httpx.Client(
            timeout=300.0,
            follow_redirects=True,
            headers={"User-Agent": "aec-elections-research/0.1 (personal-research)"},
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            files.zip_path.write_bytes(resp.content)

    if not files.extract_dir.exists() or refresh:
        log.info("extracting %s", files.zip_path.name)
        with zipfile.ZipFile(files.zip_path) as zf:
            zf.extractall(files.extract_dir)

    return files
