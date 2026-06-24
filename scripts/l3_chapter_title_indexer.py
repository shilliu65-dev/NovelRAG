from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DB_RELATIVE_PATH = Path("index") / "novel_story_bible.db"
REPORT_RELATIVE_PATH = Path("outputs") / "l3_chapter_title_index_report.md"
MAIN_CHAPTER_MIN = 1
MAIN_CHAPTER_MAX = 1922
REQUIRED_CURRENT_COLUMNS = {"chapter_id", "chapter_num", "chapter_title_current", "latest_version_id"}
L3_INDEX_COLUMNS = {
    "title_index_id",
    "chapter_id",
    "version_id",
    "chapter_num",
    "title_raw",
    "title_norm",
    "title_keywords_json",
    "title_hash",
    "created_at",
}
DEFAULT_SPECIAL_TERMS = {
    "陈伶",
    "红王",
    "陈妖",
    "陈宴",
    "楚牧云",
    "黄昏社",
    "执法者",
    "浮生绘",
    "极光界域",
    "红尘界域",
    "无极界域",
    "南海界域",
    "天枢界域",
    "灵虚界域",
    "悬玉界域",
    "承天界域",
    "永恒界域",
    "灰界",
    "戏神道",
    "医神道",
    "巫神道",
    "兵神道",
    "青神道",
    "巧神道",
    "弈神道",
    "偶神道",
    "力神道",
    "卜神道",
    "盗神道",
    "娼神道",
    "嘲神道",
    "灾厄",
    "嘲灾",
    "忌灾",
    "息灾",
    "浊灾",
    "妄灾",
    "思灾",
    "寂灾",
    "九君",
}
DEFAULT_STOPWORDS = {"的", "了", "和", "与", "在", "是", "一个", "一种", "之", "其", "这", "那", "第", "章", "卷", "篇"}


try:
    import jieba  # type: ignore

    HAS_JIEBA = True
except ImportError:
    jieba = None
    HAS_JIEBA = False


