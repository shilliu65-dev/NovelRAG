from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_extractor import discover_scene_block_source, scene_block_source_payload


EXPORT_CREATED_AT = "1970-01-01T00:00:00"
EVENT_REVIEW_NAME = "l5_event_candidate_review"
STATE_REVIEW_NAME = "l5_state_change_candidate_review"

EVENT_CSV = "l5_event_candidate_review.csv"
EVENT_JSON = "l5_event_candidate_review.json"
EVENT_MD = "l5_event_candidate_review_report.md"
STATE_CSV = "l5_state_change_candidate_review.csv"
STATE_JSON = "l5_state_change_candidate_review.json"
STATE_MD = "l5_state_change_candidate_review_report.md"
MANIFEST_JSON = "l5_event_candidate_review_manifest.json"

REQUIRED_L5_TABLES = (
    "l5_event_candidate",
    "l5_event_argument_candidate",
    "l5_event_state_change_candidate",
    "l5_event_evidence_span",
    "l5_event_extraction_run",
)

SOURCE_FINGERPRINT_TABLES = REQUIRED_L5_TABLES + ("l3_scene_blocks", "l4_scene_blocks", "scene_blocks")

EVENT_COLUMNS = [
    "review_row_id",
    "review_status",
    "review_decision",
    "review_note",
    "event_candidate_id",
    "event_candidate_hash",
    "event_source_status",
    "chapter_id",
    "chapter_num",
    "chapter_title",
    "version_id",
    "scene_block_source_status",
    "scene_block_table_name",
    "scene_block_id",
    "scene_block_validity",
    "trigger_text",
    "trigger_rule_id",
    "trigger_category",
    "event_type_candidate",
    "event_subtype_candidate",
    "subject_candidates_json",
    "object_candidates_json",
    "location_candidates_json",
    "time_hint_candidates_json",
    "organization_candidates_json",
    "power_candidates_json",
    "other_argument_candidates_json",
    "argument_count",
    "state_change_candidate_count",
    "evidence_span_count",
    "evidence_source_kind",
    "evidence_l2_sentence_id",
    "evidence_l2_paragraph_id",
    "evidence_start_offset",
    "evidence_end_offset",
    "evidence_text_backcut",
    "evidence_backcut_hash",
    "evidence_backcut_status",
    "confidence_score",
    "confidence_rule",
    "importance_candidate",
    "warning_flags_json",
    "source_run_id",
    "source_created_at",
    "export_created_at",
]

STATE_COLUMNS = [
    "review_row_id",
    "review_status",
    "review_decision",
    "review_note",
    "state_change_candidate_id",
    "state_change_candidate_hash",
    "linked_event_candidate_id",
    "chapter_id",
    "chapter_num",
    "chapter_title",
    "version_id",
    "scene_block_source_status",
    "scene_block_table_name",
    "scene_block_id",
    "scene_block_validity",
    "state_change_type_candidate",
    "state_before_candidate",
    "state_after_candidate",
    "changed_entity_candidates_json",
    "trigger_text",
    "trigger_rule_id",
    "evidence_source_kind",
    "evidence_l2_sentence_id",
    "evidence_l2_paragraph_id",
    "evidence_start_offset",
    "evidence_end_offset",
    "evidence_text_backcut",
    "evidence_backcut_hash",
    "evidence_backcut_status",
    "confidence_score",
    "confidence_rule",
    "importance_candidate",
    "warning_flags_json",
    "source_run_id",
    "source_created_at",
    "export_created_at",
]

