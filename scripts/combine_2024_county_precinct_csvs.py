from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COUNTY_DIR = ROOT / "data" / "2024" / "counties"
OUTPUT_PATH = ROOT / "data" / "2024" / "20241105__ia__general__precinct.csv"

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


def main() -> int:
    rows: list[dict[str, str]] = []

    for path in sorted(COUNTY_DIR.glob("20241105__ia__general__*__precinct.csv")):
        with path.open(encoding="utf-8", newline="") as file:
            for row in csv.DictReader(file):
                rows.append({field: row.get(field, "") for field in FIELDNAMES})

    rows.sort(
        key=lambda row: (
            row["county"],
            row["office"],
            row["district"],
            row["candidate"],
            row["precinct"],
        )
    )

    with OUTPUT_PATH.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
