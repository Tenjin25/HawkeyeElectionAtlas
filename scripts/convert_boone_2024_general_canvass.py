import csv
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber


ROOT = Path(__file__).resolve().parent.parent
SOURCE_PDF = ROOT / "tmp" / "pdfs" / "boone_2024_general.pdf"
OUTPUT_CSV = ROOT / "data" / "2024" / "counties" / "20241105__ia__general__boone__precinct.csv"

HEADER_SKIP = {
    "BOONE COUNTY ELECTION CANVASS SUMMARY",
    "2024 General Election",
}
PARTIES = {"REP", "DEM", "LIB", "PSL", "SOC", "WTP"}
OFFICE_ALIASES = {
    "President and Vice President": ("President", ""),
    "United States Representative District 4": ("U.S. House", "4"),
    "State Representative District 48": ("State House", "48"),
    "Supreme Court Justice - David May": ("Retain Supreme Court Justice David May", ""),
    "Court of Appeals - Samuel Langholz": ("Retain Court of Appeals Judge Samuel Langholz", ""),
    "Court of Appeals - Mary Ellen Tabor": ("Retain Court of Appeals Judge Mary Ellen Tabor", ""),
    "Court of Appeals - Tyler Buller": ("Retain Court of Appeals Judge Tyler J. Buller", ""),
    "Court of Appeals - Mary Chicchelly": ("Retain Court of Appeals Judge Mary Elizabeth Chicchelly", ""),
    "District 2B Judge - Derek Johnson": ("Retain District 2B Court Judge Derek Johnson", ""),
    "District 2B Associate Judge - Jon Mahoney": ("Retain District 2B Associate Judge Jon Mahoney", ""),
    "District 2B Associate Judge - Ashley Beisch": ("Retain District 2B Associate Judge Ashley Beisch", ""),
}


def normalize_office(raw: str) -> tuple[str, str]:
    title = " ".join(raw.split())
    if title in OFFICE_ALIASES:
        return OFFICE_ALIASES[title]
    if title.startswith("Constitutional Amendment "):
        return title, ""
    return title, ""


def normalize_candidate(name: str, office: str) -> str:
    candidate = " ".join(
        name.replace("Write-in", "Write-In").replace("Write- in", "Write-In").split()
    )
    if office == "President":
        mapping = {
            "Donald J. Trump and JD Vance": "Donald J. Trump and JD Vance",
            "Kamala D. Harris and Tim Walz": "Kamala D. Harris and Tim Walz",
            "Chase Oliver and Mike ter Maat": "Chase Oliver and Mike ter Maat",
            "Claudia De la Cruz and Karina Garcia": "Claudia De la Cruz and Karina Garcia",
            "William P. Stodden and Stephanie H. Cholensky": "William P. Stodden and Stephanie H. Cholensky",
            "Robert F. Kennedy Jr and Nicole Shanahan": "Robert F. Kennedy Jr and Nicole Shanahan",
            "Shiva Ayyadurai and Crystal Ellis": "Shiva Ayyadurai and Crystal Ellis",
        }
        return mapping.get(candidate, candidate)
    return candidate


def round_top(value: float) -> float:
    return round(value, 1)


def group_rows(words: list[dict]) -> list[list[dict]]:
    rows = defaultdict(list)
    for word in words:
        rows[round_top(word["top"])].append(word)
    grouped = []
    for top in sorted(rows):
        grouped.append(sorted(rows[top], key=lambda item: item["x0"]))
    return grouped


def combine_row_text(row: list[dict]) -> str:
    return " ".join(word["text"] for word in row)


def detect_office(page_rows: list[list[dict]]) -> str | None:
    for row in page_rows:
        text = combine_row_text(row)
        top = row[0]["top"] if row else 0
        if text in HEADER_SKIP or text.startswith("Page "):
            continue
        tokens = text.split()
        if (
            90 <= top <= 110
            and text
            and not any(token in text for token in ("Write-in", "Undervotes", "Overvotes", "Total", ","))
            and not all(token in PARTIES for token in tokens)
            and len(row_to_values(row)) <= 1
        ):
            return text
    return None


def find_first_data_row(page_rows: list[list[dict]]) -> list[dict] | None:
    for row in page_rows:
        text = combine_row_text(row)
        if text.startswith(("Absentee", "Total", "Page ")):
            continue
        if len(row_to_values(row)) >= 4:
            return row
    return None


