"""CLI entrypoint for the aec-pipeline.

Phase A only ships `build` for one election year, optionally narrowed to
a single seat for fast iteration during development.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import click
import polars as pl

from .emit.pmtiles import (
    clip_to_land,
    enrich_geojson,
    geojsons_to_pmtiles,
    shapefile_to_geojson,
)
from .emit.seat_json import build_seat_json, write_seat_json
from .sources.boundaries import fetch_boundaries
from .sources.coastline import fetch_land
from .sources.mediafeed import EVENT_IDS, STATES, fetch_event, fetch_event_lite
from .transform.historical import (
    history_blocks,
    primary_by_division,
    tcp_pairings_by_division,
)
from .transform.preferences import load_dop, waterfall_for_division
from .transform.results import (
    booths_for_division,
    division_meta,
    informal_for_division,
    load_candidates,
    load_first_prefs,
    load_tcp,
    primary_for_division,
    tcp_for_division,
)


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="DEBUG-level logging.")
def main(verbose: bool) -> None:
    """AEC elections pipeline."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


DEFAULT_HISTORY_YEARS = (2007, 2010, 2013, 2016, 2019, 2022, 2025)


@main.command()
@click.option("--year", type=int, required=True, help="Federal election year (e.g. 2025).")
@click.option(
    "--seat",
    "seat_filter",
    type=str,
    default=None,
    help="Optional seat name (case-insensitive) — restrict build to this division.",
)
@click.option(
    "--history-from",
    type=int,
    default=DEFAULT_HISTORY_YEARS[0],
    show_default=True,
    help="Earliest year of historical data to include in the trend chart.",
)
@click.option(
    "--no-history",
    is_flag=True,
    help="Skip building the history block (useful for fast single-seat dev iteration).",
)
@click.option("--refresh", is_flag=True, help="Force re-download of source CSVs.")
@click.option(
    "--out-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path(__file__).resolve().parents[3] / "site" / "public" / "seats",
    show_default=True,
    help="Where per-seat JSON files are written.",
)
@click.option(
    "--cache-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path(__file__).resolve().parents[3] / "pipeline" / "data" / "raw",
    show_default=True,
    help="Where raw AEC CSVs are cached.",
)
def build(
    year: int,
    seat_filter: str | None,
    history_from: int,
    no_history: bool,
    refresh: bool,
    out_dir: Path,
    cache_dir: Path,
) -> None:
    """Build per-seat JSON for one election year."""
    if year not in EVENT_IDS:
        raise click.BadParameter(f"Unknown year {year}. Known: {sorted(EVENT_IDS)}")

    click.echo(f"▸ Fetching AEC Media Feed for {year} (event {EVENT_IDS[year]})")
    files = fetch_event(year, cache_dir, refresh=refresh)

    click.echo("▸ Loading CSVs")
    candidates = load_candidates(files.candidates)
    dop = load_dop(files.dop_by_division)
    tcp = load_tcp(files.tcp_by_polling_place)
    first_prefs = load_first_prefs(
        [files.first_prefs_by_polling_place(s) for s in STATES]
    )

    history_lookup: dict[str, dict] = {}
    if not no_history:
        history_years = [y for y in DEFAULT_HISTORY_YEARS if y >= history_from and y <= year]
        click.echo(f"▸ Historical trend  years={history_years}")
        tcp_frames: list = []
        primary_frames: list = []
        for hy in history_years:
            click.echo(f"  · fetching {hy}")
            hfiles = fetch_event_lite(hy, cache_dir, refresh=refresh)
            hdop = load_dop(hfiles.dop_by_division)
            tcp_frames.append(tcp_pairings_by_division(hdop, hy))
            primary_frames.append(primary_by_division(hdop, hy))
        history_lookup = history_blocks(tcp_frames, primary_frames)
        click.echo(f"▸ Built history for {len(history_lookup)} unique division names.")

    division_ids = _resolve_division_ids(candidates, seat_filter)
    click.echo(f"▸ Building {len(division_ids)} seat(s) → {out_dir}")

    out_paths: list[Path] = []
    for div_id in division_ids:
        try:
            payload = _build_one(
                candidates=candidates,
                dop=dop,
                tcp=tcp,
                first_prefs=first_prefs,
                division_id=div_id,
                year=year,
                history_lookup=history_lookup,
            )
        except Exception as exc:  # noqa: BLE001 — surface bad-seat info
            import traceback
            click.secho(f"  ✗ division {div_id}: {exc}", fg="red", err=True)
            click.secho(traceback.format_exc(), fg="yellow", err=True)
            continue
        path = write_seat_json(payload, out_dir)
        out_paths.append(path)
        click.echo(f"  ✓ {path.name}  ({payload['seat']['name']}, {payload['seat']['state']})")

    click.echo(f"▸ Wrote {len(out_paths)} seat JSON file(s).")