BACKCUT_STATUSES = {"ok", "missing_coordinate", "missing_l2_source", "offset_invalid", "hash_mismatch", "empty_text", "not_checked"}
SCENE_SOURCE_STATUSES = {"compatible_preferred", "compatible_fallback", "missing_optional", "invalid_schema"}
WARNING_FLAGS = {
    "missing_evidence_span",
    "multiple_evidence_spans",
    "missing_trigger_text",
    "missing_argument",
    "missing_subject_candidate",
    "missing_scene_block",
    "invalid_scene_block_ref",
    "evidence_backcut_not_ok",
    "weak_candidate",
    "duplicate_trigger_same_sentence",
    "duplicate_event_same_evidence",
    "state_change_without_event",
    "event_without_state_change",
    "optional_location_source_missing",
    "optional_scene_block_source_missing",
}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def json_cell(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def object_exists(conn: sqlite3.Connection, name: str, object_type: str | None = None) -> bool:
    if object_type is None:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1", (object_type, name)).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    return [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")]


def require_l5_tables(conn: sqlite3.Connection) -> None:
    missing = [name for name in REQUIRED_L5_TABLES if not object_exists(conn, name, "table")]
    if missing:
        raise RuntimeError(f"Missing required L5.0 tables: {', '.join(missing)}")


def row_value(row: sqlite3.Row | dict[str, Any] | None, *names: str, default: Any = "") -> Any:
    if row is None:
        return default
    keys = set(row.keys()) if isinstance(row, sqlite3.Row) else set(row)
    for name in names:
        if name in keys and row[name] is not None:
            return row[name]
    return default


def parse_chapters(value: str | None) -> set[int] | None:
    if not value:
        return None
    return {int(part.strip()) for part in value.split(",") if part.strip()}


def filtered_events(conn: sqlite3.Connection, sample_chapters: str | None, chapter_num: int | None) -> list[sqlite3.Row]:
    where: list[str] = []
    params: list[Any] = []
    chapters = parse_chapters(sample_chapters)
    if chapters:
        where.append(f"chapter_num IN ({','.join('?' for _ in chapters)})")
        params.extend(sorted(chapters))
    if chapter_num is not None:
        where.append("chapter_num = ?")
        params.append(chapter_num)
    sql = "SELECT * FROM l5_event_candidate"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY chapter_num, COALESCE(scene_block_id, char(0xffff)), COALESCE(para_id, char(0xffff)), COALESCE(sentence_id, char(0xffff)), event_candidate_id"
    return list(conn.execute(sql, tuple(params)))


def load_grouped_rows(conn: sqlite3.Connection, table_name: str, key_column: str) -> dict[str, list[sqlite3.Row]]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in conn.execute(f"SELECT * FROM {table_name} ORDER BY {key_column}"):
        grouped[str(row[key_column])].append(row)
    return grouped


def chapter_titles(conn: sqlite3.Connection) -> dict[tuple[str, str], str]:
    if not object_exists(conn, "v_current_chapters", "view"):
        return {}
    return {
        (str(row["chapter_id"]), str(row["latest_version_id"])): str(row["chapter_title_current"])
        for row in conn.execute("SELECT chapter_id, latest_version_id, chapter_title_current FROM v_current_chapters")
    }


def source_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    fingerprints: dict[str, dict[str, Any]] = {}
    for table in SOURCE_FINGERPRINT_TABLES:
        if not object_exists(conn, table, "table"):
            continue
        columns = table_columns(conn, table)
        if not columns:
            continue
        order_col = columns[0]
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_col}")]
        fingerprints[table] = {
            "row_count": len(rows),
            "key_column": order_col,
            "min_key": "" if not rows else str(rows[0].get(order_col, "")),
            "max_key": "" if not rows else str(rows[-1].get(order_col, "")),
            "aggregate_hash": sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        }
    return fingerprints


def source_status(conn: sqlite3.Connection) -> tuple[dict[str, Any], dict[str, Any]]:
    source_tables = {name: {"exists": object_exists(conn, name, "table"), "columns": table_columns(conn, name) if object_exists(conn, name, "table") else []} for name in REQUIRED_L5_TABLES}
    optional_sources = {
        "l3_location_appearance": {"exists": object_exists(conn, "l3_location_appearance", "table")},
        "l3_location_def": {"exists": object_exists(conn, "l3_location_def", "table")},
        "l3_location_alias": {"exists": object_exists(conn, "l3_location_alias", "table")},
        "l3_location_candidate": {"exists": object_exists(conn, "l3_location_candidate", "table")},
        "l3_scene_blocks": {"exists": object_exists(conn, "l3_scene_blocks", "table")},
        "l4_scene_blocks": {"exists": object_exists(conn, "l4_scene_blocks", "table")},
        "scene_blocks": {"exists": object_exists(conn, "scene_blocks", "table")},
    }
    return source_tables, optional_sources


