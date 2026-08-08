import csv
import json
import re
import ssl
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
DISCOVERY_DIR = ROOT / "data" / "2024_source_discovery"
CANDIDATES_JSON = DISCOVERY_DIR / "precinct_source_candidates.json"
OUTPUT_JSON = DISCOVERY_DIR / "resolved_2024_precinct_pages.json"
OUTPUT_CSV = DISCOVERY_DIR / "resolved_2024_precinct_pages.csv"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

RESULT_PATTERN = re.compile(
    r"(2024|november|11/5|11-5|general|precinct|results)",
    re.IGNORECASE,
)
HIGH_VALUE_PATTERN = re.compile(
    r"(2024.*general|general.*2024|november\s+5.*2024|2024.*precinct|precinct.*2024)",
    re.IGNORECASE,
)


@dataclass
class ResolvedLink:
    county: str
    source_page: str
    url: str
    text: str
    score: int


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        self._href = dict(attrs).get("href")
        self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._href is None:
            return
        text = " ".join(piece.strip() for piece in self._text if piece.strip()).strip()
        self.links.append({"href": self._href, "text": text})
        self._href = None
        self._text = []


def fetch_text(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def extract_links(base_url: str, html: str) -> list[dict]:
    parser = LinkExtractor()
    parser.feed(html)
    out = []
    for link in parser.links:
        href = link["href"]
        if not href or href.startswith("javascript:"):
            continue
        out.append({"url": urljoin(base_url, href), "text": link["text"]})
    return out


def score(url: str, text: str) -> int:
    haystack = f"{url} {text}"
    total = 0
    if RESULT_PATTERN.search(haystack):
        total += 1
    if HIGH_VALUE_PATTERN.search(haystack):
        total += 5
    if re.search(r"\.(pdf|csv|xls|xlsx)$", url, re.IGNORECASE):
        total += 2
    if "2024" in haystack:
        total += 2
    if "general" in haystack.lower():
        total += 1
    if "precinct" in haystack.lower():
        total += 2
    if "november" in haystack.lower():
        total += 1
    return total


def load_seed_pages() -> dict[str, list[dict]]:
    with CANDIDATES_JSON.open(encoding="utf-8") as file:
        data = json.load(file)
    seeds = {}
    for county, items in data.items():
        filtered = [
            item
            for item in items
            if item.get("score", 0) >= 5 and item.get("url", "").startswith("http")
        ]
        seeds[county] = filtered[:5]
    return seeds


def resolve_county(county: str, seeds: list[dict]) -> list[ResolvedLink]:
    out = {}
    for seed in seeds:
        page_url = seed["url"]
        try:
            html = fetch_text(page_url)
        except Exception as exc:
            out[(page_url, f"ERROR: {exc}")] = ResolvedLink(
                county=county,
                source_page=page_url,
                url="",
                text=f"ERROR: {exc}",
                score=-1,
            )
            continue

        for link in extract_links(page_url, html):
            link_score = score(link["url"], link["text"])
            if link_score <= 0:
                continue
            key = (link["url"], link["text"])
            existing = out.get(key)
            candidate = ResolvedLink(
                county=county,
                source_page=page_url,
                url=link["url"],
                text=link["text"],
                score=link_score,
            )
            if existing is None or candidate.score > existing.score:
                out[key] = candidate

    return sorted(out.values(), key=lambda item: (-item.score, item.url, item.text))


def write_outputs(results: dict[str, list[ResolvedLink]]) -> None:
    with OUTPUT_JSON.open("w", encoding="utf-8") as file:
        json.dump({k: [asdict(v) for v in vals] for k, vals in results.items()}, file, indent=2)

    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["county", "score", "source_page", "url", "text"],
        )
        writer.writeheader()
        for county in sorted(results):
            for item in results[county]:
                writer.writerow(asdict(item))


def main() -> int:
    seeds = load_seed_pages()
    results = {}
    for county, items in seeds.items():
        if not items:
            continue
        print(f"Resolving {county}...", file=sys.stderr)
        results[county] = resolve_county(county, items)

    write_outputs(results)
    print(f"Wrote resolved outputs to {OUTPUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