@dataclass
class L3IndexStats:
    ok: bool
    project_dir: Path
    db_path: Path
    report_path: Path
    rebuild: bool = False
    limit: int | None = None
    chapter_num: int | None = None
    selected_chapters: int = 0
    processed_chapters: int = 0
    inserted_indexes: int = 0
    skipped_indexes: int = 0
    fts_inserted: int = 0
    main_count: int = 0
    fts_count: int = 0
    guard_before: dict[str, Any] = field(default_factory=dict)
    guard_after: dict[str, Any] = field(default_factory=dict)
    guard_unchanged: bool = False
    guard_notes: list[str] = field(default_factory=list)
    l2_precheck_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def project_root_from_env() -> Path:
    configured = os.environ.get("NOVEL_RAG_PROJECT_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def ensure_dirs(project_dir: Path) -> None:
    for name in ("docs", "config", "dict", "outputs", "index"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def object_exists(conn: sqlite3.Connection, name: str, kind: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1",
        (kind, name),
    ).fetchone()
    return row is not None


def table_columns(conn: sqlite3.Connection, name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({name})")}


def table_column_defs(conn: sqlite3.Connection, name: str) -> list[str]:
    return [f"{row['name']}:{row['type']}:{row['notnull']}:{row['pk']}" for row in conn.execute(f"PRAGMA table_info({name})")]


def read_word_set(path: Path, defaults: set[str]) -> set[str]:
    if not path.exists():
        return set(defaults)
    words = {line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()}
    return set(defaults) | words


def normalize_title(title: str) -> str:
    return re.sub(r"\s+", " ", title.replace("\u3000", " ")).strip()


def is_punctuation_only(value: str) -> bool:
    return bool(value) and re.fullmatch(r"[\W_]+", value, flags=re.UNICODE) is not None


def tokenize_title(title: str, project_dir: Path) -> list[str]:
    title_norm = normalize_title(title)
    special_terms = read_word_set(project_dir / "dict" / "special_terms.txt", DEFAULT_SPECIAL_TERMS)
    stopwords = read_word_set(project_dir / "dict" / "stopwords.txt", DEFAULT_STOPWORDS)
    candidates: list[tuple[int, str]] = []

    for term in sorted(special_terms, key=len, reverse=True):
        start = title_norm.find(term)
        if start >= 0:
            candidates.append((start, term))

    if HAS_JIEBA and jieba is not None:
        for term in special_terms:
            jieba.add_word(term)
        pieces = list(jieba.cut(title_norm, cut_all=False))
    else:
        pieces = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", title_norm)

    for piece in pieces:
        token = piece.strip()
        if not token:
            continue
        start = title_norm.find(token)
        candidates.append((start if start >= 0 else len(title_norm), token))

    stable: dict[str, int] = {}
    for position, token in candidates:
        token = token.strip()
        if not token or token in stopwords or is_punctuation_only(token):
            continue
        if len(token) == 1 and token not in special_terms:
            continue
        if re.fullmatch(r"第?\d+章?", token):
            continue
        stable[token] = min(position, stable.get(token, position))

    return [item for item, _ in sorted(stable.items(), key=lambda pair: (pair[1], pair[0]))]


def init_l3_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l3_chapter_title_index (
            title_index_id INTEGER PRIMARY KEY,
            chapter_id TEXT NOT NULL,
            version_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            title_raw TEXT NOT NULL,
            title_norm TEXT NOT NULL,
            title_keywords_json TEXT NOT NULL,
            title_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(chapter_num),
            UNIQUE(chapter_id, version_id)
        );

        CREATE INDEX IF NOT EXISTS idx_l3_title_chapter_num
            ON l3_chapter_title_index(chapter_num);

        CREATE INDEX IF NOT EXISTS idx_l3_title_chapter_id_version
            ON l3_chapter_title_index(chapter_id, version_id);

        CREATE INDEX IF NOT EXISTS idx_l3_title_hash
            ON l3_chapter_title_index(title_hash);

        CREATE VIRTUAL TABLE IF NOT EXISTS l3_chapter_title_fts
        USING fts5(
            title_norm,
            title_keywords,
            chapter_num UNINDEXED
        );
        """
    )


def validate_l3_schema(conn: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    if not object_exists(conn, "l3_chapter_title_index", "table"):
        errors.append("l3_chapter_title_index is missing; rerun with --rebuild")
    if not object_exists(conn, "l3_chapter_title_fts", "table"):
        errors.append("l3_chapter_title_fts is missing; rerun with --rebuild")
    if errors:
        return errors
    missing = L3_INDEX_COLUMNS - table_columns(conn, "l3_chapter_title_index")
    if missing:
        errors.append(f"l3_chapter_title_index schema mismatch: missing {', '.join(sorted(missing))}; rerun with --rebuild")
    fts_cols = table_columns(conn, "l3_chapter_title_fts")
    for col in ("title_norm", "title_keywords", "chapter_num"):
        if col not in fts_cols:
            errors.append(f"l3_chapter_title_fts schema mismatch: missing {col}; rerun with --rebuild")
    return errors


def validate_source_schema(conn: sqlite3.Connection) -> None:
    if not object_exists(conn, "v_current_chapters", "view"):
        raise RuntimeError("v_current_chapters view is missing")
    missing = REQUIRED_CURRENT_COLUMNS - table_columns(conn, "v_current_chapters")
    if missing:
        raise RuntimeError(f"v_current_chapters missing required columns: {', '.join(sorted(missing))}")


def select_current_chapters(conn: sqlite3.Connection, *, limit: int | None, chapter_num: int | None) -> list[sqlite3.Row]:
    validate_source_schema(conn)
    where = ["chapter_num BETWEEN ? AND ?"]
    params: list[Any] = [MAIN_CHAPTER_MIN, MAIN_CHAPTER_MAX]
    if chapter_num is not None:
        where.append("chapter_num = ?")
        params.append(chapter_num)
    sql = f"""
        SELECT
            chapter_id,
            latest_version_id AS version_id,
            chapter_num,
            chapter_title_current AS title_raw
        FROM v_current_chapters
        WHERE {" AND ".join(where)}
        ORDER BY chapter_num ASC
    """
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)
    return list(conn.execute(sql, params))


def existing_metadata_digest(conn: sqlite3.Connection, object_name: str, fields: list[str]) -> tuple[int, str, int]:
    columns = table_columns(conn, object_name)
    selected = [field for field in fields if field in columns]
    count = int(conn.execute(f"SELECT COUNT(*) FROM {object_name}").fetchone()[0])
    if not selected:
        return count, "metadata-fields-unavailable", 0
    digest = hashlib.sha256()
    length_sum = 0
    quoted = ", ".join(selected)
    for row in conn.execute(f"SELECT {quoted} FROM {object_name} ORDER BY 1"):
        values = ["" if value is None else str(value) for value in row]
        joined = "|".join(values)
        length_sum += sum(len(value) for value in values)
        digest.update(joined.encode("utf-8"))
        digest.update(b"\n")
    return count, digest.hexdigest(), length_sum


def collect_source_guard_stats(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[str]]:
    objects: dict[str, tuple[str, list[str]]] = {
        "chapter_registry": ("table", ["chapter_id", "chapter_num", "latest_version_id", "chapter_title_current", "volume_code", "book_code"]),
        "chapter_contents": ("table", ["version_id", "chapter_id", "chapter_num", "content_hash", "content_length", "is_current"]),
        "v_current_chapters": ("view", ["chapter_id", "chapter_num", "latest_version_id", "chapter_title_current", "content_hash", "content_length"]),
        "l2_paragraph_units": ("table", ["para_id", "chapter_id", "version_id", "para_index", "char_length", "para_hash"]),
        "l2_sentence_units": ("table", ["sentence_id", "para_id", "chapter_id", "version_id", "global_sentence_index", "char_length", "sentence_hash"]),
        "l2_index_status": ("table", ["chapter_id", "version_id", "content_hash", "paragraph_count", "sentence_count", "status"]),
    }
    stats: dict[str, Any] = {}
    notes: list[str] = []
    for name, (kind, fields) in objects.items():
        if not object_exists(conn, name, kind):
            raise RuntimeError(f"L1/L2 guard object missing: {name}")
        cols = sorted(table_columns(conn, name))
        used_fields = [field for field in fields if field in cols]
        missing_hash_fields = [field for field in fields if field.endswith("hash") and field not in cols]
        if missing_hash_fields:
            notes.append(f"{name}: missing hash fields {', '.join(missing_hash_fields)}; guard downgraded to available metadata")
        count, digest, length_sum = existing_metadata_digest(conn, name, fields)
        stats[name] = {
            "kind": kind,
            "count": count,
            "schema": sha256_text("|".join(table_column_defs(conn, name))),
            "metadata_digest": digest,
            "metadata_length_sum": length_sum,
            "used_fields": used_fields,
        }
    return stats, notes


def precheck_l2(conn: sqlite3.Connection) -> list[str]:
    notes: list[str] = []
    for name in ("l2_paragraph_units", "l2_sentence_units", "l2_index_status"):
        if not object_exists(conn, name, "table"):
            raise RuntimeError(f"L2 precheck failed: {name} is missing")
    status_cols = table_columns(conn, "l2_index_status")
    required = {"chapter_id", "version_id", "status"}
    missing = required - status_cols
    if missing:
        raise RuntimeError(f"L2 precheck failed: l2_index_status missing {', '.join(sorted(missing))}")
    compare_hash = "content_hash" in status_cols and "content_hash" in table_columns(conn, "v_current_chapters")
    if not compare_hash:
        notes.append("l2_index_status.content_hash unavailable; downgraded to status/version coverage check")

    rows = conn.execute(
        """
        SELECT c.chapter_id, c.chapter_num, c.latest_version_id, c.content_hash
        FROM v_current_chapters c
        WHERE c.chapter_num BETWEEN ? AND ?
        ORDER BY c.chapter_num
        """,
        (MAIN_CHAPTER_MIN, MAIN_CHAPTER_MAX),
    ).fetchall()
    missing_status: list[str] = []
    hash_mismatch: list[str] = []
    for row in rows:
        status = conn.execute(
            """
            SELECT *
            FROM l2_index_status
            WHERE chapter_id = ?
              AND version_id = ?
              AND status = 'indexed'
            """,
            (row["chapter_id"], row["latest_version_id"]),
        ).fetchone()
        if status is None:
            missing_status.append(f"{row['chapter_id']} chapter={row['chapter_num']}")
            continue
        if compare_hash and status["content_hash"] != row["content_hash"]:
            hash_mismatch.append(f"{row['chapter_id']} chapter={row['chapter_num']}")
    if missing_status:
        raise RuntimeError(f"L2 precheck failed: missing indexed status for {', '.join(missing_status[:10])}")
    if hash_mismatch:
        raise RuntimeError(f"L2 precheck failed: status content_hash mismatch for {', '.join(hash_mismatch[:10])}")
    notes.append(f"L2 precheck covered {len(rows)} current main chapters")
    return notes


def scope_chapter_nums(rows: list[sqlite3.Row]) -> list[int]:
    return [int(row["chapter_num"]) for row in rows]


def validate_l3_consistency(conn: sqlite3.Connection, expected_chapter_nums: list[int]) -> list[str]:
    errors = validate_l3_schema(conn)
    if errors:
        return errors
    if not expected_chapter_nums:
        return ["expected scope is empty"]
    placeholders = ", ".join("?" for _ in expected_chapter_nums)
    params = tuple(expected_chapter_nums)
    main_count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM l3_chapter_title_index WHERE chapter_num IN ({placeholders})",
            params,
        ).fetchone()[0]
    )
    fts_count = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM l3_chapter_title_fts f
            JOIN l3_chapter_title_index i ON i.title_index_id = f.rowid
            WHERE i.chapter_num IN ({placeholders})
            """,
            params,
        ).fetchone()[0]
    )
    expected_count = len(expected_chapter_nums)
    if main_count != expected_count:
        errors.append(f"L3 main table count mismatch for current scope: expected={expected_count} actual={main_count}; rerun with --rebuild")
    if fts_count != expected_count:
        errors.append(f"L3 FTS count mismatch for current scope: expected={expected_count} actual={fts_count}; rerun with --rebuild")
    orphan_fts = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM l3_chapter_title_fts f
            LEFT JOIN l3_chapter_title_index i ON i.title_index_id = f.rowid
            WHERE i.title_index_id IS NULL
            """
        ).fetchone()[0]
    )
    if orphan_fts:
        errors.append("L3 FTS has orphan rowid; rerun with --rebuild")
    missing_fts = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM l3_chapter_title_index i
            LEFT JOIN l3_chapter_title_fts f ON f.rowid = i.title_index_id
            WHERE i.chapter_num IN ({placeholders})
              AND f.rowid IS NULL
            """,
            params,
        ).fetchone()[0]
    )
    if missing_fts:
        errors.append("L3 main table has rows missing FTS rows; rerun with --rebuild")
    return errors


