# AEC Elections — research dashboard

Live: **https://electionresearch.wlsn.me**

Personal post-election research dashboard for Australian federal elections.
Three pillars per electorate: booth-level results, historical trend (1996–2025),
demographic context (ABS 2021 Census).

> **Not affiliated with the AEC.** Data sourced from the AEC Media Feed
> archive and the ABS; this site is independent research.

## Status

In active build. See [PLAN.md](./PLAN.md) for the implementation plan and
phase tracker.

## Repo layout

- `pipeline/` — Python data pipeline (Polars + DuckDB). Pulls AEC + ABS,
  emits per-seat JSON + national Parquet + PMTiles for the map.
- `site/` — Astro static frontend. Single page, MapLibre national map,
  side panel per electorate.
- `design/` — original concept files and chart generators (`lib/*.js`).
  Concepts are reference; their generators are reused by the site.
- `parties.yaml` — party registry (calibrated colours, aliases).

## Local development

```sh
# Pipeline
cd pipeline
uv sync
uv run aec-pipeline build --year 2025 --seat BENNELONG

# Site
cd site
npm install
npm run dev
```

## Stack

Python 3.12 · Polars · DuckDB · Astro · MapLibre GL · tippecanoe ·
Cloudflare Pages · R2.
