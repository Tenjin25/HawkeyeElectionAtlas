from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parents[1]
SOURCE_PDF = ROOT / "tmp" / "pdfs" / "candidate_2024_general" / "floyd_2024_general.pdf"
OUTPUT_CSV = ROOT / "data" / "2024" / "counties" / "20241105__ia__general__floyd__precinct.csv"

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

HEADER_MARKERS = {
    "Election Day",
    "Absentee",
    "Total",
}


def clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def parse_int(value: str | None) -> int:
    text = clean(value)
    return int(text.replace(",", "")) if text else 0


def normalize_candidate(cell: str) -> tuple[str, str]:
    text = clean(cell.replace("- ", "-"))
    if text in {"Yes", "No", "Write-in", "Jody Madlom Puffett"}:
        return text, ""

    match = re.match(r"^(.*?),\s*([A-Z]{2,4})$", text)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    return text, ""


def normalize_office(raw_office: str) -> tuple[str, str]:
    office = clean(raw_office)

    if office == "President and Vice President":
        return "President", ""

    match = re.fullmatch(r"United States Representative District (\d+)", office)
    if match:
        return "U.S. House", match.group(1)

    match = re.fullmatch(r"State Representative District (\d+)", office)
    if match:
        return "State House", match.group(1)

    match = re.fullmatch(r"State Senator District (\d+)", office)
    if match:
        return "State Senate", match.group(1)

    match = re.fullmatch(r"County Board of Supervisors District (\d+)(.*)", office)
    if match:
        suffix = clean(match.group(2))
        label = "County Supervisor"
        if suffix:
            label = f"{label}{suffix}"
        return label, match.group(1)

    if office == "County Agricultural Extension Council":
        return "County Agricultural Extension Council Member", ""

    if office == "County Agricultural Extension Council To Fill a Vacancy":
        return "County Agricultural Extension Council Member To Fill a Vacancy", ""

    return office, ""


def extract_office(page: pdfplumber.page.Page, current_office: str | None) -> str | None:
    text = page.extract_text() or ""
    lines = [clean(line) for line in text.splitlines() if clean(line)]
    if "2024 General Election" in lines:
        idx = lines.index("2024 General Election")
        if idx + 1 < len(lines):
            return lines[idx + 1]
    return current_office


def build_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_office: str | None = None
    current_precinct = ""
    mode_votes: dict[str, dict[str, int]] = {}
    totals_by_office: dict[tuple[str, str, str], list[int]] = defaultdict(list)

    with pdfplumber.open(SOURCE_PDF) as pdf:
        for page in pdf.pages[1:]:
            prior_office = current_office
            current_office = extract_office(page, current_office)
            if not current_office:
                continue
            if current_office != prior_office:
                current_precinct = ""
                mode_votes = {}

            tables = page.extract_tables()
            if not tables:
                continue

            table = tables[0]
            if not table or len(table) < 2:
                continue

            header = [clean(cell) for cell in table[0]]
            try:
                undervotes_idx = header.index("Undervotes")
            except ValueError as exc:
                raise ValueError(f"Could not locate candidate columns for office {current_office!r}.") from exc

            candidate_headers = header[2:undervotes_idx]
            candidates = [normalize_candidate(cell) for cell in candidate_headers]
            office, district = normalize_office(current_office)

            for raw_row in table[1:]:
                row = [clean(cell) for cell in raw_row]
                if not any(row):
                    continue

                first = row[0]
                second = row[1] if len(row) > 1 else ""

                if first == "Total":
                    continue

                if first and second in HEADER_MARKERS:
                    current_precinct = first
                elif not first and second in HEADER_MARKERS:
                    if not current_precinct:
                        raise ValueError(f"Encountered carried row without precinct in office {current_office!r}.")
                else:
                    continue

                mode = second
                candidate_values = row[2:undervotes_idx]
                mode_votes[mode] = {
                    candidate: parse_int(value)
                    for (candidate, _party), value in zip(candidates, candidate_values)
                }

                if mode != "Total":
                    continue

                for candidate, party in candidates:
                    absentee = mode_votes.get("Absentee", {}).get(candidate, 0)
                    election_day = mode_votes.get("Election Day", {}).get(candidate, 0)
                    total = mode_votes["Total"][candidate]
                    if absentee + election_day != total:
                        raise ValueError(
                            f"{current_office} / {current_precinct} / {candidate}: "
                            f"absentee {absentee} + election day {election_day} != total {total}"
                        )

                    rows.append(
                        {
                            "county": "Floyd",
                            "precinct": current_precinct,
                            "office": office,
                            "district": district,
                            "candidate": candidate,
                            "party": party,
                            "votes": str(total),
                            "absentee": str(absentee),
                            "election_day": str(election_day),
                        }
                    )
                    totals_by_office[(office, district, candidate)].append(total)

                mode_votes = {}

    return rows


def write_csv(rows: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))

    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = build_rows()
    write_csv(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
