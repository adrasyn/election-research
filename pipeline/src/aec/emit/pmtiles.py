"""GeoJSON enrichment + PMTiles generation.

Takes the AEC boundary shapefile and the per-seat results we've already
computed, joins the winner colour into each polygon's properties, then
shells out to tippecanoe to bake a single PMTiles archive that the
MapLibre national map can consume directly.
"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

import polars as pl

from ..parties import css_key

log = logging.getLogger(__name__)


def _require_tool(name: str) -> None:
    if shutil.which(name) is None:
        raise RuntimeError(
            f"`{name}` not on PATH. Install with `brew install {name}` (mac) "
            f"or your platform's equivalent."
        )


def shapefile_to_geojson(shapefile: Path, geojson_out: Path) -> Path:
    """Reproject the shapefile to WGS84 and emit GeoJSON via ogr2ogr."""
    _require_tool("ogr2ogr")
    geojson_out.parent.mkdir(parents=True, exist_ok=True)
    geojson_out.unlink(missing_ok=True)
    log.info("ogr2ogr → %s", geojson_out)
    subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "GeoJSON",
            "-t_srs",
            "EPSG:4326",
            str(geojson_out),
            str(shapefile),
        ],
        check=True,
    )
    return geojson_out


def clip_to_land(geojson_in: Path, geojson_out: Path, land_shapefile: Path) -> Path:
    """Clip AEC polygons to a land-only mask.

    AEC electorate boundaries follow cadastral lines that extend over
    water — Sydney Harbour gets covered, and many coastal divisions
    sweep offshore. We intersect against Natural Earth's land polygon so
    only the actual landmass remains. Major islands that legitimately
    sit inside electorates (Tasmania, Christmas, Cocos, Norfolk) stay
    because NE-land has them as separate features.
    """
    _require_tool("ogr2ogr")
    geojson_out.parent.mkdir(parents=True, exist_ok=True)
    geojson_out.unlink(missing_ok=True)
    log.info("ogr2ogr clip → %s", geojson_out.name)
    subprocess.run(
        [
            "ogr2ogr",
            "-f",
            "GeoJSON",
            "-clipsrc",
            str(land_shapefile),
            "-makevalid",  # clipping can create slivers / self-intersections
            str(geojson_out),
            str(geojson_in),
        ],
        check=True,
    )
    return geojson_out


# AEC shapefiles have used a few different field names for the division
# name across redistributions. We look for the first that matches.
_DIVISION_NAME_FIELDS = ("Elect_div", "ELECT_DIV", "Sortname", "SORTNAME", "Name", "NAME")


def enrich_geojson(
    geojson_in: Path,
    geojson_out: Path,
    *,
    candidates: pl.DataFrame,
    tcp: pl.DataFrame,
) -> Path:
    """Annotate each feature with winner_party / margin / state for styling."""
    log.info("enriching %s → %s", geojson_in.name, geojson_out.name)
    raw = json.loads(geojson_in.read_text())
    winner_lookup = _build_winner_lookup(candidates, tcp)
    feats_unmatched: list[str] = []
    matched = 0

    for feat in raw["features"]:
        props = feat.get("properties", {}) or {}
        name = _find_division_name(props)
        winner = winner_lookup.get(name.upper()) if name else None
        if winner is None:
            feats_unmatched.append(name or "<no name field>")
            new_props = {"divisionNm": name or "?"}
        else:
            new_props = {
                "divisionId": winner["divisionId"],
                "divisionNm": name,
                "state": winner["state"],
                "winnerParty": winner["winnerParty"],
                "winnerPartyAb": winner["winnerPartyAb"],
                "winnerSurname": winner["winnerSurname"],
                "tcpMargin": winner["tcpMargin"],
            }
            matched += 1
        feat["properties"] = new_props

    geojson_out.parent.mkdir(parents=True, exist_ok=True)
    geojson_out.write_text(json.dumps(raw))
    log.info("matched %d/%d features", matched, len(raw["features"]))
    if feats_unmatched:
        sample = ", ".join(feats_unmatched[:5])
        log.warning("%d features unmatched. Sample: %s", len(feats_unmatched), sample)
    return geojson_out


def geojsons_to_pmtiles(layers: dict[str, Path], pmtiles_out: Path) -> Path:
    """Bake one or more GeoJSONs into a single PMTiles archive.

    Each (layer_name, geojson_path) entry becomes its own layer in the
    output. The "seats" layer gets divisionId promoted to feature-id so
    MapLibre's setFeatureState (hover / active outlines) works.
    """
    _require_tool("tippecanoe")
    pmtiles_out.parent.mkdir(parents=True, exist_ok=True)
    pmtiles_out.unlink(missing_ok=True)
    log.info("tippecanoe → %s", pmtiles_out)
    args = [
        "tippecanoe",
        "-o",
        str(pmtiles_out),
        "--minimum-zoom=2",
        "--maximum-zoom=10",
        # Light simplification — anything heavier on the AEC polygons leaves
        # visible jaggies on the coastline at zoom 3-5.
        "--simplification=1",
        # Adjacent electorates share long borders; simplifying them
        # independently produces sliver gaps. This flag keeps shared
        # edges aligned across polygons.
        "--detect-shared-borders",
        # Maximum vertices retained at the top zoom level — the AEC
        # polygons are detailed and we want to keep them sharp.
        "--full-detail=14",
        # Safety: 150 polygons + 1 land are well under any realistic
        # density limit, but disable the ceilings so tippecanoe never
        # silently drops or coalesces them.
        "--no-tile-size-limit",
        "--no-feature-limit",
        "--no-tile-compression",  # MapLibre + PMTiles handles its own
        "--use-attribute-for-id=divisionId",
        "--force",
    ]
    for layer_name, geojson in layers.items():
        args.extend(["-L", f"{layer_name}:{geojson}"])
    subprocess.run(args, check=True)
    return pmtiles_out


def _build_winner_lookup(candidates: pl.DataFrame, tcp: pl.DataFrame) -> dict[str, dict[str, Any]]:
    """Per-division: who won, by how much, and what colour to render.

    AEC's TCP-by-booth CSV is *ordinary votes only*; it can disagree with
    the formal winner once postals/absents are added (see Bean 2025,
    decided by 86 ordinary votes but flipped on declaration votes). We
    sort with Elected='Y' first, then by ordinary votes — matches AEC's
    declared winner while still giving a defensible runner-up.
    """
    grouped = (
        tcp.group_by(
            "DivisionID", "DivisionNm", "StateAb", "CandidateID", "Surname", "PartyAb", "Elected"
        )
        .agg(pl.col("OrdinaryVotes").sum().alias("votes"))
        .with_columns((pl.col("Elected") == "Y").cast(pl.Int8).alias("_elected_rank"))
    )
    out: dict[str, dict[str, Any]] = {}
    for div_id in grouped["DivisionID"].unique().to_list():
        seat = grouped.filter(pl.col("DivisionID") == div_id).sort(
            ["_elected_rank", "votes"], descending=[True, True]
        )
        if len(seat) < 2:
            continue
        total = int(seat["votes"].sum())
        win = seat.row(0, named=True)
        runner_votes = int(seat.row(1, named=True)["votes"])
        margin = round((int(win["votes"]) - runner_votes) / total * 100, 2) if total else 0.0
        out[str(win["DivisionNm"]).upper()] = {
            "divisionId": int(win["DivisionID"]),
            "divisionNm": win["DivisionNm"],
            "state": win["StateAb"],
            "winnerParty": css_key(win["PartyAb"]),
            "winnerPartyAb": win["PartyAb"] or "IND",
            "winnerSurname": win["Surname"],
            "tcpMargin": margin,
        }
    return out


def _find_division_name(props: dict[str, Any]) -> str | None:
    for f in _DIVISION_NAME_FIELDS:
        v = props.get(f)
        if v:
            return str(v)
    return None
