#!/usr/bin/env python3
"""Extract Iowa block populations from the 2020 PL 94-171 state file."""

from __future__ import annotations

import argparse
import csv
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=ROOT / "data/census/ia2020.pl.zip")
    parser.add_argument("--output", type=Path, default=ROOT / "data/census/block_population_2020.csv")
    args = parser.parse_args()

    with zipfile.ZipFile(args.input) as archive:
        with archive.open("ia000012020.pl") as handle:
            population_by_logrec = {}
            for raw_line in handle:
                fields = raw_line.decode("utf-8").rstrip("\r\n").split("|")
                if len(fields) >= 6:
                    population_by_logrec[fields[4]] = int(fields[5] or 0)

        block_rows = []
        with archive.open("iageo2020.pl") as handle:
            for raw_line in handle:
                fields = raw_line.decode("utf-8").rstrip("\r\n").split("|")
                if len(fields) < 9 or fields[2] != "750":
                    continue
                geoid = fields[8].split("US", 1)[-1]
                if len(geoid) != 15 or not geoid.isdigit():
                    continue
                block_rows.append((geoid, population_by_logrec.get(fields[7], 0)))

    if len(block_rows) < 170_000:
        raise ValueError(f"Expected at least 170,000 Iowa blocks, got {len(block_rows)}")
    population_total = sum(population for _, population in block_rows)
    if population_total != 3_190_369:
        raise ValueError(f"Unexpected Iowa 2020 population total: {population_total}")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["block_id", "population", "vintage", "source"])
        source = args.input.relative_to(ROOT).as_posix()
        for block_id, population in sorted(block_rows):
            writer.writerow([block_id, population, 2020, source])

    print({"blocks": len(block_rows), "population": population_total, "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
