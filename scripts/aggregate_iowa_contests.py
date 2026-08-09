#!/usr/bin/env python3
"""Aggregate Iowa presidential, U.S. Senate, and statewide contests.

The election files use county/precinct labels while the Census boundaries use
VTD IDs. This script matches labels conservatively, aggregates accepted rows to
the native VTD vintage, and allocates them to the generated Plan 2 district
crosswalks. Unmatched or ambiguous precinct labels are written to a report.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import html
import io
import re
import zipfile
from collections import defaultdict
from pathlib import Path

import shapefile

from build_vtd_crosswalk_chain import load_single_vtd, load_vtd00, read_zip_shapefile


ROOT = Path(__file__).resolve().parents[1]
COUNTY_FIPS = {}
VTD_CACHE = {}
ELECTION_PRECINCT_CACHE = None
ELECTION_VOTE_SIGNATURE_CACHE = {}


VEST_VOTE_FIELDS = {
    2016: (
        ("PRESIDENT", "REP", "G16PRERTRU"),
        ("PRESIDENT", "DEM", "G16PREDCLI"),
        ("U S SENATE", "REP", "G16USSRGRA"),
        ("U S SENATE", "DEM", "G16USSDJUD"),
    ),
    2018: (
        ("GOVERNOR", "REP", "G18GOVRREY"),
        ("GOVERNOR", "DEM", "G18GOVDHUB"),
        ("SECRETARY OF STATE", "REP", "G18SOSRPAT"),
        ("SECRETARY OF STATE", "DEM", "G18SOSDDEJ"),
    ),
    2020: (
        ("PRESIDENT", "REP", "G20PRERTRU"),
        ("PRESIDENT", "DEM", "G20PREDBID"),
        ("U S SENATE", "REP", "G20USSRERN"),
        ("U S SENATE", "DEM", "G20USSDGRE"),
    ),
}
KNOWN_CANDIDATE_PARTIES = {
    "KAMALA D HARRIS AND TIM WALZ": "DEM",
    "DONALD J TRUMP AND JD VANCE": "REP",
}


def clean(value: object) -> str:
    value = html.unescape(str(value or "")).upper().replace("&", " AND ")
    value = re.sub(r"[’'`]", "", value)
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def precinct_key(value: object) -> str:
    text = clean(value)
    text = re.sub(r"(?<=[A-Z])(?=\d)|(?<=\d)(?=[A-Z])", " ", text)
    text = re.sub(r"\b(WLOO|WL)\b", "WATERLOO", text)
    text = re.sub(r"\bCF\b", "CEDAR FALLS", text)
    text = re.sub(r"\bW(?=\s*\d)", "WARD ", text)
    text = re.sub(r"\bP(?=\s*\d)", "PRECINCT ", text)
    text = re.sub(r"\b(\d+)(?:ST|ND|RD|TH)\b", r"\1", text)
    roman_numbers = {"I": "1", "II": "2", "III": "3", "IV": "4", "V": "5", "VI": "6"}
    text = " ".join(roman_numbers.get(token, token) for token in text.split())
    number_words = {
        "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
        "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9", "TEN": "10",
        "FIRST": "1", "SECOND": "2", "THIRD": "3", "FOURTH": "4", "FIFTH": "5",
        "SIXTH": "6", "SEVENTH": "7", "EIGHTH": "8", "NINTH": "9", "TENTH": "10",
    }
    text = " ".join(number_words.get(token, token) for token in text.split())
    abbreviations = {
        "BLDG": "BUILDING", "CH": "CHURCH", "COMM": "COMMUNITY",
        "CTR": "CENTER", "DEPT": "DEPARTMENT", "ELEM": "ELEMENTARY",
        "HS": "HIGH SCHOOL", "HQ": "HEADQUARTERS",
    }
    text = " ".join(abbreviations.get(token, token) for token in text.split())
    text = " ".join(str(int(token)) if token.isdigit() else token for token in text.split())
    text = re.sub(
        r"\b(VOTING|DISTRICT|PRECINCT|PRNCT|PCT|VTD|COUNTY|CNTY|CTY|WARD|TOWNSHIP|TWP)\b",
        " ",
        text,
    )
    text = re.sub(r"\bMOUNT\b", "MT", text)
    return re.sub(r"\s+", " ", text).strip()


def ordered_precinct_keys_for_county(value: object, county: object) -> set[str]:
    """Return precinct keys that preserve meaningful token order."""
    key = precinct_key(value)
    county_tokens = set(precinct_key(county).split())
    tokens = [token for token in key.split() if token not in county_tokens]
    ordered = " ".join(tokens)
    return {candidate for candidate in (key, ordered) if candidate}


def precinct_keys_for_county(value: object, county: object) -> set[str]:
    """Return conservative ordered and token-set keys for a precinct label."""
    keys = ordered_precinct_keys_for_county(value, county)
    ordered = min(keys, key=len, default="")
    tokens = ordered.split()
    if len(tokens) > 1:
        keys.add(" ".join(sorted(tokens)))
    return keys


def leading_precinct_code(value: object) -> tuple[str, str] | None:
    match = re.match(r"^([A-Z]{1,5})\s*0*(\d+)\b", clean(value))
    return (match.group(1), str(int(match.group(2)))) if match else None


def distinctive_location_sequence(value: object) -> list[str]:
    stopwords = {
        "ACADEMY", "BASEMENT", "BUILDING", "CENTER", "CHURCH", "CLUB",
        "COLLEGE", "COMMUNITY", "DEPARTMENT", "ELEMENTARY", "FIRE", "HALL",
        "HEADQUARTERS", "HIGH", "LODGE", "MEMORIAL", "PLACE", "SCHOOL",
        "SENIOR", "STATION", "THE",
    }
    return [
        token for token in precinct_key(value).split()
        if not token.isdigit() and token not in stopwords
    ]


def distinctive_location_tokens(value: object) -> set[str]:
    return set(distinctive_location_sequence(value))


def is_relevant_contest(office: object) -> bool:
    text = clean(office)
    if not text:
        return False
    if "PRESIDENT" in text:
        return True
    if "US SENATE" in text or "U S SENATE" in text:
        return True
    if any(marker in text for marker in ("COUNTY", "TOWNSHIP", " TWP", "CITY", "SCHOOL", "HOSPITAL", "JUDGE", "SUPERVISOR", "HOUSE", "STATE SENATE", "U S HOUSE", "SOIL AND WATER")):
        return False
    statewide_markers = ("GOVERNOR", "LIEUTENANT GOVERNOR", "SECRETARY OF STATE", "ATTORNEY GENERAL", "AUDITOR OF STATE", "TREASURER OF STATE", "SECRETARY OF AGRICULTURE", "AGRICULTURE COMMISSIONER", "COMMISSIONER OF AGRICULTURE")
    return any(marker in text for marker in statewide_markers)


def load_county_fips() -> None:
    path = ROOT / "data/census/tl_2020_19_county20.zip"
    if not path.exists():
        return
    for row in read_zip_shapefile(path, "tl_2020_19_county20"):
        attributes = row["attributes"]
        county = clean(attributes.get("NAME20") or attributes.get("NAME"))
        fips = str(attributes.get("COUNTYFP20") or attributes.get("COUNTYFP") or "").strip().zfill(3)
        if county and fips.isdigit():
            COUNTY_FIPS[county] = fips


def input_paths(year: int, root: Path) -> list[Path]:
    if year == 2000:
        return [root / "data/2000/20001107__ia__general__precinct.csv"]
    if year == 2004:
        return [root / "data/2004/20041102__ia__general__precinct.csv"]
    if year == 2012:
        return sorted((root / "data/2012").glob("*__precinct.csv"))
    if year == 2014:
        return [root / "data/2014/20141104__ia__general__precinct.csv"]
    if year == 2018:
        return [root / "data/2018/20181106__ia__general__precinct.csv"]
    if year == 2022:
        # The statewide combined export is incomplete: it omits Auditor,
        # Treasurer, and Secretary of Agriculture.  The 99 county exports
        # contain the complete certified ballot, so use them as the canonical
        # 2022 source.
        county_files = sorted((root / "data/2022/counties").glob("*__precinct.csv"))
        if county_files:
            return county_files
        return [root / "data/2022/20221108__ia__general__precinct.csv"]
    return [root / f"data/{year}/{year}1108__ia__general__precinct.csv" if year == 2016 else root / f"data/{year}/{year}1103__ia__general__precinct.csv" if year == 2020 else root / f"data/{year}/{year}1105__ia__general__precinct.csv"]


def vtd_rows_for_year(year: int):
    if year <= 2004:
        key = "00"
        if key not in VTD_CACHE:
            VTD_CACHE[key] = load_vtd00(ROOT / "data/census/tl_2008_19_vtd00")
        return key, VTD_CACHE[key]
    if year <= 2016:
        key = "10"
        if key not in VTD_CACHE:
            VTD_CACHE[key] = load_single_vtd(ROOT / "data/census/tl_2012_19_vtd10.zip", "10")
        return key, VTD_CACHE[key]
    key = "20"
    if key not in VTD_CACHE:
        VTD_CACHE[key] = load_single_vtd(ROOT / "data/census/tl_2020_19_vtd20.zip", "20")
    return key, VTD_CACHE[key]


def build_match_index(vtd_rows):
    by_county = defaultdict(list)
    for row in vtd_rows:
        by_county[row["county_fips"]].append(row)
    return by_county


def match_precinct(county: str, precinct: str, by_county: dict) -> tuple[dict | None, str, float]:
    county_fips = COUNTY_FIPS.get(clean(county), "")
    candidates = by_county.get(county_fips, []) if county_fips else [row for rows in by_county.values() for row in rows]
    source_key = precinct_key(precinct)
    if not source_key or not candidates:
        return None, "unmatched", 0.0
    exact = [row for row in candidates if precinct_key(row["name"]) == source_key]
    if len(exact) == 1:
        return exact[0], "exact", 1.0
    ranked = sorted(((difflib.SequenceMatcher(None, source_key, precinct_key(row["name"])).ratio(), row) for row in candidates), key=lambda value: value[0], reverse=True)
    if ranked and ranked[0][0] >= 0.90 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.04):
        return ranked[0][1], "fuzzy", ranked[0][0]
    return None, "ambiguous" if ranked else "unmatched", ranked[0][0] if ranked else 0.0


def read_plan_crosswalk(path: Path) -> dict[str, list[tuple[str, float]]]:
    result = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            target = row.get("target_vtd") or row.get("target_plan2_district")
            if target:
                result[row["source_vtd"]].append((target, float(row["share"])))
    return result


def read_election_precinct_crosswalk(path: Path | None = None) -> dict[str, list[tuple[str, float]]]:
    """Read the geometry bridge built from the workspace's VEST precinct layer."""
    global ELECTION_PRECINCT_CACHE
    if ELECTION_PRECINCT_CACHE is not None:
        return ELECTION_PRECINCT_CACHE
    result = defaultdict(list)
    path = path or ROOT / "data/crosswalks/election_precinct20_to_vtd20.csv"
    if path.exists():
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                result[row["source_precinct_id"]].append((row["target_vtd"], float(row["share"])))
    ELECTION_PRECINCT_CACHE = result
    return result


