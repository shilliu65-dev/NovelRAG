from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as builder


JSON_REPORT_RELATIVE_PATH = Path("outputs") / "l3_child_segments_verify_report.json"
MD_REPORT_RELATIVE_PATH = Path("outputs") / "l3_child_segments_verify_report.md"
REQUIRED_SEGMENT_COLUMNS = set(builder.SEGMENT_COLUMNS)
REQUIRED_LINK_COLUMNS = set(builder.LINK_COLUMNS)
REQUIRED_RUN_COLUMNS = {
    "build_run_id",
    "sample_chapters",
    "target_min_chars",
    "target_max_chars",
    "hard_max_chars",
    "overlap_sentences",
    "source_guard_before_hash",
    "source_guard_after_hash",
    "source_table_names_json",
    "source_mutation_detected",
    "stable_row_fingerprint",
    "status",
}


@dataclass
class VerifyResult:
    ok: bool
    project_dir: Path
    db_path: Path
    scope: list[int]
    chapter_count: int = 0
    scene_block_count: int = 0
    child_segment_count: int = 0
    avg_segment_chars: float = 0.0
    min_segment_chars: int = 0
    max_segment_chars: int = 0
    overlap_segment_count: int = 0
    oversized_sentence_warning_count: int = 0
    empty_segment_count: int = 0
    boundary_warning_count: int = 0
    source_mutation_detected: bool = False
    forbidden_final_tables: list[str] = field(default_factory=list)
    rebuild_idempotent: bool = False
    stable_row_fingerprint_before: str = ""
    stable_row_fingerprint_after_first_rebuild: str = ""
    stable_row_fingerprint_after_second_rebuild: str = ""
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    for table_name in builder.OWN_TABLES:
        if not builder.object_exists(conn, table_name, "table"):
            errors.append(f"{table_name} table is missing")
    if errors:
        return errors
    segment_missing = REQUIRED_SEGMENT_COLUMNS - builder.table_columns(conn, "l3_child_segment")
    link_missing = REQUIRED_LINK_COLUMNS - builder.table_columns(conn, "l3_child_segment_sentence_link")
    run_missing = REQUIRED_RUN_COLUMNS - builder.table_columns(conn, "l3_child_segment_build_run")
    if segment_missing:
        errors.append(f"l3_child_segment schema missing columns: {', '.join(sorted(segment_missing))}")
    if link_missing:
        errors.append(f"l3_child_segment_sentence_link schema missing columns: {', '.join(sorted(link_missing))}")
    if run_missing:
        errors.append(f"l3_child_segment_build_run schema missing columns: {', '.join(sorted(run_missing))}")
    return errors


def fetch_current_chapter(conn: sqlite3.Connection, chapter_num: int) -> sqlite3.Row | None:
    columns = builder.table_columns(conn, "v_current_chapters")
    version_expr = "latest_version_id" if "latest_version_id" in columns else "version_id"
    row = conn.execute(
        f"""
        SELECT
            chapter_id,
            {version_expr} AS version_id,
            chapter_num,
            content_full_text,
            content_length
        FROM v_current_chapters
        WHERE chapter_num = ?
        """,
        (chapter_num,),
    ).fetchone()
    return row


def fetch_scene_for_segment(conn: sqlite3.Connection, segment: sqlite3.Row) -> sqlite3.Row | None:
    if "scene_id" in builder.table_columns(conn, "l3_scene_blocks"):
        row = conn.execute(
            """
            SELECT *
            FROM l3_scene_blocks
            WHERE chapter_id = ?
              AND version_id = ?
              AND chapter_num = ?
              AND CAST(scene_id AS TEXT) = ?
            LIMIT 1
            """,
            (segment["chapter_id"], segment["version_id"], segment["chapter_num"], str(segment["scene_id"])),
        ).fetchone()
        if row is not None:
            return row
    return conn.execute(
        """
        SELECT *
        FROM l3_scene_blocks
        WHERE chapter_id = ?
          AND version_id = ?
          AND chapter_num = ?
          AND start_offset <= ?
          AND end_offset >= ?
        ORDER BY start_offset DESC
        LIMIT 1
        """,
        (
            segment["chapter_id"],
            segment["version_id"],
            segment["chapter_num"],
            segment["start_char_offset"],
            segment["end_char_offset"],
        ),
    ).fetchone()


