#!/usr/bin/env python3
"""Download and normalize Iowa's unofficial 2026 general-election precinct feed."""

from __future__ import annotations

import argparse
import csv
import gzip
import io
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://electionresults.iowa.gov"
STATE_ELECTION_ID = "127077"
ELECTION_DATE = "20261103"
USER_AGENT = "Mozilla/5.0 (compatible; HawkeyeElectionAtlas/1.0)"
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


@dataclass
class CountySource:
    county: str
    slug: str
    election_id: str
    version: str
    report_url: str
    timestamp: str
    precincts: int
    rows: int


def fetch_bytes(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def fetch_text(url: str, timeout: int = 60) -> str:
    payload = fetch_bytes(url, timeout=timeout)
    if payload.startswith(b"\x1f\x8b"):
        payload = gzip.decompress(payload)
    return payload.decode("utf-8", errors="replace")


def county_filename_slug(name: str) -> str:
    value = name.lower().replace("'", "")
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def normalize_office(label: str) -> tuple[str, str]:
    office = label.strip()
    patterns = (
        (r"United States Representative District (\d+)", "U.S. House"),
        (r"State Representative District (\d+)", "State House"),
        (r"State Senator District (\d+)", "State Senate"),
        (r"County Board of Supervisors District (\d+)", "County Supervisor"),
    )
    for pattern, normalized in patterns:
        match = re.fullmatch(pattern, office)
        if match:
            return normalized, match.group(1)
    aliases = {
        "United States Senator": "U.S. Senate",
        "Governor and Lt. Governor": "Governor",
        "County Agricultural Extension Council": "County Agricultural Extension Council Member",
    }
    return aliases.get(office, office), ""


def parse_participating_counties(settings: dict) -> list[tuple[str, str, str]]:
    raw_entries = settings["settings"]["electiondetails"]["participatingcounties"]
    counties = []
    for raw in raw_entries:
        name, election_id, _version, *_ = raw.split("|")
        counties.append((name.replace("_", " "), name, election_id))
    if len(counties) != 99:
        raise ValueError(f"Expected 99 participating counties, got {len(counties)}")
    return counties


def parse_county_xml(county: str, payload: bytes) -> tuple[list[dict[str, str]], str, int]:
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        if len(xml_names) != 1:
            raise ValueError(f"{county}: expected one XML report, got {xml_names}")
        root = ET.fromstring(archive.read(xml_names[0]))

    if root.findtext("ElectionDate") != "11/3/2026":
        raise ValueError(f"{county}: unexpected election date {root.findtext('ElectionDate')!r}")
    timestamp = root.findtext("Timestamp") or ""
    turnout_precincts = root.findall("./VoterTurnout/Precincts/Precinct")
    rows: list[dict[str, str]] = []

    for contest in root.findall("Contest"):
        office, district = normalize_office(contest.get("text", ""))
        for choice in contest.findall("Choice"):
            candidate = choice.get("text", "").strip()
            normalized_candidate = "Write-In" if candidate.lower() == "write-in" else candidate
            party = choice.get("party", "").strip()
            by_mode: dict[str, dict[str, int]] = {}
            for vote_type in choice.findall("VoteType"):
                mode = vote_type.get("name", "").strip()
                by_mode[mode] = {
                    precinct.get("name", "").strip(): int(precinct.get("votes", "0"))
                    for precinct in vote_type.findall("Precinct")
                }
            precinct_names = sorted({name for values in by_mode.values() for name in values})
            choice_total = 0
            for precinct in precinct_names:
                election_day = by_mode.get("Election Day", {}).get(precinct, 0)
                absentee = by_mode.get("Absentee", {}).get(precinct, 0)
                other = sum(
                    values.get(precinct, 0)
                    for mode, values in by_mode.items()
                    if mode not in {"Election Day", "Absentee"}
                )
                votes = election_day + absentee + other
                choice_total += votes
                rows.append(
                    {
                        "county": county,
                        "precinct": precinct,
                        "office": office,
                        "district": district,
                        "candidate": normalized_candidate,
                        "party": party,
                        "votes": str(votes),
                        "absentee": str(absentee),
                        "election_day": str(election_day + other),
                    }
                )

            reported_total = int(choice.get("totalVotes", "0"))
            if choice_total != reported_total:
                raise ValueError(
                    f"{county} / {office} / {candidate}: precinct sum {choice_total} != {reported_total}"
                )

    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))
    return rows, timestamp, len(turnout_precincts)


def write_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/2026")
    args = parser.parse_args()

    state_root = f"{BASE_URL}/IA/{STATE_ELECTION_ID}"
    state_version = fetch_text(f"{state_root}/current_ver.txt").strip()
    settings_url = f"{state_root}/{state_version}/json/en/electionsettings.json"
    settings = json.loads(fetch_text(settings_url))
    counties = parse_participating_counties(settings)

    all_rows: list[dict[str, str]] = []
    sources: list[CountySource] = []
    county_dir = args.output_dir / "counties"
    for county, slug, election_id in counties:
        county_root = f"{BASE_URL}/IA/{slug}/{election_id}"
        version = fetch_text(f"{county_root}/current_ver.txt").strip()
        report_url = f"{county_root}/{version}/reports/detailxml.zip"
        rows, timestamp, precincts = parse_county_xml(county, fetch_bytes(report_url))
        output = county_dir / f"{ELECTION_DATE}__ia__general__{county_filename_slug(county)}__precinct.csv"
        write_csv(output, rows)
        all_rows.extend(rows)
        sources.append(
            CountySource(
                county=county,
                slug=slug,
                election_id=election_id,
                version=version,
                report_url=report_url,
                timestamp=timestamp,
                precincts=precincts,
                rows=len(rows),
            )
        )
        print(f"{county}: {precincts} precincts, {len(rows)} rows")

    combined = args.output_dir / f"{ELECTION_DATE}__ia__general__precinct.csv"
    all_rows.sort(key=lambda row: (row["county"], row["office"], row["district"], row["candidate"], row["precinct"]))
    write_csv(combined, all_rows)
    manifest = {
        "election": "2026 General Election",
        "election_date": "2026-11-03",
        "status": "unofficial",
        "state_election_id": STATE_ELECTION_ID,
        "state_version": state_version,
        "state_settings_url": settings_url,
        "state_updated_at": settings.get("websiteupdatedat", ""),
        "precincts_reporting": settings.get("stateprecinctsreporting"),
        "precincts_participating": settings.get("stateprecinctsparticipating"),
        "counties": [asdict(source) for source in sources],
        "combined_output": combined.relative_to(ROOT).as_posix(),
        "combined_rows": len(all_rows),
    }
    (args.output_dir / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    print({"state_version": state_version, "counties": len(sources), "rows": len(all_rows), "output": str(combined)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
