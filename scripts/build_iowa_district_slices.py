#!/usr/bin/env python3
"""Convert verified Iowa Plan 2 aggregate CSVs into frontend district slices."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OFFICE_TYPES = {
    "PRESIDENT": "president",
    "PRESIDENT VICE PRESIDENT": "president",
    "US SENATE": "us_senate",
    "U S SENATE": "us_senate",
    "GOVERNOR": "governor",
    "LIEUTENANT GOVERNOR": "lieutenant_governor",
    "SECRETARY OF STATE": "secretary_of_state",
    "ATTORNEY GENERAL": "attorney_general",
    "AUDITOR OF STATE": "auditor",
    "TREASURER OF STATE": "treasurer",
    "SECRETARY OF AGRICULTURE": "agriculture_commissioner",
    "AGRICULTURE COMMISSIONER": "agriculture_commissioner",
}
PRESIDENTIAL_CANDIDATES = {
    1980: {"DEM": "Jimmy Carter", "REP": "Ronald Reagan"},
    1984: {"DEM": "Walter Mondale", "REP": "Ronald Reagan"},
    1988: {"DEM": "Michael Dukakis", "REP": "George H. W. Bush"},
    1992: {"DEM": "Bill Clinton", "REP": "George H. W. Bush"},
    1996: {"DEM": "Bill Clinton", "REP": "Bob Dole"},
    2000: {"DEM": "Al Gore", "REP": "George W. Bush"},
    2004: {"DEM": "John Kerry", "REP": "George W. Bush"},
    2008: {"DEM": "Barack Obama", "REP": "John McCain"},
    2012: {"DEM": "Barack Obama", "REP": "Mitt Romney"},
    2016: {"DEM": "Hillary Clinton", "REP": "Donald J. Trump"},
    2020: {"DEM": "Joe Biden", "REP": "Donald J. Trump"},
    2024: {"DEM": "Kamala D. Harris", "REP": "Donald J. Trump"},
}
CONTEST_CANDIDATES = {
    (2002, "governor"): {"DEM": "Tom Vilsack", "REP": "Doug Gross"},
    (2004, "us_senate"): {"DEM": "Arthur Small", "REP": "Chuck Grassley"},
    (2006, "governor"): {"DEM": "Chet Culver", "REP": "Jim Nussle"},
    (2010, "governor"): {"DEM": "Chet Culver", "REP": "Terry E. Branstad"},
    (2014, "governor"): {"DEM": "Jack Hatch", "REP": "Terry E. Branstad"},
    (2018, "governor"): {"DEM": "Fred Hubbell", "REP": "Kim Reynolds"},
    (2022, "governor"): {"DEM": "Deidre DeJear", "REP": "Kim Reynolds"},
}
SCOPES = {
    "congressional": ("congressional", 4),
    "house": ("state_house", 100),
    "senate": ("state_senate", 50),
}
NATIVE_DISTRICT_CONTESTS = {
    "U S HOUSE": ("congressional", "us_house"),
    "STATE HOUSE": ("state_house", "state_house"),
    "STATE SENATE": ("state_senate", "state_senate"),
}
NATIVE_DISTRICT_INPUTS = {
    2022: ROOT / "data/2022/20221108__ia__general__precinct.csv",
    2024: ROOT / "data/2024/20241105__ia__general__precinct.csv",
}
NATIVE_OFFICIAL_INPUTS = {
    # The precinct export omits some certified 2024 U.S. House votes.  The
    # accompanying county canvass has the final certified district totals.
    2024: ROOT / "data/2024/20241105__ia__general__county.csv",
}
NATIVE_DISTRICT_COUNTS = {
    2022: {"congressional": 4, "state_house": 100, "state_senate": 34},
    2024: {"congressional": 4, "state_house": 100, "state_senate": 25},
}
OFFICIAL_US_HOUSE_PARTIES_2024 = {
    "ASHLEY HINSON": "REP",
    "CHRISTINA BOHANNAN": "DEM",
    "LANON BACCAM": "DEM",
    "MARIANNETTE MILLER MEEKS": "REP",
    "RANDY FEENSTRA": "REP",
    "RYAN MELTON": "DEM",
    "SARAH CORKERY": "DEM",
    "ZACH NUNN": "REP",
}


def margin_color(margin_pct: float, winner: str) -> str:
    """Return the same categorical margin shade used by the atlas UI."""
    margin = abs(float(margin_pct))
    party = str(winner or "").strip().upper()
    if margin >= 40:
        return "#67000d" if party in {"R", "REP"} else "#08306b"
    if margin >= 30:
        return "#a50f15" if party in {"R", "REP"} else "#08519c"
    if margin >= 20:
        return "#cb181d" if party in {"R", "REP"} else "#2876b5"
    if margin >= 10:
        return "#e93a2d" if party in {"R", "REP"} else "#4795d2"
    if margin >= 5.5:
        return "#f7634b" if party in {"R", "REP"} else "#6baed6"
    if margin >= 1:
        return "#fca793" if party in {"R", "REP"} else "#8bbde0"
    if margin >= 0.5:
        return "#f4c9c5" if party in {"R", "REP"} else "#c7ddf0"
    return "#f7f7f7"


def round_preserving_sum(values: list[float]) -> list[int]:
    """Round nonnegative vote allocations while preserving their rounded sum."""
    clean_values = [max(0.0, float(value)) for value in values]
    rounded = [math.floor(value) for value in clean_values]
    target = math.floor(sum(clean_values) + 0.5)
    remainder = target - sum(rounded)
    order = sorted(
        range(len(clean_values)),
        key=lambda index: (clean_values[index] - rounded[index], -index),
        reverse=True,
    )
    for index in order[:remainder]:
        rounded[index] += 1
    return rounded


def integerize_results(results: dict[str, dict]) -> None:
    """Replace allocated vote fractions with whole votes and derived integers."""
    district_ids = sorted(results, key=int)
    for field in ("dem_votes", "rep_votes", "other_votes"):
        values = round_preserving_sum([results[district_id][field] for district_id in district_ids])
        for district_id, value in zip(district_ids, values):
            results[district_id][field] = value
    for district_id in district_ids:
        row = results[district_id]
        dem = row["dem_votes"]
        rep = row["rep_votes"]
        other = row["other_votes"]
        total = dem + rep + other
        winner = "DEM" if dem > rep else ("REP" if rep > dem else "TIE")
        row["total_votes"] = total
        row["margin"] = rep - dem
        row["margin_pct"] = ((rep - dem) / total * 100) if total else 0.0
        row["winner"] = winner
        row["color"] = margin_color(row["margin_pct"], winner)


def candidate_label(value: object, contest_type: str, year: int, party: str) -> str:
    if contest_type == "president" and party in {"DEM", "REP"}:
        normalized = PRESIDENTIAL_CANDIDATES.get(year, {}).get(party)
        if normalized:
            return normalized
    normalized = CONTEST_CANDIDATES.get((year, contest_type), {}).get(party)
    if normalized:
        return normalized
    # A handful of 2024 U.S. House source rows encode the party in the
    # candidate cell (for example, "Christina DEM") rather than `party`.
    label = re.sub(r"\s+(?:DEM(?:OCRAT(?:IC)?)?|REP(?:UBLICAN)?)\s*$", "", str(value or ""), flags=re.IGNORECASE).strip().title()
    if year == 2024 and contest_type == "us_house" and label == "Miller-Meeks":
        return "Mariannette Miller-Meeks"
    return label


def normalized_office(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def normalized_party(value: object, candidate: object) -> str:
    party = str(value or "").strip().upper()
    if party:
        return party
    suffix = re.search(r"\s+(DEM(?:OCRAT(?:IC)?)?|REP(?:UBLICAN)?)\s*$", str(candidate or ""), flags=re.IGNORECASE)
    if not suffix:
        return ""
    return "DEM" if suffix.group(1).upper().startswith("DEM") else "REP"


def normalized_candidate(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def build_native_district_slices(source: Path, year: int, lines_year: int) -> dict[tuple[str, str], dict]:
    """Aggregate district-specific legislative and U.S. House votes by reported district."""
    grouped = defaultdict(
        lambda: {
            "dem_votes": 0.0,
            "rep_votes": 0.0,
            "other_votes": 0.0,
            "dem_candidates": defaultdict(float),
            "rep_candidates": defaultdict(float),
        }
    )
    with source.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            contest = NATIVE_DISTRICT_CONTESTS.get(normalized_office(row.get("office")))
            if not contest:
                continue
            scope, contest_type = contest
            # Use the certified county canvass for this one race below.
            if contest_type == "us_house" and year in NATIVE_OFFICIAL_INPUTS:
                continue
            district_raw = str(row.get("district") or "").strip()
            if not district_raw:
                continue
            district = str(int(district_raw))
            bucket = grouped[(scope, contest_type, district)]
            party = normalized_party(row.get("party"), row.get("candidate"))
            votes = float(row.get("votes") or 0)
            candidate = candidate_label(row.get("candidate"), contest_type, year, party)
            if party == "DEM" or party.startswith("DEMOCRAT"):
                bucket["dem_votes"] += votes
                bucket["dem_candidates"][candidate] += votes
            elif party == "REP" or party.startswith("REPUBLICAN"):
                bucket["rep_votes"] += votes
                bucket["rep_candidates"][candidate] += votes
            else:
                bucket["other_votes"] += votes

    official_source = NATIVE_OFFICIAL_INPUTS.get(year)
    if official_source:
        with official_source.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                contest = NATIVE_DISTRICT_CONTESTS.get(normalized_office(row.get("office")))
                if contest != ("congressional", "us_house"):
                    continue
                district_raw = str(row.get("district") or "").strip()
                if not district_raw:
                    continue
                district = str(int(district_raw))
                candidate_raw = row.get("candidate")
                party = OFFICIAL_US_HOUSE_PARTIES_2024.get(normalized_candidate(candidate_raw), "")
                votes = float(row.get("votes") or 0)
                bucket = grouped[("congressional", "us_house", district)]
                candidate = candidate_label(candidate_raw, "us_house", year, party)
                if party == "DEM":
                    bucket["dem_votes"] += votes
                    bucket["dem_candidates"][candidate] += votes
                elif party == "REP":
                    bucket["rep_votes"] += votes
                    bucket["rep_candidates"][candidate] += votes
                else:
                    bucket["other_votes"] += votes

    results_by_contest = defaultdict(dict)
    for (scope, contest_type, district), bucket in grouped.items():
        dem_candidates = bucket.pop("dem_candidates")
        rep_candidates = bucket.pop("rep_candidates")
        bucket["dem_candidate"] = max(dem_candidates, key=dem_candidates.get, default="")
        bucket["rep_candidate"] = max(rep_candidates, key=rep_candidates.get, default="")
        results_by_contest[(scope, contest_type)][district] = bucket

    slices = {}
    for (scope, contest_type), results in results_by_contest.items():
        integerize_results(results)
        result_source = official_source if contest_type == "us_house" and official_source else source
        slices[(scope, contest_type)] = {
            "meta": {
                "state": "IA",
                "year": year,
                "lines_year": lines_year,
                "scope": scope,
                "contest_type": contest_type,
                "match_coverage_pct": 100.0,
                "vote_coverage_pct": 100.0,
                "allocation_method": "direct_precinct_vote_aggregation_by_reported_district",
                "geographic_precision": "native_district_results",
                "target_plan": "Iowa Plan 2 enacted in 2021",
                "source": result_source.relative_to(ROOT).as_posix(),
                "source_scope": scope,
            },
            "general": {"results": dict(sorted(results.items(), key=lambda item: int(item[0])))},
        }
    return slices


def build_scope_slices(
    source: Path,
    source_scope: str,
    frontend_scope: str,
    year: int,
    lines_year: int,
    coverage_pct: float,
    source_metadata: dict | None = None,
) -> dict[str, dict]:
    source_metadata = source_metadata or {}
    chamber_metadata = source_metadata.get("chambers", {}).get(source_scope, {})
    county_exact = source_metadata.get("geographic_precision") == "county_population_disaggregation" and chamber_metadata.get("split_counties") == 0
    allocation_method = (
        "verified_county_votes_to_iowa_plan2_without_county_splits"
        if county_exact
        else source_metadata.get("allocation_method", "population_weighted_precinct_to_vtd_to_plan2")
    )
    geographic_precision = (
        "county_exact_no_split"
        if county_exact
        else source_metadata.get("geographic_precision", "precinct_crosswalk")
    )
    grouped = defaultdict(
        lambda: {
            "dem_votes": 0.0,
            "rep_votes": 0.0,
            "other_votes": 0.0,
            "dem_candidate": "",
            "rep_candidate": "",
        }
    )
    with source.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            contest_type = OFFICE_TYPES.get(str(row.get("office") or "").strip().upper())
            if not contest_type:
                continue
            district = str(row.get("plan2_district") or "").strip()
            if not district:
                continue
            bucket = grouped[(contest_type, district)]
            party = normalized_party(row.get("party"), row.get("candidate"))
            votes = float(row.get("votes") or 0)
            if party == "DEM" or party.startswith("DEMOCRAT"):
                bucket["dem_votes"] += votes
                bucket["dem_candidate"] = candidate_label(row.get("candidate"), contest_type, year, "DEM")
            elif party == "REP" or party.startswith("REPUBLICAN"):
                bucket["rep_votes"] += votes
                bucket["rep_candidate"] = candidate_label(row.get("candidate"), contest_type, year, "REP")
            else:
                bucket["other_votes"] += votes

    by_contest = defaultdict(dict)
    for (contest_type, district), bucket in grouped.items():
        dem = bucket["dem_votes"]
        rep = bucket["rep_votes"]
        other = bucket["other_votes"]
        total = dem + rep + other
        signed_margin = ((rep - dem) / total * 100) if total else 0.0
        winner = "DEM" if dem > rep else ("REP" if rep > dem else "TIE")
        by_contest[contest_type][district] = {
            "dem_votes": dem,
            "rep_votes": rep,
            "other_votes": other,
            "total_votes": total,
            "dem_candidate": bucket["dem_candidate"],
            "rep_candidate": bucket["rep_candidate"],
            "margin": rep - dem,
            "margin_pct": signed_margin,
            "winner": winner,
            "color": margin_color(signed_margin, winner),
        }

    for results in by_contest.values():
        integerize_results(results)

    by_contest = {
        contest_type: results
        for contest_type, results in by_contest.items()
        if sum(row["dem_votes"] for row in results.values()) > 0
        and sum(row["rep_votes"] for row in results.values()) > 0
    }

    return {
        contest_type: {
            "meta": {
                "state": "IA",
                "year": year,
                "lines_year": lines_year,
                "scope": frontend_scope,
                "contest_type": contest_type,
                "match_coverage_pct": coverage_pct,
                "vote_coverage_pct": source_metadata.get("vote_coverage_pct", coverage_pct),
                "allocation_method": allocation_method,
                "geographic_precision": geographic_precision,
                "target_plan": source_metadata.get("target_plan", "Iowa Plan 2 enacted in 2021"),
                "source": source.relative_to(ROOT).as_posix(),
                "source_scope": source_scope,
            },
            "general": {"results": dict(sorted(results.items(), key=lambda item: int(item[0])))},
        }
        for contest_type, results in by_contest.items()
    }


def contest_total(payload: dict) -> tuple[float, float, float, float]:
    totals = [0.0, 0.0, 0.0, 0.0]
    for row in payload["general"]["results"].values():
        totals[0] += float(row["dem_votes"])
        totals[1] += float(row["rep_votes"])
        totals[2] += float(row["other_votes"])
        totals[3] += float(row["total_votes"])
    return tuple(totals)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, action="append")
    parser.add_argument("--lines-year", type=int, default=2022)
    parser.add_argument("--source-dir", type=Path, action="append")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data/district_contests",
    )
    parser.add_argument("--match-coverage-pct", type=float, default=100.0)
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove stale JSON slices not produced by this build.",
    )
    args = parser.parse_args()
    source_dirs = [
        path.resolve()
        for path in (args.source_dir or [ROOT / "data/aggregates_geojson_population_2020"])
    ]
    args.output_dir = args.output_dir.resolve()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    years = args.year or [2020]
    slices = {}
    contests_by_year = {}
    for year in years:
        for source_scope, (frontend_scope, expected_districts) in SCOPES.items():
            filename = f"{year}_contests_to_plan2_{source_scope}.csv"
            sources = [directory / filename for directory in source_dirs if (directory / filename).exists()]
            if not sources:
                raise FileNotFoundError(f"{filename} not found in: {source_dirs}")
            scope_slices = {}
            for source in sources:
                metadata_path = source.parent / "allocation_metadata.json"
                source_metadata = (
                    json.loads(metadata_path.read_text(encoding="utf-8"))
                    if metadata_path.exists()
                    else {}
                )
                year_metadata = source_metadata.get("year_metadata", {}).get(str(year), {})
                source_metadata = {**source_metadata, **year_metadata}
                source_coverage_pct = float(source_metadata.get("match_coverage_pct", args.match_coverage_pct))
                source_contests = build_scope_slices(
                    source,
                    source_scope,
                    frontend_scope,
                    year,
                    args.lines_year,
                    source_coverage_pct,
                    source_metadata,
                )
                for contest_type, payload in source_contests.items():
                    scope_slices.setdefault(contest_type, payload)
            for contest_type, payload in scope_slices.items():
                district_count = len(payload["general"]["results"])
                if district_count != expected_districts:
                    raise ValueError(
                        f"{year} {frontend_scope} {contest_type}: expected {expected_districts} districts, got {district_count}"
                    )
                slices[(year, frontend_scope, contest_type)] = payload

        contests = sorted({contest_type for slice_year, _, contest_type in slices if slice_year == year})
        contests_by_year[year] = contests
        for contest_type in contests:
            totals = {
                scope: contest_total(slices[(year, scope, contest_type)])
                for scope in ("congressional", "state_house", "state_senate")
            }
            baseline = totals["congressional"]
            for scope, total in totals.items():
                if any(abs(actual - expected) > 0.01 for actual, expected in zip(total, baseline)):
                    raise ValueError(f"{year} {contest_type}: {scope} totals do not match congressional totals")

        native_source = NATIVE_DISTRICT_INPUTS.get(year)
        if native_source:
            native_slices = build_native_district_slices(native_source, year, args.lines_year)
            for (scope, contest_type), payload in native_slices.items():
                district_count = len(payload["general"]["results"])
                expected_count = NATIVE_DISTRICT_COUNTS[year][scope]
                if district_count != expected_count:
                    raise ValueError(
                        f"{year} native {scope} {contest_type}: expected {expected_count} districts, got {district_count}"
                    )
                slices[(year, scope, contest_type)] = payload
            contests_by_year[year] = sorted(
                {contest_type for slice_year, _, contest_type in slices if slice_year == year}
            )

    # A targeted rebuild must retain catalog entries for years not being rebuilt.
    # (The JSON slices themselves are already left in place unless --clean is used.)
    manifest = []
    manifest_path = args.output_dir / "manifest.json"
    if args.year and manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8")).get("files", [])
        selected_years = set(years)
        manifest.extend(entry for entry in existing if entry.get("year") not in selected_years)
    for (year, scope, contest_type), payload in sorted(slices.items()):
        filename = f"{scope}_{contest_type}_{year}.json"
        output = args.output_dir / filename
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        district_count = len(payload["general"]["results"])
        manifest.append(
            {
                "year": year,
                "contest_type": contest_type,
                "file": filename,
                "rows": district_count,
                "districts": district_count,
                "scope": scope,
                "lines_year": args.lines_year,
                "match_coverage_pct": args.match_coverage_pct,
                "vote_coverage_pct": payload["meta"].get("vote_coverage_pct", args.match_coverage_pct),
                "allocation_method": payload["meta"].get("allocation_method", ""),
                "geographic_precision": payload["meta"].get("geographic_precision", ""),
                "target_plan": payload["meta"].get("target_plan", ""),
                "dem_total": int(contest_total(payload)[0]),
                "rep_total": int(contest_total(payload)[1]),
                "major_party_contested": True,
            }
        )

    if args.clean:
        expected_files = {entry["file"] for entry in manifest} | {"manifest.json"}
        for path in args.output_dir.glob("*.json"):
            if path.name not in expected_files:
                path.unlink()

    (args.output_dir / "manifest.json").write_text(
        json.dumps({"files": manifest}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        {
            "years": years,
            "lines_year": args.lines_year,
            "slices": len(manifest),
            "contests": contests_by_year,
            "source_dirs": [str(path) for path in source_dirs],
            "output": str(args.output_dir),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
