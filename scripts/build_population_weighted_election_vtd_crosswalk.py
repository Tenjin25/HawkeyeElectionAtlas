#!/usr/bin/env python3
"""Build a population-weighted election-precinct -> VTD20 crosswalk."""

from __future__ import annotations

import argparse
import csv
import io
import json
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile
from shapely.geometry import shape as shapely_shape
from shapely.strtree import STRtree
from shapely.validation import make_valid
from shapely.ops import transform
from pyproj import Transformer

from build_vtd_crosswalk_chain import read_zip_shapefile, load_single_vtd, repair_geometry

PROJECT = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True).transform


def read_election_geojson(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for feature in payload.get("features", []):
        # Keep GeoJSON coordinates in their source CRS here. The shared
        # repair_geometry() call below performs the one required projection
        # for both shapefile and GeoJSON inputs.
        geometry = shapely_shape(feature["geometry"])
        if geometry.is_empty:
            continue
        rows.append({"attributes": feature.get("properties") or {}, "geometry": geometry})
    return rows


def read_blocks(path: Path, population_path: Path):
    populations = {}
    if population_path.exists():
        with population_path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                populations[row["block_id"].strip()] = int(row["population"] or 0)
    with zipfile.ZipFile(path) as archive:
        names = {Path(n).suffix.lower(): n for n in archive.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(names[".shp"])),
            shx=io.BytesIO(archive.read(names[".shx"])),
            dbf=io.BytesIO(archive.read(names[".dbf"])),
        )
        fields = [f[0] for f in reader.fields[1:]]
        id_field = next(field for field in ("GEOID20", "GEOID", "BLKIDFP00") if field in fields)
        for record, raw in zip(reader.iterRecords(), reader.iterShapes()):
            attrs = dict(zip(fields, record))
            geometry = transform(PROJECT, shapely_shape(raw.__geo_interface__))
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            block_id = str(attrs[id_field]).strip()
            population = populations.get(block_id, int(attrs.get("POP20") or 0))
            if geometry.is_empty or geometry.area <= 0 or population <= 0:
                continue
            yield block_id, geometry, population
        reader.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--election-zip", type=Path, default=Path("data/election_precinct_boundaries/vest/ia_2020_vest.zip"))
    parser.add_argument("--election-stem", default="ia_2020")
    parser.add_argument("--election-geojson", type=Path)
    parser.add_argument("--county-field", default="COUNTY")
    parser.add_argument("--precinct-field", default="NAME")
    parser.add_argument("--alias-fields", default="", help="Comma-separated alternate precinct-name fields.")
    parser.add_argument("--alias-geojson", type=Path, help="Optional point/polygon layer supplying more aliases by a stable ID.")
    parser.add_argument("--alias-key-field", default="SOSID_NEW")
    parser.add_argument("--alias-lookup-fields", default="", help="Comma-separated fields read from --alias-geojson.")
    parser.add_argument("--blocks", type=Path, default=Path("data/census/tl_2020_19_tabblock20.zip"))
    parser.add_argument("--population", type=Path, default=Path("data/census/block_population_2020.csv"))
    parser.add_argument("--block-vtd", type=Path, default=Path("data/crosswalks/block20_to_vtd20.csv"))
    parser.add_argument("--vtd-chain", type=Path)
    parser.add_argument("--output", type=Path, default=Path("data/crosswalks/election_precinct20_to_vtd20_population.csv"))
    args = parser.parse_args()

    blocks = list(read_blocks(args.blocks, args.population))
    block_geometries = [row[1] for row in blocks]
    block_tree = STRtree(block_geometries)
    block_vtd = defaultdict(list)
    with args.block_vtd.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            block_vtd[row["block_id"]].append((row["vtd_id"], float(row["share"])))
    vtd_chain = defaultdict(list)
    if args.vtd_chain:
        with args.vtd_chain.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                vtd_chain[row["source_vtd"]].append((row["target_vtd"], float(row["share"])))

    precinct_rows = (
        read_election_geojson(args.election_geojson)
        if args.election_geojson
        else read_zip_shapefile(args.election_zip, args.election_stem)
    )
    alias_fields = [field.strip() for field in args.alias_fields.split(",") if field.strip()]
    alias_lookup = defaultdict(list)
    alias_lookup_fields = [field.strip() for field in args.alias_lookup_fields.split(",") if field.strip()]
    if args.alias_geojson:
        payload = json.loads(args.alias_geojson.read_text(encoding="utf-8"))
        for feature in payload.get("features", []):
            attrs = feature.get("properties") or {}
            stable_id = str(attrs.get(args.alias_key_field, "")).strip()
            if not stable_id:
                continue
            for field in alias_lookup_fields:
                alias = str(attrs.get(field, "")).strip()
                if alias and alias not in alias_lookup[stable_id]:
                    alias_lookup[stable_id].append(alias)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_precinct_id", "source_county", "source_precinct", "source_precinct_aliases", "source_population", "target_vtd", "share", "method"])
        output_rows = 0
        for row in precinct_rows:
            attrs = row["attributes"]
            county = str(attrs.get(args.county_field, "")).strip()
            if county.upper().endswith(" COUNTY"):
                county = county[:-7].strip()
            precinct = str(attrs.get(args.precinct_field, "")).strip()
            aliases = []
            for field in alias_fields:
                alias = str(attrs.get(field, "")).strip()
                if alias and alias != precinct and alias not in aliases:
                    aliases.append(alias)
            stable_id = str(attrs.get(args.alias_key_field, "")).strip()
            for alias in alias_lookup.get(stable_id, []):
                if alias != precinct and alias not in aliases:
                    aliases.append(alias)
            source_id = f"{county.upper()} - {precinct.upper()}"
            geometry = repair_geometry(row["geometry"])
            if geometry is None:
                continue
            totals = defaultdict(float)
            for index in block_tree.query(geometry, predicate="intersects"):
                block_id, block_geometry, population = blocks[int(index)]
                intersection = geometry.intersection(block_geometry)
                if intersection.is_empty or intersection.area <= 0:
                    continue
                # Uniform population density within each Census block, then
                # distribute the block across VTDs using its block/VTD share.
                weight = population * intersection.area / block_geometry.area
                for vtd, vtd_share in block_vtd.get(block_id, []):
                    for target_vtd, chain_share in vtd_chain.get(vtd, [(vtd, 1.0)]):
                        # Census blocks cannot legitimately move between Iowa
                        # counties. Drop boundary-sliver artifacts introduced
                        # by the geometry-based VTD-vintage chain.
                        if block_id[2:5] != target_vtd[2:5]:
                            continue
                        totals[target_vtd] += weight * vtd_share * chain_share
            total = sum(totals.values())
            if total <= 0:
                continue
            for vtd, weight in sorted(totals.items()):
                share = weight / total
                if share < 1e-12:
                    continue
                writer.writerow([source_id, county, precinct, "|".join(aliases), f"{total:.6f}", vtd, f"{share:.12f}", "population_weighted_block_intersection"])
                output_rows += 1
    print({"election_precincts": len(precinct_rows), "blocks_with_population": len(blocks), "links": output_rows, "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
