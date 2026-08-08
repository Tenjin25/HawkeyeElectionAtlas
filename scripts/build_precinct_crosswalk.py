#!/usr/bin/env python3
"""Build a reviewable precinct crosswalk from unlike election-result files.

The Iowa result exports in this repository have changed shape several times.
This script deliberately works from semantic column names instead of requiring
one canonical CSV layout. It accepts CSV/TSV and JSON/JSONL row files.

Examples:
  python scripts/build_precinct_crosswalk.py inspect data/2016/20161108__ia__general__precinct.csv
  python scripts/build_precinct_crosswalk.py build \
    --source data/2012/20121106__ia__general__adair__precinct.csv \
    --target data/2020/counties/20201103__ia__general__adair__precinct.csv \
    --output tmp/adair_crosswalk.csv

The output is intentionally conservative: exact normalized-name matches get a
share of 1.0. Fuzzy matches are only included when --allow-fuzzy is supplied,
and are marked with match_method=fuzzy for human review.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


FIELD_ALIASES = {
    "county": ("county", "county_name", "jurisdiction_county", "countyname"),
    "precinct": (
        "precinct", "precinct_name", "precinct_id", "precinct_key",
        "vtd", "vtd_name", "vtd_name20", "voting_precinct", "jurisdiction",
    ),
    "votes": ("votes", "vote", "total_votes", "ballots", "total"),
}
AGGREGATE_MARKERS = (
    "TOTAL", "COUNTY TOTAL", "COUNTY TOTALS", "ALL PRECINCTS",
    "ABSENTEE", "ABSENTEE AND SPECIAL", "ABSENTEE & SPECIAL",
    "ABS & SP BALLOTS", "SPECIAL BALLOTS", "STATE TOTAL",
)


def clean_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def normalize_text(value: Any) -> str:
    value = str(value or "").replace("&", " AND ").upper()
    value = re.sub(r"[’'`]", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def compact_text(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", normalize_text(value))


def is_aggregate_label(value: Any) -> bool:
    text = normalize_text(value)
    if not text:
        return True
    return any(text == marker or text.startswith(marker + " ") for marker in AGGREGATE_MARKERS)


def read_rows(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".json", ".jsonl"}:
        raw = path.read_text(encoding="utf-8-sig")
        if suffix == ".jsonl":
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        payload = json.loads(raw)
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("rows", "data", "results", "records"):
                if isinstance(payload.get(key), list):
                    return [row for row in payload[key] if isinstance(row, dict)]
        raise ValueError(f"{path}: JSON does not contain an array of row objects")

    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;|")
        return list(csv.DictReader(handle, dialect=dialect))


def choose_field(headers: Iterable[str], aliases: Iterable[str]) -> str | None:
    cleaned = {clean_header(header): header for header in headers}
    for alias in aliases:
        if alias in cleaned:
            return cleaned[alias]
    return None


def infer_fields(rows: list[dict[str, Any]]) -> dict[str, str | None]:
    headers = list(rows[0].keys()) if rows else []
    return {name: choose_field(headers, aliases) for name, aliases in FIELD_ALIASES.items()}


def canonical_precinct_rows(path: Path) -> tuple[list[dict[str, Any]], dict[str, str | None]]:
    rows = read_rows(path)
    fields = infer_fields(rows)
    if not fields["county"] or not fields["precinct"]:
        raise ValueError(
            f"{path}: could not find county/precinct columns; detected headers: "
            + ", ".join(str(key) for key in (rows[0].keys() if rows else []))
        )

    output: dict[str, dict[str, Any]] = {}
    county_field, precinct_field = fields["county"], fields["precinct"]
    for index, row in enumerate(rows, start=2):
        county_raw = row.get(county_field, "")
        precinct_raw = row.get(precinct_field, "")
        county = normalize_text(county_raw)
        precinct = normalize_text(precinct_raw)
        if not county or not precinct or is_aggregate_label(precinct) or is_aggregate_label(county):
            continue
        key = f"{county} - {precinct}"
        output.setdefault(key, {
            "precinct_id": key,
            "county": county,
            "precinct": precinct,
            "compact_id": f"{compact_text(county)}-{compact_text(precinct)}",
            "source_file": str(path),
            "source_row": index,
        })
    return list(output.values()), fields


def load_overrides(path: Path | None) -> dict[str, str]:
    if not path:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, dict) and isinstance(payload.get("overrides"), dict):
        payload = payload["overrides"]
    if not isinstance(payload, dict):
        raise ValueError("override file must be a JSON object mapping source IDs to target IDs")
    return {normalize_text(key): normalize_text(value) for key, value in payload.items()}


def build_crosswalk(source: list[dict[str, Any]], target: list[dict[str, Any]], *, allow_fuzzy: bool, overrides: dict[str, str]) -> list[dict[str, Any]]:
    target_by_id = {row["precinct_id"]: row for row in target}
    target_by_compact = defaultdict(list)
    target_by_county: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in target:
        target_by_compact[row["compact_id"]].append(row)
        target_by_county[row["county"]].append(row)

    output = []
    for source_row in source:
        source_id = source_row["precinct_id"]
        target_id = overrides.get(source_id)
        method, confidence = "override", 1.0
        candidates = []
        if target_id not in target_by_id:
            exact = target_by_compact.get(source_row["compact_id"], [])
            if len(exact) == 1:
                target_id, method, confidence = exact[0]["precinct_id"], "exact", 1.0
            elif allow_fuzzy:
                candidates = target_by_county.get(source_row["county"], [])
                ranked = sorted(
                    ((difflib.SequenceMatcher(None, source_row["compact_id"], row["compact_id"]).ratio(), row) for row in candidates),
                    key=lambda item: item[0], reverse=True,
                )
                if ranked and ranked[0][0] >= 0.86 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.04):
                    confidence, target_row = ranked[0]
                    target_id, method = target_row["precinct_id"], "fuzzy"
        if target_id in target_by_id:
            output.append({
                "source_precinct_id": source_id,
                "target_precinct_id": target_id,
                "share": "1.000000",
                "match_method": method,
                "confidence": f"{confidence:.6f}",
            })
        else:
            output.append({
                "source_precinct_id": source_id,
                "target_precinct_id": "",
                "share": "",
                "match_method": "unmatched",
                "confidence": "",
            })
    return output


def write_rows_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    write_rows_csv(path, rows, ["source_precinct_id", "target_precinct_id", "share", "match_method", "confidence"])


def command_inspect(path: Path) -> int:
    rows, fields = canonical_precinct_rows(path)
    counties = sorted({row["county"] for row in rows})
    print(json.dumps({"file": str(path), "detected_fields": fields, "precinct_rows": len(rows), "counties": len(counties), "sample": rows[:5]}, indent=2))
    return 0


def command_build(args: argparse.Namespace) -> int:
    source, source_fields = canonical_precinct_rows(args.source)
    target, target_fields = canonical_precinct_rows(args.target)
    rows = build_crosswalk(source, target, allow_fuzzy=args.allow_fuzzy, overrides=load_overrides(args.overrides))
    write_csv(args.output, rows)
    if args.normalized_output:
        write_rows_csv(args.normalized_output, source, ["precinct_id", "county", "precinct", "compact_id", "source_file", "source_row"])
    matched = sum(bool(row["target_precinct_id"]) for row in rows)
    fuzzy = sum(row["match_method"] == "fuzzy" for row in rows)
    print(json.dumps({"source_rows": len(source), "target_rows": len(target), "matched": matched, "unmatched": len(rows) - matched, "fuzzy_matches": fuzzy, "output": str(args.output), "source_fields": source_fields, "target_fields": target_fields}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("file", type=Path)
    inspect.set_defaults(func=lambda args: command_inspect(args.file))
    build = subparsers.add_parser("build")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--target", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--normalized-output", type=Path)
    build.add_argument("--overrides", type=Path)
    build.add_argument("--allow-fuzzy", action="store_true")
    build.set_defaults(func=command_build)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
