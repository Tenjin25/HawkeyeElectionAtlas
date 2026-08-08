from __future__ import annotations

import argparse
import json
import ssl
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "tmp" / "state_detailxls_2024"
COUNTY_CSV_DIR = ROOT / "data" / "2024" / "counties"
MANIFEST_PATH = OUTPUT_DIR / "download_manifest.json"
BASE_URL = "https://electionresults.iowa.gov"
STATE = "IA"
ELECTION_ID_START = 122323
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

COUNTIES = [
    "Adair",
    "Adams",
    "Allamakee",
    "Appanoose",
    "Audubon",
    "Benton",
    "Black Hawk",
    "Boone",
    "Bremer",
    "Buchanan",
    "Buena Vista",
    "Butler",
    "Calhoun",
    "Carroll",
    "Cass",
    "Cedar",
    "Cerro Gordo",
    "Cherokee",
    "Chickasaw",
    "Clarke",
    "Clay",
    "Clayton",
    "Clinton",
    "Crawford",
    "Dallas",
    "Davis",
    "Decatur",
    "Delaware",
    "Des Moines",
    "Dickinson",
    "Dubuque",
    "Emmet",
    "Fayette",
    "Floyd",
    "Franklin",
    "Fremont",
    "Greene",
    "Grundy",
    "Guthrie",
    "Hamilton",
    "Hancock",
    "Hardin",
    "Harrison",
    "Henry",
    "Howard",
    "Humboldt",
    "Ida",
    "Iowa",
    "Jackson",
    "Jasper",
    "Jefferson",
    "Johnson",
    "Jones",
    "Keokuk",
    "Kossuth",
    "Lee",
    "Linn",
    "Louisa",
    "Lucas",
    "Lyon",
    "Madison",
    "Mahaska",
    "Marion",
    "Marshall",
    "Mills",
    "Mitchell",
    "Monona",
    "Monroe",
    "Montgomery",
    "Muscatine",
    "O'Brien",
    "Osceola",
    "Page",
    "Palo Alto",
    "Plymouth",
    "Pocahontas",
    "Polk",
    "Pottawattamie",
    "Poweshiek",
    "Ringgold",
    "Sac",
    "Scott",
    "Shelby",
    "Sioux",
    "Story",
    "Tama",
    "Taylor",
    "Union",
    "Van Buren",
    "Wapello",
    "Warren",
    "Washington",
    "Wayne",
    "Webster",
    "Winnebago",
    "Winneshiek",
    "Woodbury",
    "Worth",
    "Wright",
]


@dataclass
class DownloadResult:
    county: str
    election_id: int
    county_slug: str
    version: str = ""
    status: str = ""
    detailxls_url: str = ""
    sum_url: str = ""
    error: str = ""


def county_slug(county: str) -> str:
    return county.replace(" ", "_")


def county_output_exists(county: str) -> bool:
    path = COUNTY_CSV_DIR / f"20241105__ia__general__{county.lower()}__precinct.csv"
    return path.exists()


def fetch_bytes(url: str, *, timeout: int = 60) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        return response.read()


def fetch_text(url: str, *, timeout: int = 60) -> str:
    return fetch_bytes(url, timeout=timeout).decode("utf-8", errors="replace")


def target_counties(only_missing: bool, requested: Iterable[str] | None) -> list[str]:
    if requested:
        requested_set = {item.lower() for item in requested}
        counties = [county for county in COUNTIES if county.lower() in requested_set]
    else:
        counties = COUNTIES[:]

    if only_missing:
        counties = [county for county in counties if not county_output_exists(county)]

    return counties


def fetch_county(county: str, election_id: int) -> DownloadResult:
    slug = county_slug(county)
    result = DownloadResult(county=county, election_id=election_id, county_slug=slug)

    current_ver_url = f"{BASE_URL}/{STATE}/{slug}/{election_id}/current_ver.txt"
    try:
        version = fetch_text(current_ver_url).strip()
        result.version = version
        result.detailxls_url = (
            f"{BASE_URL}/{STATE}/{slug}/{election_id}/{version}/reports/detailxls.zip"
        )
        result.sum_url = f"{BASE_URL}/{STATE}/{slug}/{election_id}/{version}/json/sum.json"

        detailxls_bytes = fetch_bytes(result.detailxls_url)
        sum_bytes = fetch_bytes(result.sum_url)

        (OUTPUT_DIR / f"{county.lower()}_detailxls.zip").write_bytes(detailxls_bytes)
        (OUTPUT_DIR / f"{county.lower()}_sum.json").write_bytes(sum_bytes)
        result.status = "downloaded"
    except HTTPError as exc:
        result.status = "http_error"
        result.error = f"{exc.code} {exc.reason}"
    except URLError as exc:
        result.status = "url_error"
        result.error = str(exc.reason)
    except Exception as exc:  # pragma: no cover - defensive
        result.status = "error"
        result.error = str(exc)

    return result


def write_manifest(results: list[DownloadResult]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(
        json.dumps([asdict(result) for result in results], indent=2),
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("counties", nargs="*")
    parser.add_argument("--only-missing", action="store_true")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    counties = target_counties(args.only_missing, args.counties)
    results: list[DownloadResult] = []

    for county in counties:
        election_id = ELECTION_ID_START + COUNTIES.index(county)
        result = fetch_county(county, election_id)
        results.append(result)
        print(f"{county}: {result.status}" + (f" ({result.error})" if result.error else ""))

    write_manifest(results)
    print(f"Wrote manifest to {MANIFEST_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