def valid_scene_refs(conn: sqlite3.Connection, scene_source: dict[str, Any]) -> set[str]:
    table = scene_source.get("scene_block_table_name")
    id_col = scene_source.get("scene_block_id_column")
    if not table or not id_col or not object_exists(conn, str(table), "table"):
        return set()
    return {str(row["scene_block_id"]) for row in conn.execute(f"SELECT {id_col} AS scene_block_id FROM {table}")}


def scene_validity(scene_id: str, scene_source: dict[str, Any], valid_refs: set[str]) -> str:
    if not scene_source.get("detected_scene_block_source"):
        return "missing_optional"
    if not scene_id:
        return "missing"
    return "valid" if scene_id in valid_refs else "invalid"


def backcut_evidence(conn: sqlite3.Connection, evidence: sqlite3.Row | None, event: sqlite3.Row | None = None) -> dict[str, Any]:
    source = evidence if evidence is not None else event
    if source is None:
        return {"text": "", "hash": "", "status": "missing_coordinate", "start": "", "end": "", "sentence_id": "", "para_id": "", "kind": ""}
    chapter_id = row_value(source, "chapter_id", default=row_value(event, "chapter_id", default=""))
    version_id = row_value(source, "version_id", default=row_value(event, "version_id", default=""))
    sentence_id = row_value(source, "sentence_id", "l2_sentence_id", default=row_value(event, "sentence_id", default=""))
    para_id = row_value(source, "para_id", "paragraph_id", "l2_paragraph_id", default=row_value(event, "para_id", default=""))
    start = row_value(source, "span_start_offset", "start_offset", "sentence_start_offset", default="")
    end = row_value(source, "span_end_offset", "end_offset", "sentence_end_offset", default="")
    expected_hash = row_value(source, "span_hash", "text_hash", "evidence_hash", default=row_value(event, "evidence_hash", default=""))
    if start == "" or end == "" or not chapter_id or not version_id:
        return {"text": "", "hash": "", "status": "missing_coordinate", "start": start, "end": end, "sentence_id": sentence_id, "para_id": para_id, "kind": ""}
    if sentence_id and object_exists(conn, "v_l2_current_sentences", "view"):
        l2 = conn.execute("SELECT 1 FROM v_l2_current_sentences WHERE sentence_id = ? AND version_id = ?", (sentence_id, version_id)).fetchone()
        if l2 is None:
            return {"text": "", "hash": "", "status": "missing_l2_source", "start": start, "end": end, "sentence_id": sentence_id, "para_id": para_id, "kind": "l2_sentence"}
    elif para_id and object_exists(conn, "v_l2_current_paragraphs", "view"):
        l2 = conn.execute("SELECT 1 FROM v_l2_current_paragraphs WHERE para_id = ? AND version_id = ?", (para_id, version_id)).fetchone()
        if l2 is None:
            return {"text": "", "hash": "", "status": "missing_l2_source", "start": start, "end": end, "sentence_id": sentence_id, "para_id": para_id, "kind": "l2_paragraph"}
    chapter = None
    if object_exists(conn, "v_current_chapters", "view"):
        chapter = conn.execute("SELECT content_full_text FROM v_current_chapters WHERE chapter_id = ? AND latest_version_id = ?", (chapter_id, version_id)).fetchone()
    if chapter is None:
        return {"text": "", "hash": "", "status": "missing_l2_source", "start": start, "end": end, "sentence_id": sentence_id, "para_id": para_id, "kind": "l2_sentence" if sentence_id else "l2_paragraph"}
    try:
        start_int = int(start)
        end_int = int(end)
    except (TypeError, ValueError):
        return {"text": "", "hash": "", "status": "offset_invalid", "start": start, "end": end, "sentence_id": sentence_id, "para_id": para_id, "kind": "l2_sentence" if sentence_id else "l2_paragraph"}
    content = str(chapter["content_full_text"])
    if start_int < 0 or end_int <= start_int or end_int > len(content):
        return {"text": "", "hash": "", "status": "offset_invalid", "start": start_int, "end": end_int, "sentence_id": sentence_id, "para_id": para_id, "kind": "l2_sentence" if sentence_id else "l2_paragraph"}
    text = content[start_int:end_int]
    text_hash = sha256_text(text)
    if text == "":
        status = "empty_text"
    elif expected_hash and expected_hash != text_hash:
        status = "hash_mismatch"
    else:
        status = "ok"
    return {"text": text, "hash": text_hash, "status": status, "start": start_int, "end": end_int, "sentence_id": sentence_id, "para_id": para_id, "kind": "l2_sentence" if sentence_id else "l2_paragraph"}


