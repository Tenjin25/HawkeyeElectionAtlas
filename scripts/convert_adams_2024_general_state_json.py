from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUMMARY_JSON = ROOT / "tmp" / "adams_summary.json"
DETAILS_JSON = ROOT / "tmp" / "adams_details.json"
OUTPUT_CSV = ROOT / "data" / "2024" / "counties" / "20241105__ia__general__adams__precinct.csv"

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


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_candidate(name: str) -> str:
    return "Write-In" if name.strip().lower() == "write-in" else name.strip()


def normalize_office(raw_office: str) -> tuple[str, str]:
    office = raw_office.strip()

    if office == "President and Vice President":
        return "President", ""

    match = re.fullmatch(r"United States Representative District (\d+)", office)
    if match:
        return "U.S. House", match.group(1)

    match = re.fullmatch(r"State Representative District (\d+)", office)
    if match:
        return "State House", match.group(1)

    match = re.fullmatch(r"County Board of Supervisors District (\d+)", office)
    if match:
        return "County Supervisor", match.group(1)

    if office == "County Agricultural Extension Council":
        return "County Agricultural Extension Council Member", ""

    return office, ""


def build_rows() -> list[dict[str, str]]:
    summary = {contest["K"]: contest for contest in load_json(SUMMARY_JSON)}
    details = load_json(DETAILS_JSON)["Contests"]

    rows: list[dict[str, str]] = []

    for contest in details:
        contest_id = contest["K"]
        summary_contest = summary[contest_id]
        office, district = normalize_office(summary_contest["C"])
        candidates = [normalize_candidate(name) for name in summary_contest["CH"]]
        parties = summary_contest.get("P") or [""] * len(candidates)
        precincts = contest["P"]
        vote_matrix = contest["V"]

        for precinct, precinct_votes in zip(precincts, vote_matrix):
            if len(precinct_votes) != len(candidates):
                raise ValueError(
                    f"Contest {contest_id} has {len(precinct_votes)} precinct vote columns "
                    f"but {len(candidates)} candidates."
                )

            for candidate, party, votes in zip(candidates, parties, precinct_votes):
                rows.append(
                    {
                        "county": "Adams",
                        "precinct": precinct,
                        "office": office,
                        "district": district,
                        "candidate": candidate,
                        "party": party or "",
                        "votes": str(votes),
                        # The state JSON exposes precinct totals, but not election-day / absentee splits.
                        "absentee": "",
                        "election_day": "",
                    }
                )

    return rows


def verify(rows: list[dict[str, str]]) -> None:
    summary = {contest["K"]: contest for contest in load_json(SUMMARY_JSON)}
    details = load_json(DETAILS_JSON)["Contests"]

    totals_by_contest: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for contest in details:
        summary_contest = summary[contest["K"]]
        for candidate, precinct_votes in zip(summary_contest["CH"], zip(*contest["V"])):
            totals_by_contest[contest["K"]][normalize_candidate(candidate)] = sum(precinct_votes)

    for contest_id, candidate_totals in totals_by_contest.items():
        summary_contest = summary[contest_id]
        expected = {
            normalize_candidate(candidate): votes
            for candidate, votes in zip(summary_contest["CH"], summary_contest["V"])
        }
        if candidate_totals != expected:
            raise ValueError(
                f"Contest {contest_id} totals do not match summary.\n"
                f"Expected: {expected}\n"
                f"Actual:   {dict(candidate_totals)}"
            )


def write_csv(rows: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = build_rows()
    verify(rows)
    write_csv(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
