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
from scripts.l5_timeline_anchor_candidate_indexer import CREATED_AT, MANIFEST_JSON


ANCHOR_CSV = "l5_timeline_anchor_candidates.csv"
ANCHOR_JSON = "l5_timeline_anchor_candidates.json"
RELATIVE_CSV = "l5_timeline_relative_order_candidates.csv"
RELATIVE_JSON = "l5_timeline_relative_order_candidates.json"
AUDIT_CSV = "l5_timeline_anchor_audit.csv"
AUDIT_JSON = "l5_timeline_anchor_audit.json"
REPORT_MD = "l5_timeline_anchor_candidate_report.md"


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
        "# L5.7 Timeline Anchor Candidate Report",
        "",
        "## Summary",
        "",
        f"- timeline_anchor_candidate_count: {manifest['row_counts']['timeline_anchor_candidate_count']}",
        f"- relative_order_candidate_count: {manifest['row_counts']['relative_order_candidate_count']}",
        f"- fallback_anchor_count: {manifest['row_counts']['fallback_anchor_count']}",
        f"- possible_flashback_count: {manifest['row_counts']['possible_flashback_count']}",
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


def run_l5_timeline_anchor_candidate_reporter(
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
    conn = sqlite3.connect(root / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        anchors = fetch_rows(conn, "l5_timeline_anchor_candidate")
        relatives = fetch_rows(conn, "l5_timeline_relative_order_candidate")
        audits = fetch_rows(conn, "l5_timeline_anchor_audit")
        runs = fetch_rows(conn, "l5_timeline_anchor_run")
    finally:
        conn.close()

    anchor_columns = list(anchors[0].keys()) if anchors else ["timeline_anchor_candidate_id", "merge_group_candidate_id", "anchor_status", "anchor_type", "anchor_hash"]
    relative_columns = list(relatives[0].keys()) if relatives else ["relative_order_candidate_id", "source_timeline_anchor_candidate_id", "target_timeline_anchor_candidate_id", "relation_type"]
    audit_columns = list(audits[0].keys()) if audits else ["audit_id", "timeline_anchor_candidate_id", "merge_group_candidate_id", "audit_type", "severity"]
    write_csv(out_dir / ANCHOR_CSV, anchors, anchor_columns)
    write_csv(out_dir / RELATIVE_CSV, relatives, relative_columns)
    write_csv(out_dir / AUDIT_CSV, audits, audit_columns)
    write_json(out_dir / ANCHOR_JSON, {"export_name": "l5_timeline_anchor_candidate", "created_at": CREATED_AT, "row_count": len(anchors), "columns": anchor_columns, "rows": anchors})
    write_json(out_dir / RELATIVE_JSON, {"export_name": "l5_timeline_relative_order_candidate", "created_at": CREATED_AT, "row_count": len(relatives), "columns": relative_columns, "rows": relatives})
    write_json(out_dir / AUDIT_JSON, {"export_name": "l5_timeline_anchor_audit", "created_at": CREATED_AT, "row_count": len(audits), "columns": audit_columns, "rows": audits})
    fallback_count = sum(1 for row in audits if str(row.get("audit_type") or "") == "fallback_to_chapter_order")
    flashback_count = sum(1 for row in anchors if int(row.get("is_flashback_candidate") or 0) == 1)
    manifest = {
        "export_layer": "L5.7 Timeline Anchor Candidate",
        "project_dir": str(root),
        "database_path": str(root / DB_RELATIVE_PATH),
        "created_at": CREATED_AT,
        "row_counts": {
            "timeline_anchor_candidate_count": len(anchors),
            "relative_order_candidate_count": len(relatives),
            "fallback_anchor_count": fallback_count,
            "possible_flashback_count": flashback_count,
            "audit_count": len(audits),
            "run_count": len(runs),
        },
        "audit_type_counts": dict(sorted(Counter(str(row.get("audit_type") or "") for row in audits).items())),
        "output_files": [
            str(out_dir / ANCHOR_CSV),
            str(out_dir / ANCHOR_JSON),
            str(out_dir / RELATIVE_CSV),
            str(out_dir / RELATIVE_JSON),
            str(out_dir / AUDIT_CSV),
            str(out_dir / AUDIT_JSON),
            str(out_dir / REPORT_MD),
            str(out_dir / MANIFEST_JSON),
        ],
    }
    write_report(out_dir / REPORT_MD, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export L5.7 timeline anchor candidate reports.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_timeline_anchor_candidate_reporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.7 timeline anchor candidate rows: {manifest['row_counts']['timeline_anchor_candidate_count']}")


if __name__ == "__main__":
    main()
