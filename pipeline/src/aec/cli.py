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

from .emit.seat_json import build_seat_json, write_seat_json
from .sources.mediafeed import EVENT_IDS, STATES, fetch_event
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


@main.command()
@click.option("--year", type=int, required=True, help="Federal election year (e.g. 2025).")
@click.option(
    "--seat",
    "seat_filter",
    type=str,
    default=None,
    help="Optional seat name (case-insensitive) — restrict build to this division.",
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
    year: int, seat_filter: str | None, refresh: bool, out_dir: Path, cache_dir: Path
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
) -> dict:
    meta = division_meta(candidates, division_id)
    primary = primary_for_division(first_prefs, division_id)
    tcp_rows = tcp_for_division(tcp, division_id)
    booths = booths_for_division(first_prefs, tcp, division_id)
    waterfall = waterfall_for_division(dop, division_id)
    informal = informal_for_division(first_prefs, division_id)
    return build_seat_json(
        meta=meta,
        primary=primary,
        tcp=tcp_rows,
        booths=booths,
        waterfall=waterfall,
        informal=informal,
        year=year,
    )


if __name__ == "__main__":
    main()