def build_election_precinct_match_index(
    path: Path | None = None,
) -> dict[str, list[tuple[str, str, str, float]]]:
    index = defaultdict(list)
    path = path or ROOT / "data/crosswalks/election_precinct20_to_vtd20.csv"
    if not path.exists():
        return index
    with path.open(encoding="utf-8", newline="") as handle:
        seen = set()
        for row in csv.DictReader(handle):
            source_id = row["source_precinct_id"]
            county = clean(row["source_county"])
            source_population = float(row.get("source_population") or 0)
            aliases = [row["source_precinct"]]
            aliases.extend(str(row.get("source_precinct_aliases") or "").split("|"))
            for alias in aliases:
                alias = str(alias or "").strip()
                for alias_key in precinct_keys_for_county(alias, county):
                    key = (county, alias_key)
                    marker = (key, source_id)
                    if key[1] and marker not in seen:
                        index[county].append((key[1], source_id, alias, source_population))
                        seen.add(marker)
    return index


def build_source_vote_signatures(paths: list[Path], vote_fields) -> dict[tuple[str, str], tuple[float, ...]]:
    """Build a multi-contest signature for each reported precinct."""
    values = defaultdict(lambda: defaultdict(float))
    for path in paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            precinct_field = "precinct" if "precinct" in fields else "jurisdiction"
            for row in reader:
                office = clean(row.get("office"))
                party = clean(row.get("party"))
                signature_field = next(
                    (field for expected_office, expected_party, field in vote_fields if office == expected_office and party == expected_party),
                    None,
                )
                if not signature_field:
                    continue
                key = (clean(row.get("county")), precinct_key(row.get(precinct_field)))
                try:
                    values[key][signature_field] += float(row.get("votes") or 0)
                except ValueError:
                    continue
    ordered_fields = [field for _, _, field in vote_fields]
    return {
        key: tuple(row.get(field, 0.0) for field in ordered_fields)
        for key, row in values.items()
        if all(field in row for field in ordered_fields)
    }