def insert_chapter_title(conn: sqlite3.Connection, project_dir: Path, row: sqlite3.Row) -> tuple[bool, bool]:
    title_raw = str(row["title_raw"])
    title_norm = normalize_title(title_raw)
    keywords = tokenize_title(title_raw, project_dir)
    keywords_json = json.dumps(keywords, ensure_ascii=False, separators=(",", ":"))
    keywords_text = " ".join(keywords)
    chapter_num = int(row["chapter_num"])
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO l3_chapter_title_index (
            title_index_id, chapter_id, version_id, chapter_num,
            title_raw, title_norm, title_keywords_json, title_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chapter_num,
            row["chapter_id"],
            row["version_id"],
            chapter_num,
            title_raw,
            title_norm,
            keywords_json,
            sha256_text(title_raw),
        ),
    )
    inserted = conn.total_changes > before
    if not inserted:
        return False, False
    conn.execute(
        """
        INSERT INTO l3_chapter_title_fts(rowid, title_norm, title_keywords, chapter_num)
        VALUES (?, ?, ?, ?)
        """,
        (chapter_num, title_norm, keywords_text, chapter_num),
    )
    return True, True


def table_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    main = int(conn.execute("SELECT COUNT(*) FROM l3_chapter_title_index").fetchone()[0])
    fts = int(conn.execute("SELECT COUNT(*) FROM l3_chapter_title_fts").fetchone()[0])
    return main, fts


