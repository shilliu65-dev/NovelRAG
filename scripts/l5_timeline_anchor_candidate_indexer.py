from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import object_exists


CREATED_AT = "1970-01-01T00:00:00"
MANIFEST_JSON = "l5_timeline_anchor_candidate_manifest.json"

L5_7_TABLES = (
    "l5_timeline_anchor_candidate",
    "l5_timeline_relative_order_candidate",
    "l5_timeline_anchor_audit",
    "l5_timeline_anchor_run",
)
L5_7_INPUT_TABLES = (
    "l5_event_merge_group_candidate",
    "l5_event_merge_group_member",
    "l5_confirmed_event_candidate",
    "l5_confirmed_event_evidence_span",
)
OPTIONAL_L3_TIMELINE_TABLES = ("l3_chapter_time_mapping", "l3_era_def", "l3_timeline_anchor")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__type__": "bytes", "hex": value.hex()}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def table_fingerprints(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for table in tables:
        if not object_exists(conn, table, "table"):
            result[table] = {"exists": False, "row_count": 0, "columns": [], "aggregate_hash": ""}
            continue
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        result[table] = {"exists": True, "row_count": len(rows), "columns": columns, "aggregate_hash": sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}
    return result


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l5_timeline_anchor_candidate (
            timeline_anchor_candidate_id TEXT PRIMARY KEY,
            merge_group_candidate_id TEXT NOT NULL,
            representative_confirmed_event_candidate_id TEXT NOT NULL,
            anchor_status TEXT NOT NULL,
            anchor_type TEXT NOT NULL,
            era_id TEXT,
            chapter_num INTEGER,
            narrative_order_key TEXT,
            absolute_order_key TEXT,
            scene_order_index INTEGER,
            evidence_order_index INTEGER,
            is_flashback_candidate INTEGER NOT NULL,
            time_text TEXT,
            location_text TEXT,
            confidence TEXT,
            anchor_rule TEXT NOT NULL,
            anchor_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_timeline_relative_order_candidate (
            relative_order_candidate_id TEXT PRIMARY KEY,
            source_timeline_anchor_candidate_id TEXT NOT NULL,
            target_timeline_anchor_candidate_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            relation_rule TEXT NOT NULL,
            confidence TEXT,
            relation_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_timeline_anchor_audit (
            audit_id TEXT PRIMARY KEY,
            timeline_anchor_candidate_id TEXT,
            merge_group_candidate_id TEXT,
            audit_type TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_timeline_anchor_run (
            run_id TEXT PRIMARY KEY,
            input_merge_group_candidate_count INTEGER NOT NULL,
            timeline_anchor_candidate_count INTEGER NOT NULL,
            relative_order_candidate_count INTEGER NOT NULL,
            fallback_anchor_count INTEGER NOT NULL,
            possible_flashback_count INTEGER NOT NULL,
            audit_count INTEGER NOT NULL,
            source_mutation_detected INTEGER NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            l5_6_fingerprint TEXT,
            l3_timeline_fingerprint TEXT,
            run_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def clear_l5_7_tables(conn: sqlite3.Connection) -> None:
    for table in reversed(L5_7_TABLES):
        conn.execute(f"DELETE FROM {table}")


def require_sources(conn: sqlite3.Connection) -> None:
    missing = sorted(table for table in L5_7_INPUT_TABLES if not object_exists(conn, table, "table"))
    if missing:
        raise RuntimeError(f"Missing required L5.6/L5.5 tables: {missing}")


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
    order_sql = ", ".join(sql_identifier(column) for column in columns)
    return [dict(row) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {sql_identifier(table)} ({', '.join(sql_identifier(column) for column in columns)}) VALUES ({placeholders})",
        [tuple(row.get(column) for column in columns) for row in rows],
    )


def numeric_part(value: Any) -> int:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else 0


def load_era_by_chapter(conn: sqlite3.Connection) -> dict[int, str]:
    if not object_exists(conn, "l3_chapter_time_mapping", "table"):
        return {}
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(l3_chapter_time_mapping)")}
    if not {"chapter_num", "era_id"}.issubset(columns):
        return {}
    return {int(row["chapter_num"]): str(row["era_id"] or "") for row in conn.execute("SELECT chapter_num, era_id FROM l3_chapter_time_mapping")}


def build_rows(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int], str]:
    groups = fetch_rows(conn, "l5_event_merge_group_candidate")
    evidence = fetch_rows(conn, "l5_confirmed_event_evidence_span")
    era_by_chapter = load_era_by_chapter(conn)
    l3_fingerprint = sha256_text(json.dumps(table_fingerprints(conn, OPTIONAL_L3_TIMELINE_TABLES), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    min_evidence_by_confirmed: dict[str, int] = {}
    for row in evidence:
        confirmed_id = str(row["confirmed_event_candidate_id"])
        order_value = int(row.get("start_offset") or 0)
        min_evidence_by_confirmed[confirmed_id] = min(order_value, min_evidence_by_confirmed.get(confirmed_id, order_value))

    sorted_groups = sorted(groups, key=lambda row: (int(row.get("chapter_num_min") or 0), numeric_part(row.get("scene_block_id_primary")), str(row["merge_group_candidate_id"])))
    scene_index_by_group = {str(row["merge_group_candidate_id"]): index for index, row in enumerate(sorted_groups, start=1)}
    anchor_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for group in sorted_groups:
        group_id = str(group["merge_group_candidate_id"])
        representative_id = str(group["representative_confirmed_event_candidate_id"])
        chapter_num = int(group.get("chapter_num_min") or 0)
        era_id = era_by_chapter.get(chapter_num, "")
        anchor_type = "era_anchor" if era_id else "chapter_order_anchor"
        anchor_rule = "era_anchor" if era_id else "chapter_order_anchor"
        scene_index = scene_index_by_group[group_id]
        evidence_index = min_evidence_by_confirmed.get(representative_id, 0)
        anchor_id = stable_id("l5ta", group_id)
        narrative_key = f"ch{chapter_num:06d}:scene{scene_index:06d}:evidence{evidence_index:08d}"
        anchor_hash = sha256_text(json.dumps([anchor_id, group_id, representative_id, anchor_type, narrative_key, era_id], ensure_ascii=False, separators=(",", ":")))
        anchor_rows.append(
            {
                "timeline_anchor_candidate_id": anchor_id,
                "merge_group_candidate_id": group_id,
                "representative_confirmed_event_candidate_id": representative_id,
                "anchor_status": "timeline_anchor_candidate",
                "anchor_type": anchor_type,
                "era_id": era_id,
                "chapter_num": chapter_num,
                "narrative_order_key": narrative_key,
                "absolute_order_key": f"era:{era_id}:{narrative_key}" if era_id else "",
                "scene_order_index": scene_index,
                "evidence_order_index": evidence_index,
                "is_flashback_candidate": 0,
                "time_text": group.get("time_text", ""),
                "location_text": group.get("location_text", ""),
                "confidence": "medium" if era_id else "low",
                "anchor_rule": anchor_rule,
                "anchor_hash": anchor_hash,
                "created_at": CREATED_AT,
            }
        )
        if not era_id:
            audit_rows.append(
                {
                    "audit_id": stable_id("l5ta_aud", anchor_id, "fallback_to_chapter_order"),
                    "timeline_anchor_candidate_id": anchor_id,
                    "merge_group_candidate_id": group_id,
                    "audit_type": "fallback_to_chapter_order",
                    "description": "L3 timeline mapping unavailable for chapter; used chapter order anchor.",
                    "severity": "info",
                    "created_at": CREATED_AT,
                }
            )
    relative_rows: list[dict[str, Any]] = []
    for index in range(len(anchor_rows) - 1):
        source = anchor_rows[index]
        target = anchor_rows[index + 1]
        relation_type = "same_chapter" if source["chapter_num"] == target["chapter_num"] else "before"
        relation_hash = sha256_text(json.dumps([source["timeline_anchor_candidate_id"], target["timeline_anchor_candidate_id"], relation_type], ensure_ascii=False, separators=(",", ":")))
        relative_rows.append(
            {
                "relative_order_candidate_id": stable_id("l5tro", relation_hash),
                "source_timeline_anchor_candidate_id": source["timeline_anchor_candidate_id"],
                "target_timeline_anchor_candidate_id": target["timeline_anchor_candidate_id"],
                "relation_type": relation_type,
                "relation_rule": "narrative_order_key",
                "confidence": "medium",
                "relation_hash": relation_hash,
                "created_at": CREATED_AT,
            }
        )

    counts = {
        "input_merge_group_candidate_count": len(groups),
        "timeline_anchor_candidate_count": len(anchor_rows),
        "relative_order_candidate_count": len(relative_rows),
        "fallback_anchor_count": sum(1 for row in audit_rows if row["audit_type"] == "fallback_to_chapter_order"),
        "possible_flashback_count": sum(1 for row in anchor_rows if int(row["is_flashback_candidate"]) == 1),
        "audit_count": len(audit_rows),
    }
    return anchor_rows, relative_rows, audit_rows, counts, l3_fingerprint


def stable_output_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for table in ("l5_timeline_anchor_candidate", "l5_timeline_relative_order_candidate", "l5_timeline_anchor_audit"):
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        hashes[table] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashes


def run_l5_timeline_anchor_candidate_indexer(
    project_dir: Path | str | None = None,
    *,
    output_dir: Path | str = "outputs",
    rebuild: bool = False,
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
        require_sources(conn)
        initialize_schema(conn)
        source_before = table_fingerprints(conn, L5_7_INPUT_TABLES)
        input_before = table_fingerprints(conn, L5_7_INPUT_TABLES)
        if rebuild:
            clear_l5_7_tables(conn)
        anchor_rows, relative_rows, audit_rows, counts, l3_fingerprint = build_rows(conn)
        insert_rows(conn, "l5_timeline_anchor_candidate", anchor_rows)
        insert_rows(conn, "l5_timeline_relative_order_candidate", relative_rows)
        insert_rows(conn, "l5_timeline_anchor_audit", audit_rows)
        stable_hashes = stable_output_hashes(conn)
        source_after = table_fingerprints(conn, L5_7_INPUT_TABLES)
        input_after = table_fingerprints(conn, L5_7_INPUT_TABLES)
        source_mutation = source_before != source_after
        input_mutation = input_before != input_after
        l5_6_fingerprint = sha256_text(json.dumps(source_before, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        run_hash = sha256_text(json.dumps([counts, stable_hashes, l5_6_fingerprint, l3_fingerprint], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        conn.execute(
            "INSERT INTO l5_timeline_anchor_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_id("l5ta_run", run_hash),
                counts["input_merge_group_candidate_count"],
                counts["timeline_anchor_candidate_count"],
                counts["relative_order_candidate_count"],
                counts["fallback_anchor_count"],
                counts["possible_flashback_count"],
                counts["audit_count"],
                int(source_mutation),
                int(input_mutation),
                l5_6_fingerprint,
                l3_fingerprint,
                run_hash,
                CREATED_AT,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from scripts.l5_timeline_anchor_candidate_reporter import run_l5_timeline_anchor_candidate_reporter

    report_manifest = run_l5_timeline_anchor_candidate_reporter(root, output_dir=out_dir)
    manifest = {
        "export_layer": "L5.7 Timeline Anchor Candidate",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": counts,
        "audit_type_counts": report_manifest["audit_type_counts"],
        "source_mutation_detected": source_mutation,
        "input_mutation_detected": input_mutation,
        "l5_6_fingerprint": l5_6_fingerprint,
        "l3_timeline_fingerprint": l3_fingerprint,
        "stable_output_hashes": stable_hashes,
        "reporter_manifest": report_manifest,
    }
    write_json(out_dir / MANIFEST_JSON, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L5.7 timeline anchor candidates.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    manifest = run_l5_timeline_anchor_candidate_indexer(args.project_dir, output_dir=args.output_dir, rebuild=args.rebuild)
    print(f"L5.7 timeline anchor candidate rows: {manifest['row_counts']['timeline_anchor_candidate_count']}")


if __name__ == "__main__":
    main()