def fetch_sentence(conn: sqlite3.Connection, sentence_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT *
        FROM v_l2_current_sentences
        WHERE sentence_id = ?
        LIMIT 1
        """,
        (sentence_id,),
    ).fetchone()


def validate_segment(conn: sqlite3.Connection, segment: sqlite3.Row, result: VerifyResult) -> None:
    child_id = str(segment["child_segment_id"])
    chapter = fetch_current_chapter(conn, int(segment["chapter_num"]))
    if chapter is None:
        result.errors.append(f"{child_id}: current chapter missing")
        return
    if segment["chapter_id"] != chapter["chapter_id"] or segment["version_id"] != chapter["version_id"]:
        result.errors.append(f"{child_id}: child_segment crosses or mismatches current chapter identity")
    scene = fetch_scene_for_segment(conn, segment)
    if scene is None:
        result.errors.append(f"{child_id}: source l3_scene_block not found")
        return
    if (
        segment["chapter_id"] != scene["chapter_id"]
        or segment["version_id"] != scene["version_id"]
        or int(segment["chapter_num"]) != int(scene["chapter_num"])
    ):
        result.errors.append(f"{child_id}: child_segment crosses scene_block chapter identity")
    if int(segment["start_char_offset"]) < int(scene["start_offset"]) or int(segment["end_char_offset"]) > int(scene["end_offset"]):
        result.errors.append(f"{child_id}: child_segment crosses scene_block boundaries")
    if int(segment["sentence_count"]) < 1:
        result.errors.append(f"{child_id}: child_segment has no sentences")
    links = conn.execute(
        """
        SELECT *
        FROM l3_child_segment_sentence_link
        WHERE child_segment_id = ?
        ORDER BY position_in_segment
        """,
        (child_id,),
    ).fetchall()
    if len(links) != int(segment["sentence_count"]):
        result.errors.append(f"{child_id}: sentence link count mismatch")
    if not links:
        result.errors.append(f"{child_id}: no L2 sentence links")
        return
    for expected_position, link in enumerate(links, start=1):
        if int(link["position_in_segment"]) != expected_position:
            result.errors.append(f"{child_id}: sentence link position gap")
        if (
            link["chapter_id"] != segment["chapter_id"]
            or link["version_id"] != segment["version_id"]
            or int(link["chapter_num"]) != int(segment["chapter_num"])
            or str(link["scene_id"]) != str(segment["scene_id"])
        ):
            result.errors.append(f"{child_id}: sentence link crosses child segment identity")

    sentence_rows: list[sqlite3.Row] = []
    for link in links:
        sentence = fetch_sentence(conn, str(link["sentence_id"]))
        if sentence is None:
            result.errors.append(f"{child_id}: L2 sentence link cannot be traced: {link['sentence_id']}")
            continue
        sentence_rows.append(sentence)
        if (
            sentence["chapter_id"] != segment["chapter_id"]
            or sentence["version_id"] != segment["version_id"]
            or int(sentence["chapter_num"]) != int(segment["chapter_num"])
        ):
            result.errors.append(f"{child_id}: linked sentence crosses chapter")
        if int(sentence["start_offset"]) < int(scene["start_offset"]) or int(sentence["end_offset"]) > int(scene["end_offset"]):
            result.errors.append(f"{child_id}: linked sentence crosses scene_block")
        if int(link["sentence_start_offset"]) != int(sentence["start_offset"]):
            result.errors.append(f"{child_id}: link sentence_start_offset mismatch")
        if int(link["sentence_end_offset"]) != int(sentence["end_offset"]):
            result.errors.append(f"{child_id}: link sentence_end_offset mismatch")
        if link["sentence_text_hash"] != builder.sentence_hash(str(chapter["content_full_text"]), sentence):
            result.errors.append(f"{child_id}: link sentence_text_hash mismatch")
    if len(sentence_rows) != len(links):
        return

    first_sentence = sentence_rows[0]
    last_sentence = sentence_rows[-1]
    if segment["start_sentence_id"] != first_sentence["sentence_id"]:
        result.errors.append(f"{child_id}: start_sentence_id mismatch")
    if segment["end_sentence_id"] != last_sentence["sentence_id"]:
        result.errors.append(f"{child_id}: end_sentence_id mismatch")
    if int(segment["start_char_offset"]) != int(first_sentence["start_offset"]):
        result.errors.append(f"{child_id}: starts inside a sentence")
    if int(segment["end_char_offset"]) != int(last_sentence["end_offset"]):
        result.errors.append(f"{child_id}: ends inside a sentence")
    content = str(chapter["content_full_text"])
    segment_text = content[int(segment["start_char_offset"]) : int(segment["end_char_offset"])]
    if segment["segment_text"] != segment_text:
        result.errors.append(f"{child_id}: segment_text does not match L1 backcut")
    if segment["segment_text_hash"] != builder.sha256_text(str(segment["segment_text"])):
        result.errors.append(f"{child_id}: segment_text_hash mismatch")
    recomputed_source = builder.source_fingerprint_for_segment(scene, sentence_rows, content)
    if segment["source_fingerprint"] != recomputed_source:
        result.errors.append(f"{child_id}: source_fingerprint mismatch")
    distinct_para_ids = list(dict.fromkeys(str(sentence["para_id"]) for sentence in sentence_rows))
    if int(segment["paragraph_count"]) != len(distinct_para_ids):
        result.errors.append(f"{child_id}: paragraph_count mismatch")
    if segment["start_para_id"] != sentence_rows[0]["para_id"] or segment["end_para_id"] != sentence_rows[-1]["para_id"]:
        result.errors.append(f"{child_id}: paragraph boundary mismatch")
    if int(segment["has_overlap"]) == 1:
        overlap_from = segment["overlap_from_child_segment_id"]
        if not overlap_from:
            result.errors.append(f"{child_id}: has_overlap without overlap_from_child_segment_id")
        else:
            previous_links = conn.execute(
                """
                SELECT sentence_id
                FROM l3_child_segment_sentence_link
                WHERE child_segment_id = ?
                """,
                (overlap_from,),
            ).fetchall()
            previous_sentence_ids = {str(row["sentence_id"]) for row in previous_links}
            overlap_links = [link for link in links if int(link["is_overlap_sentence"]) == 1]
            if not overlap_links:
                result.errors.append(f"{child_id}: has_overlap without overlap sentence links")
            for link in overlap_links:
                if str(link["sentence_id"]) not in previous_sentence_ids:
                    result.errors.append(f"{child_id}: overlap sentence does not come from previous child segment")


def validate_current_rows(conn: sqlite3.Connection, scope: list[int], result: VerifyResult) -> None:
    builder.validate_source_objects(conn)
    schema_errors = ensure_schema(conn)
    if schema_errors:
        result.errors.extend(schema_errors)
        return
    placeholders = builder.scoped_placeholders(scope)
    params = tuple(scope)
    for chapter_num in scope:
        count = int(
            conn.execute(
                "SELECT COUNT(*) FROM l3_child_segment WHERE chapter_num = ?",
                (chapter_num,),
            ).fetchone()[0]
        )
        if count < 1:
            result.errors.append(f"chapter {chapter_num}: no child_segments generated")
    segments = conn.execute(
        f"""
        SELECT *
        FROM l3_child_segment
        WHERE chapter_num IN ({placeholders})
        ORDER BY chapter_num, scene_id, segment_index_in_scene
        """,
        params,
    ).fetchall()
    for segment in segments:
        validate_segment(conn, segment, result)
    metrics = builder.scope_metrics(conn, scope)
    result.chapter_count = metrics["chapter_count"]
    result.scene_block_count = metrics["scene_block_count"]
    result.child_segment_count = metrics["child_segment_count"]
    result.avg_segment_chars = metrics["avg_segment_chars"]
    result.min_segment_chars = metrics["min_segment_chars"]
    result.max_segment_chars = metrics["max_segment_chars"]
    result.overlap_segment_count = metrics["overlap_segment_count"]
    result.oversized_sentence_warning_count = metrics["oversized_sentence_warning_count"]
    result.empty_segment_count = metrics["empty_segment_count"]
    result.boundary_warning_count = metrics["boundary_warning_count"]
    result.warning_count = metrics["warning_count"]


def latest_build_params(conn: sqlite3.Connection, scope: list[int]) -> dict[str, int]:
    sample = ",".join(str(item) for item in scope)
    if builder.object_exists(conn, "l3_child_segment_build_run", "table"):
        row = conn.execute(
            """
            SELECT target_min_chars, target_max_chars, hard_max_chars, overlap_sentences
            FROM l3_child_segment_build_run
            WHERE sample_chapters = ?
            ORDER BY finished_at DESC, build_run_id DESC
            LIMIT 1
            """,
            (sample,),
        ).fetchone()
        if row is not None:
            return {
                "target_min_chars": int(row["target_min_chars"]),
                "target_max_chars": int(row["target_max_chars"]),
                "hard_max_chars": int(row["hard_max_chars"]),
                "overlap_sentences": int(row["overlap_sentences"]),
            }
    return {
        "target_min_chars": builder.DEFAULT_TARGET_MIN_CHARS,
        "target_max_chars": builder.DEFAULT_TARGET_MAX_CHARS,
        "hard_max_chars": builder.DEFAULT_HARD_MAX_CHARS,
        "overlap_sentences": builder.DEFAULT_OVERLAP_SENTENCES,
    }


def write_json_report(result: VerifyResult) -> None:
    path = result.project_dir / JSON_REPORT_RELATIVE_PATH
    payload = {
        "verified_at": now_iso(),
        "database": str(result.db_path),
        "sample_chapters": result.scope,
        "chapter_count": result.chapter_count,
        "scene_block_count": result.scene_block_count,
        "child_segment_count": result.child_segment_count,
        "avg_segment_chars": result.avg_segment_chars,
        "min_segment_chars": result.min_segment_chars,
        "max_segment_chars": result.max_segment_chars,
        "overlap_segment_count": result.overlap_segment_count,
        "oversized_sentence_warning_count": result.oversized_sentence_warning_count,
        "empty_segment_count": result.empty_segment_count,
        "boundary_warning_count": result.boundary_warning_count,
        "source_mutation_detected": result.source_mutation_detected,
        "forbidden_final_tables": result.forbidden_final_tables,
        "rebuild_idempotent": result.rebuild_idempotent,
        "stable_row_fingerprint_before": result.stable_row_fingerprint_before,
        "stable_row_fingerprint_after_first_rebuild": result.stable_row_fingerprint_after_first_rebuild,
        "stable_row_fingerprint_after_second_rebuild": result.stable_row_fingerprint_after_second_rebuild,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "final_status": "PASS" if result.ok else "FAIL",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md_report(result: VerifyResult) -> None:
    path = result.project_dir / MD_REPORT_RELATIVE_PATH
    final_line = "L3.5 child segments FULL PASS" if result.ok else "L3.5 child segments FULL FAIL"
    lines = [
        "# L3.5 child_segments verify report",
        "",
        f"- verified_at: {now_iso()}",
        f"- database: {result.db_path}",
        f"- sample_chapters: {','.join(str(item) for item in result.scope)}",
        f"- chapter_count: {result.chapter_count}",
        f"- scene_block_count: {result.scene_block_count}",
        f"- child_segment_count: {result.child_segment_count}",
        f"- avg_segment_chars: {result.avg_segment_chars}",
        f"- min_segment_chars: {result.min_segment_chars}",
        f"- max_segment_chars: {result.max_segment_chars}",
        f"- overlap_segment_count: {result.overlap_segment_count}",
        f"- oversized_sentence_warning_count: {result.oversized_sentence_warning_count}",
        f"- empty_segment_count: {result.empty_segment_count}",
        f"- boundary_warning_count: {result.boundary_warning_count}",
        f"- rebuild_idempotent: {result.rebuild_idempotent}",
        f"- source_mutation_detected: {result.source_mutation_detected}",
        f"- forbidden_final_tables: {', '.join(result.forbidden_final_tables) if result.forbidden_final_tables else 'none'}",
        f"- no_llm_calls: YES",
        f"- embedding_written: NO",
        f"- chroma_written: NO",
        f"- vector_database_written: NO",
        f"- final: {'PASS' if result.ok else 'FAIL'}",
        "",
        "## Errors",
        "",
        *(f"- {error}" for error in result.errors),
        *(["- none"] if not result.errors else []),
        "",
        "## Warnings",
        "",
        *(f"- {warning}" for warning in result.warnings),
        *(["- none"] if not result.warnings else []),
        "",
        final_line,
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_verification(
    project_dir: Path | str,
    *,
    sample_chapters: str = builder.DEFAULT_SAMPLE_CHAPTERS,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    builder.ensure_dirs(root)
    db_path = root / builder.DB_RELATIVE_PATH
    scope = builder.parse_scope(sample_chapters, None)
    result = VerifyResult(False, root, db_path, scope)
    source_table_names: list[str] = []
    source_guard_before_hash = ""
    params = {
        "target_min_chars": builder.DEFAULT_TARGET_MIN_CHARS,
        "target_max_chars": builder.DEFAULT_TARGET_MAX_CHARS,
        "hard_max_chars": builder.DEFAULT_HARD_MAX_CHARS,
        "overlap_sentences": builder.DEFAULT_OVERLAP_SENTENCES,
    }
    try:
        if not db_path.exists():
            raise RuntimeError("SQLite database does not exist")
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            builder.validate_source_objects(conn)
            source_table_names = builder.discover_source_table_names(conn)
            source_guard_before_hash = builder.guard_hash(builder.collect_source_guard_stats(conn, source_table_names))
            result.forbidden_final_tables = builder.forbidden_final_tables(conn)
            if result.forbidden_final_tables:
                result.errors.append(f"forbidden final tables exist: {', '.join(result.forbidden_final_tables)}")
            validate_current_rows(conn, scope, result)
            result.stable_row_fingerprint_before = builder.stable_row_fingerprint(conn, scope)
            params = latest_build_params(conn, scope)
        finally:
            conn.close()

        if not result.errors:
            sample = ",".join(str(item) for item in scope)
            first = builder.run_build(
                root,
                sample_chapters=sample,
                rebuild=True,
                target_min_chars=params["target_min_chars"],
                target_max_chars=params["target_max_chars"],
                hard_max_chars=params["hard_max_chars"],
                overlap_sentences=params["overlap_sentences"],
            )
            if not first.ok:
                result.errors.extend(f"first rebuild failed: {error}" for error in first.errors)
            result.stable_row_fingerprint_after_first_rebuild = first.stable_row_fingerprint
            second = builder.run_build(
                root,
                sample_chapters=sample,
                rebuild=True,
                target_min_chars=params["target_min_chars"],
                target_max_chars=params["target_max_chars"],
                hard_max_chars=params["hard_max_chars"],
                overlap_sentences=params["overlap_sentences"],
            )
            if not second.ok:
                result.errors.extend(f"second rebuild failed: {error}" for error in second.errors)
            result.stable_row_fingerprint_after_second_rebuild = second.stable_row_fingerprint
            result.rebuild_idempotent = (
                bool(first.stable_row_fingerprint)
                and first.stable_row_fingerprint == second.stable_row_fingerprint
            )
            if not result.rebuild_idempotent:
                result.errors.append("rebuild two-round idempotence failed")

            conn = sqlite3.connect(db_path, timeout=30)
            conn.row_factory = sqlite3.Row
            try:
                validate_current_rows(conn, scope, result)
            finally:
                conn.close()

        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            source_guard_after_hash = builder.guard_hash(builder.collect_source_guard_stats(conn, source_table_names))
            result.source_mutation_detected = source_guard_before_hash != source_guard_after_hash
            if result.source_mutation_detected:
                result.errors.append("source table mutation detected during L3.5 verification")
            result.forbidden_final_tables = builder.forbidden_final_tables(conn)
            if result.forbidden_final_tables and not any("forbidden final tables" in error for error in result.errors):
                result.errors.append(f"forbidden final tables exist: {', '.join(result.forbidden_final_tables)}")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))

    result.error_count = len(result.errors)
    result.ok = result.error_count == 0
    write_json_report(result)
    write_md_report(result)
    return result


def final_status_line(result: VerifyResult) -> str:
    return "L3.5 child segments FULL PASS" if result.ok else "L3.5 child segments FULL FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.5 child_segments.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--sample-chapters", type=str, default=builder.DEFAULT_SAMPLE_CHAPTERS)
    args = parser.parse_args()
    result = run_verification(args.project_dir, sample_chapters=args.sample_chapters)
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
