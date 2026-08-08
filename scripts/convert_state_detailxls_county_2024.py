from __future__ import annotations

import csv
import gzip
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TMP_DIR = ROOT / "tmp" / "state_detailxls_2024"
OUTPUT_DIR = ROOT / "data" / "2024" / "counties"

NS = {"s": "urn:schemas-microsoft-com:office:spreadsheet"}
S_ATTR = "{urn:schemas-microsoft-com:office:spreadsheet}"

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "candidate",
    "party",
    "votes",
    "absentee",
    "election_day",
]

SKIP_WORKSHEETS = {"Table of Contents", "Registered Voters"}


def expand_row(row: ET.Element) -> dict[int, str]:
    values: dict[int, str] = {}
    column_index = 1
    for cell in row.findall("s:Cell", NS):
        explicit_index = cell.attrib.get(f"{S_ATTR}Index")
        merge_across = int(cell.attrib.get(f"{S_ATTR}MergeAcross", "0"))
        if explicit_index:
            column_index = int(explicit_index)
        data = cell.find("s:Data", NS)
        values[column_index] = data.text.strip() if data is not None and data.text else ""
        column_index += 1 + merge_across
    return values


def load_json(path: Path) -> dict:
    raw = path.read_bytes()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def load_workbook_rows(path: Path) -> list[tuple[str, list[dict[int, str]]]]:
    with zipfile.ZipFile(path) as archive:
        workbook_name = archive.namelist()[0]
        workbook_root = ET.parse(BytesIO(archive.read(workbook_name))).getroot()

    worksheets: list[tuple[str, list[dict[int, str]]]] = []
    for worksheet in workbook_root.findall("s:Worksheet", NS):
        name = worksheet.attrib.get(f"{S_ATTR}Name", "").strip()
        if name in SKIP_WORKSHEETS:
            continue

        table = worksheet.find("s:Table", NS)
        if table is None:
            continue

        rows = [expand_row(row) for row in table.findall("s:Row", NS)]
        if rows:
            worksheets.append((name, rows))

    return worksheets


def clean_title(raw_title: str) -> str:
    return re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", raw_title).strip()


def normalize_office(raw_office: str) -> tuple[str, str]:
    office = clean_title(raw_office)

    if office == "President and Vice President":
        return "President", ""

    match = re.fullmatch(r"United States Representative District (\d+)", office)
    if match:
        return "U.S. House", match.group(1)

    match = re.fullmatch(r"State Senator District (\d+)", office)
    if match:
        return "State Senate", match.group(1)

    match = re.fullmatch(r"State Representative District (\d+)", office)
    if match:
        return "State House", match.group(1)

    match = re.fullmatch(r"County Board of Supervisors District (\d+)", office)
    if match:
        return "County Supervisor", match.group(1)

    if office == "County Agricultural Extension Council":
        return "County Agricultural Extension Council Member", ""

    if office == "County Agricultural Extension Council To Fill a Vacancy":
        return "County Agricultural Extension Council Member To Fill a Vacancy", ""

    if office == "County Soil and Water Conservation District Commissioner":
        return "Soil and Water Conservation District Commissioner", ""

    return office, ""


def normalize_candidate(name: str) -> str:
    return "Write-In" if name.strip().lower() == "write-in" else name.strip()


def county_filename_slug(county: str) -> str:
    return county.lower().replace(" ", "_").replace("'", "")


def contest_party_lookup(summary: dict) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for contest in summary["Contests"]:
        parties = contest.get("P") or [""] * len(contest["CH"])
        lookup[clean_title(contest["C"])] = {
            normalize_candidate(candidate): (party or "")
            for candidate, party in zip(contest["CH"], parties)
        }
    return lookup


