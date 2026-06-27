from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


DETERMINISTIC_TIMESTAMP = "1970-01-01T00:00:00"
DEFAULT_SAMPLE_CHAPTERS = (1, 2, 1697)
RULES_RELATIVE_PATH = Path("config") / "event_trigger_rules.seed.json"
JSON_REPORT_RELATIVE_PATH = Path("outputs") / "l5_event_candidates_sample.json"
MD_REPORT_RELATIVE_PATH = Path("outputs") / "l5_event_candidates_sample_report.md"
SOURCE_LAYER = "l2_sentence"

EVENT_FAMILIES = {
    "character_event",
    "location_event",
    "domain_event",
    "gray_world_event",
    "power_event",
    "organization_event",
    "object_event",
    "relationship_event",
    "unknown_event",
}
EVENT_TYPES = {
    "character_arrival",
    "character_departure",
    "character_meeting",
    "character_conflict",
    "character_attack",
    "character_injury",
    "character_death",
    "character_rescue",
    "character_escape",
    "character_identity_reveal",
    "character_alias_change",
    "character_join_org",
    "character_leave_org",
    "character_betrayal",
    "character_unknown_action",
    "location_arrival",
    "location_departure",
    "location_battle",
    "location_discovery",
    "location_destroyed",
    "location_sealed",
    "location_occupied",
    "location_escape",
    "location_unknown_change",
    "domain_attacked",
    "domain_invaded",
    "domain_polluted",
    "domain_destroyed",
    "domain_defended",
    "domain_recovered",
    "domain_sealed",
    "domain_unknown_change",
    "gray_world_intrusion",
    "gray_tide_spread",
    "disaster_attack",
    "disaster_manifestation",
    "disaster_contamination",
    "disaster_suppression",
    "disaster_defeated",
    "gray_world_unknown_event",
    "power_awakening",
    "power_usage",
    "power_upgrade",
    "power_loss",
    "godway_stage_change",
    "domain_field_awakening",
    "power_unknown_change",
    "organization_meeting",
    "organization_operation",
    "organization_conflict",
    "organization_exposure",
    "organization_foundation",
    "organization_collapse",
    "organization_unknown_event",
    "relationship_alliance",
    "relationship_conflict",
    "relationship_betrayal",
    "relationship_reconciliation",
    "relationship_unknown_change",
    "object_obtained",
    "object_lost",
    "object_destroyed",
    "object_transferred",
    "object_unknown_change",
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
STATE_TYPES = {
    "life_state",
    "identity_state",
    "location_state",
    "domain_state",
    "organization_state",
    "power_state",
    "relationship_state",
    "unknown_state",
}
ARGUMENT_ROLES = {
    "subject",
    "object",
    "target",
    "location",
    "source_location",
    "destination_location",
    "affected_location",
    "character",
    "organization",
    "power",
    "gray_world_term",
    "unknown",
}
ENTITY_LAYERS = {"l3_character", "l3_location", "l3_gray_world", "raw_text", "unknown"}
SCENE_BLOCK_SOURCE_CANDIDATES = ("l4_scene_blocks", "l3_scene_blocks", "scene_blocks")


@dataclass(frozen=True)
class TriggerRule:
    rule_id: str
    event_family: str
    event_type: str
    trigger_terms: tuple[str, ...]
    negative_terms: tuple[str, ...]
    confidence_level: str
    state_change_hint: dict[str, str] | None
    note: str
    priority: int


@dataclass
class ExtractionStats:
    db_path: Path
    json_report_path: Path
    md_report_path: Path
    extraction_run_id: str
    sample_chapters: list[int]
    rule_seed_hash: str
    candidate_count: int = 0
    state_change_candidate_count: int = 0
    missing_optional_sources: list[str] = field(default_factory=list)
    duplicate_skips: list[dict[str, Any]] = field(default_factory=list)
    warning_notes: list[str] = field(default_factory=list)
    detected_scene_block_source: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SceneBlockSource:
    detected: bool
    status: str
    table_name: str | None
    id_column: str | None
    note: str


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def parse_sample_chapters(value: str | None) -> list[int]:
    if not value:
        return list(DEFAULT_SAMPLE_CHAPTERS)
    chapters = sorted({int(part.strip()) for part in value.split(",") if part.strip()})
    return chapters


def object_exists(conn: sqlite3.Connection, name: str, object_type: str | None = None) -> bool:
    if object_type is None:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1", (object_type, name)).fetchone()
    return row is not None


def require_source_views(conn: sqlite3.Connection) -> None:
    missing = [name for name in ("v_current_chapters", "v_l2_current_paragraphs", "v_l2_current_sentences") if not object_exists(conn, name, "view")]
    if missing:
        raise RuntimeError(f"Missing required L1/L2 current views: {', '.join(missing)}")


def load_rules(project_dir: Path) -> tuple[list[TriggerRule], str]:
    path = project_dir / RULES_RELATIVE_PATH
    raw_text = path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    rules: list[TriggerRule] = []
    for index, item in enumerate(payload.get("rules", [])):
        event_family = str(item["event_family"])
        event_type = str(item["event_type"])
        confidence = str(item["confidence_level"])
        if event_family not in EVENT_FAMILIES:
            raise ValueError(f"Invalid event_family in seed: {event_family}")
        if event_type not in EVENT_TYPES:
            raise ValueError(f"Invalid event_type in seed: {event_type}")
        if confidence not in CONFIDENCE_LEVELS:
            raise ValueError(f"Invalid confidence_level in seed: {confidence}")
        hint = item.get("state_change_hint")
        if hint is not None and str(hint.get("state_type")) not in STATE_TYPES:
            raise ValueError(f"Invalid state_type in seed: {hint.get('state_type')}")
        rules.append(
            TriggerRule(
                rule_id=str(item["rule_id"]),
                event_family=event_family,
                event_type=event_type,
                trigger_terms=tuple(str(term) for term in item.get("trigger_terms", []) if str(term)),
                negative_terms=tuple(str(term) for term in item.get("negative_terms", []) if str(term)),
                confidence_level=confidence,
                state_change_hint=hint,
                note=str(item.get("note") or ""),
                priority=index,
            )
        )
    return rules, sha256_text(raw_text)


def init_schema(conn: sqlite3.Connection) -> None:
    require_source_views(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l5_event_extraction_run (
            extraction_run_id TEXT PRIMARY KEY,
            run_scope TEXT NOT NULL,
            sample_chapters TEXT NOT NULL,
            rule_seed_hash TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            candidate_count INTEGER NOT NULL DEFAULT 0,
            state_change_candidate_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            note TEXT,
            CHECK(status IN ('running', 'passed', 'failed'))
        );

        CREATE TABLE IF NOT EXISTS l5_event_candidate (
            event_candidate_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            event_family TEXT NOT NULL,
            trigger_text TEXT NOT NULL,
            trigger_rule_id TEXT NOT NULL,
            status TEXT NOT NULL,
            confidence_level TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            version_id TEXT NOT NULL,
            scene_block_id TEXT,
            para_id TEXT NOT NULL,
            sentence_id TEXT NOT NULL,
            sentence_start_offset INTEGER NOT NULL,
            sentence_end_offset INTEGER NOT NULL,
            trigger_start_offset INTEGER NOT NULL,
            trigger_end_offset INTEGER NOT NULL,
            sentence_hash TEXT NOT NULL,
            paragraph_hash TEXT,
            l1_backcut_matched INTEGER NOT NULL,
            evidence_text TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            source_layer TEXT NOT NULL,
            extraction_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            CHECK(status = 'candidate'),
            CHECK(confidence_level IN ('low', 'medium', 'high')),
            CHECK(source_layer IN ('l2_sentence', 'l2_paragraph', 'l4_scene_block')),
            CHECK(event_family IN ('character_event', 'location_event', 'domain_event', 'gray_world_event', 'power_event', 'organization_event', 'object_event', 'relationship_event', 'unknown_event')),
            UNIQUE(chapter_id, version_id, sentence_id, trigger_start_offset, trigger_end_offset, trigger_text, event_type)
        );

        CREATE TABLE IF NOT EXISTS l5_event_argument_candidate (
            argument_id TEXT PRIMARY KEY,
            event_candidate_id TEXT NOT NULL,
            argument_role TEXT NOT NULL,
            entity_layer TEXT NOT NULL,
            entity_id TEXT,
            entity_text TEXT NOT NULL,
            entity_status TEXT,
            distance_scope TEXT NOT NULL,
            status TEXT NOT NULL,
            source_note TEXT,
            created_at TEXT NOT NULL,
            CHECK(argument_role IN ('subject', 'object', 'target', 'location', 'source_location', 'destination_location', 'affected_location', 'character', 'organization', 'power', 'gray_world_term', 'unknown')),
            CHECK(entity_layer IN ('l3_character', 'l3_location', 'l3_gray_world', 'raw_text', 'unknown')),
            CHECK(status = 'candidate'),
            UNIQUE(event_candidate_id, argument_role, entity_layer, entity_id, entity_text, distance_scope)
        );

        CREATE TABLE IF NOT EXISTS l5_event_state_change_candidate (
            state_change_candidate_id TEXT PRIMARY KEY,
            event_candidate_id TEXT NOT NULL,
            entity_layer TEXT NOT NULL,
            entity_id TEXT,
            entity_text TEXT NOT NULL,
            state_type TEXT NOT NULL,
            from_state TEXT,
            to_state TEXT,
            status TEXT NOT NULL,
            evidence_text TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            source_note TEXT,
            created_at TEXT NOT NULL,
            CHECK(entity_layer IN ('l3_character', 'l3_location', 'l3_gray_world', 'raw_text', 'unknown')),
            CHECK(state_type IN ('life_state', 'identity_state', 'location_state', 'domain_state', 'organization_state', 'power_state', 'relationship_state', 'unknown_state')),
            CHECK(status = 'candidate')
        );

        CREATE TABLE IF NOT EXISTS l5_event_evidence_span (
            evidence_span_id TEXT PRIMARY KEY,
            event_candidate_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            version_id TEXT NOT NULL,
            scene_block_id TEXT,
            para_id TEXT NOT NULL,
            sentence_id TEXT NOT NULL,
            span_start_offset INTEGER NOT NULL,
            span_end_offset INTEGER NOT NULL,
            span_text TEXT NOT NULL,
            span_hash TEXT NOT NULL,
            l1_backcut_matched INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(event_candidate_id, sentence_id, span_start_offset, span_end_offset, span_text)
        );
        """
    )


def clear_l5_tables(conn: sqlite3.Connection) -> None:
    for table in (
        "l5_event_argument_candidate",
        "l5_event_state_change_candidate",
        "l5_event_evidence_span",
        "l5_event_candidate",
        "l5_event_extraction_run",
    ):
        conn.execute(f"DELETE FROM {table}")


def source_table_fingerprints(conn: sqlite3.Connection) -> dict[str, int]:
    names = (
        "l2_index_status",
        "l2_paragraph_units",
        "l2_sentence_units",
        "l3_character_appearance",
        "l3_location_appearance",
        "l3_location_def",
        "l3_location_alias",
        "l3_location_candidate",
        "l4_scene_blocks",
        "l3_scene_blocks",
        "scene_blocks",
    )
    result: dict[str, int] = {}
    for name in names:
        if object_exists(conn, name):
            result[name] = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
    return result


def paragraph_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    return {row["para_id"]: row["para_hash"] for row in conn.execute("SELECT para_id, para_hash FROM v_l2_current_paragraphs")}


def content_by_chapter_version(conn: sqlite3.Connection, sample_chapters: list[int]) -> dict[tuple[str, str], str]:
    placeholders = ",".join("?" for _ in sample_chapters)
    return {
        (row["chapter_id"], row["latest_version_id"]): row["content_full_text"]
        for row in conn.execute(
            f"SELECT chapter_id, latest_version_id, content_full_text FROM v_current_chapters WHERE chapter_num IN ({placeholders})",
            tuple(sample_chapters),
        )
    }


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def scene_block_id_column(columns: set[str]) -> str | None:
    for candidate in ("scene_block_id", "scene_id", "scene_key", "block_id"):
        if candidate in columns:
            return candidate
    return None


def discover_scene_block_source(conn: sqlite3.Connection) -> SceneBlockSource:
    invalid_notes: list[str] = []
    for table_name in SCENE_BLOCK_SOURCE_CANDIDATES:
        if not object_exists(conn, table_name, "table"):
            continue
        columns = table_columns(conn, table_name)
        id_column = scene_block_id_column(columns)
        required = {"chapter_num", "start_offset", "end_offset"}
        if id_column is not None and required.issubset(columns):
            status = "compatible_preferred" if table_name == "l4_scene_blocks" else "compatible_fallback"
            return SceneBlockSource(True, status, table_name, id_column, f"Using {table_name}.{id_column}")
        missing = sorted(required.difference(columns))
        if id_column is None:
            missing.append("scene_block_id|scene_id|scene_key|block_id")
        invalid_notes.append(f"{table_name} missing columns: {', '.join(missing)}")
    if invalid_notes:
        return SceneBlockSource(False, "invalid_schema", None, None, "; ".join(invalid_notes))
    return SceneBlockSource(False, "missing_optional", None, None, "No compatible scene block table found")


def scene_block_source_payload(source: SceneBlockSource, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    return {
        "detected_scene_block_source": source.detected,
        "scene_block_source_status": source.status,
        "scene_block_table_name": source.table_name,
        "scene_block_id_column": source.id_column,
        "scene_block_note": source.note,
    }


def scene_block_audit_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "scene_block_linked_event_count": int(conn.execute("SELECT COUNT(*) FROM l5_event_candidate WHERE scene_block_id IS NOT NULL AND scene_block_id != ''").fetchone()[0]),
        "event_without_scene_block_count": int(conn.execute("SELECT COUNT(*) FROM l5_event_candidate WHERE scene_block_id IS NULL OR scene_block_id = ''").fetchone()[0]),
    }


def scene_block_map(conn: sqlite3.Connection, sample_chapters: list[int], source: SceneBlockSource) -> dict[str, str]:
    if not source.detected or source.table_name is None or source.id_column is None:
        return {}
    placeholders = ",".join("?" for _ in sample_chapters)
    columns = table_columns(conn, source.table_name)
    rows = conn.execute(
        f"""
        SELECT {source.id_column} AS scene_block_id, chapter_num, start_offset, end_offset {', version_id' if 'version_id' in columns else ''}
        FROM {source.table_name}
        WHERE chapter_num IN ({placeholders})
        ORDER BY chapter_num, start_offset
        """,
        tuple(sample_chapters),
    ).fetchall()
    mapping: dict[str, str] = {}
    sentence_rows = conn.execute(
        f"""
        SELECT sentence_id, chapter_num, version_id, start_offset
        FROM v_l2_current_sentences
        WHERE chapter_num IN ({placeholders})
        """,
        tuple(sample_chapters),
    ).fetchall()
    for sentence in sentence_rows:
        for scene in rows:
            version_ok = "version_id" not in scene.keys() or scene["version_id"] == sentence["version_id"]
            if version_ok and int(scene["chapter_num"]) == int(sentence["chapter_num"]) and int(scene["start_offset"]) <= int(sentence["start_offset"]) < int(scene["end_offset"]):
                mapping[sentence["sentence_id"]] = str(scene["scene_block_id"])
                break
    return mapping


def collect_missing_optional_sources(conn: sqlite3.Connection) -> list[str]:
    optional = ("l3_character_appearance", "l3_location_appearance", "l3_location_def", "l3_location_alias", "l3_location_candidate")
    missing = [name for name in optional if not object_exists(conn, name, "table")]
    source = discover_scene_block_source(conn)
    if not source.detected:
        missing.append("scene_block_source")
    return missing


def find_rule_matches(sentence_text: str, sentence_start: int, rules: list[TriggerRule]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    raw: list[dict[str, Any]] = []
    for rule in rules:
        if any(term in sentence_text for term in rule.negative_terms):
            continue
        for term in rule.trigger_terms:
            cursor = 0
            while True:
                local_start = sentence_text.find(term, cursor)
                if local_start < 0:
                    break
                raw.append(
                    {
                        "rule": rule,
                        "trigger_text": term,
                        "start": sentence_start + local_start,
                        "end": sentence_start + local_start + len(term),
                    }
                )
                cursor = local_start + max(1, len(term))
    raw.sort(key=lambda item: (item["start"], item["end"], item["rule"].priority, -len(item["trigger_text"])))
    kept: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    occupied: set[tuple[int, int]] = set()
    for item in raw:
        span = (int(item["start"]), int(item["end"]))
        if span in occupied:
            skipped.append(
                {
                    "trigger_text": item["trigger_text"],
                    "trigger_start_offset": item["start"],
                    "trigger_end_offset": item["end"],
                    "event_type": item["rule"].event_type,
                    "trigger_rule_id": item["rule"].rule_id,
                    "reason": "same_trigger_span_lower_priority",
                }
            )
            continue
        occupied.add(span)
        kept.append(item)
    return kept, skipped


def insert_event_rows(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    match: dict[str, Any],
    *,
    extraction_run_id: str,
    paragraph_hash_by_id: dict[str, str],
    content_cache: dict[tuple[str, str], str],
    scene_by_sentence: dict[str, str],
) -> str:
    rule: TriggerRule = match["rule"]
    sentence_text = str(row["sentence_text"])
    sentence_start = int(row["start_offset"])
    sentence_end = int(row["end_offset"])
    trigger_start = int(match["start"])
    trigger_end = int(match["end"])
    trigger_text = str(match["trigger_text"])
    content = content_cache.get((row["chapter_id"], row["version_id"]), "")
    sentence_cut = content[sentence_start:sentence_end] if content else ""
    trigger_cut = content[trigger_start:trigger_end] if content else ""
    backcut = int(sentence_cut == sentence_text and sha256_text(sentence_cut) == row["sentence_hash"] and trigger_cut == trigger_text)
    event_candidate_id = stable_id(
        "evt",
        row["chapter_id"],
        row["version_id"],
        row["sentence_id"],
        trigger_start,
        trigger_end,
        trigger_text,
        rule.event_type,
    )
    scene_block_id = scene_by_sentence.get(str(row["sentence_id"]))
    conn.execute(
        """
        INSERT OR IGNORE INTO l5_event_candidate (
            event_candidate_id, event_type, event_family, trigger_text, trigger_rule_id,
            status, confidence_level, chapter_id, chapter_num, version_id, scene_block_id,
            para_id, sentence_id, sentence_start_offset, sentence_end_offset,
            trigger_start_offset, trigger_end_offset, sentence_hash, paragraph_hash,
            l1_backcut_matched, evidence_text, evidence_hash, source_layer,
            extraction_run_id, created_at
        )
        VALUES (?, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_candidate_id,
            rule.event_type,
            rule.event_family,
            trigger_text,
            rule.rule_id,
            rule.confidence_level,
            row["chapter_id"],
            int(row["chapter_num"]),
            row["version_id"],
            scene_block_id,
            row["para_id"],
            row["sentence_id"],
            sentence_start,
            sentence_end,
            trigger_start,
            trigger_end,
            row["sentence_hash"],
            paragraph_hash_by_id.get(row["para_id"]),
            backcut,
            sentence_text,
            sha256_text(sentence_text),
            SOURCE_LAYER,
            extraction_run_id,
            DETERMINISTIC_TIMESTAMP,
        ),
    )
    evidence_span_id = stable_id("evs", event_candidate_id, row["sentence_id"], sentence_start, sentence_end, sentence_text)
    conn.execute(
        """
        INSERT OR IGNORE INTO l5_event_evidence_span (
            evidence_span_id, event_candidate_id, chapter_id, chapter_num, version_id,
            scene_block_id, para_id, sentence_id, span_start_offset, span_end_offset,
            span_text, span_hash, l1_backcut_matched, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            evidence_span_id,
            event_candidate_id,
            row["chapter_id"],
            int(row["chapter_num"]),
            row["version_id"],
            scene_block_id,
            row["para_id"],
            row["sentence_id"],
            sentence_start,
            sentence_end,
            sentence_text,
            sha256_text(sentence_text),
            backcut,
            DETERMINISTIC_TIMESTAMP,
        ),
    )
    return event_candidate_id


def insert_argument_rows(conn: sqlite3.Connection, event_candidate_id: str, sentence_row: sqlite3.Row, trigger_text: str) -> None:
    added = False
    if object_exists(conn, "l3_character_appearance", "table"):
        rows = conn.execute(
            """
            SELECT character_id, matched_text, status, sentence_id, para_id
            FROM l3_character_appearance
            WHERE sentence_id = ? OR para_id = ?
            ORDER BY CASE WHEN sentence_id = ? THEN 0 ELSE 1 END, character_id, matched_text
            """,
            (sentence_row["sentence_id"], sentence_row["para_id"], sentence_row["sentence_id"]),
        ).fetchall()
        seen_entities: set[tuple[str, str, str]] = set()
        for entity in rows:
            distance_scope = "same_sentence" if entity["sentence_id"] == sentence_row["sentence_id"] else "same_paragraph"
            key = ("l3_character", str(entity["character_id"]), str(entity["matched_text"]))
            if key in seen_entities:
                continue
            seen_entities.add(key)
            argument_id = stable_id("arg", event_candidate_id, "character", "l3_character", entity["character_id"], entity["matched_text"], distance_scope)
            conn.execute(
                """
                INSERT OR IGNORE INTO l5_event_argument_candidate (
                    argument_id, event_candidate_id, argument_role, entity_layer, entity_id,
                    entity_text, entity_status, distance_scope, status, source_note, created_at
                )
                VALUES (?, ?, 'character', 'l3_character', ?, ?, ?, ?, 'candidate', ?, ?)
                """,
                (
                    argument_id,
                    event_candidate_id,
                    entity["character_id"],
                    entity["matched_text"],
                    entity["status"],
                    distance_scope,
                    "l3_character_appearance",
                    DETERMINISTIC_TIMESTAMP,
                ),
            )
            added = True
    if not added:
        argument_id = stable_id("arg", event_candidate_id, "unknown", "raw_text", "", trigger_text, "same_sentence")
        conn.execute(
            """
            INSERT OR IGNORE INTO l5_event_argument_candidate (
                argument_id, event_candidate_id, argument_role, entity_layer, entity_id,
                entity_text, entity_status, distance_scope, status, source_note, created_at
            )
            VALUES (?, ?, 'unknown', 'raw_text', NULL, ?, 'candidate', 'same_sentence', 'candidate', 'trigger_text_fallback', ?)
            """,
            (argument_id, event_candidate_id, trigger_text, DETERMINISTIC_TIMESTAMP),
        )


def insert_state_change_row(conn: sqlite3.Connection, event_candidate_id: str, rule: TriggerRule, evidence_text: str, trigger_text: str) -> None:
    if rule.state_change_hint is None:
        return
    hint = rule.state_change_hint
    state_change_candidate_id = stable_id("stc", event_candidate_id, hint.get("state_type"), hint.get("from_state"), hint.get("to_state"))
    conn.execute(
        """
        INSERT OR IGNORE INTO l5_event_state_change_candidate (
            state_change_candidate_id, event_candidate_id, entity_layer, entity_id, entity_text,
            state_type, from_state, to_state, status, evidence_text, evidence_hash,
            source_note, created_at
        )
        VALUES (?, ?, 'raw_text', NULL, ?, ?, ?, ?, 'candidate', ?, ?, ?, ?)
        """,
        (
            state_change_candidate_id,
            event_candidate_id,
            trigger_text,
            hint.get("state_type", "unknown_state"),
            hint.get("from_state"),
            hint.get("to_state"),
            evidence_text,
            sha256_text(evidence_text),
            f"explicit trigger rule {rule.rule_id}",
            DETERMINISTIC_TIMESTAMP,
        ),
    )


def build_json_report(conn: sqlite3.Connection, stats: ExtractionStats) -> dict[str, Any]:
    family_counts = dict(conn.execute("SELECT event_family, COUNT(*) FROM l5_event_candidate GROUP BY event_family ORDER BY event_family").fetchall())
    type_counts = dict(conn.execute("SELECT event_type, COUNT(*) FROM l5_event_candidate GROUP BY event_type ORDER BY event_type").fetchall())
    trigger_counts = [
        {"trigger_text": row["trigger_text"], "count": int(row["n"])}
        for row in conn.execute("SELECT trigger_text, COUNT(*) AS n FROM l5_event_candidate GROUP BY trigger_text ORDER BY n DESC, trigger_text LIMIT 20")
    ]
    candidates = [
        dict(row)
        for row in conn.execute(
            """
            SELECT event_candidate_id, event_family, event_type, trigger_text, chapter_num,
                   sentence_id, trigger_start_offset, trigger_end_offset, evidence_text,
                   confidence_level, status
            FROM l5_event_candidate
            ORDER BY chapter_num, sentence_start_offset, trigger_start_offset, event_type
            """
        )
    ]
    scene_source = stats.detected_scene_block_source or scene_block_source_payload(discover_scene_block_source(conn))
    scene_source.update(scene_block_audit_counts(conn))
    return {
        "run_summary": {
            "extraction_run_id": stats.extraction_run_id,
            "rule_seed_hash": stats.rule_seed_hash,
            "status": "passed",
        },
        "sample_chapters": stats.sample_chapters,
        "event_candidate_count": stats.candidate_count,
        "state_change_candidate_count": stats.state_change_candidate_count,
        "event_family_counts": family_counts,
        "event_type_counts": type_counts,
        "missing_optional_sources": stats.missing_optional_sources,
        "scene_block_source": scene_source,
        "detected_scene_block_source": scene_source["detected_scene_block_source"],
        "scene_block_source_status": scene_source["scene_block_source_status"],
        "scene_block_table_name": scene_source["scene_block_table_name"],
        "scene_block_linked_event_count": scene_source["scene_block_linked_event_count"],
        "event_without_scene_block_count": scene_source["event_without_scene_block_count"],
        "top_trigger_terms": trigger_counts,
        "duplicate_skips": stats.duplicate_skips,
        "candidates": candidates,
    }


def build_markdown_report(payload: dict[str, Any]) -> str:
    lines = [
        "# L5 Event Candidate Sample Report",
        "",
        "## Run Summary",
        "",
        f"- extraction_run_id: {payload['run_summary']['extraction_run_id']}",
        f"- status: {payload['run_summary']['status']}",
        f"- event_candidate_count: {payload['event_candidate_count']}",
        f"- state_change_candidate_count: {payload['state_change_candidate_count']}",
        "",
        "## Scope",
        "",
        f"- sample_chapters: {', '.join(str(item) for item in payload['sample_chapters'])}",
        "",
        "## Counts by Event Family",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in payload["event_family_counts"].items()) or lines.append("- none")
    lines.extend(["", "## Counts by Event Type", ""])
    lines.extend(f"- {key}: {value}" for key, value in payload["event_type_counts"].items()) or lines.append("- none")
    lines.extend(["", "## Missing Optional Sources", ""])
    lines.extend(f"- {item}" for item in payload["missing_optional_sources"]) or lines.append("- none")
    lines.extend(["", "## Scene Block Source Audit", ""])
    scene_source = payload.get("scene_block_source", {})
    lines.extend(
        [
            f"- detected_scene_block_source: {payload.get('detected_scene_block_source')}",
            f"- scene_block_source_status: {payload.get('scene_block_source_status')}",
            f"- scene_block_table_name: {payload.get('scene_block_table_name')}",
            f"- scene_block_linked_event_count: {payload.get('scene_block_linked_event_count')}",
            f"- event_without_scene_block_count: {payload.get('event_without_scene_block_count')}",
            f"- scene_block_note: {scene_source.get('scene_block_note')}",
        ]
    )
    lines.extend(["", "## Sample Candidates", ""])
    for item in payload["candidates"][:30]:
        lines.append(f"- ch{item['chapter_num']} {item['event_type']} `{item['trigger_text']}` {item['sentence_id']}")
    if not payload["candidates"]:
        lines.append("- none")
    lines.extend(["", "## State Change Candidates", ""])
    lines.append(f"- total: {payload['state_change_candidate_count']}")
    lines.extend(["", "## Duplicate Skips", ""])
    for item in payload["duplicate_skips"][:50]:
        lines.append(f"- {item['reason']}: {item['event_type']} `{item['trigger_text']}` at {item['trigger_start_offset']}-{item['trigger_end_offset']}")
    if not payload["duplicate_skips"]:
        lines.append("- none")
    lines.extend(["", "## Verification Notes", "", "- Verifier must recompute offsets and L1/L2 backcuts.", ""])
    return "\n".join(lines)


def run_l5_event_candidate_extractor(
    project_dir: Path | str | None = None,
    *,
    sample_chapters: str | None = None,
    rebuild: bool = False,
) -> ExtractionStats:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    sample = parse_sample_chapters(sample_chapters)
    rules, rule_seed_hash = load_rules(root)
    extraction_run_id = stable_id("l5run", "sample", ",".join(str(item) for item in sample), rule_seed_hash, length=20)
    stats = ExtractionStats(
        db_path=root / DB_RELATIVE_PATH,
        json_report_path=root / JSON_REPORT_RELATIVE_PATH,
        md_report_path=root / MD_REPORT_RELATIVE_PATH,
        extraction_run_id=extraction_run_id,
        sample_chapters=sample,
        rule_seed_hash=rule_seed_hash,
    )
    conn = sqlite3.connect(stats.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    before_fingerprints: dict[str, int] = {}
    try:
        with conn:
            init_schema(conn)
            before_fingerprints = source_table_fingerprints(conn)
            if rebuild:
                clear_l5_tables(conn)
            stats.missing_optional_sources = collect_missing_optional_sources(conn)
            paragraph_hash_by_id = paragraph_hashes(conn)
            content_cache = content_by_chapter_version(conn, sample)
            scene_source = discover_scene_block_source(conn)
            stats.detected_scene_block_source = scene_block_source_payload(scene_source)
            scene_by_sentence = scene_block_map(conn, sample, scene_source)
            conn.execute(
                """
                INSERT OR REPLACE INTO l5_event_extraction_run (
                    extraction_run_id, run_scope, sample_chapters, rule_seed_hash,
                    started_at, finished_at, candidate_count, state_change_candidate_count,
                    status, note
                )
                VALUES (?, 'sample', ?, ?, ?, NULL, 0, 0, 'running', ?)
                """,
                (extraction_run_id, ",".join(str(item) for item in sample), rule_seed_hash, DETERMINISTIC_TIMESTAMP, "L5.0 sample extraction"),
            )
            placeholders = ",".join("?" for _ in sample)
            sentence_rows = conn.execute(
                f"""
                SELECT *
                FROM v_l2_current_sentences
                WHERE chapter_num IN ({placeholders})
                ORDER BY chapter_num, global_sentence_index, sentence_id
                """,
                tuple(sample),
            ).fetchall()
            for row in sentence_rows:
                matches, skipped = find_rule_matches(str(row["sentence_text"]), int(row["start_offset"]), rules)
                stats.duplicate_skips.extend(skipped)
                for match in matches:
                    event_id = insert_event_rows(
                        conn,
                        row,
                        match,
                        extraction_run_id=extraction_run_id,
                        paragraph_hash_by_id=paragraph_hash_by_id,
                        content_cache=content_cache,
                        scene_by_sentence=scene_by_sentence,
                    )
                    insert_argument_rows(conn, event_id, row, str(match["trigger_text"]))
                    insert_state_change_row(conn, event_id, match["rule"], str(row["sentence_text"]), str(match["trigger_text"]))
            stats.candidate_count = int(conn.execute("SELECT COUNT(*) FROM l5_event_candidate").fetchone()[0])
            stats.state_change_candidate_count = int(conn.execute("SELECT COUNT(*) FROM l5_event_state_change_candidate").fetchone()[0])
            conn.execute(
                """
                UPDATE l5_event_extraction_run
                SET finished_at = ?, candidate_count = ?, state_change_candidate_count = ?, status = 'passed',
                    note = ?
                WHERE extraction_run_id = ?
                """,
                (
                    DETERMINISTIC_TIMESTAMP,
                    stats.candidate_count,
                    stats.state_change_candidate_count,
                    "Missing optional sources: " + ", ".join(stats.missing_optional_sources) if stats.missing_optional_sources else "All optional sources available",
                    extraction_run_id,
                ),
            )
            after_fingerprints = source_table_fingerprints(conn)
            if before_fingerprints != after_fingerprints:
                raise RuntimeError("L1/L2/L3/L4 source table fingerprint changed during L5 extraction")
            payload = build_json_report(conn, stats)
        stats.json_report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        stats.md_report_path.write_text(build_markdown_report(payload), encoding="utf-8")
    except Exception:
        with conn:
            if object_exists(conn, "l5_event_extraction_run", "table"):
                conn.execute(
                    """
                    INSERT OR REPLACE INTO l5_event_extraction_run (
                        extraction_run_id, run_scope, sample_chapters, rule_seed_hash,
                        started_at, finished_at, candidate_count, state_change_candidate_count,
                        status, note
                    )
                    VALUES (?, 'sample', ?, ?, ?, ?, 0, 0, 'failed', 'extractor exception')
                    """,
                    (extraction_run_id, ",".join(str(item) for item in sample), rule_seed_hash, DETERMINISTIC_TIMESTAMP, DETERMINISTIC_TIMESTAMP),
                )
        raise
    finally:
        conn.close()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L5 event and state-change candidate index.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--sample-chapters", type=str, default=None)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    stats = run_l5_event_candidate_extractor(args.project_dir, sample_chapters=args.sample_chapters, rebuild=args.rebuild)
    print(f"L5 event candidates: {stats.candidate_count}")


if __name__ == "__main__":
    main()
