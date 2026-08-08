import argparse
import csv
import json
import re
import ssl
import sys
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "data" / "2024_source_discovery"
AUDITORS_URL = "https://sos.iowa.gov/auditors"
AUDITOR_SITES_FILE = OUTPUT_DIR / "county_auditor_sites.json"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)

KEYWORD_PATTERN = re.compile(
    r"(2024|general|election|results|precinct|november|11/5|11-5|11_5)",
    re.IGNORECASE,
)
PREFERRED_PATTERN = re.compile(
    r"(2024.*general.*precinct|precinct.*2024|general.*results|election.*results)",
    re.IGNORECASE,
)


@dataclass
class CountySource:
    county: str
    auditor_url: str


@dataclass
class CandidateLink:
    county: str
    source_page: str
    url: str
    text: str
    score: int


class LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._current_href = None
        self._current_text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        attr_map = dict(attrs)
        self._current_href = attr_map.get("href")
        self._current_text = []

    def handle_data(self, data):
        if self._current_href is not None:
            self._current_text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() != "a" or self._current_href is None:
            return
        text = " ".join(piece.strip() for piece in self._current_text if piece.strip()).strip()
        self.links.append({"href": self._current_href, "text": text})
        self._current_href = None
        self._current_text = []


def fetch_text(url: str, timeout: int = 30) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    context = ssl.create_default_context()
    with urlopen(request, timeout=timeout, context=context) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def normalize_auditor_url(url: str) -> str:
    if "safelinks.protection.outlook.com" in url:
        parsed = urlparse(url)
        wrapped = parse_qs(parsed.query).get("url", [""])[0]
        if wrapped:
            url = unquote(wrapped)
    return url.rstrip("/")


def extract_links(base_url: str, html: str) -> list[dict]:
    parser = LinkExtractor()
    parser.feed(html)
    links = []
    for link in parser.links:
        href = link["href"]
        if not href:
            continue
        absolute = urljoin(base_url, href)
        links.append({"url": absolute, "text": link["text"]})
    return links


def same_host(url_a: str, url_b: str) -> bool:
    return urlparse(url_a).netloc.lower() == urlparse(url_b).netloc.lower()


def score_link(url: str, text: str) -> int:
    haystack = f"{url} {text}"
    score = 0
    if KEYWORD_PATTERN.search(haystack):
        score += 1
    if PREFERRED_PATTERN.search(haystack):
        score += 3
    if re.search(r"\.(pdf|xls|xlsx|csv)$", url, re.IGNORECASE):
        score += 2
    if "election" in haystack.lower():
        score += 1
    if "precinct" in haystack.lower():
        score += 2
    if "results" in haystack.lower():
        score += 1
    return score


def parse_auditor_sources() -> list[CountySource]:
    if AUDITOR_SITES_FILE.exists():
        with AUDITOR_SITES_FILE.open(encoding="utf-8") as file:
            raw_sources = json.load(file)
        return [
            CountySource(
                county=item["county"],
                auditor_url=normalize_auditor_url(item["auditor_url"]),
            )
            for item in raw_sources
            if item.get("auditor_url") and item["auditor_url"] != "https://null/"
        ]

    html = fetch_text(AUDITORS_URL)
    links = extract_links(AUDITORS_URL, html)
    county_links = {}
    pending_county = None

    for link in links:
        text = link["text"].strip()
        url = link["url"].strip()

        if re.fullmatch(r"[A-Z][A-Za-z' ]+", text) and "County Auditor" not in text:
            pending_county = text
            continue

        if pending_county and url.startswith("http") and "iowa.gov" in urlparse(url).netloc.lower():
            county_links[pending_county] = url
            pending_county = None

    return [
        CountySource(county=k, auditor_url=normalize_auditor_url(v))
        for k, v in sorted(county_links.items())
    ]


def choose_seed_pages(auditor_url: str, links: Iterable[dict]) -> list[str]:
    seeds = [auditor_url]
    ranked = []
    for link in links:
        url = link["url"]
        text = link["text"]
        if not same_host(auditor_url, url):
            continue
        score = score_link(url, text)
        if score > 0:
            ranked.append((score, url))
    ranked.sort(reverse=True)
    for _, url in ranked[:5]:
        if url not in seeds:
            seeds.append(url)
    return seeds[:6]


def discover_county_candidates(source: CountySource) -> list[CandidateLink]:
    homepage_html = fetch_text(source.auditor_url)
    homepage_links = extract_links(source.auditor_url, homepage_html)
    seed_pages = choose_seed_pages(source.auditor_url, homepage_links)

    candidates = {}
    for page_url in seed_pages:
        try:
            page_html = homepage_html if page_url == source.auditor_url else fetch_text(page_url)
        except Exception:
            continue

        for link in extract_links(page_url, page_html):
            score = score_link(link["url"], link["text"])
            if score <= 0:
                continue
            key = (link["url"], link["text"])
            existing = candidates.get(key)
            candidate = CandidateLink(
                county=source.county,
                source_page=page_url,
                url=link["url"],
                text=link["text"],
                score=score,
            )
            if existing is None or candidate.score > existing.score:
                candidates[key] = candidate

    return sorted(candidates.values(), key=lambda item: (-item.score, item.url))


def write_outputs(
    sources: list[CountySource],
    results: dict[str, list[CandidateLink]],
    *,
    overwrite_auditor_sites: bool,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if overwrite_auditor_sites:
        with AUDITOR_SITES_FILE.open("w", encoding="utf-8") as file:
            json.dump([asdict(source) for source in sources], file, indent=2)

    with (OUTPUT_DIR / "precinct_source_candidates.json").open("w", encoding="utf-8") as file:
        json.dump(
            {county: [asdict(item) for item in items] for county, items in results.items()},
            file,
            indent=2,
        )

    with (OUTPUT_DIR / "precinct_source_candidates.csv").open(
        "w", newline="", encoding="utf-8"
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=["county", "score", "source_page", "url", "text"],
        )
        writer.writeheader()
        for county in sorted(results):
            for item in results[county]:
                writer.writerow(
                    {
                        "county": item.county,
                        "score": item.score,
                        "source_page": item.source_page,
                        "url": item.url,
                        "text": item.text,
                    }
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--county", action="append", dest="counties")
    args = parser.parse_args()

    all_sources = parse_auditor_sources()
    sources = all_sources
    if args.counties:
        wanted = {name.lower() for name in args.counties}
        sources = [source for source in sources if source.county.lower() in wanted]

    results = {}
    for source in sources:
        print(f"Discovering {source.county}...", file=sys.stderr)
        try:
            results[source.county] = discover_county_candidates(source)
        except Exception as exc:
            results[source.county] = [
                CandidateLink(
                    county=source.county,
                    source_page=source.auditor_url,
                    url="",
                    text=f"ERROR: {exc}",
                    score=-1,
                )
            ]

    write_outputs(
        sources,
        results,
        overwrite_auditor_sites=not AUDITOR_SITES_FILE.exists() or len(sources) == len(all_sources),
    )
    print(f"Wrote discovery outputs to {OUTPUT_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
