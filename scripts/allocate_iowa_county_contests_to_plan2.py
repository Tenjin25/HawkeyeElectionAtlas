#!/usr/bin/env python3
"""Allocate verified Iowa county contest totals through 2020 blocks to Plan 2."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path

from build_iowa_contest_slices import load_counties


ROOT = Path(__file__).resolve().parents[1]
CHAMBERS = ("congressional", "house", "senate")
EXPECTED_DISTRICTS = {"congressional": 4, "house": 100, "senate": 50}


def clean(value: object) -> str:
    """Normalize source labels without importing GIS-only dependencies."""
    value = str(value or "").upper().replace("&", " AND ")
    value = re.sub(r"[’'`]", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def load_population(path: Path) -> dict[str, int]:
    population = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            block_id = str(row.get("block_id") or "").strip()
            if len(block_id) == 15 and block_id.isdigit():
                population[block_id] = int(float(row.get("population") or 0))
    return population


def build_county_district_shares(
    block_population: dict[str, int],
    crosswalk_dir: Path,
) -> tuple[dict[str, dict[str, list[tuple[str, float]]]], dict]:
    county_names = {clean(name): fips for fips, name in load_counties().items()}
    if len(county_names) != 99:
        raise ValueError(f"Expected 99 Iowa counties, got {len(county_names)}")

    shares_by_chamber = {}
    summary = {"counties": len(county_names), "chambers": {}}
    for chamber in CHAMBERS:
        district_population = defaultdict(float)
        path = crosswalk_dir / f"block20_to_plan2_{chamber}_equivalency.csv"
        mapped_blocks = set()
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                block_id = str(row.get("block_id") or "").strip()
                district = str(row.get("plan2_district") or "").strip()
                if not block_id or not district:
                    continue
                mapped_blocks.add(block_id)
                population = block_population.get(block_id, 0)
                share = float(row.get("share") or 0)
                district_population[(block_id[2:5], district)] += population * share

        county_totals = defaultdict(float)
        for (county_fips, _), population in district_population.items():
            county_totals[county_fips] += population
        by_county_fips = defaultdict(list)
        for (county_fips, district), population in district_population.items():
            total = county_totals[county_fips]
            if total > 0 and population > 0:
                by_county_fips[county_fips].append((district, population / total))

        if len(by_county_fips) != 99:
            missing = sorted(set(county_names.values()) - set(by_county_fips))
            raise ValueError(f"{chamber}: missing county population shares: {missing}")
        district_ids = {district for links in by_county_fips.values() for district, _ in links}
        if len(district_ids) != EXPECTED_DISTRICTS[chamber]:
            raise ValueError(
                f"{chamber}: expected {EXPECTED_DISTRICTS[chamber]} districts, got {len(district_ids)}"
            )
        for county_fips, links in by_county_fips.items():
            if abs(sum(share for _, share in links) - 1.0) > 1e-9:
                raise ValueError(f"{chamber}: shares do not sum to one for county {county_fips}")

        shares_by_chamber[chamber] = {
            county_name: sorted(by_county_fips[county_fips], key=lambda item: int(item[0]))
            for county_name, county_fips in county_names.items()
        }
        summary["chambers"][chamber] = {
            "districts": len(district_ids),
            "mapped_blocks": len(mapped_blocks),
            "split_counties": sum(len(links) > 1 for links in by_county_fips.values()),
            "population": int(round(sum(county_totals.values()))),
            "crosswalk": path.relative_to(ROOT).as_posix(),
        }
    return shares_by_chamber, summary


def allocate_year(
    year: int,
    source: Path,
    output_dir: Path,
    shares_by_chamber: dict[str, dict[str, list[tuple[str, float]]]],
) -> dict:
    source_rows = []
    with source.open(encoding="utf-8", newline="") as handle:
        source_rows.extend(csv.DictReader(handle))

    outputs = {}
    for chamber in CHAMBERS:
        allocated = defaultdict(float)
        county_shares = shares_by_chamber[chamber]
        for row in source_rows:
            county = clean(row.get("county"))
            links = county_shares.get(county)
            if not links:
                raise ValueError(f"{year} {chamber}: no Plan 2 shares for county {county!r}")
            votes = float(row.get("votes") or 0)
            key = (
                clean(row.get("office")),
                clean(row.get("district")),
                clean(row.get("candidate")),
                clean(row.get("party")),
            )
            for target_district, share in links:
                allocated[(target_district,) + key] += votes * share

        output = output_dir / f"{year}_contests_to_plan2_{chamber}.csv"
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["year", "plan2_district", "office", "district", "candidate", "party", "votes"])
            for (target, office, district, candidate, party), votes in sorted(
                allocated.items(), key=lambda item: (int(item[0][0]), item[0][1:])
            ):
                writer.writerow([year, target, office, district, candidate, party, f"{votes:.12f}"])
        outputs[chamber] = {
            "rows": len(allocated),
            "districts": len({key[0] for key in allocated}),
            "output": output.relative_to(ROOT).as_posix(),
        }
    return {"year": year, "source": source.relative_to(ROOT).as_posix(), "outputs": outputs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, action="append")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "data/aggregates_verified")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/aggregates_county_block_population_2020",
    )
    parser.add_argument(
        "--block-population",
        type=Path,
        default=ROOT / "data/census/block_population_2020.csv",
    )
    parser.add_argument("--crosswalk-dir", type=Path, default=ROOT / "data/crosswalks")
    args = parser.parse_args()

    sources = {
        int(path.name[:4]): path
        for path in args.source_dir.glob("*_contests_to_county.csv")
        if path.name[:4].isdigit()
    }
    years = sorted(set(args.year or sources))
    missing = [year for year in years if year not in sources]
    if missing:
        raise FileNotFoundError(f"Missing verified county aggregates for years: {missing}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    population = load_population(args.block_population)
    shares, summary = build_county_district_shares(population, args.crosswalk_dir)
    summary["allocation_method"] = "verified_county_votes_to_2020_census_blocks_by_population_to_iowa_plan2"
    summary["lines_year"] = 2022
    summary["target_plan"] = "Iowa Plan 2 enacted in 2021"
    summary["block_population"] = args.block_population.relative_to(ROOT).as_posix()
    summary["years"] = years
    summary["vote_coverage_pct"] = 100.0
    summary["geographic_precision"] = "county_population_disaggregation"

    results = [allocate_year(year, sources[year], args.output_dir, shares) for year in years]
    metadata_path = args.output_dir / "allocation_metadata.json"
    if metadata_path.exists():
        previous_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        summary["years"] = sorted(set(previous_metadata.get("years", [])) | set(years))
    metadata_path.write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    results_path = args.output_dir / "allocation_summary.json"
    previous_results = []
    if results_path.exists():
        previous_results = json.loads(results_path.read_text(encoding="utf-8")).get("years", [])
    merged_results = [result for result in previous_results if result.get("year") not in years] + results
    results_path.write_text(
        json.dumps({"years": sorted(merged_results, key=lambda result: result["year"])}, indent=2) + "\n",
        encoding="utf-8",
    )
    print({"years": years, "population_blocks": len(population), "output": str(args.output_dir)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