def build_election_vote_signature_index(
    path: Path,
    vote_fields,
    wanted_signatures=None,
):
    """Index VEST geometries by county and exact multi-contest vote totals."""
    result = defaultdict(list)
    records_by_county = defaultdict(list)
    if path.exists():
        with zipfile.ZipFile(path) as archive:
            dbf_name = next((name for name in archive.namelist() if name.lower().endswith(".dbf")), None)
            if dbf_name:
                reader = shapefile.Reader(dbf=io.BytesIO(archive.read(dbf_name)))
                ordered_fields = [field for _, _, field in vote_fields]
                for record in reader.iterRecords():
                    attributes = record.as_dict()
                    county_label = str(attributes.get("COUNTY") or "").strip()
                    county = clean(county_label)
                    precinct = str(attributes.get("NAME") or "").strip()
                    source_id = f"{county_label.upper()} - {precinct.upper()}"
                    signature = tuple(float(attributes.get(field) or 0) for field in ordered_fields)
                    result[(county, signature)].append(((source_id, 1.0),))
                    records_by_county[county].append((source_id, signature))
    # Some Iowa result rows merge two mapped precincts. Search only for the
    # requested signatures that did not match a single VEST record.
    for county, target in wanted_signatures or []:
        if result.get((county, target)):
            continue
        rows = records_by_county.get(county, [])
        for left_index, (left_id, left_signature) in enumerate(rows):
            for right_id, right_signature in rows[left_index + 1:]:
                combined = tuple(a + b for a, b in zip(left_signature, right_signature))
                if combined != target:
                    continue
                left_weight = sum(left_signature)
                right_weight = sum(right_signature)
                denominator = left_weight + right_weight
                if denominator <= 0:
                    shares = ((left_id, 0.5), (right_id, 0.5))
                else:
                    shares = ((left_id, left_weight / denominator), (right_id, right_weight / denominator))
                result[(county, target)].append(shares)
    return result


