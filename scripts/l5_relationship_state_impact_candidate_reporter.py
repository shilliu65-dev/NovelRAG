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
from scripts.l5_relationship_state_impact_candidate_indexer import CREATED_AT, MANIFEST_JSON


RELATIONSHIP_CSV = "l5_relationship_impact_candidates.csv"
RELATIONSHIP_JSON = "l5_relationship_impact_candidates.json"
STATE_CSV = "l5_state_impact_candidates.csv"
STATE_JSON = "l5_state_impact_candidates.json"
EVIDENCE_CSV = "l5_impact_candidate_evidence.csv"
EVIDENCE_JSON = "l5_impact_candidate_evidence.json"
AUDIT_CSV = "l5_relationship_state_impact_audit.csv"
AUDIT_JSON = "l5_relationship_state_impact_audit.json"
REPORT_MD = "l5_relationship_state_impact_candidate_report.md"


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
        "# L5.8 Relationship / State Impact Candidate Report",
        "",
        "## Summary",
        "",
        f"- relationship_impact_candidate_count: {manifest['row_counts']['relationship_impact_candidate_count']}",
        f"- state_impact_candidate_count: {manifest['row_counts']['state_impact_candidate_count']}",
        f"- impact_evidence_count: {manifest['row_counts']['impact_evidence_count']}",
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


def run_l5_relationship_state_impact_candidate_reporter(
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
        relationships = fetch_rows(conn, "l5_relationship_impact_candidate")
        states = fetch_rows(conn, "l5_state_impact_candidate")
        evidence = fetch_rows(conn, "l5_impact_candidate_evidence")
        audits = fetch_rows(conn, "l5_relationship_state_impact_audit")
        runs = fetch_rows(conn, "l5_relationship_state_impact_run")
    finally:
        conn.close()

    relationship_columns = list(relationships[0].keys()) if relationships else ["relationship_impact_candidate_id", "merge_group_candidate_id", "source_confirmed_event_candidate_id", "relationship_impact_type", "relationship_impact_hash"]
    state_columns = list(states[0].keys()) if states else ["state_impact_candidate_id", "merge_group_candidate_id", "source_confirmed_event_candidate_id", "state_impact_type", "state_impact_hash"]
    evidence_columns = list(evidence[0].keys()) if evidence else ["impact_evidence_id", "impact_candidate_type", "impact_candidate_id", "source_confirmed_event_candidate_id"]
    audit_columns = list(audits[0].keys()) if audits else ["audit_id", "source_confirmed_event_candidate_id", "merge_group_candidate_id", "audit_type", "severity"]
    write_csv(out_dir / RELATIONSHIP_CSV, relationships, relationship_columns)
    write_csv(out_dir / STATE_CSV, states, state_columns)
    write_csv(out_dir / EVIDENCE_CSV, evidence, evidence_columns)
    write_csv(out_dir / AUDIT_CSV, audits, audit_columns)
    write_json(out_dir / RELATIONSHIP_JSON, {"export_name": "l5_relationship_impact_candidate", "created_at": CREATED_AT, "row_count": len(relationships), "columns": relationship_columns, "rows": relationships})
    write_json(out_dir / STATE_JSON, {"export_name": "l5_state_impact_candidate", "created_at": CREATED_AT, "row_count": len(states), "columns": state_columns, "rows": states})
    write_json(out_dir / EVIDENCE_JSON, {"export_name": "l5_impact_candidate_evidence", "created_at": CREATED_AT, "row_count": len(evidence), "columns": evidence_columns, "rows": evidence})
    write_json(out_dir / AUDIT_JSON, {"export_name": "l5_relationship_state_impact_audit", "created_at": CREATED_AT, "row_count": len(audits), "columns": audit_columns, "rows": audits})
    manifest = {
        "export_layer": "L5.8 Relationship / State Impact Candidate",
        "project_dir": str(root),
        "database_path": str(root / DB_RELATIVE_PATH),
        "created_at": CREATED_AT,
        "row_counts": {
            "relationship_impact_candidate_count": len(relationships),
            "state_impact_candidate_count": len(states),
            "impact_evidence_count": len(evidence),
            "audit_count": len(audits),
            "run_count": len(runs),
        },
        "audit_type_counts": dict(sorted(Counter(str(row.get("audit_type") or "") for row in audits).items())),
        "output_files": [
            str(out_dir / RELATIONSHIP_CSV),
            str(out_dir / RELATIONSHIP_JSON),
            str(out_dir / STATE_CSV),
            str(out_dir / STATE_JSON),
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
    parser = argparse.ArgumentParser(description="Export L5.8 relationship/state impact candidate reports.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_relationship_state_impact_candidate_reporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.8 relationship impact candidate rows: {manifest['row_counts']['relationship_impact_candidate_count']}")


if __name__ == "__main__":
    main()