def extract_columns(page_rows: list[list[dict]], first_data_row: list[dict], office_top: float | None) -> list[dict]:
    anchors = sorted(word["x0"] for word in first_data_row if re.fullmatch(r"[\d,]+", word["text"]))
    if len(anchors) < 4:
        return []
    candidate_anchor_count = len(anchors) - 3
    header_top_min = (office_top + 20) if office_top is not None else 140
    header_top_max = first_data_row[0]["top"] - 8
    header_words = [
        word
        for row in page_rows
        for word in row
        if header_top_min <= word["top"] <= header_top_max and word["x0"] >= anchors[0] - 40 and word["x0"] <= anchors[-1] + 25
    ]

    grouped = defaultdict(list)
    for word in header_words:
        nearest_anchor = min(anchors, key=lambda anchor: abs(word["x0"] - anchor))
        grouped[nearest_anchor].append(word)

    parsed = []
    for index, anchor in enumerate(anchors):
        role = "candidate"
        if index == len(anchors) - 3:
            role = "undervotes"
        elif index == len(anchors) - 2:
            role = "overvotes"
        elif index == len(anchors) - 1:
            role = "total"

        words = sorted(grouped.get(anchor, []), key=lambda item: (item["top"], item["x0"]))
        text = " ".join(word["text"] for word in words).strip()
        if role != "candidate":
            parsed.append({"x0": anchor, "text": text, "role": role, "candidate": "", "party": ""})
            continue

        tokens = text.replace(",", " ,").split()
        party = ""
        if tokens and tokens[-1] in PARTIES:
            party = tokens.pop()
        name = " ".join(tokens).replace(" ,", ",").strip().rstrip(",")
        if name == "Write-in":
            name = "Write-In"
            party = ""
        parsed.append({"x0": anchor, "text": text, "role": "candidate", "candidate": name, "party": party})

    return parsed[:candidate_anchor_count] + parsed[candidate_anchor_count:]


def row_to_values(row: list[dict]) -> list[str]:
    return [word["text"] for word in row if re.fullmatch(r"[\d,]+", word["text"])]


def clean_page_text(text: str) -> str:
    return (
        text.replace("WardElection", "Ward Election")
        .replace("ElectionDay", "Election Day")
        .replace("DayAbsentee", "Day Absentee")
        .strip()
    )


def extract_precinct_from_tokens(tokens: list[str]) -> str:
    filtered = [token for token in tokens if token not in {"Election", "Day"}]
    return " ".join(filtered).strip()


def find_data_start_top(page_rows: list[list[dict]]) -> float:
    for row in page_rows:
        top = row[0]["top"]
        if top < 120:
            continue
        if row_to_values(row):
            return max(0, top - 20)
    return 0


