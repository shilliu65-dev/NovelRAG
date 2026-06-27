from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_extractor import (
    ARGUMENT_ROLES,
    CONFIDENCE_LEVELS,
    ENTITY_LAYERS,
    EVENT_FAMILIES,
    EVENT_TYPES,
    JSON_REPORT_RELATIVE_PATH,
    MD_REPORT_RELATIVE_PATH,
    STATE_TYPES,
    collect_missing_optional_sources,
    discover_scene_block_source,
    parse_sample_chapters,
    run_l5_event_candidate_extractor,
    scene_block_audit_counts,
    sha256_text,
)


PASS_MESSAGE = "L5 event candidate FULL PASS"
FAIL_MESSAGE = "L5 event candidate VERIFY FAIL"
VERIFY_JSON_RELATIVE_PATH = Path("outputs") / "l5_event_candidate_verify_report.json"
VERIFY_MD_RELATIVE_PATH = Path("outputs") / "l5_event_candidate_verify_report.md"


@dataclass
class L5VerificationResult:
    ok: bool
    db_path: Path
    json_report_path: Path
    md_report_path: Path
    final_message: str = FAIL_MESSAGE
    checks: dict[str, int] = field(default_factory=dict)
    fatal_problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def object_exists(conn: sqlite3.Connection, name: str, object_type: str | None = None) -> bool:
    if object_type is None:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1", (object_type, name)).fetchone()
    return row is not None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def add_check(result: L5VerificationResult, key: str, value: int) -> None:
    result.checks[key] = value
    if value:
        result.fatal_problems.append(f"{key}={value}")


def verify_schema(conn: sqlite3.Connection, result: L5VerificationResult) -> bool:
    required_tables = (
        "l5_event_candidate",
        "l5_event_argument_candidate",
        "l5_event_state_change_candidate",
        "l5_event_evidence_span",
        "l5_event_extraction_run",
    )
    required_views = ("v_current_chapters", "v_l2_current_paragraphs", "v_l2_current_sentences")
    missing_tables = [name for name in required_tables if not object_exists(conn, name, "table")]
    missing_views = [name for name in required_views if not object_exists(conn, name, "view")]
    if missing_tables:
        result.fatal_problems.append(f"missing_l5_tables={', '.join(missing_tables)}")
    if missing_views:
        result.fatal_problems.append(f"missing_required_views={', '.join(missing_views)}")
    return not result.fatal_problems


def duplicate_count(conn: sqlite3.Connection, sql: str) -> int:
    rows = conn.execute(sql).fetchall()
    return sum(int(row["n"]) - 1 for row in rows)


