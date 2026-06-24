from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_chapter_title_indexer as l3_entry
from scripts import l3_scene_block_builder as builder


REPORT_RELATIVE_PATH = Path("outputs") / "l3_scene_blocks_verify_report.md"
FULL_REPORT_RELATIVE_PATH = Path("outputs") / "l3_scene_blocks_full_verify_report.md"
REQUIRED_BLOCK_COLUMNS = {
    "scene_id",
    "scene_key",
    "chapter_id",
    "version_id",
    "chapter_num",
    "scene_index_in_chapter",
    "start_para_id",
    "end_para_id",
    "start_para_index",
    "end_para_index",
    "start_sentence_id",
    "end_sentence_id",
    "start_offset",
    "end_offset",
    "length",
    "scene_kind",
    "split_reason",
    "summary_short",
    "source_hash",
    "created_at",
}
REQUIRED_STATUS_COLUMNS = {
    "chapter_id",
    "version_id",
    "chapter_num",
    "status",
    "scene_count",
    "paragraph_count",
    "sentence_count",
    "created_at",
}


@dataclass
class VerifyResult:
    ok: bool
    project_dir: Path
    db_path: Path
    report_path: Path
    scope: list[int]
    all_chapters: bool = False
    chapter_count: int = 0
    block_count: int = 0
    status_count: int = 0
    guard_unchanged: bool = False
    guard_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def ensure_schema(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    if not l3_entry.object_exists(conn, "l3_scene_blocks", "table"):
        errors.append("l3_scene_blocks table is missing")
    if not l3_entry.object_exists(conn, "l3_scene_block_status", "table"):
        errors.append("l3_scene_block_status table is missing")
    if errors:
        return errors
    block_missing = REQUIRED_BLOCK_COLUMNS - l3_entry.table_columns(conn, "l3_scene_blocks")
    status_missing = REQUIRED_STATUS_COLUMNS - l3_entry.table_columns(conn, "l3_scene_block_status")
    if block_missing:
        errors.append(f"l3_scene_blocks schema missing columns: {', '.join(sorted(block_missing))}")
    if status_missing:
        errors.append(f"l3_scene_block_status schema missing columns: {', '.join(sorted(status_missing))}")
    return errors


def fetch_authority(conn: sqlite3.Connection, chapter_num: int) -> tuple[sqlite3.Row | None, list[sqlite3.Row], list[sqlite3.Row]]:
    chapter = conn.execute(
        """
        SELECT chapter_id, latest_version_id AS version_id, chapter_num, content_full_text, content_length
        FROM v_current_chapters
        WHERE chapter_num = ?
        """,
        (chapter_num,),
    ).fetchone()
    paragraphs = builder.fetch_paragraphs(conn, chapter_num)
    sentences = builder.fetch_sentences(conn, chapter_num)
    return chapter, paragraphs, sentences


def validate_chapter(conn: sqlite3.Connection, chapter_num: int, result: VerifyResult) -> None:
    chapter, paragraphs, sentences = fetch_authority(conn, chapter_num)
    if chapter is None:
        result.errors.append(f"L1 authority missing chapter {chapter_num}")
        return
    if not paragraphs:
        result.errors.append(f"L2 paragraph authority missing chapter {chapter_num}")
        return
    try:
        builder.validate_scene_key_parts(str(chapter["chapter_id"]), str(chapter["version_id"]))
    except ValueError as exc:
        result.errors.append(str(exc))
        return

    blocks = conn.execute(
        """
        SELECT *
        FROM l3_scene_blocks
        WHERE chapter_id = ? AND version_id = ? AND chapter_num = ?
        ORDER BY scene_index_in_chapter ASC
        """,
        (chapter["chapter_id"], chapter["version_id"], chapter_num),
    ).fetchall()
    status = conn.execute(
        """
        SELECT *
        FROM l3_scene_block_status
        WHERE chapter_id = ? AND version_id = ? AND chapter_num = ?
        """,
        (chapter["chapter_id"], chapter["version_id"], chapter_num),
    ).fetchone()
    if not blocks:
        result.errors.append(f"L3 scene blocks missing for chapter {chapter_num}")
        return
    if status is None:
        result.errors.append(f"L3 scene block status missing for chapter {chapter_num}")
        return
    if status["status"] != "indexed":
        result.errors.append(f"invalid status for chapter {chapter_num}: {status['status']}")
    if int(status["scene_count"]) != len(blocks):
        result.errors.append(f"status scene_count mismatch for chapter {chapter_num}")
    if int(status["paragraph_count"]) != len(paragraphs):
        result.errors.append(f"status paragraph_count mismatch for chapter {chapter_num}")
    if int(status["sentence_count"]) != len(sentences):
        result.errors.append(f"status sentence_count mismatch for chapter {chapter_num}")

    para_by_index = {int(row["para_index"]): row for row in paragraphs}
    expected_para_ids = [str(row["para_id"]) for row in paragraphs]
    covered_para_ids: list[str] = []
    previous_end = -1
    seen_scene_keys: set[str] = set()
    content = str(chapter["content_full_text"])
    content_length = int(chapter["content_length"])

    for expected_index, block in enumerate(blocks, start=1):
        block_key = str(block["scene_key"])
        if block_key in seen_scene_keys:
            result.errors.append(f"duplicate scene_key in chapter {chapter_num}: {block_key}")
        seen_scene_keys.add(block_key)
        expected_key = builder.scene_key(str(chapter["chapter_id"]), str(chapter["version_id"]), expected_index)
        if block_key != expected_key:
            result.errors.append(f"scene_key mismatch for chapter {chapter_num} index {expected_index}")
        if int(block["scene_index_in_chapter"]) != expected_index:
            result.errors.append(f"scene_index gap for chapter {chapter_num}")
        if block["scene_kind"] not in builder.SCENE_KINDS:
            result.errors.append(f"invalid scene_kind in {block_key}")
        if block["split_reason"] not in builder.SPLIT_REASONS:
            result.errors.append(f"invalid split_reason in {block_key}")
        if block["chapter_id"] != chapter["chapter_id"] or block["version_id"] != chapter["version_id"]:
            result.errors.append(f"cross-chapter identity mismatch in {block_key}")
        start_offset = int(block["start_offset"])
        end_offset = int(block["end_offset"])
        if start_offset < 0 or end_offset > content_length or end_offset <= start_offset:
            result.errors.append(f"invalid offsets in {block_key}")
        if start_offset < previous_end:
            result.errors.append(f"overlapping offsets in chapter {chapter_num}")
        previous_end = end_offset
        if int(block["length"]) != end_offset - start_offset:
            result.errors.append(f"length mismatch in {block_key}")
        if builder.sha256_text(content[start_offset:end_offset]) != block["source_hash"]:
            result.errors.append(f"source_hash mismatch in {block_key}")

        start_para_index = int(block["start_para_index"])
        end_para_index = int(block["end_para_index"])
        if start_para_index > end_para_index:
            result.errors.append(f"invalid paragraph index range in {block_key}")
            continue
        block_paragraphs = [para_by_index.get(index) for index in range(start_para_index, end_para_index + 1)]
        if any(row is None for row in block_paragraphs):
            result.errors.append(f"L2 paragraph range missing in {block_key}")
            continue
        typed_paragraphs = [row for row in block_paragraphs if row is not None]
        if str(typed_paragraphs[0]["para_id"]) != block["start_para_id"]:
            result.errors.append(f"start_para_id mismatch in {block_key}")
        if str(typed_paragraphs[-1]["para_id"]) != block["end_para_id"]:
            result.errors.append(f"end_para_id mismatch in {block_key}")
        if int(typed_paragraphs[0]["start_offset"]) != start_offset:
            result.errors.append(f"start_offset does not align with L2 paragraph in {block_key}")
        if int(typed_paragraphs[-1]["end_offset"]) != end_offset:
            result.errors.append(f"end_offset does not align with L2 paragraph in {block_key}")
        covered_para_ids.extend(str(row["para_id"]) for row in typed_paragraphs)

    if covered_para_ids != expected_para_ids:
        result.errors.append(f"paragraph coverage mismatch against L2 authority for chapter {chapter_num}")


def build_report(result: VerifyResult) -> str:
    final_line = final_status_line(result)
    lines = [
        "# L3 scene_blocks verify report",
        "",
        f"- verify_time: {datetime.now().isoformat(timespec='seconds')}",
        f"- database: {result.db_path}",
        f"- scope: {','.join(str(item) for item in result.scope)}",
        f"- verified_chapters: {result.chapter_count}",
        f"- block_count: {result.block_count}",
        f"- status_count: {result.status_count}",
        f"- l1_l2_authority_reloaded: YES",
        f"- l1_l2_guard_unchanged: {result.guard_unchanged}",
        f"- full_1922_verify: {'YES' if result.all_chapters else 'NO'}",
        f"- final: {'PASS' if result.ok else 'FAIL'}",
        "",
        "## Guard notes",
        "",
        *(result.guard_notes or ["- none"]),
        "",
        "## Errors",
        "",
        *(result.errors or ["- none"]),
        "",
        final_line,
        "",
    ]
    return "\n".join(lines)


def final_status_line(result: VerifyResult) -> str:
    if result.all_chapters:
        return "L3 scene_blocks FULL PASS" if result.ok else "L3 scene_blocks FULL FAIL"
    return "L3 SCENE BLOCKS VERIFY PASS" if result.ok else "L3 SCENE BLOCKS VERIFY FAIL"


def write_report(result: VerifyResult) -> None:
    result.report_path.parent.mkdir(parents=True, exist_ok=True)
    result.report_path.write_text(build_report(result), encoding="utf-8")


def run_verification(
    project_dir: Path | str,
    *,
    sample_chapters: str | None = None,
    chapter_num: int | None = None,
    all_chapters: bool = False,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    builder.ensure_dirs(root)
    db_path = root / builder.DB_RELATIVE_PATH
    scope: list[int] = []
    report_path = root / (FULL_REPORT_RELATIVE_PATH if all_chapters else REPORT_RELATIVE_PATH)
    result = VerifyResult(False, root, db_path, report_path, scope, all_chapters)
    try:
        scope = builder.parse_scope(sample_chapters, chapter_num, all_chapters=all_chapters)
        result.scope = scope
        if not db_path.exists():
            raise RuntimeError("SQLite database does not exist")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            builder.validate_source_views(conn)
            before_guard, before_notes = l3_entry.collect_source_guard_stats(conn)
            result.guard_notes.extend(before_notes)
            schema_errors = ensure_schema(conn)
            if schema_errors:
                result.errors.extend(schema_errors)
                raise RuntimeError("L3 scene block schema validation failed")
            placeholders = builder.scoped_placeholders(scope)
            params = tuple(scope)
            result.block_count = int(
                conn.execute(f"SELECT COUNT(*) FROM l3_scene_blocks WHERE chapter_num IN ({placeholders})", params).fetchone()[0]
            )
            result.status_count = int(
                conn.execute(f"SELECT COUNT(*) FROM l3_scene_block_status WHERE chapter_num IN ({placeholders})", params).fetchone()[0]
            )
            for item in scope:
                validate_chapter(conn, item, result)
            result.chapter_count = len(scope)
            after_guard, after_notes = l3_entry.collect_source_guard_stats(conn)
            result.guard_notes.extend(after_notes)
            result.guard_unchanged = before_guard == after_guard
            if not result.guard_unchanged:
                result.errors.append("L1/L2 guard changed during L3 scene block verification")
            result.ok = not result.errors
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        result.ok = False
    write_report(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3 scene_blocks.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--sample-chapters", type=str, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    parser.add_argument("--all", dest="all_chapters", action="store_true")
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        sample_chapters=args.sample_chapters,
        chapter_num=args.chapter_num,
        all_chapters=args.all_chapters,
    )
    print(f"L3 scene blocks verify report: {result.report_path}")
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
