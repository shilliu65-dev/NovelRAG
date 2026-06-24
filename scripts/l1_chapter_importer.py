from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


CLEAN_RULES_VERSION = "l1_md_norm_v1"
DB_RELATIVE_PATH = Path("index") / "novel_story_bible.db"
LOG_RELATIVE_PATH = Path("logs") / "l1_importer.log"
REPORT_RELATIVE_PATH = Path("outputs") / "l1_import_report.md"
FAILED_RELATIVE_PATH = Path("outputs") / "l1_failed_chapters.txt"


@dataclass(frozen=True)
class VolumeRange:
    start_chapter: int
    end_chapter: int
    volume_code: str
    volume_name: str
    book_code: str
    book_name: str
    core_stage: str
    boundary_confidence: str


# Boundaries marked "provisional" are current manual working boundaries, not
# a claim that the original publication defines exact book breaks there.
CHAPTER_RANGE_REGISTRY: tuple[VolumeRange, ...] = (
    VolumeRange(1, 160, "V01", "第一卷《戏中人》", "B01", "第1册《戏中人》", "极光界域前期 / 世界观奠基", "exact"),
    VolumeRange(161, 319, "V01", "第一卷《戏中人》", "B02", "第2册《极光君》", "极光界域后期 / 极光君主线", "exact"),
    VolumeRange(320, 458, "V02", "第二卷《绘朱颜》", "B03", "第3册《戏神道》", "戏神道展开 / 红尘线开启", "exact"),
    VolumeRange(459, 593, "V02", "第二卷《绘朱颜》", "B04", "第4册《绘朱颜》", "绘朱颜主线", "exact"),
    VolumeRange(594, 724, "V02", "第二卷《绘朱颜》", "B05", "第5册《绣红尘》", "红尘界域深化", "exact"),
    VolumeRange(725, 851, "V03", "第三卷《祭神舞》", "B06", "第6册《通天劫》", "通天劫主线", "exact"),
    VolumeRange(852, 1005, "V03", "第三卷《祭神舞》", "B07", "第7册《帝神道》", "帝神道主线", "exact"),
    VolumeRange(1006, 1148, "V03", "第三卷《祭神舞》", "B08", "第8册《祭神舞》", "祭神舞主线", "exact"),
    VolumeRange(1149, 1288, "V04", "第四卷《嘲歌行》", "B09", "第9册《吴山宴》", "吴山宴主线", "exact"),
    VolumeRange(1289, 1470, "V04", "第四卷《嘲歌行》", "B10", "第10册《嘲歌行》", "嘲歌行主线", "provisional"),
    VolumeRange(1471, 1700, "V05", "第五卷《灭世曲》", "B11", "第11册《灭世曲》", "灭世曲主线", "provisional"),
    VolumeRange(1701, 1922, "V06", "第六卷《红王泪》", "B12", "第12册《红王泪》", "红王泪主线 / 正文完结", "provisional"),
)

VOLUME_REGISTRY = CHAPTER_RANGE_REGISTRY

UNKNOWN_METADATA = {
    "volume_code": "UNKNOWN",
    "volume_name": "UNKNOWN",
    "book_code": "UNKNOWN",
    "book_name": "UNKNOWN",
    "core_stage": "UNKNOWN",
}

EXTRA_METADATA = {
    "volume_code": "EXTRA",
    "volume_name": "非正文附录",
    "book_code": "EXTRA",
    "book_name": "完本感言 / 后记 / 附录",
    "core_stage": "非正文内容",
}


@dataclass
class ImportStats:
    scanned_files: int = 0
    parsed_files: int = 0
    inserted_chapters: int = 0
    unchanged_chapters: int = 0
    new_versions: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    unknown_metadata_chapters: int = 0
    db_total_chapters: int = 0
    db_total_versions: int = 0
    run_started_at: str = ""
    project_dir: Path = Path()
    input_dir: Path = Path()
    db_path: Path = Path()
    report_path: Path = Path()
    failed_path: Path = Path()
    details: list[str] = field(default_factory=list)
    unknown_chapters: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class ImportCandidate:
    path: Path
    chapter_num: int
    source_order: int
    has_h1_chapter_num: bool


