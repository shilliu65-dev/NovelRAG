from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_chapter_title_indexer as l3_entry


DB_RELATIVE_PATH = Path("index") / "novel_story_bible.db"
REPORT_RELATIVE_PATH = Path("outputs") / "l3_scene_blocks_report.md"
FULL_REPORT_RELATIVE_PATH = Path("outputs") / "l3_scene_blocks_full_report.md"
ALLOWED_SAMPLE_CHAPTERS = {1, 2, 1697}
FULL_SCOPE = list(range(1, 1923))
SCENE_KINDS = {"normal", "separator", "author_note", "promo", "noise"}
SPECIAL_PARA_KINDS = {"separator", "author_note", "promo", "noise"}
SPLIT_REASONS = {
    "target_max_reached",
    "chapter_end",
    "single_long_paragraph",
    "strong_separator_trigger",
    "strong_separator_block",
}


@dataclass
class SceneBlock:
    scene_key: str
    chapter_id: str
    version_id: str
    chapter_num: int
    scene_index_in_chapter: int
    start_para_id: str
    end_para_id: str
    start_para_index: int
    end_para_index: int
    start_sentence_id: str | None
    end_sentence_id: str | None
    start_offset: int
    end_offset: int
    length: int
    scene_kind: str
    split_reason: str
    summary_short: str | None
    source_hash: str


@dataclass
class ChapterBuildStats:
    chapter_num: int
    chapter_id: str
    version_id: str
    paragraph_count: int
    sentence_count: int
    scene_count: int


@dataclass
class BuildResult:
    ok: bool
    project_dir: Path
    db_path: Path
    report_path: Path
    scope: list[int]
    rebuild: bool
    dry_run: bool
    all_chapters: bool
    target_min_chars: int
    target_max_chars: int
    chapters: list[ChapterBuildStats] = field(default_factory=list)
    scene_kind_counts: dict[str, int] = field(default_factory=dict)
    split_reason_counts: dict[str, int] = field(default_factory=dict)
    length_min: int | None = None
    length_max: int | None = None
    length_total: int = 0
    guard_unchanged: bool = False
    guard_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def ensure_dirs(project_dir: Path) -> None:
    for name in ("docs", "outputs", "index"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)