def argument_bucket(arguments: list[sqlite3.Row]) -> dict[str, list[dict[str, Any]]]:
    buckets = {"subject": [], "object": [], "location": [], "time_hint": [], "organization": [], "power": [], "other": []}
    for arg in arguments:
        item = {
            "argument_id": row_value(arg, "argument_id"),
            "role": row_value(arg, "argument_role"),
            "entity_layer": row_value(arg, "entity_layer"),
            "entity_id": row_value(arg, "entity_id"),
            "entity_text": row_value(arg, "entity_text"),
            "distance_scope": row_value(arg, "distance_scope"),
            "status": row_value(arg, "status"),
        }
        role = str(item["role"])
        if role in {"subject"}:
            buckets["subject"].append(item)
        elif role in {"object", "target"}:
            buckets["object"].append(item)
        elif "location" in role:
            buckets["location"].append(item)
        elif role == "organization":
            buckets["organization"].append(item)
        elif role == "power":
            buckets["power"].append(item)
        else:
            buckets["other"].append(item)
    return buckets


def confidence_score(confidence: str) -> str:
    return {"high": "0.8", "medium": "0.5", "low": "0.2"}.get(confidence, "")


def append_warning(row: dict[str, Any], flag: str) -> None:
    flags = json.loads(row["warning_flags_json"])
    if flag not in flags:
        flags.append(flag)
    row["warning_flags_json"] = json_cell(sorted(flags))


