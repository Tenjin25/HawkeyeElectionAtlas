#!/usr/bin/env python3
"""Extract total population by Census block from an Iowa SF1 archive."""

from __future__ import annotations

import argparse
import csv
import io
import zipfile
from pathlib import Path


def extract(archive_path: Path, year: str, output: Path, geo_archive_path: Path | None = None) -> dict:
    if year == "2010":
        geo_member, data_member = "iageo2010.sf1", "ia000012010.sf1"
        state_slice, county_slice, tract_slice, block_slice = (27, 29), (29, 32), (54, 60), (61, 65)
        prefix = "SF1ST"
    elif year == "2000":
        geo_member, data_member = "iageo.uf1", "ia00001.uf1"
        state_slice, county_slice, tract_slice, block_slice = (29, 31), (31, 34), (55, 61), (62, 66)
        prefix = "uSF1"
    else:
        raise ValueError(year)

    with zipfile.ZipFile(archive_path) as archive, zipfile.ZipFile(geo_archive_path or archive_path) as geo_archive:
        populations = {}
        with archive.open(data_member) as raw:
            for line in io.TextIOWrapper(raw, encoding="latin1"):
                fields = line.rstrip("\r\n").split(",")
                if len(fields) >= 6 and fields[0] == prefix:
                    populations[fields[4]] = int(fields[5] or 0)

        output.parent.mkdir(parents=True, exist_ok=True)
        block_populations = {}
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["block_id", "population", "vintage", "source"])
            with geo_archive.open(geo_member) as raw:
                for line in io.TextIOWrapper(raw, encoding="latin1"):
                    if line[8:11] != "101":
                        continue
                    logrecno = line[18:25]
                    state = line[state_slice[0]:state_slice[1]]
                    county = line[county_slice[0]:county_slice[1]]
                    tract = line[tract_slice[0]:tract_slice[1]]
                    block = line[block_slice[0]:block_slice[1]]
                    if not (state.strip() and county.strip() and tract.strip() and block.strip()):
                        continue
                    block_id = f"{state}{county}{tract}{block}"
                    block_populations[block_id] = block_populations.get(block_id, 0) + populations.get(logrecno, 0)
            for block_id, population in sorted(block_populations.items()):
                writer.writerow([block_id, population, year, str(archive_path)])
    return {"year": year, "blocks": len(block_populations), "population_records": len(populations), "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--year", choices=["2000", "2010"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--geo-archive", type=Path)
    args = parser.parse_args()
    print(extract(args.archive, args.year, args.output, args.geo_archive))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
