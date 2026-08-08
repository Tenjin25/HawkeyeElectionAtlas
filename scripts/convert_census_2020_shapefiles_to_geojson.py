#!/usr/bin/env python3
"""Convert Iowa 2020 Census VTD and county shapefiles to WGS84 GeoJSON."""

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


def convert(archive: Path, output: Path) -> dict:
    with zipfile.ZipFile(archive) as z:
        names = {Path(n).suffix.lower(): n for n in z.namelist()}
        reader = shapefile.Reader(
            shp=io.BytesIO(z.read(names[".shp"])),
            shx=io.BytesIO(z.read(names[".shx"])),
            dbf=io.BytesIO(z.read(names[".dbf"])),
        )
        fields = [f[0] for f in reader.fields[1:]]
        features = []
        for record, raw in zip(reader.iterRecords(), reader.iterShapes()):
            geometry = shape(raw.__geo_interface__)
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            features.append({
                "type": "Feature",
                "properties": dict(zip(fields, record)),
                "geometry": mapping(transform(TO_WGS84, geometry)),
            })
        reader.close()
    output.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")) + "\n", encoding="utf-8")
    return {"output": str(output), "features": len(features), "fields": fields}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--census-dir", type=Path, default=Path("data/census"))
    args = parser.parse_args()
    layers = {
        "tl_2020_19_vtd20.zip": "tl_2020_19_vtd20.geojson",
        "tl_2020_19_county20.zip": "tl_2020_19_county20.geojson",
    }
    for source, target in layers.items():
        print(convert(args.census_dir / source, args.census_dir / target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