def build_event_rows(
    conn: sqlite3.Connection,
    events: list[sqlite3.Row],
    args_by_event: dict[str, list[sqlite3.Row]],
    states_by_event: dict[str, list[sqlite3.Row]],
    evidence_by_event: dict[str, list[sqlite3.Row]],
    titles: dict[tuple[str, str], str],
    scene_source: dict[str, Any],
    valid_scenes: set[str],
    optional_sources: dict[str, Any],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        event_id = str(row_value(event, "event_candidate_id"))
        evidence_rows = evidence_by_event.get(event_id, [])
        evidence = evidence_rows[0] if evidence_rows else None
        backcut = backcut_evidence(conn, evidence, event)
        arguments = args_by_event.get(event_id, [])
        buckets = argument_bucket(arguments)
        warnings: list[str] = []
        if not evidence_rows:
            warnings.append("missing_evidence_span")
        if len(evidence_rows) > 1:
            warnings.append("multiple_evidence_spans")
        if not row_value(event, "trigger_text"):
            warnings.append("missing_trigger_text")
        if not arguments:
            warnings.append("missing_argument")
        if not buckets["subject"]:
            warnings.append("missing_subject_candidate")
        scene_id = str(row_value(event, "scene_block_id"))
        scene_ref_validity = scene_validity(scene_id, scene_source, valid_scenes)
        if scene_ref_validity in {"missing", "missing_optional"}:
            warnings.append("missing_scene_block")
        if scene_ref_validity == "invalid":
            warnings.append("invalid_scene_block_ref")
        if backcut["status"] != "ok":
            warnings.append("evidence_backcut_not_ok")
        if row_value(event, "confidence_level") == "low":
            warnings.append("weak_candidate")
        if not states_by_event.get(event_id):
            warnings.append("event_without_state_change")
        if not optional_sources.get("l3_location_appearance", {}).get("exists"):
            warnings.append("optional_location_source_missing")
        if scene_source.get("scene_block_source_status") == "missing_optional":
            warnings.append("optional_scene_block_source_missing")
        row = {
            "review_row_id": stable_id("rev_evt", event_id),
            "review_status": "candidate",
            "review_decision": "",
            "review_note": "",
            "event_candidate_id": event_id,
            "event_candidate_hash": sha256_text(json.dumps(dict(event), ensure_ascii=False, sort_keys=True, default=str)),
            "event_source_status": row_value(event, "status"),
            "chapter_id": row_value(event, "chapter_id"),
            "chapter_num": row_value(event, "chapter_num"),
            "chapter_title": titles.get((str(row_value(event, "chapter_id")), str(row_value(event, "version_id"))), ""),
            "version_id": row_value(event, "version_id"),
            "scene_block_source_status": scene_source.get("scene_block_source_status", ""),
            "scene_block_table_name": scene_source.get("scene_block_table_name") or "",
            "scene_block_id": scene_id,
            "scene_block_validity": scene_ref_validity,
            "trigger_text": row_value(event, "trigger_text"),
            "trigger_rule_id": row_value(event, "trigger_rule_id"),
            "trigger_category": row_value(event, "event_family"),
            "event_type_candidate": row_value(event, "event_type"),
            "event_subtype_candidate": "",
            "subject_candidates_json": json_cell(buckets["subject"]),
            "object_candidates_json": json_cell(buckets["object"]),
            "location_candidates_json": json_cell(buckets["location"]),
            "time_hint_candidates_json": json_cell(buckets["time_hint"]),
            "organization_candidates_json": json_cell(buckets["organization"]),
            "power_candidates_json": json_cell(buckets["power"]),
            "other_argument_candidates_json": json_cell(buckets["other"]),
            "argument_count": len(arguments),
            "state_change_candidate_count": len(states_by_event.get(event_id, [])),
            "evidence_span_count": len(evidence_rows),
            "evidence_source_kind": backcut["kind"],
            "evidence_l2_sentence_id": backcut["sentence_id"],
            "evidence_l2_paragraph_id": backcut["para_id"],
            "evidence_start_offset": backcut["start"],
            "evidence_end_offset": backcut["end"],
            "evidence_text_backcut": backcut["text"],
            "evidence_backcut_hash": backcut["hash"],
            "evidence_backcut_status": backcut["status"],
            "confidence_score": confidence_score(str(row_value(event, "confidence_level"))),
            "confidence_rule": row_value(event, "confidence_level"),
            "importance_candidate": "",
            "warning_flags_json": json_cell(sorted(warnings)),
            "source_run_id": row_value(event, "extraction_run_id"),
            "source_created_at": row_value(event, "created_at"),
            "export_created_at": EXPORT_CREATED_AT,
        }
        rows.append(row)
    duplicate_trigger_counts = Counter((row["chapter_num"], row["evidence_l2_sentence_id"], row["trigger_text"], row["trigger_rule_id"]) for row in rows)
    duplicate_evidence_counts = Counter((row["chapter_num"], row["evidence_backcut_hash"]) for row in rows if row["evidence_backcut_hash"])
    for row in rows:
        if duplicate_trigger_counts[(row["chapter_num"], row["evidence_l2_sentence_id"], row["trigger_text"], row["trigger_rule_id"])] > 1:
            append_warning(row, "duplicate_trigger_same_sentence")
        if row["evidence_backcut_hash"] and duplicate_evidence_counts[(row["chapter_num"], row["evidence_backcut_hash"])] > 1:
            append_warning(row, "duplicate_event_same_evidence")
    return sorted(rows, key=lambda row: (int(row["chapter_num"] or 0), row["scene_block_id"] or "\uffff", row["evidence_l2_paragraph_id"] or "\uffff", row["evidence_l2_sentence_id"] or "\uffff", row["event_candidate_id"]))


def build_state_rows(
    conn: sqlite3.Connection,
    states: list[sqlite3.Row],
    event_by_id: dict[str, sqlite3.Row],
    evidence_by_event: dict[str, list[sqlite3.Row]],
    titles: dict[tuple[str, str], str],
    scene_source: dict[str, Any],
    valid_scenes: set[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for state in states:
        event_id = str(row_value(state, "event_candidate_id"))
        event = event_by_id.get(event_id)
        evidence_rows = evidence_by_event.get(event_id, [])
        evidence = evidence_rows[0] if evidence_rows else None
        backcut = backcut_evidence(conn, evidence, event)
        scene_id = str(row_value(event, "scene_block_id")) if event is not None else ""
        scene_ref_validity = scene_validity(scene_id, scene_source, valid_scenes)
        warnings: list[str] = []
        if event is None:
            warnings.append("state_change_without_event")
        if scene_ref_validity in {"missing", "missing_optional"}:
            warnings.append("missing_scene_block")
        if scene_ref_validity == "invalid":
            warnings.append("invalid_scene_block_ref")
        if backcut["status"] != "ok":
            warnings.append("evidence_backcut_not_ok")
        changed_entity = {
            "entity_layer": row_value(state, "entity_layer"),
            "entity_id": row_value(state, "entity_id"),
            "entity_text": row_value(state, "entity_text"),
        }
        row = {
            "review_row_id": stable_id("rev_stc", row_value(state, "state_change_candidate_id")),
            "review_status": "candidate",
            "review_decision": "",
            "review_note": "",
            "state_change_candidate_id": row_value(state, "state_change_candidate_id"),
            "state_change_candidate_hash": sha256_text(json.dumps(dict(state), ensure_ascii=False, sort_keys=True, default=str)),
            "linked_event_candidate_id": event_id,
            "chapter_id": row_value(event, "chapter_id"),
            "chapter_num": row_value(event, "chapter_num"),
            "chapter_title": titles.get((str(row_value(event, "chapter_id")), str(row_value(event, "version_id"))), ""),
            "version_id": row_value(event, "version_id"),
            "scene_block_source_status": scene_source.get("scene_block_source_status", ""),
            "scene_block_table_name": scene_source.get("scene_block_table_name") or "",
            "scene_block_id": scene_id,
            "scene_block_validity": scene_ref_validity,
            "state_change_type_candidate": row_value(state, "state_type"),
            "state_before_candidate": row_value(state, "from_state"),
            "state_after_candidate": row_value(state, "to_state"),
            "changed_entity_candidates_json": json_cell([changed_entity]),
            "trigger_text": row_value(event, "trigger_text"),
            "trigger_rule_id": row_value(event, "trigger_rule_id"),
            "evidence_source_kind": backcut["kind"],
            "evidence_l2_sentence_id": backcut["sentence_id"],
            "evidence_l2_paragraph_id": backcut["para_id"],
            "evidence_start_offset": backcut["start"],
            "evidence_end_offset": backcut["end"],
            "evidence_text_backcut": backcut["text"],
            "evidence_backcut_hash": backcut["hash"],
            "evidence_backcut_status": backcut["status"],
            "confidence_score": confidence_score(str(row_value(event, "confidence_level"))),
            "confidence_rule": row_value(event, "confidence_level"),
            "importance_candidate": "",
            "warning_flags_json": json_cell(sorted(warnings)),
            "source_run_id": row_value(event, "extraction_run_id"),
            "source_created_at": row_value(state, "created_at"),
            "export_created_at": EXPORT_CREATED_AT,
        }
        rows.append(row)
    return sorted(rows, key=lambda row: (int(row["chapter_num"] or 0), row["scene_block_id"] or "\uffff", row["linked_event_candidate_id"] or "\uffff", row["state_change_candidate_id"]))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_json(path: Path, export_name: str, project_dir: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    payload = {
        "export_name": export_name,
        "project_dir": str(project_dir),
        "created_at": EXPORT_CREATED_AT,
        "row_count": len(rows),
        "columns": columns,
        "rows": [{column: row.get(column, "") for column in columns} for row in rows],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def warning_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for flag in json.loads(row["warning_flags_json"]):
            counts[str(flag)] += 1
    return dict(sorted(counts.items()))


def status_counts(rows: list[dict[str, Any]], column: str) -> dict[str, int]:
    return dict(sorted(Counter(str(row.get(column, "")) for row in rows).items()))


def write_markdown_report(path: Path, title: str, columns: list[str], rows: list[dict[str, Any]], manifest: dict[str, Any], *, state_report: bool = False) -> None:
    lines = [
        f"# {title}",
        "",
        "## Summary",
        "",
        f"- created_at: {EXPORT_CREATED_AT}",
        f"- row_count: {len(rows)}",
        "",
        "## Source Tables",
        "",
    ]
    lines.extend(f"- {name}: exists={info.get('exists')}" for name, info in manifest["source_tables"].items())
    lines.extend(["", "## Optional Source Status", ""])
    lines.extend(f"- {name}: exists={info.get('exists')}" for name, info in manifest["optional_sources"].items())
    lines.extend(["", "## Scene Block Source Detection", ""])
    lines.extend(f"- {key}: {value}" for key, value in manifest["detected_scene_block_source"].items())
    lines.extend(["", "## Row Counts", ""])
    lines.extend(f"- {key}: {value}" for key, value in manifest["row_counts"].items())
    lines.extend(["", "## Warning Counts", ""])
    counts = warning_counts(rows)
    lines.extend(f"- {key}: {value}" for key, value in counts.items()) if counts else lines.append("- none")
    lines.extend(["", "## Evidence Back-cut Status", ""])
    lines.extend(f"- {key}: {value}" for key, value in status_counts(rows, "evidence_backcut_status").items())
    if state_report:
        lines.extend(["", "## State Change Type Candidates", ""])
        lines.extend(f"- {key}: {value}" for key, value in status_counts(rows, "state_change_type_candidate").items()) if rows else lines.append("- none")
    else:
        lines.extend(["", "## Top Trigger Categories", ""])
        lines.extend(f"- {key}: {value}" for key, value in status_counts(rows, "trigger_category").items()) if rows else lines.append("- none")
    lines.extend(["", "## Review Columns", ""])
    lines.extend(f"- {column}" for column in columns)
    lines.extend(["", "## Output Files", ""])
    lines.extend(f"- {item}" for item in manifest["output_files"])
    lines.extend(["", "## PASS / WARNING / FAIL", ""])
    lines.append("- PASS" if not manifest["source_mutation_detected"] else "- FAIL source mutation detected")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def build_manifest(
    project_dir: Path,
    db_path: Path,
    source_tables: dict[str, Any],
    optional_sources: dict[str, Any],
    scene_source: dict[str, Any],
    output_files: list[str],
    event_rows: list[dict[str, Any]],
    state_rows: list[dict[str, Any]],
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    warnings = warning_counts(event_rows + state_rows)
    return {
        "export_layer": "L5.1 Event Candidate Review Export",
        "project_dir": str(project_dir),
        "database_path": str(db_path),
        "created_at": EXPORT_CREATED_AT,
        "source_tables": source_tables,
        "optional_sources": optional_sources,
        "detected_scene_block_source": scene_source,
        "output_files": output_files,
        "row_counts": {"event_review_rows": len(event_rows), "state_change_review_rows": len(state_rows)},
        "warning_counts": warnings,
        "source_fingerprints_before": before,
        "source_fingerprints_after": after,
        "source_mutation_detected": before != after,
    }


def run_l5_event_candidate_review_exporter(
    project_dir: Path | str | None = None,
    *,
    sample_chapters: str | None = None,
    chapter_num: int | None = None,
    output_dir: Path | str = "outputs",
    strict: bool = False,
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
        require_l5_tables(conn)
        before = source_fingerprints(conn)
        source_tables, optional_sources = source_status(conn)
        scene_source = scene_block_source_payload(discover_scene_block_source(conn))
        valid_scenes = valid_scene_refs(conn, scene_source)
        events = filtered_events(conn, sample_chapters, chapter_num)
        event_ids = {str(row["event_candidate_id"]) for row in events}
        args_by_event = {key: value for key, value in load_grouped_rows(conn, "l5_event_argument_candidate", "event_candidate_id").items() if key in event_ids}
        states_by_event = {key: value for key, value in load_grouped_rows(conn, "l5_event_state_change_candidate", "event_candidate_id").items() if key in event_ids}
        evidence_by_event = {key: value for key, value in load_grouped_rows(conn, "l5_event_evidence_span", "event_candidate_id").items() if key in event_ids}
        titles = chapter_titles(conn)
        event_by_id = {str(row["event_candidate_id"]): row for row in events}
        state_rows_source = [row for rows in states_by_event.values() for row in rows]
        event_rows = build_event_rows(conn, events, args_by_event, states_by_event, evidence_by_event, titles, scene_source, valid_scenes, optional_sources)
        state_rows = build_state_rows(conn, state_rows_source, event_by_id, evidence_by_event, titles, scene_source, valid_scenes)
        after = source_fingerprints(conn)
    finally:
        conn.close()

    output_files = [
        str(out_dir / EVENT_CSV),
        str(out_dir / EVENT_JSON),
        str(out_dir / EVENT_MD),
        str(out_dir / STATE_CSV),
        str(out_dir / STATE_JSON),
        str(out_dir / STATE_MD),
        str(out_dir / MANIFEST_JSON),
    ]
    manifest = build_manifest(root, db_path, source_tables, optional_sources, scene_source, output_files, event_rows, state_rows, before, after)
    if strict and manifest["warning_counts"]:
        raise RuntimeError(f"Strict export failed with warnings: {manifest['warning_counts']}")
    if manifest["source_mutation_detected"]:
        raise RuntimeError("Source mutation detected during L5.1 export")

    write_csv(out_dir / EVENT_CSV, EVENT_COLUMNS, event_rows)
    write_json(out_dir / EVENT_JSON, EVENT_REVIEW_NAME, root, EVENT_COLUMNS, event_rows)
    write_csv(out_dir / STATE_CSV, STATE_COLUMNS, state_rows)
    write_json(out_dir / STATE_JSON, STATE_REVIEW_NAME, root, STATE_COLUMNS, state_rows)
    (out_dir / MANIFEST_JSON).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    write_markdown_report(out_dir / EVENT_MD, "L5.1 Event Candidate Review Export Report", EVENT_COLUMNS, event_rows, manifest, state_report=False)
    write_markdown_report(out_dir / STATE_MD, "L5.1 State Change Candidate Review Export Report", STATE_COLUMNS, state_rows, manifest, state_report=True)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export L5.0 event candidates for human review.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--sample-chapters", type=str, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    manifest = run_l5_event_candidate_review_exporter(
        args.project_dir,
        sample_chapters=args.sample_chapters,
        chapter_num=args.chapter_num,
        output_dir=args.output_dir,
        strict=args.strict,
    )
    print(f"L5.1 event review rows: {manifest['row_counts']['event_review_rows']}")
    print(f"L5.1 state change review rows: {manifest['row_counts']['state_change_review_rows']}")


if __name__ == "__main__":
    main()
