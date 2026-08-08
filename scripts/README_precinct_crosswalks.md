# Precinct crosswalk setup

`build_precinct_crosswalk.py` is the common ingestion layer for election files
whose column layouts differ by year or county. It supports CSV, TSV, JSON row
arrays, and JSONL. It recognizes common variants of county, precinct, and vote
columns; for the older Iowa exports it uses `jurisdiction` as the precinct
label. County totals and absentee/special-ballot aggregate rows are excluded.

Inspect a file before building a bridge:

```powershell
python scripts/build_precinct_crosswalk.py inspect data/2016/20161108__ia__general__precinct.csv
```

Build a conservative bridge:

```powershell
python scripts/build_precinct_crosswalk.py build `
  --source data/2012/20121106__ia__general__adair__precinct.csv `
  --target data/2020/counties/20201103__ia__general__adair__precinct.csv `
  --output tmp/adair_2012_to_2020_crosswalk.csv
```

The output is compatible with the existing `source_precinct_id`,
`target_precinct_id`, and `share` convention. Exact matches receive share 1.
Use `--allow-fuzzy` only when you want high-confidence name suggestions; those
rows are marked `match_method=fuzzy` so they can be reviewed. For genuine
precinct splits/merges, provide a JSON override file mapping source IDs to
target IDs and then assign reviewed shares in the generated CSV.

## 2008 VTD00 geometry

The Census 2008 release stores Iowa VTD00 files by county. Download all 99
county archives and create a manifest with:

```powershell
python scripts/fetch_iowa_2008_vtd00.py
```

The source directory is the official Census `TIGER2008/19_IOWA` index. The
downloaded archives are kept separately under `data/census/tl_2008_19_vtd00/`
so they can be inspected or re-run without mixing them with the 2010/2020
statewide inputs.

## Geometry chain and proposed Plan 2

Build equal-area VTD bridges and VTD-to-Plan2 district bridges with:

```powershell
python scripts/build_vtd_crosswalk_chain.py
```

This writes the 2000-to-2010, 2010-to-2020, and chained 2000-to-2020 files,
plus nine VTD-to-Plan2 files covering congressional, House, and Senate
districts for each VTD vintage. Shares are normalized per source VTD.

For block-based chains, run:

```powershell
python scripts/build_block_vtd_chain.py
```

For a resumable run, use `--vintage 00`, `--vintage 10`, or `--vintage 20`
with `--only-direct`, then `--compose-only`. This produces direct block-to-VTD
membership files and NHGIS-weighted `block00_to_vtd10_chain.csv`,
`block10_to_vtd20_chain.csv`, and `block00_to_vtd20_chain.csv`.

To connect the supplied Plan 2 district shapefiles through 2020 Census
blocks to VTD20s, run:

```powershell
python scripts/build_plan2_block_vtd_chain.py
```

This writes exact Plan 2 block equivalencies plus normalized
`vtd20_to_plan2_*_via_blocks.csv` files for Congress, House, and Senate.
It also carries the Plan 2 assignments backward to
`block10_to_plan2_*_via_blocks.csv` and
`block00_to_plan2_*_via_blocks.csv` through the NHGIS 2010→2020 and
2000→2010→2020 block transitions. The explicit normalized
`vtd20_to_block20.csv` leg is included as well.

Historical population-weighted election disaggregation uses
`scripts/extract_sf1_block_population.py` to extract official 2000 and 2010
block totals, then `scripts/build_population_weighted_election_vtd_crosswalk.py`
to allocate current election-precinct geometries through the era-appropriate
block/VTD chain. The resulting crosswalks are suffixed `_population_2000` or
`_population_2010`.

## Election precinct boundaries

For a polygon boundary layer, use the equal-area geometry bridge:

```powershell
python scripts/build_election_precinct_vtd_crosswalk.py
```

The default input is the VEST 2020 Iowa layer at
`data/election_precinct_boundaries/vest/ia_2020_vest.zip`. The output is
`data/crosswalks/election_precinct20_to_vtd20.csv`. Each election precinct
can map to multiple Census VTD20s; `share` is normalized to sum to 1 for each
source precinct. The script accepts a different ZIP and shapefile stem with
`--election-zip` and `--election-stem`.

It also composes that bridge with the workspace's Plan 2 VTD20 files and
writes `election_precinct20_to_plan2_congressional.csv`,
`election_precinct20_to_plan2_house.csv`, and
`election_precinct20_to_plan2_senate.csv`.

## 2020 district aggregation

`aggregate_iowa_contests.py` resolves 2020 result labels against the VEST
geometry conservatively. Exact normalized names are preferred. When an Iowa
results label is abbreviated, the script uses the unique county-level tuple of
Trump, Biden, Ernst, and Greenfield votes stored in the VEST DBF as a verified
fallback. Prefix and fuzzy name matching are only used when that signature is
not available. The precinct match report records the selected method for every
precinct.

For a population-weighted trial using the GeoJSON-derived Plan 2 bridges:

```powershell
python scripts/aggregate_iowa_contests.py --year 2020 `
  --output-dir tmp/district_aggregation_trial `
  --current-geometry `
  --election-crosswalk data/crosswalks/election_precinct20_to_vtd20_population.csv `
  --crosswalk-dir data/crosswalks_geojson
```

After validating the aggregate totals, publish the browser slices and 2022-lines
manifest with:

```powershell
$dra2008 = Join-Path $env:TEMP '2010_election_IA.csv'
Invoke-WebRequest `
  'https://raw.githubusercontent.com/dra2020/vtd_data/master/2010_VTD/IA/2010_election_IA.csv' `
  -OutFile $dra2008

python scripts/allocate_iowa_historical_contests_to_plan2.py `
  --dra-2008-source $dra2008
python scripts/allocate_iowa_precinct_contests_to_plan2.py

python scripts/build_iowa_district_slices.py --clean `
  --year 2000 --year 2002 --year 2004 --year 2006 --year 2008 `
  --year 2010 --year 2012 --year 2014 --year 2016 --year 2018 `
  --year 2020 --year 2022 --year 2024 `
  --source-dir data/aggregates_precinct_plan2 `
  --source-dir data/aggregates_historical_calibrated `
  --source-dir data/aggregates_verified `
  --source-dir data/aggregates_county_block_population_2020
```

The publisher requires 4 congressional, 100 House, and 50 Senate districts and
checks that each scope preserves identical statewide totals for every contest.
It selects each contest from the first source directory that contains it, so
precinct-native and precinct-seeded results can coexist with county-population
fallbacks. The external 2008 presidential seed is the Iowa 2010-VTD election
file from [Dave's Redistricting](https://github.com/dra2020/vtd_data).
