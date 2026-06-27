from __future__ import annotations

import argparse
import hashlib
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


CREATED_AT = "1970-01-01T00:00:00"
MANIFEST_JSON = "l5_confirmed_event_candidate_manifest.json"

L5_5_TABLES = (
    "l5_confirmed_event_candidate",
    "l5_confirmed_event_argument_candidate",
    "l5_confirmed_event_evidence_span",
    "l5_confirmed_event_blocked_audit",
    "l5_confirmed_event_candidate_run",
)
L5_3_SOURCE_TABLES = (
    "l5_normalized_event_candidate",
    "l5_normalized_event_argument",
    "l5_normalized_event_evidence",
)
L5_4_INPUT_TABLES = (
    "l5_review_decision_current",
    "l5_review_decision_conflict_audit",
)


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
        result[table] = {
            "exists": True,
            "row_count": len(rows),
            "columns": columns,
            "aggregate_hash": sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        }
    return result


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l5_confirmed_event_candidate (
            confirmed_event_candidate_id TEXT PRIMARY KEY,
            normalized_event_id TEXT NOT NULL UNIQUE,
            current_decision_id TEXT NOT NULL,
            review_batch_id TEXT NOT NULL,
            review_source_file_hash TEXT NOT NULL,
            confirmation_status TEXT NOT NULL,
            confirmed_candidate_hash TEXT NOT NULL,
            source_event_candidate_id TEXT NOT NULL,
            chapter_id TEXT,
            chapter_num INTEGER,
            chapter_title TEXT,
            version_id TEXT,
            scene_block_id TEXT,
            scene_block_table_name TEXT,
            l5_2_event_type TEXT NOT NULL,
            l5_2_event_subtype TEXT,
            subject_text TEXT NOT NULL,
            subject_entity_kind TEXT,
            normalized_confidence_score REAL,
            evidence_backcut_status TEXT,
            source_normalized_stable_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_confirmed_event_argument_candidate (
            confirmed_argument_candidate_id TEXT PRIMARY KEY,
            confirmed_event_candidate_id TEXT NOT NULL,
            normalized_argument_id TEXT NOT NULL,
            normalized_event_id TEXT NOT NULL,
            source_event_candidate_id TEXT NOT NULL,
            argument_role TEXT NOT NULL,
            entity_kind TEXT,
            argument_text TEXT NOT NULL,
            confidence REAL,
            source_kind TEXT,
            source_rule TEXT,
            evidence_text TEXT,
            l5_2_seed_checksum TEXT NOT NULL,
            stable_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_confirmed_event_evidence_span (
            confirmed_evidence_span_id TEXT PRIMARY KEY,
            confirmed_event_candidate_id TEXT NOT NULL,
            normalized_evidence_id TEXT NOT NULL,
            normalized_event_id TEXT NOT NULL,
            source_event_candidate_id TEXT NOT NULL,
            evidence_source_kind TEXT,
            l2_sentence_id TEXT,
            l2_paragraph_id TEXT,
            start_offset INTEGER,
            end_offset INTEGER,
            evidence_text TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            evidence_backcut_status TEXT NOT NULL,
            l5_2_seed_checksum TEXT NOT NULL,
            stable_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_confirmed_event_blocked_audit (
            blocked_audit_id TEXT PRIMARY KEY,
            normalized_event_id TEXT NOT NULL,
            current_decision_id TEXT,
            review_batch_id TEXT,
            review_source_file_hash TEXT,
            block_reason TEXT NOT NULL,
            detail_message TEXT,
            normalized_event_present INTEGER NOT NULL,
            decision_present INTEGER NOT NULL,
            conflict_present INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_confirmed_event_candidate_run (
            run_id TEXT PRIMARY KEY,
            input_current_decision_count INTEGER NOT NULL,
            approved_current_decision_count INTEGER NOT NULL,
            confirmed_event_candidate_count INTEGER NOT NULL,
            confirmed_event_argument_candidate_count INTEGER NOT NULL,
            confirmed_event_evidence_span_count INTEGER NOT NULL,
            blocked_audit_count INTEGER NOT NULL,
            source_fingerprints_before_json TEXT NOT NULL,
            source_fingerprints_after_json TEXT NOT NULL,
            source_mutation_detected INTEGER NOT NULL,
            input_fingerprints_before_json TEXT NOT NULL,
            input_fingerprints_after_json TEXT NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            stable_output_hashes_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT
        );
        """
    )


def clear_l5_5_tables(conn: sqlite3.Connection) -> None:
    for table in reversed(L5_5_TABLES):
        conn.execute(f"DELETE FROM {table}")


def require_sources(conn: sqlite3.Connection) -> None:
    required = set(L5_3_SOURCE_TABLES + L5_4_INPUT_TABLES)
    missing = sorted(table for table in required if not object_exists(conn, table, "table"))
    if missing:
        raise RuntimeError(f"Missing required L5.3/L5.4 tables: {missing}")


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


def decision_to_block_reason(decision: str | None, has_conflict: bool, source_present: bool) -> str:
    if not source_present:
        return "blocked_by_source_missing"
    if has_conflict:
        return "blocked_by_conflict"
    if decision is None:
        return "blocked_by_missing_review"
    return {
        "duplicate_candidate": "blocked_by_duplicate",
        "rejected": "blocked_by_rejected",
        "needs_context": "blocked_by_needs_context",
        "uncertain": "blocked_by_uncertain",
        "weak_candidate": "blocked_by_weak_candidate",
    }.get(decision, "blocked_by_conflict")


def build_rows(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    normalized_events = fetch_rows(conn, "l5_normalized_event_candidate")
    arguments = fetch_rows(conn, "l5_normalized_event_argument")
    evidences = fetch_rows(conn, "l5_normalized_event_evidence")
    current_rows = fetch_rows(conn, "l5_review_decision_current")
    conflicts = fetch_rows(conn, "l5_review_decision_conflict_audit")

    events_by_id = {row["normalized_event_candidate_id"]: row for row in normalized_events}
    current_by_id = {row["normalized_event_id"]: row for row in current_rows}
    args_by_event: dict[str, list[dict[str, Any]]] = {}
    for row in arguments:
        args_by_event.setdefault(str(row["normalized_event_candidate_id"]), []).append(row)
    evidences_by_event: dict[str, list[dict[str, Any]]] = {}
    for row in evidences:
        evidences_by_event.setdefault(str(row["normalized_event_candidate_id"]), []).append(row)
    conflicted_ids = {str(row["normalized_event_id"]) for row in conflicts if str(row.get("severity") or "") == "error"}

    confirmed_rows: list[dict[str, Any]] = []
    confirmed_arg_rows: list[dict[str, Any]] = []
    confirmed_evidence_rows: list[dict[str, Any]] = []
    blocked_rows: list[dict[str, Any]] = []

    for event_id, event in events_by_id.items():
        current = current_by_id.get(event_id)
        has_conflict = event_id in conflicted_ids
        decision = str(current.get("human_decision") or "") if current else None
        if current and decision == "approved_candidate" and not has_conflict:
            confirmed_id = stable_id("l5c_evt", event_id)
            current_decision_id = str(current["review_import_id"])
            confirmed_hash = sha256_text(
                json.dumps(
                    [
                        confirmed_id,
                        event_id,
                        current_decision_id,
                        current["review_batch_id"],
                        current["source_file_hash"],
                        event.get("stable_hash", ""),
                    ],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            confirmed_rows.append(
                {
                    "confirmed_event_candidate_id": confirmed_id,
                    "normalized_event_id": event_id,
                    "current_decision_id": current_decision_id,
                    "review_batch_id": str(current["review_batch_id"]),
                    "review_source_file_hash": str(current["source_file_hash"]),
                    "confirmation_status": "confirmed_candidate",
                    "confirmed_candidate_hash": confirmed_hash,
                    "source_event_candidate_id": str(event["source_event_candidate_id"]),
                    "chapter_id": event.get("chapter_id", ""),
                    "chapter_num": event.get("chapter_num"),
                    "chapter_title": event.get("chapter_title", ""),
                    "version_id": event.get("version_id", ""),
                    "scene_block_id": event.get("scene_block_id", ""),
                    "scene_block_table_name": event.get("scene_block_table_name", ""),
                    "l5_2_event_type": str(event["l5_2_event_type"]),
                    "l5_2_event_subtype": event.get("l5_2_event_subtype", ""),
                    "subject_text": str(event["subject_text"]),
                    "subject_entity_kind": event.get("subject_entity_kind", ""),
                    "normalized_confidence_score": event.get("normalized_confidence_score"),
                    "evidence_backcut_status": event.get("evidence_backcut_status", ""),
                    "source_normalized_stable_hash": str(event["stable_hash"]),
                    "created_at": CREATED_AT,
                }
            )
            for argument in args_by_event.get(event_id, []):
                confirmed_arg_rows.append(
                    {
                        "confirmed_argument_candidate_id": stable_id("l5c_arg", confirmed_id, argument["normalized_argument_id"]),
                        "confirmed_event_candidate_id": confirmed_id,
                        "normalized_argument_id": str(argument["normalized_argument_id"]),
                        "normalized_event_id": event_id,
                        "source_event_candidate_id": str(argument["source_event_candidate_id"]),
                        "argument_role": str(argument["argument_role"]),
                        "entity_kind": argument.get("entity_kind", ""),
                        "argument_text": str(argument["argument_text"]),
                        "confidence": argument.get("confidence"),
                        "source_kind": argument.get("source_kind", ""),
                        "source_rule": argument.get("source_rule", ""),
                        "evidence_text": argument.get("evidence_text", ""),
                        "l5_2_seed_checksum": str(argument["l5_2_seed_checksum"]),
                        "stable_hash": sha256_text(json.dumps([confirmed_id, argument["stable_hash"]], ensure_ascii=False, separators=(",", ":"))),
                        "created_at": CREATED_AT,
                    }
                )
            for evidence in evidences_by_event.get(event_id, []):
                confirmed_evidence_rows.append(
                    {
                        "confirmed_evidence_span_id": stable_id("l5c_evd", confirmed_id, evidence["normalized_evidence_id"]),
                        "confirmed_event_candidate_id": confirmed_id,
                        "normalized_evidence_id": str(evidence["normalized_evidence_id"]),
                        "normalized_event_id": event_id,
                        "source_event_candidate_id": str(evidence["source_event_candidate_id"]),
                        "evidence_source_kind": evidence.get("evidence_source_kind", ""),
                        "l2_sentence_id": evidence.get("l2_sentence_id", ""),
                        "l2_paragraph_id": evidence.get("l2_paragraph_id", ""),
                        "start_offset": evidence.get("start_offset"),
                        "end_offset": evidence.get("end_offset"),
                        "evidence_text": str(evidence["evidence_text"]),
                        "evidence_hash": str(evidence["evidence_hash"]),
                        "evidence_backcut_status": str(evidence["evidence_backcut_status"]),
                        "l5_2_seed_checksum": str(evidence["l5_2_seed_checksum"]),
                        "stable_hash": sha256_text(json.dumps([confirmed_id, evidence["stable_hash"]], ensure_ascii=False, separators=(",", ":"))),
                        "created_at": CREATED_AT,
                    }
                )
            continue

        block_reason = decision_to_block_reason(decision, has_conflict, True)
        blocked_rows.append(
            {
                "blocked_audit_id": stable_id("l5c_blk", event_id, block_reason),
                "normalized_event_id": event_id,
                "current_decision_id": str(current["review_import_id"]) if current else "",
                "review_batch_id": str(current["review_batch_id"]) if current else "",
                "review_source_file_hash": str(current["source_file_hash"]) if current else "",
                "block_reason": block_reason,
                "detail_message": f"normalized_event_id={event_id} blocked before confirmation",
                "normalized_event_present": 1,
                "decision_present": 1 if current else 0,
                "conflict_present": 1 if has_conflict else 0,
                "created_at": CREATED_AT,
            }
        )

    for event_id, current in current_by_id.items():
        if event_id in events_by_id:
            continue
        blocked_rows.append(
            {
                "blocked_audit_id": stable_id("l5c_blk", event_id, "blocked_by_source_missing"),
                "normalized_event_id": event_id,
                "current_decision_id": str(current["review_import_id"]),
                "review_batch_id": str(current["review_batch_id"]),
                "review_source_file_hash": str(current["source_file_hash"]),
                "block_reason": "blocked_by_source_missing",
                "detail_message": f"normalized_event_id={event_id} missing from L5.3 source table",
                "normalized_event_present": 0,
                "decision_present": 1,
                "conflict_present": 1 if event_id in conflicted_ids else 0,
                "created_at": CREATED_AT,
            }
        )

    counts = {
        "input_normalized_event_count": len(normalized_events),
        "input_current_decision_count": len(current_rows),
        "approved_current_decision_count": sum(1 for row in current_rows if str(row.get("human_decision") or "") == "approved_candidate"),
    }
    return confirmed_rows, confirmed_arg_rows, confirmed_evidence_rows, blocked_rows, counts


def stable_output_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for table in (
        "l5_confirmed_event_candidate",
        "l5_confirmed_event_argument_candidate",
        "l5_confirmed_event_evidence_span",
        "l5_confirmed_event_blocked_audit",
    ):
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        hashes[table] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashes


def run_l5_confirmed_event_candidate_indexer(
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
        source_before = table_fingerprints(conn, L5_3_SOURCE_TABLES)
        input_before = table_fingerprints(conn, L5_4_INPUT_TABLES)
        if rebuild:
            clear_l5_5_tables(conn)
        confirmed_rows, confirmed_arg_rows, confirmed_evidence_rows, blocked_rows, counts = build_rows(conn)
        insert_rows(conn, "l5_confirmed_event_candidate", confirmed_rows)
        insert_rows(conn, "l5_confirmed_event_argument_candidate", confirmed_arg_rows)
        insert_rows(conn, "l5_confirmed_event_evidence_span", confirmed_evidence_rows)
        insert_rows(conn, "l5_confirmed_event_blocked_audit", blocked_rows)
        stable_hashes = stable_output_hashes(conn)
        source_after = table_fingerprints(conn, L5_3_SOURCE_TABLES)
        input_after = table_fingerprints(conn, L5_4_INPUT_TABLES)
        source_mutation = source_before != source_after
        input_mutation = input_before != input_after
        run_id = stable_id("l5c_run", counts["input_current_decision_count"], len(confirmed_rows), len(blocked_rows))
        conn.execute(
            """
            INSERT INTO l5_confirmed_event_candidate_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                counts["input_current_decision_count"],
                counts["approved_current_decision_count"],
                len(confirmed_rows),
                len(confirmed_arg_rows),
                len(confirmed_evidence_rows),
                len(blocked_rows),
                json.dumps(source_before, ensure_ascii=False, sort_keys=True),
                json.dumps(source_after, ensure_ascii=False, sort_keys=True),
                int(source_mutation),
                json.dumps(input_before, ensure_ascii=False, sort_keys=True),
                json.dumps(input_after, ensure_ascii=False, sort_keys=True),
                int(input_mutation),
                json.dumps(stable_hashes, ensure_ascii=False, sort_keys=True),
                CREATED_AT,
                CREATED_AT,
                "ok",
                "",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from scripts.l5_confirmed_event_candidate_reporter import run_l5_confirmed_event_candidate_reporter

    report_manifest = run_l5_confirmed_event_candidate_reporter(root, output_dir=out_dir)
    blocked_reason_counts = report_manifest["blocked_reason_counts"]
    manifest = {
        "export_layer": "L5.5 Confirmed Event Candidate Index",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": {
            **counts,
            "confirmed_event_candidate_count": len(confirmed_rows),
            "confirmed_event_argument_candidate_count": len(confirmed_arg_rows),
            "confirmed_event_evidence_span_count": len(confirmed_evidence_rows),
            "blocked_audit_count": len(blocked_rows),
        },
        "blocked_reason_counts": blocked_reason_counts,
        "source_mutation_detected": source_mutation,
        "input_mutation_detected": input_mutation,
        "source_fingerprints_before": source_before,
        "source_fingerprints_after": source_after,
        "input_fingerprints_before": input_before,
        "input_fingerprints_after": input_after,
        "stable_output_hashes": stable_hashes,
        "reporter_manifest": report_manifest,
    }
    write_json(out_dir / MANIFEST_JSON, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L5.5 confirmed event candidate index.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    manifest = run_l5_confirmed_event_candidate_indexer(args.project_dir, output_dir=args.output_dir, rebuild=args.rebuild)
    print(f"L5.5 confirmed event candidate rows: {manifest['row_counts']['confirmed_event_candidate_count']}")


if __name__ == "__main__":
    main()
