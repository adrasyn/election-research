# AEC Elections Dashboard — Implementation Plan

> Status: **shipped 2026-05-10. Live at https://electionresearch.wlsn.me/**
> Phases A–F all complete. Post-deploy queue at the bottom of this doc.

## Project state snapshot

- **Live**: https://electionresearch.wlsn.me/ (Cloudflare Pages, custom domain on Hover)
- **GitHub**: https://github.com/adrasyn/election-research (public; auto-deploys on push to main)
- **Coverage**: 150/150 seats with bio + booth + history (2004–2025) + preferences + demographics + classification chips
- **Stack confirmed shipped**: Python+Polars pipeline → 2× simplified GeoJSON + per-seat JSON → Astro 5 + MapLibre (geojson sources, no PMTiles) → Cloudflare Pages
- **QA**: pipeline/scripts/qa_check.py passes 6/6 categories; matches AEC declared 2025 outcome (ALP 94 / LIB 18 / LNP 16 / NAT 9 / IND 10 / GRN 1 / CA 1 / KAP 1)

## Session log

### 2026-05-11 — Trend chart extended back to 2004

Wired 2004 (event 12246) into the historical pipeline. AEC publishes the full structured CSV set for 2004 with the same schema as 2007+ — DOP / TPP / per-candidate FP — so the only adapter needed was a URL-path override (2004 lives under `/results/Downloads/`, 2007+ under `/Website/Downloads/`). Trend chart now spans 21 years (2004 → 2025) across all 150 seats. QA + deep QA both pass.

Pre-2007 scrape ambitions for 1996/1998/2001 are deferred: AEC doesn't publish structured downloads for those years (1996/1998 aren't on results.aec.gov.au at all; 2001 is HTML virtual-tally-room only). Adding them would require a Wikipedia or Adam Carr scraper plus redistribution-aware division name mapping — different shape of work from this drop-in.

### 2026-05-11 — National analysis tab + pipeline QA fix

Two shipped commits today.

**`98c94e9` — Pipeline FP/TCP/informal completeness fix**
A deep-QA pass (`pipeline/scripts/qa_deep.py`, 7 categories) surfaced three real data issues. The big one: per-candidate primary votes were short by ~21k median per seat (~20%) because the pipeline only ingested booth-level FP. Now reads:
- `HouseFirstPrefsByCandidateByVoteTypeDownload-{event}.csv` — canonical per-candidate FP across Ordinary+Absent+Provisional+PrePoll+Postal
- `HouseInformalByDivisionDownload-{event}.csv` — published informal % (matches AEC exactly)
- `HouseTcpByCandidateByVoteTypeDownload-{event}.csv` — for accurate tile-level TPP margins

Also patched two real fixed-location booths AEC publishes as `0,0` (Henty in Farrer 545 votes; Carlingford North in Parramatta 1602 votes) via a `MANUAL_COORDS` override so they appear on the booth inset. Verified against AEC's published Tally Room for Barton + Kennedy — all numbers now match exactly.