def build_report(stats: L3IndexStats) -> str:
    final_line = "L3 CHAPTER TITLE INDEX BUILD PASS" if stats.ok else "L3 CHAPTER TITLE INDEX BUILD FAIL"
    lines = [
        "# L3 章节标题入口索引构建报告",
        "",
        f"- 构建时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 数据库路径：{stats.db_path}",
        f"- rebuild：{stats.rebuild}",
        f"- limit：{stats.limit if stats.limit is not None else '无'}",
        f"- chapter-num：{stats.chapter_num if stats.chapter_num is not None else '无'}",
        f"- 读取章节数：{stats.selected_chapters}",
        f"- 处理章节数：{stats.processed_chapters}",
        f"- 新增标题索引数：{stats.inserted_indexes}",
        f"- 跳过标题索引数：{stats.skipped_indexes}",
        f"- FTS 写入数：{stats.fts_inserted}",
        f"- 主表最终行数：{stats.main_count}",
        f"- FTS 最终行数：{stats.fts_count}",
        f"- L1/L2 guard unchanged：{stats.guard_unchanged}",
        f"- 最终结论：{'PASS' if stats.ok else 'FAIL'}",
        "",
        "## L2 预检说明",
        "",
    ]
    lines.extend(f"- {note}" for note in stats.l2_precheck_notes) if stats.l2_precheck_notes else lines.append("- 无")
    lines.extend(["", "## Guard 说明", ""])
    lines.extend(f"- {note}" for note in stats.guard_notes) if stats.guard_notes else lines.append("- 无")
    lines.extend(["", "## 错误列表", ""])
    lines.extend(f"- {error}" for error in stats.errors) if stats.errors else lines.append("- 无")
    lines.extend(["", final_line, ""])
    return "\n".join(lines)


