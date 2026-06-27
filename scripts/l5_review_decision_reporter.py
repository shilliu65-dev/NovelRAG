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
from scripts.l5_review_decision_intake import CREATED_AT, L5_4_TABLES, MANIFEST_JSON


SUMMARY_JSON = "l5_review_decision_summary.json"
REPORT_MD = "l5_review_decision_report.md"
CURRENT_CSV = "l5_review_decision_current.csv"
CURRENT_JSON = "l5_review_decision_current.json"
CONFLICT_CSV = "l5_review_decision_conflict_audit.csv"
CONFLICT_JSON = "l5_review_decision_conflict_audit.json"
CONFLICT_MD = "l5_review_decision_conflict_audit.md"
READINESS_CSV = "l5_confirmed_event_candidate_readiness.csv"
READINESS_JSON = "l5_confirmed_event_candidate_readiness.json"
READINESS_REPORT = "l5_confirmed_event_candidate_readiness_report.md"


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


def fetch_l5_3_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not object_exists(conn, "l5_normalized_event_candidate", "table"):
        return []
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT normalized_event_candidate_id, chapter_num, scene_block_id, l5_2_event_type,
                   l5_2_event_subtype, subject_text, normalized_confidence_score, evidence_backcut_status
            FROM l5_normalized_event_candidate
            ORDER BY chapter_num, normalized_event_candidate_id
            """
        )
    ]


def readiness_for(decision: str | None, has_conflict: bool) -> str:
    if has_conflict:
        return "blocked_by_conflict"
    if decision is None:
        return "missing_review_decision"
    return {
        "approved_candidate": "ready_for_l5_5",
        "duplicate_candidate": "blocked_by_duplicate",
        "rejected": "blocked_by_rejected",
        "needs_context": "blocked_by_needs_context",
        "uncertain": "blocked_by_uncertain",
        "weak_candidate": "blocked_by_weak_candidate",
    }.get(decision, "blocked_by_conflict")


def build_readiness_rows(events: list[dict[str, Any]], current_rows: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    current_by_id = {row["normalized_event_id"]: row for row in current_rows}
    conflicted = {row["normalized_event_id"] for row in conflicts if row.get("severity") == "error"}
    rows: list[dict[str, Any]] = []
    for event in events:
        event_id = event["normalized_event_candidate_id"]
        current = current_by_id.get(event_id)
        decision = current.get("human_decision") if current else None
        rows.append(
            {
                "normalized_event_id": event_id,
                "chapter_num": event.get("chapter_num", ""),
                "scene_block_id": event.get("scene_block_id", ""),
                "event_type": event.get("l5_2_event_type", ""),
                "event_subtype": event.get("l5_2_event_subtype", ""),
                "subject_text": event.get("subject_text", ""),
                "human_decision": decision or "",
                "human_confidence": current.get("human_confidence", "") if current else "",
                "duplicate_of_normalized_event_id": current.get("duplicate_of_normalized_event_id", "") if current else "",
                "readiness_status": readiness_for(decision, event_id in conflicted),
                "readiness_reason": "current decision approved and no conflict" if decision == "approved_candidate" and event_id not in conflicted else "",
            }
        )
    return rows


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# L5.4 Review Decision Intake Report",
        "",
        "## Summary",
        "",
        f"- imported_decision_count: {manifest['row_counts']['imported_decision_count']}",
        f"- current_decision_count: {manifest['row_counts']['current_decision_count']}",
        f"- conflict_count: {manifest['row_counts']['conflict_count']}",
        f"- readiness_row_count: {manifest['row_counts']['readiness_row_count']}",
        "",
        "## Readiness Status Counts",
        "",
    ]
    readiness = manifest["readiness_status_counts"]
    lines.extend(f"- {key}: {value}" for key, value in readiness.items()) if readiness else lines.append("- none")
    lines.extend(["", "## Output Files", ""])
    lines.extend(f"- {item}" for item in manifest["output_files"])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_conflict_report(path: Path, conflicts: list[dict[str, Any]]) -> None:
    counts = Counter(row.get("conflict_type", "") for row in conflicts)
    lines = ["# L5.4 Review Decision Conflict Audit", "", f"- conflict_count: {len(conflicts)}", "", "## Conflict Type Counts", ""]
    lines.extend(f"- {key}: {value}" for key, value in sorted(counts.items())) if counts else lines.append("- none")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_readiness_report(path: Path, rows: list[dict[str, Any]]) -> None:
    counts = Counter(row.get("readiness_status", "") for row in rows)
    lines = ["# L5.5 Confirmed Event Candidate Readiness", "", f"- row_count: {len(rows)}", "", "## Readiness Status Counts", ""]
    lines.extend(f"- {key}: {value}" for key, value in sorted(counts.items())) if counts else lines.append("- none")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_review_decision_reporter(
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
        imports = fetch_rows(conn, "l5_review_decision_import")
        current = fetch_rows(conn, "l5_review_decision_current")
        conflicts = fetch_rows(conn, "l5_review_decision_conflict_audit")
        runs = fetch_rows(conn, "l5_review_decision_run")
        events = fetch_l5_3_events(conn)
    finally:
        conn.close()

    readiness_rows = build_readiness_rows(events, current, conflicts)
    current_columns = list(current[0].keys()) if current else [
        "normalized_event_id",
        "review_import_id",
        "review_batch_id",
        "human_decision",
        "human_confidence",
        "duplicate_of_normalized_event_id",
        "source_file_hash",
    ]
    conflict_columns = list(conflicts[0].keys()) if conflicts else [
        "conflict_id",
        "conflict_type",
        "normalized_event_id",
        "related_normalized_event_id",
        "severity",
        "message",
        "review_batch_id",
        "source_file_hash",
        "created_at",
    ]
    readiness_columns = list(readiness_rows[0].keys()) if readiness_rows else [
        "normalized_event_id",
        "chapter_num",
        "scene_block_id",
        "event_type",
        "event_subtype",
        "subject_text",
        "human_decision",
        "human_confidence",
        "duplicate_of_normalized_event_id",
        "readiness_status",
        "readiness_reason",
    ]
    write_csv(out_dir / CURRENT_CSV, current, current_columns)
    write_csv(out_dir / CONFLICT_CSV, conflicts, conflict_columns)
    write_csv(out_dir / READINESS_CSV, readiness_rows, readiness_columns)
    write_json(out_dir / CURRENT_JSON, {"export_name": "l5_review_decision_current", "created_at": CREATED_AT, "row_count": len(current), "columns": current_columns, "rows": current})
    write_json(out_dir / CONFLICT_JSON, {"export_name": "l5_review_decision_conflict_audit", "created_at": CREATED_AT, "row_count": len(conflicts), "columns": conflict_columns, "rows": conflicts})
    write_json(out_dir / READINESS_JSON, {"export_name": "l5_confirmed_event_candidate_readiness", "created_at": CREATED_AT, "row_count": len(readiness_rows), "columns": readiness_columns, "rows": readiness_rows})

    manifest = {
        "export_layer": "L5.4 Review Decision Intake",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": {
            "imported_decision_count": len(imports),
            "current_decision_count": len(current),
            "conflict_count": len(conflicts),
            "run_count": len(runs),
            "readiness_row_count": len(readiness_rows),
        },
        "decision_counts": dict(sorted(Counter(row.get("human_decision", "") for row in current).items())),
        "conflict_type_counts": dict(sorted(Counter(row.get("conflict_type", "") for row in conflicts).items())),
        "readiness_status_counts": dict(sorted(Counter(row.get("readiness_status", "") for row in readiness_rows).items())),
        "output_files": [
            str(out_dir / REPORT_MD),
            str(out_dir / SUMMARY_JSON),
            str(out_dir / CURRENT_CSV),
            str(out_dir / CURRENT_JSON),
            str(out_dir / CONFLICT_CSV),
            str(out_dir / CONFLICT_JSON),
            str(out_dir / CONFLICT_MD),
            str(out_dir / READINESS_CSV),
            str(out_dir / READINESS_JSON),
            str(out_dir / READINESS_REPORT),
            str(out_dir / MANIFEST_JSON),
        ],
    }
    write_json(out_dir / SUMMARY_JSON, manifest)
    write_report(out_dir / REPORT_MD, manifest)
    write_conflict_report(out_dir / CONFLICT_MD, conflicts)
    write_readiness_report(out_dir / READINESS_REPORT, readiness_rows)
    existing_manifest = {}
    manifest_path = out_dir / MANIFEST_JSON
    if manifest_path.exists():
        try:
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_manifest = {}
    write_json(manifest_path, {**existing_manifest, "reporter_manifest": manifest} if existing_manifest else manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Report L5.4 review decisions and L5.5 readiness.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_review_decision_reporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.4 review decision current rows: {manifest['row_counts']['current_decision_count']}")
    print(f"L5.5 readiness rows: {manifest['row_counts']['readiness_row_count']}")


if __name__ == "__main__":
    main()
