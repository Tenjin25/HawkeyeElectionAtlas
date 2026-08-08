#!/usr/bin/env python3
"""Convert Iowa aggregate CSVs into the browser atlas contest-slice format."""

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
    2024: {"DEM": "Kamala Harris", "REP": "Donald J. Trump"},
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

IOWA_COUNTY_ALIASES = {
    "OBREIN": "O'BRIEN",
    "OBRIEN": "O'BRIEN",
    "VAN": "VAN BUREN",
    "VANBUREN": "VAN BUREN",
}


def normalize_county_label(value: object) -> str:
    """Return an official Iowa county label for known source variants."""
    raw = re.sub(r"\s+COUNTY$", "", str(value or "").strip(), flags=re.IGNORECASE)
    compact = re.sub(r"[^A-Z0-9]", "", raw.upper())
    return IOWA_COUNTY_ALIASES.get(compact, raw.upper())


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


def candidate_label(value: object, contest_type: str, year: int, party: str) -> str:
    if contest_type == "president" and party in {"DEM", "REP"}:
        normalized_party = "DEM" if party == "DEM" else "REP"
        normalized = PRESIDENTIAL_CANDIDATES.get(year, {}).get(normalized_party)
        if normalized:
            return normalized
    normalized = CONTEST_CANDIDATES.get((year, contest_type), {}).get(party)
    if normalized:
        return normalized
    return str(value or "").strip().title()


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


def integerize_contest_rows(rows: list[dict]) -> None:
    """Replace allocated vote fractions with whole votes and derived integers."""
    rows.sort(key=lambda row: row["county"])
    for field in ("dem_votes", "rep_votes", "other_votes"):
        for row, value in zip(rows, round_preserving_sum([row[field] for row in rows])):
            row[field] = value
    for row in rows:
        dem = row["dem_votes"]
        rep = row["rep_votes"]
        other = row["other_votes"]
        total = dem + rep + other
        row["total_votes"] = total
        row["margin"] = rep - dem
        row["margin_pct"] = ((rep - dem) / total * 100) if total else 0
        row["winner"] = row["dem_candidate"] if dem >= rep else row["rep_candidate"]
        winner_party = "DEM" if dem > rep else ("REP" if rep > dem else "TIE")
        row["color"] = margin_color(row["margin_pct"], winner_party)


def load_counties():
    out = {}
    path = ROOT / "data/2000/20001107__ia__general__precinct.csv"
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            # This source numbers Iowa's 99 counties alphabetically from 1 to
            # 99. Iowa's three-digit county FIPS codes follow the same order,
            # using the odd numbers 001 through 197.
            county_num = str(row.get("county_num") or "").strip()
            if county_num.isdigit():
                fips = f"{2 * int(county_num) - 1:03d}"
                out[fips] = row.get("county", "").strip()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=ROOT / "data/aggregates")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/iowa_contests")
    parser.add_argument("--year", type=int, action="append")
    args = parser.parse_args()

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    counties = load_counties()
    manifest = []
    selected_years = set(args.year or [])
    sources = {int(path.name[:4]): path for path in args.source_dir.glob("*_contests_to_vtd20.csv")}
    # County aggregates come directly from official result rows and therefore
    # remain complete even when some precinct geometry cannot be matched.
    sources.update({int(path.name[:4]): path for path in args.source_dir.glob("*_contests_to_county.csv")})
    for year, source in sorted(sources.items()):
        if selected_years and year not in selected_years:
            continue
        county_source = source.name.endswith("_contests_to_county.csv")
        grouped = defaultdict(lambda: {"dem": 0.0, "rep": 0.0, "other": 0.0, "dem_candidate": "", "rep_candidate": ""})
        with source.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                contest_type = OFFICE_TYPES.get((row.get("office") or "").strip().upper())
                if not contest_type:
                    continue
                county = normalize_county_label(
                    (row.get("county") or "").strip().upper()
                    if county_source
                    else counties.get((row.get("vtd_id") or "")[2:5], (row.get("vtd_id") or "")[:5])
                )
                key = (contest_type, row.get("district", "").strip(), county)
                party = (row.get("party") or "").strip().upper()
                party_bucket = "DEM" if party == "DEM" or party.startswith("DEMOCRAT") else ("REP" if party == "REP" or party.startswith("REPUBLICAN") else party)
                candidate = candidate_label(row.get("candidate"), contest_type, year, party_bucket)
                votes = float(row.get("votes") or 0)
                bucket = grouped[key]
                if party == "DEM" or party.startswith("DEMOCRAT"):
                    bucket["dem"] += votes
                    bucket["dem_candidate"] = candidate
                elif party == "REP" or party.startswith("REPUBLICAN"):
                    bucket["rep"] += votes
                    bucket["rep_candidate"] = candidate
                else:
                    bucket["other"] += votes
        by_contest = defaultdict(list)
        for (contest_type, district, county), totals in grouped.items():
            total = totals["dem"] + totals["rep"] + totals["other"]
            winner = totals["dem_candidate"] if totals["dem"] >= totals["rep"] else totals["rep_candidate"]
            by_contest[(contest_type, district)].append({
                "county": county,
                "dem_votes": totals["dem"],
                "rep_votes": totals["rep"],
                "other_votes": totals["other"],
                "total_votes": total,
                "dem_candidate": totals["dem_candidate"],
                "rep_candidate": totals["rep_candidate"],
                "margin": totals["rep"] - totals["dem"],
                "margin_pct": ((totals["rep"] - totals["dem"]) / total * 100) if total else 0,
                "winner": winner,
                "color": margin_color(
                    ((totals["rep"] - totals["dem"]) / total * 100) if total else 0,
                    "DEM" if totals["dem"] > totals["rep"] else ("REP" if totals["rep"] > totals["dem"] else "TIE"),
                ),
            })
        for (contest_type, district), rows in by_contest.items():
            if len(rows) != 99:
                continue
            integerize_contest_rows(rows)
            dem_total = sum(row["dem_votes"] for row in rows)
            rep_total = sum(row["rep_votes"] for row in rows)
            if dem_total <= 0 or rep_total <= 0:
                continue
            filename = f"{contest_type}_{year}.json"
            (output_dir / filename).write_text(
                json.dumps({"rows": rows}, indent=2) + "\n",
                encoding="utf-8",
            )
            manifest.append({
                "year": year,
                "contest_type": contest_type,
                "file": filename,
                "rows": len(rows),
                "scope": "",
                "dem_total": dem_total,
                "rep_total": rep_total,
                "major_party_contested": True,
            })
    if not selected_years:
        expected_files = {entry["file"] for entry in manifest} | {"manifest.json"}
        for path in output_dir.glob("*.json"):
            if path.name not in expected_files:
                path.unlink()
    (output_dir / "manifest.json").write_text(json.dumps({"files": manifest}, indent=2) + "\n", encoding="utf-8")
    print({"slices": len(manifest), "source": str(args.source_dir), "output": str(output_dir)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