def validate_enums_and_duplicates(conn: sqlite3.Connection, result: L5VerificationResult, sample: list[int]) -> None:
    placeholders = ",".join("?" for _ in sample)
    add_check(result, "event_outside_sample_scope", scalar(conn, f"SELECT COUNT(*) FROM l5_event_candidate WHERE chapter_num NOT IN ({placeholders})", tuple(sample)))
    add_check(result, "invalid_event_status", scalar(conn, "SELECT COUNT(*) FROM l5_event_candidate WHERE status != 'candidate'"))
    add_check(result, "invalid_state_change_status", scalar(conn, "SELECT COUNT(*) FROM l5_event_state_change_candidate WHERE status != 'candidate'"))
    add_check(result, "invalid_argument_status", scalar(conn, "SELECT COUNT(*) FROM l5_event_argument_candidate WHERE status != 'candidate'"))
    add_check(result, "invalid_event_family", scalar(conn, f"SELECT COUNT(*) FROM l5_event_candidate WHERE event_family NOT IN ({','.join('?' for _ in EVENT_FAMILIES)})", tuple(sorted(EVENT_FAMILIES))))
    add_check(result, "invalid_event_type", scalar(conn, f"SELECT COUNT(*) FROM l5_event_candidate WHERE event_type NOT IN ({','.join('?' for _ in EVENT_TYPES)})", tuple(sorted(EVENT_TYPES))))
    add_check(result, "invalid_confidence_level", scalar(conn, f"SELECT COUNT(*) FROM l5_event_candidate WHERE confidence_level NOT IN ({','.join('?' for _ in CONFIDENCE_LEVELS)})", tuple(sorted(CONFIDENCE_LEVELS))))
    add_check(result, "invalid_state_type", scalar(conn, f"SELECT COUNT(*) FROM l5_event_state_change_candidate WHERE state_type NOT IN ({','.join('?' for _ in STATE_TYPES)})", tuple(sorted(STATE_TYPES))))
    add_check(result, "invalid_argument_role", scalar(conn, f"SELECT COUNT(*) FROM l5_event_argument_candidate WHERE argument_role NOT IN ({','.join('?' for _ in ARGUMENT_ROLES)})", tuple(sorted(ARGUMENT_ROLES))))
    add_check(result, "invalid_entity_layer_argument", scalar(conn, f"SELECT COUNT(*) FROM l5_event_argument_candidate WHERE entity_layer NOT IN ({','.join('?' for _ in ENTITY_LAYERS)})", tuple(sorted(ENTITY_LAYERS))))
    add_check(result, "invalid_entity_layer_state", scalar(conn, f"SELECT COUNT(*) FROM l5_event_state_change_candidate WHERE entity_layer NOT IN ({','.join('?' for _ in ENTITY_LAYERS)})", tuple(sorted(ENTITY_LAYERS))))
    add_check(
        result,
        "duplicate_event_candidate_key",
        duplicate_count(
            conn,
            """
            SELECT COUNT(*) AS n
            FROM l5_event_candidate
            GROUP BY chapter_id, version_id, sentence_id, trigger_start_offset, trigger_end_offset, trigger_text, event_type
            HAVING COUNT(*) > 1
            """,
        ),
    )
    add_check(
        result,
        "duplicate_evidence_span_key",
        duplicate_count(
            conn,
            """
            SELECT COUNT(*) AS n
            FROM l5_event_evidence_span
            GROUP BY event_candidate_id, sentence_id, span_start_offset, span_end_offset, span_text
            HAVING COUNT(*) > 1
            """,
        ),
    )
    add_check(
        result,
        "duplicate_argument_key",
        duplicate_count(
            conn,
            """
            SELECT COUNT(*) AS n
            FROM l5_event_argument_candidate
            GROUP BY event_candidate_id, argument_role, entity_layer, entity_id, entity_text, distance_scope
            HAVING COUNT(*) > 1
            """,
        ),
    )


