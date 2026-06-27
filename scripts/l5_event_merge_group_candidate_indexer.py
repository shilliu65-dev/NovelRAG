from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import object_exists


CREATED_AT = "1970-01-01T00:00:00"
MANIFEST_JSON = "l5_event_merge_group_candidate_manifest.json"

L5_6_TABLES = (
    "l5_event_merge_group_candidate",
    "l5_event_merge_group_member",
    "l5_event_merge_group_evidence",
    "l5_event_merge_group_audit",
    "l5_event_merge_group_run",
)
L5_5_INPUT_TABLES = (
    "l5_confirmed_event_candidate",
    "l5_confirmed_event_argument_candidate",
    "l5_confirmed_event_evidence_span",
    "l5_confirmed_event_blocked_audit",
    "l5_confirmed_event_candidate_run",
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
        CREATE TABLE IF NOT EXISTS l5_event_merge_group_candidate (
            merge_group_candidate_id TEXT PRIMARY KEY,
            representative_confirmed_event_candidate_id TEXT NOT NULL,
            group_signature TEXT NOT NULL,
            group_status TEXT NOT NULL,
            group_rule TEXT NOT NULL,
            group_confidence TEXT,
            member_count INTEGER NOT NULL,
            chapter_num_min INTEGER,
            chapter_num_max INTEGER,
            scene_block_id_primary TEXT,
            event_type TEXT,
            event_subtype TEXT,
            predicate_canonical TEXT,
            subject_text TEXT,
            object_text TEXT,
            location_text TEXT,
            time_text TEXT,
            merge_group_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_merge_group_member (
            merge_group_member_id TEXT PRIMARY KEY,
            merge_group_candidate_id TEXT NOT NULL,
            confirmed_event_candidate_id TEXT NOT NULL,
            normalized_event_id TEXT NOT NULL,
            member_role TEXT NOT NULL,
            member_rank INTEGER NOT NULL,
            membership_rule TEXT NOT NULL,
            membership_score REAL,
            member_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_merge_group_evidence (
            merge_group_evidence_id TEXT PRIMARY KEY,
            merge_group_candidate_id TEXT NOT NULL,
            confirmed_event_candidate_id TEXT NOT NULL,
            confirmed_evidence_id TEXT,
            evidence_text_preview TEXT,
            evidence_hash TEXT,
            evidence_role TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_merge_group_audit (
            audit_id TEXT PRIMARY KEY,
            confirmed_event_candidate_id TEXT,
            merge_group_candidate_id TEXT,
            audit_type TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_merge_group_run (
            run_id TEXT PRIMARY KEY,
            input_confirmed_event_candidate_count INTEGER NOT NULL,
            merge_group_candidate_count INTEGER NOT NULL,
            merge_group_member_count INTEGER NOT NULL,
            singleton_group_count INTEGER NOT NULL,
            multi_member_group_count INTEGER NOT NULL,
            audit_count INTEGER NOT NULL,
            source_mutation_detected INTEGER NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            l5_5_fingerprint TEXT,
            run_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def clear_l5_6_tables(conn: sqlite3.Connection) -> None:
    for table in reversed(L5_6_TABLES):
        conn.execute(f"DELETE FROM {table}")


def require_sources(conn: sqlite3.Connection) -> None:
    missing = sorted(table for table in L5_5_INPUT_TABLES if not object_exists(conn, table, "table"))
    if missing:
        raise RuntimeError(f"Missing required L5.5 tables: {missing}")


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


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def argument_values(arguments: list[dict[str, Any]], confirmed_id: str) -> dict[str, str]:
    roles: dict[str, str] = {}
    for row in arguments:
        if str(row.get("confirmed_event_candidate_id") or "") != confirmed_id:
            continue
        role = normalize_text(row.get("argument_role"))
        text = str(row.get("argument_text") or "").strip()
        if not text:
            continue
        if role in {"object", "target", "patient", "recipient"} and "object_text" not in roles:
            roles["object_text"] = text
        elif role in {"location", "place"} and "location_text" not in roles:
            roles["location_text"] = text
        elif role in {"time", "temporal"} and "time_text" not in roles:
            roles["time_text"] = text
    return roles


def enriched_event(event: dict[str, Any], arguments: list[dict[str, Any]]) -> dict[str, Any]:
    values = argument_values(arguments, str(event["confirmed_event_candidate_id"]))
    return {
        **event,
        "predicate_canonical": str(event.get("l5_2_event_type") or ""),
        "object_text": values.get("object_text", ""),
        "location_text": values.get("location_text", ""),
        "time_text": values.get("time_text", ""),
    }


def group_signature(event: dict[str, Any]) -> str:
    payload = [
        event.get("chapter_num"),
        normalize_text(event.get("scene_block_id")),
        normalize_text(event.get("subject_text")),
        normalize_text(event.get("predicate_canonical")),
        normalize_text(event.get("object_text")),
    ]
    return sha256_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def stable_output_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for table in (
        "l5_event_merge_group_candidate",
        "l5_event_merge_group_member",
        "l5_event_merge_group_evidence",
        "l5_event_merge_group_audit",
    ):
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        hashes[table] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashes


def build_rows(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
    arguments = fetch_rows(conn, "l5_confirmed_event_argument_candidate")
    evidence_spans = fetch_rows(conn, "l5_confirmed_event_evidence_span")
    evidence_by_confirmed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_spans:
        evidence_by_confirmed[str(row["confirmed_event_candidate_id"])].append(row)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in confirmed:
        event = enriched_event(row, arguments)
        groups[group_signature(event)].append(event)

    candidate_rows: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for signature, members in sorted(groups.items(), key=lambda item: item[0]):
        members = sorted(members, key=lambda row: (row.get("chapter_num") or 0, str(row.get("scene_block_id") or ""), str(row["confirmed_event_candidate_id"])))
        representative = members[0]
        member_count = len(members)
        group_rule = "exact_event_signature" if member_count > 1 else "singleton_group"
        group_id = stable_id("l5mg", signature)
        chapter_values = [int(row["chapter_num"]) for row in members if row.get("chapter_num") is not None]
        group_hash = sha256_text(json.dumps([group_id, signature, [row["confirmed_event_candidate_id"] for row in members]], ensure_ascii=False, separators=(",", ":")))
        candidate_rows.append(
            {
                "merge_group_candidate_id": group_id,
                "representative_confirmed_event_candidate_id": representative["confirmed_event_candidate_id"],
                "group_signature": signature,
                "group_status": "merge_group_candidate",
                "group_rule": group_rule,
                "group_confidence": "high" if member_count > 1 else "medium",
                "member_count": member_count,
                "chapter_num_min": min(chapter_values) if chapter_values else None,
                "chapter_num_max": max(chapter_values) if chapter_values else None,
                "scene_block_id_primary": representative.get("scene_block_id", ""),
                "event_type": representative.get("l5_2_event_type", ""),
                "event_subtype": representative.get("l5_2_event_subtype", ""),
                "predicate_canonical": representative.get("predicate_canonical", ""),
                "subject_text": representative.get("subject_text", ""),
                "object_text": representative.get("object_text", ""),
                "location_text": representative.get("location_text", ""),
                "time_text": representative.get("time_text", ""),
                "merge_group_hash": group_hash,
                "created_at": CREATED_AT,
            }
        )
        audit_type = "multi_member_group" if member_count > 1 else "singleton_group"
        audit_rows.append(
            {
                "audit_id": stable_id("l5mg_aud", group_id, audit_type),
                "confirmed_event_candidate_id": representative["confirmed_event_candidate_id"],
                "merge_group_candidate_id": group_id,
                "audit_type": audit_type,
                "description": f"{group_rule} created with {member_count} member(s)",
                "severity": "info",
                "created_at": CREATED_AT,
            }
        )
        for rank, member in enumerate(members, start=1):
            role = "representative" if rank == 1 else "member"
            member_hash = sha256_text(json.dumps([group_id, member["confirmed_event_candidate_id"], role, rank], ensure_ascii=False, separators=(",", ":")))
            member_rows.append(
                {
                    "merge_group_member_id": stable_id("l5mg_mem", group_id, member["confirmed_event_candidate_id"]),
                    "merge_group_candidate_id": group_id,
                    "confirmed_event_candidate_id": member["confirmed_event_candidate_id"],
                    "normalized_event_id": member["normalized_event_id"],
                    "member_role": role,
                    "member_rank": rank,
                    "membership_rule": group_rule,
                    "membership_score": 1.0 if group_rule == "exact_event_signature" else 0.5,
                    "member_hash": member_hash,
                    "created_at": CREATED_AT,
                }
            )
            for evidence in evidence_by_confirmed.get(str(member["confirmed_event_candidate_id"]), []):
                preview = str(evidence.get("evidence_text") or "")[:160]
                evidence_rows.append(
                    {
                        "merge_group_evidence_id": stable_id("l5mg_evd", group_id, evidence["confirmed_evidence_span_id"]),
                        "merge_group_candidate_id": group_id,
                        "confirmed_event_candidate_id": member["confirmed_event_candidate_id"],
                        "confirmed_evidence_id": evidence["confirmed_evidence_span_id"],
                        "evidence_text_preview": preview,
                        "evidence_hash": evidence.get("evidence_hash", ""),
                        "evidence_role": "member_evidence",
                        "created_at": CREATED_AT,
                    }
                )

    counts = {
        "input_confirmed_event_candidate_count": len(confirmed),
        "merge_group_candidate_count": len(candidate_rows),
        "merge_group_member_count": len(member_rows),
        "singleton_group_count": sum(1 for row in candidate_rows if int(row["member_count"]) == 1),
        "multi_member_group_count": sum(1 for row in candidate_rows if int(row["member_count"]) > 1),
        "audit_count": len(audit_rows),
    }
    return candidate_rows, member_rows, evidence_rows, audit_rows, counts


def run_l5_event_merge_group_candidate_indexer(
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
        source_before = table_fingerprints(conn, L5_5_INPUT_TABLES)
        input_before = table_fingerprints(conn, L5_5_INPUT_TABLES)
        if rebuild:
            clear_l5_6_tables(conn)
        candidate_rows, member_rows, evidence_rows, audit_rows, counts = build_rows(conn)
        insert_rows(conn, "l5_event_merge_group_candidate", candidate_rows)
        insert_rows(conn, "l5_event_merge_group_member", member_rows)
        insert_rows(conn, "l5_event_merge_group_evidence", evidence_rows)
        insert_rows(conn, "l5_event_merge_group_audit", audit_rows)
        stable_hashes = stable_output_hashes(conn)
        source_after = table_fingerprints(conn, L5_5_INPUT_TABLES)
        input_after = table_fingerprints(conn, L5_5_INPUT_TABLES)
        source_mutation = source_before != source_after
        input_mutation = input_before != input_after
        l5_5_fingerprint = sha256_text(json.dumps(source_before, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        run_hash = sha256_text(json.dumps([counts, stable_hashes, l5_5_fingerprint], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        conn.execute(
            "INSERT INTO l5_event_merge_group_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_id("l5mg_run", run_hash),
                counts["input_confirmed_event_candidate_count"],
                counts["merge_group_candidate_count"],
                counts["merge_group_member_count"],
                counts["singleton_group_count"],
                counts["multi_member_group_count"],
                counts["audit_count"],
                int(source_mutation),
                int(input_mutation),
                l5_5_fingerprint,
                run_hash,
                CREATED_AT,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from scripts.l5_event_merge_group_candidate_reporter import run_l5_event_merge_group_candidate_reporter

    report_manifest = run_l5_event_merge_group_candidate_reporter(root, output_dir=out_dir)
    manifest = {
        "export_layer": "L5.6 Event Merge Group Candidate",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": counts,
        "audit_type_counts": report_manifest["audit_type_counts"],
        "source_mutation_detected": source_mutation,
        "input_mutation_detected": input_mutation,
        "l5_5_fingerprint": l5_5_fingerprint,
        "stable_output_hashes": stable_hashes,
        "reporter_manifest": report_manifest,
    }
    write_json(out_dir / MANIFEST_JSON, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L5.6 event merge group candidates.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    manifest = run_l5_event_merge_group_candidate_indexer(args.project_dir, output_dir=args.output_dir, rebuild=args.rebuild)
    print(f"L5.6 merge group candidate rows: {manifest['row_counts']['merge_group_candidate_count']}")


if __name__ == "__main__":
    main()
