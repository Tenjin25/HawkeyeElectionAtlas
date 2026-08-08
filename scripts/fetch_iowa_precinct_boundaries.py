#!/usr/bin/env python3
"""Download the Iowa Secretary of State county precinct boundary ZIPs."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import urllib.request
from pathlib import Path
from urllib.parse import unquote


INDEX_URL = "https://sos.iowa.gov/shapefiles-county-precincts"
BASE_URL = "https://sos.iowa.gov/elections/pdf/shapefiles/County%20Precincts/"


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "IAPrecinctMap/1.0"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/election_precinct_boundaries/current"))
    parser.add_argument("--manifest", type=Path, default=Path("data/election_precinct_boundaries/current_manifest.json"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    html = fetch(INDEX_URL).decode("utf-8", errors="replace")
    hrefs = re.findall(r'href=["\']([^"\']+Precinct[^"\']+\.zip)["\']', html, re.I)
    urls = sorted(set((href if href.startswith("http") else BASE_URL + href.lstrip("/")) for href in hrefs))
    if not urls:
        raise SystemExit("No county precinct ZIP links found")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def download(url: str):
        name = unquote(url.rsplit("/", 1)[-1])
        destination = args.output_dir / name
        if not destination.exists():
            destination.write_bytes(fetch(url))
        return {"url": url, "file": str(destination)}

    manifest = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download, url): url for url in urls}
        for future in as_completed(futures):
            try:
                entry = future.result()
                manifest.append(entry)
                print(entry["file"], flush=True)
            except Exception as error:
                print(f"ERROR {futures[future]}: {error}", flush=True)
    manifest.sort(key=lambda entry: entry["file"])
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({"source": INDEX_URL, "files": manifest}, indent=2) + "\n", encoding="utf-8")
    print(f"Downloaded {len(manifest)} county precinct archives")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
