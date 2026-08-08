#!/usr/bin/env python3
"""Build Plan 2 district/block/VTD crosswalks from equivalency files."""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

CHAMBERS = {
    "congressional": "Plan2-Congress-Final.csv",
    "house": "PLAN2-HOUSE-FINAL.csv",
    "senate": "PLAN2-SENATE-FINAL.csv",
}


def read_equivalency(path: Path, member: str) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive, archive.open(member) as raw:
        return {
            row[0].strip(): row[1].strip()
            for row in csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
            if len(row) >= 2 and row[0].strip() and row[1].strip()
        }


def read_block_vtd(path: Path) -> dict[str, list[tuple[str, float]]]:
    result = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            block, vtd = row["block_id"].strip(), row["vtd_id"].strip()
            share = float(row["share"] or 0)
            if block and vtd and share > 0:
                result[block].append((vtd, share))
    return result


def read_nhgis(path: Path, source_field: str, target_field: str) -> dict[str, list[tuple[str, float]]]:
    result = defaultdict(list)
    with zipfile.ZipFile(path) as archive:
        member = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        with archive.open(member) as raw:
            for row in csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline="")):
                source, target = row[source_field].strip(), row[target_field].strip()
                weight = float(row["weight"] or 0)
                if source and target and weight > 0:
                    result[source].append((target, weight))
    return normalize(result)


def normalize(mapping):
    result = defaultdict(list)
    for source, links in mapping.items():
        total = sum(value for _, value in links)
        if total > 0:
            result[source] = [(target, value / total) for target, value in links]
    return result


def write_block_plan2(output: Path, mapping: dict[str, str], chamber: str) -> int:
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["block_id", "plan2_district", "share", "chamber", "method"])
        for block, district in sorted(mapping.items()):
            writer.writerow([block, district, "1.000000000000", chamber, "Plan2_equivalency_file"])
    return len(mapping)


def write_vtd_blocks(output: Path, vtd_to_blocks) -> int:
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_vtd", "target_block", "share", "chain"])
        links = 0
        for vtd, block_links in sorted(vtd_to_blocks.items()):
            for block, share in sorted(block_links):
                writer.writerow([vtd, block, f"{share:.12f}", "vtd20_to_block20"])
                links += 1
    return links


def compose(vtd_to_blocks, block_to_district, output: Path, chamber: str) -> dict:
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_vtd", "target_plan2_district", "share", "chamber", "chain"])
        source_count = links = 0
        for vtd, block_links in sorted(vtd_to_blocks.items()):
            totals = defaultdict(float)
            for block, block_share in block_links:
                district = block_to_district.get(block)
                if district:
                    totals[district] += block_share
            total = sum(totals.values())
            if total <= 0:
                continue
            source_count += 1
            for district, value in sorted(totals.items()):
                writer.writerow([vtd, district, f"{value / total:.12f}", chamber, "vtd20_to_block20_to_plan2"])
                links += 1
    return {"source_vtds": source_count, "links": links, "output": str(output)}


def compose_transition(source_to_middle, middle_to_district, output: Path, chamber: str, chain: str) -> dict:
    source_to_district = defaultdict(list)
    for source, middle_links in source_to_middle.items():
        totals = defaultdict(float)
        for middle, source_share in middle_links:
            for district, middle_share in middle_to_district.get(middle, []):
                totals[district] += source_share * middle_share
        source_to_district[source] = list(totals.items())
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_id", "target_plan2_district", "share", "chamber", "chain"])
        links = 0
        for source, values in sorted(source_to_district.items()):
            total = sum(value for _, value in values)
            if total <= 0:
                continue
            for district, value in sorted(values):
                writer.writerow([source, district, f"{value / total:.12f}", chamber, chain])
                links += 1
    return {"source_ids": len(source_to_district), "links": links, "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--equivalency", type=Path, default=Path("data/census/Plan2_EquivalencyFiles.zip"))
    parser.add_argument("--block-vtd", type=Path, default=Path("data/crosswalks/block20_to_vtd20.csv"))
    parser.add_argument("--nhgis0010", type=Path, default=Path("data/census/nhgis_blk2000_blk2010_19.zip"))
    parser.add_argument("--nhgis1020", type=Path, default=Path("data/census/nhgis_blk2010_blk2020_19.zip"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/crosswalks"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    block_to_vtd = read_block_vtd(args.block_vtd)
    # Invert block-normalized memberships and normalize them per VTD.
    vtd_totals = defaultdict(float)
    for links in block_to_vtd.values():
        for vtd, share in links:
            vtd_totals[vtd] += share
    vtd_to_blocks = defaultdict(list)
    for block, links in block_to_vtd.items():
        for vtd, share in links:
            vtd_to_blocks[vtd].append((block, share / vtd_totals[vtd]))

    summary = {"inputs": {"block_vtd": str(args.block_vtd)}, "outputs": {}}
    vtd_blocks_output = args.output_dir / "vtd20_to_block20.csv"
    summary["outputs"]["vtd20_to_block20"] = {
        "source_vtds": len(vtd_to_blocks),
        "links": write_vtd_blocks(vtd_blocks_output, vtd_to_blocks),
        "output": str(vtd_blocks_output),
    }
    for chamber, member in CHAMBERS.items():
        mapping = read_equivalency(args.equivalency, member)
        block_output = args.output_dir / f"block20_to_plan2_{chamber}_equivalency.csv"
        vtd_output = args.output_dir / f"vtd20_to_plan2_{chamber}_via_blocks.csv"
        block_count = write_block_plan2(block_output, mapping, chamber)
        result = compose(vtd_to_blocks, mapping, vtd_output, chamber)
        result["blocks"] = block_count
        result["unmapped_blocks"] = sum(block not in block_to_vtd for block in mapping)
        summary["outputs"][chamber] = result
        print(result, flush=True)

        # Carry the exact 2020-block assignment backward through NHGIS block
        # transitions, preserving the district shares after each composition.
        block10_to_block20 = read_nhgis(args.nhgis1020, "blk2010ge", "blk2020ge")
        block00_to_block10 = read_nhgis(args.nhgis0010, "blk2000ge", "blk2010ge")
        block20_to_district = defaultdict(list)
        for block, district in mapping.items():
            block20_to_district[block].append((district, 1.0))
        summary["outputs"][f"block10_to_plan2_{chamber}"] = compose_transition(
            block10_to_block20, block20_to_district,
            args.output_dir / f"block10_to_plan2_{chamber}_via_blocks.csv", chamber,
            "block10_to_block20_to_plan2",
        )
        block10_to_district = defaultdict(list)
        for block, links in block10_to_block20.items():
            for block20, share in links:
                for district, district_share in block20_to_district.get(block20, []):
                    block10_to_district[block].append((district, share * district_share))
        summary["outputs"][f"block00_to_plan2_{chamber}"] = compose_transition(
            block00_to_block10, normalize(block10_to_district),
            args.output_dir / f"block00_to_plan2_{chamber}_via_blocks.csv", chamber,
            "block00_to_block10_to_block20_to_plan2",
        )
    (args.output_dir / "plan2_block_vtd_chain_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