def project_root_from_env() -> Path:
    configured = os.environ.get("NOVEL_RAG_PROJECT_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1]


def ensure_dirs(project_dir: Path) -> None:
    for name in ("index", "logs", "outputs"):
        (project_dir / name).mkdir(parents=True, exist_ok=True)


def configure_logger(project_dir: Path) -> logging.Logger:
    ensure_dirs(project_dir)
    logger = logging.getLogger("l1_chapter_importer")
    logger.setLevel(logging.INFO)
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(project_dir / LOG_RELATIVE_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger


def close_logger(logger: logging.Logger) -> None:
    for handler in logger.handlers:
        handler.close()
    logger.handlers.clear()


def strip_frontmatter(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if not normalized.startswith("---\n"):
        return normalized
    end_marker = normalized.find("\n---\n", 4)
    if end_marker == -1:
        return normalized
    return normalized[end_marker + len("\n---\n") :]


def normalize_text(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = normalized.replace("\t", " ").replace("\u3000", " ")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def markdown_to_l1_content(markdown_text: str) -> str:
    return normalize_text(strip_frontmatter(markdown_text))


def compute_sha256(value: str | bytes) -> str:
    data = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def extract_frontmatter_value(text: str, key: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(key)}:\s*['\"]?([^'\"\n]+)['\"]?\s*$", text)
    if match:
        return match.group(1).strip()
    return None


def extract_chapter_number(text: str | None) -> int | None:
    if not text:
        return None
    match = re.search(r"第\s*(\d+)\s*章", text)
    if match:
        return int(match.group(1))
    return None


def extract_h1_title(content: str) -> str:
    for line in content.splitlines()[:12]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def extract_source_file_order(path: Path, raw_text: str = "") -> int | None:
    file_match = re.search(r"chapter[_-]0*(\d+)", path.stem, flags=re.IGNORECASE)
    if file_match:
        return int(file_match.group(1))

    order = extract_frontmatter_value(raw_text, "order")
    if order and order.isdigit():
        return int(order)
    return None


def extract_chapter_num(path: Path, raw_text: str = "") -> int | None:
    content = markdown_to_l1_content(raw_text) if raw_text else ""
    h1_num = extract_chapter_number(extract_h1_title(content))
    if h1_num is not None:
        return h1_num

    title_num = extract_chapter_number(extract_frontmatter_value(raw_text, "title"))
    if title_num is not None:
        return title_num

    filename_title_num = extract_chapter_number(path.stem)
    if filename_title_num is not None:
        return filename_title_num

    return extract_source_file_order(path, raw_text)


def build_import_candidates(files: list[Path]) -> tuple[dict[Path, ImportCandidate], dict[Path, str]]:
    candidates: dict[Path, ImportCandidate] = {}
    skipped: dict[Path, str] = {}
    main_by_chapter: dict[int, list[ImportCandidate]] = {}

    for path in files:
        raw = read_text(path)
        content = markdown_to_l1_content(raw)
        h1_num = extract_chapter_number(extract_h1_title(content))
        chapter_num = extract_chapter_num(path, raw)
        if chapter_num is None:
            continue
        source_order = extract_source_file_order(path, raw) or chapter_num
        candidate = ImportCandidate(
            path=path,
            chapter_num=chapter_num,
            source_order=source_order,
            has_h1_chapter_num=h1_num is not None,
        )
        candidates[path] = candidate
        if candidate.has_h1_chapter_num:
            main_by_chapter.setdefault(chapter_num, []).append(candidate)

    for chapter_num, duplicates in main_by_chapter.items():
        if len(duplicates) <= 1:
            continue
        keep = max(duplicates, key=lambda item: (item.source_order, item.path.name))
        for duplicate in duplicates:
            if duplicate.path == keep.path:
                continue
            skipped[duplicate.path] = (
                f"skipped duplicate H1 chapter number {chapter_num}; "
                f"kept {keep.path.name}"
            )
    return candidates, skipped


def parse_chapter_title(content: str, path: Path, raw_text: str = "") -> str:
    title = extract_frontmatter_value(raw_text, "title")
    if title:
        return title[:160]
    for line in content.splitlines()[:8]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()[:160]
    return path.stem[:160]


def metadata_by_chapter(chapter_num: int) -> dict[str, str]:
    for item in VOLUME_REGISTRY:
        if item.start_chapter <= chapter_num <= item.end_chapter:
            return {
                "volume_code": item.volume_code,
                "volume_name": item.volume_name,
                "book_code": item.book_code,
                "book_name": item.book_name,
                "core_stage": item.core_stage,
            }
    if chapter_num > 1922:
        return dict(EXTRA_METADATA)
    return dict(UNKNOWN_METADATA)


def collect_markdown_files(input_dir: Path) -> list[Path]:
    if not input_dir.exists():
        return []
    return sorted(input_dir.glob("*.md"))


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-65536;")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS chapter_registry (
            chapter_id TEXT PRIMARY KEY,
            chapter_num INTEGER NOT NULL UNIQUE,
            chapter_title_current TEXT,
            volume_code TEXT NOT NULL,
            volume_name TEXT NOT NULL,
            book_code TEXT NOT NULL,
            book_name TEXT NOT NULL,
            core_stage TEXT,
            latest_version_id TEXT,
            source_file_name TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_l1_registry_volume_book
            ON chapter_registry(volume_code, book_code);

        CREATE TABLE IF NOT EXISTS chapter_contents (
            version_id TEXT PRIMARY KEY,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            content_full_text TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content_length INTEGER NOT NULL,
            source_file_path TEXT,
            source_file_hash TEXT,
            file_mtime REAL DEFAULT 0,
            clean_rules_version TEXT NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_current INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(chapter_id) REFERENCES chapter_registry(chapter_id),
            UNIQUE(chapter_id, content_hash),
            CHECK(content_length > 0),
            CHECK(is_current IN (0, 1))
        );

        CREATE INDEX IF NOT EXISTS idx_l1_contents_chapter_current
            ON chapter_contents(chapter_id, is_current);

        CREATE INDEX IF NOT EXISTS idx_l1_contents_chapter_num
            ON chapter_contents(chapter_num);

        CREATE INDEX IF NOT EXISTS idx_l1_contents_hash
            ON chapter_contents(content_hash);

        CREATE UNIQUE INDEX IF NOT EXISTS ux_l1_one_current_version
            ON chapter_contents(chapter_id)
            WHERE is_current = 1;

        CREATE VIEW IF NOT EXISTS v_current_chapters AS
        SELECT
            r.chapter_id,
            r.chapter_num,
            r.chapter_title_current,
            r.volume_code,
            r.volume_name,
            r.book_code,
            r.book_name,
            r.core_stage,
            r.latest_version_id,
            c.content_full_text,
            c.content_hash,
            c.content_length,
            c.imported_at
        FROM chapter_registry AS r
        JOIN chapter_contents AS c
            ON c.chapter_id = r.chapter_id
           AND c.version_id = r.latest_version_id
           AND c.is_current = 1;
        """
    )


def reset_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP VIEW IF EXISTS v_current_chapters;
        DROP TABLE IF EXISTS chapter_contents;
        DROP TABLE IF EXISTS chapter_registry;
        """
    )


def get_current_version(conn: sqlite3.Connection, chapter_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT version_id, content_hash
        FROM chapter_contents
        WHERE chapter_id = ?
          AND is_current = 1
        LIMIT 1
        """,
        (chapter_id,),
    ).fetchone()


def upsert_registry(
    conn: sqlite3.Connection,
    *,
    chapter_id: str,
    chapter_num: int,
    title: str,
    metadata: dict[str, str],
    latest_version_id: str | None,
    source_file_name: str,
) -> None:
    conn.execute(
        """
        INSERT INTO chapter_registry (
            chapter_id, chapter_num, chapter_title_current, volume_code,
            volume_name, book_code, book_name, core_stage, latest_version_id,
            source_file_name, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(chapter_num) DO UPDATE SET
            chapter_title_current = excluded.chapter_title_current,
            volume_code = excluded.volume_code,
            volume_name = excluded.volume_name,
            book_code = excluded.book_code,
            book_name = excluded.book_name,
            core_stage = excluded.core_stage,
            latest_version_id = COALESCE(excluded.latest_version_id, chapter_registry.latest_version_id),
            source_file_name = excluded.source_file_name,
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            chapter_id,
            chapter_num,
            title,
            metadata["volume_code"],
            metadata["volume_name"],
            metadata["book_code"],
            metadata["book_name"],
            metadata["core_stage"],
            latest_version_id,
            source_file_name,
        ),
    )


def insert_new_version(
    conn: sqlite3.Connection,
    *,
    version_id: str,
    chapter_id: str,
    chapter_num: int,
    content: str,
    content_hash: str,
    source_file_path: str,
    source_file_hash: str,
    file_mtime: float,
) -> None:
    conn.execute(
        """
        INSERT INTO chapter_contents (
            version_id, chapter_id, chapter_num, content_full_text,
            content_hash, content_length, source_file_path, source_file_hash,
            file_mtime, clean_rules_version, is_current
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            version_id,
            chapter_id,
            chapter_num,
            content,
            content_hash,
            len(content),
            source_file_path,
            source_file_hash,
            file_mtime,
            CLEAN_RULES_VERSION,
        ),
    )


def mark_old_versions_non_current(conn: sqlite3.Connection, chapter_id: str) -> None:
    conn.execute(
        """
        UPDATE chapter_contents
        SET is_current = 0
        WHERE chapter_id = ?
          AND is_current = 1
        """,
        (chapter_id,),
    )


def update_unchanged_source(
    conn: sqlite3.Connection,
    *,
    chapter_id: str,
    source_file_name: str,
    source_file_path: str,
    source_file_hash: str,
    file_mtime: float,
) -> None:
    conn.execute(
        """
        UPDATE chapter_registry
        SET source_file_name = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE chapter_id = ?
        """,
        (source_file_name, chapter_id),
    )
    conn.execute(
        """
        UPDATE chapter_contents
        SET source_file_path = ?,
            source_file_hash = ?,
            file_mtime = ?
        WHERE chapter_id = ?
          AND is_current = 1
        """,
        (source_file_path, source_file_hash, file_mtime, chapter_id),
    )


def build_import_report(stats: ImportStats) -> str:
    lines = [
        "# L1 Import Report",
        "",
        f"- run_started_at: {stats.run_started_at}",
        f"- project_dir: {stats.project_dir}",
        f"- input_dir: {stats.input_dir}",
        f"- db_path: {stats.db_path}",
        f"- scanned_files: {stats.scanned_files}",
        f"- parsed_files: {stats.parsed_files}",
        f"- inserted_chapters: {stats.inserted_chapters}",
        f"- unchanged_chapters: {stats.unchanged_chapters}",
        f"- new_versions: {stats.new_versions}",
        f"- skipped_files: {stats.skipped_files}",
        f"- failed_files: {stats.failed_files}",
        f"- unknown_metadata_chapters: {stats.unknown_metadata_chapters}",
        f"- db_total_chapters: {stats.db_total_chapters}",
        f"- db_total_versions: {stats.db_total_versions}",
        "",
        "## Unknown Metadata Chapters",
        "",
        ", ".join(str(num) for num in stats.unknown_chapters) if stats.unknown_chapters else "None",
        "",
        "## Details",
        "",
    ]
    lines.extend(f"- {detail}" for detail in stats.details)
    return "\n".join(lines) + "\n"


def write_import_outputs(stats: ImportStats) -> None:
    stats.report_path.write_text(build_import_report(stats), encoding="utf-8")
    stats.failed_path.write_text("\n".join(stats.details) + ("\n" if stats.details else ""), encoding="utf-8")


def run_l1_import(project_dir: str | Path | None = None, *, reset_db: bool = False) -> ImportStats:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    logger = configure_logger(root)
    stats = ImportStats(
        run_started_at=datetime.now().isoformat(timespec="seconds"),
        project_dir=root,
        input_dir=root / "chapters",
        db_path=root / DB_RELATIVE_PATH,
        report_path=root / REPORT_RELATIVE_PATH,
        failed_path=root / FAILED_RELATIVE_PATH,
    )
    files = collect_markdown_files(stats.input_dir)
    stats.scanned_files = len(files)
    logger.info("L1 import started input=%s files=%s db=%s", stats.input_dir, stats.scanned_files, stats.db_path)

    conn = sqlite3.connect(stats.db_path, timeout=60, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if reset_db:
            reset_schema(conn)
        init_schema(conn)
        candidates, duplicate_skips = build_import_candidates(files)
        for path in files:
            try:
                if path in duplicate_skips:
                    stats.skipped_files += 1
                    detail = f"{path.name}: {duplicate_skips[path]}"
                    stats.details.append(detail)
                    logger.warning(detail)
                    continue

                raw = read_text(path)
                chapter_num = candidates[path].chapter_num if path in candidates else extract_chapter_num(path, raw)
                if chapter_num is None:
                    stats.skipped_files += 1
                    detail = f"{path.name}: skipped because chapter number was not found"
                    stats.details.append(detail)
                    logger.warning(detail)
                    continue

                content = markdown_to_l1_content(raw)
                if not content:
                    stats.skipped_files += 1
                    detail = f"{path.name}: skipped because content is empty after normalization"
                    stats.details.append(detail)
                    logger.warning(detail)
                    continue

                chapter_id = f"ch_{chapter_num:04d}"
                title = parse_chapter_title(content, path, raw)
                metadata = metadata_by_chapter(chapter_num)
                if metadata["volume_code"] == "UNKNOWN":
                    stats.unknown_metadata_chapters += 1
                    stats.unknown_chapters.append(chapter_num)

                content_hash = compute_sha256(content)
                version_id = f"ver_{content_hash}"
                source_file_hash = compute_sha256(path.read_bytes())
                file_mtime = path.stat().st_mtime

                conn.execute("BEGIN IMMEDIATE;")
                current = get_current_version(conn, chapter_id)
                if current is None:
                    upsert_registry(
                        conn,
                        chapter_id=chapter_id,
                        chapter_num=chapter_num,
                        title=title,
                        metadata=metadata,
                        latest_version_id=version_id,
                        source_file_name=path.name,
                    )
                    insert_new_version(
                        conn,
                        version_id=version_id,
                        chapter_id=chapter_id,
                        chapter_num=chapter_num,
                        content=content,
                        content_hash=content_hash,
                        source_file_path=str(path),
                        source_file_hash=source_file_hash,
                        file_mtime=file_mtime,
                    )
                    stats.inserted_chapters += 1
                elif current["content_hash"] == content_hash:
                    upsert_registry(
                        conn,
                        chapter_id=chapter_id,
                        chapter_num=chapter_num,
                        title=title,
                        metadata=metadata,
                        latest_version_id=None,
                        source_file_name=path.name,
                    )
                    update_unchanged_source(
                        conn,
                        chapter_id=chapter_id,
                        source_file_name=path.name,
                        source_file_path=str(path),
                        source_file_hash=source_file_hash,
                        file_mtime=file_mtime,
                    )
                    stats.unchanged_chapters += 1
                else:
                    mark_old_versions_non_current(conn, chapter_id)
                    insert_new_version(
                        conn,
                        version_id=version_id,
                        chapter_id=chapter_id,
                        chapter_num=chapter_num,
                        content=content,
                        content_hash=content_hash,
                        source_file_path=str(path),
                        source_file_hash=source_file_hash,
                        file_mtime=file_mtime,
                    )
                    upsert_registry(
                        conn,
                        chapter_id=chapter_id,
                        chapter_num=chapter_num,
                        title=title,
                        metadata=metadata,
                        latest_version_id=version_id,
                        source_file_name=path.name,
                    )
                    stats.new_versions += 1
                conn.execute("COMMIT;")
                stats.parsed_files += 1
            except Exception as exc:
                if conn.in_transaction:
                    conn.execute("ROLLBACK;")
                stats.failed_files += 1
                detail = f"{path.name}: failed with {exc}"
                stats.details.append(detail)
                logger.exception(detail)

        stats.db_total_chapters = conn.execute("SELECT COUNT(*) FROM chapter_registry").fetchone()[0]
        stats.db_total_versions = conn.execute("SELECT COUNT(*) FROM chapter_contents").fetchone()[0]
        write_import_outputs(stats)
        logger.info(
            "L1 import finished parsed=%s inserted=%s unchanged=%s new_versions=%s failed=%s",
            stats.parsed_files,
            stats.inserted_chapters,
            stats.unchanged_chapters,
            stats.new_versions,
            stats.failed_files,
        )
    finally:
        conn.close()
        close_logger(logger)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Markdown chapters into L1 SQLite storage.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--reset-db", action="store_true", help="Drop and recreate only the L1 SQLite schema before import.")
    args = parser.parse_args()
    stats = run_l1_import(args.project_dir, reset_db=args.reset_db)
    print(f"L1 import report: {stats.report_path}")
    print(f"chapters={stats.db_total_chapters} versions={stats.db_total_versions} failed={stats.failed_files}")
    raise SystemExit(1 if stats.failed_files else 0)


if __name__ == "__main__":
    main()
