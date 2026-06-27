from __future__ import annotations

import argparse
import csv
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
from scripts.l5_event_merge_group_candidate_indexer import CREATED_AT, MANIFEST_JSON


CANDIDATE_CSV = "l5_event_merge_group_candidates.csv"
CANDIDATE_JSON = "l5_event_merge_group_candidates.json"
MEMBER_CSV = "l5_event_merge_group_members.csv"
MEMBER_JSON = "l5_event_merge_group_members.json"
EVIDENCE_CSV = "l5_event_merge_group_evidence.csv"
EVIDENCE_JSON = "l5_event_merge_group_evidence.json"
AUDIT_CSV = "l5_event_merge_group_audit.csv"
AUDIT_JSON = "l5_event_merge_group_audit.json"
REPORT_MD = "l5_event_merge_group_candidate_report.md"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not object_exists(conn, table, "table"):
        return []
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    order_sql = ", ".join(columns)
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_sql}")]


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# L5.6 Event Merge Group Candidate Report",
        "",
        "## Summary",
        "",
        f"- merge_group_candidate_count: {manifest['row_counts']['merge_group_candidate_count']}",
        f"- merge_group_member_count: {manifest['row_counts']['merge_group_member_count']}",
        f"- singleton_group_count: {manifest['row_counts']['singleton_group_count']}",
        f"- multi_member_group_count: {manifest['row_counts']['multi_member_group_count']}",
        f"- audit_count: {manifest['row_counts']['audit_count']}",
        "",
        "## Audit Type Counts",
        "",
    ]
    audit_counts = manifest["audit_type_counts"]
    lines.extend(f"- {key}: {value}" for key, value in audit_counts.items()) if audit_counts else lines.append("- none")
    lines.extend(["", "## Output Files", ""])
    lines.extend(f"- {item}" for item in manifest["output_files"])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_event_merge_group_candidate_reporter(
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
    db_path = root / DB_RELATIVE_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        candidates = fetch_rows(conn, "l5_event_merge_group_candidate")
        members = fetch_rows(conn, "l5_event_merge_group_member")
        evidence = fetch_rows(conn, "l5_event_merge_group_evidence")
        audits = fetch_rows(conn, "l5_event_merge_group_audit")
        runs = fetch_rows(conn, "l5_event_merge_group_run")
    finally:
        conn.close()

    candidate_columns = list(candidates[0].keys()) if candidates else ["merge_group_candidate_id", "representative_confirmed_event_candidate_id", "group_status", "group_rule", "member_count"]
    member_columns = list(members[0].keys()) if members else ["merge_group_member_id", "merge_group_candidate_id", "confirmed_event_candidate_id", "member_role", "membership_score"]
    evidence_columns = list(evidence[0].keys()) if evidence else ["merge_group_evidence_id", "merge_group_candidate_id", "confirmed_event_candidate_id", "confirmed_evidence_id"]
    audit_columns = list(audits[0].keys()) if audits else ["audit_id", "confirmed_event_candidate_id", "merge_group_candidate_id", "audit_type", "severity"]

    write_csv(out_dir / CANDIDATE_CSV, candidates, candidate_columns)
    write_csv(out_dir / MEMBER_CSV, members, member_columns)
    write_csv(out_dir / EVIDENCE_CSV, evidence, evidence_columns)
    write_csv(out_dir / AUDIT_CSV, audits, audit_columns)
    write_json(out_dir / CANDIDATE_JSON, {"export_name": "l5_event_merge_group_candidate", "created_at": CREATED_AT, "row_count": len(candidates), "columns": candidate_columns, "rows": candidates})
    write_json(out_dir / MEMBER_JSON, {"export_name": "l5_event_merge_group_member", "created_at": CREATED_AT, "row_count": len(members), "columns": member_columns, "rows": members})
    write_json(out_dir / EVIDENCE_JSON, {"export_name": "l5_event_merge_group_evidence", "created_at": CREATED_AT, "row_count": len(evidence), "columns": evidence_columns, "rows": evidence})
    write_json(out_dir / AUDIT_JSON, {"export_name": "l5_event_merge_group_audit", "created_at": CREATED_AT, "row_count": len(audits), "columns": audit_columns, "rows": audits})

    singleton_count = sum(1 for row in candidates if int(row.get("member_count") or 0) == 1)
    multi_count = sum(1 for row in candidates if int(row.get("member_count") or 0) > 1)
    manifest = {
        "export_layer": "L5.6 Event Merge Group Candidate",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": {
            "merge_group_candidate_count": len(candidates),
            "merge_group_member_count": len(members),
            "merge_group_evidence_count": len(evidence),
            "singleton_group_count": singleton_count,
            "multi_member_group_count": multi_count,
            "audit_count": len(audits),
            "run_count": len(runs),
        },
        "audit_type_counts": dict(sorted(Counter(str(row.get("audit_type") or "") for row in audits).items())),
        "output_files": [
            str(out_dir / CANDIDATE_CSV),
            str(out_dir / CANDIDATE_JSON),
            str(out_dir / MEMBER_CSV),
            str(out_dir / MEMBER_JSON),
            str(out_dir / EVIDENCE_CSV),
            str(out_dir / EVIDENCE_JSON),
            str(out_dir / AUDIT_CSV),
            str(out_dir / AUDIT_JSON),
            str(out_dir / REPORT_MD),
            str(out_dir / MANIFEST_JSON),
        ],
    }
    write_report(out_dir / REPORT_MD, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export L5.6 event merge group candidate reports.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_event_merge_group_candidate_reporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.6 merge group candidate rows: {manifest['row_counts']['merge_group_candidate_count']}")


if __name__ == "__main__":
    main()