def validate_backcuts(conn: sqlite3.Connection, result: L5VerificationResult) -> None:
    noncurrent_version_reference = 0
    offset_out_of_bounds = 0
    trigger_text_mismatch = 0
    evidence_text_mismatch = 0
    sentence_hash_mismatch = 0
    l1_backcut_flag_mismatch = 0
    missing_evidence_span = 0
    rows = conn.execute(
        """
        SELECT e.*, c.latest_version_id, c.content_full_text, c.content_length
        FROM l5_event_candidate e
        LEFT JOIN v_current_chapters c ON c.chapter_id = e.chapter_id
        ORDER BY e.chapter_num, e.sentence_start_offset, e.trigger_start_offset
        """
    ).fetchall()
    for row in rows:
        if row["content_full_text"] is None or row["latest_version_id"] != row["version_id"]:
            noncurrent_version_reference += 1
            continue
        content = row["content_full_text"]
        content_length = int(row["content_length"])
        sentence_start = int(row["sentence_start_offset"])
        sentence_end = int(row["sentence_end_offset"])
        trigger_start = int(row["trigger_start_offset"])
        trigger_end = int(row["trigger_end_offset"])
        if (
            sentence_start < 0
            or sentence_start >= sentence_end
            or sentence_end > content_length
            or trigger_start < sentence_start
            or trigger_start >= trigger_end
            or trigger_end > sentence_end
        ):
            offset_out_of_bounds += 1
            continue
        sentence_cut = content[sentence_start:sentence_end]
        trigger_cut = content[trigger_start:trigger_end]
        sentence_ok = sha256_text(sentence_cut) == row["sentence_hash"]
        trigger_ok = trigger_cut == row["trigger_text"]
        evidence_ok = sentence_cut == row["evidence_text"]
        actual_flag = int(sentence_ok and trigger_ok and evidence_ok)
        if not trigger_ok:
            trigger_text_mismatch += 1
        if not evidence_ok:
            evidence_text_mismatch += 1
        if not sentence_ok:
            sentence_hash_mismatch += 1
        if actual_flag != int(row["l1_backcut_matched"]):
            l1_backcut_flag_mismatch += 1
        span_count = scalar(conn, "SELECT COUNT(*) FROM l5_event_evidence_span WHERE event_candidate_id = ?", (row["event_candidate_id"],))
        if span_count < 1:
            missing_evidence_span += 1
    add_check(result, "noncurrent_version_reference", noncurrent_version_reference)
    add_check(result, "offset_out_of_bounds", offset_out_of_bounds)
    add_check(result, "trigger_text_mismatch", trigger_text_mismatch)
    add_check(result, "evidence_text_mismatch", evidence_text_mismatch)
    add_check(result, "sentence_hash_mismatch", sentence_hash_mismatch)
    add_check(result, "l1_backcut_flag_mismatch", l1_backcut_flag_mismatch)
    add_check(result, "event_without_evidence_span", missing_evidence_span)

    span_offset_out_of_bounds = 0
    span_text_mismatch = 0
    span_flag_mismatch = 0
    for row in conn.execute(
        """
        SELECT s.*, c.latest_version_id, c.content_full_text, c.content_length
        FROM l5_event_evidence_span s
        LEFT JOIN v_current_chapters c ON c.chapter_id = s.chapter_id
        """
    ):
        if row["content_full_text"] is None or row["latest_version_id"] != row["version_id"]:
            span_text_mismatch += 1
            continue
        content = row["content_full_text"]
        start = int(row["span_start_offset"])
        end = int(row["span_end_offset"])
        if start < 0 or start >= end or end > int(row["content_length"]):
            span_offset_out_of_bounds += 1
            continue
        span_cut = content[start:end]
        actual_flag = int(span_cut == row["span_text"] and sha256_text(span_cut) == row["span_hash"])
        if span_cut != row["span_text"]:
            span_text_mismatch += 1
        if actual_flag != int(row["l1_backcut_matched"]):
            span_flag_mismatch += 1
    add_check(result, "span_offset_out_of_bounds", span_offset_out_of_bounds)
    add_check(result, "span_text_mismatch", span_text_mismatch)
    add_check(result, "span_backcut_flag_mismatch", span_flag_mismatch)


