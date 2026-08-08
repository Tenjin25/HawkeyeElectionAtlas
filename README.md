# Hawkeye Election Atlas

Hawkeye Election Atlas is an interactive browser atlas for exploring Iowa election results by county, congressional district, Iowa House district, and Iowa Senate district.

The current atlas includes statewide contests from 1980 through 2024 and district aggregations from 2000 through 2024. District results use Iowa's Plan 2 boundaries enacted in 2021. Negative signed margins represent Democratic leads; positive signed margins represent Republican leads.

## Run locally

The atlas is a static site, so it only needs a local HTTP server:

```powershell
python -m http.server 8000
```

Then open `http://localhost:8000/`.

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
