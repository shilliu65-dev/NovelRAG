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
from scripts.l5_confirmed_event_candidate_indexer import CREATED_AT, MANIFEST_JSON
from scripts.l5_event_candidate_review_exporter import object_exists


REPORT_MD = "l5_confirmed_event_candidate_report.md"
CONFIRMED_CSV = "l5_confirmed_event_candidates.csv"
CONFIRMED_JSON = "l5_confirmed_event_candidates.json"
ARGUMENT_CSV = "l5_confirmed_event_arguments.csv"
ARGUMENT_JSON = "l5_confirmed_event_arguments.json"
EVIDENCE_CSV = "l5_confirmed_event_evidence_spans.csv"
EVIDENCE_JSON = "l5_confirmed_event_evidence_spans.json"
BLOCKED_CSV = "l5_confirmed_event_blocked_audit.csv"
BLOCKED_JSON = "l5_confirmed_event_blocked_audit.json"


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
    blocked_counts = manifest["blocked_reason_counts"]
    lines = [
        "# L5.5 Confirmed Event Candidate Report",
        "",
        "## Summary",
        "",
        f"- confirmed_event_candidate_count: {manifest['row_counts']['confirmed_event_candidate_count']}",
        f"- confirmed_event_argument_candidate_count: {manifest['row_counts']['confirmed_event_argument_candidate_count']}",
        f"- confirmed_event_evidence_span_count: {manifest['row_counts']['confirmed_event_evidence_span_count']}",
        f"- blocked_audit_count: {manifest['row_counts']['blocked_audit_count']}",
        "",
        "## Blocked Reason Counts",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in blocked_counts.items()) if blocked_counts else lines.append("- none")
    lines.extend(["", "## Output Files", ""])
    lines.extend(f"- {item}" for item in manifest["output_files"])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_confirmed_event_candidate_reporter(
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
        confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
        arguments = fetch_rows(conn, "l5_confirmed_event_argument_candidate")
        evidences = fetch_rows(conn, "l5_confirmed_event_evidence_span")
        blocked = fetch_rows(conn, "l5_confirmed_event_blocked_audit")
        runs = fetch_rows(conn, "l5_confirmed_event_candidate_run")
    finally:
        conn.close()

    confirmed_columns = list(confirmed[0].keys()) if confirmed else [
        "confirmed_event_candidate_id",
        "normalized_event_id",
        "current_decision_id",
        "review_batch_id",
        "review_source_file_hash",
        "confirmation_status",
        "confirmed_candidate_hash",
    ]
    argument_columns = list(arguments[0].keys()) if arguments else [
        "confirmed_argument_candidate_id",
        "confirmed_event_candidate_id",
        "normalized_argument_id",
        "normalized_event_id",
        "argument_role",
    ]
    evidence_columns = list(evidences[0].keys()) if evidences else [
        "confirmed_evidence_span_id",
        "confirmed_event_candidate_id",
        "normalized_evidence_id",
        "normalized_event_id",
        "evidence_hash",
    ]
    blocked_columns = list(blocked[0].keys()) if blocked else [
        "blocked_audit_id",
        "normalized_event_id",
        "block_reason",
        "detail_message",
    ]

    write_csv(out_dir / CONFIRMED_CSV, confirmed, confirmed_columns)
    write_csv(out_dir / ARGUMENT_CSV, arguments, argument_columns)
    write_csv(out_dir / EVIDENCE_CSV, evidences, evidence_columns)
    write_csv(out_dir / BLOCKED_CSV, blocked, blocked_columns)
    write_json(out_dir / CONFIRMED_JSON, {"export_name": "l5_confirmed_event_candidate", "created_at": CREATED_AT, "row_count": len(confirmed), "columns": confirmed_columns, "rows": confirmed})
    write_json(out_dir / ARGUMENT_JSON, {"export_name": "l5_confirmed_event_argument_candidate", "created_at": CREATED_AT, "row_count": len(arguments), "columns": argument_columns, "rows": arguments})
    write_json(out_dir / EVIDENCE_JSON, {"export_name": "l5_confirmed_event_evidence_span", "created_at": CREATED_AT, "row_count": len(evidences), "columns": evidence_columns, "rows": evidences})
    write_json(out_dir / BLOCKED_JSON, {"export_name": "l5_confirmed_event_blocked_audit", "created_at": CREATED_AT, "row_count": len(blocked), "columns": blocked_columns, "rows": blocked})

    manifest = {
        "export_layer": "L5.5 Confirmed Event Candidate Index",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": {
            "confirmed_event_candidate_count": len(confirmed),
            "confirmed_event_argument_candidate_count": len(arguments),
            "confirmed_event_evidence_span_count": len(evidences),
            "blocked_audit_count": len(blocked),
            "run_count": len(runs),
        },
        "blocked_reason_counts": dict(sorted(Counter(str(row.get("block_reason") or "") for row in blocked).items())),
        "output_files": [
            str(out_dir / REPORT_MD),
            str(out_dir / CONFIRMED_CSV),
            str(out_dir / CONFIRMED_JSON),
            str(out_dir / ARGUMENT_CSV),
            str(out_dir / ARGUMENT_JSON),
            str(out_dir / EVIDENCE_CSV),
            str(out_dir / EVIDENCE_JSON),
            str(out_dir / BLOCKED_CSV),
            str(out_dir / BLOCKED_JSON),
            str(out_dir / MANIFEST_JSON),
        ],
    }
    write_report(out_dir / REPORT_MD, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export L5.5 confirmed event candidate reports.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_confirmed_event_candidate_reporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.5 confirmed event candidate rows: {manifest['row_counts']['confirmed_event_candidate_count']}")


if __name__ == "__main__":
    main()
