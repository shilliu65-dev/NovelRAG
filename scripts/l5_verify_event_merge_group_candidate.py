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
from scripts.l5_event_merge_group_candidate_indexer import L5_6_TABLES, run_l5_event_merge_group_candidate_indexer
from scripts.l5_event_merge_group_candidate_reporter import (
    AUDIT_CSV,
    AUDIT_JSON,
    CANDIDATE_CSV,
    CANDIDATE_JSON,
    EVIDENCE_CSV,
    EVIDENCE_JSON,
    MEMBER_CSV,
    MEMBER_JSON,
    REPORT_MD,
    run_l5_event_merge_group_candidate_reporter,
)


PASS_MESSAGE = "L5.6 event merge group candidate FULL PASS"
FAIL_MESSAGE = "L5.6 event merge group candidate FAIL"
VERIFY_JSON = "l5_event_merge_group_candidate_verify_report.json"
VERIFY_MD = "l5_event_merge_group_candidate_verify_report.md"
MANIFEST_JSON = "l5_event_merge_group_candidate_manifest.json"

REQUIRED_COLUMNS = {
    "l5_event_merge_group_candidate": {
        "merge_group_candidate_id",
        "representative_confirmed_event_candidate_id",
        "group_signature",
        "group_status",
        "group_rule",
        "member_count",
        "merge_group_hash",
    },
    "l5_event_merge_group_member": {
        "merge_group_member_id",
        "merge_group_candidate_id",
        "confirmed_event_candidate_id",
        "normalized_event_id",
        "member_role",
        "membership_rule",
        "membership_score",
    },
    "l5_event_merge_group_evidence": {
        "merge_group_evidence_id",
        "merge_group_candidate_id",
        "confirmed_event_candidate_id",
    },
    "l5_event_merge_group_audit": {
        "audit_id",
        "audit_type",
        "description",
        "severity",
    },
    "l5_event_merge_group_run": {
        "run_id",
        "source_mutation_detected",
        "input_mutation_detected",
        "run_hash",
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


def validate_db(project_dir: Path, problems: list[str]) -> dict[str, int]:
    conn = sqlite3.connect(project_dir / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing_tables = set(L5_6_TABLES).difference(tables)
        add_problem(problems, bool(missing_tables), f"missing L5.6 tables: {sorted(missing_tables)}")
        for table, required in REQUIRED_COLUMNS.items():
            if table in tables:
                missing_columns = required.difference(table_columns(conn, table))
                add_problem(problems, bool(missing_columns), f"{table} missing columns: {sorted(missing_columns)}")

        groups = fetch_rows(conn, "l5_event_merge_group_candidate")
        members = fetch_rows(conn, "l5_event_merge_group_member")
        runs = fetch_rows(conn, "l5_event_merge_group_run")
        confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
        group_ids = {str(row["merge_group_candidate_id"]) for row in groups}
        confirmed_ids = {str(row["confirmed_event_candidate_id"]) for row in confirmed}
        members_by_group: dict[str, list[dict[str, Any]]] = {}
        members_by_confirmed: dict[str, list[dict[str, Any]]] = {}
        for row in members:
            members_by_group.setdefault(str(row["merge_group_candidate_id"]), []).append(row)
            members_by_confirmed.setdefault(str(row["confirmed_event_candidate_id"]), []).append(row)
            add_problem(problems, str(row["confirmed_event_candidate_id"]) not in confirmed_ids, f"member missing L5.5 link: {row['confirmed_event_candidate_id']}")
            add_problem(problems, str(row["merge_group_candidate_id"]) not in group_ids, f"member missing group link: {row['merge_group_member_id']}")

        for row in confirmed:
            count = len(members_by_confirmed.get(str(row["confirmed_event_candidate_id"]), []))
            add_problem(problems, count != 1, f"confirmed candidate group membership count is {count}: {row['confirmed_event_candidate_id']}")
        for group in groups:
            group_id = str(group["merge_group_candidate_id"])
            actual_members = members_by_group.get(group_id, [])
            add_problem(problems, not actual_members, f"group has no members: {group_id}")
            add_problem(problems, int(group.get("member_count") or 0) != len(actual_members), f"group member_count mismatch: {group_id}")
            add_problem(problems, str(group.get("group_status") or "") != "merge_group_candidate", f"invalid group_status: {group_id}")
            representative = str(group["representative_confirmed_event_candidate_id"])
            add_problem(problems, representative not in {str(row["confirmed_event_candidate_id"]) for row in actual_members}, f"representative not group member: {group_id}")
            if int(group.get("member_count") or 0) > 1:
                add_problem(problems, not str(group.get("group_rule") or ""), f"multi-member group missing rule: {group_id}")
                add_problem(problems, any(row.get("membership_score") is None for row in actual_members), f"multi-member group missing score: {group_id}")
        forbidden = {"final_merged_event", "l5_merged_event", "final_event"}
        add_problem(problems, bool(forbidden.intersection(tables)), f"forbidden final merged event table exists: {sorted(forbidden.intersection(tables))}")
        latest = runs[-1] if runs else {}
        add_problem(problems, bool(latest) and int(latest.get("source_mutation_detected") or 0) != 0, "source_mutation_detected is not false")
        add_problem(problems, bool(latest) and int(latest.get("input_mutation_detected") or 0) != 0, "input_mutation_detected is not false")
        return {
            "merge_group_candidate_count": len(groups),
            "merge_group_member_count": len(members),
            "run_count": len(runs),
        }
    finally:
        conn.close()


def write_reports(out_dir: Path, result: dict[str, Any]) -> None:
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = [
        "# L5.6 Event Merge Group Candidate Verification",
        "",
        f"- ok: {result['ok']}",
        f"- final_message: {result['final_message']}",
        f"- merge_group_candidate_count: {result.get('merge_group_candidate_count', 0)}",
        f"- merge_group_member_count: {result.get('merge_group_member_count', 0)}",
        "",
        "## Problems",
        "",
    ]
    lines.extend(f"- {problem}" for problem in result["problems"]) if result["problems"] else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")


def verify_l5_event_merge_group_candidate(
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

    first = run_l5_event_merge_group_candidate_indexer(root, output_dir=out_dir, rebuild=True)
    second = run_l5_event_merge_group_candidate_indexer(root, output_dir=out_dir, rebuild=True)
    add_problem(problems, first["stable_output_hashes"] != second["stable_output_hashes"], "rebuild is not idempotent")
    add_problem(problems, bool(second["source_mutation_detected"]), "source mutation detected")
    add_problem(problems, bool(second["input_mutation_detected"]), "input mutation detected")
    report = run_l5_event_merge_group_candidate_reporter(root, output_dir=out_dir)
    for filename in (
        CANDIDATE_CSV,
        CANDIDATE_JSON,
        MEMBER_CSV,
        MEMBER_JSON,
        EVIDENCE_CSV,
        EVIDENCE_JSON,
        AUDIT_CSV,
        AUDIT_JSON,
        REPORT_MD,
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
    parser = argparse.ArgumentParser(description="Verify L5.6 event merge group candidates.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_event_merge_group_candidate(args.project_dir, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
