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
from scripts.l5_relationship_state_impact_candidate_indexer import L5_8_TABLES, run_l5_relationship_state_impact_candidate_indexer
from scripts.l5_relationship_state_impact_candidate_reporter import (
    AUDIT_CSV,
    AUDIT_JSON,
    EVIDENCE_CSV,
    EVIDENCE_JSON,
    RELATIONSHIP_CSV,
    RELATIONSHIP_JSON,
    REPORT_MD,
    STATE_CSV,
    STATE_JSON,
    run_l5_relationship_state_impact_candidate_reporter,
)


PASS_MESSAGE = "L5.8 relationship state impact candidate FULL PASS"
FAIL_MESSAGE = "L5.8 relationship state impact candidate FAIL"
VERIFY_JSON = "l5_relationship_state_impact_candidate_verify_report.json"
VERIFY_MD = "l5_relationship_state_impact_candidate_verify_report.md"
MANIFEST_JSON = "l5_relationship_state_impact_candidate_manifest.json"

REQUIRED_COLUMNS = {
    "l5_relationship_impact_candidate": {"relationship_impact_candidate_id", "merge_group_candidate_id", "source_confirmed_event_candidate_id", "relationship_impact_type", "relationship_impact_hash"},
    "l5_state_impact_candidate": {"state_impact_candidate_id", "merge_group_candidate_id", "source_confirmed_event_candidate_id", "state_impact_type", "state_impact_hash"},
    "l5_impact_candidate_evidence": {"impact_evidence_id", "impact_candidate_type", "impact_candidate_id", "source_confirmed_event_candidate_id"},
    "l5_relationship_state_impact_audit": {"audit_id", "audit_type", "description", "severity"},
    "l5_relationship_state_impact_run": {"run_id", "source_mutation_detected", "input_mutation_detected", "run_hash"},
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


def validate_db(project_dir: Path, problems: list[str]) -> dict[str, int]:
    conn = sqlite3.connect(project_dir / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing_tables = set(L5_8_TABLES).difference(tables)
        add_problem(problems, bool(missing_tables), f"missing L5.8 tables: {sorted(missing_tables)}")
        for table, required in REQUIRED_COLUMNS.items():
            if table in tables:
                missing_columns = required.difference(table_columns(conn, table))
                add_problem(problems, bool(missing_columns), f"{table} missing columns: {sorted(missing_columns)}")
        relationships = fetch_rows(conn, "l5_relationship_impact_candidate")
        states = fetch_rows(conn, "l5_state_impact_candidate")
        evidence = fetch_rows(conn, "l5_impact_candidate_evidence")
        runs = fetch_rows(conn, "l5_relationship_state_impact_run")
        groups = fetch_rows(conn, "l5_event_merge_group_candidate")
        confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
        anchors = fetch_rows(conn, "l5_timeline_anchor_candidate")
        group_ids = {str(row["merge_group_candidate_id"]) for row in groups}
        confirmed_ids = {str(row["confirmed_event_candidate_id"]) for row in confirmed}
        anchor_ids = {str(row["timeline_anchor_candidate_id"]) for row in anchors}
        relationship_ids = {str(row["relationship_impact_candidate_id"]) for row in relationships}
        state_ids = {str(row["state_impact_candidate_id"]) for row in states}
        for row in relationships:
            add_problem(problems, str(row["merge_group_candidate_id"]) not in group_ids, f"relationship missing L5.6 link: {row['relationship_impact_candidate_id']}")
            add_problem(problems, str(row["source_confirmed_event_candidate_id"]) not in confirmed_ids, f"relationship missing L5.5 link: {row['relationship_impact_candidate_id']}")
            anchor_id = str(row.get("timeline_anchor_candidate_id") or "")
            add_problem(problems, bool(anchor_id) and anchor_id not in anchor_ids, f"relationship missing L5.7 link: {row['relationship_impact_candidate_id']}")
            add_problem(problems, not str(row.get("relationship_impact_hash") or ""), f"relationship hash missing: {row['relationship_impact_candidate_id']}")
        for row in states:
            add_problem(problems, str(row["merge_group_candidate_id"]) not in group_ids, f"state missing L5.6 link: {row['state_impact_candidate_id']}")
            add_problem(problems, str(row["source_confirmed_event_candidate_id"]) not in confirmed_ids, f"state missing L5.5 link: {row['state_impact_candidate_id']}")
            anchor_id = str(row.get("timeline_anchor_candidate_id") or "")
            add_problem(problems, bool(anchor_id) and anchor_id not in anchor_ids, f"state missing L5.7 link: {row['state_impact_candidate_id']}")
            add_problem(problems, not str(row.get("state_impact_hash") or ""), f"state hash missing: {row['state_impact_candidate_id']}")
        for row in evidence:
            kind = str(row["impact_candidate_type"])
            impact_id = str(row["impact_candidate_id"])
            add_problem(problems, kind == "relationship" and impact_id not in relationship_ids, f"impact evidence missing relationship link: {row['impact_evidence_id']}")
            add_problem(problems, kind == "state" and impact_id not in state_ids, f"impact evidence missing state link: {row['impact_evidence_id']}")
        forbidden = {"final_relationship_graph", "relationship_graph", "l5_final_relationship_graph", "final_state_machine", "state_machine", "l5_final_state_machine"}
        add_problem(problems, bool(forbidden.intersection(tables)), f"forbidden final impact table exists: {sorted(forbidden.intersection(tables))}")
        latest = runs[-1] if runs else {}
        add_problem(problems, bool(latest) and int(latest.get("source_mutation_detected") or 0) != 0, "source_mutation_detected is not false")
        add_problem(problems, bool(latest) and int(latest.get("input_mutation_detected") or 0) != 0, "input_mutation_detected is not false")
        return {
            "relationship_impact_candidate_count": len(relationships),
            "state_impact_candidate_count": len(states),
            "impact_evidence_count": len(evidence),
            "run_count": len(runs),
        }
    finally:
        conn.close()


def write_reports(out_dir: Path, result: dict[str, Any]) -> None:
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = [
        "# L5.8 Relationship / State Impact Candidate Verification",
        "",
        f"- ok: {result['ok']}",
        f"- final_message: {result['final_message']}",
        f"- relationship_impact_candidate_count: {result.get('relationship_impact_candidate_count', 0)}",
        f"- state_impact_candidate_count: {result.get('state_impact_candidate_count', 0)}",
        f"- impact_evidence_count: {result.get('impact_evidence_count', 0)}",
        "",
        "## Problems",
        "",
    ]
    lines.extend(f"- {problem}" for problem in result["problems"]) if result["problems"] else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")


def verify_l5_relationship_state_impact_candidate(
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
    first = run_l5_relationship_state_impact_candidate_indexer(root, output_dir=out_dir, rebuild=True)
    second = run_l5_relationship_state_impact_candidate_indexer(root, output_dir=out_dir, rebuild=True)
    add_problem(problems, first["stable_output_hashes"] != second["stable_output_hashes"], "rebuild is not idempotent")
    add_problem(problems, bool(second["source_mutation_detected"]), "source mutation detected")
    add_problem(problems, bool(second["input_mutation_detected"]), "input mutation detected")
    report = run_l5_relationship_state_impact_candidate_reporter(root, output_dir=out_dir)
    for filename in (RELATIONSHIP_CSV, RELATIONSHIP_JSON, STATE_CSV, STATE_JSON, EVIDENCE_CSV, EVIDENCE_JSON, AUDIT_CSV, AUDIT_JSON, REPORT_MD, MANIFEST_JSON):
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
    parser = argparse.ArgumentParser(description="Verify L5.8 relationship/state impact candidates.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_relationship_state_impact_candidate(args.project_dir, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
