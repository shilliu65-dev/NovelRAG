from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import object_exists
from scripts.l5_event_understanding_quality_gate import FORBIDDEN_FINAL_TABLES, L5_9_TABLES, run_l5_event_understanding_quality_gate
from scripts.l5_event_understanding_quality_gate_reporter import (
    CHAIN_CSV,
    CHAIN_JSON,
    ISSUES_CSV,
    ISSUES_JSON,
    METRICS_CSV,
    METRICS_JSON,
    SUMMARY_JSON,
    SUMMARY_MD,
    run_l5_event_understanding_quality_gate_reporter,
)


PASS_MESSAGE = "L5.9 event understanding quality gate FULL PASS"
FAIL_MESSAGE = "L5.9 event understanding quality gate FAIL"
VERIFY_JSON = "l5_event_quality_gate_verify_report.json"
VERIFY_MD = "l5_event_quality_gate_verify_report.md"
MANIFEST_JSON = "l5_event_quality_gate_manifest.json"

REQUIRED_COLUMNS = {
    "l5_event_quality_gate_summary": {"quality_gate_run_id", "confirmed_event_candidate_count", "quality_gate_status", "run_hash"},
    "l5_event_quality_gate_chain": {"chain_id", "normalized_event_id", "confirmed_event_candidate_id", "merge_group_candidate_id", "timeline_anchor_candidate_id", "chain_status", "readiness_status", "chain_hash"},
    "l5_event_quality_gate_issue": {"issue_id", "issue_scope", "issue_type", "issue_severity", "description", "issue_hash"},
    "l5_event_quality_gate_metric": {"metric_id", "metric_name", "metric_value", "metric_group"},
    "l5_event_quality_gate_run": {"run_id", "source_mutation_detected", "input_mutation_detected", "forbidden_final_table_count", "quality_gate_status", "error_count", "warning_count", "info_count", "run_hash"},
}


def add_problem(problems: list[str], condition: bool, message: str) -> None:
    if condition:
        problems.append(message)


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not object_exists(conn, table, "table"):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not object_exists(conn, table, "table"):
        return []
    columns = list(table_columns(conn, table))
    order_sql = ", ".join(columns)
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_sql}")]