def parse_scope(sample_chapters: str | None, chapter_num: int | None, *, all_chapters: bool = False) -> list[int]:
    modes = sum(1 for value in (bool(sample_chapters), chapter_num is not None, all_chapters) if value)
    if modes > 1:
        raise ValueError("--sample-chapters, --chapter-num, and --all are mutually exclusive")
    if modes == 0:
        raise ValueError("explicit scope is required; use --sample-chapters 1,2,1697, --chapter-num, or --all")
    if all_chapters:
        return list(FULL_SCOPE)
    if chapter_num is not None:
        if chapter_num not in ALLOWED_SAMPLE_CHAPTERS:
            raise ValueError("--chapter-num only allows 1, 2, or 1697 in this sample build")
        return [chapter_num]

    assert sample_chapters is not None
    try:
        values = [int(item.strip()) for item in sample_chapters.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("--sample-chapters must be a comma-separated integer list") from exc
    if not values:
        raise ValueError("--sample-chapters cannot be empty")
    unique_sorted = sorted(set(values))
    if unique_sorted != values:
        raise ValueError("--sample-chapters must be unique and sorted ascending")
    if not set(values).issubset(ALLOWED_SAMPLE_CHAPTERS):
        raise ValueError("--sample-chapters only allows chapters 1, 2, and 1697")
    return values


def validate_scene_key_parts(chapter_id: str, version_id: str) -> None:
    if ":" in chapter_id or ":" in version_id:
        raise ValueError("chapter_id/version_id cannot contain ':' because scene_key uses ':' separators")


def scene_key(chapter_id: str, version_id: str, scene_index: int) -> str:
    validate_scene_key_parts(chapter_id, version_id)
    return f"{chapter_id}:{version_id}:scene:{scene_index}"


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l3_scene_blocks (
            scene_id INTEGER PRIMARY KEY AUTOINCREMENT,
            scene_key TEXT NOT NULL UNIQUE,
            chapter_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            scene_index_in_chapter INTEGER NOT NULL,
            start_para_id TEXT NOT NULL,
            end_para_id TEXT NOT NULL,
            start_para_index INTEGER NOT NULL,
            end_para_index INTEGER NOT NULL,
            start_sentence_id TEXT,
            end_sentence_id TEXT,
            start_offset INTEGER NOT NULL,
            end_offset INTEGER NOT NULL,
            length INTEGER NOT NULL,
            scene_kind TEXT NOT NULL CHECK(scene_kind IN ('normal', 'separator', 'author_note', 'promo', 'noise')),
            split_reason TEXT NOT NULL CHECK(split_reason IN (
                'target_max_reached',
                'chapter_end',
                'single_long_paragraph',
                'strong_separator_trigger',
                'strong_separator_block'
            )),
            summary_short TEXT,
            source_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chapter_id, version_id, scene_index_in_chapter)
        );

        CREATE TABLE IF NOT EXISTS l3_scene_block_status (
            chapter_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            status TEXT NOT NULL CHECK(status = 'indexed'),
            scene_count INTEGER NOT NULL,
            paragraph_count INTEGER NOT NULL,
            sentence_count INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chapter_id, version_id)
        );

        CREATE INDEX IF NOT EXISTS idx_l3_scene_blocks_chapter
            ON l3_scene_blocks(chapter_num, scene_index_in_chapter);

        CREATE INDEX IF NOT EXISTS idx_l3_scene_blocks_chapter_version
            ON l3_scene_blocks(chapter_id, version_id);

        CREATE INDEX IF NOT EXISTS idx_l3_scene_blocks_key
            ON l3_scene_blocks(scene_key);
        """
    )


def scoped_placeholders(scope: list[int]) -> str:
    return ", ".join("?" for _ in scope)


def validate_source_views(conn: sqlite3.Connection) -> None:
    for name in ("v_current_chapters", "v_l2_current_paragraphs", "v_l2_current_sentences"):
        if not l3_entry.object_exists(conn, name, "view"):
            raise RuntimeError(f"required L1/L2 view missing: {name}")


def fetch_current_chapters(conn: sqlite3.Connection, scope: list[int]) -> list[sqlite3.Row]:
    placeholders = scoped_placeholders(scope)
    rows = conn.execute(
        f"""
        SELECT chapter_id, latest_version_id AS version_id, chapter_num, content_full_text, content_length
        FROM v_current_chapters
        WHERE chapter_num IN ({placeholders})
        ORDER BY chapter_num ASC
        """,
        tuple(scope),
    ).fetchall()
    if [int(row["chapter_num"]) for row in rows] != scope:
        raise RuntimeError(f"current chapters missing for scope {scope}")
    for row in rows:
        validate_scene_key_parts(str(row["chapter_id"]), str(row["version_id"]))
    return rows


def fetch_paragraphs(conn: sqlite3.Connection, chapter_num: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT *
            FROM v_l2_current_paragraphs
            WHERE chapter_num = ?
            ORDER BY para_index ASC
            """,
            (chapter_num,),
        )
    )


