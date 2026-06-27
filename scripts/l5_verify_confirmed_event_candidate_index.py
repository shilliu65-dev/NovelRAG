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
from scripts.l5_confirmed_event_candidate_indexer import L5_3_SOURCE_TABLES, L5_4_INPUT_TABLES, L5_5_TABLES, run_l5_confirmed_event_candidate_indexer
from scripts.l5_confirmed_event_candidate_reporter import (
    ARGUMENT_CSV,
    ARGUMENT_JSON,
    BLOCKED_CSV,
    BLOCKED_JSON,
    CONFIRMED_CSV,
    CONFIRMED_JSON,
    EVIDENCE_CSV,
    EVIDENCE_JSON,
    REPORT_MD,
    run_l5_confirmed_event_candidate_reporter,
)
from scripts.l5_event_candidate_review_exporter import object_exists


PASS_MESSAGE = "L5.5 confirmed event candidate index FULL PASS"
FAIL_MESSAGE = "L5.5 confirmed event candidate index FAIL"
VERIFY_JSON = "l5_confirmed_event_candidate_verify_report.json"
VERIFY_MD = "l5_confirmed_event_candidate_verify_report.md"
MANIFEST_JSON = "l5_confirmed_event_candidate_manifest.json"

REQUIRED_COLUMNS = {
    "l5_confirmed_event_candidate": {
        "confirmed_event_candidate_id",
        "normalized_event_id",
        "current_decision_id",
        "review_batch_id",
        "review_source_file_hash",
        "confirmation_status",
        "confirmed_candidate_hash",
    },
    "l5_confirmed_event_argument_candidate": {
        "confirmed_argument_candidate_id",
        "confirmed_event_candidate_id",
        "normalized_argument_id",
        "normalized_event_id",
    },
    "l5_confirmed_event_evidence_span": {
        "confirmed_evidence_span_id",
        "confirmed_event_candidate_id",
        "normalized_evidence_id",
        "normalized_event_id",
    },
    "l5_confirmed_event_blocked_audit": {
        "blocked_audit_id",
        "normalized_event_id",
        "block_reason",
    },
    "l5_confirmed_event_candidate_run": {
        "run_id",
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
        missing = set(L5_5_TABLES).difference(tables)
        add_problem(problems, bool(missing), f"missing L5.5 tables: {sorted(missing)}")
        for table, required in REQUIRED_COLUMNS.items():
            if table in tables:
                missing_columns = required.difference(table_columns(conn, table))
                add_problem(problems, bool(missing_columns), f"{table} missing columns: {sorted(missing_columns)}")

        confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
        arguments = fetch_rows(conn, "l5_confirmed_event_argument_candidate")
        evidences = fetch_rows(conn, "l5_confirmed_event_evidence_span")
        blocked = fetch_rows(conn, "l5_confirmed_event_blocked_audit")
        runs = fetch_rows(conn, "l5_confirmed_event_candidate_run")
        current = fetch_rows(conn, "l5_review_decision_current")
        conflicts = fetch_rows(conn, "l5_review_decision_conflict_audit")
        valid_events = {str(row[0]) for row in conn.execute("SELECT normalized_event_candidate_id FROM l5_normalized_event_candidate")}
        valid_current_ids = {str(row["review_import_id"]) for row in current}
        current_by_id = {str(row["review_import_id"]): row for row in current}
        conflict_ids = {str(row["normalized_event_id"]) for row in conflicts if str(row.get("severity") or "") == "error"}

        confirmed_ids: set[str] = set()
        confirmed_normalized_ids: set[str] = set()
        for row in confirmed:
            confirmed_id = str(row.get("confirmed_event_candidate_id") or "")
            normalized_event_id = str(row.get("normalized_event_id") or "")
            current_decision_id = str(row.get("current_decision_id") or "")
            add_problem(problems, confirmed_id in confirmed_ids, f"duplicate confirmed_event_candidate_id: {confirmed_id}")
            add_problem(problems, normalized_event_id in confirmed_normalized_ids, f"duplicate normalized_event_id in confirmed: {normalized_event_id}")
            add_problem(problems, normalized_event_id not in valid_events, f"confirmed row missing L5.3 source: {normalized_event_id}")
            add_problem(problems, current_decision_id not in valid_current_ids, f"confirmed row missing L5.4 current decision: {normalized_event_id}")
            add_problem(problems, current_by_id.get(current_decision_id, {}).get("human_decision") != "approved_candidate", f"confirmed row not backed by approved_candidate: {normalized_event_id}")
            add_problem(problems, normalized_event_id in conflict_ids, f"conflicted normalized_event_id entered confirmed: {normalized_event_id}")
            add_problem(problems, str(row.get("confirmation_status") or "") != "confirmed_candidate", f"invalid confirmation_status: {normalized_event_id}")
            add_problem(problems, not str(row.get("confirmed_candidate_hash") or ""), f"confirmed_candidate_hash missing: {normalized_event_id}")
            confirmed_ids.add(confirmed_id)
            confirmed_normalized_ids.add(normalized_event_id)

        disallowed_decisions = {"duplicate_candidate", "rejected", "needs_context", "uncertain", "weak_candidate"}
        for row in confirmed:
            decision_row = current_by_id.get(str(row["current_decision_id"]), {})
            add_problem(problems, str(decision_row.get("human_decision") or "") in disallowed_decisions, f"blocked decision entered confirmed: {row['normalized_event_id']}")

        for row in arguments:
            add_problem(problems, str(row.get("confirmed_event_candidate_id") or "") not in confirmed_ids, f"argument missing confirmed_event_candidate_id link: {row.get('confirmed_argument_candidate_id')}")
        for row in evidences:
            add_problem(problems, str(row.get("confirmed_event_candidate_id") or "") not in confirmed_ids, f"evidence missing confirmed_event_candidate_id link: {row.get('confirmed_evidence_span_id')}")

        blocked_by_event = {str(row["normalized_event_id"]): row for row in blocked}
        for event_id in valid_events:
            if event_id not in confirmed_normalized_ids:
                add_problem(problems, event_id not in blocked_by_event, f"non-confirmed normalized event missing blocked audit row: {event_id}")

        latest = runs[-1] if runs else {}
        add_problem(problems, bool(latest) and int(latest.get("source_mutation_detected") or 0) != 0, "source_mutation_detected is not false")
        add_problem(problems, bool(latest) and int(latest.get("input_mutation_detected") or 0) != 0, "input_mutation_detected is not false")
        return {
            "confirmed_count": len(confirmed),
            "argument_count": len(arguments),
            "evidence_count": len(evidences),
            "blocked_count": len(blocked),
            "run_count": len(runs),
        }
    finally:
        conn.close()


def write_reports(out_dir: Path, result: dict[str, Any]) -> None:
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = [
        "# L5.5 Confirmed Event Candidate Verification",
        "",
        f"- ok: {result['ok']}",
        f"- final_message: {result['final_message']}",
        f"- confirmed_count: {result.get('confirmed_count', 0)}",
        f"- argument_count: {result.get('argument_count', 0)}",
        f"- evidence_count: {result.get('evidence_count', 0)}",
        f"- blocked_count: {result.get('blocked_count', 0)}",
        "",
        "## Problems",
        "",
    ]
    lines.extend(f"- {problem}" for problem in result["problems"]) if result["problems"] else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")


def verify_l5_confirmed_event_candidate_index(
    project_dir: Path | str | None = None,
    *,
    output_dir: Path | str = "outputs",
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []

    first = run_l5_confirmed_event_candidate_indexer(root, output_dir=out_dir, rebuild=True)
    second = run_l5_confirmed_event_candidate_indexer(root, output_dir=out_dir, rebuild=True)
    add_problem(problems, first["stable_output_hashes"] != second["stable_output_hashes"], "rebuild is not idempotent")
    add_problem(problems, bool(second["source_mutation_detected"]), "source mutation detected")
    add_problem(problems, bool(second["input_mutation_detected"]), "input mutation detected")

    report = run_l5_confirmed_event_candidate_reporter(root, output_dir=out_dir)
    for filename in (
        REPORT_MD,
        CONFIRMED_CSV,
        CONFIRMED_JSON,
        ARGUMENT_CSV,
        ARGUMENT_JSON,
        EVIDENCE_CSV,
        EVIDENCE_JSON,
        BLOCKED_CSV,
        BLOCKED_JSON,
        MANIFEST_JSON,
    ):
        add_problem(problems, not (out_dir / filename).exists(), f"missing reporter/index output {filename}")

    db_metrics = validate_db(root, problems)
    result = {
        "ok": not problems,
        "problems": problems,
        "final_message": PASS_MESSAGE if not problems else FAIL_MESSAGE,
        "source_mutation_detected": second["source_mutation_detected"],
        "input_mutation_detected": second["input_mutation_detected"],
        "reporter_row_counts": report.get("row_counts", {}),
        **db_metrics,
    }
    write_reports(out_dir, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5.5 confirmed event candidate index.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_confirmed_event_candidate_index(args.project_dir, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
