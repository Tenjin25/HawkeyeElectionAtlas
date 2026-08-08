from __future__ import annotations

import csv
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OCR_DIR = ROOT / "tmp" / "marion_canvass_ocr_300"
OUTPUT_CSV = ROOT / "data" / "2024" / "counties" / "20241105__ia__general__marion__precinct.csv"

FIELDNAMES = [
    "county",
    "precinct",
    "office",
    "district",
    "candidate",
    "party",
    "votes",
    "absentee",
    "election_day",
]


def clean(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def clean_number(text: str) -> int:
    text = clean(text)
    text = text.replace(" ", "").replace(",", "")
    text = text.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    text = re.sub(r"[^0-9]", "", text)
    if not text:
        return 0
    return int(text)


def parse_number_or_none(text: str) -> int | None:
    original = clean(text)
    compact = original.replace(" ", "").replace(",", "")
    compact = compact.replace("O", "0").replace("o", "0").replace("I", "1").replace("l", "1")
    digits = re.sub(r"[^0-9]", "", compact)
    if digits:
        return int(digits)
    return None


def cluster_words(words: list[dict], threshold: int) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    for word in sorted(words, key=lambda item: (item["y"], item["x"])):
        if not clusters:
            clusters.append([word])
            continue
        last = clusters[-1]
        last_y = statistics.median(item["y"] for item in last)
        if abs(word["y"] - last_y) <= threshold:
            last.append(word)
        else:
            clusters.append([word])
    return clusters


def cluster_header_columns(words: list[dict], threshold: int) -> list[dict]:
    columns: list[dict] = []
    for word in sorted(words, key=lambda item: item["x"]):
        center = word["x"] + word["w"] / 2
        if not columns:
            columns.append({"words": [word], "center": center})
            continue
        last = columns[-1]
        if center - last["center"] <= threshold:
            last["words"].append(word)
            xs = [item["x"] + item["w"] / 2 for item in last["words"]]
            last["center"] = sum(xs) / len(xs)
        else:
            columns.append({"words": [word], "center": center})

    result = []
    for column in columns:
        words_sorted = sorted(column["words"], key=lambda item: (item["y"], item["x"]))
        label = clean(" ".join(word["text"] for word in words_sorted))
        label = label.replace("Write- in", "Write-In")
        label = label.replace("Write- ", "Write-")
        label = label.replace(" ,", ",")
        xs = [word["x"] for word in words_sorted]
        rights = [word["x"] + word["w"] for word in words_sorted]
        result.append(
            {
                "label": label,
                "left": min(xs),
                "right": max(rights),
                "center": sum((word["x"] + word["w"] / 2) for word in words_sorted) / len(words_sorted),
            }
        )
    expanded: list[dict] = []
    for column in result:
        if column["label"] == "Yes No":
            mid = (column["left"] + column["right"]) / 2
            expanded.append({"label": "Yes", "left": column["left"], "right": mid, "center": (column["left"] + mid) / 2})
            expanded.append({"label": "No", "left": mid, "right": column["right"], "center": (mid + column["right"]) / 2})
        else:
            expanded.append(column)

    labels = [column["label"] for column in expanded]
    if labels[:3] == ["Spencer A DEM", "Waugh,", "Brooke Boden, REP"]:
        first, second, third = expanded[:3]
        merged = {
            "label": "Spencer A. Waugh, DEM",
            "left": min(first["left"], second["left"]),
            "right": max(first["right"], second["right"]),
            "center": (first["center"] + second["center"]) / 2,
        }
        expanded = [merged, third] + expanded[3:]
    elif labels[:2] == ["Barb Kniff REP", "McCulla,"]:
        first, second = expanded[:2]
        merged = {
            "label": "Barb Kniff McCulla, REP",
            "left": min(first["left"], second["left"]),
            "right": max(first["right"], second["right"]),
            "center": (first["center"] + second["center"]) / 2,
        }
        expanded = [merged] + expanded[2:]

    labels = [column["label"] for column in expanded]
    candidate_prefix = []
    for label in labels:
        if label in {"Write-in", "Undervotes", "Overvotes", "Total", "Yes", "No"}:
            break
        candidate_prefix.append(label)
    if len(candidate_prefix) == 4 and all(" " not in label and "," not in label for label in candidate_prefix):
        merged_columns = []
        first, second, third, fourth = expanded[:4]
        merged_columns.append(
            {
                "label": f"{first['label']} {second['label']}",
                "left": min(first["left"], second["left"]),
                "right": max(first["right"], second["right"]),
                "center": (first["center"] + second["center"]) / 2,
            }
        )
        merged_columns.append(
            {
                "label": f"{third['label']} {fourth['label']}",
                "left": min(third["left"], fourth["left"]),
                "right": max(third["right"], fourth["right"]),
                "center": (third["center"] + fourth["center"]) / 2,
            }
        )
        expanded = merged_columns + expanded[4:]
    return expanded


def normalize_office(raw: str) -> tuple[str, str]:
    office = clean(raw)
    office = office.replace("Elecfion", "Election")
    office = office.replace("0F", "OF")

    replacements = {
        "President and Vice President": ("President", ""),
        "County Soil and Water Conservation District Commissioner": (
            "Soil and Water Conservation District Commissioner",
            "",
        ),
        "County Agricultural Extension Council": ("County Agricultural Extension Council Member", ""),
        "County Agricultural Extension Council To Fill a Vacancy": (
            "County Agricultural Extension Council Member To Fill a Vacancy",
            "",
        ),
    }
    if office in replacements:
        return replacements[office]

    match = re.fullmatch(r"United States Representative District (\d+)", office)
    if match:
        return "U.S. House", match.group(1)

    match = re.fullmatch(r"State Senator District (\d+)", office)
    if match:
        return "State Senate", match.group(1)

    match = re.fullmatch(r"State Representative District (\d+)", office)
    if match:
        return "State House", match.group(1)

    match = re.fullmatch(r"County Board of Supervisors District (\d+)", office)
    if match:
        return "County Supervisor", match.group(1)

    return office, ""


def normalize_candidate(raw: str) -> tuple[str, str]:
    label = clean(raw)
    corrections = {
        "Write-": "Write-In",
        "Write- in": "Write-In",
        "soc": "SOC",
        "D. Kamala Harris and Tim Walz, DEM": "Kamala D. Harris and Tim Walz, DEM",
        "P. William Stodden and Stephanie H. Cholensky, soc": "William P. Stodden and Stephanie H. Cholensky, SOC",
        "Shiva Ayyadurai Crystal and Ellis": "Shiva Ayyadurai and Crystal Ellis",
    }
    label = corrections.get(label, label)

    if label in {"Write-In", "Yes", "No"}:
        return label, ""

    match = re.fullmatch(r"(.*?),\s*([A-Z]{2,4})", label)
    if match:
        return clean(match.group(1)), match.group(2)

    return label, ""


def extract_office(words: list[dict], current_office: str | None, threshold: int, scale: float) -> str | None:
    top_words = [word for word in words if word["x"] > 300 * scale and word["y"] < 280 * scale]
    lines = []
    for cluster in cluster_words(top_words, threshold=threshold):
        line = clean(" ".join(word["text"] for word in sorted(cluster, key=lambda item: item["x"])))
        if line:
            lines.append(line)

    if len(lines) >= 3 and "General Election" in lines[1]:
        return lines[2]
    return current_office


def body_start(words: list[dict], scale: float) -> int:
    candidates = [word["y"] for word in words if word["x"] < 300 * scale and word["y"] > 350 * scale]
    if not candidates:
        raise ValueError("Could not determine body start.")
    return min(candidates)


def normalize_precinct(raw: str) -> str:
    precinct = clean(raw)
    precinct = precinct.replace(" ownship", " Township")
    precinct = precinct.replace(" TLEY", " Valley")
    precinct = precinct.replace(" PLEASANTVILLE", " Pleasantville")
    precinct = precinct.replace(" ashington", " Washington")
    precinct = precinct.replace(" one", " One")
    return precinct


def normalize_mode(text: str) -> str:
    text = clean(text)
    if text == "Day":
        return "Election Day"
    if text == "Election":
        return "Election Day"
    if "Election" in text and "Day" in text:
        return "Election Day"
    if "Absentee" in text:
        return "Absentee"
    if "Total" in text:
        return "Total"
    return ""


def is_valid_header(columns: list[dict]) -> bool:
    labels = [column["label"] for column in columns]
    if len(labels) < 4:
        return False
    if "Total" not in labels:
        return False
    alpha_labels = sum(any(char.isalpha() for char in label) for label in labels)
    return alpha_labels >= 4


def build_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    current_office: str | None = None
    current_precinct = ""
    current_columns: list[dict] = []

    for path in sorted(OCR_DIR.glob("page-*.json")):
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        words = [{**word, "text": clean(word["text"])} for word in payload["words"] if clean(word["text"])]
        page_text = clean(payload.get("text", ""))
        scale = max((word["x"] for word in words), default=1600) / 1600
        row_threshold = max(25, int(round(25 * scale)))
        header_threshold = max(55, int(round(55 * scale)))

        office = extract_office(words, current_office, row_threshold, scale)
        if office is None:
            continue
        if office != current_office:
            current_precinct = ""
        current_office = office

        try:
            start_y = body_start(words, scale)
        except ValueError:
            if "Total" in page_text and "Page" in page_text:
                continue
            raise
        has_office_line = any(
            word["x"] > 300 * scale and 200 * scale <= word["y"] <= 260 * scale for word in words
        )
        header_min_y = 260 * scale if has_office_line else 120 * scale
        header_words = [
            word
            for word in words
            if word["x"] >= 390 * scale
            and header_min_y <= word["y"] < start_y - 15
            and not re.fullmatch(r"\d+", word["text"])
        ]
        columns = cluster_header_columns(header_words, header_threshold)
        if columns and is_valid_header(columns):
            current_columns = columns
        if not current_columns:
            raise ValueError(f"No columns found for {path.name}")

        body_top = max(0, start_y - 35 * scale)
        body_words = [word for word in words if body_top <= word["y"] < 1145 * scale]
        data_words = [word for word in body_words if word["x"] >= 300 * scale]
        row_clusters = cluster_words(data_words, threshold=row_threshold)

        parsed_rows: list[dict] = []
        for cluster in row_clusters:
            cluster_sorted = sorted(cluster, key=lambda item: item["x"])
            row = {
                "y": statistics.median(item["y"] for item in cluster_sorted),
                "mode_words": [item for item in cluster_sorted if 300 * scale <= item["x"] < 390 * scale],
                "data_words": [item for item in cluster_sorted if item["x"] >= 390 * scale],
                "precinct_words": [],
            }
            parsed_rows.append(row)

        for word in [item for item in body_words if item["x"] < 300 * scale]:
            target = min(parsed_rows, key=lambda row: abs(row["y"] - word["y"]))
            if abs(target["y"] - word["y"]) <= 40 * scale:
                target["precinct_words"].append(word)

        office_label, district = normalize_office(current_office)
        contest_rows: dict[str, dict[str, dict[str, int]]] = defaultdict(dict)
        in_county_total = False

        for row in parsed_rows:
            precinct_text = normalize_precinct(
                " ".join(word["text"] for word in sorted(row["precinct_words"], key=lambda item: (item["y"], item["x"])))
            )
            mode_text = normalize_mode(
                " ".join(word["text"] for word in sorted(row["mode_words"], key=lambda item: (item["y"], item["x"])))
            )

            if precinct_text == "Page":
                break
            values_by_column: dict[str, str] = defaultdict(str)
            for word in row["data_words"]:
                center = word["x"] + word["w"] / 2
                column = min(current_columns, key=lambda item: abs(item["center"] - center))
                values_by_column[column["label"]] += f" {word['text']}"

            raw_values = {}
            for label, text in values_by_column.items():
                parsed = parse_number_or_none(text)
                if parsed is not None:
                    raw_values[label] = parsed

            is_county_total = precinct_text == "Total" and mode_text in {"Election Day", "", "Total"}
            if not is_county_total and not precinct_text and mode_text in {"", "Absentee", "Total"}:
                total_value = raw_values.get("Total", 0)
                if total_value >= 5000:
                    is_county_total = True

            if is_county_total:
                in_county_total = True
                continue
            if in_county_total:
                continue

            if precinct_text:
                current_precinct = precinct_text
            if not current_precinct or not mode_text:
                continue

            contest_rows[current_precinct][mode_text] = raw_values

        for precinct, modes in contest_rows.items():
            if "Total" not in modes:
                continue
            value_labels = [column["label"] for column in current_columns if column["label"] != "Total"]

            for mode_values in modes.values():
                mode_values.setdefault("Overvotes", 0)

            # First, recover missing row values from the opposite vote mode and the combined total.
            for label in value_labels:
                total_val = modes.get("Total", {}).get(label)
                election_day_val = modes.get("Election Day", {}).get(label)
                absentee_val = modes.get("Absentee", {}).get(label)
                if total_val is None:
                    continue
                if election_day_val is None and absentee_val is not None:
                    modes.setdefault("Election Day", {})[label] = max(0, total_val - absentee_val)
                elif absentee_val is None and election_day_val is not None:
                    modes.setdefault("Absentee", {})[label] = max(0, total_val - election_day_val)

            # If OCR dropped digits inside a present cell, prefer the value implied by the
            # opposite vote mode when it also fixes the row-total balance for that mode.
            for mode_name, other_mode in (("Election Day", "Absentee"), ("Absentee", "Election Day")):
                mode_values = modes.get(mode_name)
                other_values = modes.get(other_mode)
                total_mode_values = modes.get("Total")
                if not mode_values or not other_values or not total_mode_values or "Total" not in mode_values:
                    continue
                row_residual = mode_values["Total"] - sum(mode_values.get(label, 0) for label in value_labels if label in mode_values)
                if row_residual == 0:
                    continue
                repairs: list[tuple[str, int]] = []
                for label in value_labels:
                    total_val = total_mode_values.get(label)
                    other_val = other_values.get(label)
                    current_val = mode_values.get(label)
                    if total_val is None or other_val is None or current_val is None:
                        continue
                    inferred = total_val - other_val
                    if inferred < 0 or inferred == current_val:
                        continue
                    if inferred - current_val == row_residual:
                        repairs.append((label, inferred))
                if len(repairs) == 1:
                    label, inferred = repairs[0]
                    mode_values[label] = inferred

            # Then, if a mode still has exactly one missing cell, infer it from the row total.
            for mode_name in ("Election Day", "Absentee", "Total"):
                mode_values = modes.get(mode_name)
                if not mode_values or "Total" not in mode_values:
                    continue
                missing = [label for label in value_labels if label not in mode_values]
                if len(missing) != 1:
                    continue
                inferred = mode_values["Total"] - sum(mode_values.get(label, 0) for label in value_labels if label in mode_values)
                if inferred >= 0:
                    mode_values[missing[0]] = inferred

            for label in [column["label"] for column in current_columns]:
                if label in {"Undervotes", "Overvotes", "Total"}:
                    continue
                total = modes["Total"].get(label)
                absentee = modes.get("Absentee", {}).get(label)
                election_day = modes.get("Election Day", {}).get(label)

                if total is None:
                    if absentee is None or election_day is None:
                        continue
                    total = absentee + election_day

                if absentee is None and election_day is None:
                    absentee = 0
                    election_day = total
                elif absentee is None:
                    absentee = max(0, total - election_day)
                elif election_day is None:
                    election_day = max(0, total - absentee)

                if absentee + election_day != total:
                    raise ValueError(
                        f"{current_office} / {precinct} / {label}: "
                        f"{absentee} + {election_day} != {total}"
                    )
                candidate, party = normalize_candidate(label)
                rows.append(
                    {
                        "county": "Marion",
                        "precinct": precinct,
                        "office": office_label,
                        "district": district,
                        "candidate": candidate,
                        "party": party,
                        "votes": str(total),
                        "absentee": str(absentee),
                        "election_day": str(election_day),
                    }
                )

    return rows


def write_csv(rows: list[dict[str, str]]) -> None:
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda row: (row["office"], row["district"], row["candidate"], row["precinct"]))
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    rows = build_rows()
    write_csv(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
