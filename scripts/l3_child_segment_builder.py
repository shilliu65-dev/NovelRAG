from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DB_RELATIVE_PATH = Path("index") / "novel_story_bible.db"
ALLOWED_SAMPLE_CHAPTERS = {1, 2, 1697}
DEFAULT_SAMPLE_CHAPTERS = "1,2,1697"
DEFAULT_TARGET_MIN_CHARS = 400
DEFAULT_TARGET_MAX_CHARS = 900
DEFAULT_HARD_MAX_CHARS = 1200
DEFAULT_OVERLAP_SENTENCES = 1
OWN_TABLES = {
    "l3_child_segment",
    "l3_child_segment_sentence_link",
    "l3_child_segment_build_run",
    "l3_child_segment_warning",
}
FORBIDDEN_FINAL_TABLES = {
    "final_event",
    "final_timeline",
    "final_relationship_graph",
    "final_state_machine",
}
SEGMENT_COLUMNS = [
    "child_segment_id",
    "scene_id",
    "chapter_id",
    "version_id",
    "chapter_num",
    "segment_index_in_scene",
    "segment_kind",
    "segment_text",
    "char_len",
    "sentence_count",
    "paragraph_count",
    "start_para_id",
    "end_para_id",
    "start_sentence_id",
    "end_sentence_id",
    "start_char_offset",
    "end_char_offset",
    "has_overlap",
    "overlap_from_child_segment_id",
    "boundary_status",
    "segment_text_hash",
    "source_fingerprint",
    "build_run_id",
    "created_at",
]
LINK_COLUMNS = [
    "link_id",
    "child_segment_id",
    "scene_id",
    "chapter_id",
    "version_id",
    "chapter_num",
    "position_in_segment",
    "sentence_id",
    "para_id",
    "sentence_start_offset",
    "sentence_end_offset",
    "sentence_text_hash",
    "is_overlap_sentence",
    "build_run_id",
    "created_at",
]
WARNING_COLUMNS = [
    "warning_id",
    "build_run_id",
    "warning_type",
    "chapter_id",
    "version_id",
    "chapter_num",
    "scene_id",
    "child_segment_id",
    "sentence_id",
    "message",
    "created_at",
]


