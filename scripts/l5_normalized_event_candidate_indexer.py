from __future__ import annotations

import argparse
import csv
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
from scripts.l5_event_candidate_extractor import discover_scene_block_source, scene_block_source_payload, table_columns
from scripts.l5_event_candidate_review_exporter import object_exists


CREATED_AT = "1970-01-01T00:00:00"
DEFAULT_SAMPLE_CHAPTERS = "1,2,1697"
DEFAULT_INPUT_CSV = Path("outputs") / "l5_event_candidate_review_enhanced.csv"
SEED_PATH = Path("config") / "event_normalization_rules.seed.json"

SAMPLE_JSON = "l5_normalized_event_candidates_sample.json"
SAMPLE_CSV = "l5_normalized_event_candidates_sample.csv"
SAMPLE_REPORT = "l5_normalized_event_candidates_sample_report.md"
STATE_JSON = "l5_normalized_state_change_candidates_sample.json"
STATE_CSV = "l5_normalized_state_change_candidates_sample.csv"
MANIFEST_JSON = "l5_normalized_event_candidate_manifest.json"

NORMALIZED_EVENT_TABLES = (
    "l5_normalized_event_candidate",
    "l5_normalized_event_argument",
    "l5_normalized_state_change_candidate",
    "l5_normalized_event_evidence",
    "l5_normalized_event_source_map",
    "l5_normalized_event_index_run",
    "l5_normalized_event_warning",
)
FORBIDDEN_TABLES = {
    "confirmed_event",
    "l5_confirmed_event",
    "l5_event_timeline",
    "l5_relationship_graph",
    "l5_event_merge_group",
    "l5_normalized_event",
    "l5_normalized_state_change",
}
READY_RECOMMENDATION = "ready_for_l5_3_candidate"
SKIPPED_RECOMMENDATIONS = {"likely_duplicate", "needs_context", "weak_candidate"}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def parse_json_array(value: str | None) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [item for item in parsed if isinstance(item, dict)]


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def parse_sample_chapters(sample_chapters: str | None) -> set[str]:
    value = sample_chapters or DEFAULT_SAMPLE_CHAPTERS
    return {part.strip() for part in value.split(",") if part.strip()}


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


def non_l5_3_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    for table_row in table_rows:
        table_name = str(table_row[0])
        if table_name in NORMALIZED_EVENT_TABLES:
            continue
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table_name)})")]
        if not columns:
            continue
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table_name)} ORDER BY {order_sql}")]
        result[table_name] = {
            "row_count": len(rows),
            "columns": columns,
            "aggregate_hash": sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        }
    return result