**`e22f0f7` — Nationwide insights view (queue item #1)**
National analysis tab, four sub-tabs:
- **Demographics** — 6 beeswarm strips with party-bloc lanes (ALP/Coalition/IND/Other), dots coloured by winning party. Each axis: income, age, born overseas, bachelor+, renting, First Nations.
- **Results** — National primary share + change-since-2022 bar charts on top; 2CP margin + swing-to-winner + winner first-pref strips below.
- **Marginals** — Three-column league (Labor / Coalition / Crossbench), full lists sorted most-marginal-first, click-through to seat panel.
- **Cross-tab** — Free 2D scatter with dropdown axis pickers across demographic / regional / political variables. Dots reuse DOM across axis changes so cx/cy CSS transitions tween smoothly.

Swing-to-winner cascade (133 seats / 14 seats / 3 seats):
1. AEC's published TCP swing when clean
2. TPP swing oriented to winner's bloc when AEC's swing is artefact (signature: `abs(swing) ≈ winner_TCP_pct`)
3. Antony Green's notional 2-Candidate baseline for Mayo, Nicholls, Calare (non-traditional 2CP)

New artefacts: `pipeline/scripts/build_national.py` (emits `site/public/national.json` ~94 KB), and three chart generators in `site/public/lib/`: `nationalbeeswarm.js`, `nationalscatter.js`, `nationalbars.js`.

### 2026-05-10 — chips + theming polish
- Shipped queue item #2 (classification chips). Standalone classifier at `pipeline/scripts/classify.py`; not part of the build pipeline (read-existing-JSON, compute quartiles, write-back).
- Editorial rule that landed: only emit chips for Q1/Q4 of each demographic axis — middle quartiles get no chip so every visible chip means the seat is atypical.
- Terminology decision: **First Nations** everywhere user-facing (chip labels, tooltips, demographic-grid row label). Internal data fields keep ABS schema naming (`indigenousPct`).
- Terminology decision: **electorates** (not "seats") in tooltips.
- Tooltips are styled `data-tip` + `::after` pseudo, not native `title` — appear instantly, charcoal palette, max-width 280px so they wrap.
- MapLibre zoom controls (+/−) recoloured to match panel chrome (was stock white, jarring on charcoal).

### Earlier
- 2026-05-10 morning: booth-inset map shipped (queue item #3), commit `d9cfbd9`.
- 2026-05-10 evening: Phase F code-review fixes (XSS sink + a11y), commit `ef8b1d9`.

## Post-deploy queue

Prioritised order is up to the user; each is independent.

1. ~~**Nationwide insights view**~~ — **Shipped 2026-05-11** (commit `e22f0f7`). Four sub-tabs: Demographics (6 party-laned beeswarms), Results (national bars + outcome strips), Marginals (3-column league), Cross-tab (free 2D scatter with dropdown axis pickers). See session log.

2. ~~**Electorate classification + tag chips on bio**~~ — **Shipped 2026-05-10.** Top/bottom quartile chips for income, age, migrant share, education, tenure + absolute-cut First Nations tier. See session log above.

3. ~~**Booth-inset map**~~ — **Shipped 2026-05-10** (commit `d9cfbd9`). Per-seat SVG inset above the booth table: real electorate outline + real booth lat/lng dots from AEC's polling-place feed; click-to-highlight wired up.

4. **Booth-level demographic estimation (Voronoi × SA1)** — Voronoi tessellation of polling-place lat/lng clipped to electorate boundary, intersected with ABS SA1 polygons, area-weighted to derive synthetic per-booth demographics (income, age, born-overseas, etc.). Joins to existing booth results to support "this booth votes ALP and has median income $X" analyses. **Heaviest pipeline work** of remaining items.

5. **1996–2004 historical scrape** — **Partially shipped 2026-05-11.** 2004 (event 12246) wired via the existing pipeline — AEC publishes the full CSV set with the same schema as 2007+, only the URL path differs (`/results/` vs `/Website/`). 1996/1998/2001 deferred: 1996/1998 aren't on results.aec.gov.au; 2001 is HTML-only. Adding them needs an external-source scraper (Wikipedia or Adam Carr) plus redistribution-aware division name mapping.

## Polish items deferred from Phase F code review

- maplibre-gl bundle (~700 KB) is loaded eagerly. Could lazy-import after first paint of the panel — defer.
- `design/lib/` duplicates `site/public/lib/`. Kept for the design reference HTMLs (`design/concept-01.html`, etc.). Sync risk noted; consolidate via a symlink or build step when convenient.
- Map seats are not keyboard-navigable (MapLibre canvas constraint — would need a parallel ARIA listbox or skip-link). Not on the critical path for a desktop research tool.
- Pipeline `except Exception` per-seat in `cli.py` logs but doesn't fail the build. `qa_check.py` catches missing seats downstream so this is OK; consider a `--strict` flag.

## Original phase plan (for history)

## Decisions locked at kickoff (2026-05-09)

- **GitHub repo:** new public repo, Cloudflare Pages auto-deploys on push
- **History depth:** 1996–2025 (9 elections, Howard onwards). 2007–2025 from AEC structured feeds; 1996, 1998, 2001, 2004 scraped from AEC website HTML
- **DuckDB-WASM:** deferred to post-deploy (Phase G). v1 ships with per-seat JSON fetch only
- **Repo visibility:** public from day 1 (election data is public-interest; no secrets)
- **Domain:** `*.pages.dev` for v1; custom domain decision deferred

## Goal

Ship a live, free-tier public site that lets a political tragic explore Australian federal election results with editorial-quality depth. Three pillars per electorate (booth, history, demographics), driven by a national map. 2025 first; 2022 added once 2025 ships. Hosted on a `*.pages.dev` URL, total monthly cost $0.

**"Done" looks like:** open the URL, see a national map of 150 electorates colour-coded by 2025 winner. Click any seat → side panel opens with real booth-level results, real 2010–2025 historical trend, real ABS Census demographic context. Same generators (`design/lib/*.js`) drive the charts. Layout matches the concept files visually.

## Architecture (locking in earlier decisions)

```
┌─────────────────────────┐    ┌──────────────────────────┐    ┌──────────────────────────┐
│  AEC Media Feed (XML)   │    │  ABS 2021 Census (CSV)   │    │  AEC GIS shapefiles      │
│  + AEC polling places   │    │  + AEC SA1 corresp.      │    │  (electorate boundaries) │
└──────────┬──────────────┘    └──────────┬───────────────┘    └──────────┬───────────────┘
           │                              │                               │
           └──────────────┬───────────────┴───────────────────────────────┘
                          ▼
              ┌─────────────────────────────────┐
              │  Python pipeline (Polars+DuckDB)│
              │  — normalises, joins, validates │
              └──────────────┬──────────────────┘
                             ▼
              ┌──────────────┴──────────────────────┐
              ▼                       ▼              ▼
        Parquet (per-table)    JSON (per-seat)   PMTiles (boundaries)
        public/data/*.parquet  public/seats/*.json  public/tiles/aec-2025.pmtiles
                             │
                             ▼
              ┌─────────────────────────────────┐
              │  Astro static site              │
              │  + MapLibre (national map)      │
              │  + DuckDB-WASM (cross-seat SQL) │
              │  + design/lib/* generators      │
              └──────────────┬──────────────────┘
                             ▼
              ┌─────────────────────────────────┐
              │  Cloudflare Pages (build+host)  │
              │  R2 (large parquet/pmtiles)     │
              └─────────────────────────────────┘
```

Per-seat panels fetch a single small JSON (~20–80 KB) on click. National-scale aggregations (e.g. "show me all seats where ALP gained on >5% swing") run in DuckDB-WASM against shared Parquets. Map clicks are O(1) because the active-seat data is one fetch.

## Repo layout (new)

```
aec-elections/
├── PLAN.md                          ← this doc
├── parties.yaml                     ← (exists)
├── README.md                        ← brief, points at PLAN.md
├── pipeline/                        ← Python data pipeline
│   ├── pyproject.toml
│   ├── README.md
│   ├── src/aec/
│   │   ├── sources/                 ← raw fetchers (AEC feed, ABS, GIS)
│   │   │   ├── mediafeed.py
│   │   │   ├── boundaries.py
│   │   │   └── census.py
│   │   ├── transform/               ← Polars transformations
│   │   │   ├── results.py           ← per-booth + per-seat results
│   │   │   ├── preferences.py       ← distribution-of-prefs → waterfall data shape
│   │   │   ├── historical.py        ← 2010–2025 trend per seat
│   │   │   └── demographics.py      ← ABS join via SA1
│   │   ├── emit/                    ← output writers
│   │   │   ├── parquet.py
│   │   │   ├── seat_json.py         ← writes per-seat JSON matching frontend schema
│   │   │   └── pmtiles.py           ← shells out to tippecanoe
│   │   └── cli.py                   ← `aec-pipeline build --year 2025`
│   ├── tests/                       ← pytest, conservation checks
│   └── data/                        ← raw downloads (gitignored)
├── site/                            ← Astro frontend
│   ├── package.json
│   ├── astro.config.mjs
│   ├── src/
│   │   ├── pages/index.astro        ← single-page (map + side panel)
│   │   ├── components/
│   │   │   ├── NationalMap.astro    ← MapLibre wrapper
│   │   │   ├── SeatPanel.astro      ← orchestrates the 3 pillars
│   │   │   ├── BoothInset.astro
│   │   │   ├── HistoryPillar.astro  ← uses trendchart.js + primarystack.js
│   │   │   ├── DemographicPillar.astro
│   │   │   └── PreferencesPillar.astro  ← uses waterfall.js
│   │   ├── styles/global.css        ← extracted from concept-01 <style>
│   │   └── lib/                     ← copied from design/lib/
│   ├── public/
│   │   ├── data/                    ← Parquet output (built artefact)
│   │   ├── seats/                   ← per-seat JSON (built artefact)
│   │   └── tiles/                   ← PMTiles (built artefact)
│   └── tests/                       ← Playwright smoke + visual regression
├── design/                          ← (exists; concept files preserved as reference)
└── .github/workflows/build.yml      ← runs pipeline + builds site on push
```

## Phases

### Phase A — Foundations + one seat end-to-end (~1 day)

**Goal:** wire Bennelong's *real* 2025 data through the entire pipeline → static site, end-to-end. Prove the architecture before building breadth.

**Deliverables:**
- `git init`, sensible `.gitignore`
- `pipeline/` Python project with one CLI entry point
- AEC Media Feed fetcher (single seat, single event)
- Schema for `results_booth`, `results_tcp`, `preferences_rounds`, `seat_meta` Parquets
- Per-seat JSON writer matching the data shape the existing generators consume
- `site/` Astro scaffold with the concept-01 styling extracted
- One working URL (`localhost:4321/?seat=BENNELONG`) showing real data

**Acceptance:**
- TCP votes from pipeline match published AEC results exactly
- Waterfall renders correctly using `lib/waterfall.js` + real preference rounds
- All conservation checks pass (every round sums to formal vote total)

**Files touched:** new repo skeleton; `pipeline/src/aec/{sources,transform,emit}/*.py`; `site/src/pages/index.astro`; `site/src/lib/waterfall.js`.

---

### Phase B — National map (~1 day)

**Goal:** AEC electorate boundaries rendered as the national view; click on any seat → opens its side panel.

**Deliverables:**
- `boundaries.py`: download AEC GIS shapefiles (2024-redistribution edition, used for 2025 election)
- `pmtiles.py`: simplify geometry (`mapshaper -simplify 5%`), convert to GeoJSON, then `tippecanoe -o aec-2025.pmtiles --maximum-zoom=10 --minimum-zoom=2 --simplification=4`
- MapLibre integration in `NationalMap.astro`: PMTiles source, fill-colour expression keyed off `winner_party_id`, hover-state + click handler
- Replace concept-01's blob-Australia placeholder

**Acceptance:**
- All 150 electorates visible at zoom 0
- Click on any seat fires panel-open with that seat's slug
- Pages load in <2 s on broadband
- Map colour matches `parties.yaml` exactly

**Files touched:** `pipeline/src/aec/sources/boundaries.py`; `pipeline/src/aec/emit/pmtiles.py`; `site/src/components/NationalMap.astro`.

---

### Phase C — Scale to 150 seats (~0.5 day)

**Goal:** run Phase A's per-seat output for every electorate. Spot-check diverse archetypes.

**Deliverables:**
- Pipeline runs over all 150 seats in <10 minutes
- 150 per-seat JSON files generated in `site/public/seats/`
- `site/public/data/*.parquet` (national-scale aggregates: results_booth, results_tcp, history, demographics)
- Visual spot-check on 8 representative seats:
  - **Bennelong** (classic ALP-LIB)
  - **Melbourne** (ALP gain from GRN)
  - **Wentworth** (IND retain)
  - **Cowper** (NAT vs IND)
  - **Brisbane** (3-way LNP-ALP-GRN, GRN-held in 2022)
  - **Lyne** (NAT-LIB classic regional)
  - **Solomon** (NT, small electorate)
  - **Lingiari** (NT, large remote, low booth density)

**Acceptance:**
- All 8 sample seats render without runtime errors
- No NaN/missing values in any rendered chart
- Each seat's TCP percentages match AEC published results

**Files touched:** mostly data outputs; bug fixes to pipeline as edge cases surface.

---

### Phase D — History + Demographics pillars (~2 days)

**Goal:** the two pillars that the panel currently mocks become real.

**Deliverables (history):**
- 2010, 2013, 2016, 2019, 2022 results pulled from AEC Media Feed archive
- `historical.py` produces per-seat 6-election trend + per-year primary stack
- Boundary-redistribution caveat: trend uses notional 2PP/2CP recalculated on current (2025) boundaries where AEC publishes notionals; otherwise marked with a note in the panel

**Deliverables (demographics):**
- ABS 2021 Census Datapacks (DataPack G01–G09 covers age, sex, country of birth, religion, ancestry, education, dwellings, household income)
- AEC SA1-to-electorate correspondence file (2024 boundaries)
- `demographics.py` aggregates SA1 weighted by population to electorate
- Per-seat demographic JSON matches concept-01's panel layout

**Acceptance:**
- Three randomly-spotted seats: their 6-election trend matches Wikipedia's "results of … election in …"
- National median age, $ household income, % born overseas, % bachelor+ each within 0.3 of published ABS figures
- Sparklines (mortgage stress / dwelling rent) reflect actual time-series ABS data, not mocks

**Files touched:** `pipeline/src/aec/transform/{historical,demographics}.py`; `site/src/components/{HistoryPillar,DemographicPillar}.astro`.

---

### Phase E — Deploy (~0.5 day)

**Goal:** live on a public URL.

**Deliverables:**
- GitHub repo (push from local; `git init` was Phase A)
- Cloudflare Pages project connected to repo, build command `cd pipeline && python -m aec.cli build && cd ../site && npm run build`, output dir `site/dist`
- R2 bucket for the largest artefacts if Pages's per-file limit is hit (≤25 MB). PMTiles likely fits in Pages directly.
- `.github/workflows/build.yml` for repeatable CI builds
- Smoke-test: hit the live URL, click 5 random seats, verify renders

**Acceptance:**
- Public URL loads in <2.5 s on broadband
- All 150 seats clickable
- Lighthouse ≥ 90 on Performance and Accessibility
- I can post the URL to the user with confidence

---

### Phase F — Self code-review (~0.5 day, after E)

Run the `code-review` skill on the diff of `main`. Address all critical and high-severity findings before declaring done. Specifically watch for:
- XSS in any place data is rendered as HTML (the JSON includes free-text candidate names)
- Secrets accidentally committed (none expected, but check)
- Pipeline reproducibility (running the build twice produces identical artefacts)
- Conservation checks in tests (`pytest -m conservation`)
- Accessibility: keyboard nav for the panel, focus management on map clicks

## Open questions — need user confirmation before kickoff

1. **GitHub:** create a new public GitHub repo for this? Cloudflare Pages auto-deploy is easiest with a GitHub connection. (Alternative: `wrangler pages publish` from local — also free, no GitHub required.)
2. **Domain:** ship on the auto-assigned `aec-elections.pages.dev` (or similar), or are you wanting a custom domain? (Custom isn't free if you don't already own one.)
3. **History scope:** include 2010–2022 historical in the first ship, or punt to a follow-up after 2025 is solid? (Plan above includes it; punting drops Phase D's history half and shaves ~1 day.)
4. **Time:** are you OK with this taking 5–7 working days of dedicated execution across multiple sessions? Auto mode says yes — confirming.
5. **Public-or-private during build:** I'll commit + push as I go. Want the repo public from day 1, or keep private until Phase E?

## Risks & mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| 1 | AEC Media Feed XML schema changes between elections (2010 vs 2025 differ) | High | Med | Build tolerant parsers per-event; explicit schema versioning in pipeline; fail loudly with diagnostic context |
| 2 | Boundary changes 2022→2025 break naive booth-identity joins | High | High | Use AEC notional 2PP files for 2022 on 2025 boundaries; mark booths-with-no-prior-data explicitly in UI |
| 3 | PMTiles bundle exceeds Cloudflare Pages 25 MB per-file limit | Med | Med | Pre-test size after `tippecanoe`; if oversized, host on R2 with CORS allowed |
| 4 | DuckDB-WASM bundle (~3 MB) hurts initial page load | Med | Low | Lazy-load only when user opens a "compare seats" view; map + single-seat work without it |
| 5 | Census privacy randomisation makes small-cell counts unstable | Med | Low | Don't render counts <50 from Census; aggregate to suppress |
| 6 | Cloudflare free-tier exhaustion (500 builds/month or 100k req/day) | Low | Low | One build per push; monitor usage; fall back to GitHub Pages if hit |
| 7 | I introduce a per-seat edge case that breaks rendering for 1 of 150 (e.g. a single-candidate uncontested seat) | Med | Low | Phase C's 8-seat spot-check; add a `pytest` per-seat smoke that loads each JSON and asserts required keys |
| 8 | XSS via candidate name fields | Low | High | Generators already use `escapeHTML`; add an end-to-end test with a `"<script>"`-named test fixture |

## Self-review of this plan

I read this back and these are the things that worry me:

**1. Phase A is doing a lot.** "Pipeline scaffold + Astro scaffold + one seat end-to-end + JSON schema design" is more like 1.5 days realistically. I left it at 1 because the schema is already de-facto designed by the existing JS generators (`waterfall.js` etc.) — they consumed mock data with a defined shape; pipeline just emits that shape. Watch for slip; if Phase A spills, fold the spillover into Phase B's start, don't compress later phases.

**2. Real AEC Media Feed format isn't validated.** I'm asserting it'll cleanly parse to my schema. AEC publishes ~15 different XML files per election; I'll need to confirm which one has booth-level TCP and which has distribution-of-preferences. **Mitigation:** Phase A's first task is "fetch + manually inspect the 2025 feed before writing transform code." If schema is uglier than expected, flag immediately rather than power through.

**3. Astro choice — is it actually right?** Astro is great for static-first with islands. But this is essentially a single-page app with a map and a panel — nothing else. Vanilla HTML/JS would also work and ship 30% smaller. I picked Astro for: TypeScript, clean component model, easy partial hydration of the DuckDB-WASM island, future-proofing if we add a `/seats/[slug]` printable view. **Mitigation:** if Phase A's Astro setup feels like overkill after the first half-day, switch to a vite + vanilla setup. Cost of switch is low at that point.

**4. DuckDB-WASM may be premature.** I'm planning it in for "compare seats" / national-scale queries, but the MVP doesn't need it. Could ship Phase A–E with just per-seat JSON fetch and add DuckDB later. **Mitigation:** treat DuckDB as Phase G (post-deploy enhancement); descope from the critical path.

**5. Cloudflare auth boundary.** Connecting Pages to GitHub needs a one-time OAuth in the Cloudflare dashboard — that's a manual step you have to do in browser. I'll prepare everything else, then hand you a 30-second click-through. Flagged as open question 1.

**6. The 8-seat spot-check is a heuristic, not a guarantee.** Rendering 150 unique data shapes from a generator means there will be edge cases I haven't anticipated. **Mitigation:** add a "per-seat smoke" test in Phase C that loads each JSON and asserts the generators don't throw — catches breakage even on seats nobody manually inspects.

**7. I haven't planned for site updates.** What happens when AEC updates the feed? When new ABS data drops? Right now the pipeline runs on demand. For a personal post-election dashboard this is fine. **Mitigation:** document in `pipeline/README.md` how to rebuild; don't over-engineer for "live" since the project is explicitly post-election static.

**8. Memory says "result tag should match winning party colour" was already addressed in concept-01.** When I scaffold from concept-01, that's preserved automatically. Noting so I don't accidentally lose user-confirmed editorial decisions in the port.

**Things I'm intentionally not planning:**
- Predictions / live updates (out of scope per project memory)
- Comments / accounts / social (out of scope, free-tier wouldn't support cleanly)
- Mobile-first responsive — concept files are desktop-shaped; I'll add basic responsive but won't redesign the side panel for narrow viewports until you ask

## Execution discipline

Once approved:
- One git branch per phase (`phase-a-foundations`, `phase-b-map`, …); merge to `main` at acceptance
- Commit at every clean checkpoint (every ~30–60 min of work)
- Run `pytest pipeline/` before every commit on the pipeline; run `npm run build && npm run test` before every site commit
- After Phase B and after Phase E, invoke the `code-review` skill on the cumulative diff
- Treat every "open in browser" as the truth check — don't declare a phase done without seeing it work
- Flag any user-decision needed (open questions surfacing mid-work) immediately, don't power through
