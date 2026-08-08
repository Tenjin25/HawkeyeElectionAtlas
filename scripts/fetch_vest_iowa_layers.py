#!/usr/bin/env python3
"""Fetch public VEST Iowa precinct layers from Harvard Dataverse."""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path


VEST_2020_URL = "https://dataverse.harvard.edu/api/access/datafile/4789403"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/election_precinct_boundaries/vest/ia_2020_vest.zip"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not args.output.exists():
        request = urllib.request.Request(VEST_2020_URL, headers={"User-Agent": "IAPrecinctMap/1.0"})
        with urllib.request.urlopen(request, timeout=120) as response:
            args.output.write_bytes(response.read())
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
