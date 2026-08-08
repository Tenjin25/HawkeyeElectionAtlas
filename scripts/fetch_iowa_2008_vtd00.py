#!/usr/bin/env python3
"""Download the official 2008 Iowa county VTD00 ZIP files.

TIGER2008 publishes VTD00 one county at a time. The script discovers the
county directories from Census, downloads each county archive, and writes a
manifest so the crosswalk pipeline can use the files reproducibly.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import re
import urllib.request
from pathlib import Path


BASE_URL = "https://www2.census.gov/geo/tiger/TIGER2008/19_IOWA/"
COUNTY_LINK_RE = re.compile(r'href="(19\d{3}_[^"]+/)"', re.I)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "IAPrecinctMap/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/census/tl_2008_19_vtd00"))
    parser.add_argument("--manifest", type=Path, default=Path("data/census/tl_2008_19_vtd00_manifest.json"))
    parser.add_argument("--limit", type=int, help="download only the first N counties (for testing)")
    parser.add_argument("--workers", type=int, default=8, help="parallel downloads (default: 8)")
    args = parser.parse_args()

    listing = fetch(BASE_URL).decode("utf-8", errors="replace")
    counties = sorted(set(COUNTY_LINK_RE.findall(listing)))
    if args.limit:
        counties = counties[:args.limit]
    if not counties:
        raise SystemExit("No Iowa county directories found at the Census URL")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    for county_dir in counties:
        match = re.match(r"(19\d{3})_", county_dir)
        if not match:
            continue
        geoid = match.group(1)
        filename = f"tl_2008_{geoid}_vtd00.zip"
        url = f"{BASE_URL}{county_dir}{filename}"
        destination = args.output_dir / filename
        jobs.append((geoid, county_dir, filename, url, destination))

    def download(job):
        geoid, county_dir, filename, url, destination = job
        if not destination.exists():
            destination.write_bytes(fetch(url))
        return {"geoid": geoid, "county_directory": county_dir, "url": url, "file": str(destination)}

    manifest = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download, job): job for job in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                entry = future.result()
            except Exception as error:
                print(f"{job[0]}: ERROR {error}")
                continue
            manifest.append(entry)
            print(f"{entry['geoid']}: {entry['file']}")

    manifest.sort(key=lambda entry: entry["geoid"])

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(json.dumps({"source": BASE_URL, "files": manifest}, indent=2) + "\n", encoding="utf-8")
    print(f"Downloaded {len(manifest)} county archives; manifest: {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
