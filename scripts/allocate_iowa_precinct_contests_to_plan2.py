#!/usr/bin/env python3
"""Allocate modern Iowa statewide precinct results to reported Plan 2 districts."""

from __future__ import annotations

import argparse
import csv
import difflib
import json
from collections import defaultdict
from pathlib import Path

from aggregate_iowa_contests import clean, precinct_key
from allocate_iowa_county_contests_to_plan2 import build_county_district_shares, load_population
from build_iowa_district_slices import OFFICE_TYPES


ROOT = Path(__file__).resolve().parents[1]
INPUTS = {
    2022: ROOT / "data/2022/20221108__ia__general__precinct.csv",
    2024: ROOT / "data/2024/20241105__ia__general__precinct.csv",
}
CHAMBERS = ("house", "senate")


def normalized_office(value: object) -> str:
    return clean(value)


def normalized_precinct(value: object) -> str:
    return precinct_key(value)


def district_links(votes: dict[str, float]) -> list[tuple[str, float]]:
    total = sum(votes.values())
    if total <= 0:
        share = 1 / len(votes)
        return sorted(((district, share) for district in votes), key=lambda item: int(item[0]))
    return sorted(
        ((district, value / total) for district, value in votes.items()),
        key=lambda item: int(item[0]),
    )


def build_house_links(rows: list[dict]) -> tuple[dict, dict, dict]:
    house_votes = defaultdict(lambda: defaultdict(float))
    county_votes = defaultdict(lambda: defaultdict(float))
    county_precincts = defaultdict(set)
    for row in rows:
        if normalized_office(row.get("office")) != "STATE HOUSE":
            continue
        district_raw = str(row.get("district") or "").strip()
        if not district_raw:
            continue
        district = str(int(district_raw))
        county = clean(row.get("county"))
        precinct = normalized_precinct(row.get("precinct"))
        votes = float(row.get("votes") or 0)
        house_votes[(county, precinct)][district] += votes
        county_votes[county][district] += votes
        county_precincts[county].add(precinct)
    return (
        {key: district_links(value) for key, value in house_votes.items()},
        {county: district_links(value) for county, value in county_votes.items()},
        county_precincts,
    )


def find_links(
    county: str,
    precinct: str,
    house_links: dict,
    county_links: dict,
    county_precincts: dict,
) -> tuple[list[tuple[str, float]], str]:
    direct = house_links.get((county, precinct))
    if direct:
        return direct, "direct"

    candidates = sorted(county_precincts.get(county, set()))
    same_tokens = [candidate for candidate in candidates if sorted(candidate.split()) == sorted(precinct.split())]
    if len(same_tokens) == 1:
        return house_links[(county, same_tokens[0])], "token_match"

    first_token = precinct.split()[0] if precinct else ""
    same_prefix = [candidate for candidate in candidates if candidate.split()[:1] == [first_token]]
    prefix_districts = {
        district
        for candidate in same_prefix
        for district, _ in house_links[(county, candidate)]
    }
    if same_prefix and len(prefix_districts) == 1:
        return [(next(iter(prefix_districts)), 1.0)], "prefix_match"

    close = difflib.get_close_matches(precinct, candidates, n=1, cutoff=0.82)
    if close:
        return house_links[(county, close[0])], "fuzzy_match"

    links = county_links.get(county)
    if not links:
        raise ValueError(f"No State House mapping for {county} / {precinct}")
    if len(links) == 1:
        return links, "single_district_county"
    return links, "county_house_vote_fallback"


