from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import object_exists
from scripts.l5_review_decision_intake import DEFAULT_INPUT_FILE, HUMAN_DECISIONS, L5_4_TABLES, run_l5_review_decision_intake
from scripts.l5_review_decision_reporter import (
    CONFLICT_CSV,
    CONFLICT_JSON,
    CONFLICT_MD,
    CURRENT_CSV,
    CURRENT_JSON,
    READINESS_CSV,
    READINESS_JSON,
    READINESS_REPORT,
    REPORT_MD,
    SUMMARY_JSON,
    run_l5_review_decision_reporter,
)


PASS_MESSAGE = "L5.4 review decision intake FULL PASS"
FAIL_MESSAGE = "L5.4 review decision intake FAIL"
VERIFY_JSON = "l5_review_decision_verify_report.json"
VERIFY_MD = "l5_review_decision_verify_report.md"

REQUIRED_COLUMNS = {
    "l5_review_decision_import": {
        "review_import_id",
        "review_batch_id",
        "normalized_event_id",
        "human_decision",
        "human_confidence",
        "duplicate_of_normalized_event_id",
        "source_file_hash",
        "is_valid",
        "validation_errors_json",
    },
    "l5_review_decision_current": {
        "normalized_event_id",
        "review_import_id",
        "review_batch_id",
        "human_decision",
        "source_file_hash",
        "current_hash",
    },
    "l5_review_decision_conflict_audit": {
        "conflict_id",
        "conflict_type",
        "normalized_event_id",
        "severity",
        "source_file_hash",
    },
    "l5_review_decision_run": {
        "run_id",
        "input_file",
        "source_file_hash",
        "source_mutation_detected",
        "input_mutation_detected",
        "stable_output_hashes_json",
    },
}


def add_problem(problems: list[str], condition: bool, message: str) -> None:
    if condition:
        problems.append(message)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not object_exists(conn, table, "table"):
        return []
    columns = list(table_columns(conn, table))
    order_sql = ", ".join(columns)
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_sql}")]


def validate_db(project_dir: Path, problems: list[str]) -> dict[str, Any]:
    db_path = project_dir / DB_RELATIVE_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(L5_4_TABLES).difference(tables)
        add_problem(problems, bool(missing), f"missing L5.4 tables: {sorted(missing)}")
        for table, required in REQUIRED_COLUMNS.items():
            if table in tables:
                missing_columns = required.difference(table_columns(conn, table))
                add_problem(problems, bool(missing_columns), f"{table} missing columns: {sorted(missing_columns)}")
        valid_ids = {str(row[0]) for row in conn.execute("SELECT normalized_event_candidate_id FROM l5_normalized_event_candidate")} if object_exists(conn, "l5_normalized_event_candidate", "table") else set()
        imports = fetch_rows(conn, "l5_review_decision_import")
        current = fetch_rows(conn, "l5_review_decision_current")
        conflicts = fetch_rows(conn, "l5_review_decision_conflict_audit")
        runs = fetch_rows(conn, "l5_review_decision_run")
        for row in imports:
            event_id = row.get("normalized_event_id", "")
            decision = row.get("human_decision", "")
            add_problem(problems, event_id not in valid_ids, f"import row does not link to L5.3: {event_id}")
            add_problem(problems, decision not in HUMAN_DECISIONS, f"invalid human_decision: {decision}")
            errors = json.loads(row.get("validation_errors_json") or "[]")
            if decision == "duplicate_candidate":
                add_problem(problems, not row.get("duplicate_of_normalized_event_id"), f"duplicate_candidate missing target: {event_id}")
                add_problem(problems, row.get("duplicate_of_normalized_event_id") == event_id, f"duplicate_candidate self target: {event_id}")
            if decision == "needs_context":
                add_problem(problems, not row.get("needs_context_reason"), f"needs_context missing reason: {event_id}")
            if decision == "rejected":
                add_problem(problems, not row.get("reject_reason") and not row.get("human_notes"), f"rejected missing reason or notes: {event_id}")
            add_problem(problems, bool(errors) and int(row.get("is_valid") or 0) == 1, f"invalid import marked valid: {event_id}")
        seen_current: set[str] = set()
        for row in current:
            event_id = row.get("normalized_event_id", "")
            add_problem(problems, event_id in seen_current, f"multiple current decisions for {event_id}")
            seen_current.add(event_id)
            add_problem(problems, event_id not in valid_ids, f"current row does not link to L5.3: {event_id}")
        latest = runs[-1] if runs else {}
        add_problem(problems, bool(latest) and int(latest.get("source_mutation_detected") or 0) != 0, "source_mutation_detected is not false")
        add_problem(problems, bool(latest) and int(latest.get("input_mutation_detected") or 0) != 0, "input_mutation_detected is not false")
        return {
            "import_count": len(imports),
            "current_count": len(current),
            "conflict_count": len(conflicts),
            "run_count": len(runs),
        }
    finally:
        conn.close()


def write_reports(out_dir: Path, result: dict[str, Any]) -> None:
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = [
        "# L5.4 Review Decision Intake Verification",
        "",
        f"- ok: {result['ok']}",
        f"- final_message: {result['final_message']}",
        f"- import_count: {result.get('import_count', 0)}",
        f"- current_count: {result.get('current_count', 0)}",
        f"- conflict_count: {result.get('conflict_count', 0)}",
        "",
        "## Problems",
        "",
    ]
    lines.extend(f"- {problem}" for problem in result["problems"]) if result["problems"] else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")


def verify_l5_review_decision_intake(
    project_dir: Path | str | None = None,
    *,
    input_file: Path | str = DEFAULT_INPUT_FILE,
    output_dir: Path | str = "outputs",
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    first = run_l5_review_decision_intake(root, input_file=input_file, output_dir=out_dir, rebuild=True)
    second = run_l5_review_decision_intake(root, input_file=input_file, output_dir=out_dir, rebuild=True)
    add_problem(problems, first["stable_output_hashes"] != second["stable_output_hashes"], "rebuild is not idempotent")
    add_problem(problems, bool(second["source_mutation_detected"]), "source mutation detected")
    add_problem(problems, bool(second["input_mutation_detected"]), "input mutation detected")
    add_problem(problems, second["validation"]["error_count"] != 0, "validation error_count is not zero")

    report = run_l5_review_decision_reporter(root, output_dir=out_dir)
    for filename in (
        REPORT_MD,
        SUMMARY_JSON,
        CURRENT_CSV,
        CURRENT_JSON,
        CONFLICT_CSV,
        CONFLICT_JSON,
        CONFLICT_MD,
        READINESS_CSV,
        READINESS_JSON,
        READINESS_REPORT,
    ):
        add_problem(problems, not (out_dir / filename).exists(), f"missing reporter output {filename}")
    add_problem(problems, report["row_counts"].get("readiness_row_count", 0) < 0, "readiness export cannot be generated")
    db_metrics = validate_db(root, problems)
    result = {
        "ok": not problems,
        "problems": problems,
        "final_message": PASS_MESSAGE if not problems else FAIL_MESSAGE,
        "source_mutation_detected": second["source_mutation_detected"],
        "input_mutation_detected": second["input_mutation_detected"],
        "validation": second["validation"],
        "reporter_row_counts": report.get("row_counts", {}),
        **db_metrics,
    }
    write_reports(out_dir, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5.4 review decision intake.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT_FILE)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_review_decision_intake(args.project_dir, input_file=args.input_file, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