def validate_db(project_dir: Path, problems: list[str]) -> dict[str, int]:
    conn = sqlite3.connect(project_dir / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing_tables = set(L5_9_TABLES).difference(tables)
        add_problem(problems, bool(missing_tables), f"missing L5.9 tables: {sorted(missing_tables)}")
        for table, required in REQUIRED_COLUMNS.items():
            if table in tables:
                missing_columns = required.difference(table_columns(conn, table))
                add_problem(problems, bool(missing_columns), f"{table} missing columns: {sorted(missing_columns)}")
        summaries = fetch_rows(conn, "l5_event_quality_gate_summary")
        chains = fetch_rows(conn, "l5_event_quality_gate_chain")
        issues = fetch_rows(conn, "l5_event_quality_gate_issue")
        metrics = fetch_rows(conn, "l5_event_quality_gate_metric")
        runs = fetch_rows(conn, "l5_event_quality_gate_run")
        confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
        groups = fetch_rows(conn, "l5_event_merge_group_candidate")
        anchors = fetch_rows(conn, "l5_timeline_anchor_candidate")
        confirmed_ids = {str(row["confirmed_event_candidate_id"]) for row in confirmed if row.get("confirmed_event_candidate_id")}
        group_ids = {str(row["merge_group_candidate_id"]) for row in groups if row.get("merge_group_candidate_id")}
        anchor_ids = {str(row["timeline_anchor_candidate_id"]) for row in anchors if row.get("timeline_anchor_candidate_id")}
        chain_confirmed = {str(row["confirmed_event_candidate_id"]) for row in chains if row.get("confirmed_event_candidate_id")}
        add_problem(problems, len(summaries) != 1, f"summary row count is {len(summaries)}")
        for confirmed_id in confirmed_ids:
            add_problem(problems, confirmed_id not in chain_confirmed, f"confirmed candidate missing chain: {confirmed_id}")
        for row in chains:
            confirmed_id = str(row.get("confirmed_event_candidate_id") or "")
            group_id = str(row.get("merge_group_candidate_id") or "")
            anchor_id = str(row.get("timeline_anchor_candidate_id") or "")
            add_problem(problems, bool(confirmed_id) and confirmed_id not in confirmed_ids, f"chain confirmed link missing: {row['chain_id']}")
            add_problem(problems, bool(group_id) and group_id not in group_ids, f"chain merge group link missing: {row['chain_id']}")
            add_problem(problems, bool(anchor_id) and anchor_id not in anchor_ids, f"chain timeline anchor link missing: {row['chain_id']}")
        issue_counts = Counter(str(row["issue_severity"]) for row in issues)
        latest_run = runs[-1] if runs else {}
        add_problem(problems, int(latest_run.get("error_count") or 0) != issue_counts.get("error", 0), "run error_count does not match issue table")
        add_problem(problems, int(latest_run.get("warning_count") or 0) != issue_counts.get("warning", 0), "run warning_count does not match issue table")
        add_problem(problems, int(latest_run.get("info_count") or 0) != issue_counts.get("info", 0), "run info_count does not match issue table")
        metric_groups = {str(row["metric_group"]) for row in metrics}
        add_problem(problems, not {"count", "ratio", "readiness", "mutation", "forbidden_table_check"}.issubset(metric_groups), f"missing metric groups: {sorted({'count', 'ratio', 'readiness', 'mutation', 'forbidden_table_check'}.difference(metric_groups))}")
        forbidden_metric = next((row for row in metrics if row.get("metric_name") == "forbidden_final_table_count"), {})
        add_problem(problems, int(forbidden_metric.get("metric_value") or -1) != 0, "forbidden final table metric is not zero")
        add_problem(problems, bool(FORBIDDEN_FINAL_TABLES.intersection(tables)), f"forbidden final tables exist: {sorted(FORBIDDEN_FINAL_TABLES.intersection(tables))}")
        add_problem(problems, bool(latest_run) and int(latest_run.get("source_mutation_detected") or 0) != 0, "source_mutation_detected is not false")
        add_problem(problems, bool(latest_run) and int(latest_run.get("input_mutation_detected") or 0) != 0, "input_mutation_detected is not false")
        if summaries:
            add_problem(problems, str(summaries[-1].get("quality_gate_status") or "") == "quality_fail", "quality_gate_status is quality_fail")
        return {
            "summary_count": len(summaries),
            "chain_count": len(chains),
            "issue_count": len(issues),
            "metric_count": len(metrics),
            "run_count": len(runs),
        }
    finally:
        conn.close()


def write_reports(out_dir: Path, result: dict[str, Any]) -> None:
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = [
        "# L5.9 Event Understanding Quality Gate Verification",
        "",
        f"- ok: {result['ok']}",
        f"- final_message: {result['final_message']}",
        f"- summary_count: {result.get('summary_count', 0)}",
        f"- chain_count: {result.get('chain_count', 0)}",
        f"- issue_count: {result.get('issue_count', 0)}",
        f"- metric_count: {result.get('metric_count', 0)}",
        "",
        "## Problems",
        "",
    ]
    lines.extend(f"- {problem}" for problem in result["problems"]) if result["problems"] else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")


def verify_l5_event_understanding_quality_gate(
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
    first = run_l5_event_understanding_quality_gate(root, output_dir=out_dir, rebuild=True)
    second = run_l5_event_understanding_quality_gate(root, output_dir=out_dir, rebuild=True)
    add_problem(problems, first["stable_output_hashes"] != second["stable_output_hashes"], "rebuild is not idempotent")
    add_problem(problems, bool(second["source_mutation_detected"]), "source mutation detected")
    add_problem(problems, bool(second["input_mutation_detected"]), "input mutation detected")
    report = run_l5_event_understanding_quality_gate_reporter(root, output_dir=out_dir)
    for filename in (SUMMARY_JSON, SUMMARY_MD, CHAIN_CSV, CHAIN_JSON, ISSUES_CSV, ISSUES_JSON, METRICS_CSV, METRICS_JSON, MANIFEST_JSON):
        add_problem(problems, not (out_dir / filename).exists(), f"missing reporter/index output {filename}")
    db_metrics = validate_db(root, problems)
    result = {
        "ok": not problems,
        "problems": problems,
        "final_message": PASS_MESSAGE if not problems else FAIL_MESSAGE,
        "source_mutation_detected": second["source_mutation_detected"],
        "input_mutation_detected": second["input_mutation_detected"],
        "quality_gate_status": second["quality_gate_status"],
        "forbidden_final_table_count": second["forbidden_final_table_count"],
        "reporter_row_counts": report.get("row_counts", {}),
        **db_metrics,
    }
    write_reports(out_dir, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5.9 event understanding quality gate.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_event_understanding_quality_gate(args.project_dir, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
