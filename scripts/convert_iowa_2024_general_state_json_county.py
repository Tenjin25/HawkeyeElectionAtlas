from __future__ import annotations

import argparse
import csv
import json
import re
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

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


def county_slug(name: str) -> str:
    slug = name.strip().lower()
    slug = slug.replace("'", "")
    slug = re.sub(r"[^a-z0-9]+", "_", slug)
    return slug.strip("_")


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


def build_rows(county: str, summary_path: Path, details_path: Path) -> list[dict[str, str]]:
    summary = {contest["K"]: contest for contest in load_json(summary_path)}
    details = load_json(details_path)["Contests"]

    rows: list[dict[str, str]] = []

    for contest in details:
        contest_id = contest["K"]
        summary_contest = summary.get(contest_id)
        if summary_contest is None:
            raise ValueError(f"Contest {contest_id} exists in details but not summary.")

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
                        "county": county,
                        "precinct": precinct,
                        "office": office,
                        "district": district,
                        "candidate": candidate,
                        "party": party or "",
                        "votes": str(votes),
                        # These state JSON bundles expose precinct totals only, not vote-mode splits.
                        "absentee": "0",
                        "election_day": str(votes),
                    }
                )

    return rows


def verify(summary_path: Path, details_path: Path) -> None:
    summary = {contest["K"]: contest for contest in load_json(summary_path)}
    details = load_json(details_path)["Contests"]

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


def output_path(county: str, date: str) -> Path:
    return ROOT / "data" / "2024" / "counties" / f"{date}__ia__general__{county_slug(county)}__precinct.csv"


def write_csv(rows: list[dict[str, str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))

    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--details", required=True)
    parser.add_argument("--date", default="20241105")
    args = parser.parse_args()

    summary_path = Path(args.summary)
    details_path = Path(args.details)
    out_path = output_path(args.county, args.date)

    rows = build_rows(args.county, summary_path, details_path)
    verify(summary_path, details_path)
    write_csv(rows, out_path)
    print(f"Wrote {len(rows)} rows to {out_path}")


if __name__ == "__main__":
    main()
