#!/usr/bin/env python3
"""Calibrate official Iowa county totals with historical precinct/VTD distributions."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from aggregate_iowa_contests import clean
from allocate_iowa_county_contests_to_plan2 import build_county_district_shares, load_population
from build_iowa_contest_slices import load_counties


ROOT = Path(__file__).resolve().parents[1]
CHAMBERS = ("house", "senate")
VINTAGES = {2000: "00", 2004: "00", 2008: "10", 2012: "10", 2014: "10"}
DRA_2008_URL = "https://raw.githubusercontent.com/dra2020/vtd_data/master/2010_VTD/IA/2010_election_IA.csv"


def load_crosswalk(path: Path) -> dict[str, list[tuple[str, float]]]:
    links = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            links[str(row["source_vtd"]).strip()].append(
                (str(int(row["target_vtd"])), float(row["share"]))
            )
    return links


def add_seed(seed: dict, vtd_id: str, office: str, party: str, votes: float, links: dict) -> None:
    if votes <= 0 or vtd_id not in links:
        return
    county_fips = vtd_id[2:5]
    for district, share in links[vtd_id]:
        seed[(county_fips, clean(office), clean(party))][district] += votes * share


def load_native_seed(year: int, chamber: str, links: dict) -> dict:
    seed = defaultdict(lambda: defaultdict(float))
    source = ROOT / "data/aggregates_historical_native" / f"{year}_contests_to_vtd{VINTAGES[year]}.csv"
    with source.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            add_seed(
                seed,
                str(row["vtd_id"]).strip(),
                row.get("office", ""),
                row.get("party", ""),
                float(row.get("votes") or 0),
                links,
            )
    return seed


def load_dra_2008_seed(path: Path, chamber: str, links: dict) -> dict:
    seed = defaultdict(lambda: defaultdict(float))
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            vtd_id = str(row["GEOID10"]).strip()
            add_seed(seed, vtd_id, "PRESIDENT VICE PRESIDENT", "DEMOCRAT", float(row["Dem_2008_pres"]), links)
            add_seed(seed, vtd_id, "PRESIDENT VICE PRESIDENT", "REPUBLICAN", float(row["Rep_2008_pres"]), links)
    return seed


def allocate_year(
    year: int,
    chamber: str,
    population_shares: dict,
    dra_2008_source: Path | None,
    output_dir: Path,
) -> dict:
    vintage = VINTAGES[year]
    links = load_crosswalk(ROOT / "data/crosswalks" / f"vtd{vintage}_to_plan2_{chamber}.csv")
    if year == 2008:
        if not dra_2008_source or not dra_2008_source.exists():
            raise FileNotFoundError("2008 requires --dra-2008-source pointing to 2010_election_IA.csv")
        seed = load_dra_2008_seed(dra_2008_source, chamber, links)
        seed_source = DRA_2008_URL
    else:
        seed = load_native_seed(year, chamber, links)
        seed_source = f"data/aggregates_historical_native/{year}_contests_to_vtd{vintage}.csv"

    fips_to_name = {fips: clean(name) for fips, name in load_counties().items()}
    name_to_fips = {name: fips for fips, name in fips_to_name.items()}
    official = ROOT / "data/aggregates_verified" / f"{year}_contests_to_county.csv"
    with official.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    official_group_totals = defaultdict(float)
    seeded_offices = {office for (_, office, _), values in seed.items() if sum(values.values()) > 0}
    for row in rows:
        county = clean(row.get("county"))
        fips = name_to_fips.get(county)
        if not fips:
            raise ValueError(f"Unknown county {county!r}")
        office = clean(row.get("office"))
        if office in seeded_offices:
            official_group_totals[(fips, office, clean(row.get("party")))] += float(row.get("votes") or 0)

    calibrated_shares = {}
    diagnostics = {"seeded_groups": 0, "population_only_groups": 0, "seed_votes": 0.0, "official_votes": 0.0}
    for group, total in official_group_totals.items():
        fips, office, party = group
        county = fips_to_name[fips]
        pop_links = dict(population_shares[county])
        seed_links = seed.get(group, {})
        seed_total = sum(seed_links.values())
        diagnostics["official_votes"] += total
        diagnostics["seed_votes"] += min(seed_total, total)
        if seed_total > 0:
            diagnostics["seeded_groups"] += 1
            if seed_total > total:
                values = {district: value * total / seed_total for district, value in seed_links.items()}
            else:
                remainder = total - seed_total
                values = {
                    district: seed_links.get(district, 0.0) + remainder * share
                    for district, share in pop_links.items()
                }
        else:
            diagnostics["population_only_groups"] += 1
            values = {district: total * share for district, share in pop_links.items()}
        value_total = sum(values.values())
        calibrated_shares[group] = {
            district: value / value_total for district, value in values.items()
        } if value_total > 0 else pop_links

    allocated = defaultdict(float)
    for row in rows:
        county = clean(row.get("county"))
        fips = name_to_fips[county]
        office = clean(row.get("office"))
        if office not in seeded_offices:
            continue
        group = (fips, office, clean(row.get("party")))
        key = (
            office,
            clean(row.get("district")),
            clean(row.get("candidate")),
            clean(row.get("party")),
        )
        votes = float(row.get("votes") or 0)
        for district, share in calibrated_shares[group].items():
            allocated[(district,) + key] += votes * share

    output = output_dir / f"{year}_contests_to_plan2_{chamber}.csv"
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["year", "plan2_district", "office", "district", "candidate", "party", "votes"])
        for (target, office, district, candidate, party), votes in sorted(
            allocated.items(), key=lambda item: (int(item[0][0]), item[0][1:])
        ):
            writer.writerow([year, target, office, district, candidate, party, f"{votes:.12f}"])
    diagnostics.update({
        "year": year,
        "chamber": chamber,
        "source": seed_source,
        "output": output.relative_to(ROOT).as_posix(),
        "districts": len({key[0] for key in allocated}),
        "seeded_offices": sorted(seeded_offices),
        "seed_coverage_pct": round(diagnostics["seed_votes"] / diagnostics["official_votes"] * 100, 4)
        if diagnostics["official_votes"] else 0.0,
    })
    return diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, action="append")
    parser.add_argument("--dra-2008-source", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/aggregates_historical_calibrated",
    )
    parser.add_argument(
        "--block-population",
        type=Path,
        default=ROOT / "data/census/block_population_2020.csv",
    )
    parser.add_argument("--crosswalk-dir", type=Path, default=ROOT / "data/crosswalks")
    args = parser.parse_args()
    years = sorted(set(args.year or VINTAGES))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    population = load_population(args.block_population)
    all_population_shares, _ = build_county_district_shares(population, args.crosswalk_dir)
    results = [
        allocate_year(year, chamber, all_population_shares[chamber], args.dra_2008_source, args.output_dir)
        for year in years
        for chamber in CHAMBERS
    ]
    year_metadata = {
        str(year): {
            "match_coverage_pct": min(
                result["seed_coverage_pct"] for result in results if result["year"] == year
            )
        }
        for year in years
    }
    metadata = {
        "allocation_method": "official_county_totals_with_matched_precinct_vtd_seeds_and_population_remainder",
        "geographic_precision": "precinct_seeded_county_calibration",
        "vote_coverage_pct": 100.0,
        "lines_year": 2022,
        "target_plan": "Iowa Plan 2 enacted in 2021",
        "years": years,
        "year_metadata": year_metadata,
        "dra_2008_source": DRA_2008_URL if 2008 in years else None,
    }
    (args.output_dir / "allocation_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "allocation_summary.json").write_text(
        json.dumps({"results": results}, indent=2) + "\n", encoding="utf-8"
    )
    print({"years": years, "output": str(args.output_dir)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
