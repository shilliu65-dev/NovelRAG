from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import object_exists


CREATED_AT = "1970-01-01T00:00:00"
TEMPLATE_CSV = "l5_review_decision_template.csv"
TEMPLATE_JSON = "l5_review_decision_template.json"
TEMPLATE_REPORT = "l5_review_decision_template_report.md"
TEMPLATE_MANIFEST = "l5_review_decision_template_manifest.json"

HUMAN_DECISIONS = {
    "approved_candidate",
    "weak_candidate",
    "duplicate_candidate",
    "needs_context",
    "rejected",
    "uncertain",
}

REQUIRED_TEMPLATE_COLUMNS = [
    "normalized_event_id",
    "event_key",
    "chapter_num",
    "scene_block_id",
    "scene_block_source",
    "event_type",
    "event_subtype",
    "subject_text",
    "predicate_canonical",
    "object_text",
    "location_text",
    "time_text",
    "evidence_text_preview",
    "evidence_quality",
    "normalization_confidence",
    "review_recommendation_from_l5_3",
    "human_decision",
    "human_confidence",
    "human_notes",
    "duplicate_of_normalized_event_id",
    "needs_context_reason",
    "reject_reason",
    "reviewer_name",
]


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def fetch_l5_3_events(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    if not object_exists(conn, "l5_normalized_event_candidate", "table"):
        raise RuntimeError("Missing L5.3 table l5_normalized_event_candidate")
    return [
        dict(row)
        for row in conn.execute(
            """
            SELECT *
            FROM l5_normalized_event_candidate
            ORDER BY chapter_num, normalized_event_candidate_id
            """
        )
    ]


def fetch_arguments(conn: sqlite3.Connection) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    if not object_exists(conn, "l5_normalized_event_argument", "table"):
        return result
    for row in conn.execute(
        """
        SELECT normalized_event_candidate_id, argument_role, argument_text
        FROM l5_normalized_event_argument
        ORDER BY normalized_event_candidate_id, argument_role, normalized_argument_id
        """
    ):
        event_id = str(row["normalized_event_candidate_id"])
        role = str(row["argument_role"] or "")
        result.setdefault(event_id, {}).setdefault(role, str(row["argument_text"] or ""))
    return result


def preview(value: str, limit: int = 180) -> str:
    value = " ".join((value or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def build_template_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    arguments = fetch_arguments(conn)
    for event in fetch_l5_3_events(conn):
        event_id = str(event.get("normalized_event_candidate_id") or "")
        event_args = arguments.get(event_id, {})
        object_text = event_args.get("object") or event_args.get("target") or event_args.get("patient") or ""
        location_text = event_args.get("current_location") or event_args.get("location") or event_args.get("destination_location") or ""
        time_text = event_args.get("time_hint") or ""
        rows.append(
            {
                "normalized_event_id": event_id,
                "event_key": event.get("stable_hash") or event.get("source_event_candidate_id") or event_id,
                "chapter_num": event.get("chapter_num", ""),
                "scene_block_id": event.get("scene_block_id", ""),
                "scene_block_source": event.get("scene_block_table_name", ""),
                "event_type": event.get("l5_2_event_type", ""),
                "event_subtype": event.get("l5_2_event_subtype", ""),
                "subject_text": event.get("subject_text", ""),
                "predicate_canonical": event.get("l5_2_event_type", ""),
                "object_text": object_text,
                "location_text": location_text,
                "time_text": time_text,
                "evidence_text_preview": preview(str(event.get("evidence_text_backcut") or "")),
                "evidence_quality": event.get("evidence_backcut_status", ""),
                "normalization_confidence": event.get("normalized_confidence_score", ""),
                "review_recommendation_from_l5_3": event.get("normalization_status", ""),
                "human_decision": "",
                "human_confidence": "",
                "human_notes": "",
                "duplicate_of_normalized_event_id": "",
                "needs_context_reason": "",
                "reject_reason": "",
                "reviewer_name": "",
            }
        )
    return rows


def write_report(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# L5.4 Review Decision Template Export",
        "",
        "## Summary",
        "",
        f"- row_count: {manifest['row_count']}",
        f"- source_table: l5_normalized_event_candidate",
        f"- candidate_only: true",
        "",
        "## Human Decision Enum",
        "",
    ]
    lines.extend(f"- {decision}" for decision in sorted(HUMAN_DECISIONS))
    lines.extend(["", "## Output Files", ""])
    lines.extend(f"- {path}" for path in manifest["output_files"])
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_review_decision_template_exporter(
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
        rows = build_template_rows(conn)
    finally:
        conn.close()

    write_csv(out_dir / TEMPLATE_CSV, rows, REQUIRED_TEMPLATE_COLUMNS)
    write_json(
        out_dir / TEMPLATE_JSON,
        {
            "export_name": "l5_review_decision_template",
            "export_layer": "L5.4 Review Decision Intake",
            "created_at": CREATED_AT,
            "row_count": len(rows),
            "columns": REQUIRED_TEMPLATE_COLUMNS,
            "human_decision_enum": sorted(HUMAN_DECISIONS),
            "rows": rows,
        },
    )
    manifest = {
        "export_layer": "L5.4 Review Decision Intake",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_count": len(rows),
        "columns": REQUIRED_TEMPLATE_COLUMNS,
        "output_files": [
            str(out_dir / TEMPLATE_CSV),
            str(out_dir / TEMPLATE_JSON),
            str(out_dir / TEMPLATE_REPORT),
            str(out_dir / TEMPLATE_MANIFEST),
        ],
    }
    write_json(out_dir / TEMPLATE_MANIFEST, manifest)
    write_report(out_dir / TEMPLATE_REPORT, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export L5.4 human review decision template from L5.3 candidates.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_review_decision_template_exporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.4 review decision template rows: {manifest['row_count']}")


if __name__ == "__main__":
    main()
