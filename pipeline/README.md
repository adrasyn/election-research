# aec-pipeline

Data pipeline for the AEC elections research dashboard. Pulls public AEC
Media Feed CSVs and ABS Census data, normalises with Polars, emits per-seat
JSON + national Parquet + PMTiles for the static site.

## Setup

```sh
cd pipeline
python3 -m venv .venv
. .venv/bin/activate
pip install -e .[dev]
```

## Usage

```sh
# Build one seat (fast, useful during development)
aec-pipeline build --year 2025 --seat BENNELONG

# Build all 150 seats for an election
aec-pipeline build --year 2025

# Force re-download of source CSVs (default: cached)
aec-pipeline build --year 2025 --refresh
```

Output lands in `../site/public/seats/{SEAT}.json` and `../site/public/data/*.parquet`.

## Layout

- `src/aec/sources/` — raw fetchers (no transformation)
- `src/aec/transform/` — Polars transformations (deterministic, pure)
- `src/aec/emit/` — output writers (per-seat JSON, Parquet, PMTiles)
- `src/aec/cli.py` — Click-based CLI
- `tests/` — pytest, including conservation checks

## Data sources

| Source | URL pattern | Used for |
|---|---|---|
| AEC Media Feed (CSV) | `results.aec.gov.au/{event_id}/Website/Downloads/` | 2007–2025 booth + TCP + DOP |
| AEC GIS shapefiles | `aec.gov.au/electorates/gis/` | electorate boundaries |
| ABS 2021 Census Datapacks | `abs.gov.au/census/find-census-data/datapacks` | demographic context |
| AEC website HTML tables | `results.aec.gov.au/{event_id}/Website/...` | 1996–2004 historical |

Event IDs: 2025=31496, 2022=27966, 2019=24310, 2016=20499, 2013=17496,
2010=15508, 2007=13745.
