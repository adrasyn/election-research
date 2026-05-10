"""ABS 2021 Census General Community Profile fetcher.

Pulls the CED-level GCP zip (~8 MB) and surfaces paths to the per-table
CSV files. CED-level (Commonwealth Electoral Division) is granular
enough for the demographic pillar without forcing an SA1→CED join.

Caveat: 2021 CED boundaries are the ones used at the 2022 election;
the 2024 redistribution shifted some seats. For most divisions the
demographic mismatch is small. Boundary-aware joining via SA1 is a
follow-on Phase D refinement.
"""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

GCP_CED_URL = (
    "https://www.abs.gov.au/census/find-census-data/datapacks/download/"
    "2021_GCP_CED_for_AUS_short-header.zip"
)

# Path inside the zip where the CED-level CSVs live.
_GCP_CED_DIR = "2021 Census GCP Commonwealth Electroral Division for AUS"


@dataclass(frozen=True)
class CensusFiles:
    """Resolved local paths inside the unzipped GCP CED tree."""

    cache_root: Path

    @property
    def gcp_dir(self) -> Path:
        return self.cache_root / "abs" / "gcp_ced" / _GCP_CED_DIR

    @property
    def metadata_dir(self) -> Path:
        return self.cache_root / "abs" / "gcp_ced" / "Metadata"

    def table(self, code: str) -> Path:
        """Path to a per-table CSV (e.g. table('G01') → 2021Census_G01_AUST_CED.csv)."""
        return self.gcp_dir / f"2021Census_{code}_AUST_CED.csv"

    @property
    def geo_descriptor(self) -> Path:
        return self.metadata_dir / "2021Census_geog_desc_1st_2nd_3rd_release.xlsx"


def fetch_census(cache_root: Path, *, refresh: bool = False) -> CensusFiles:
    """Download + extract the GCP CED datapack. Idempotent."""
    files = CensusFiles(cache_root=cache_root)
    extract_root = files.cache_root / "abs" / "gcp_ced"
    extract_root.mkdir(parents=True, exist_ok=True)
    zip_path = files.cache_root / "abs" / "gcp_ced.zip"

    if not zip_path.exists() or refresh:
        log.info("downloading %s", GCP_CED_URL)
        with httpx.Client(
            timeout=180.0,
            follow_redirects=True,
            headers={"User-Agent": "aec-elections-research/0.1 (personal-research)"},
        ) as client:
            resp = client.get(GCP_CED_URL)
            resp.raise_for_status()
            zip_path.write_bytes(resp.content)

    if not files.gcp_dir.exists() or refresh:
        log.info("extracting %s", zip_path.name)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)
    return files
