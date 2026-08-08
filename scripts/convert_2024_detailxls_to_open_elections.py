import csv
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SOURCE_ZIP = ROOT / "data" / "detailxls.zip"
OUTPUT_DIR = ROOT / "data" / "2024"
OUTPUT_CSV = OUTPUT_DIR / "20241105__ia__general__county.csv"

NS = {"s": "urn:schemas-microsoft-com:office:spreadsheet"}
S_ATTR = "{urn:schemas-microsoft-com:office:spreadsheet}"


def expand_row(row):
    values = {}
    column_index = 1
    for cell in row.findall("s:Cell", NS):
        explicit_index = cell.attrib.get(f"{S_ATTR}Index")
        if explicit_index:
            column_index = int(explicit_index)
        data = cell.find("s:Data", NS)
        values[column_index] = data.text if data is not None and data.text is not None else ""
        column_index += 1
    return values


def clean_title(raw_title):
    return re.sub(r"\s*\(\s*\d+\s*\)\s*$", "", raw_title).strip()


def normalize_office(raw_title):
    title = clean_title(raw_title)

    if title == "President and Vice President":
        return "President", ""

    match = re.match(r"United States Representative District (\d+)$", title)
    if match:
        return "U.S. House", match.group(1)

    match = re.match(r"State Senator District (\d+)$", title)
    if match:
        return "State Senate", match.group(1)

    match = re.match(r"State Representative District (\d+)$", title)
    if match:
        return "State House", match.group(1)

    return title, ""


def iter_contest_rows(workbook_root):
    worksheets = workbook_root.findall("s:Worksheet", NS)
    for worksheet in worksheets:
        name = worksheet.attrib.get(f"{S_ATTR}Name", "")
        if name in {"Table of Contents", "Registered Voters"}:
            continue

        table = worksheet.find("s:Table", NS)
        if table is None:
            continue

        rows = [expand_row(row) for row in table.findall("s:Row", NS)]
        if len(rows) < 4:
            continue

        title = rows[0].get(1, "").strip()
        if not title:
            continue

        candidate_header = rows[1]
        candidate_names = []
        candidate_col = 3
        while candidate_col in candidate_header:
            candidate_name = candidate_header.get(candidate_col, "").strip()
            if candidate_name:
                candidate_names.append(candidate_name)
            candidate_col += 1

        if not candidate_names:
            continue

        office, district = normalize_office(title)

        for row in rows[3:]:
            county = row.get(1, "").strip()
            if not county or county == "Total:":
                continue

            for index, candidate_name in enumerate(candidate_names):
                start_col = 3 + (index * 3)
                election_day = row.get(start_col, "").strip()
                absentee = row.get(start_col + 1, "").strip()
                total_votes = row.get(start_col + 2, "").strip()

                if not total_votes:
                    continue

                yield {
                    "office": office,
                    "district": district,
                    "candidate": candidate_name,
                    "party": "",
                    "county": county,
                    "votes": total_votes,
                    "absentee": absentee,
                    "election_day": election_day,
                }


def load_workbook_root():
    with zipfile.ZipFile(SOURCE_ZIP) as archive:
        with archive.open("detail.xls") as workbook_file:
            workbook_bytes = workbook_file.read()
    return ET.parse(BytesIO(workbook_bytes)).getroot()


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    workbook_root = load_workbook_root()
    rows = list(iter_contest_rows(workbook_root))

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as output_file:
        writer = csv.DictWriter(
            output_file,
            fieldnames=[
                "office",
                "district",
                "candidate",
                "party",
                "county",
                "votes",
                "absentee",
                "election_day",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
