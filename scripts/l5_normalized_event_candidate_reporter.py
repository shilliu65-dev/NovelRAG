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
from scripts.l5_event_candidate_extractor import discover_scene_block_source, scene_block_source_payload
from scripts.l5_event_candidate_review_exporter import object_exists
from scripts.l5_normalized_event_candidate_indexer import (
    CREATED_AT,
    MANIFEST_JSON,
    NORMALIZED_EVENT_TABLES,
    SAMPLE_CSV,
    SAMPLE_JSON,
    SAMPLE_REPORT,
    STATE_CSV,
    STATE_JSON,
)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
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


def table_count(conn: sqlite3.Connection, table: str) -> int:
    if not object_exists(conn, table, "table"):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def scene_block_audit(conn: sqlite3.Connection) -> dict[str, Any]:
    source = scene_block_source_payload(discover_scene_block_source(conn), conn)
    if object_exists(conn, "l5_normalized_event_candidate", "table"):
        linked = int(
            conn.execute(
                "SELECT COUNT(*) FROM l5_normalized_event_candidate WHERE scene_block_id IS NOT NULL AND scene_block_id != ''"
            ).fetchone()[0]
        )
        without = int(
            conn.execute(
                "SELECT COUNT(*) FROM l5_normalized_event_candidate WHERE scene_block_id IS NULL OR scene_block_id = ''"
            ).fetchone()[0]
        )
    else:
        linked = 0
        without = 0
    return {
        **source,
        "scene_block_linked_event_count": linked,
        "event_without_scene_block_count": without,
    }


def latest_run(conn: sqlite3.Connection) -> dict[str, Any]:
    if not object_exists(conn, "l5_normalized_event_index_run", "table"):
        return {}
    row = conn.execute("SELECT * FROM l5_normalized_event_index_run ORDER BY finished_at DESC, run_id DESC LIMIT 1").fetchone()
    return dict(row) if row else {}