def match_election_precinct(
    county: str,
    precinct: str,
    index,
    vote_signature: tuple[float, ...] | None = None,
    vote_signature_index=None,
) -> tuple[list[tuple[str, float]] | None, str, float]:
    candidates = index.get(clean(county), [])
    ordered_source_keys = ordered_precinct_keys_for_county(precinct, county)
    source_keys = precinct_keys_for_county(precinct, county)
    source_key = max(source_keys, key=len, default="")
    if vote_signature is not None and vote_signature_index is not None:
        vote_matches = vote_signature_index.get((clean(county), vote_signature), [])
        if len(vote_matches) == 1:
            method = "election_geometry_vote_composite" if len(vote_matches[0]) > 1 else "election_geometry_vote_signature"
            return list(vote_matches[0]), method, 1.0
    # Preserve ward/precinct order before consulting token-set aliases. Without
    # this pass, e.g. ward 1 precinct 2 and ward 2 precinct 1 both collapse to
    # the same sorted tokens and become needlessly ambiguous.
    ordered_exact = list({
        row[1]: row
        for row in candidates
        if ordered_source_keys & ordered_precinct_keys_for_county(row[2], county)
    }.values())
    if len(ordered_exact) == 1:
        return [(ordered_exact[0][1], 1.0)], "election_geometry_exact", 1.0
    if len(ordered_exact) > 1:
        denominator = sum(max(0.0, row[3]) for row in ordered_exact)
        if denominator > 0:
            shares = [(row[1], max(0.0, row[3]) / denominator) for row in ordered_exact]
        else:
            shares = [(row[1], 1.0 / len(ordered_exact)) for row in ordered_exact]
        return shares, "election_geometry_population_composite", 1.0
    exact = list({row[1]: row for row in candidates if row[0] in source_keys}.values())
    if len(exact) == 1:
        return [(exact[0][1], 1.0)], "election_geometry_exact", 1.0
    leading_number = re.match(r"^0*(\d+)\b", clean(precinct))
    if leading_number:
        number = str(int(leading_number.group(1)))
        numeric = list({
            row[1]: row
            for row in candidates
            if row[0].isdigit() and str(int(row[0])) == number
        }.values())
        if len(numeric) == 1:
            return [(numeric[0][1], 1.0)], "election_geometry_code", 1.0
    source_code = leading_precinct_code(precinct)
    if source_code:
        coded = list({
            row[1]: row
            for row in candidates
            if leading_precinct_code(row[2]) == source_code
        }.values())
        if len(coded) == 1:
            return [(coded[0][1], 1.0)], "election_geometry_code", 1.0
    source_location = distinctive_location_tokens(precinct)
    if source_location:
        contained = {}
        source_numbers = {token for token in precinct_key(precinct).split() if token.isdigit()}
        for row in candidates:
            candidate_location = distinctive_location_tokens(row[2])
            if not candidate_location:
                continue
            candidate_numbers = {token for token in precinct_key(row[2]).split() if token.isdigit()}
            if source_numbers and candidate_numbers and not candidate_numbers <= source_numbers:
                continue
            intersects_completely = (
                source_location <= candidate_location
                or candidate_location <= source_location
            )
            candidate_acronym = "".join(token[0] for token in distinctive_location_sequence(row[2]) if token)
            acronym_match = len(source_location) == 1 and next(iter(source_location)) == candidate_acronym
            if intersects_completely or acronym_match:
                contained[row[1]] = row
        if len(contained) == 1:
            row = next(iter(contained.values()))
            return [(row[1], 1.0)], "election_geometry_location", 0.97
    compact_keys = {re.sub(r"[^A-Z0-9]+", "", key) for key in source_keys}
    prefix = list({
        row[1]: row
        for row in candidates
        if any(
            re.sub(r"[^A-Z0-9]+", "", row[0]).startswith(compact)
            or compact.startswith(re.sub(r"[^A-Z0-9]+", "", row[0]))
            for compact in compact_keys if compact
        )
    }.values())
    if len(prefix) == 1:
        return [(prefix[0][1], 1.0)], "election_geometry_prefix", 0.98
    ranked_by_source = {}
    for row in candidates:
        score = max(
            (difflib.SequenceMatcher(None, key, row[0]).ratio() for key in source_keys),
            default=0.0,
        )
        if score > ranked_by_source.get(row[1], (0.0, None))[0]:
            ranked_by_source[row[1]] = (score, row)
    ranked = sorted(ranked_by_source.values(), key=lambda value: value[0], reverse=True)
    if ranked and ranked[0][0] >= 0.88 and (len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= 0.04):
        return [(ranked[0][1][1], 1.0)], "election_geometry_fuzzy", ranked[0][0]
    return None, "election_geometry_ambiguous" if ranked else "election_geometry_unmatched", ranked[0][0] if ranked else 0.0