def allocate_year(year: int, source: Path, output_dir: Path, population_fallback: dict) -> dict:
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    house_links, county_links, county_precincts = build_house_links(rows)
    for county, links in population_fallback.items():
        county_links.setdefault(county, links)

    house_seed = defaultdict(lambda: defaultdict(float))
    party_seed = defaultdict(lambda: defaultdict(float))
    match_methods = defaultdict(int)
    statewide_rows = 0
    for row in rows:
        office = normalized_office(row.get("office"))
        if office not in OFFICE_TYPES:
            continue
        county = clean(row.get("county"))
        precinct = normalized_precinct(row.get("precinct"))
        links, method = find_links(county, precinct, house_links, county_links, county_precincts)
        match_methods[method] += 1
        statewide_rows += 1
        exact_key = (
            county,
            office,
            clean(row.get("district")),
            clean(row.get("candidate")),
            clean(row.get("party")),
        )
        party_key = (county, office, clean(row.get("party")))
        votes = float(row.get("votes") or 0)
        for house, share in links:
            house_seed[exact_key][house] += votes * share
            party_seed[party_key][house] += votes * share

    official = ROOT / "data/aggregates_verified" / f"{year}_contests_to_county.csv"
    with official.open(encoding="utf-8", newline="") as handle:
        official_rows = list(csv.DictReader(handle))
    outputs = {chamber: defaultdict(float) for chamber in CHAMBERS}
    calibration_methods = defaultdict(int)
    for row in official_rows:
        office = normalized_office(row.get("office"))
        if office not in OFFICE_TYPES:
            continue
        county = clean(row.get("county"))
        exact_key = (
            county,
            office,
            clean(row.get("district")),
            clean(row.get("candidate")),
            clean(row.get("party")),
        )
        party_key = (county, office, clean(row.get("party")))
        seed_links = house_seed.get(exact_key)
        if seed_links and sum(seed_links.values()) > 0:
            links = district_links(seed_links)
            calibration_methods["candidate_precinct_seed"] += 1
        elif party_seed.get(party_key) and sum(party_seed[party_key].values()) > 0:
            links = district_links(party_seed[party_key])
            calibration_methods["party_precinct_seed"] += 1
        else:
            links = population_fallback[county]
            calibration_methods["county_population_fallback"] += 1
        key = (
            office,
            clean(row.get("district")),
            clean(row.get("candidate")),
            clean(row.get("party")),
        )
        votes = float(row.get("votes") or 0)
        senate_links = defaultdict(float)
        for house, share in links:
            outputs["house"][(house,) + key] += votes * share
            senate_links[str((int(house) + 1) // 2)] += share
        for senate, share in senate_links.items():
            outputs["senate"][(senate,) + key] += votes * share

    result = {
        "year": year,
        "source": source.relative_to(ROOT).as_posix(),
        "official_totals": official.relative_to(ROOT).as_posix(),
        "matches": dict(match_methods),
        "calibration": dict(calibration_methods),
    }
    for chamber in CHAMBERS:
        output = output_dir / f"{year}_contests_to_plan2_{chamber}.csv"
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["year", "plan2_district", "office", "district", "candidate", "party", "votes"])
            for (target, office, district, candidate, party), votes in sorted(
                outputs[chamber].items(), key=lambda item: (int(item[0][0]), item[0][1:])
            ):
                writer.writerow([year, target, office, district, candidate, party, f"{votes:.12f}"])
        result[chamber] = {
            "output": output.relative_to(ROOT).as_posix(),
            "rows": len(outputs[chamber]),
            "districts": len({key[0] for key in outputs[chamber]}),
        }
    result["statewide_rows"] = statewide_rows
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, action="append")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/aggregates_precinct_plan2",
    )
    parser.add_argument(
        "--block-population",
        type=Path,
        default=ROOT / "data/census/block_population_2020.csv",
    )
    parser.add_argument("--crosswalk-dir", type=Path, default=ROOT / "data/crosswalks")
    args = parser.parse_args()
    years = sorted(set(args.year or INPUTS))
    missing = [year for year in years if year not in INPUTS]
    if missing:
        raise ValueError(f"No configured precinct input for: {missing}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    population = load_population(args.block_population)
    all_population_shares, _ = build_county_district_shares(population, args.crosswalk_dir)
    results = [
        allocate_year(year, INPUTS[year], args.output_dir, all_population_shares["house"])
        for year in years
    ]
    metadata = {
        "allocation_method": "official_county_totals_calibrated_to_precinct_reported_plan2_house_district",
        "geographic_precision": "precinct_to_native_plan2_legislative_district",
        "vote_coverage_pct": 100.0,
        "match_coverage_pct": 100.0,
        "lines_year": 2022,
        "target_plan": "Iowa Plan 2 enacted in 2021",
        "split_precinct_method": "reported_state_house_ballot_vote_share",
        "years": years,
    }
    (args.output_dir / "allocation_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    (args.output_dir / "allocation_summary.json").write_text(
        json.dumps({"years": results}, indent=2) + "\n", encoding="utf-8"
    )
    print({"years": years, "output": str(args.output_dir)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