def write_markdown_report(path: Path, manifest: dict[str, Any]) -> None:
    scene = manifest["detected_scene_block_source"]
    lines = [
        "# L5.3 Normalized Event Candidate Index Sample Report",
        "",
        "## Summary",
        "",
        f"- layer: {manifest['export_layer']}",
        f"- normalized_event_candidate_count: {manifest['row_counts']['normalized_event_candidate_count']}",
        f"- normalized_state_change_candidate_count: {manifest['row_counts']['normalized_state_change_candidate_count']}",
        f"- source_layer: L5.1a",
        f"- candidate_only: true",
        "",
        "## Source Discovery",
        "",
        f"- detected_scene_block_source: {scene.get('detected_scene_block_source')}",
        f"- scene_block_source_status: {scene.get('scene_block_source_status')}",
        f"- scene_block_table_name: {scene.get('scene_block_table_name')}",
        f"- scene_block_linked_event_count: {scene.get('scene_block_linked_event_count')}",
        f"- event_without_scene_block_count: {scene.get('event_without_scene_block_count')}",
        "",
        "## Event Type Counts",
        "",
    ]
    event_counts = manifest["event_type_counts"]
    lines.extend(f"- {key}: {value}" for key, value in event_counts.items()) if event_counts else lines.append("- none")
    lines.extend(["", "## Normalization Status Counts", ""])
    status_counts = manifest["normalization_status_counts"]
    lines.extend(f"- {key}: {value}" for key, value in status_counts.items()) if status_counts else lines.append("- none")
    lines.extend(["", "## Output Files", ""])
    lines.extend(f"- {item}" for item in manifest["output_files"])
    lines.extend(["", "## PASS / WARNING / FAIL", ""])
    if scene.get("scene_block_source_status") == "missing_optional":
        lines.append("- WARNING missing optional scene block source")
    else:
        lines.append("- PASS")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_normalized_event_candidate_reporter(
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
    if not db_path.exists():
        raise RuntimeError(f"Missing SQLite database: {db_path}")

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        event_rows = fetch_rows(conn, "l5_normalized_event_candidate")
        state_rows = fetch_rows(conn, "l5_normalized_state_change_candidate")
        argument_rows = fetch_rows(conn, "l5_normalized_event_argument")
        evidence_rows = fetch_rows(conn, "l5_normalized_event_evidence")
        source_map_rows = fetch_rows(conn, "l5_normalized_event_source_map")
        warning_rows = fetch_rows(conn, "l5_normalized_event_warning")
        scene = scene_block_audit(conn)
        run = latest_run(conn)
        table_counts = {table: table_count(conn, table) for table in NORMALIZED_EVENT_TABLES}
    finally:
        conn.close()

    event_columns = list(event_rows[0].keys()) if event_rows else [
        "normalized_event_candidate_id",
        "source_event_candidate_id",
        "source_layer",
        "chapter_num",
        "scene_block_id",
        "l5_2_event_type",
        "normalization_status",
        "subject_text",
        "subject_is_confirmed",
        "l5_2_seed_checksum",
    ]
    state_columns = list(state_rows[0].keys()) if state_rows else [
        "normalized_state_change_candidate_id",
        "normalized_event_candidate_id",
        "source_event_candidate_id",
        "state_change_type",
        "is_confirmed",
        "l5_2_seed_checksum",
    ]
    write_csv(out_dir / SAMPLE_CSV, event_columns, event_rows)
    write_csv(out_dir / STATE_CSV, state_columns, state_rows)
    write_json(
        out_dir / SAMPLE_JSON,
        {
            "export_name": "l5_normalized_event_candidates_sample",
            "export_layer": "L5.3 Normalized Event Candidate Index",
            "created_at": CREATED_AT,
            "row_count": len(event_rows),
            "columns": event_columns,
            "rows": event_rows,
        },
    )
    write_json(
        out_dir / STATE_JSON,
        {
            "export_name": "l5_normalized_state_change_candidates_sample",
            "export_layer": "L5.3 Normalized Event Candidate Index",
            "created_at": CREATED_AT,
            "row_count": len(state_rows),
            "columns": state_columns,
            "rows": state_rows,
        },
    )

    manifest = {
        "export_layer": "L5.3 Normalized Event Candidate Index",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "detected_scene_block_source": scene,
        "row_counts": {
            "normalized_event_candidate_count": len(event_rows),
            "normalized_event_argument_count": len(argument_rows),
            "normalized_state_change_candidate_count": len(state_rows),
            "normalized_event_evidence_count": len(evidence_rows),
            "normalized_event_source_map_count": len(source_map_rows),
            "normalized_event_warning_count": len(warning_rows),
        },
        "table_counts": table_counts,
        "event_type_counts": dict(sorted(Counter(row.get("l5_2_event_type", "") for row in event_rows).items())),
        "normalization_status_counts": dict(sorted(Counter(row.get("normalization_status", "") for row in event_rows).items())),
        "latest_run": run,
        "output_files": [
            str(out_dir / SAMPLE_JSON),
            str(out_dir / SAMPLE_CSV),
            str(out_dir / SAMPLE_REPORT),
            str(out_dir / STATE_JSON),
            str(out_dir / STATE_CSV),
            str(out_dir / MANIFEST_JSON),
        ],
    }
    write_markdown_report(out_dir / SAMPLE_REPORT, manifest)
    existing_manifest_path = out_dir / MANIFEST_JSON
    if existing_manifest_path.exists():
        try:
            existing_manifest = json.loads(existing_manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing_manifest = {}
        if existing_manifest:
            merged = {**existing_manifest, "reporter_manifest": manifest}
            write_json(existing_manifest_path, merged)
        else:
            write_json(existing_manifest_path, manifest)
    else:
        write_json(existing_manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Report L5.3 normalized event candidate index sample.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_normalized_event_candidate_reporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.3 normalized event candidate report rows: {manifest['row_counts']['normalized_event_candidate_count']}")
    print(f"L5.3 scene block source status: {manifest['detected_scene_block_source'].get('scene_block_source_status')}")


if __name__ == "__main__":
    main()