def write_report(stats: L3IndexStats) -> None:
    stats.report_path.parent.mkdir(parents=True, exist_ok=True)
    stats.report_path.write_text(build_report(stats), encoding="utf-8")


def run_index(
    project_dir: Path | str | None = None,
    *,
    rebuild: bool = False,
    limit: int | None = None,
    chapter_num: int | None = None,
) -> L3IndexStats:
    if limit is not None and chapter_num is not None:
        root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
        ensure_dirs(root)
        stats = L3IndexStats(False, root, root / DB_RELATIVE_PATH, root / REPORT_RELATIVE_PATH, rebuild, limit, chapter_num)
        stats.errors.append("--limit and --chapter-num are mutually exclusive")
        write_report(stats)
        return stats

    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    db_path = root / DB_RELATIVE_PATH
    stats = L3IndexStats(
        ok=False,
        project_dir=root,
        db_path=db_path,
        report_path=root / REPORT_RELATIVE_PATH,
        rebuild=rebuild,
        limit=limit,
        chapter_num=chapter_num,
    )
    if not db_path.exists():
        stats.errors.append("SQLite database does not exist")
        write_report(stats)
        return stats

    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        try:
            stats.l2_precheck_notes.extend(precheck_l2(conn))
            stats.guard_before, before_notes = collect_source_guard_stats(conn)
            stats.guard_notes.extend(before_notes)
            if rebuild:
                init_l3_schema(conn)
                conn.execute("DELETE FROM l3_chapter_title_fts")
                conn.execute("DELETE FROM l3_chapter_title_index")
                conn.commit()
            rows = select_current_chapters(conn, limit=limit, chapter_num=chapter_num)
            stats.selected_chapters = len(rows)
            if not rows:
                raise RuntimeError("no current main chapters selected")
            if not rebuild:
                consistency_errors = validate_l3_consistency(conn, scope_chapter_nums(rows))
                if consistency_errors:
                    stats.errors.extend(consistency_errors)
                    raise RuntimeError("L3 consistency check failed")

            conn.execute("BEGIN IMMEDIATE")
            try:
                for row in rows:
                    inserted, fts_inserted = insert_chapter_title(conn, root, row)
                    stats.processed_chapters += 1
                    if inserted:
                        stats.inserted_indexes += 1
                    else:
                        stats.skipped_indexes += 1
                    if fts_inserted:
                        stats.fts_inserted += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise

            stats.main_count, stats.fts_count = table_counts(conn)
            stats.guard_after, after_notes = collect_source_guard_stats(conn)
            stats.guard_notes.extend(after_notes)
            stats.guard_unchanged = stats.guard_before == stats.guard_after
            if not stats.guard_unchanged:
                stats.errors.append("L1/L2 guard changed during L3 title indexing")
            stats.ok = not stats.errors
        except Exception as exc:  # noqa: BLE001
            if not stats.errors or str(exc) not in stats.errors:
                stats.errors.append(str(exc))
            stats.main_count, stats.fts_count = table_counts(conn) if object_exists(conn, "l3_chapter_title_index", "table") and object_exists(conn, "l3_chapter_title_fts", "table") else (0, 0)
            stats.ok = False
    finally:
        conn.close()
    write_report(stats)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L3 chapter title entry index.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    args = parser.parse_args()
    stats = run_index(args.project_dir, rebuild=args.rebuild, limit=args.limit, chapter_num=args.chapter_num)
    print(f"L3 chapter title index report: {stats.report_path}")
    print("L3 CHAPTER TITLE INDEX BUILD PASS" if stats.ok else "L3 CHAPTER TITLE INDEX BUILD FAIL")
    raise SystemExit(0 if stats.ok else 1)


if __name__ == "__main__":
    main()