def validate_reports_and_optional_sources(root: Path, conn: sqlite3.Connection, result: L5VerificationResult) -> None:
    missing_reports = 0
    for relative in (JSON_REPORT_RELATIVE_PATH, MD_REPORT_RELATIVE_PATH):
        if not (root / relative).exists():
            missing_reports += 1
    add_check(result, "missing_required_reports", missing_reports)
    missing_optional = collect_missing_optional_sources(conn)
    run_note = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM l5_event_extraction_run
        WHERE note IS NOT NULL
          AND (note LIKE '%Missing optional sources:%' OR note LIKE '%All optional sources available%')
        """,
    )
    if missing_optional and run_note == 0:
        add_check(result, "missing_optional_sources_not_reported", 1)
    else:
        result.checks["missing_optional_sources_not_reported"] = 0
    result.warnings.extend(f"missing_optional_source={name}" for name in missing_optional)


def validate_scene_block_source(conn: sqlite3.Connection, result: L5VerificationResult) -> None:
    source = discover_scene_block_source(conn)
    counts = scene_block_audit_counts(conn)
    result.checks["scene_block_linked_event_count"] = counts["scene_block_linked_event_count"]
    result.checks["event_without_scene_block_count"] = counts["event_without_scene_block_count"]
    if not source.detected:
        result.checks["invalid_scene_block_reference"] = 0
        result.warnings.append(f"scene_block_source_warning={source.status}: {source.note}")
        return
    if source.table_name is None or source.id_column is None:
        add_check(result, "invalid_scene_block_reference", counts["scene_block_linked_event_count"])
        return
    event_refs = {
        str(row["scene_block_id"])
        for row in conn.execute(
            """
            SELECT DISTINCT scene_block_id
            FROM l5_event_candidate
            WHERE scene_block_id IS NOT NULL AND scene_block_id != ''
            """
        )
    }
    span_refs = {
        str(row["scene_block_id"])
        for row in conn.execute(
            """
            SELECT DISTINCT scene_block_id
            FROM l5_event_evidence_span
            WHERE scene_block_id IS NOT NULL AND scene_block_id != ''
            """
        )
    }
    used_refs = event_refs.union(span_refs)
    if not used_refs:
        result.checks["invalid_scene_block_reference"] = 0
        return
    valid_refs = {
        str(row["scene_block_id"])
        for row in conn.execute(f"SELECT {source.id_column} AS scene_block_id FROM {source.table_name}")
    }
    add_check(result, "invalid_scene_block_reference", len(used_refs.difference(valid_refs)))


def query_signature(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> str:
    rows = [dict(row) for row in conn.execute(sql, params).fetchall()]
    return sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def source_fingerprint(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        result: dict[str, str] = {}
        if object_exists(conn, "v_current_chapters", "view"):
            result["v_current_chapters"] = query_signature(
                conn,
                """
                SELECT chapter_id, chapter_num, latest_version_id, content_hash, content_length
                FROM v_current_chapters
                ORDER BY chapter_id
                """,
            )
        if object_exists(conn, "v_l2_current_paragraphs", "view"):
            result["v_l2_current_paragraphs"] = query_signature(
                conn,
                """
                SELECT para_id, chapter_id, version_id, start_offset, end_offset, para_hash
                FROM v_l2_current_paragraphs
                ORDER BY para_id
                """,
            )
        if object_exists(conn, "v_l2_current_sentences", "view"):
            result["v_l2_current_sentences"] = query_signature(
                conn,
                """
                SELECT sentence_id, para_id, chapter_id, version_id, start_offset, end_offset, sentence_hash
                FROM v_l2_current_sentences
                ORDER BY sentence_id
                """,
            )
        for name in (
            "l3_character_appearance",
            "l3_location_appearance",
            "l3_location_def",
            "l3_location_alias",
            "l3_location_candidate",
            "l4_scene_blocks",
            "l3_scene_blocks",
            "scene_blocks",
        ):
            if object_exists(conn, name, "table"):
                columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({name})")]
                order_column = columns[0]
                result[name] = query_signature(conn, f"SELECT * FROM {name} ORDER BY {order_column}")
        return result
    finally:
        conn.close()


def l5_fingerprint(db_path: Path) -> dict[str, str]:
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        return {
            "l5_event_candidate": query_signature(
                conn,
                """
                SELECT event_candidate_id, event_type, event_family, trigger_text, trigger_rule_id,
                       status, confidence_level, chapter_id, chapter_num, version_id, scene_block_id,
                       para_id, sentence_id, sentence_start_offset, sentence_end_offset,
                       trigger_start_offset, trigger_end_offset, sentence_hash, paragraph_hash,
                       l1_backcut_matched, evidence_text, evidence_hash, source_layer, extraction_run_id
                FROM l5_event_candidate
                ORDER BY event_candidate_id
                """,
            ),
            "l5_event_argument_candidate": query_signature(
                conn,
                """
                SELECT argument_id, event_candidate_id, argument_role, entity_layer, entity_id,
                       entity_text, entity_status, distance_scope, status, source_note
                FROM l5_event_argument_candidate
                ORDER BY argument_id
                """,
            ),
            "l5_event_state_change_candidate": query_signature(
                conn,
                """
                SELECT state_change_candidate_id, event_candidate_id, entity_layer, entity_id,
                       entity_text, state_type, from_state, to_state, status, evidence_text,
                       evidence_hash, source_note
                FROM l5_event_state_change_candidate
                ORDER BY state_change_candidate_id
                """,
            ),
            "l5_event_evidence_span": query_signature(
                conn,
                """
                SELECT evidence_span_id, event_candidate_id, chapter_id, chapter_num, version_id,
                       scene_block_id, para_id, sentence_id, span_start_offset, span_end_offset,
                       span_text, span_hash, l1_backcut_matched
                FROM l5_event_evidence_span
                ORDER BY evidence_span_id
                """,
            ),
        }
    finally:
        conn.close()


def validate_rebuild_idempotency_and_source_immutability(root: Path, result: L5VerificationResult, sample_chapters: str | None) -> None:
    before_source = source_fingerprint(result.db_path)
    run_l5_event_candidate_extractor(root, sample_chapters=sample_chapters, rebuild=True)
    first_l5 = l5_fingerprint(result.db_path)
    run_l5_event_candidate_extractor(root, sample_chapters=sample_chapters, rebuild=True)
    second_l5 = l5_fingerprint(result.db_path)
    after_source = source_fingerprint(result.db_path)
    add_check(result, "rebuild_not_idempotent", int(first_l5 != second_l5))
    add_check(result, "source_tables_modified_by_l5", int(before_source != after_source))


def build_reports(result: L5VerificationResult) -> None:
    payload = {
        "ok": result.ok,
        "final_message": result.final_message,
        "checks": result.checks,
        "fatal_problems": result.fatal_problems,
        "warnings": result.warnings,
    }
    result.json_report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# L5 Event Candidate Verification Report",
        "",
        f"- Final: {'PASS' if result.ok else 'FAIL'}",
        "",
        "## Checks",
        "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(result.checks.items()))
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in result.warnings) if result.warnings else lines.append("- none")
    lines.extend(["", "## Fatal Problems", ""])
    lines.extend(f"- {item}" for item in result.fatal_problems) if result.fatal_problems else lines.append("- none")
    lines.extend(["", result.final_message, ""])
    result.md_report_path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_event_candidate_verification(project_dir: Path | str | None = None, *, sample_chapters: str | None = None) -> L5VerificationResult:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
    result = L5VerificationResult(
        ok=False,
        db_path=root / DB_RELATIVE_PATH,
        json_report_path=root / VERIFY_JSON_RELATIVE_PATH,
        md_report_path=root / VERIFY_MD_RELATIVE_PATH,
    )
    sample = parse_sample_chapters(sample_chapters)
    conn = sqlite3.connect(result.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if verify_schema(conn, result):
            validate_enums_and_duplicates(conn, result, sample)
            validate_backcuts(conn, result)
            validate_reports_and_optional_sources(root, conn, result)
            validate_scene_block_source(conn, result)
    finally:
        conn.close()
    if not result.fatal_problems:
        validate_rebuild_idempotency_and_source_immutability(root, result, sample_chapters)
    result.ok = not result.fatal_problems
    result.final_message = PASS_MESSAGE if result.ok else FAIL_MESSAGE
    build_reports(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5 event candidate index.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--sample-chapters", type=str, default=None)
    args = parser.parse_args()
    result = run_l5_event_candidate_verification(args.project_dir, sample_chapters=args.sample_chapters)
    print(result.final_message)
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