def fetch_sentences(conn: sqlite3.Connection, chapter_num: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT *
            FROM v_l2_current_sentences
            WHERE chapter_num = ?
            ORDER BY start_offset ASC, end_offset ASC, sentence_index ASC
            """,
            (chapter_num,),
        )
    )


def sentence_bounds(sentences: list[sqlite3.Row], start_offset: int, end_offset: int) -> tuple[str | None, str | None]:
    matching = [row for row in sentences if int(row["start_offset"]) >= start_offset and int(row["end_offset"]) <= end_offset]
    if not matching:
        return None, None
    return str(matching[0]["sentence_id"]), str(matching[-1]["sentence_id"])


def make_block(
    chapter: sqlite3.Row,
    paragraphs: list[sqlite3.Row],
    sentences: list[sqlite3.Row],
    scene_index: int,
    scene_kind_value: str,
    split_reason_value: str,
) -> SceneBlock:
    if scene_kind_value not in SCENE_KINDS:
        raise ValueError(f"invalid scene_kind: {scene_kind_value}")
    if split_reason_value not in SPLIT_REASONS:
        raise ValueError(f"invalid split_reason: {split_reason_value}")
    start_para = paragraphs[0]
    end_para = paragraphs[-1]
    start_offset = int(start_para["start_offset"])
    end_offset = int(end_para["end_offset"])
    if end_offset <= start_offset:
        raise ValueError("invalid block offset range")
    content = str(chapter["content_full_text"])
    start_sentence_id, end_sentence_id = sentence_bounds(sentences, start_offset, end_offset)
    chapter_id = str(chapter["chapter_id"])
    version_id = str(chapter["version_id"])
    return SceneBlock(
        scene_key=scene_key(chapter_id, version_id, scene_index),
        chapter_id=chapter_id,
        version_id=version_id,
        chapter_num=int(chapter["chapter_num"]),
        scene_index_in_chapter=scene_index,
        start_para_id=str(start_para["para_id"]),
        end_para_id=str(end_para["para_id"]),
        start_para_index=int(start_para["para_index"]),
        end_para_index=int(end_para["para_index"]),
        start_sentence_id=start_sentence_id,
        end_sentence_id=end_sentence_id,
        start_offset=start_offset,
        end_offset=end_offset,
        length=end_offset - start_offset,
        scene_kind=scene_kind_value,
        split_reason=split_reason_value,
        summary_short=None,
        source_hash=sha256_text(content[start_offset:end_offset]),
    )


def build_chapter_blocks(
    chapter: sqlite3.Row,
    paragraphs: list[sqlite3.Row],
    sentences: list[sqlite3.Row],
    *,
    target_max_chars: int,
) -> list[SceneBlock]:
    if not paragraphs:
        raise RuntimeError(f"chapter {chapter['chapter_num']} has no L2 paragraphs")
    blocks: list[SceneBlock] = []
    pending: list[sqlite3.Row] = []

    def pending_length() -> int:
        return int(pending[-1]["end_offset"]) - int(pending[0]["start_offset"]) if pending else 0

    def flush(reason: str) -> None:
        nonlocal pending
        if not pending:
            return
        blocks.append(make_block(chapter, pending, sentences, len(blocks) + 1, "normal", reason))
        pending = []

    for para in paragraphs:
        para_kind = str(para["para_kind"] or "normal")
        para_length = int(para["end_offset"]) - int(para["start_offset"])
        if para_kind in SPECIAL_PARA_KINDS:
            flush("strong_separator_trigger")
            blocks.append(make_block(chapter, [para], sentences, len(blocks) + 1, para_kind, "strong_separator_block"))
            continue
        if para_length > target_max_chars:
            flush("target_max_reached")
            blocks.append(make_block(chapter, [para], sentences, len(blocks) + 1, "normal", "single_long_paragraph"))
            continue
        if pending and pending_length() + 1 + para_length > target_max_chars:
            flush("target_max_reached")
        pending.append(para)
    flush("chapter_end")
    return blocks


def validate_blocks_in_memory(chapter: sqlite3.Row, paragraphs: list[sqlite3.Row], blocks: list[SceneBlock]) -> None:
    if not blocks:
        raise RuntimeError(f"chapter {chapter['chapter_num']} produced no scene blocks")
    expected_para_ids = [str(row["para_id"]) for row in paragraphs]
    seen: list[str] = []
    previous_end = -1
    for expected_index, block in enumerate(blocks, start=1):
        if block.scene_index_in_chapter != expected_index:
            raise RuntimeError(f"scene index gap in chapter {chapter['chapter_num']}")
        if block.start_offset < 0 or block.end_offset > int(chapter["content_length"]) or block.end_offset <= block.start_offset:
            raise RuntimeError(f"invalid offsets in {block.scene_key}")
        if block.start_offset < previous_end:
            raise RuntimeError(f"overlapping blocks in chapter {chapter['chapter_num']}")
        previous_end = block.end_offset
        covered = [
            str(row["para_id"])
            for row in paragraphs
            if int(row["para_index"]) >= block.start_para_index and int(row["para_index"]) <= block.end_para_index
        ]
        seen.extend(covered)
    if seen != expected_para_ids:
        raise RuntimeError(f"paragraph coverage mismatch in chapter {chapter['chapter_num']}")


def insert_blocks(conn: sqlite3.Connection, blocks: list[SceneBlock]) -> None:
    conn.executemany(
        """
        INSERT INTO l3_scene_blocks (
            scene_key, chapter_id, version_id, chapter_num, scene_index_in_chapter,
            start_para_id, end_para_id, start_para_index, end_para_index,
            start_sentence_id, end_sentence_id, start_offset, end_offset, length,
            scene_kind, split_reason, summary_short, source_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                block.scene_key,
                block.chapter_id,
                block.version_id,
                block.chapter_num,
                block.scene_index_in_chapter,
                block.start_para_id,
                block.end_para_id,
                block.start_para_index,
                block.end_para_index,
                block.start_sentence_id,
                block.end_sentence_id,
                block.start_offset,
                block.end_offset,
                block.length,
                block.scene_kind,
                block.split_reason,
                block.summary_short,
                block.source_hash,
            )
            for block in blocks
        ],
    )