def parse_page(
    pdf_page,
    current_context: tuple[str, str] | None,
    current_columns: list[dict] | None,
    pending_label_tokens: list[str],
    pending_numbers: list[str] | None,
) -> tuple[tuple[str, str] | None, list[dict] | None, list[dict], list[str], list[str] | None]:
    words = pdf_page.extract_words(use_text_flow=True)
    page_rows = group_rows(words)
    office_title = detect_office(page_rows)
    context = current_context
    office_top = None
    if office_title is not None:
        office_top = next(row[0]["top"] for row in page_rows if combine_row_text(row) == office_title)
        context = normalize_office(office_title)
        current_columns = None
        pending_label_tokens = []
        pending_numbers = None
    if context is None:
        return None, current_columns, [], pending_label_tokens, pending_numbers
    office, district = context

    columns = current_columns
    if columns is None:
        first_data_row = find_first_data_row(page_rows)
        if first_data_row is None:
            return context, current_columns, [], pending_label_tokens, pending_numbers
        columns = extract_columns(page_rows, first_data_row, office_top)
        current_columns = columns
    candidate_columns = [column for column in columns if column["role"] == "candidate"]
    if not candidate_columns:
        return context, current_columns, [], pending_label_tokens, pending_numbers

    records = []
    current_precinct = None
    data_start_top = find_data_start_top(page_rows)
    in_summary_totals = False
    for row in page_rows:
        text = clean_page_text(combine_row_text(row))
        top = row[0]["top"] if row else 0
        if text in HEADER_SKIP or text.startswith("Page "):
            continue
        if top < data_start_top:
            continue
        values = row_to_values(row)
        token_texts = [word["text"] for word in row]

        if text.startswith("Total Election Day") or text.startswith("Total Absentee"):
            in_summary_totals = True
            pending_label_tokens = []
            pending_numbers = None
            continue
        if in_summary_totals and text.startswith(("Absentee", "Total")):
            continue

        direct_match = re.match(r"^(?P<precinct>.+?)\s*Election Day\s+(?P<numbers>(?:[\d,]+\s+)+[\d,]+)$", text)
        if direct_match:
            in_summary_totals = False
            current_precinct = direct_match.group("precinct").strip()
            values = direct_match.group("numbers").split()
            row_type = "Election Day"
        elif text.startswith("Absentee"):
            if current_precinct is None:
                continue
            in_summary_totals = False
            row_type = "Absentee"
        elif text.startswith("Total"):
            if pending_label_tokens == ["Election"] or pending_label_tokens == ["Total", "Election"]:
                in_summary_totals = True
            pending_label_tokens = []
            pending_numbers = None
            continue
        else:
            non_numbers = [token for token in token_texts if not re.fullmatch(r"[\d,]+", token)]
            if pending_numbers and "Day" in token_texts and len(values) < len(columns):
                pending_label_tokens.extend(token_texts)
                precinct = extract_precinct_from_tokens(pending_label_tokens)
                if precinct:
                    current_precinct = precinct
                    values = pending_numbers
                    row_type = "Election Day"
                    pending_label_tokens = []
                    pending_numbers = None
                    in_summary_totals = False
                else:
                    continue
            elif values and non_numbers:
                pending_label_tokens.extend(non_numbers)
                pending_numbers = values
                continue
            elif values and pending_label_tokens:
                pending_numbers = values
                continue
            elif non_numbers:
                pending_label_tokens.extend(non_numbers)
                if pending_numbers and "Day" in non_numbers:
                    precinct = extract_precinct_from_tokens(pending_label_tokens)
                    if precinct:
                        current_precinct = precinct
                        values = pending_numbers
                        row_type = "Election Day"
                        pending_label_tokens = []
                        pending_numbers = None
                        in_summary_totals = False
                    else:
                        continue
                else:
                    continue
            else:
                continue

        if len(values) < len(columns):
            continue

        candidate_values = values[: len(candidate_columns)]
        for column, value in zip(candidate_columns, candidate_values):
            votes = int(value.replace(",", ""))
            records.append(
                {
                    "county": "Boone",
                    "precinct": current_precinct,
                    "office": office,
                    "district": district,
                    "candidate": normalize_candidate(column["candidate"], office),
                    "party": column["party"],
                    "votes": votes,
                    "absentee": votes if row_type == "Absentee" else 0,
                    "election_day": votes if row_type == "Election Day" else 0,
                }
            )
    return context, current_columns, records, pending_label_tokens, pending_numbers


def validate(rows: list[dict]) -> list[str]:
    issues = []
    totals = defaultdict(int)
    for row in rows:
        key = (row["office"], row["district"], row["candidate"])
        totals[key] += row["votes"]

    checks = {
        ("President", "", "Donald J. Trump and JD Vance"): 9199,
        ("President", "", "Kamala D. Harris and Tim Walz"): 5895,
        ("President", "", "Write-In"): 57,
        ("U.S. House", "4", "Randy Feenstra"): 9004,
        ("U.S. House", "4", "Ryan Melton"): 5636,
    }
    for key, expected in checks.items():
        actual = totals.get(key)
        if actual != expected:
            issues.append(f"Mismatch for {key}: parsed {actual}, expected {expected}")
    return issues


def main() -> int:
    rows = []
    current_context = None
    current_columns = None
    pending_label_tokens: list[str] = []
    pending_numbers: list[str] | None = None
    with pdfplumber.open(SOURCE_PDF) as pdf:
        for page in pdf.pages:
            current_context, current_columns, page_rows, pending_label_tokens, pending_numbers = parse_page(
                page,
                current_context,
                current_columns,
                pending_label_tokens,
                pending_numbers,
            )
            rows.extend(page_rows)

    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "county",
                "precinct",
                "office",
                "district",
                "candidate",
                "party",
                "votes",
                "absentee",
                "election_day",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    issues = validate(rows)
    for issue in issues:
        print(f"WARNING: {issue}")
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
