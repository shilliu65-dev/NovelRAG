from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import (
    BACKCUT_STATUSES,
    EVENT_COLUMNS,
    EVENT_CSV,
    EVENT_JSON,
    EVENT_MD,
    MANIFEST_JSON,
    SCENE_SOURCE_STATUSES,
    STATE_COLUMNS,
    STATE_CSV,
    STATE_JSON,
    STATE_MD,
    WARNING_FLAGS,
    object_exists,
    run_l5_event_candidate_review_exporter,
)
from scripts.l5_event_candidate_extractor import discover_scene_block_source, scene_block_source_payload


PASS_MESSAGE = "L5.1 event candidate review export FULL PASS"
FAIL_MESSAGE = "L5.1 event candidate review export VERIFY FAIL"
VERIFY_JSON = "l5_event_candidate_review_verify_report.json"
VERIFY_MD = "l5_event_candidate_review_verify_report.md"

JSON_COLUMNS = {
    "subject_candidates_json",
    "object_candidates_json",
    "location_candidates_json",
    "time_hint_candidates_json",
    "organization_candidates_json",
    "power_candidates_json",
    "other_argument_candidates_json",
    "changed_entity_candidates_json",
    "warning_flags_json",
}

REQUIRED_REPORT_SECTIONS = {
    EVENT_MD: [
        "## Summary",
        "## Source Tables",
        "## Optional Source Status",
        "## Scene Block Source Detection",
        "## Row Counts",
        "## Warning Counts",
        "## Evidence Back-cut Status",
        "## Top Trigger Categories",
        "## Review Columns",
        "## Output Files",
        "## PASS / WARNING / FAIL",
    ],
    STATE_MD: [
        "## Summary",
        "## Source Tables",
        "## Optional Source Status",
        "## Scene Block Source Detection",
        "## Row Counts",
        "## Warning Counts",
        "## Evidence Back-cut Status",
        "## State Change Type Candidates",
        "## Review Columns",
        "## Output Files",
        "## PASS / WARNING / FAIL",
    ],
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def add_problem(problems: list[str], condition: bool, message: str) -> None:
    if condition:
        problems.append(message)


def validate_json_columns(rows: list[dict[str, str]], columns: set[str], problems: list[str], label: str) -> None:
    for index, row in enumerate(rows, start=1):
        for column in columns.intersection(row):
            try:
                value = json.loads(row[column] or "[]")
            except json.JSONDecodeError:
                problems.append(f"{label} row {index} invalid JSON column {column}")
                continue
            if column == "warning_flags_json":
                if not isinstance(value, list):
                    problems.append(f"{label} row {index} warning_flags_json is not an array")
                else:
                    invalid = sorted(set(str(item) for item in value).difference(WARNING_FLAGS))
                    if invalid:
                        problems.append(f"{label} row {index} invalid warning flags {invalid}")


def validate_rows(rows: list[dict[str, str]], required_columns: list[str], problems: list[str], label: str) -> None:
    if rows:
        columns = set(rows[0])
    else:
        columns = set(required_columns)
    missing = sorted(set(required_columns).difference(columns))
    add_problem(problems, bool(missing), f"{label} missing columns: {missing}")
    for index, row in enumerate(rows, start=1):
        if row.get("review_status") != "candidate":
            problems.append(f"{label} row {index} review_status is not candidate")
        if "review_decision" not in row:
            problems.append(f"{label} row {index} missing review_decision")
        if "review_note" not in row:
            problems.append(f"{label} row {index} missing review_note")
        if row.get("evidence_backcut_status") not in BACKCUT_STATUSES:
            problems.append(f"{label} row {index} invalid evidence_backcut_status")
        if row.get("scene_block_source_status") not in SCENE_SOURCE_STATUSES:
            problems.append(f"{label} row {index} invalid scene_block_source_status")
    validate_json_columns(rows, JSON_COLUMNS, problems, label)


def validate_scene_refs(project_dir: Path, rows: list[dict[str, str]], problems: list[str]) -> None:
    conn = sqlite3.connect(project_dir / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        source = scene_block_source_payload(discover_scene_block_source(conn))
        if not source.get("detected_scene_block_source"):
            return
        table = source.get("scene_block_table_name")
        id_col = source.get("scene_block_id_column")
        if not table or not id_col or not object_exists(conn, str(table), "table"):
            problems.append("scene block source detected but unavailable")
            return
        valid = {str(row["scene_block_id"]) for row in conn.execute(f"SELECT {id_col} AS scene_block_id FROM {table}")}
        invalid = sorted({row.get("scene_block_id", "") for row in rows if row.get("scene_block_id") and row.get("scene_block_id") not in valid})
        if invalid:
            problems.append(f"invalid scene block refs: {invalid[:5]}")
    finally:
        conn.close()


def validate_reports(output_dir: Path, problems: list[str]) -> None:
    for filename, sections in REQUIRED_REPORT_SECTIONS.items():
        path = output_dir / filename
        if not path.exists():
            problems.append(f"missing markdown report {filename}")
            continue
        text = path.read_text(encoding="utf-8")
        for section in sections:
            if section not in text:
                problems.append(f"{filename} missing section {section}")


def verify_l5_event_candidate_review_export(project_dir: Path | str | None = None, *, output_dir: Path | str = "outputs") -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    run_l5_event_candidate_review_exporter(root, output_dir=out_dir)
    stable_files = [out_dir / EVENT_CSV, out_dir / EVENT_JSON, out_dir / STATE_CSV, out_dir / STATE_JSON]
    first_hashes = {str(path): sha256_file(path) for path in stable_files if path.exists()}
    run_l5_event_candidate_review_exporter(root, output_dir=out_dir)
    second_hashes = {str(path): sha256_file(path) for path in stable_files if path.exists()}
    if first_hashes != second_hashes:
        problems.append("export is not deterministic across two consecutive runs")

    required_files = [EVENT_CSV, EVENT_JSON, EVENT_MD, STATE_CSV, STATE_JSON, STATE_MD, MANIFEST_JSON]
    for filename in required_files:
        if not (out_dir / filename).exists():
            problems.append(f"missing output file {filename}")

    event_rows = read_csv_rows(out_dir / EVENT_CSV) if (out_dir / EVENT_CSV).exists() else []
    state_rows = read_csv_rows(out_dir / STATE_CSV) if (out_dir / STATE_CSV).exists() else []
    event_json = read_json(out_dir / EVENT_JSON) if (out_dir / EVENT_JSON).exists() else {"row_count": -1, "rows": []}
    state_json = read_json(out_dir / STATE_JSON) if (out_dir / STATE_JSON).exists() else {"row_count": -1, "rows": []}
    manifest = read_json(out_dir / MANIFEST_JSON) if (out_dir / MANIFEST_JSON).exists() else {}

    add_problem(problems, len(event_rows) != int(event_json.get("row_count", -1)), "event CSV/JSON row count mismatch")
    add_problem(problems, len(state_rows) != int(state_json.get("row_count", -1)), "state CSV/JSON row count mismatch")
    add_problem(problems, len(event_json.get("rows", [])) != len(event_rows), "event JSON rows length mismatch")
    add_problem(problems, len(state_json.get("rows", [])) != len(state_rows), "state JSON rows length mismatch")
    validate_rows(event_rows, EVENT_COLUMNS, problems, "event")
    validate_rows(state_rows, STATE_COLUMNS, problems, "state")
    validate_scene_refs(root, event_rows + state_rows, problems)
    validate_reports(out_dir, problems)

    if manifest:
        add_problem(problems, bool(manifest.get("source_mutation_detected")), "manifest reports source mutation")
        add_problem(
            problems,
            manifest.get("source_fingerprints_before") != manifest.get("source_fingerprints_after"),
            "source fingerprints before/after differ",
        )
        scene_status = manifest.get("detected_scene_block_source", {}).get("scene_block_source_status")
        add_problem(problems, scene_status not in SCENE_SOURCE_STATUSES, "manifest has invalid scene block source status")

    result = {
        "ok": not problems,
        "problems": problems,
        "event_rows": len(event_rows),
        "state_rows": len(state_rows),
        "final_message": PASS_MESSAGE if not problems else FAIL_MESSAGE,
    }
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = ["# L5.1 Event Candidate Review Export Verification", "", f"- ok: {result['ok']}", f"- event_rows: {len(event_rows)}", f"- state_rows: {len(state_rows)}", "", "## Problems", ""]
    lines.extend(f"- {problem}" for problem in problems) if problems else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5.1 event candidate review export artifacts.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_event_candidate_review_export(args.project_dir, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