def write_status(
    conn: sqlite3.Connection,
    chapter: sqlite3.Row,
    scene_count: int,
    paragraph_count: int,
    sentence_count: int,
) -> None:
    conn.execute(
        """
        INSERT INTO l3_scene_block_status (
            chapter_id, version_id, chapter_num, status, scene_count, paragraph_count, sentence_count
        )
        VALUES (?, ?, ?, 'indexed', ?, ?, ?)
        """,
        (
            chapter["chapter_id"],
            chapter["version_id"],
            chapter["chapter_num"],
            scene_count,
            paragraph_count,
            sentence_count,
        ),
    )


def delete_scope(conn: sqlite3.Connection, scope: list[int]) -> None:
    placeholders = scoped_placeholders(scope)
    params = tuple(scope)
    conn.execute(f"DELETE FROM l3_scene_blocks WHERE chapter_num IN ({placeholders})", params)
    conn.execute(f"DELETE FROM l3_scene_block_status WHERE chapter_num IN ({placeholders})", params)


def build_report(result: BuildResult) -> str:
    final_line = final_status_line(result)
    chapter_lines = [
        f"- chapter {item.chapter_num}: paragraphs={item.paragraph_count}, sentences={item.sentence_count}, scenes={item.scene_count}"
        for item in result.chapters
    ]
    lines = [
        "# L3 scene_blocks build report",
        "",
        f"- build_time: {datetime.now().isoformat(timespec='seconds')}",
        f"- database: {result.db_path}",
        f"- scope: {','.join(str(item) for item in result.scope)}",
        f"- rebuild: {result.rebuild}",
        f"- dry_run: {result.dry_run}",
        f"- all_chapters: {result.all_chapters}",
        f"- target_min_chars: {result.target_min_chars}",
        f"- target_max_chars: {result.target_max_chars}",
        f"- processed_chapters: {len(result.chapters)}",
        f"- length_min: {result.length_min if result.length_min is not None else 'none'}",
        f"- length_max: {result.length_max if result.length_max is not None else 'none'}",
        f"- length_total: {result.length_total}",
        f"- l1_l2_guard_unchanged: {result.guard_unchanged}",
        f"- raw_txt_read: NO",
        f"- llm_called: NO",
        f"- chroma_written: NO",
        f"- full_1922_run: {'YES' if result.all_chapters else 'NO'}",
        f"- final: {'PASS' if result.ok else 'FAIL'}",
        "",
        "## Chapters",
        "",
        *(chapter_lines or ["- none"]),
        "",
        "## scene_kind counts",
        "",
        *[f"- {key}: {value}" for key, value in sorted(result.scene_kind_counts.items())],
        "",
        "## split_reason counts",
        "",
        *[f"- {key}: {value}" for key, value in sorted(result.split_reason_counts.items())],
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


def final_status_line(result: BuildResult) -> str:
    if result.all_chapters and not result.dry_run:
        return "L3 scene_blocks FULL PASS" if result.ok else "L3 scene_blocks FULL FAIL"
    return "L3 SCENE BLOCKS BUILD PASS" if result.ok else "L3 SCENE BLOCKS BUILD FAIL"


def write_report(result: BuildResult) -> None:
    result.report_path.parent.mkdir(parents=True, exist_ok=True)
    result.report_path.write_text(build_report(result), encoding="utf-8")


def run_build(
    project_dir: Path | str,
    *,
    sample_chapters: str | None = None,
    chapter_num: int | None = None,
    all_chapters: bool = False,
    rebuild: bool = False,
    dry_run: bool = False,
    target_min_chars: int = 1500,
    target_max_chars: int = 3000,
) -> BuildResult:
    root = Path(project_dir).resolve()
    ensure_dirs(root)
    db_path = root / DB_RELATIVE_PATH
    scope: list[int] = []
    report_path = root / (FULL_REPORT_RELATIVE_PATH if all_chapters else REPORT_RELATIVE_PATH)
    result = BuildResult(False, root, db_path, report_path, scope, rebuild, dry_run, all_chapters, target_min_chars, target_max_chars)
    try:
        scope = parse_scope(sample_chapters, chapter_num, all_chapters=all_chapters)
        result.scope = scope
        if target_min_chars <= 0 or target_max_chars <= 0 or target_min_chars > target_max_chars:
            raise ValueError("target chars must be positive and min <= max")
        if not db_path.exists():
            raise RuntimeError("SQLite database does not exist")
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            validate_source_views(conn)
            before_guard, before_notes = l3_entry.collect_source_guard_stats(conn)
            result.guard_notes.extend(before_notes)
            chapters = fetch_current_chapters(conn, scope)

            planned: list[tuple[sqlite3.Row, list[sqlite3.Row], list[sqlite3.Row], list[SceneBlock]]] = []
            for chapter in chapters:
                paragraphs = fetch_paragraphs(conn, int(chapter["chapter_num"]))
                sentences = fetch_sentences(conn, int(chapter["chapter_num"]))
                blocks = build_chapter_blocks(chapter, paragraphs, sentences, target_max_chars=target_max_chars)
                validate_blocks_in_memory(chapter, paragraphs, blocks)
                planned.append((chapter, paragraphs, sentences, blocks))

            if dry_run:
                for chapter, paragraphs, sentences, blocks in planned:
                    result.chapters.append(
                        ChapterBuildStats(
                            chapter_num=int(chapter["chapter_num"]),
                            chapter_id=str(chapter["chapter_id"]),
                            version_id=str(chapter["version_id"]),
                            paragraph_count=len(paragraphs),
                            sentence_count=len(sentences),
                            scene_count=len(blocks),
                        )
                    )
                    for block in blocks:
                        result.scene_kind_counts[block.scene_kind] = result.scene_kind_counts.get(block.scene_kind, 0) + 1
                        result.split_reason_counts[block.split_reason] = result.split_reason_counts.get(block.split_reason, 0) + 1
                        result.length_total += block.length
                        result.length_min = block.length if result.length_min is None else min(result.length_min, block.length)
                        result.length_max = block.length if result.length_max is None else max(result.length_max, block.length)
            else:
                init_schema(conn)
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if rebuild:
                        delete_scope(conn, scope)
                    else:
                        placeholders = scoped_placeholders(scope)
                        existing = conn.execute(
                            f"SELECT COUNT(*) FROM l3_scene_blocks WHERE chapter_num IN ({placeholders})",
                            tuple(scope),
                        ).fetchone()[0]
                        if existing:
                            raise RuntimeError("L3 scene blocks already exist for scope; rerun with --rebuild")
                    for chapter, paragraphs, sentences, blocks in planned:
                        insert_blocks(conn, blocks)
                        write_status(conn, chapter, len(blocks), len(paragraphs), len(sentences))
                        result.chapters.append(
                            ChapterBuildStats(
                                chapter_num=int(chapter["chapter_num"]),
                                chapter_id=str(chapter["chapter_id"]),
                                version_id=str(chapter["version_id"]),
                                paragraph_count=len(paragraphs),
                                sentence_count=len(sentences),
                                scene_count=len(blocks),
                            )
                        )
                        for block in blocks:
                            result.scene_kind_counts[block.scene_kind] = result.scene_kind_counts.get(block.scene_kind, 0) + 1
                            result.split_reason_counts[block.split_reason] = result.split_reason_counts.get(block.split_reason, 0) + 1
                            result.length_total += block.length
                            result.length_min = block.length if result.length_min is None else min(result.length_min, block.length)
                            result.length_max = block.length if result.length_max is None else max(result.length_max, block.length)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            after_guard, after_notes = l3_entry.collect_source_guard_stats(conn)
            result.guard_notes.extend(after_notes)
            result.guard_unchanged = before_guard == after_guard
            if not result.guard_unchanged:
                result.errors.append("L1/L2 guard changed during L3 scene block build")
            result.ok = not result.errors
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        result.errors.append(str(exc))
        result.ok = False
    write_report(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L3 scene_blocks.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--sample-chapters", type=str, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    parser.add_argument("--all", dest="all_chapters", action="store_true")
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-min-chars", type=int, default=1500)
    parser.add_argument("--target-max-chars", type=int, default=3000)
    args = parser.parse_args()
    result = run_build(
        args.project_dir,
        sample_chapters=args.sample_chapters,
        chapter_num=args.chapter_num,
        all_chapters=args.all_chapters,
        rebuild=args.rebuild,
        dry_run=args.dry_run,
        target_min_chars=args.target_min_chars,
        target_max_chars=args.target_max_chars,
    )
    print(f"L3 scene blocks report: {result.report_path}")
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