def build_rows(county: str, workbook_path: Path, summary_path: Path) -> list[dict[str, str]]:
    summary = load_json(summary_path)
    contests = summary["Contests"]
    worksheets = load_workbook_rows(workbook_path)

    if len(worksheets) != len(contests):
        raise ValueError(
            f"{county}: workbook has {len(worksheets)} contest sheets but summary has {len(contests)} contests."
        )

    parties_by_contest = contest_party_lookup(summary)
    rows: list[dict[str, str]] = []

    for contest, (sheet_name, sheet_rows) in zip(contests, worksheets):
        title = clean_title(sheet_rows[0].get(1, ""))
        summary_title = clean_title(contest["C"])
        if title != summary_title:
            raise ValueError(
                f"{county}: worksheet {sheet_name} title {title!r} does not match summary title {summary_title!r}."
            )

        candidate_cells = sheet_rows[1]
        candidate_names = [
            normalize_candidate(candidate_cells[column])
            for column in sorted(candidate_cells)
            if column >= 3 and candidate_cells[column]
        ]

        summary_candidates = [normalize_candidate(name) for name in contest["CH"]]
        if candidate_names != summary_candidates:
            raise ValueError(
                f"{county}: candidate order mismatch for {summary_title}.\n"
                f"Workbook: {candidate_names}\nSummary:  {summary_candidates}"
            )

        office, district = normalize_office(summary_title)
        parties = parties_by_contest[summary_title]

        for sheet_row in sheet_rows[3:]:
            precinct = sheet_row.get(1, "").strip()
            if not precinct or precinct == "Total:":
                continue

            for index, candidate in enumerate(candidate_names):
                start_col = 3 + (index * 3)
                election_day = sheet_row.get(start_col, "").strip()
                absentee = sheet_row.get(start_col + 1, "").strip()
                votes = sheet_row.get(start_col + 2, "").strip()

                if not votes:
                    continue

                rows.append(
                    {
                        "county": county,
                        "precinct": precinct,
                        "office": office,
                        "district": district,
                        "_contest_key": contest["K"],
                        "_raw_office": summary_title,
                        "candidate": candidate,
                        "party": parties.get(candidate, ""),
                        "votes": votes,
                        "absentee": absentee,
                        "election_day": election_day,
                    }
                )

    return rows


def verify(rows: list[dict[str, str]], summary_path: Path) -> list[str]:
    totals: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        total = int(row["votes"])
        absentee = int(row["absentee"] or "0")
        election_day = int(row["election_day"] or "0")
        if total != absentee + election_day:
            raise ValueError(
                f"Vote split mismatch for {row['office']} / {row['candidate']} / {row['precinct']}: "
                f"{total} != {absentee} + {election_day}"
            )

        contest_key = (row["_contest_key"], row["candidate"])
        totals[contest_key] += total

    summary = load_json(summary_path)
    warnings: list[str] = []
    for contest in summary["Contests"]:
        office, district = normalize_office(contest["C"])
        for candidate, votes in zip(contest["CH"], contest["V"]):
            normalized_candidate = normalize_candidate(candidate)
            contest_key = (contest["K"], normalized_candidate)
            actual_votes = totals.pop(contest_key, 0)
            if actual_votes != votes:
                warnings.append(
                    f"Summary mismatch for {office} {district} / {normalized_candidate}: "
                    f"expected {votes}, got {actual_votes}"
                )

    if totals:
        warnings.append(f"Unexpected extra contest totals remain: {sorted(totals)[:5]}")

    return warnings


def write_csv(rows: list[dict[str, str]], county: str) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"20241105__ia__general__{county_filename_slug(county)}__precinct.csv"
    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))

    with output_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows({field: row.get(field, "") for field in FIELDNAMES} for row in rows)

    return output_path


def convert_county(county_slug: str) -> Path:
    county = county_slug.strip().title()
    workbook_path = TMP_DIR / f"{county_slug.lower()}_detailxls.zip"
    summary_path = TMP_DIR / f"{county_slug.lower()}_sum.json"

    if not workbook_path.exists():
        raise FileNotFoundError(f"Missing workbook: {workbook_path}")
    if not summary_path.exists():
        raise FileNotFoundError(f"Missing summary JSON: {summary_path}")

    rows = build_rows(county, workbook_path, summary_path)
    warnings = verify(rows, summary_path)
    output_path = write_csv(rows, county)
    print(f"Wrote {len(rows)} rows to {output_path}")
    for warning in warnings[:10]:
        print(f"WARNING {county}: {warning}")
    if len(warnings) > 10:
        print(f"WARNING {county}: {len(warnings) - 10} additional summary mismatch warnings")
    return output_path


def main() -> int:
    counties = sys.argv[1:] or ["clarke", "fremont", "madison"]
    for county in counties:
        convert_county(county)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