def load_seed(project_dir: Path) -> dict[str, Any]:
    path = project_dir / SEED_PATH
    if not path.exists():
        raise RuntimeError(f"Missing L5.2 seed: {path}")
    raw = path.read_text(encoding="utf-8")
    checksum = sha256_text(raw)
    data = json.loads(raw)

    event_types = {str(item.get("event_type")) for item in data.get("event_type_catalog", []) if isinstance(item, dict) and item.get("event_type")}
    state_types = {
        str(item.get("state_change_type"))
        for item in data.get("state_change_type_catalog", [])
        if isinstance(item, dict) and item.get("state_change_type")
    }
    argument_roles = {str(item.get("role") or item.get("argument_role")) for item in data.get("argument_role_catalog", []) if isinstance(item, dict) and (item.get("role") or item.get("argument_role"))}
    event_mapping = {
        str(item.get("event_type_candidate")): {
            "event_type": str(item.get("maps_to_event_type") or ""),
            "subtype": str(item.get("default_subtype") or ""),
        }
        for item in data.get("l5_event_type_candidate_mapping", [])
        if isinstance(item, dict) and item.get("event_type_candidate")
    }
    trigger_mapping = {
        str(item.get("trigger_category")): {
            "event_type": str(item.get("maps_to_event_type") or ""),
            "subtype": str(item.get("default_subtype") or ""),
        }
        for item in data.get("trigger_category_mapping", [])
        if isinstance(item, dict) and item.get("trigger_category")
    }
    state_mapping = {
        str(item.get("state_change_type_candidate")): str(item.get("maps_to_state_change_type") or "")
        for item in data.get("l5_state_change_type_candidate_mapping", [])
        if isinstance(item, dict) and item.get("state_change_type_candidate")
    }
    importance_by_event: dict[str, str] = {}
    for item in data.get("importance_rules", []):
        if not isinstance(item, dict):
            continue
        level = str(item.get("importance_level") or "")
        for event_type in item.get("applies_to_event_types", []) or item.get("conditions", {}).get("event_types_any", []):
            if level:
                importance_by_event[str(event_type)] = level

    if not event_types:
        raise RuntimeError("L5.2 seed has no event_type_catalog")
    if not argument_roles:
        raise RuntimeError("L5.2 seed has no argument_role_catalog")
    return {
        "path": str(path),
        "checksum": checksum,
        "data": data,
        "event_types": event_types,
        "state_types": state_types,
        "argument_roles": argument_roles,
        "event_mapping": event_mapping,
        "trigger_mapping": trigger_mapping,
        "state_mapping": state_mapping,
        "importance_by_event": importance_by_event,
    }


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l5_normalized_event_candidate (
            normalized_event_candidate_id TEXT PRIMARY KEY,
            source_event_candidate_id TEXT NOT NULL,
            source_event_candidate_hash TEXT,
            source_review_row_id TEXT,
            source_layer TEXT NOT NULL,
            chapter_id TEXT,
            chapter_num INTEGER,
            chapter_title TEXT,
            version_id TEXT,
            scene_block_id TEXT,
            scene_block_table_name TEXT,
            scene_block_source_status TEXT,
            scene_block_validity TEXT,
            trigger_text TEXT,
            trigger_rule_id TEXT,
            trigger_category TEXT,
            event_type_candidate TEXT,
            event_subtype_candidate TEXT,
            l5_2_event_type TEXT NOT NULL,
            l5_2_event_subtype TEXT,
            normalization_status TEXT NOT NULL,
            normalization_rule_id TEXT NOT NULL,
            subject_text TEXT NOT NULL,
            subject_entity_kind TEXT,
            subject_is_confirmed INTEGER NOT NULL,
            confidence_score REAL,
            normalized_confidence_score REAL,
            importance_level TEXT,
            evidence_backcut_status TEXT,
            evidence_backcut_hash TEXT,
            evidence_text_backcut TEXT,
            l5_2_seed_checksum TEXT NOT NULL,
            stable_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_normalized_event_argument (
            normalized_argument_id TEXT PRIMARY KEY,
            normalized_event_candidate_id TEXT NOT NULL,
            source_event_candidate_id TEXT NOT NULL,
            argument_role TEXT NOT NULL,
            entity_kind TEXT,
            argument_text TEXT NOT NULL,
            confidence REAL,
            source_kind TEXT,
            source_rule TEXT,
            evidence_text TEXT,
            is_confirmed INTEGER NOT NULL,
            l5_2_seed_checksum TEXT NOT NULL,
            stable_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_normalized_state_change_candidate (
            normalized_state_change_candidate_id TEXT PRIMARY KEY,
            normalized_event_candidate_id TEXT NOT NULL,
            source_event_candidate_id TEXT NOT NULL,
            state_change_type TEXT NOT NULL,
            subject_text TEXT,
            before_value TEXT,
            after_value TEXT,
            evidence_text TEXT,
            is_confirmed INTEGER NOT NULL,
            l5_2_seed_checksum TEXT NOT NULL,
            stable_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_normalized_event_evidence (
            normalized_evidence_id TEXT PRIMARY KEY,
            normalized_event_candidate_id TEXT NOT NULL,
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
        CREATE TABLE IF NOT EXISTS l5_normalized_event_source_map (
            source_map_id TEXT PRIMARY KEY,
            normalized_event_candidate_id TEXT NOT NULL,
            source_layer TEXT NOT NULL,
            source_kind TEXT NOT NULL,
            source_id TEXT,
            source_hash TEXT,
            source_field TEXT,
            source_value TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_normalized_event_index_run (
            run_id TEXT PRIMARY KEY,
            layer TEXT NOT NULL,
            sample_chapters TEXT NOT NULL,
            input_file TEXT NOT NULL,
            input_row_count INTEGER NOT NULL,
            inserted_event_count INTEGER NOT NULL,
            skipped_count INTEGER NOT NULL,
            l5_2_seed_checksum TEXT NOT NULL,
            source_fingerprints_before_json TEXT NOT NULL,
            source_fingerprints_after_json TEXT NOT NULL,
            source_mutation_detected INTEGER NOT NULL,
            input_hashes_before_json TEXT NOT NULL,
            input_hashes_after_json TEXT NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            stable_output_hashes_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS l5_normalized_event_warning (
            warning_id TEXT PRIMARY KEY,
            normalized_event_candidate_id TEXT,
            source_event_candidate_id TEXT,
            warning_code TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def clear_l5_3_tables(conn: sqlite3.Connection) -> None:
    for table in reversed(NORMALIZED_EVENT_TABLES):
        conn.execute(f"DELETE FROM {table}")


def best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    usable = [candidate for candidate in candidates if str(candidate.get("text") or "").strip()]
    if not usable:
        return None
    return max(usable, key=lambda candidate: float(candidate.get("confidence") or 0.0))


def normalize_event_type(row: dict[str, str], seed: dict[str, Any]) -> tuple[str, str, str] | None:
    candidate_type = row.get("event_type_candidate", "")
    if candidate_type in seed["event_types"]:
        return candidate_type, row.get("event_subtype_candidate", ""), "event_type_candidate_direct"
    mapped = seed["event_mapping"].get(candidate_type)
    if mapped and mapped["event_type"] in seed["event_types"]:
        return mapped["event_type"], mapped["subtype"], f"l5_event_type_candidate_mapping:{candidate_type}"
    trigger = seed["trigger_mapping"].get(row.get("trigger_category", ""))
    if trigger and trigger["event_type"] in seed["event_types"]:
        return trigger["event_type"], trigger["subtype"], f"trigger_category_mapping:{row.get('trigger_category', '')}"
    return None


def numeric(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except ValueError:
        return default


def integer(value: str | None) -> int | None:
    try:
        return int(value or "")
    except ValueError:
        return None


def importance_level(event_type: str, row: dict[str, str], seed: dict[str, Any]) -> str:
    if event_type in seed["importance_by_event"]:
        return seed["importance_by_event"][event_type]
    candidate = row.get("importance_candidate") or ""
    return candidate if candidate else "normal"


def argument_role_for(candidate: dict[str, Any], fallback: str, seed: dict[str, Any]) -> str:
    role = str(candidate.get("role") or fallback or "subject")
    if role in seed["argument_roles"]:
        return role
    for replacement in (fallback, "subject", "object", "unknown"):
        if replacement in seed["argument_roles"]:
            return replacement
    return sorted(seed["argument_roles"])[0]


def candidate_to_argument(
    normalized_event_id: str,
    source_event_id: str,
    candidate: dict[str, Any],
    role: str,
    seed_checksum: str,
) -> dict[str, Any]:
    text = str(candidate.get("text") or candidate.get("entity_text") or "").strip()
    stable = sha256_text(json.dumps([normalized_event_id, role, text, candidate.get("entity_kind", ""), seed_checksum], ensure_ascii=False, separators=(",", ":")))
    return {
        "normalized_argument_id": stable_id("l5n_arg", normalized_event_id, role, text),
        "normalized_event_candidate_id": normalized_event_id,
        "source_event_candidate_id": source_event_id,
        "argument_role": role,
        "entity_kind": str(candidate.get("entity_kind") or candidate.get("entity_layer") or "unknown"),
        "argument_text": text,
        "confidence": numeric(str(candidate.get("confidence") or ""), 0.0),
        "source_kind": str(candidate.get("source_kind") or ""),
        "source_rule": str(candidate.get("source_rule") or ""),
        "evidence_text": str(candidate.get("evidence_text") or ""),
        "is_confirmed": 0,
        "l5_2_seed_checksum": seed_checksum,
        "stable_hash": stable,
        "created_at": CREATED_AT,
    }


def build_records(rows: list[dict[str, str]], seed: dict[str, Any], sample_chapters: set[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    events: list[dict[str, Any]] = []
    arguments: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    evidences: list[dict[str, Any]] = []
    source_maps: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    skips: Counter[str] = Counter()
    seed_checksum = seed["checksum"]

    for row in rows:
        if sample_chapters and str(row.get("chapter_num", "")) not in sample_chapters:
            continue
        recommendation = row.get("enhanced_review_recommendation") or ""
        source_event_id = row.get("event_candidate_id", "")
        if recommendation != READY_RECOMMENDATION:
            skips[recommendation if recommendation in SKIPPED_RECOMMENDATIONS else "not_ready"] += 1
            continue
        if row.get("evidence_backcut_status") != "ok" or not row.get("evidence_text_backcut"):
            skips["evidence_not_ok"] += 1
            continue
        normalized_type = normalize_event_type(row, seed)
        if normalized_type is None:
            skips["invalid_event_type"] += 1
            warnings.append(
                {
                    "warning_id": stable_id("l5n_warn", source_event_id, "invalid_event_type"),
                    "normalized_event_candidate_id": "",
                    "source_event_candidate_id": source_event_id,
                    "warning_code": "invalid_event_type",
                    "severity": "warning",
                    "message": f"event_type_candidate cannot be mapped to L5.2 catalog: {row.get('event_type_candidate', '')}",
                    "created_at": CREATED_AT,
                }
            )
            continue
        subject_candidate = best_candidate(parse_json_array(row.get("enhanced_subject_candidates_json")))
        if subject_candidate is None:
            skips["missing_subject"] += 1
            continue

        event_type, subtype, rule_id = normalized_type
        subject_text = str(subject_candidate.get("text") or "").strip()
        stable_hash = sha256_text(
            json.dumps(
                {
                    "source_event_candidate_id": source_event_id,
                    "event_type": event_type,
                    "subtype": subtype,
                    "subject": subject_text,
                    "evidence_hash": row.get("evidence_backcut_hash", ""),
                    "seed": seed_checksum,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        normalized_event_id = stable_id("l5n_evt", stable_hash)
        base_confidence = numeric(row.get("confidence_score"), 0.0)
        subject_confidence = numeric(str(subject_candidate.get("confidence") or row.get("enhanced_subject_confidence") or ""), 0.0)
        normalized_confidence = min(1.0, round(max(base_confidence, subject_confidence) + 0.05, 4))
        event = {
            "normalized_event_candidate_id": normalized_event_id,
            "source_event_candidate_id": source_event_id,
            "source_event_candidate_hash": row.get("event_candidate_hash", ""),
            "source_review_row_id": row.get("review_row_id", ""),
            "source_layer": "L5.1a",
            "chapter_id": row.get("chapter_id", ""),
            "chapter_num": integer(row.get("chapter_num")),
            "chapter_title": row.get("chapter_title", ""),
            "version_id": row.get("version_id", ""),
            "scene_block_id": row.get("scene_block_id", ""),
            "scene_block_table_name": row.get("scene_block_table_name", ""),
            "scene_block_source_status": row.get("scene_block_source_status", ""),
            "scene_block_validity": row.get("scene_block_validity", ""),
            "trigger_text": row.get("trigger_text", ""),
            "trigger_rule_id": row.get("trigger_rule_id", ""),
            "trigger_category": row.get("trigger_category", ""),
            "event_type_candidate": row.get("event_type_candidate", ""),
            "event_subtype_candidate": row.get("event_subtype_candidate", ""),
            "l5_2_event_type": event_type,
            "l5_2_event_subtype": subtype,
            "normalization_status": "normalized_candidate",
            "normalization_rule_id": rule_id,
            "subject_text": subject_text,
            "subject_entity_kind": str(subject_candidate.get("entity_kind") or "unknown"),
            "subject_is_confirmed": 0,
            "confidence_score": base_confidence,
            "normalized_confidence_score": normalized_confidence,
            "importance_level": importance_level(event_type, row, seed),
            "evidence_backcut_status": row.get("evidence_backcut_status", ""),
            "evidence_backcut_hash": row.get("evidence_backcut_hash", ""),
            "evidence_text_backcut": row.get("evidence_text_backcut", ""),
            "l5_2_seed_checksum": seed_checksum,
            "stable_hash": stable_hash,
            "created_at": CREATED_AT,
        }
        events.append(event)

        subject_role = argument_role_for(subject_candidate, "subject", seed)
        arguments.append(candidate_to_argument(normalized_event_id, source_event_id, subject_candidate, subject_role, seed_checksum))
        for column, fallback in (
            ("enhanced_object_candidates_json", "object"),
            ("enhanced_location_candidates_json", "current_location"),
            ("enhanced_time_hint_candidates_json", "time_hint"),
            ("enhanced_argument_candidates_json", "object"),
        ):
            for candidate in parse_json_array(row.get(column)):
                text = str(candidate.get("text") or "").strip()
                if not text:
                    continue
                role = argument_role_for(candidate, fallback, seed)
                arguments.append(candidate_to_argument(normalized_event_id, source_event_id, candidate, role, seed_checksum))

        evidence_hash = row.get("evidence_backcut_hash") or sha256_text(row.get("evidence_text_backcut", ""))
        evidence_stable = sha256_text(json.dumps([normalized_event_id, evidence_hash, seed_checksum], ensure_ascii=False, separators=(",", ":")))
        evidences.append(
            {
                "normalized_evidence_id": stable_id("l5n_evd", normalized_event_id, evidence_hash),
                "normalized_event_candidate_id": normalized_event_id,
                "source_event_candidate_id": source_event_id,
                "evidence_source_kind": row.get("evidence_source_kind", ""),
                "l2_sentence_id": row.get("evidence_l2_sentence_id", ""),
                "l2_paragraph_id": row.get("evidence_l2_paragraph_id", ""),
                "start_offset": integer(row.get("evidence_start_offset")),
                "end_offset": integer(row.get("evidence_end_offset")),
                "evidence_text": row.get("evidence_text_backcut", ""),
                "evidence_hash": evidence_hash,
                "evidence_backcut_status": row.get("evidence_backcut_status", ""),
                "l5_2_seed_checksum": seed_checksum,
                "stable_hash": evidence_stable,
                "created_at": CREATED_AT,
            }
        )
        for source_kind, source_id, source_hash, source_field, source_value in (
            ("enhanced_review_row", row.get("review_row_id", ""), row.get("event_candidate_hash", ""), "enhanced_review_recommendation", recommendation),
            ("event_candidate", source_event_id, row.get("event_candidate_hash", ""), "event_type_candidate", row.get("event_type_candidate", "")),
            ("evidence_span", row.get("evidence_l2_sentence_id", ""), evidence_hash, "evidence_backcut_status", row.get("evidence_backcut_status", "")),
            ("normalization_seed", SEED_PATH.as_posix(), seed_checksum, "l5_2_seed_checksum", seed_checksum),
        ):
            source_maps.append(
                {
                    "source_map_id": stable_id("l5n_map", normalized_event_id, source_kind, source_id, source_field),
                    "normalized_event_candidate_id": normalized_event_id,
                    "source_layer": "L5.1a" if source_kind != "normalization_seed" else "L5.2",
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "source_hash": source_hash,
                    "source_field": source_field,
                    "source_value": source_value,
                    "created_at": CREATED_AT,
                }
            )
    return events, arguments, states, evidences, source_maps, warnings, skips


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"
    conn.executemany(sql, [[row.get(column) for column in columns] for row in rows])


def table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def current_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: table_count(conn, table) for table in NORMALIZED_EVENT_TABLES if object_exists(conn, table, "table")}


def stable_db_output_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for table in NORMALIZED_EVENT_TABLES:
        if not object_exists(conn, table, "table"):
            continue
        if table == "l5_normalized_event_index_run":
            continue
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
        order_sql = ", ".join(columns)
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_sql}")]
        hashes[table] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashes


def scene_block_audit(conn: sqlite3.Connection, source_payload: dict[str, Any]) -> dict[str, Any]:
    linked = table_count(conn, "l5_normalized_event_candidate") - int(
        conn.execute(
            "SELECT COUNT(*) FROM l5_normalized_event_candidate WHERE scene_block_id IS NULL OR scene_block_id = ''"
        ).fetchone()[0]
    )
    without = int(
        conn.execute(
            "SELECT COUNT(*) FROM l5_normalized_event_candidate WHERE scene_block_id IS NULL OR scene_block_id = ''"
        ).fetchone()[0]
    )
    return {
        **source_payload,
        "scene_block_linked_event_count": linked,
        "event_without_scene_block_count": without,
    }


def run_l5_normalized_event_candidate_indexer(
    project_dir: Path | str | None = None,
    *,
    sample_chapters: str | None = None,
    input_csv: Path | str = DEFAULT_INPUT_CSV,
    output_dir: Path | str = "outputs",
    rebuild: bool = False,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_csv)
    if not input_path.is_absolute():
        input_path = root / input_path
    if not input_path.exists():
        raise RuntimeError(f"Missing L5.1a enhanced review CSV: {input_path}")

    db_path = root / DB_RELATIVE_PATH
    if not db_path.exists():
        raise RuntimeError(f"Missing SQLite database: {db_path}")

    seed = load_seed(root)
    chapters = parse_sample_chapters(sample_chapters)
    input_hashes_before = {input_path.name: file_hash(input_path)}
    json_input = input_path.with_suffix(".json")
    if json_input.exists():
        input_hashes_before[json_input.name] = file_hash(json_input)
    input_rows = read_csv_rows(input_path)

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        source_before = non_l5_3_fingerprints(conn)
        scene_source = scene_block_source_payload(discover_scene_block_source(conn), conn)
        initialize_schema(conn)
        if rebuild:
            clear_l5_3_tables(conn)
        events, arguments, states, evidences, source_maps, warnings, skips = build_records(input_rows, seed, chapters)
        insert_rows(conn, "l5_normalized_event_candidate", events)
        insert_rows(conn, "l5_normalized_event_argument", arguments)
        insert_rows(conn, "l5_normalized_state_change_candidate", states)
        insert_rows(conn, "l5_normalized_event_evidence", evidences)
        insert_rows(conn, "l5_normalized_event_source_map", source_maps)
        insert_rows(conn, "l5_normalized_event_warning", warnings)
        source_after = non_l5_3_fingerprints(conn)
        output_hashes = stable_db_output_hashes(conn)
        run_id = stable_id("l5n_run", ",".join(sorted(chapters)), seed["checksum"], input_hashes_before[input_path.name])
        input_hashes_after = {input_path.name: file_hash(input_path)}
        if json_input.exists():
            input_hashes_after[json_input.name] = file_hash(json_input)
        manifest_core = {
            "row_counts": {
                "input_enhanced_review_rows": len(input_rows),
                "normalized_event_candidate_count": len(events),
                "normalized_event_argument_count": len(arguments),
                "normalized_state_change_candidate_count": len(states),
                "normalized_event_evidence_count": len(evidences),
                "normalized_event_source_map_count": len(source_maps),
                "normalized_event_warning_count": len(warnings),
            },
            "skip_counts": dict(sorted(skips.items())),
            "source_mutation_detected": source_before != source_after,
            "input_mutation_detected": input_hashes_before != input_hashes_after,
            "stable_output_hashes": output_hashes,
        }
        conn.execute(
            """
            INSERT INTO l5_normalized_event_index_run VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                "L5.3 Normalized Event Candidate Index",
                ",".join(sorted(chapters, key=lambda item: int(item) if item.isdigit() else item)),
                str(input_path),
                len(input_rows),
                len(events),
                sum(skips.values()),
                seed["checksum"],
                json.dumps(source_before, ensure_ascii=False, sort_keys=True),
                json.dumps(source_after, ensure_ascii=False, sort_keys=True),
                1 if source_before != source_after else 0,
                json.dumps(input_hashes_before, ensure_ascii=False, sort_keys=True),
                json.dumps(input_hashes_after, ensure_ascii=False, sort_keys=True),
                1 if input_hashes_before != input_hashes_after else 0,
                json.dumps(output_hashes, ensure_ascii=False, sort_keys=True),
                CREATED_AT,
                CREATED_AT,
                "passed",
                "candidate normalization only; no confirmed facts created",
            ),
        )
        conn.commit()
        scene_audit = scene_block_audit(conn, scene_source)
        counts = current_row_counts(conn)
    finally:
        conn.close()

    from scripts.l5_normalized_event_candidate_reporter import run_l5_normalized_event_candidate_reporter

    report_manifest = run_l5_normalized_event_candidate_reporter(root, output_dir=out_dir)
    manifest = {
        "export_layer": "L5.3 Normalized Event Candidate Index",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "sample_chapters": sorted(chapters, key=lambda item: int(item) if item.isdigit() else item),
        "input_file": str(input_path),
        "l5_2_seed_path": str(root / SEED_PATH),
        "l5_2_seed_checksum": seed["checksum"],
        "detected_scene_block_source": scene_audit,
        "table_counts": counts,
        **manifest_core,
        "source_fingerprints_before": source_before,
        "source_fingerprints_after": source_after,
        "input_hashes_before": input_hashes_before,
        "input_hashes_after": input_hashes_after,
        "reporter_manifest": report_manifest,
    }
    write_json(out_dir / MANIFEST_JSON, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L5.3 normalized event candidate index sample.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--sample-chapters", type=str, default=DEFAULT_SAMPLE_CHAPTERS)
    parser.add_argument("--input-csv", type=Path, default=DEFAULT_INPUT_CSV)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    manifest = run_l5_normalized_event_candidate_indexer(
        args.project_dir,
        sample_chapters=args.sample_chapters,
        input_csv=args.input_csv,
        output_dir=args.output_dir,
        rebuild=args.rebuild,
    )
    print(f"L5.3 normalized event candidate rows: {manifest['row_counts']['normalized_event_candidate_count']}")
    print(f"L5.3 skipped input rows: {sum(manifest['skip_counts'].values())}")


if __name__ == "__main__":
    main()
