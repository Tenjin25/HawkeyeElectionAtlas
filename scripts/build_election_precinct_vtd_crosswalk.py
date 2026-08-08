#!/usr/bin/env python3
"""Crosswalk an election-precinct shapefile to Census VTDs by area."""

from __future__ import annotations

import argparse
import csv
from collections import defaultdict
from pathlib import Path

from pyproj import Transformer
from shapely.ops import transform
from shapely.strtree import STRtree

from build_vtd_crosswalk_chain import load_single_vtd, read_zip_shapefile, repair_geometry


PROJECT = Transformer.from_crs("EPSG:4269", "EPSG:5070", always_xy=True).transform


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--election-zip", type=Path, default=Path("data/election_precinct_boundaries/vest/ia_2020_vest.zip"))
    parser.add_argument("--election-stem", default="ia_2020")
    parser.add_argument("--output", type=Path, default=Path("data/crosswalks/election_precinct20_to_vtd20.csv"))
    args = parser.parse_args()

    vtd_rows = load_single_vtd(Path("data/census/tl_2020_19_vtd20.zip"), "20")
    vtd_geometries = [row["geometry"] for row in vtd_rows]
    tree = STRtree(vtd_geometries)
    election_rows = read_zip_shapefile(args.election_zip, args.election_stem)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    links = 0
    unmatched = 0
    precinct_links = []
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["source_precinct_id", "source_county", "source_precinct", "target_vtd", "share", "method"])
        for row in election_rows:
            attributes = row["attributes"]
            source_county = str(attributes.get("COUNTY", "")).strip()
            source_precinct = str(attributes.get("NAME", "")).strip()
            source_id = f"{source_county.upper()} - {source_precinct.upper()}"
            geometry = repair_geometry(row["geometry"])
            if geometry is None:
                unmatched += 1
                continue
            matches = []
            for index in tree.query(geometry, predicate="intersects"):
                target = vtd_rows[int(index)]
                intersection = geometry.intersection(target["geometry"])
                if not intersection.is_empty and intersection.area > 0:
                    matches.append((target["id"], intersection.area))
            total = sum(area for _, area in matches)
            if total <= 0:
                unmatched += 1
                continue
            for target_vtd, area in matches:
                share = area / total
                writer.writerow([source_id, source_county, source_precinct, target_vtd, f"{share:.12f}", "equal_area_intersection"])
                precinct_links.append((source_id, source_county, source_precinct, target_vtd, share))
                links += 1

    # Compose with the actual Plan 2 bridges generated from
    # IA_ProposedPlan2_Oct2021.zip. This keeps district labels tied to the
    # workspace's shapefile rather than deriving them from election metadata.
    for district_type in ("congressional", "house", "senate"):
        plan2_path = Path(f"data/crosswalks/vtd20_to_plan2_{district_type}.csv")
        if not plan2_path.exists():
            continue
        plan2 = defaultdict(list)
        with plan2_path.open(encoding="utf-8", newline="") as handle:
            for plan_row in csv.DictReader(handle):
                plan2[plan_row["source_vtd"]].append(
                    (plan_row["target_vtd"], plan_row.get("target_name", ""), float(plan_row["share"]))
                )
        composed = defaultdict(list)
        for source_id, county, precinct, vtd, precinct_share in precinct_links:
            for district, district_name, plan_share in plan2.get(vtd, []):
                composed[(source_id, county, precinct, district, district_name)].append(precinct_share * plan_share)
        district_output = args.output.with_name(f"election_precinct20_to_plan2_{district_type}.csv")
        with district_output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["source_precinct_id", "source_county", "source_precinct", "target_district", "target_name", "share", "method"])
            for (source_id, county, precinct, district, district_name), shares in sorted(composed.items()):
                writer.writerow([source_id, county, precinct, district, district_name, f"{sum(shares):.12f}", "vtd20_to_plan2_composed"])

    print({"election_precincts": len(election_rows), "links": links, "unmatched_precincts": unmatched, "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