def is_successful_match_method(method: str) -> bool:
    return method in {"exact", "fuzzy"} or (
        method.startswith("election_geometry_")
        and not method.endswith(("ambiguous", "unmatched"))
    )


def aggregate_year(year: int, root: Path, output_dir: Path, current_geometry: bool = False, election_crosswalk_path: Path | None = None, crosswalk_dir: Path | None = None, election_vote_zip: Path | None = None) -> dict:
    use_current_geometry = current_geometry or year == 2020
    if use_current_geometry:
        vintage = "20"
        by_county = {}
    else:
        vintage, vtd_rows = vtd_rows_for_year(year)
        by_county = build_match_index(vtd_rows)
    election_index = build_election_precinct_match_index(election_crosswalk_path) if use_current_geometry else None
    election_crosswalk = read_election_precinct_crosswalk(election_crosswalk_path) if use_current_geometry else None
    year_input_paths = input_paths(year, root)
    vote_fields = VEST_VOTE_FIELDS.get(year)
    vote_zip = election_vote_zip or root / f"data/election_precinct_boundaries/vest/ia_{year}_vest.zip"
    source_vote_signatures = (
        build_source_vote_signatures(year_input_paths, vote_fields)
        if use_current_geometry and vote_fields and vote_zip.exists()
        else {}
    )
    election_vote_signature_index = (
        build_election_vote_signature_index(
            vote_zip,
            vote_fields,
            {(county, signature) for (county, _), signature in source_vote_signatures.items()},
        )
        if source_vote_signatures
        else None
    )
    totals = defaultdict(float)
    county_totals = defaultdict(float)
    contest_options = defaultdict(set)
    match_rows = {}
    precinct_match_cache = {}
    source_rows = 0
    accepted_rows = 0
    for path in year_input_paths:
        if not path.exists():
            continue
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = reader.fieldnames or []
            precinct_field = "precinct" if "precinct" in fields else "jurisdiction"
            for row_number, row in enumerate(reader, start=2):
                if not is_relevant_contest(row.get("office")):
                    continue
                # Statewide/county exports sometimes repeat candidate totals in
                # rows with no precinct. They are summaries, not geographic
                # reporting units, and would otherwise double-count votes.
                if not clean(row.get(precinct_field)):
                    continue
                source_rows += 1
                try:
                    votes = float(row.get("votes") or 0)
                except ValueError:
                    continue
                office = clean(row.get("office"))
                district = clean(row.get("district"))
                candidate = clean(row.get("candidate"))
                party = clean(row.get("party")) or KNOWN_CANDIDATE_PARTIES.get(candidate, "")
                contest_key = (office, district)
                option = candidate or party
                if option:
                    contest_options[contest_key].add(option)
                contest = (office, district, candidate, party)
                county_totals[(clean(row.get("county")),) + contest] += votes
                election_source_parts = None
                precinct_match_key = (
                    clean(row.get("county")),
                    precinct_key(row.get(precinct_field)),
                )
                if use_current_geometry and election_index:
                    cached_match = precinct_match_cache.get(precinct_match_key)
                    if cached_match is None:
                        source_signature = source_vote_signatures.get(precinct_match_key)
                        cached_match = match_election_precinct(
                            row.get("county", ""),
                            row.get(precinct_field, ""),
                            election_index,
                            source_signature,
                            election_vote_signature_index,
                        )
                        precinct_match_cache[precinct_match_key] = cached_match
                    election_source_parts, method, confidence = cached_match
                    vtd = {
                        "id": "|".join(source_id for source_id, _ in election_source_parts)
                    } if election_source_parts else None
                else:
                    cached_match = precinct_match_cache.get(precinct_match_key)
                    if cached_match is None:
                        cached_match = match_precinct(row.get("county", ""), row.get(precinct_field, ""), by_county)
                        precinct_match_cache[precinct_match_key] = cached_match
                    vtd, method, confidence = cached_match
                match_key = f"{clean(row.get('county'))} - {clean(row.get(precinct_field))}"
                match_rows.setdefault(match_key, {"county": row.get("county", ""), "precinct": row.get(precinct_field, ""), "vtd_id": vtd["id"] if vtd else "", "match_method": method, "confidence": confidence, "source_file": str(path), "source_row": row_number})
                if not vtd:
                    continue
                if use_current_geometry and election_crosswalk and election_source_parts:
                    for election_source_id, source_share in election_source_parts:
                        for target_vtd, share in election_crosswalk.get(election_source_id, []):
                            totals[(target_vtd,) + contest] += votes * source_share * share
                    accepted_rows += 1
                else:
                    totals[(vtd["id"],) + contest] += votes
                    accepted_rows += 1

    contested_keys = {key for key, options in contest_options.items() if len(options) > 1}
    excluded_uncontested = len(contest_options) - len(contested_keys)
    totals = defaultdict(float, {key: value for key, value in totals.items() if (key[1], key[2]) in contested_keys})
    county_totals = defaultdict(float, {key: value for key, value in county_totals.items() if (key[1], key[2]) in contested_keys})

    output_dir.mkdir(parents=True, exist_ok=True)
    county_output = output_dir / f"{year}_contests_to_county.csv"
    with county_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["year", "county", "office", "district", "candidate", "party", "votes"])
        for (county, office, district, candidate, party), votes in sorted(county_totals.items()):
            writer.writerow([year, county, office, district, candidate, party, f"{votes:.6f}"])

    vtd_output = output_dir / f"{year}_contests_to_vtd{vintage}.csv"
    with vtd_output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["year", "vtd_id", "office", "district", "candidate", "party", "votes"])
        for (vtd_id, office, district, candidate, party), votes in sorted(totals.items()):
            writer.writerow([year, vtd_id, office, district, candidate, party, f"{votes:.6f}"])

    report = output_dir / f"{year}_precinct_match_report.csv"
    with report.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["county", "precinct", "vtd_id", "match_method", "confidence", "source_file", "source_row"])
        writer.writeheader()
        writer.writerows(sorted(match_rows.values(), key=lambda row: (clean(row["county"]), clean(row["precinct"]))))

    district_outputs = 0
    crosswalk_dir = crosswalk_dir or root / "data/crosswalks"
    for chamber in ("congressional", "house", "senate"):
        crosswalk = read_plan_crosswalk(crosswalk_dir / f"vtd{vintage}_to_plan2_{chamber}.csv")
        district_totals = defaultdict(float)
        for (vtd_id, office, district, candidate, party), votes in totals.items():
            for target, share in crosswalk.get(vtd_id, []):
                district_totals[(target, office, district, candidate, party)] += votes * share
        output = output_dir / f"{year}_contests_to_plan2_{chamber}.csv"
        with output.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["year", "plan2_district", "office", "district", "candidate", "party", "votes"])
            for (target, office, district, candidate, party), votes in sorted(district_totals.items()):
                writer.writerow([year, target, office, district, candidate, party, f"{votes:.6f}"])
        district_outputs += 1
    return {"year": year, "vintage": vintage, "source_rows": source_rows, "accepted_rows": accepted_rows, "contested_contests": len(contested_keys), "excluded_uncontested_contests": excluded_uncontested, "unique_matched_precincts": sum(is_successful_match_method(row["match_method"]) for row in match_rows.values()), "unique_unmatched_or_ambiguous": sum(not is_successful_match_method(row["match_method"]) for row in match_rows.values()), "vtd_rows": len(totals), "district_outputs": district_outputs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, action="append", choices=[2000, 2004, 2012, 2014, 2016, 2018, 2020, 2022, 2024])
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/aggregates")
    parser.add_argument("--current-geometry", action="store_true", help="Map historical labels through the 2020 election-precinct geometry bridge and Plan 2.")
    parser.add_argument("--election-crosswalk", type=Path, help="Override the election-precinct to VTD20 crosswalk.")
    parser.add_argument("--election-vote-zip", type=Path, help="VEST archive whose embedded vote fields identify election precincts.")
    parser.add_argument("--crosswalk-dir", type=Path, help="Directory containing vtdXX_to_plan2_*.csv files.")
    args = parser.parse_args()
    load_county_fips()
    years = args.year or [2000, 2004, 2012, 2014, 2016, 2018, 2020, 2022, 2024]
    summaries = [aggregate_year(year, ROOT, args.output_dir, args.current_geometry, args.election_crosswalk, args.crosswalk_dir, args.election_vote_zip) for year in years]
    print(summaries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
