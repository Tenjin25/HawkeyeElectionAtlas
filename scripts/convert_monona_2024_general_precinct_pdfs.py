import argparse
import csv
import re
import ssl
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pypdf import PdfReader


ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_CSV = ROOT / "data" / "2024_source_discovery" / "resolved_2024_precinct_pages.csv"
PDF_DIR = ROOT / "tmp" / "pdfs" / "monona_2024_general"
OUTPUT_CSV = ROOT / "data" / "2024" / "counties" / "20241105__ia__general__monona__precinct.csv"
SUMMARY_PDF = ROOT / "tmp" / "pdfs" / "monona_2024_general_all_precincts.pdf"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

REPORT_HEADER_PREFIXES = (
    "Precinct Summary Report",
    "Election Summary Report",
    "General Election",
    "Monona County, Iowa",
    "Tuesday, November 5, 2024",
    "GENERAL ELECTION 11-05-2024",
    "OFFICIAL RESULTS",
    "UNOFFICIAL RESULTS",
    "Date:",
    "Time:",
    "Page ",
    "Registered Voters ",
    "Number of Precincts ",
    "Precincts Reporting ",
)
SKIP_LINE_PREFIXES = REPORT_HEADER_PREFIXES + (
    "Vote For ",
    "Undervote",
    "Overvote",
)
TOTAL_VOTES_RE = re.compile(r"^[\d,]+Total Votes(?:\s+100\.00%)?$")
RESULT_RE = re.compile(
    r"^(?P<candidate>.+?)(?:\s+\((?P<party>[A-Z]+)\))?\s+(?P<votes>[\d,]+)\s+(?P<pct>\d+(?:\.\d+)?%)$"
)


def slugify_county(county: str) -> str:
    return county.lower().replace(" ", "_")


def normalize_candidate(name: str, office: str) -> str:
    candidate = " ".join(name.split())
    if candidate == "(Write-in vote, if any)":
        return "Write-In"
    if office == "President":
        mapping = {
            "Kamala D. Harris Tim Walz": "Kamala D. Harris and Tim Walz",
            "Donald J. Trump JD Vance": "Donald J. Trump and JD Vance",
            "Chase Oliver Mike ter Maat": "Chase Oliver and Mike ter Maat",
            "Claudia De la Cruz Karina Garcia": "Claudia De la Cruz and Karina Garcia",
            "William P. Stodden Stephanie H. Cholensky": "William P. Stodden and Stephanie H. Cholensky",
            "Robert F. Kennedy Jr Nicole Shanahan": "Robert F. Kennedy Jr and Nicole Shanahan",
            "Shiva Ayyadurai Crystal Ellis": "Shiva Ayyadurai and Crystal Ellis",
        }
        return mapping.get(candidate, candidate)
    return candidate


def normalize_office(raw_title: str) -> tuple[str, str]:
    title = " ".join(raw_title.split())

    if title == "United States President and Vice President":
        return "President", ""

    match = re.match(r"United States Representative District (\d+)$", title)
    if match:
        return "U.S. House", match.group(1)

    match = re.match(r"State Representative District (\d+)$", title)
    if match:
        return "State House", match.group(1)

    match = re.match(r"County Board of Supervisors District (\d+)$", title)
    if match:
        return "County Supervisor", match.group(1)

    return title, ""


