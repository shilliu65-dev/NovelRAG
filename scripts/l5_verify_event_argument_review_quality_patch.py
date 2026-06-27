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
from scripts.l5_event_argument_review_quality_patch import (
    ENHANCED_COLUMNS,
    ENHANCED_CSV,
    ENHANCED_JSON,
    FUTURE_TABLES,
    QUALITY_MANIFEST,
    QUALITY_REPORT,
    RECOMMENDATIONS,
    run_l5_event_argument_review_quality_patch,
)
from scripts.l5_event_candidate_review_exporter import EVENT_COLUMNS, object_exists


PASS_MESSAGE = "L5.1a event argument review quality patch FULL PASS"
FAIL_MESSAGE = "L5.1a event argument review quality patch VERIFY FAIL"
VERIFY_JSON = "l5_event_argument_quality_verify_report.json"
VERIFY_MD = "l5_event_argument_quality_verify_report.md"
JSON_COLUMNS = {
    "subject_candidates_json",
    "object_candidates_json",
    "location_candidates_json",
    "time_hint_candidates_json",
    "organization_candidates_json",
    "power_candidates_json",
    "other_argument_candidates_json",
    "warning_flags_json",
    "enhanced_subject_candidates_json",
    "enhanced_object_candidates_json",
    "enhanced_location_candidates_json",
    "enhanced_time_hint_candidates_json",
    "enhanced_argument_candidates_json",
    "enhanced_subject_evidence_span_json",
    "enhanced_warning_flags_json",
}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def add_problem(problems: list[str], condition: bool, message: str) -> None:
    if condition:
        problems.append(message)


def validate_rows(rows: list[dict[str, str]], problems: list[str]) -> None:
    columns = set(rows[0]) if rows else set(EVENT_COLUMNS + ENHANCED_COLUMNS)
    add_problem(problems, bool(set(EVENT_COLUMNS).difference(columns)), f"missing original columns: {sorted(set(EVENT_COLUMNS).difference(columns))}")
    add_problem(problems, bool(set(ENHANCED_COLUMNS).difference(columns)), f"missing enhanced columns: {sorted(set(ENHANCED_COLUMNS).difference(columns))}")
    for index, row in enumerate(rows, start=1):
        for column in JSON_COLUMNS.intersection(row):
            try:
                parsed = json.loads(row[column] or "[]")
            except json.JSONDecodeError:
                problems.append(f"row {index} invalid JSON column {column}")
                continue
            if column == "enhanced_subject_candidates_json":
                if not isinstance(parsed, list):
                    problems.append(f"row {index} enhanced subjects not array")
                for candidate in parsed:
                    if candidate.get("is_confirmed") is not False:
                        problems.append(f"row {index} enhanced candidate is_confirmed not false")
        for numeric_column in ("enhanced_subject_confidence", "enhanced_quality_score"):
            try:
                value = float(row.get(numeric_column, ""))
            except ValueError:
                problems.append(f"row {index} invalid {numeric_column}")
                continue
            if not 0.0 <= value <= 1.0:
                problems.append(f"row {index} {numeric_column} out of range")
        if row.get("enhanced_review_recommendation") not in RECOMMENDATIONS:
            problems.append(f"row {index} invalid enhanced_review_recommendation")
        if row.get("review_decision") == "accept":
            problems.append(f"row {index} review_decision auto-accepted")


def validate_no_future_tables(project_dir: Path, problems: list[str]) -> None:
    conn = sqlite3.connect(project_dir / DB_RELATIVE_PATH, timeout=30)
    try:
        existing = {name for name in FUTURE_TABLES if object_exists(conn, name, "table")}
    finally:
        conn.close()
    if existing:
        problems.append(f"future tables exist: {sorted(existing)}")


def verify_l5_event_argument_review_quality_patch(project_dir: Path | str | None = None, *, output_dir: Path | str = "outputs") -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    run_l5_event_argument_review_quality_patch(root, output_dir=out_dir)
    stable_files = [out_dir / ENHANCED_CSV, out_dir / ENHANCED_JSON]
    first_hashes = {path.name: sha256_file(path) for path in stable_files if path.exists()}
    run_l5_event_argument_review_quality_patch(root, output_dir=out_dir)
    second_hashes = {path.name: sha256_file(path) for path in stable_files if path.exists()}
    if first_hashes != second_hashes:
        problems.append("enhanced outputs are not deterministic")

    for filename in (ENHANCED_CSV, ENHANCED_JSON, QUALITY_REPORT, QUALITY_MANIFEST):
        if not (out_dir / filename).exists():
            problems.append(f"missing output file {filename}")
    rows = read_csv_rows(out_dir / ENHANCED_CSV) if (out_dir / ENHANCED_CSV).exists() else []
    enhanced_json = json.loads((out_dir / ENHANCED_JSON).read_text(encoding="utf-8-sig")) if (out_dir / ENHANCED_JSON).exists() else {"row_count": -1, "rows": []}
    manifest = json.loads((out_dir / QUALITY_MANIFEST).read_text(encoding="utf-8")) if (out_dir / QUALITY_MANIFEST).exists() else {}

    add_problem(problems, len(rows) != int(enhanced_json.get("row_count", -1)), "CSV/JSON row count mismatch")
    add_problem(problems, len(enhanced_json.get("rows", [])) != len(rows), "JSON rows length mismatch")
    validate_rows(rows, problems)
    validate_no_future_tables(root, problems)
    if manifest:
        add_problem(problems, bool(manifest.get("source_mutation_detected")), "source mutation detected")
        add_problem(problems, bool(manifest.get("original_l5_1_export_mutation_detected")), "original L5.1 export mutation detected")
        add_problem(problems, manifest.get("source_fingerprints_before") != manifest.get("source_fingerprints_after"), "source fingerprints differ")
        metrics = manifest.get("quality_metrics", {})
        if metrics.get("original_missing_subject_candidate_count", 0) > 0 and metrics.get("enhanced_subject_candidate_added_count", 0) > 0:
            add_problem(
                problems,
                metrics.get("enhanced_missing_subject_candidate_count", 0) >= metrics.get("original_missing_subject_candidate_count", 0),
                "enhanced missing subject count did not improve",
            )
    report_text = (out_dir / QUALITY_REPORT).read_text(encoding="utf-8") if (out_dir / QUALITY_REPORT).exists() else ""
    for section in (
        "## Summary",
        "## Input Files",
        "## Source Tables",
        "## Optional Source Status",
        "## L5.2 Seed Status",
        "## Scene Block Source Detection",
        "## Character Source Detection",
        "## Quality Metrics",
        "## Subject Candidate Enhancement",
        "## Warning Counts",
        "## Rule Usage Counts",
        "## Review Recommendation Counts",
        "## Output Files",
        "## PASS / WARNING / FAIL",
    ):
        if section not in report_text:
            problems.append(f"quality report missing {section}")

    result = {
        "ok": not problems,
        "problems": problems,
        "row_count": len(rows),
        "final_message": PASS_MESSAGE if not problems else FAIL_MESSAGE,
    }
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = ["# L5.1a Event Argument Review Quality Patch Verification", "", f"- ok: {result['ok']}", f"- row_count: {len(rows)}", "", "## Problems", ""]
    lines.extend(f"- {problem}" for problem in problems) if problems else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5.1a event argument review quality patch.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_event_argument_review_quality_patch(args.project_dir, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