@dataclass
class BuildResult:
    ok: bool
    project_dir: Path
    db_path: Path
    scope: list[int]
    rebuild: bool
    build_run_id: str = ""
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
    warning_count: int = 0
    error_count: int = 0
    stable_row_fingerprint: str = ""
    forbidden_final_tables: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def quote_ident(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def ensure_dirs(project_dir: Path) -> None:
    for name in ("docs", "outputs", "index"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)


def row_keys(row: sqlite3.Row) -> set[str]:
    return set(row.keys())


def row_value(row: sqlite3.Row, key: str, default: Any = None) -> Any:
    return row[key] if key in row_keys(row) else default


def object_exists(conn: sqlite3.Connection, name: str, kind: str | None = None) -> bool:
    if kind is None:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
            (kind, name),
        ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({quote_ident(name)})")}


def table_column_defs(conn: sqlite3.Connection, name: str) -> list[str]:
    return [
        f"{row['name']}:{row['type']}:{row['notnull']}:{row['pk']}"
        for row in conn.execute(f"PRAGMA table_info({quote_ident(name)})")
    ]


def scoped_placeholders(scope: list[int]) -> str:
    return ", ".join("?" for _ in scope)


def parse_scope(sample_chapters: str | None, chapter_num: int | None) -> list[int]:
    modes = sum(1 for value in (bool(sample_chapters), chapter_num is not None) if value)
    if modes > 1:
        raise ValueError("--sample-chapters and --chapter-num are mutually exclusive")
    if modes == 0:
        raise ValueError("explicit scope is required; use --sample-chapters 1,2,1697 or --chapter-num")
    if chapter_num is not None:
        if chapter_num not in ALLOWED_SAMPLE_CHAPTERS:
            raise ValueError("--chapter-num only allows 1, 2, or 1697 in this L3.5 sample build")
        return [chapter_num]
    assert sample_chapters is not None
    try:
        values = [int(item.strip()) for item in sample_chapters.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("--sample-chapters must be a comma-separated integer list") from exc
    if not values:
        raise ValueError("--sample-chapters cannot be empty")
    if values != sorted(set(values)):
        raise ValueError("--sample-chapters must be unique and sorted ascending")
    if not set(values).issubset(ALLOWED_SAMPLE_CHAPTERS):
        raise ValueError("--sample-chapters only allows chapters 1, 2, and 1697")
    return values


def default_scope() -> list[int]:
    return parse_scope(DEFAULT_SAMPLE_CHAPTERS, None)


def validate_source_objects(conn: sqlite3.Connection) -> None:
    for name in ("l3_scene_blocks", "v_l2_current_paragraphs", "v_l2_current_sentences", "v_current_chapters"):
        if not object_exists(conn, name):
            raise RuntimeError(f"required source object missing: {name}")
    scene_required = {"chapter_id", "version_id", "chapter_num", "start_offset", "end_offset"}
    sentence_required = {
        "sentence_id",
        "para_id",
        "chapter_id",
        "version_id",
        "chapter_num",
        "start_offset",
        "end_offset",
        "sentence_hash",
    }
    current_required = {"chapter_id", "chapter_num", "content_full_text", "content_length"}
    scene_missing = scene_required - table_columns(conn, "l3_scene_blocks")
    sentence_missing = sentence_required - table_columns(conn, "v_l2_current_sentences")
    current_cols = table_columns(conn, "v_current_chapters")
    current_missing = current_required - current_cols
    if "latest_version_id" not in current_cols and "version_id" not in current_cols:
        current_missing.add("latest_version_id/version_id")
    if scene_missing:
        raise RuntimeError(f"l3_scene_blocks missing required columns: {', '.join(sorted(scene_missing))}")
    if sentence_missing:
        raise RuntimeError(f"v_l2_current_sentences missing required columns: {', '.join(sorted(sentence_missing))}")
    if current_missing:
        raise RuntimeError(f"v_current_chapters missing required columns: {', '.join(sorted(current_missing))}")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l3_child_segment (
            child_segment_id TEXT PRIMARY KEY,
            scene_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            segment_index_in_scene INTEGER NOT NULL,
            segment_kind TEXT NOT NULL,
            segment_text TEXT NOT NULL,
            char_len INTEGER NOT NULL,
            sentence_count INTEGER NOT NULL CHECK(sentence_count >= 1),
            paragraph_count INTEGER NOT NULL CHECK(paragraph_count >= 1),
            start_para_id TEXT NOT NULL,
            end_para_id TEXT NOT NULL,
            start_sentence_id TEXT NOT NULL,
            end_sentence_id TEXT NOT NULL,
            start_char_offset INTEGER NOT NULL,
            end_char_offset INTEGER NOT NULL,
            has_overlap INTEGER NOT NULL CHECK(has_overlap IN (0, 1)),
            overlap_from_child_segment_id TEXT,
            boundary_status TEXT NOT NULL,
            segment_text_hash TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            build_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(scene_id, chapter_id, version_id, segment_index_in_scene)
        );

        CREATE TABLE IF NOT EXISTS l3_child_segment_sentence_link (
            link_id TEXT PRIMARY KEY,
            child_segment_id TEXT NOT NULL,
            scene_id TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            position_in_segment INTEGER NOT NULL,
            sentence_id TEXT NOT NULL,
            para_id TEXT NOT NULL,
            sentence_start_offset INTEGER NOT NULL,
            sentence_end_offset INTEGER NOT NULL,
            sentence_text_hash TEXT NOT NULL,
            is_overlap_sentence INTEGER NOT NULL CHECK(is_overlap_sentence IN (0, 1)),
            build_run_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(child_segment_id, position_in_segment)
        );

        CREATE TABLE IF NOT EXISTS l3_child_segment_build_run (
            build_run_id TEXT PRIMARY KEY,
            run_scope TEXT NOT NULL,
            sample_chapters TEXT NOT NULL,
            target_min_chars INTEGER NOT NULL,
            target_max_chars INTEGER NOT NULL,
            hard_max_chars INTEGER NOT NULL,
            overlap_sentences INTEGER NOT NULL,
            chapter_count INTEGER NOT NULL,
            scene_block_count INTEGER NOT NULL,
            child_segment_count INTEGER NOT NULL,
            avg_segment_chars REAL NOT NULL,
            min_segment_chars INTEGER NOT NULL,
            max_segment_chars INTEGER NOT NULL,
            overlap_segment_count INTEGER NOT NULL,
            oversized_sentence_warning_count INTEGER NOT NULL,
            empty_segment_count INTEGER NOT NULL,
            boundary_warning_count INTEGER NOT NULL,
            source_guard_before_hash TEXT NOT NULL,
            source_guard_after_hash TEXT NOT NULL,
            source_table_names_json TEXT NOT NULL,
            source_mutation_detected INTEGER NOT NULL CHECK(source_mutation_detected IN (0, 1)),
            forbidden_final_table_count INTEGER NOT NULL,
            stable_row_fingerprint TEXT NOT NULL,
            error_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            note TEXT
        );

        CREATE TABLE IF NOT EXISTS l3_child_segment_warning (
            warning_id TEXT PRIMARY KEY,
            build_run_id TEXT NOT NULL,
            warning_type TEXT NOT NULL,
            chapter_id TEXT,
            version_id TEXT,
            chapter_num INTEGER,
            scene_id TEXT,
            child_segment_id TEXT,
            sentence_id TEXT,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_l3_child_segment_scope
            ON l3_child_segment(chapter_num, scene_id, segment_index_in_scene);

        CREATE INDEX IF NOT EXISTS idx_l3_child_segment_scene
            ON l3_child_segment(scene_id, chapter_id, version_id);

        CREATE INDEX IF NOT EXISTS idx_l3_child_segment_link_child
            ON l3_child_segment_sentence_link(child_segment_id, position_in_segment);

        CREATE INDEX IF NOT EXISTS idx_l3_child_segment_link_sentence
            ON l3_child_segment_sentence_link(sentence_id);

        CREATE INDEX IF NOT EXISTS idx_l3_child_segment_warning_scope
            ON l3_child_segment_warning(chapter_num, warning_type);
        """
    )


def discover_source_table_names(conn: sqlite3.Connection) -> list[str]:
    names = [
        str(row["name"])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    ]
    result: list[str] = []
    for name in names:
        if name in OWN_TABLES or name.startswith("sqlite_"):
            continue
        if name in FORBIDDEN_FINAL_TABLES:
            result.append(name)
            continue
        if name.startswith(("chapter_", "l2_", "l3_", "l4_", "l5_")):
            result.append(name)
    return result


def selected_guard_columns(columns: set[str]) -> list[str]:
    exact = {
        "chapter_id",
        "chapter_num",
        "version_id",
        "latest_version_id",
        "content_hash",
        "content_length",
        "is_current",
        "para_id",
        "sentence_id",
        "scene_id",
        "scene_key",
        "scene_index_in_chapter",
        "start_offset",
        "end_offset",
        "length",
        "source_hash",
        "status",
        "rowid",
    }
    selected = [
        col
        for col in sorted(columns)
        if col in exact
        or col.endswith("_id")
        or col.endswith("_hash")
        or col.endswith("_count")
        or col.endswith("_current")
        or "title" in col
    ]
    return selected[:24]


def collect_source_guard_stats(conn: sqlite3.Connection, table_names: list[str] | None = None) -> dict[str, Any]:
    names = table_names if table_names is not None else discover_source_table_names(conn)
    stats: dict[str, Any] = {}
    for name in names:
        if not object_exists(conn, name, "table"):
            stats[name] = {"missing": True}
            continue
        columns = table_columns(conn, name)
        schema_hash = sha256_text("|".join(table_column_defs(conn, name)))
        count = int(conn.execute(f"SELECT COUNT(*) FROM {quote_ident(name)}").fetchone()[0])
        try:
            max_rowid = conn.execute(f"SELECT MAX(rowid) FROM {quote_ident(name)}").fetchone()[0]
        except sqlite3.DatabaseError:
            max_rowid = None
        guard_cols = selected_guard_columns(columns)
        digest = hashlib.sha256()
        if guard_cols:
            quoted_cols = ", ".join(quote_ident(col) for col in guard_cols)
            order_col = "rowid" if max_rowid is not None else guard_cols[0]
            try:
                rows = conn.execute(
                    f"SELECT {quoted_cols} FROM {quote_ident(name)} ORDER BY {quote_ident(order_col)}"
                )
                for row in rows:
                    digest.update(
                        "|".join("" if value is None else str(value) for value in row).encode("utf-8")
                    )
                    digest.update(b"\n")
            except sqlite3.DatabaseError:
                digest.update(f"count={count}|max_rowid={max_rowid}".encode("utf-8"))
        else:
            digest.update(f"count={count}|max_rowid={max_rowid}".encode("utf-8"))
        stats[name] = {
            "missing": False,
            "count": count,
            "max_rowid": max_rowid,
            "schema_hash": schema_hash,
            "guard_columns": guard_cols,
            "metadata_digest": digest.hexdigest(),
        }
    return stats


def guard_hash(stats: dict[str, Any]) -> str:
    return sha256_json(stats)


def forbidden_final_tables(conn: sqlite3.Connection) -> list[str]:
    placeholders = ", ".join("?" for _ in FORBIDDEN_FINAL_TABLES)
    rows = conn.execute(
        f"""
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
          AND name IN ({placeholders})
        ORDER BY name
        """,
        tuple(sorted(FORBIDDEN_FINAL_TABLES)),
    ).fetchall()
    return [str(row["name"]) for row in rows]


def fetch_current_chapters(conn: sqlite3.Connection, scope: list[int]) -> dict[int, sqlite3.Row]:
    columns = table_columns(conn, "v_current_chapters")
    version_expr = "latest_version_id" if "latest_version_id" in columns else "version_id"
    content_hash_expr = "content_hash" if "content_hash" in columns else "'' AS content_hash"
    placeholders = scoped_placeholders(scope)
    rows = conn.execute(
        f"""
        SELECT
            chapter_id,
            {version_expr} AS version_id,
            chapter_num,
            content_full_text,
            content_length,
            {content_hash_expr}
        FROM v_current_chapters
        WHERE chapter_num IN ({placeholders})
        ORDER BY chapter_num ASC
        """,
        tuple(scope),
    ).fetchall()
    found = [int(row["chapter_num"]) for row in rows]
    if found != scope:
        raise RuntimeError(f"current chapters missing for scope {scope}; found {found}")
    return {int(row["chapter_num"]): row for row in rows}


def fetch_scene_blocks(conn: sqlite3.Connection, scope: list[int]) -> list[sqlite3.Row]:
    placeholders = scoped_placeholders(scope)
    order_col = "scene_index_in_chapter" if "scene_index_in_chapter" in table_columns(conn, "l3_scene_blocks") else "start_offset"
    return conn.execute(
        f"""
        SELECT *
        FROM l3_scene_blocks
        WHERE chapter_num IN ({placeholders})
        ORDER BY chapter_num ASC, {order_col} ASC, start_offset ASC
        """,
        tuple(scope),
    ).fetchall()


def fetch_sentences_for_scene(conn: sqlite3.Connection, scene: sqlite3.Row) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT *
        FROM v_l2_current_sentences
        WHERE chapter_num = ?
          AND chapter_id = ?
          AND version_id = ?
          AND start_offset >= ?
          AND end_offset <= ?
        ORDER BY start_offset ASC, end_offset ASC, sentence_index ASC
        """,
        (
            scene["chapter_num"],
            scene["chapter_id"],
            scene["version_id"],
            scene["start_offset"],
            scene["end_offset"],
        ),
    ).fetchall()


def scene_identity(scene: sqlite3.Row) -> str:
    value = row_value(scene, "scene_id", None)
    if value is not None:
        return str(value)
    return str(row_value(scene, "scene_key", f"{scene['chapter_id']}:{scene['start_offset']}:{scene['end_offset']}"))


def sentence_text(chapter_text: str, sentence: sqlite3.Row) -> str:
    explicit = row_value(sentence, "sentence_text", None)
    if explicit is not None:
        return str(explicit)
    return chapter_text[int(sentence["start_offset"]) : int(sentence["end_offset"])]


def sentence_hash(chapter_text: str, sentence: sqlite3.Row) -> str:
    explicit = row_value(sentence, "sentence_hash", None)
    if explicit:
        return str(explicit)
    return sha256_text(sentence_text(chapter_text, sentence))


def span_len(sentences: list[sqlite3.Row], start: int, end_inclusive: int) -> int:
    return int(sentences[end_inclusive]["end_offset"]) - int(sentences[start]["start_offset"])


def make_primary_ranges(
    sentences: list[sqlite3.Row],
    *,
    target_min_chars: int,
    target_max_chars: int,
    hard_max_chars: int,
) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < len(sentences):
        first_len = span_len(sentences, start, start)
        if first_len > hard_max_chars:
            ranges.append((start, start + 1))
            start += 1
            continue
        end = start + 1
        while end < len(sentences):
            current_len = span_len(sentences, start, end - 1)
            next_len = span_len(sentences, start, end)
            if current_len >= target_min_chars and next_len > target_max_chars:
                break
            if next_len > hard_max_chars:
                break
            end += 1
        ranges.append((start, end))
        start = end
    return ranges


def child_segment_id(chapter_num: int, scene_id: str, segment_index: int) -> str:
    scene_token = sha256_text(f"{chapter_num}|{scene_id}")[:12]
    return f"l3cs_{chapter_num:04d}_{scene_token}_{segment_index:04d}"


def source_fingerprint_for_segment(scene: sqlite3.Row, sentences: list[sqlite3.Row], chapter_text: str) -> str:
    payload = {
        "scene_id": scene_identity(scene),
        "scene_key": row_value(scene, "scene_key", ""),
        "chapter_id": scene["chapter_id"],
        "version_id": scene["version_id"],
        "chapter_num": int(scene["chapter_num"]),
        "scene_start_offset": int(scene["start_offset"]),
        "scene_end_offset": int(scene["end_offset"]),
        "scene_source_hash": row_value(scene, "source_hash", ""),
        "sentences": [
            {
                "sentence_id": sentence["sentence_id"],
                "para_id": sentence["para_id"],
                "start_offset": int(sentence["start_offset"]),
                "end_offset": int(sentence["end_offset"]),
                "sentence_hash": sentence_hash(chapter_text, sentence),
            }
            for sentence in sentences
        ],
    }
    return sha256_json(payload)


def warning_id(*parts: object) -> str:
    return "l3csw_" + sha256_text("|".join(str(part) for part in parts))[:24]


def build_records_for_scene(
    scene: sqlite3.Row,
    sentences: list[sqlite3.Row],
    chapter: sqlite3.Row,
    *,
    build_run_id: str,
    created_at: str,
    target_min_chars: int,
    target_max_chars: int,
    hard_max_chars: int,
    overlap_sentences: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    scene_id = scene_identity(scene)
    chapter_text = str(chapter["content_full_text"])
    if not sentences:
        warning = {
            "warning_id": warning_id(build_run_id, scene_id, "scene_without_sentences"),
            "build_run_id": build_run_id,
            "warning_type": "boundary_scene_without_sentences",
            "chapter_id": scene["chapter_id"],
            "version_id": scene["version_id"],
            "chapter_num": int(scene["chapter_num"]),
            "scene_id": scene_id,
            "child_segment_id": None,
            "sentence_id": None,
            "message": "scene_block has no L2 sentence rows inside its boundaries",
            "created_at": created_at,
        }
        return [], [], [warning]

    ranges = make_primary_ranges(
        sentences,
        target_min_chars=target_min_chars,
        target_max_chars=target_max_chars,
        hard_max_chars=hard_max_chars,
    )
    segments: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    previous_child_id: str | None = None
    for segment_index, (primary_start, primary_end) in enumerate(ranges, start=1):
        overlap_start = primary_start
        if previous_child_id is not None and overlap_sentences > 0:
            overlap_start = max(0, primary_start - overlap_sentences)
        actual_sentences = sentences[overlap_start:primary_end]
        if not actual_sentences:
            warnings.append(
                {
                    "warning_id": warning_id(build_run_id, scene_id, segment_index, "empty_segment"),
                    "build_run_id": build_run_id,
                    "warning_type": "boundary_empty_segment",
                    "chapter_id": scene["chapter_id"],
                    "version_id": scene["version_id"],
                    "chapter_num": int(scene["chapter_num"]),
                    "scene_id": scene_id,
                    "child_segment_id": None,
                    "sentence_id": None,
                    "message": "planned child segment had no sentences and was not inserted",
                    "created_at": created_at,
                }
            )
            continue
        child_id = child_segment_id(int(scene["chapter_num"]), scene_id, segment_index)
        start_offset = int(actual_sentences[0]["start_offset"])
        end_offset = int(actual_sentences[-1]["end_offset"])
        segment_text_value = chapter_text[start_offset:end_offset]
        distinct_para_ids = list(dict.fromkeys(str(sentence["para_id"]) for sentence in actual_sentences))
        has_overlap = 1 if overlap_start < primary_start else 0
        oversized = [
            sentence
            for sentence in actual_sentences
            if int(sentence["end_offset"]) - int(sentence["start_offset"]) > hard_max_chars
        ]
        boundary_status = "ok"
        if oversized:
            boundary_status = "oversized_sentence_warning"
        elif len(segment_text_value) > hard_max_chars:
            boundary_status = "over_hard_max_warning"
        segment = {
            "child_segment_id": child_id,
            "scene_id": scene_id,
            "chapter_id": scene["chapter_id"],
            "version_id": scene["version_id"],
            "chapter_num": int(scene["chapter_num"]),
            "segment_index_in_scene": segment_index,
            "segment_kind": str(row_value(scene, "scene_kind", "normal") or "normal"),
            "segment_text": segment_text_value,
            "char_len": len(segment_text_value),
            "sentence_count": len(actual_sentences),
            "paragraph_count": len(distinct_para_ids),
            "start_para_id": str(actual_sentences[0]["para_id"]),
            "end_para_id": str(actual_sentences[-1]["para_id"]),
            "start_sentence_id": str(actual_sentences[0]["sentence_id"]),
            "end_sentence_id": str(actual_sentences[-1]["sentence_id"]),
            "start_char_offset": start_offset,
            "end_char_offset": end_offset,
            "has_overlap": has_overlap,
            "overlap_from_child_segment_id": previous_child_id if has_overlap else None,
            "boundary_status": boundary_status,
            "segment_text_hash": sha256_text(segment_text_value),
            "source_fingerprint": source_fingerprint_for_segment(scene, actual_sentences, chapter_text),
            "build_run_id": build_run_id,
            "created_at": created_at,
        }
        segments.append(segment)
        for position, sentence in enumerate(actual_sentences, start=1):
            is_overlap_sentence = 1 if position <= primary_start - overlap_start else 0
            links.append(
                {
                    "link_id": f"{child_id}:sent:{position:04d}",
                    "child_segment_id": child_id,
                    "scene_id": scene_id,
                    "chapter_id": scene["chapter_id"],
                    "version_id": scene["version_id"],
                    "chapter_num": int(scene["chapter_num"]),
                    "position_in_segment": position,
                    "sentence_id": str(sentence["sentence_id"]),
                    "para_id": str(sentence["para_id"]),
                    "sentence_start_offset": int(sentence["start_offset"]),
                    "sentence_end_offset": int(sentence["end_offset"]),
                    "sentence_text_hash": sentence_hash(chapter_text, sentence),
                    "is_overlap_sentence": is_overlap_sentence,
                    "build_run_id": build_run_id,
                    "created_at": created_at,
                }
            )
        for sentence in oversized:
            warnings.append(
                {
                    "warning_id": warning_id(build_run_id, scene_id, child_id, sentence["sentence_id"], "oversized"),
                    "build_run_id": build_run_id,
                    "warning_type": "oversized_sentence",
                    "chapter_id": scene["chapter_id"],
                    "version_id": scene["version_id"],
                    "chapter_num": int(scene["chapter_num"]),
                    "scene_id": scene_id,
                    "child_segment_id": child_id,
                    "sentence_id": str(sentence["sentence_id"]),
                    "message": f"single sentence exceeds hard_max_chars={hard_max_chars}; sentence was not split",
                    "created_at": created_at,
                }
            )
        if boundary_status == "over_hard_max_warning":
            warnings.append(
                {
                    "warning_id": warning_id(build_run_id, scene_id, child_id, "over_hard_max"),
                    "build_run_id": build_run_id,
                    "warning_type": "boundary_over_hard_max",
                    "chapter_id": scene["chapter_id"],
                    "version_id": scene["version_id"],
                    "chapter_num": int(scene["chapter_num"]),
                    "scene_id": scene_id,
                    "child_segment_id": child_id,
                    "sentence_id": None,
                    "message": f"child segment exceeds hard_max_chars={hard_max_chars} because sentence boundaries were preserved",
                    "created_at": created_at,
                }
            )
        previous_child_id = child_id
    return segments, links, warnings


def make_build_run_id(
    scope: list[int],
    *,
    target_min_chars: int,
    target_max_chars: int,
    hard_max_chars: int,
    overlap_sentences: int,
    segment_source_fingerprints: list[str],
) -> str:
    payload = {
        "scope": scope,
        "target_min_chars": target_min_chars,
        "target_max_chars": target_max_chars,
        "hard_max_chars": hard_max_chars,
        "overlap_sentences": overlap_sentences,
        "segment_source_fingerprints": segment_source_fingerprints,
    }
    return "l3_child_segments_" + sha256_json(payload)[:24]


def delete_scope(conn: sqlite3.Connection, scope: list[int]) -> None:
    placeholders = scoped_placeholders(scope)
    params = tuple(scope)
    conn.execute(
        f"""
        DELETE FROM l3_child_segment_sentence_link
        WHERE child_segment_id IN (
            SELECT child_segment_id
            FROM l3_child_segment
            WHERE chapter_num IN ({placeholders})
        )
        """,
        params,
    )
    conn.execute(f"DELETE FROM l3_child_segment_warning WHERE chapter_num IN ({placeholders})", params)
    conn.execute(f"DELETE FROM l3_child_segment WHERE chapter_num IN ({placeholders})", params)


def insert_dicts(conn: sqlite3.Connection, table_name: str, columns: list[str], rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    quoted_cols = ", ".join(quote_ident(column) for column in columns)
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {quote_ident(table_name)} ({quoted_cols}) VALUES ({placeholders})",
        [tuple(row[column] for column in columns) for row in rows],
    )


def stable_row_fingerprint(conn: sqlite3.Connection, scope: list[int]) -> str:
    if not object_exists(conn, "l3_child_segment", "table"):
        return ""
    placeholders = scoped_placeholders(scope)
    params = tuple(scope)
    segment_cols = [
        column
        for column in SEGMENT_COLUMNS
        if column not in {"segment_text", "created_at"}
    ]
    link_cols = [column for column in LINK_COLUMNS if column != "created_at"]
    warning_cols = [column for column in WARNING_COLUMNS if column != "created_at"]
    segments = [
        {column: row[column] for column in segment_cols}
        for row in conn.execute(
            f"""
            SELECT {", ".join(quote_ident(column) for column in segment_cols)}
            FROM l3_child_segment
            WHERE chapter_num IN ({placeholders})
            ORDER BY chapter_num, scene_id, segment_index_in_scene, child_segment_id
            """,
            params,
        ).fetchall()
    ]
    links = [
        {column: row[column] for column in link_cols}
        for row in conn.execute(
            f"""
            SELECT {", ".join(quote_ident(column) for column in link_cols)}
            FROM l3_child_segment_sentence_link
            WHERE chapter_num IN ({placeholders})
            ORDER BY chapter_num, scene_id, child_segment_id, position_in_segment
            """,
            params,
        ).fetchall()
    ]
    warnings = []
    if object_exists(conn, "l3_child_segment_warning", "table"):
        warnings = [
            {column: row[column] for column in warning_cols}
            for row in conn.execute(
                f"""
                SELECT {", ".join(quote_ident(column) for column in warning_cols)}
                FROM l3_child_segment_warning
                WHERE chapter_num IN ({placeholders})
                ORDER BY chapter_num, scene_id, child_segment_id, warning_type, warning_id
                """,
                params,
            ).fetchall()
        ]
    return sha256_json({"segments": segments, "links": links, "warnings": warnings})


def scope_metrics(conn: sqlite3.Connection, scope: list[int]) -> dict[str, Any]:
    placeholders = scoped_placeholders(scope)
    params = tuple(scope)
    if not object_exists(conn, "l3_child_segment", "table"):
        return {
            "chapter_count": 0,
            "scene_block_count": 0,
            "child_segment_count": 0,
            "avg_segment_chars": 0.0,
            "min_segment_chars": 0,
            "max_segment_chars": 0,
            "overlap_segment_count": 0,
            "oversized_sentence_warning_count": 0,
            "empty_segment_count": 0,
            "boundary_warning_count": 0,
            "warning_count": 0,
        }
    row = conn.execute(
        f"""
        SELECT
            COUNT(DISTINCT chapter_num) AS chapter_count,
            COUNT(*) AS child_segment_count,
            COALESCE(AVG(char_len), 0) AS avg_segment_chars,
            COALESCE(MIN(char_len), 0) AS min_segment_chars,
            COALESCE(MAX(char_len), 0) AS max_segment_chars,
            SUM(CASE WHEN has_overlap = 1 THEN 1 ELSE 0 END) AS overlap_segment_count,
            SUM(CASE WHEN sentence_count = 0 THEN 1 ELSE 0 END) AS empty_segment_count
        FROM l3_child_segment
        WHERE chapter_num IN ({placeholders})
        """,
        params,
    ).fetchone()
    scene_block_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM l3_scene_blocks WHERE chapter_num IN ({placeholders})",
            params,
        ).fetchone()[0]
    )
    warning_count = 0
    oversized_count = 0
    boundary_count = 0
    if object_exists(conn, "l3_child_segment_warning", "table"):
        warning_count = int(
            conn.execute(
                f"SELECT COUNT(*) FROM l3_child_segment_warning WHERE chapter_num IN ({placeholders})",
                params,
            ).fetchone()[0]
        )
        oversized_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM l3_child_segment_warning
                WHERE chapter_num IN ({placeholders})
                  AND warning_type = 'oversized_sentence'
                """,
                params,
            ).fetchone()[0]
        )
        boundary_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM l3_child_segment_warning
                WHERE chapter_num IN ({placeholders})
                  AND warning_type LIKE 'boundary_%'
                """,
                params,
            ).fetchone()[0]
        )
    return {
        "chapter_count": int(row["chapter_count"] or 0),
        "scene_block_count": scene_block_count,
        "child_segment_count": int(row["child_segment_count"] or 0),
        "avg_segment_chars": round(float(row["avg_segment_chars"] or 0), 2),
        "min_segment_chars": int(row["min_segment_chars"] or 0),
        "max_segment_chars": int(row["max_segment_chars"] or 0),
        "overlap_segment_count": int(row["overlap_segment_count"] or 0),
        "oversized_sentence_warning_count": oversized_count,
        "empty_segment_count": int(row["empty_segment_count"] or 0),
        "boundary_warning_count": boundary_count,
        "warning_count": warning_count,
    }


