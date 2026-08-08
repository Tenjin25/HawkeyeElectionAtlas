#!/usr/bin/env python3
"""Build Iowa VTD crosswalks from 2000, 2010, and 2020 shapefiles.

The output uses equal-area polygon intersections. Each source VTD's
intersections are normalized to shares that sum to 1, and the two decade
crosswalks are multiplied to produce a 2000 -> 2020 chain.

This is a geometry-based bridge. The NHGIS block crosswalks in data/census are
retained for population-specific allocation work, but are not required for
this first boundary chain and would require loading millions of block shapes.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import shapefile
from pyproj import Transformer
from shapely.geometry import shape as shapely_shape
from shapely.ops import transform
from shapely.strtree import STRtree
from shapely.validation import make_valid


PROJECT = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True).transform


def repair_geometry(raw: Any):
    geometry = shapely_shape(raw.__geo_interface__ if hasattr(raw, "__geo_interface__") else raw)
    geometry = transform(PROJECT, geometry)
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.is_empty or geometry.area <= 0:
        return None
    return geometry


def read_zip_shapefile(path: Path, stem: str | None = None) -> list[dict[str, Any]]:
    with zipfile.ZipFile(path) as archive:
        members = archive.namelist()
        if stem:
            members = [name for name in members if Path(name).stem.lower() == stem.lower()]
        names = {Path(name).suffix.lower(): name for name in members}
        required = {".shp", ".shx", ".dbf"}
        if not required <= set(names):
            raise ValueError(f"{path}: missing one of {sorted(required)}")
        shp_data = io.BytesIO(archive.read(names[".shp"]))
        shx_data = io.BytesIO(archive.read(names[".shx"]))
        dbf_data = io.BytesIO(archive.read(names[".dbf"]))
        reader = shapefile.Reader(shp=shp_data, shx=shx_data, dbf=dbf_data)
        fields = [field[0] for field in reader.fields[1:]]
        rows = []
        for record, geometry in zip(reader.iterRecords(), reader.iterShapes()):
            rows.append({"attributes": dict(zip(fields, record)), "geometry": geometry})
        reader.close()
        return rows


def load_vtd00(directory: Path) -> list[dict[str, Any]]:
    paths = sorted(directory.glob("tl_2008_*_vtd00.zip"))
    if len(paths) != 99:
        raise ValueError(f"Expected 99 Iowa VTD00 archives in {directory}, found {len(paths)}")
    rows = []
    for path in paths:
        rows.extend(read_zip_shapefile(path))
    return normalize_vtd_rows(rows, "00")


def load_single_vtd(path: Path, vintage: str) -> list[dict[str, Any]]:
    return normalize_vtd_rows(read_zip_shapefile(path), vintage)


def load_plan2(path: Path, chamber: str) -> list[dict[str, Any]]:
    stem = {"congressional": "Plan2_Congress", "house": "Plan2_House", "senate": "Plan2_Senate"}[chamber]
    output = []
    for row in read_zip_shapefile(path, stem):
        attributes = row["attributes"]
        geometry = repair_geometry(row["geometry"])
        district = str(attributes.get("DISTRICT", "")).strip()
        if geometry is None or not district:
            continue
        output.append({
            "id": district,
            "county_fips": "",
            "name": str(attributes.get("DISTRICT_L", "") or attributes.get("NAME", "") or district).strip(),
            "geometry": geometry,
        })
    return output


def normalize_vtd_rows(rows: list[dict[str, Any]], vintage: str) -> list[dict[str, Any]]:
    id_field = {"00": "VTDIDFP00", "10": "GEOID10", "20": "GEOID20"}[vintage]
    county_field = {"00": "COUNTYFP00", "10": "COUNTYFP10", "20": "COUNTYFP20"}[vintage]
    name_field = {"00": "NAMELSAD00", "10": "NAMELSAD10", "20": "NAMELSAD20"}[vintage]
    output = []
    for row in rows:
        attributes = row["attributes"]
        precinct_id = str(attributes.get(id_field, "")).strip()
        if not precinct_id:
            continue
        geometry = repair_geometry(row["geometry"])
        if geometry is None:
            continue
        output.append({
            "id": precinct_id,
            "county_fips": str(attributes.get(county_field, "")).zfill(3),
            "name": str(attributes.get(name_field, "")).strip(),
            "geometry": geometry,
        })
    return output


def build_pair(source: list[dict[str, Any]], target: list[dict[str, Any]], source_vintage: str, target_vintage: str, min_share: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target_geometries = [row["geometry"] for row in target]
    tree = STRtree(target_geometries)
    raw_matches = []
    coverage = []
    for source_row in source:
        source_geometry = source_row["geometry"]
        source_area = source_geometry.area
        matches = []
        for target_index in tree.query(source_geometry, predicate="intersects"):
            target_row = target[int(target_index)]
            intersection = source_geometry.intersection(target_row["geometry"])
            area = intersection.area if not intersection.is_empty else 0
            if area > 0:
                matches.append((target_row, area))
        matched_area = sum(area for _, area in matches)
        coverage.append(matched_area / source_area if source_area else 0)
        if matched_area <= 0:
            continue
        for target_row, area in matches:
            share = area / matched_area
            if share >= min_share:
                raw_matches.append({
                    "source_vtd": source_row["id"],
                    "target_vtd": target_row["id"],
                    "source_county_fips": source_row["county_fips"],
                    "target_county_fips": target_row["county_fips"],
                    "source_name": source_row["name"],
                    "target_name": target_row["name"],
                    "share": share,
                    "source_vintage": source_vintage,
                    "target_vintage": target_vintage,
                    "method": "equal_area_intersection",
                })
    summary = {
        "source_vtds": len(source),
        "target_vtds": len(target),
        "links": len(raw_matches),
        "unmatched_source_vtds": sum(1 for value in coverage if value <= 0),
        "mean_source_coverage": sum(coverage) / len(coverage) if coverage else 0,
        "min_source_coverage": min(coverage) if coverage else 0,
    }
    return raw_matches, summary


def write_pair(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["source_vtd", "target_vtd", "source_county_fips", "target_county_fips", "source_name", "target_name", "share", "source_vintage", "target_vintage", "method"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            output = dict(row)
            output["share"] = f"{float(output['share']):.12f}"
            writer.writerow(output)


def chain_pairs(first: list[dict[str, Any]], second: list[dict[str, Any]], vtd10: list[dict[str, Any]], vtd20: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    second_by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in first:
        first_by_source[row["source_vtd"]].append(row)
    for row in second:
        second_by_source[row["source_vtd"]].append(row)
    names10 = {row["id"]: row for row in vtd10}
    names20 = {row["id"]: row for row in vtd20}
    output = []
    for source_vtd, links in first_by_source.items():
        totals: dict[str, float] = defaultdict(float)
        for link in links:
            for next_link in second_by_source.get(link["target_vtd"], []):
                totals[next_link["target_vtd"]] += float(link["share"]) * float(next_link["share"])
        total = sum(totals.values())
        if total <= 0:
            continue
        source = next((row for row in first if row["source_vtd"] == source_vtd), None)
        for target_vtd, value in sorted(totals.items()):
            target = names20.get(target_vtd, {})
            output.append({
                "source_vtd": source_vtd,
                "target_vtd": target_vtd,
                "source_county_fips": (source or {}).get("source_county_fips", ""),
                "target_county_fips": target.get("county_fips", ""),
                "source_name": (source or {}).get("source_name", ""),
                "target_name": target.get("name", ""),
                "share": value / total,
                "source_vintage": "00",
                "target_vintage": "20",
                "method": "matrix_chain_00_10_20",
            })
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vtd00-dir", type=Path, default=Path("data/census/tl_2008_19_vtd00"))
    parser.add_argument("--vtd10", type=Path, default=Path("data/census/tl_2012_19_vtd10.zip"))
    parser.add_argument("--vtd20", type=Path, default=Path("data/census/tl_2020_19_vtd20.zip"))
    parser.add_argument("--plan2", type=Path, default=Path("data/census/IA_ProposedPlan2_Oct2021.zip"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/crosswalks"))
    parser.add_argument("--min-share", type=float, default=1e-8)
    args = parser.parse_args()

    vtd00 = load_vtd00(args.vtd00_dir)
    vtd10 = load_single_vtd(args.vtd10, "10")
    vtd20 = load_single_vtd(args.vtd20, "20")
    pair_0010, summary_0010 = build_pair(vtd00, vtd10, "00", "10", args.min_share)
    pair_1020, summary_1020 = build_pair(vtd10, vtd20, "10", "20", args.min_share)
    pair_0020 = chain_pairs(pair_0010, pair_1020, vtd10, vtd20)
    write_pair(args.output_dir / "vtd00_to_vtd10.csv", pair_0010)
    write_pair(args.output_dir / "vtd10_to_vtd20.csv", pair_1020)
    write_pair(args.output_dir / "vtd00_to_vtd20_chain.csv", pair_0020)
    plan_outputs = {}
    for chamber in ("congressional", "house", "senate"):
        plan = load_plan2(args.plan2, chamber)
        for vintage, vtd_rows in (("00", vtd00), ("10", vtd10), ("20", vtd20)):
            links, plan_summary = build_pair(vtd_rows, plan, vintage, f"plan2_{chamber}", args.min_share)
            name = f"vtd{vintage}_to_plan2_{chamber}.csv"
            write_pair(args.output_dir / name, links)
            plan_outputs[f"{vintage}_to_{chamber}"] = plan_summary
    summary = {"inputs": {"vtd00_archives": len(list(args.vtd00_dir.glob("*.zip"))), "vtd00": len(vtd00), "vtd10": len(vtd10), "vtd20": len(vtd20), "plan2": str(args.plan2)}, "pairs": {"00_to_10": summary_0010, "10_to_20": summary_1020, "00_to_20_chain_links": len(pair_0020), "plan2": plan_outputs}, "outputs": [str(args.output_dir / name) for name in ("vtd00_to_vtd10.csv", "vtd10_to_vtd20.csv", "vtd00_to_vtd20_chain.csv")] + [str(args.output_dir / f"vtd{vintage}_to_plan2_{chamber}.csv") for vintage in ("00", "10", "20") for chamber in ("congressional", "house", "senate")]}
    (args.output_dir / "vtd_crosswalk_chain_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
