# Hawkeye Election Atlas

Hawkeye Election Atlas is an interactive browser atlas for exploring Iowa election results by county, congressional district, Iowa House district, and Iowa Senate district.

The current atlas includes statewide contests from 1980 through 2024 and district aggregations from 2000 through 2024. District results use Iowa's Plan 2 boundaries enacted in 2021. Negative signed margins represent Democratic leads; positive signed margins represent Republican leads.

## Run locally

The atlas is a static site, so it only needs a local HTTP server:

```powershell
python -m http.server 8000
```

Then open `http://localhost:8000/`.

## Current polling sites

The **Polling Sites** switch under More shows a separate point layer of current polling locations. It loads `data/ia_current_polling_sites.geojson`, built from the tracked current polling-place source by `scripts/build_current_polling_sites.py`. The builder adds denomination suffixes only where the site and affiliation were independently verified; the source URL is stored with each such point. The current points are separate from the 2020 precinct polygons and their historical election results.

Rebuild the displayed point layer after updating its source:

```powershell
python scripts/build_current_polling_sites.py
```
## Project layout

- `index.html` contains the map interface and application orchestration.
- `js/` contains reusable election, display, trend, region, and data helpers.
- `data/iowa_contests/` contains pretty-printed county contest slices.
- `data/district_contests/` contains Plan 2 congressional and legislative contest slices.
- `scripts/` contains the ingestion, normalization, crosswalk, aggregation, and frontend build pipeline.
- `data/census/` and `data/crosswalks/` contain geography and equivalency inputs used by the pipeline.

## Large source files

Downloaded archives, spreadsheets, shapefiles, and PDFs are tracked with Git LFS. Install Git LFS before cloning if you need the complete source-data pipeline:

```powershell
git lfs install
```

The rendered atlas assets remain ordinary repository files so the static application can load them directly.

## CVAP data attribution

Citizen Voting Age Population (CVAP) totals use the U.S. Census Bureau's 2020-2024 American Community Survey five-year CVAP Special Tabulation. Precinct and legacy-boundary aggregates use the Redistricting Data Hub's **2024 CVAP Data Disaggregated to 2020 Census Blocks**.

- Census source: https://www.census.gov/programs-surveys/decennial-census/about/voting-rights/cvap/2020-2024-CVAP.html
- Block-level source and processing: https://redistrictingdatahub.org/

Credit: **U.S. Census Bureau; Redistricting Data Hub.**

## Legend layout (September 2026)

The map key now uses the same expandable, scrollable category-row layout as Margin Categories for Winners, Flips, Shift, and Demographics. Each row pairs a named category with its map color and a short range or interpretation. Population Change uses the same layout where that mode is available.

Shift retains its 15-step diverging spectrum and separates Democratic and Republican movement at 0.5, 1, 5, 10, 15, 20, and 25 percentage points. Movement below 0.5 points is near-white; the 25-point-and-higher category is named **Extreme**. The blue/orange colorblind palette follows the same directional bins.