def write_build_run(
    conn: sqlite3.Connection,
    *,
    build_run_id: str,
    scope: list[int],
    target_min_chars: int,
    target_max_chars: int,
    hard_max_chars: int,
    overlap_sentences: int,
    metrics: dict[str, Any],
    source_guard_before_hash: str,
    source_guard_after_hash: str,
    source_table_names: list[str],
    source_mutation_detected: bool,
    forbidden_tables: list[str],
    stable_fingerprint: str,
    error_count: int,
    status: str,
    started_at: str,
    finished_at: str,
    note: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO l3_child_segment_build_run (
            build_run_id, run_scope, sample_chapters,
            target_min_chars, target_max_chars, hard_max_chars, overlap_sentences,
            chapter_count, scene_block_count, child_segment_count,
            avg_segment_chars, min_segment_chars, max_segment_chars,
            overlap_segment_count, oversized_sentence_warning_count,
            empty_segment_count, boundary_warning_count,
            source_guard_before_hash, source_guard_after_hash,
            source_table_names_json, source_mutation_detected,
            forbidden_final_table_count, stable_row_fingerprint,
            error_count, warning_count, status, started_at, finished_at, note
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(build_run_id) DO UPDATE SET
            run_scope = excluded.run_scope,
            sample_chapters = excluded.sample_chapters,
            target_min_chars = excluded.target_min_chars,
            target_max_chars = excluded.target_max_chars,
            hard_max_chars = excluded.hard_max_chars,
            overlap_sentences = excluded.overlap_sentences,
            chapter_count = excluded.chapter_count,
            scene_block_count = excluded.scene_block_count,
            child_segment_count = excluded.child_segment_count,
            avg_segment_chars = excluded.avg_segment_chars,
            min_segment_chars = excluded.min_segment_chars,
            max_segment_chars = excluded.max_segment_chars,
            overlap_segment_count = excluded.overlap_segment_count,
            oversized_sentence_warning_count = excluded.oversized_sentence_warning_count,
            empty_segment_count = excluded.empty_segment_count,
            boundary_warning_count = excluded.boundary_warning_count,
            source_guard_before_hash = excluded.source_guard_before_hash,
            source_guard_after_hash = excluded.source_guard_after_hash,
            source_table_names_json = excluded.source_table_names_json,
            source_mutation_detected = excluded.source_mutation_detected,
            forbidden_final_table_count = excluded.forbidden_final_table_count,
            stable_row_fingerprint = excluded.stable_row_fingerprint,
            error_count = excluded.error_count,
            warning_count = excluded.warning_count,
            status = excluded.status,
            started_at = excluded.started_at,
            finished_at = excluded.finished_at,
            note = excluded.note
        """,
        (
            build_run_id,
            "sample",
            ",".join(str(item) for item in scope),
            target_min_chars,
            target_max_chars,
            hard_max_chars,
            overlap_sentences,
            metrics["chapter_count"],
            metrics["scene_block_count"],
            metrics["child_segment_count"],
            metrics["avg_segment_chars"],
            metrics["min_segment_chars"],
            metrics["max_segment_chars"],
            metrics["overlap_segment_count"],
            metrics["oversized_sentence_warning_count"],
            metrics["empty_segment_count"],
            metrics["boundary_warning_count"],
            source_guard_before_hash,
            source_guard_after_hash,
            json.dumps(source_table_names, ensure_ascii=False, separators=(",", ":")),
            1 if source_mutation_detected else 0,
            len(forbidden_tables),
            stable_fingerprint,
            error_count,
            metrics["warning_count"],
            status,
            started_at,
            finished_at,
            note,
        ),
    )


def validate_params(target_min_chars: int, target_max_chars: int, hard_max_chars: int, overlap_sentences: int) -> None:
    if target_min_chars <= 0 or target_max_chars <= 0 or hard_max_chars <= 0:
        raise ValueError("target and hard char limits must be positive")
    if target_min_chars > target_max_chars:
        raise ValueError("target_min_chars must be <= target_max_chars")
    if target_max_chars > hard_max_chars:
        raise ValueError("target_max_chars must be <= hard_max_chars")
    if overlap_sentences < 0:
        raise ValueError("overlap_sentences must be >= 0")


def run_build(
    project_dir: Path | str,
    *,
    sample_chapters: str | None = None,
    chapter_num: int | None = None,
    rebuild: bool = False,
    target_min_chars: int = DEFAULT_TARGET_MIN_CHARS,
    target_max_chars: int = DEFAULT_TARGET_MAX_CHARS,
    hard_max_chars: int = DEFAULT_HARD_MAX_CHARS,
    overlap_sentences: int = DEFAULT_OVERLAP_SENTENCES,
) -> BuildResult:
    root = Path(project_dir).resolve()
    ensure_dirs(root)
    db_path = root / DB_RELATIVE_PATH
    scope: list[int] = []
    result = BuildResult(False, root, db_path, scope, rebuild)
    started_at = now_iso()
    try:
        scope = parse_scope(sample_chapters, chapter_num)
        result.scope = scope
        validate_params(target_min_chars, target_max_chars, hard_max_chars, overlap_sentences)
        if not db_path.exists():
            raise RuntimeError("SQLite database does not exist")
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            validate_source_objects(conn)
            source_table_names = discover_source_table_names(conn)
            source_guard_before = collect_source_guard_stats(conn, source_table_names)
            source_guard_before_hash = guard_hash(source_guard_before)
            chapters = fetch_current_chapters(conn, scope)
            scenes = fetch_scene_blocks(conn, scope)
            if not scenes:
                raise RuntimeError(f"l3_scene_blocks has no rows for scope {scope}")
            scene_chapters = sorted({int(scene["chapter_num"]) for scene in scenes})
            if scene_chapters != scope:
                raise RuntimeError(f"l3_scene_blocks missing chapters for scope {scope}; found {scene_chapters}")

            created_at = now_iso()
            preliminary_segments: list[dict[str, Any]] = []
            preliminary_links: list[dict[str, Any]] = []
            preliminary_warnings: list[dict[str, Any]] = []
            for scene in scenes:
                chapter = chapters[int(scene["chapter_num"])]
                sentences = fetch_sentences_for_scene(conn, scene)
                segments, links, warnings = build_records_for_scene(
                    scene,
                    sentences,
                    chapter,
                    build_run_id="pending",
                    created_at=created_at,
                    target_min_chars=target_min_chars,
                    target_max_chars=target_max_chars,
                    hard_max_chars=hard_max_chars,
                    overlap_sentences=overlap_sentences,
                )
                preliminary_segments.extend(segments)
                preliminary_links.extend(links)
                preliminary_warnings.extend(warnings)
            if not preliminary_segments:
                raise RuntimeError("L3.5 produced no child segments for selected sample scope")

            build_run_id = make_build_run_id(
                scope,
                target_min_chars=target_min_chars,
                target_max_chars=target_max_chars,
                hard_max_chars=hard_max_chars,
                overlap_sentences=overlap_sentences,
                segment_source_fingerprints=[row["source_fingerprint"] for row in preliminary_segments],
            )
            result.build_run_id = build_run_id
            for row in preliminary_segments:
                row["build_run_id"] = build_run_id
            for row in preliminary_links:
                row["build_run_id"] = build_run_id
                row["link_id"] = row["link_id"].replace(":sent:", f":{build_run_id}:sent:")
            for row in preliminary_warnings:
                row["build_run_id"] = build_run_id
                row["warning_id"] = warning_id(build_run_id, row["scene_id"], row["child_segment_id"], row["sentence_id"], row["warning_type"])

            init_schema(conn)
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
                if rebuild:
                    delete_scope(conn, scope)
                else:
                    placeholders = scoped_placeholders(scope)
                    existing = int(
                        conn.execute(
                            f"SELECT COUNT(*) FROM l3_child_segment WHERE chapter_num IN ({placeholders})",
                            tuple(scope),
                        ).fetchone()[0]
                    )
                    if existing:
                        raise RuntimeError("L3.5 child segments already exist for scope; rerun with --rebuild")
                insert_dicts(conn, "l3_child_segment", SEGMENT_COLUMNS, preliminary_segments)
                insert_dicts(conn, "l3_child_segment_sentence_link", LINK_COLUMNS, preliminary_links)
                insert_dicts(conn, "l3_child_segment_warning", WARNING_COLUMNS, preliminary_warnings)
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            metrics = scope_metrics(conn, scope)
            stable_fingerprint = stable_row_fingerprint(conn, scope)
            source_guard_after = collect_source_guard_stats(conn, source_table_names)
            source_guard_after_hash = guard_hash(source_guard_after)
            forbidden_tables = forbidden_final_tables(conn)
            source_mutation_detected = source_guard_before_hash != source_guard_after_hash
            status = "failed" if source_mutation_detected or forbidden_tables else "passed"
            finished_at = now_iso()
            conn.execute("BEGIN IMMEDIATE")
            try:
                write_build_run(
                    conn,
                    build_run_id=build_run_id,
                    scope=scope,
                    target_min_chars=target_min_chars,
                    target_max_chars=target_max_chars,
                    hard_max_chars=hard_max_chars,
                    overlap_sentences=overlap_sentences,
                    metrics=metrics,
                    source_guard_before_hash=source_guard_before_hash,
                    source_guard_after_hash=source_guard_after_hash,
                    source_table_names=source_table_names,
                    source_mutation_detected=source_mutation_detected,
                    forbidden_tables=forbidden_tables,
                    stable_fingerprint=stable_fingerprint,
                    error_count=1 if status == "failed" else 0,
                    status=status,
                    started_at=started_at,
                    finished_at=finished_at,
                    note="No LLM calls. No embedding. No Chroma/vector writes.",
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

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
            result.error_count = 1 if status == "failed" else 0
            result.source_mutation_detected = source_mutation_detected
            result.forbidden_final_tables = forbidden_tables
            result.stable_row_fingerprint = stable_fingerprint
            if source_mutation_detected:
                result.errors.append("source table mutation detected during L3.5 build")
            if forbidden_tables:
                result.errors.append(f"forbidden final tables exist: {', '.join(forbidden_tables)}")
            result.ok = not result.errors
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        result.error_count = len(result.errors)
        result.ok = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L3.5 child_segments from L3 scene_blocks and L2 sentences.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--sample-chapters", type=str, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--target-min-chars", type=int, default=DEFAULT_TARGET_MIN_CHARS)
    parser.add_argument("--target-max-chars", type=int, default=DEFAULT_TARGET_MAX_CHARS)
    parser.add_argument("--hard-max-chars", type=int, default=DEFAULT_HARD_MAX_CHARS)
    parser.add_argument("--overlap-sentences", type=int, default=DEFAULT_OVERLAP_SENTENCES)
    args = parser.parse_args()
    result = run_build(
        args.project_dir,
        sample_chapters=args.sample_chapters,
        chapter_num=args.chapter_num,
        rebuild=args.rebuild,
        target_min_chars=args.target_min_chars,
        target_max_chars=args.target_max_chars,
        hard_max_chars=args.hard_max_chars,
        overlap_sentences=args.overlap_sentences,
    )
    print("L3.5 child segments BUILD PASS" if result.ok else "L3.5 child segments BUILD FAIL")
    if result.ok:
        print(f"child_segment_count={result.child_segment_count}")
        print(f"build_run_id={result.build_run_id}")
    else:
        for error in result.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
