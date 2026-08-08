#!/usr/bin/env python3
"""Prepare compact Iowa GeoJSON layers used by the browser atlas."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from shapely.geometry import mapping
from pyproj import Transformer
from shapely.ops import transform, unary_union

from build_vtd_crosswalk_chain import read_zip_shapefile, repair_geometry


ROOT = Path(__file__).resolve().parents[1]
TO_WGS84 = Transformer.from_crs("EPSG:5070", "EPSG:4326", always_xy=True).transform


def write_geojson(rows, output: Path, property_builder, projected=False):
    features = []
    for row in rows:
        geometry = row["geometry"] if projected else repair_geometry(row["geometry"])
        if geometry is None or geometry.is_empty:
            continue
        geometry = transform(TO_WGS84, geometry)
        features.append({
            "type": "Feature",
            "properties": property_builder(row["attributes"]),
            "geometry": mapping(geometry),
        })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")
    return len(features)


def main() -> int:
    census_zip = ROOT / "data/census/tl_2020_19_vtd20.zip"
    vtd_rows = read_zip_shapefile(census_zip, "tl_2020_19_vtd20")
    write_geojson(
        vtd_rows,
        ROOT / "data/census/tl_2020_19_vtd20.geojson",
        lambda a: {"GEOID20": a.get("GEOID20", ""), "NAME20": a.get("NAME20", ""), "COUNTYFP20": a.get("COUNTYFP20", "")},
    )

    county_groups = defaultdict(list)
    for row in vtd_rows:
        geometry = repair_geometry(row["geometry"])
        if geometry is not None and not geometry.is_empty:
            county_groups[str(row["attributes"].get("COUNTYFP20", ""))].append(geometry)
    county_rows = [{"geometry": unary_union(geometries), "attributes": {"COUNTYFP20": county}} for county, geometries in county_groups.items()]
    write_geojson(county_rows, ROOT / "data/census/tl_2020_19_county20.geojson", lambda a: {"GEOID": f"19{a['COUNTYFP20']}", "NAME": a["COUNTYFP20"]}, projected=True)

    vest_rows = read_zip_shapefile(ROOT / "data/election_precinct_boundaries/vest/ia_2020_vest.zip", "ia_2020")
    write_geojson(vest_rows, ROOT / "data/election_precinct_boundaries/vest/ia_2020.geojson", lambda a: {"COUNTY": a.get("COUNTY", ""), "NAME": a.get("NAME", ""), "DISTRICT": a.get("DISTRICT", "")})

    plan2_zip = ROOT / "data/census/IA_ProposedPlan2_Oct2021.zip"
    stems = {
        "congressional": "Plan2_Congress",
        "state_house": "Plan2_House",
        "state_senate": "Plan2_Senate",
    }
    counts = {}
    for scope, stem in stems.items():
        rows = read_zip_shapefile(plan2_zip, stem)
        counts[scope] = write_geojson(rows, ROOT / f"data/census/plan2_{scope}.geojson", lambda a: {"DISTRICT": a.get("DISTRICT", ""), "NAME": a.get("NAME", "")})
    print({"vtd20": len(vtd_rows), "counties": len(county_rows), "vest_precincts": len(vest_rows), "plan2": counts})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
