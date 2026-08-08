#!/usr/bin/env python3
"""Convert the Plan 2 shapefiles to browser-ready WGS84 GeoJSON."""

from __future__ import annotations

import argparse
import io
import json
import zipfile
from pathlib import Path

import shapefile
from pyproj import Transformer
from shapely.geometry import mapping, shape
from shapely.ops import transform
from shapely.validation import make_valid

TO_WGS84 = Transformer.from_crs("EPSG:4269", "EPSG:4326", always_xy=True).transform
LAYERS = {
    "congressional": "Plan2_Congress",
    "house": "Plan2_House",
    "senate": "Plan2_Senate",
}


def convert(archive_path: Path, stem: str, output_path: Path) -> dict:
    with zipfile.ZipFile(archive_path) as archive:
        members = {Path(name).suffix.lower(): name for name in archive.namelist() if Path(name).stem.lower() == stem.lower()}
        reader = shapefile.Reader(
            shp=io.BytesIO(archive.read(members[".shp"])),
            shx=io.BytesIO(archive.read(members[".shx"])),
            dbf=io.BytesIO(archive.read(members[".dbf"])),
        )
        fields = [field[0] for field in reader.fields[1:]]
        features = []
        for record, raw_geometry in zip(reader.iterRecords(), reader.iterShapes()):
            properties = dict(zip(fields, record))
            geometry = shape(raw_geometry.__geo_interface__)
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            geometry = transform(TO_WGS84, geometry)
            features.append({"type": "Feature", "properties": properties, "geometry": mapping(geometry)})
        reader.close()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"output": str(output_path), "features": len(features), "fields": fields}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("data/census/IA_ProposedPlan2_Oct2021.zip"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/census"))
    args = parser.parse_args()
    for layer, stem in LAYERS.items():
        print(convert(args.archive, stem, args.output_dir / f"plan2_{layer}.geojson"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