def _resolve_division_ids(candidates: pl.DataFrame, seat_filter: str | None) -> list[int]:
    if seat_filter is None:
        return sorted({int(x) for x in candidates["DivisionID"].unique().to_list()})
    needle = seat_filter.strip().lower()
    matches = candidates.filter(pl.col("DivisionNm").str.to_lowercase() == needle)
    if matches.is_empty():
        # Try contains-match for forgiving CLI experience.
        matches = candidates.filter(
            pl.col("DivisionNm").str.to_lowercase().str.contains(needle)
        )
    if matches.is_empty():
        raise click.BadParameter(f"No division matched {seat_filter!r}.")
    ids = sorted({int(x) for x in matches["DivisionID"].unique().to_list()})
    return ids


def _build_one(
    *,
    candidates: pl.DataFrame,
    dop: pl.DataFrame,
    tcp: pl.DataFrame,
    first_prefs: pl.DataFrame,
    division_id: int,
    year: int,
    history_lookup: dict[str, dict] | None = None,
) -> dict:
    meta = division_meta(candidates, division_id)
    primary = primary_for_division(first_prefs, division_id)
    tcp_rows = tcp_for_division(tcp, division_id)
    booths = booths_for_division(first_prefs, tcp, division_id)
    waterfall = waterfall_for_division(dop, division_id)
    informal = informal_for_division(first_prefs, division_id)
    history = None
    if history_lookup:
        history = history_lookup.get(meta["name"].lower())
    return build_seat_json(
        meta=meta,
        primary=primary,
        tcp=tcp_rows,
        booths=booths,
        waterfall=waterfall,
        informal=informal,
        history=history,
        year=year,
    )


@main.command("build-tiles")
@click.option("--year", type=int, required=True, help="Boundary year (and matching election).")
@click.option("--refresh", is_flag=True, help="Force re-download of source files.")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=Path(__file__).resolve().parents[3]
    / "site"
    / "public"
    / "tiles"
    / "aec-2025.pmtiles",
    show_default=True,
    help="Output PMTiles path.",
)
@click.option(
    "--cache-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=Path(__file__).resolve().parents[3] / "pipeline" / "data" / "raw",
    show_default=True,
)
def build_tiles(year: int, refresh: bool, out_path: Path, cache_dir: Path) -> None:
    """Fetch boundaries + winners, emit a national PMTiles archive."""
    click.echo(f"▸ Boundaries for {year}")
    boundary = fetch_boundaries(year, cache_dir, refresh=refresh)

    click.echo("▸ Election results (for winner-party fill)")
    files = fetch_event(year, cache_dir, refresh=refresh)
    candidates = load_candidates(files.candidates)
    tcp = load_tcp(files.tcp_by_polling_place)

    geo_dir = cache_dir / "geo"
    raw_geojson = geo_dir / f"aec-{year}-raw.geojson"
    enriched_geojson = geo_dir / f"aec-{year}-enriched.geojson"
    clipped_geojson = geo_dir / f"aec-{year}-clipped.geojson"
    land_geojson = geo_dir / f"aus-land-{year}.geojson"

    click.echo("▸ ogr2ogr  Shapefile → GeoJSON (WGS84)")
    shapefile_to_geojson(boundary.shapefile, raw_geojson)

    click.echo("▸ enrich  joining winner-party properties")
    enrich_geojson(raw_geojson, enriched_geojson, candidates=candidates, tcp=tcp)

    click.echo("▸ coastline  GADM 4.1 admin-0 (Australia, ~1:1M)")
    land_shp = fetch_land(cache_dir, refresh=refresh)

    click.echo("▸ clip  AEC polygons → land only (Sydney Harbour, offshore)")
    clip_to_land(enriched_geojson, clipped_geojson, land_shp)

    click.echo("▸ ogr2ogr  GADM land → GeoJSON (basemap layer)")
    shapefile_to_geojson(land_shp, land_geojson)

    click.echo("▸ tippecanoe  GeoJSON → PMTiles  (layers: seats, land)")
    geojsons_to_pmtiles(
        {"seats": clipped_geojson, "land": land_geojson},
        out_path,
    )

    size_mb = out_path.stat().st_size / 1_048_576
    click.echo(f"▸ Wrote {out_path}  ({size_mb:.2f} MB)")


if __name__ == "__main__":
    main()
