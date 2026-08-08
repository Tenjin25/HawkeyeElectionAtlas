#!/usr/bin/env python3
"""Build VTD20 -> Plan 2 crosswalks directly from GeoJSON layers."""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import shape
from shapely.ops import transform
from shapely.strtree import STRtree
from shapely.validation import make_valid

PROJECT = Transformer.from_crs("EPSG:4326", "EPSG:5070", always_xy=True).transform


def load(path: Path, id_field: str):
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for feature in data["features"]:
        geometry = transform(PROJECT, shape(feature["geometry"]))
        if not geometry.is_valid:
            geometry = make_valid(geometry)
        if geometry.is_empty or geometry.area <= 0:
            continue
        identifier = str(feature["properties"].get(id_field, "")).strip()
        if identifier:
            rows.append((identifier, geometry))
    return rows


def build(vtd_path: Path, plan_path: Path, plan_id: str, output: Path, chamber: str) -> dict:
    vtd = load(vtd_path, "GEOID20")
    plan = load(plan_path, plan_id)
    tree = STRtree([geometry for _, geometry in plan])
    output.parent.mkdir(parents=True, exist_ok=True)
    links = 0
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_vtd", "target_vtd", "source_county_fips", "target_county_fips", "source_name", "target_name", "share", "source_vintage", "target_vintage", "method"])
        for vtd_id, vtd_geometry in vtd:
            matches = []
            for index in tree.query(vtd_geometry, predicate="intersects"):
                district, plan_geometry = plan[int(index)]
                area = vtd_geometry.intersection(plan_geometry).area
                if area > 0:
                    matches.append((district, area))
            total = sum(area for _, area in matches)
            if total <= 0:
                continue
            for district, area in sorted(matches):
                writer.writerow([vtd_id, district, vtd_id[2:5], "", "", district, f"{area / total:.12f}", "20", f"plan2_{chamber}", "equal_area_intersection_geojson"])
                links += 1
    return {"chamber": chamber, "vtds": len(vtd), "districts": len(plan), "links": links, "output": str(output)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census-dir", type=Path, default=Path("data/census"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/crosswalks_geojson"))
    args = parser.parse_args()
    layers = [("congressional", "plan2_congressional.geojson", "DISTRICT"), ("house", "plan2_house.geojson", "DISTRICT"), ("senate", "plan2_senate.geojson", "DISTRICT")]
    for chamber, plan_file, plan_id in layers:
        print(build(args.census_dir / "tl_2020_19_vtd20.geojson", args.census_dir / plan_file, plan_id, args.output_dir / f"vtd20_to_plan2_{chamber}.csv", chamber))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
