#!/usr/bin/env python3
"""Normalize county-level Iowa statewide election exports."""

from __future__ import annotations

import argparse
import csv
import html
import re
from collections import defaultdict
from pathlib import Path


def clean(value: object) -> str:
    value = html.unescape(str(value or "")).upper().replace("&", " AND ")
    value = re.sub(r"[â€™'`]", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def is_relevant_contest(office: object) -> bool:
    text = clean(office)
    if not text:
        return False
    if "PRESIDENT" in text or "US SENATE" in text or "U S SENATE" in text:
        return True
    if any(marker in text for marker in (
        "COUNTY", "TOWNSHIP", " TWP", "CITY", "SCHOOL", "HOSPITAL", "JUDGE",
        "SUPERVISOR", "HOUSE", "STATE SENATE", "U S HOUSE", "SOIL AND WATER",
    )):
        return False
    statewide_markers = (
        "GOVERNOR", "LIEUTENANT GOVERNOR", "SECRETARY OF STATE", "ATTORNEY GENERAL",
        "AUDITOR OF STATE", "TREASURER OF STATE", "SECRETARY OF AGRICULTURE",
        "AGRICULTURE COMMISSIONER", "COMMISSIONER OF AGRICULTURE",
    )
    return any(marker in text for marker in statewide_markers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--input", type=Path, action="append", required=True)
    parser.add_argument("--party-map-from", type=Path, action="append", default=[])
    parser.add_argument("--party-override", action="append", default=[], help="CANDIDATE=PARTY")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    party_options = defaultdict(set)
    for path in args.party_map_from:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                candidate = clean(row.get("candidate"))
                party = clean(row.get("party"))
                if candidate and candidate not in {"TOTAL", "TOTALS", "OVER VOTES", "UNDER VOTES"} and party:
                    party_options[candidate].add(party)
    party_by_candidate = {
        candidate: next(iter(parties))
        for candidate, parties in party_options.items()
        if len(parties) == 1
    }
    for override in args.party_override:
        candidate, separator, party = override.partition("=")
        if not separator:
            raise SystemExit(f"Invalid --party-override: {override!r}")
        party_by_candidate[clean(candidate)] = clean(party)

    def infer_party(candidate: str) -> str:
        if candidate in party_by_candidate:
            return party_by_candidate[candidate]
        candidate_tokens = set(candidate.split())
        matches = {
            party
            for primary_candidate, party in party_by_candidate.items()
            if len(primary_candidate.split()) >= 2
            and set(primary_candidate.split()) <= candidate_tokens
        }
        return next(iter(matches)) if len(matches) == 1 else ""

    totals = defaultdict(float)
    contest_options = defaultdict(set)
    for path in args.input:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if not is_relevant_contest(row.get("office")):
                    continue
                reporting_level = clean(row.get("reporting_level"))
                if reporting_level and reporting_level != "COUNTY":
                    continue
                county = clean(row.get("county") or row.get("jurisdiction"))
                if not county or county in {"TOTAL", "TOTALS", "GRAND TOTAL", "GRAND TOTALS"}:
                    continue
                office = clean(row.get("office"))
                district = clean(row.get("district"))
                candidate = clean(row.get("candidate"))
                if candidate in {"TOTAL", "TOTALS", "OVER VOTES", "UNDER VOTES"}:
                    continue
                party = clean(row.get("party")) or infer_party(candidate)
                try:
                    votes = float(row.get("votes") or 0)
                except ValueError:
                    continue
                option = candidate or party
                if option:
                    contest_options[(office, district)].add(option)
                totals[(county, office, district, candidate, party)] += votes

    contested = {key for key, options in contest_options.items() if len(options) > 1}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["year", "county", "office", "district", "candidate", "party", "votes"])
        for (county, office, district, candidate, party), votes in sorted(totals.items()):
            if (office, district) in contested:
                writer.writerow([args.year, county, office, district, candidate, party, f"{votes:.6f}"])
    print({"year": args.year, "contests": len(contested), "rows": len(totals), "output": str(args.output)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