def load_sources() -> list[dict]:
    rows = []
    with DISCOVERY_CSV.open(encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            url = row["url"].strip()
            text = row["text"].strip()
            if row["county"] != "Monona":
                continue
            if "general_election_2024_11_05_" not in url or not url.endswith(".pdf"):
                continue
            if text.startswith("ALL Monona County Precincts"):
                continue
            rows.append(row)
    rows.sort(key=lambda row: (0 if row["text"].startswith("Absentee") else 1, row["url"]))
    return rows


def download_pdf(url: str, destination: Path) -> None:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urlopen(request, timeout=60, context=context) as response:
        destination.write_bytes(response.read())


def ensure_pdfs(sources: list[dict], download: bool) -> list[tuple[dict, Path]]:
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    resolved = []
    for source in sources:
        filename = Path(urlparse(source["url"]).path).name
        path = PDF_DIR / filename
        if download and not path.exists():
            download_pdf(source["url"], path)
        if not path.exists():
            raise FileNotFoundError(f"Missing PDF: {path}")
        resolved.append((source, path))
    return resolved


def extract_lines(pdf_path: Path) -> list[str]:
    reader = PdfReader(str(pdf_path))
    lines = []
    for page in reader.pages:
        text = page.extract_text() or ""
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                lines.append(stripped)
    return lines


def choose_precinct(source_text: str, lines: list[str]) -> str:
    for index, line in enumerate(lines):
        if line in {"OFFICIAL RESULTS", "UNOFFICIAL RESULTS"}:
            if index > 0:
                previous = lines[index - 1]
                if previous not in {
                    "GENERAL ELECTION 11-05-2024",
                    "General Election",
                    "Precinct Summary Report",
                    "Election Summary Report",
                }:
                    return previous
    label = source_text.split("·", 1)[0].strip().replace("Â", "")
    if label.lower().startswith("absentee"):
        return "Absentee Only"
    if " - " in label:
        return label.split(" - ", 1)[0].strip()
    return label


def is_header_line(line: str) -> bool:
    if any(line.startswith(prefix) for prefix in REPORT_HEADER_PREFIXES):
        return True
    if "Registered Voters " in line:
        return True
    if re.match(r"^\d+ of \d+ Precincts Reporting", line):
        return True
    return False


def is_possible_contest_line(line: str) -> bool:
    if is_header_line(line):
        return False
    if any(line.startswith(prefix) for prefix in SKIP_LINE_PREFIXES):
        return False
    if TOTAL_VOTES_RE.match(line):
        return False
    if RESULT_RE.match(line):
        return False
    return True


def parse_result(buffer: list[str]) -> tuple[str, str, int] | None:
    text = " ".join(buffer)
    text = re.sub(r"\s+", " ", text).strip()
    match = RESULT_RE.match(text)
    if not match:
        return None
    candidate = match.group("candidate").strip()
    party = (match.group("party") or "").strip()
    votes = int(match.group("votes").replace(",", ""))
    return candidate, party, votes


def parse_pdf(source: dict, pdf_path: Path) -> list[dict]:
    lines = extract_lines(pdf_path)
    precinct = choose_precinct(source["text"], lines)
    is_absentee = precinct == "Absentee Only"
    rows = []
    current_office = None
    current_district = ""
    buffer = []
    seen_registered_voters = False
    index = 0

    while index < len(lines):
        line = lines[index]

        if "Registered Voters " in line:
            seen_registered_voters = True
            index += 1
            continue

        if not seen_registered_voters:
            index += 1
            continue

        if TOTAL_VOTES_RE.match(line):
            current_office = None
            current_district = ""
            buffer = []
            index += 1
            continue

        if current_office is None and is_possible_contest_line(line):
            current_office, current_district = normalize_office(line)
            buffer = []
            index += 1
            continue

        if current_office is None:
            index += 1
            continue

        if is_header_line(line) or line.startswith("Vote For ") or line.startswith("Number of Precincts ") or line.startswith("Precincts Reporting "):
            index += 1
            continue

        if line.startswith("Undervote") or line.startswith("Overvote"):
            buffer = []
            index += 1
            continue

        buffer.append(line)
        parsed = parse_result(buffer)
        if parsed is not None:
            candidate, party, votes = parsed
            rows.append(
                {
                    "county": "Monona",
                    "precinct": precinct,
                    "office": current_office,
                    "district": current_district,
                    "candidate": normalize_candidate(candidate, current_office),
                    "party": party,
                    "votes": votes,
                    "absentee": votes if is_absentee else 0,
                    "election_day": 0 if is_absentee else votes,
                }
            )
            buffer = []
        index += 1

    return rows


def parse_summary_totals() -> dict[tuple[str, str, str], int]:
    if not SUMMARY_PDF.exists():
        return {}

    lines = extract_lines(SUMMARY_PDF)
    totals = {}
    current_office = None
    current_district = ""
    buffer = []
    seen_registered_voters = False
    index = 0

    while index < len(lines):
        line = lines[index]

        if "Registered Voters " in line or re.match(r"^\d+ of \d+ Precincts Reporting", line):
            seen_registered_voters = True
            index += 1
            continue

        if not seen_registered_voters:
            index += 1
            continue

        if TOTAL_VOTES_RE.match(line):
            current_office = None
            current_district = ""
            buffer = []
            index += 1
            continue

        if current_office is None and is_possible_contest_line(line):
            current_office, current_district = normalize_office(line)
            buffer = []
            index += 1
            continue

        if current_office is None or is_header_line(line) or line.startswith("Vote For ") or line.startswith("Number of Precincts ") or line.startswith("Precincts Reporting "):
            index += 1
            continue

        if line.startswith("Undervote") or line.startswith("Overvote"):
            buffer = []
            index += 1
            continue

        buffer.append(line)
        parsed = parse_result(buffer)
        if parsed is not None:
            candidate, _, votes = parsed
            key = (current_office, current_district, normalize_candidate(candidate, current_office))
            totals[key] = votes
            buffer = []
        index += 1

    return totals


def validate(rows: list[dict]) -> list[str]:
    issues = []
    parsed_totals = {}
    for row in rows:
        key = (row["office"], row["district"], row["candidate"])
        parsed_totals[key] = parsed_totals.get(key, 0) + row["votes"]

    summary_totals = parse_summary_totals()
    for key, expected in summary_totals.items():
        if key[0] == "100.00%":
            continue
        actual = parsed_totals.get(key)
        if actual != expected:
            issues.append(f"Mismatch for {key}: parsed {actual}, summary {expected}")
    return issues


def write_rows(rows: list[dict]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true", help="Download missing Monona PDFs before parsing.")
    args = parser.parse_args()

    sources = load_sources()
    resolved = ensure_pdfs(sources, download=args.download)
    rows = []
    for source, pdf_path in resolved:
        rows.extend(parse_pdf(source, pdf_path))

    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))
    write_rows(rows)

    issues = validate(rows)
    if issues:
        for issue in issues:
            print(f"WARNING: {issue}")
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
