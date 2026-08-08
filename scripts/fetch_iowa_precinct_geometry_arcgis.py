#!/usr/bin/env python3
"""Fetch Iowa historical precinct polygons or current polling-place points."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from pathlib import Path


SERVICE = "https://gis.legis.iowa.gov/arcgis/rest/services/AR/Precincts/MapServer"
LAYERS = {
    "current": 0,
    "2015": 1,
}


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "IAPrecinctMap/1.0"})
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layer", choices=sorted(LAYERS), default="2015")
    parser.add_argument("--page-size", type=int, default=1000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    layer_id = LAYERS[args.layer]
    output = args.output or Path(
        "data/election_precinct_boundaries/historical/ia_precincts_2015.geojson"
        if args.layer == "2015"
        else "data/election_precinct_boundaries/current/ia_polling_places_current.geojson"
    )
    count_url = f"{SERVICE}/{layer_id}/query?" + urllib.parse.urlencode(
        {"where": "1=1", "returnCountOnly": "true", "f": "json"}
    )
    expected = int(fetch_json(count_url)["count"])

    features = []
    offset = 0
    while offset < expected:
        query = urllib.parse.urlencode(
            {
                "where": "1=1",
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": "4326",
                "resultOffset": offset,
                "resultRecordCount": args.page_size,
                "f": "geojson",
            }
        )
        payload = fetch_json(f"{SERVICE}/{layer_id}/query?{query}")
        page = payload.get("features") or []
        if not page:
            break
        features.extend(page)
        offset += len(page)

    if len(features) != expected:
        raise RuntimeError(f"Expected {expected} precincts, downloaded {len(features)}")
    geometry_types = sorted({feature.get("geometry", {}).get("type", "") for feature in features})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "name": "Iowa precincts 2015" if args.layer == "2015" else "Iowa current polling places",
                "source": f"{SERVICE}/{layer_id}",
                "geometry_types": geometry_types,
                "features": features,
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print({"layer": args.layer, "features": len(features), "geometry_types": geometry_types, "output": str(output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
