#!/usr/bin/env python3
"""Build block-to-VTD links and NHGIS-weighted block-to-VTD chains.

Direct block-to-VTD membership is calculated from equal-area polygon
intersection. Cross-decade links then use the supplied NHGIS block weights:
block00 -> block10 -> block20 -> VTD20.
"""

from __future__ import annotations

import argparse
import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform
from shapely.strtree import STRtree
from pyproj import Transformer
from shapely.validation import make_valid

from build_vtd_crosswalk_chain import load_single_vtd, load_vtd00


PROJECT = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True).transform


def read_blocks(path: Path, vintage: str):
    fields_by_vintage = {"00": "BLKIDFP00", "10": "GEOID", "20": "GEOID20"}
    id_field = fields_by_vintage[vintage]
    with zipfile.ZipFile(path) as archive:
        members = {Path(name).suffix.lower(): name for name in archive.namelist()}
        shp_data = io.BytesIO(archive.read(members[".shp"]))
        shx_data = io.BytesIO(archive.read(members[".shx"]))
        dbf_data = io.BytesIO(archive.read(members[".dbf"]))
        reader = shapefile.Reader(shp=shp_data, shx=shx_data, dbf=dbf_data)
        fields = [field[0] for field in reader.fields[1:]]
        for record, raw_geometry in zip(reader.iterRecords(), reader.iterShapes()):
            attributes = dict(zip(fields, record))
            block_id = str(attributes.get(id_field, "")).strip()
            if not block_id:
                continue
            geometry = transform(PROJECT, shapely_shape(raw_geometry.__geo_interface__))
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            if not geometry.is_empty and geometry.area > 0:
                yield block_id, geometry
        reader.close()


def build_direct(block_path: Path, vtd_rows: list[dict], vintage: str, output: Path) -> dict:
    target_geometries = [row["geometry"] for row in vtd_rows]
    tree = STRtree(target_geometries)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = links = unmatched = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["block_id", "vtd_id", "share", "vintage", "method"])
        for count, (block_id, block_geometry) in enumerate(read_blocks(block_path, vintage), start=1):
            candidates = []
            for index in tree.query(block_geometry, predicate="intersects"):
                vtd = vtd_rows[int(index)]
                intersection = block_geometry.intersection(vtd["geometry"])
                area = intersection.area if not intersection.is_empty else 0
                if area > 0:
                    candidates.append((vtd["id"], area))
            total = sum(area for _, area in candidates)
            if total <= 0:
                unmatched += 1
                continue
            for vtd_id, area in candidates:
                writer.writerow([block_id, vtd_id, f"{area / total:.12f}", vintage, "equal_area_intersection"])
                links += 1
            if count % 100000 == 0:
                print(f"{vintage}: processed {count:,} blocks", flush=True)
    return {"blocks": count, "links": links, "unmatched_blocks": unmatched, "output": str(output)}


def read_membership(path: Path) -> dict[str, list[tuple[str, float]]]:
    result = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            result[row["block_id"]].append((row["vtd_id"], float(row["share"])))
    return result


def read_nhgis(path: Path, source_field: str, target_field: str) -> dict[str, list[tuple[str, float]]]:
    result = defaultdict(list)
    with zipfile.ZipFile(path) as archive:
        name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
        text = io.TextIOWrapper(archive.open(name), encoding="utf-8-sig", newline="")
        for row in csv.DictReader(text):
            source = row[source_field].strip()
            target = row[target_field].strip()
            weight = float(row["weight"] or 0)
            if source and target and weight > 0:
                result[source].append((target, weight))
    return result


def compose(source_to_block: dict[str, list[tuple[str, float]]], block_to_vtd: dict[str, list[tuple[str, float]]], output: Path, label: str) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    count = links = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_block_id", "target_vtd_id", "share", "chain"])
        for source_block, block_links in source_to_block.items():
            totals = defaultdict(float)
            for target_block, transition_weight in block_links:
                for vtd_id, membership_share in block_to_vtd.get(target_block, []):
                    totals[vtd_id] += transition_weight * membership_share
            total = sum(totals.values())
            if total <= 0:
                continue
            count += 1
            for vtd_id, value in sorted(totals.items()):
                writer.writerow([source_block, vtd_id, f"{value / total:.12f}", label])
                links += 1
    return {"source_blocks": count, "links": links, "output": str(output)}


def compose_transitions(first: dict[str, list[tuple[str, float]]], second: dict[str, list[tuple[str, float]]]) -> dict[str, list[tuple[str, float]]]:
    result = defaultdict(list)
    for source, first_links in first.items():
        totals = defaultdict(float)
        for middle, first_weight in first_links:
            for target, second_weight in second.get(middle, []):
                totals[target] += first_weight * second_weight
        total = sum(totals.values())
        if total > 0:
            result[source] = [(target, value / total) for target, value in totals.items()]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/crosswalks"))
    parser.add_argument("--block00", type=Path, default=Path("data/census/tl_2008_19_tabblock00.zip"))
    parser.add_argument("--block10", type=Path, default=Path("data/census/tl_2012_19_tabblock.zip"))
    parser.add_argument("--block20", type=Path, default=Path("data/census/tl_2020_19_tabblock20.zip"))
    parser.add_argument("--nhgis0010", type=Path, default=Path("data/census/nhgis_blk2000_blk2010_19.zip"))
    parser.add_argument("--nhgis1020", type=Path, default=Path("data/census/nhgis_blk2010_blk2020_19.zip"))
    parser.add_argument("--only-direct", action="store_true")
    parser.add_argument("--vintage", choices=("00", "10", "20", "all"), default="all")
    parser.add_argument("--compose-only", action="store_true")
    args = parser.parse_args()

    if args.compose_only:
        vtd00 = vtd10 = vtd20 = []
    else:
        vtd00, vtd10, vtd20 = load_vtd00(Path("data/census/tl_2008_19_vtd00")), load_single_vtd(Path("data/census/tl_2012_19_vtd10.zip"), "10"), load_single_vtd(Path("data/census/tl_2020_19_vtd20.zip"), "20")
    direct = {}
    candidates = (("00", args.block00, vtd00), ("10", args.block10, vtd10), ("20", args.block20, vtd20))
    for vintage, block_path, vtd_rows in candidates:
        if args.compose_only or args.vintage not in ("all", vintage):
            continue
        direct[vintage] = args.output_dir / f"block{vintage}_to_vtd{vintage}.csv"
        print(build_direct(block_path, vtd_rows, vintage, direct[vintage]), flush=True)
    if args.only_direct or args.vintage != "all" or args.compose_only is False and args.vintage != "all":
        return 0

    membership10 = read_membership(args.output_dir / "block10_to_vtd10.csv")
    membership20 = read_membership(args.output_dir / "block20_to_vtd20.csv")
    transition0010 = read_nhgis(args.nhgis0010, "blk2000ge", "blk2010ge")
    transition1020 = read_nhgis(args.nhgis1020, "blk2010ge", "blk2020ge")
    summaries = {
        "block00_to_vtd10": compose(transition0010, membership10, args.output_dir / "block00_to_vtd10_chain.csv", "block00_to_block10_to_vtd10"),
        "block10_to_vtd20": compose(transition1020, membership20, args.output_dir / "block10_to_vtd20_chain.csv", "block10_to_block20_to_vtd20"),
    }
    transition0020 = compose_transitions(transition0010, transition1020)
    summaries["block00_to_vtd20"] = compose(transition0020, membership20, args.output_dir / "block00_to_vtd20_chain.csv", "block00_to_block10_to_block20_to_vtd20")
    print(summaries, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
