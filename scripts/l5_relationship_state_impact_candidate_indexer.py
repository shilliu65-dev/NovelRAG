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
MANIFEST_JSON = "l5_relationship_state_impact_candidate_manifest.json"

L5_8_TABLES = (
    "l5_relationship_impact_candidate",
    "l5_state_impact_candidate",
    "l5_impact_candidate_evidence",
    "l5_relationship_state_impact_audit",
    "l5_relationship_state_impact_run",
)
L5_8_INPUT_TABLES = (
    "l5_confirmed_event_candidate",
    "l5_confirmed_event_argument_candidate",
    "l5_confirmed_event_evidence_span",
    "l5_event_merge_group_candidate",
    "l5_event_merge_group_member",
    "l5_timeline_anchor_candidate",
    "l5_timeline_relative_order_candidate",
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
        result[table] = {"exists": True, "row_count": len(rows), "columns": columns, "aggregate_hash": sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}
    return result


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l5_relationship_impact_candidate (
            relationship_impact_candidate_id TEXT PRIMARY KEY,
            merge_group_candidate_id TEXT NOT NULL,
            timeline_anchor_candidate_id TEXT,
            source_confirmed_event_candidate_id TEXT NOT NULL,
            subject_text TEXT NOT NULL,
            object_text TEXT NOT NULL,
            subject_entity_candidate_id TEXT,
            object_entity_candidate_id TEXT,
            relationship_impact_type TEXT NOT NULL,
            impact_direction TEXT NOT NULL,
            predicate_canonical TEXT,
            evidence_text_preview TEXT,
            confidence TEXT,
            impact_rule TEXT NOT NULL,
            relationship_impact_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_state_impact_candidate (
            state_impact_candidate_id TEXT PRIMARY KEY,
            merge_group_candidate_id TEXT NOT NULL,
            timeline_anchor_candidate_id TEXT,
            source_confirmed_event_candidate_id TEXT NOT NULL,
            entity_text TEXT NOT NULL,
            entity_candidate_id TEXT,
            state_impact_type TEXT NOT NULL,
            state_before TEXT,
            state_after TEXT,
            location_text TEXT,
            power_text TEXT,
            predicate_canonical TEXT,
            evidence_text_preview TEXT,
            confidence TEXT,
            impact_rule TEXT NOT NULL,
            state_impact_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_impact_candidate_evidence (
            impact_evidence_id TEXT PRIMARY KEY,
            impact_candidate_type TEXT NOT NULL,
            impact_candidate_id TEXT NOT NULL,
            source_confirmed_event_candidate_id TEXT NOT NULL,
            confirmed_evidence_id TEXT,
            evidence_text_preview TEXT,
            evidence_hash TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_relationship_state_impact_audit (
            audit_id TEXT PRIMARY KEY,
            source_confirmed_event_candidate_id TEXT,
            merge_group_candidate_id TEXT,
            audit_type TEXT NOT NULL,
            description TEXT NOT NULL,
            severity TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_relationship_state_impact_run (
            run_id TEXT PRIMARY KEY,
            input_confirmed_event_candidate_count INTEGER NOT NULL,
            input_merge_group_candidate_count INTEGER NOT NULL,
            input_timeline_anchor_candidate_count INTEGER NOT NULL,
            relationship_impact_candidate_count INTEGER NOT NULL,
            state_impact_candidate_count INTEGER NOT NULL,
            impact_evidence_count INTEGER NOT NULL,
            audit_count INTEGER NOT NULL,
            source_mutation_detected INTEGER NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            l5_5_fingerprint TEXT,
            l5_6_fingerprint TEXT,
            l5_7_fingerprint TEXT,
            run_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def clear_l5_8_tables(conn: sqlite3.Connection) -> None:
    for table in reversed(L5_8_TABLES):
        conn.execute(f"DELETE FROM {table}")


def require_sources(conn: sqlite3.Connection) -> None:
    missing = sorted(table for table in L5_8_INPUT_TABLES if not object_exists(conn, table, "table"))
    if missing:
        raise RuntimeError(f"Missing required L5.5/L5.6/L5.7 tables: {missing}")


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


def first_evidence(evidence_by_confirmed: dict[str, list[dict[str, Any]]], confirmed_id: str) -> dict[str, Any]:
    rows = evidence_by_confirmed.get(confirmed_id, [])
    return rows[0] if rows else {}


def argument_bucket(arguments: list[dict[str, Any]]) -> dict[str, dict[str, list[str]]]:
    bucket: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for row in arguments:
        confirmed_id = str(row["confirmed_event_candidate_id"])
        role = normalize_text(row.get("argument_role"))
        text = str(row.get("argument_text") or "").strip()
        if text:
            bucket[confirmed_id][role].append(text)
    return bucket


def first_role(bucket: dict[str, dict[str, list[str]]], confirmed_id: str, roles: tuple[str, ...]) -> str:
    values = bucket.get(confirmed_id, {})
    for role in roles:
        if values.get(role):
            return values[role][0]
    return ""


def relationship_type(predicate: str) -> str:
    text = normalize_text(predicate)
    if any(token in text for token in ("attack", "injury", "death", "conflict", "wound", "kill")):
        return "conflict"
    if any(token in text for token in ("help", "rescue", "protect", "cooperate", "deliver")):
        return "cooperation"
    if any(token in text for token in ("order", "judge", "appoint", "punish", "control")):
        return "authority"
    if any(token in text for token in ("reveal", "discover", "inform", "hide")):
        return "information"
    return "unknown_interaction"


def state_impact(predicate: str, location_text: str, object_text: str) -> tuple[str, str, str, str]:
    text = normalize_text(" ".join([predicate, object_text]))
    if any(token in text for token in ("death", "die", "kill")):
        return "life_status", "", "dead_or_at_risk", "event_type_state_hint"
    if any(token in text for token in ("injury", "wound", "attack")):
        return "injury_status", "", "injured_or_at_risk", "event_type_state_hint"
    if any(token in text for token in ("power", "upgrade", "awaken", "realm")):
        return "power_status", "", "power_changed", "power_state_hint"
    if location_text and any(token in text for token in ("movement", "arrive", "leave", "enter", "escape")):
        return "location_status", "", location_text, "location_state_hint"
    if any(token in text for token in ("reveal", "identity")):
        return "identity_status", "", "identity_information_changed", "event_type_state_hint"
    return "unknown_state", "", "", "uncertainty_fallback"


def build_rows(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
    arguments = fetch_rows(conn, "l5_confirmed_event_argument_candidate")
    evidence_spans = fetch_rows(conn, "l5_confirmed_event_evidence_span")
    groups = fetch_rows(conn, "l5_event_merge_group_candidate")
    members = fetch_rows(conn, "l5_event_merge_group_member")
    anchors = fetch_rows(conn, "l5_timeline_anchor_candidate")
    arg_bucket = argument_bucket(arguments)
    group_by_confirmed = {str(row["confirmed_event_candidate_id"]): str(row["merge_group_candidate_id"]) for row in members}
    anchor_by_group = {str(row["merge_group_candidate_id"]): str(row["timeline_anchor_candidate_id"]) for row in anchors}
    group_by_id = {str(row["merge_group_candidate_id"]): row for row in groups}
    evidence_by_confirmed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in evidence_spans:
        evidence_by_confirmed[str(row["confirmed_event_candidate_id"])].append(row)
    character_tables_missing = not object_exists(conn, "l3_character_def", "table")

    relationship_rows: list[dict[str, Any]] = []
    state_rows: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []

    for event in confirmed:
        confirmed_id = str(event["confirmed_event_candidate_id"])
        group_id = group_by_confirmed.get(confirmed_id, "")
        group = group_by_id.get(group_id, {})
        anchor_id = anchor_by_group.get(group_id, "")
        subject_text = first_role(arg_bucket, confirmed_id, ("subject", "agent")) or str(event.get("subject_text") or "")
        object_text = first_role(arg_bucket, confirmed_id, ("object", "target", "patient", "recipient")) or str(group.get("object_text") or "")
        location_text = first_role(arg_bucket, confirmed_id, ("location", "place")) or str(group.get("location_text") or "")
        predicate = str(group.get("predicate_canonical") or event.get("l5_2_event_type") or "")
        evidence = first_evidence(evidence_by_confirmed, confirmed_id)
        evidence_preview = str(evidence.get("evidence_text") or "")[:160]
        evidence_hash = str(evidence.get("evidence_hash") or "")
        confirmed_evidence_id = str(evidence.get("confirmed_evidence_span_id") or "")

        if not subject_text:
            audit_rows.append({"audit_id": stable_id("l5imp_aud", confirmed_id, "missing_subject"), "source_confirmed_event_candidate_id": confirmed_id, "merge_group_candidate_id": group_id, "audit_type": "missing_subject", "description": "source event has no subject text", "severity": "warning", "created_at": CREATED_AT})
        if not object_text:
            audit_rows.append({"audit_id": stable_id("l5imp_aud", confirmed_id, "missing_object"), "source_confirmed_event_candidate_id": confirmed_id, "merge_group_candidate_id": group_id, "audit_type": "missing_object", "description": "source event has no object text", "severity": "info", "created_at": CREATED_AT})
        if subject_text and object_text and normalize_text(subject_text) == normalize_text(object_text):
            audit_rows.append({"audit_id": stable_id("l5imp_aud", confirmed_id, "same_subject_object"), "source_confirmed_event_candidate_id": confirmed_id, "merge_group_candidate_id": group_id, "audit_type": "same_subject_object", "description": "subject and object text are the same; relationship candidate skipped", "severity": "warning", "created_at": CREATED_AT})
        elif subject_text and object_text:
            impact_type = relationship_type(predicate)
            impact_rule = "subject_object_interaction" if impact_type == "unknown_interaction" else f"{impact_type}_action_hint"
            relationship_id = stable_id("l5rel", group_id, confirmed_id, subject_text, object_text, impact_type)
            relationship_hash = sha256_text(json.dumps([relationship_id, group_id, anchor_id, confirmed_id, subject_text, object_text, impact_type], ensure_ascii=False, separators=(",", ":")))
            relationship_rows.append(
                {
                    "relationship_impact_candidate_id": relationship_id,
                    "merge_group_candidate_id": group_id,
                    "timeline_anchor_candidate_id": anchor_id,
                    "source_confirmed_event_candidate_id": confirmed_id,
                    "subject_text": subject_text,
                    "object_text": object_text,
                    "subject_entity_candidate_id": "",
                    "object_entity_candidate_id": "",
                    "relationship_impact_type": impact_type,
                    "impact_direction": "subject_to_object",
                    "predicate_canonical": predicate,
                    "evidence_text_preview": evidence_preview,
                    "confidence": "medium" if impact_type != "unknown_interaction" else "low",
                    "impact_rule": impact_rule,
                    "relationship_impact_hash": relationship_hash,
                    "created_at": CREATED_AT,
                }
            )
            evidence_rows.append({"impact_evidence_id": stable_id("l5imp_evd", "relationship", relationship_id, confirmed_evidence_id), "impact_candidate_type": "relationship", "impact_candidate_id": relationship_id, "source_confirmed_event_candidate_id": confirmed_id, "confirmed_evidence_id": confirmed_evidence_id, "evidence_text_preview": evidence_preview, "evidence_hash": evidence_hash, "created_at": CREATED_AT})

        state_type, state_before, state_after, state_rule = state_impact(predicate, location_text, object_text)
        if subject_text:
            state_id = stable_id("l5state", group_id, confirmed_id, state_type, subject_text, state_after)
            state_hash = sha256_text(json.dumps([state_id, group_id, anchor_id, confirmed_id, subject_text, state_type, state_after], ensure_ascii=False, separators=(",", ":")))
            state_rows.append(
                {
                    "state_impact_candidate_id": state_id,
                    "merge_group_candidate_id": group_id,
                    "timeline_anchor_candidate_id": anchor_id,
                    "source_confirmed_event_candidate_id": confirmed_id,
                    "entity_text": subject_text,
                    "entity_candidate_id": "",
                    "state_impact_type": state_type,
                    "state_before": state_before,
                    "state_after": state_after,
                    "location_text": location_text if state_type == "location_status" else "",
                    "power_text": object_text if state_type == "power_status" else "",
                    "predicate_canonical": predicate,
                    "evidence_text_preview": evidence_preview,
                    "confidence": "medium" if state_type != "unknown_state" else "low",
                    "impact_rule": state_rule,
                    "state_impact_hash": state_hash,
                    "created_at": CREATED_AT,
                }
            )
            evidence_rows.append({"impact_evidence_id": stable_id("l5imp_evd", "state", state_id, confirmed_evidence_id), "impact_candidate_type": "state", "impact_candidate_id": state_id, "source_confirmed_event_candidate_id": confirmed_id, "confirmed_evidence_id": confirmed_evidence_id, "evidence_text_preview": evidence_preview, "evidence_hash": evidence_hash, "created_at": CREATED_AT})
            if state_type == "unknown_state":
                audit_rows.append({"audit_id": stable_id("l5imp_aud", confirmed_id, "weak_state_signal"), "source_confirmed_event_candidate_id": confirmed_id, "merge_group_candidate_id": group_id, "audit_type": "weak_state_signal", "description": "state impact generated from uncertainty fallback", "severity": "info", "created_at": CREATED_AT})
        if character_tables_missing and (relationship_rows or state_rows):
            audit_rows.append({"audit_id": stable_id("l5imp_aud", confirmed_id, "raw_text_entity_fallback"), "source_confirmed_event_candidate_id": confirmed_id, "merge_group_candidate_id": group_id, "audit_type": "raw_text_entity_fallback", "description": "L3 entity table unavailable; retained raw text entity candidates", "severity": "info", "created_at": CREATED_AT})
        if not anchor_id:
            audit_rows.append({"audit_id": stable_id("l5imp_aud", confirmed_id, "missing_timeline_anchor"), "source_confirmed_event_candidate_id": confirmed_id, "merge_group_candidate_id": group_id, "audit_type": "missing_timeline_anchor", "description": "no L5.7 anchor found for merge group", "severity": "warning", "created_at": CREATED_AT})

    counts = {
        "input_confirmed_event_candidate_count": len(confirmed),
        "input_merge_group_candidate_count": len(groups),
        "input_timeline_anchor_candidate_count": len(anchors),
        "relationship_impact_candidate_count": len(relationship_rows),
        "state_impact_candidate_count": len(state_rows),
        "impact_evidence_count": len(evidence_rows),
        "audit_count": len(audit_rows),
    }
    return relationship_rows, state_rows, evidence_rows, audit_rows, counts


def stable_output_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for table in ("l5_relationship_impact_candidate", "l5_state_impact_candidate", "l5_impact_candidate_evidence", "l5_relationship_state_impact_audit"):
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        hashes[table] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashes


def run_l5_relationship_state_impact_candidate_indexer(
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
        source_before = table_fingerprints(conn, L5_8_INPUT_TABLES)
        input_before = table_fingerprints(conn, L5_8_INPUT_TABLES)
        if rebuild:
            clear_l5_8_tables(conn)
        relationship_rows, state_rows, evidence_rows, audit_rows, counts = build_rows(conn)
        insert_rows(conn, "l5_relationship_impact_candidate", relationship_rows)
        insert_rows(conn, "l5_state_impact_candidate", state_rows)
        insert_rows(conn, "l5_impact_candidate_evidence", evidence_rows)
        insert_rows(conn, "l5_relationship_state_impact_audit", audit_rows)
        stable_hashes = stable_output_hashes(conn)
        source_after = table_fingerprints(conn, L5_8_INPUT_TABLES)
        input_after = table_fingerprints(conn, L5_8_INPUT_TABLES)
        source_mutation = source_before != source_after
        input_mutation = input_before != input_after
        l5_5_fingerprint = sha256_text(json.dumps({key: source_before[key] for key in source_before if key.startswith("l5_confirmed")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        l5_6_fingerprint = sha256_text(json.dumps({key: source_before[key] for key in source_before if key.startswith("l5_event_merge")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        l5_7_fingerprint = sha256_text(json.dumps({key: source_before[key] for key in source_before if key.startswith("l5_timeline")}, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        run_hash = sha256_text(json.dumps([counts, stable_hashes, l5_5_fingerprint, l5_6_fingerprint, l5_7_fingerprint], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        conn.execute(
            "INSERT INTO l5_relationship_state_impact_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                stable_id("l5imp_run", run_hash),
                counts["input_confirmed_event_candidate_count"],
                counts["input_merge_group_candidate_count"],
                counts["input_timeline_anchor_candidate_count"],
                counts["relationship_impact_candidate_count"],
                counts["state_impact_candidate_count"],
                counts["impact_evidence_count"],
                counts["audit_count"],
                int(source_mutation),
                int(input_mutation),
                l5_5_fingerprint,
                l5_6_fingerprint,
                l5_7_fingerprint,
                run_hash,
                CREATED_AT,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from scripts.l5_relationship_state_impact_candidate_reporter import run_l5_relationship_state_impact_candidate_reporter

    report_manifest = run_l5_relationship_state_impact_candidate_reporter(root, output_dir=out_dir)
    manifest = {
        "export_layer": "L5.8 Relationship / State Impact Candidate",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": counts,
        "audit_type_counts": report_manifest["audit_type_counts"],
        "source_mutation_detected": source_mutation,
        "input_mutation_detected": input_mutation,
        "l5_5_fingerprint": l5_5_fingerprint,
        "l5_6_fingerprint": l5_6_fingerprint,
        "l5_7_fingerprint": l5_7_fingerprint,
        "stable_output_hashes": stable_hashes,
        "reporter_manifest": report_manifest,
    }
    write_json(out_dir / MANIFEST_JSON, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L5.8 relationship/state impact candidates.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    manifest = run_l5_relationship_state_impact_candidate_indexer(args.project_dir, output_dir=args.output_dir, rebuild=args.rebuild)
    print(f"L5.8 relationship impact candidate rows: {manifest['row_counts']['relationship_impact_candidate_count']}")


if __name__ == "__main__":
    main()
