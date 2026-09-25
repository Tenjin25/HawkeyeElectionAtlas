"""Build a compact, display-ready point layer from Iowa's current polling sites.

The source is a local election-administration export. Denomination overrides below
are applied only when an independent church/denomination source matches the site.
"""

import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data/election_precinct_boundaries/current/ia_polling_places_current.geojson"
OUTPUT = ROOT / "data/ia_current_polling_sites.geojson"

# (county, street address): (display name, affiliation source)
VERIFIED = {
    ("CERRO GORDO", "100 S PIERCE AVE"): ("First Presbyterian Church, PCUSA", "https://pcusa.org/congregation/first-church-mason-city-ia"),
    ("DALLAS", "1204 13TH ST"): ("First Presbyterian Church, PCUSA", "https://pcusa.org/ko/congregation/first-church-dallas-center-ia"),
    ("DALLAS", "14300 HICKMAN RD"): ("Heartland Presbyterian Church, PCUSA", "https://pcusa.org/congregation/heartland-church-clive-ia"),
    ("CLINTON", "400 5TH AVE S"): ("First United Presbyterian Church, PCUSA", "https://pcusa.org/congregation/first-united-church-clinton-ia"),
    ("BLACK HAWK", "2015 RAINBOW DR"): ("Cedar Heights Community Presbyterian Church, PCUSA", "https://pcusa.org/congregation/cedar-heights-community-church-cedar-falls-ia"),
    ("BUCHANAN", "643 6TH ST"): ("First Presbyterian Church, PCUSA", "https://pcusa.org/congregation/first-church-jesup-ia"),
    ("BUCHANAN", "115 6TH AVE NW"): ("First Presbyterian Church, PCUSA", "https://pcusa.org/congregation/first-church-independence-ia"),
    ("POLK", "3120 SW 9TH ST"): ("Park Avenue Presbyterian Church, PCUSA", "https://www.pcusa.org/site_media/media/uploads/oga/pdf/2021_stats_minutes.pdf"),
    ("POLK", "4601 DOUGLAS AVE"): ("Douglas Avenue Presbyterian Church, PCUSA", "https://pcusa.org/congregation/douglas-avenue-church-des-moines-ia"),
    ("POLK", "3829 GRAND AVE"): ("Central Presbyterian Church, PCUSA", "https://pcusa.org/congregation/central-church-des-moines-ia"),
    ("POLK", "1025 28TH ST"): ("Covenant Presbyterian Church, PCUSA", "https://www.wdmcovenant.org/about-us.html"),
    ("POTTAWATTAMIE", "1900 S 7TH ST"): ("Bethany Presbyterian Church, PCUSA", "https://pcusa.org/congregation/bethany-church-council-bluffs-ia"),
    ("POTTAWATTAMIE", "224 WALLACE AVE"): ("Gethsemane Presbyterian Church, PCUSA", "https://pcusa.org/congregation/gethsemane-church-council-bluffs-ia"),
    ("LINN", "310 5TH ST SE"): ("First Presbyterian Church, PCUSA", "https://pcusa.org/congregation/first-church-cedar-rapids-ia"),
    ("LINN", "715 38TH ST SE"): ("Calvin-Sinclair Presbyterian Church, PCUSA", "https://pcusa.org/site_media/media/uploads/oga/pdf/statistics/2022_minutes_part_iia_stats.pdf"),
    ("STORY", "159 SHELDON AVENUE"): ("Collegiate Presbyterian Church, PCUSA", "https://pcusa.org/congregation/collegiate-church-ames-ia"),
    ("WEBSTER", "1111 5TH AVE N"): ("First Presbyterian Church, PCUSA", "https://www.pcusa.org/sites/default/files/2024-12/2023_minutes_part_iib_stats.pdf"),
    ("SAC", "100 W 3RD ST"): ("First Presbyterian Church, PCUSA", "https://pcusa.org/congregation/first-church-schaller-ia"),
    ("SCOTT", "1702 IOWA ST"): ("First Presbyterian Church, PCUSA", "https://pcusa.org/congregation/first-church-davenport-ia"),
    ("SCOTT", "4209 W LOCUST ST"): ("New Hope Presbyterian Church, PCUSA", "https://pcusa.org/congregation/new-hope-church-davenport-ia"),
    ("TAMA", "1803 HWY D65"): ("Amity Presbyterian Church, PCUSA", "https://www.pcusa.org/sites/default/files/2024-12/2023_minutes_part_iib_stats.pdf"),
    ("SCOTT", "2619 N DIVISION ST"): ("Newcomb Presbyterian Church, PCUSA", "https://pcusa.org/congregation/newcomb-church-davenport-ia"),
    ("SCOTT", "200 S 12TH ST"): ("First Presbyterian Church of LeClaire, PCUSA", "https://peia.org/directories/churches/char/L/"),
}


def clean_name(value):
    value = re.sub(r"^\(?[A-Z]{1,3}\d{1,4}\)?\s*", "", value.strip())
    value = re.sub(r"^\(?[A-Z]?\d{1,4}\)?\s*[- ]\s*", "", value.strip())
    value = re.sub(r"^\(?[A-Z]\d{1,4}\)?\s*", "", value)
    value = re.sub(r"\s*\([^)]*\)\s*$", "", value)
    return " ".join(word.capitalize() for word in value.split())


def main():
    source = json.loads(SOURCE.read_text(encoding="utf-8"))
    features = []
    matched = set()
    for feature in source["features"]:
        p = feature["properties"]
        county = str(p.get("COUNTY") or "").strip()
        address = str(p.get("ADDR_LINE_1") or "").strip()
        key = (county.upper(), " ".join(address.upper().rstrip(".").split()))
        original_name = str(p.get("POLLING_PLACE") or "").strip()
        if not original_name or feature.get("geometry", {}).get("type") != "Point":
            continue
        match = VERIFIED.get(key) if "PRESBYTERIAN" in original_name.upper() else None
        if match:
            matched.add(key)
        features.append({
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {
                "name": match[0] if match else clean_name(original_name),
                "county": county,
                "precinct": str(p.get("PRECINCT") or "").strip(),
                "address": address,
                "city": str(p.get("CITY_1") or "").strip(),
                "zip": str(p.get("ZIP") or "").strip(),
                "affiliation_source": match[1] if match else "",
            },
        })
    missing = set(VERIFIED) - matched
    if missing:
        raise ValueError(f"Verified sites absent from source: {sorted(missing)}")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(features)} current polling sites; {sum(bool(f['properties']['affiliation_source']) for f in features)} verified affiliation labels")


if __name__ == "__main__":
    main()
