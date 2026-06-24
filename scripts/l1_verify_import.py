from __future__ import annotations

import argparse
import gc
import hashlib
import random
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env, run_l1_import


EXPECTED_MAIN_CHAPTERS = 1922
EXPECTED_TOTAL_ENTRIES = 1928
REQUIRED_TABLES = ("chapter_registry", "chapter_contents")
REQUIRED_VIEWS = ("v_current_chapters",)
REQUIRED_REGISTRY_COLUMNS = {
    "chapter_id",
    "chapter_num",
    "chapter_title_current",
    "volume_code",
    "volume_name",
    "book_code",
    "book_name",
    "core_stage",
    "latest_version_id",
    "source_file_name",
}
REQUIRED_CONTENT_COLUMNS = {
    "version_id",
    "chapter_id",
    "chapter_num",
    "content_full_text",
    "content_hash",
    "content_length",
    "source_file_path",
    "source_file_hash",
    "file_mtime",
    "clean_rules_version",
    "is_current",
}
FORBIDDEN_MARKERS = (
    "chroma",
    "embedding",
    "chunk",
    "summary",
    "entity",
    "relationship",
    "scene",
)


@dataclass
class PreviewRow:
    chapter_id: str
    chapter_num: int
    title: str
    content_length: int
    head: str
    tail: str


@dataclass
class VerificationResult:
    ok: bool
    db_path: Path
    report_path: Path
    tables_exist: dict[str, bool] = field(default_factory=dict)
    views_exist: dict[str, bool] = field(default_factory=dict)
    missing_schema_items: list[str] = field(default_factory=list)
    total_chapters: int = 0
    current_versions: int = 0
    total_versions: int = 0
    view_current_rows: int = 0
    missing_chapter_nums: list[int] = field(default_factory=list)
    expected_main_chapters: int = EXPECTED_MAIN_CHAPTERS
    expected_total_entries: int = EXPECTED_TOTAL_ENTRIES
    main_chapter_entries: int = 0
    total_entry_mismatch: bool = False
    main_chapter_mismatch: bool = False
    multiple_current_versions: int = 0
    multiple_current_details: list[str] = field(default_factory=list)
    latest_version_mismatches: int = 0
    latest_version_details: list[str] = field(default_factory=list)
    content_length_mismatches: int = 0
    content_length_details: list[str] = field(default_factory=list)
    content_hash_mismatches: int = 0
    content_hash_details: list[str] = field(default_factory=list)
    empty_content_versions: int = 0
    empty_content_details: list[str] = field(default_factory=list)
    orphan_versions: int = 0
    orphan_version_details: list[str] = field(default_factory=list)
    unknown_metadata_chapters: list[int] = field(default_factory=list)
    main_unknown_metadata_count: int = 0
    main_unknown_metadata_details: list[str] = field(default_factory=list)
    extra_metadata_violations: list[str] = field(default_factory=list)
    title_number_mismatches: int = 0
    title_number_mismatch_details: list[str] = field(default_factory=list)
    non_chapter_title_violations: list[str] = field(default_factory=list)
    sample_previews: list[PreviewRow] = field(default_factory=list)
    idempotency_ran: bool = False
    idempotency_new_versions: int = 0
    idempotency_before_versions: int = 0
    idempotency_after_versions: int = 0
    idempotency_message: str = ""
    single_change_ran: bool = False
    single_change_new_versions: int = 0
    single_change_message: str = ""
    forbidden_logic_findings: list[str] = field(default_factory=list)
    fatal_problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def close_conn(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except sqlite3.DatabaseError:
        pass
    conn.close()
    gc.collect()


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def object_exists(conn: sqlite3.Connection, name: str, kind: str) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = ?
              AND name = ?
            LIMIT 1
            """,
            (kind, name),
        ).fetchone()
        is not None
    )


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def list_missing_chapter_nums(conn: sqlite3.Connection) -> list[int]:
    nums = [int(row["chapter_num"]) for row in conn.execute("SELECT chapter_num FROM chapter_registry ORDER BY chapter_num")]
    if not nums:
        return []
    existing = set(nums)
    return [num for num in range(nums[0], nums[-1] + 1) if num not in existing]


def extract_chapter_number(text: str | None) -> int | None:
    if not text:
        return None
    start = chr(0x7B2C)
    end = chr(0x7AE0)
    start_index = text.find(start)
    if start_index == -1:
        return None
    end_index = text.find(end, start_index + 1)
    if end_index == -1:
        return None
    middle = text[start_index + 1 : end_index]
    digits = "".join(char for char in middle if char.isdigit())
    return int(digits) if digits else None


def extract_h1(content: str | None) -> str:
    if not content:
        return ""
    for line in content.splitlines()[:12]:
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def is_obvious_extra_title(title: str) -> bool:
    keywords = ("完本感言", "感言", "后记", "附录")
    return any(keyword in title for keyword in keywords)


def compute_length_mismatches(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    details: list[str] = []
    for row in conn.execute(
        """
        SELECT chapter_id, chapter_num, version_id, content_length, content_full_text
        FROM chapter_contents
        ORDER BY chapter_num, imported_at
        """
    ):
        actual = len(row["content_full_text"])
        if int(row["content_length"]) != actual:
            details.append(f"{row['chapter_id']} version={row['version_id']} stored={row['content_length']} actual={actual}")
    return len(details), details


def compute_hash_mismatches(conn: sqlite3.Connection) -> tuple[int, list[str]]:
    details: list[str] = []
    for row in conn.execute(
        """
        SELECT chapter_id, chapter_num, version_id, content_hash, content_full_text
        FROM chapter_contents
        ORDER BY chapter_num, imported_at
        """
    ):
        expected = hashlib.sha256(row["content_full_text"].encode("utf-8")).hexdigest()
        if row["content_hash"] != expected:
            details.append(f"{row['chapter_id']} version={row['version_id']}")
    return len(details), details


def collect_sample_previews(conn: sqlite3.Connection) -> list[PreviewRow]:
    rows = list(
        conn.execute(
            """
            SELECT chapter_id, chapter_num, chapter_title_current,
                   content_full_text, content_length
            FROM v_current_chapters
            ORDER BY chapter_num
            """
        )
    )
    if len(rows) > 5:
        rows = random.Random(20260618).sample(rows, 5)
        rows.sort(key=lambda row: row["chapter_num"])
    previews: list[PreviewRow] = []
    for row in rows:
        text = row["content_full_text"]
        previews.append(
            PreviewRow(
                chapter_id=row["chapter_id"],
                chapter_num=int(row["chapter_num"]),
                title=row["chapter_title_current"] or "",
                content_length=int(row["content_length"]),
                head=text[:120].replace("\n", "\\n"),
                tail=text[-120:].replace("\n", "\\n"),
            )
        )
    return previews


def collect_database_checks(conn: sqlite3.Connection, result: VerificationResult) -> None:
    result.total_chapters = scalar(conn, "SELECT COUNT(*) FROM chapter_registry")
    result.total_versions = scalar(conn, "SELECT COUNT(*) FROM chapter_contents")
    result.current_versions = scalar(conn, "SELECT COUNT(*) FROM chapter_contents WHERE is_current = 1")
    result.view_current_rows = scalar(conn, "SELECT COUNT(*) FROM v_current_chapters")
    result.missing_chapter_nums = list_missing_chapter_nums(conn)
    result.main_chapter_entries = scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM chapter_registry
        WHERE chapter_num BETWEEN 1 AND ?
        """,
        (result.expected_main_chapters,),
    )
    result.total_entry_mismatch = result.total_chapters != result.expected_total_entries
    result.main_chapter_mismatch = result.main_chapter_entries != result.expected_main_chapters
    result.unknown_metadata_chapters = [
        int(row["chapter_num"])
        for row in conn.execute(
            """
            SELECT chapter_num
            FROM chapter_registry
            WHERE volume_code = 'UNKNOWN'
               OR volume_name = 'UNKNOWN'
               OR book_code = 'UNKNOWN'
               OR book_name = 'UNKNOWN'
            ORDER BY chapter_num
            """
        )
    ]
    main_unknown_rows = list(
        conn.execute(
            """
            SELECT chapter_id, chapter_num, chapter_title_current, source_file_name,
                   volume_code, volume_name, book_code, book_name, core_stage
            FROM chapter_registry
            WHERE chapter_num BETWEEN 1 AND ?
              AND (
                volume_code IS NULL OR volume_code = '' OR volume_code = 'UNKNOWN'
                OR volume_name IS NULL OR volume_name = '' OR volume_name = 'UNKNOWN'
                OR book_code IS NULL OR book_code = '' OR book_code = 'UNKNOWN'
                OR book_name IS NULL OR book_name = '' OR book_name = 'UNKNOWN'
                OR core_stage IS NULL OR core_stage = '' OR core_stage = 'UNKNOWN'
              )
            ORDER BY chapter_num
            """,
            (result.expected_main_chapters,),
        )
    )
    if main_unknown_rows or result.main_unknown_metadata_count == 0:
        result.main_unknown_metadata_count = len(main_unknown_rows)
        result.main_unknown_metadata_details = [
            f"{row['chapter_id']} chapter_num={row['chapter_num']} title={row['chapter_title_current']} source={row['source_file_name']}"
            for row in main_unknown_rows
        ]

    extra_rows = list(
        conn.execute(
            """
            SELECT chapter_id, chapter_num, chapter_title_current, source_file_name,
                   volume_code, book_code
            FROM chapter_registry
            WHERE chapter_num > ?
              AND (volume_code != 'EXTRA' OR book_code != 'EXTRA')
            ORDER BY chapter_num
            """,
            (result.expected_main_chapters,),
        )
    )
    if extra_rows or not result.extra_metadata_violations:
        result.extra_metadata_violations = [
            f"{row['chapter_id']} chapter_num={row['chapter_num']} volume_code={row['volume_code']} book_code={row['book_code']} "
            f"title={row['chapter_title_current']} source={row['source_file_name']}"
            for row in extra_rows
        ]

    collect_title_number_checks(conn, result)

    rows = list(
        conn.execute(
            """
            SELECT chapter_id, COUNT(*) AS current_count
            FROM chapter_contents
            WHERE is_current = 1
            GROUP BY chapter_id
            HAVING COUNT(*) > 1
            ORDER BY chapter_id
            """
        )
    )
    result.multiple_current_versions = len(rows)
    result.multiple_current_details = [f"{row['chapter_id']} current_count={row['current_count']}" for row in rows]

    rows = list(
        conn.execute(
            """
            SELECT r.chapter_id, r.chapter_num, r.latest_version_id
            FROM chapter_registry AS r
            LEFT JOIN chapter_contents AS c
              ON c.chapter_id = r.chapter_id
             AND c.version_id = r.latest_version_id
             AND c.is_current = 1
            WHERE c.version_id IS NULL
            ORDER BY r.chapter_num
            """
        )
    )
    result.latest_version_mismatches = len(rows)
    result.latest_version_details = [f"{row['chapter_id']} latest={row['latest_version_id']}" for row in rows]
    result.content_length_mismatches, result.content_length_details = compute_length_mismatches(conn)
    result.content_hash_mismatches, result.content_hash_details = compute_hash_mismatches(conn)

    rows = list(
        conn.execute(
            """
            SELECT chapter_id, chapter_num, version_id
            FROM chapter_contents
            WHERE content_full_text = ''
               OR content_full_text IS NULL
               OR content_length <= 0
            ORDER BY chapter_num
            """
        )
    )
    result.empty_content_versions = len(rows)
    result.empty_content_details = [f"{row['chapter_id']} version={row['version_id']}" for row in rows]

    rows = list(
        conn.execute(
            """
            SELECT c.chapter_id, c.chapter_num, c.version_id
            FROM chapter_contents AS c
            LEFT JOIN chapter_registry AS r
              ON r.chapter_id = c.chapter_id
            WHERE r.chapter_id IS NULL
            ORDER BY c.chapter_num
            """
        )
    )
    result.orphan_versions = len(rows)
    result.orphan_version_details = [f"{row['chapter_id']} version={row['version_id']}" for row in rows]
    result.sample_previews = collect_sample_previews(conn)


def collect_title_number_checks(conn: sqlite3.Connection, result: VerificationResult) -> None:
    mismatch_details: list[str] = []
    non_chapter_violations: list[str] = []
    for row in conn.execute(
        """
        SELECT r.chapter_id, r.chapter_num, r.chapter_title_current, r.source_file_name,
               c.content_full_text
        FROM chapter_registry AS r
        LEFT JOIN chapter_contents AS c
          ON c.chapter_id = r.chapter_id
         AND c.version_id = r.latest_version_id
         AND c.is_current = 1
        ORDER BY r.chapter_num
        """
    ):
        title = row["chapter_title_current"] or ""
        h1 = extract_h1(row["content_full_text"])
        title_num = extract_chapter_number(title)
        h1_num = extract_chapter_number(h1)
        detail = (
            f"{row['chapter_id']} | chapter_num={row['chapter_num']} | "
            f"registry title={title} | content H1={h1} | source_file_name={row['source_file_name']}"
        )
        if title_num is not None and title_num != int(row["chapter_num"]):
            mismatch_details.append(detail)
            continue
        if h1_num is not None and h1_num != int(row["chapter_num"]):
            mismatch_details.append(detail)
            continue
        if title_num is None and h1_num is None and not is_obvious_extra_title(title) and not is_obvious_extra_title(h1):
            non_chapter_violations.append(detail)
    if mismatch_details or result.title_number_mismatches == 0:
        result.title_number_mismatches = len(mismatch_details)
        result.title_number_mismatch_details = mismatch_details
    if non_chapter_violations or not result.non_chapter_title_violations:
        result.non_chapter_title_violations = non_chapter_violations


def collect_schema_checks(conn: sqlite3.Connection, result: VerificationResult) -> None:
    if result.tables_exist.get("chapter_registry"):
        missing = REQUIRED_REGISTRY_COLUMNS - table_columns(conn, "chapter_registry")
        result.missing_schema_items.extend(f"chapter_registry.{name}" for name in sorted(missing))
    if result.tables_exist.get("chapter_contents"):
        missing = REQUIRED_CONTENT_COLUMNS - table_columns(conn, "chapter_contents")
        result.missing_schema_items.extend(f"chapter_contents.{name}" for name in sorted(missing))


def run_idempotency_check(project_dir: Path, result: VerificationResult) -> None:
    before_conn = sqlite3.connect(project_dir / DB_RELATIVE_PATH)
    try:
        before = scalar(before_conn, "SELECT COUNT(*) FROM chapter_contents")
    finally:
        close_conn(before_conn)
    stats = run_l1_import(project_dir)
    after_conn = sqlite3.connect(project_dir / DB_RELATIVE_PATH)
    try:
        after = scalar(after_conn, "SELECT COUNT(*) FROM chapter_contents")
    finally:
        close_conn(after_conn)

    result.idempotency_ran = True
    result.idempotency_before_versions = before
    result.idempotency_after_versions = after
    result.idempotency_new_versions = after - before
    if result.idempotency_new_versions == 0 and stats.inserted_chapters == 0 and stats.new_versions == 0 and stats.failed_files == 0:
        result.idempotency_message = "PASS: repeated import did not add versions"
    else:
        result.idempotency_message = (
            "FAIL: repeated import changed version state "
            f"inserted={stats.inserted_chapters} new_versions={stats.new_versions} failed={stats.failed_files} "
            f"delta={result.idempotency_new_versions}"
        )


def copy_sample_project(source_project: Path, target_project: Path) -> list[Path]:
    source_files = sorted((source_project / "chapters").glob("*.md"))[:3]
    chapters_target = target_project / "chapters"
    chapters_target.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in source_files:
        destination = chapters_target / source.name
        shutil.copy2(source, destination)
        copied.append(destination)
    return copied


def run_single_change_check(project_dir: Path, result: VerificationResult) -> None:
    source_files = sorted((project_dir / "chapters").glob("*.md"))
    if len(source_files) < 3:
        result.single_change_message = "SKIP: fewer than 3 Markdown chapters are available"
        return

    with tempfile.TemporaryDirectory() as temp_dir:
        sample_root = Path(temp_dir)
        copied = copy_sample_project(project_dir, sample_root)
        first = run_l1_import(sample_root)
        before_conn = sqlite3.connect(sample_root / DB_RELATIVE_PATH)
        try:
            before_total = scalar(before_conn, "SELECT COUNT(*) FROM chapter_contents")
            before_by_chapter = dict(
                before_conn.execute(
                    """
                    SELECT chapter_id, COUNT(*) AS version_count
                    FROM chapter_contents
                    GROUP BY chapter_id
                    """
                ).fetchall()
            )
        finally:
            close_conn(before_conn)

        copied[1].write_text(copied[1].read_text(encoding="utf-8") + "\n\nL1 temporary verification mutation.\n", encoding="utf-8")
        second = run_l1_import(sample_root)
        after_conn = sqlite3.connect(sample_root / DB_RELATIVE_PATH)
        try:
            after_total = scalar(after_conn, "SELECT COUNT(*) FROM chapter_contents")
            after_by_chapter = dict(
                after_conn.execute(
                    """
                    SELECT chapter_id, COUNT(*) AS version_count
                    FROM chapter_contents
                    GROUP BY chapter_id
                    """
                ).fetchall()
            )
            current_count = scalar(after_conn, "SELECT COUNT(*) FROM chapter_contents WHERE is_current = 1")
        finally:
            close_conn(after_conn)

    result.single_change_ran = True
    result.single_change_new_versions = after_total - before_total
    changed = [key for key, after_count in after_by_chapter.items() if after_count != before_by_chapter.get(key)]
    if (
        first.inserted_chapters == 3
        and second.new_versions == 1
        and result.single_change_new_versions == 1
        and len(changed) == 1
        and current_count == 3
    ):
        result.single_change_message = f"PASS: one temporary chapter change created one new version ({changed[0]})"
    else:
        result.single_change_message = (
            "FAIL: temporary single-change version rule failed "
            f"first_inserted={first.inserted_chapters} second_new={second.new_versions} "
            f"delta={result.single_change_new_versions} changed={changed} current={current_count}"
        )


def collect_forbidden_logic_findings() -> list[str]:
    importer_path = Path(__file__).with_name("l1_chapter_importer.py")
    text = importer_path.read_text(encoding="utf-8", errors="ignore").lower()
    findings = [marker for marker in FORBIDDEN_MARKERS if marker in text]
    return [f"l1_chapter_importer.py contains marker: {marker}" for marker in findings]


def evaluate_result(result: VerificationResult) -> None:
    if not all(result.tables_exist.values()) or not all(result.views_exist.values()):
        result.fatal_problems.append("missing required table or view")
    if result.missing_schema_items:
        result.fatal_problems.append("missing required schema items")
    if result.total_chapters <= 0:
        result.fatal_problems.append("chapter_registry has no chapters")
    if result.total_entry_mismatch:
        result.warnings.append(
            f"total entry count mismatch: expected {result.expected_total_entries}, actual {result.total_chapters}"
        )
    if result.main_chapter_mismatch:
        result.fatal_problems.append(
            f"main chapter count mismatch: expected {result.expected_main_chapters}, actual {result.main_chapter_entries}"
        )
    if result.current_versions != result.total_chapters:
        result.fatal_problems.append("current version count does not equal chapter count")
    if result.total_versions < result.current_versions:
        result.fatal_problems.append("total version count is lower than current version count")
    if result.view_current_rows != result.total_chapters:
        result.fatal_problems.append("v_current_chapters row count does not equal chapter count")
    if result.missing_chapter_nums:
        result.fatal_problems.append("chapter numbers are not continuous")
    if result.multiple_current_versions:
        result.fatal_problems.append("multiple current versions exist")
    if result.latest_version_mismatches:
        result.fatal_problems.append("latest_version_id does not point to current version")
    if result.content_length_mismatches:
        result.fatal_problems.append("content_length mismatch")
    if result.content_hash_mismatches:
        result.fatal_problems.append("content_hash mismatch")
    if result.empty_content_versions:
        result.fatal_problems.append("empty content exists")
    if result.orphan_versions:
        result.fatal_problems.append("orphan content versions exist")
    if result.main_unknown_metadata_count:
        result.fatal_problems.append("main chapter metadata is UNKNOWN")
    if result.extra_metadata_violations:
        result.fatal_problems.append("extra entries do not use EXTRA metadata")
    if result.title_number_mismatches:
        result.fatal_problems.append("chapter title number mismatch")
    if result.non_chapter_title_violations:
        result.fatal_problems.append("non-chapter title is not a recognized extra entry")
    if not result.idempotency_ran or result.idempotency_new_versions != 0:
        result.fatal_problems.append("idempotency check failed")
    if not result.single_change_ran or result.single_change_new_versions != 1 or not result.single_change_message.startswith("PASS"):
        result.fatal_problems.append("single chapter change check failed")
    if result.forbidden_logic_findings:
        result.fatal_problems.append("forbidden downstream logic marker found")
    if result.unknown_metadata_chapters:
        result.warnings.append(f"UNKNOWN metadata chapters: {len(result.unknown_metadata_chapters)}")
    result.ok = not result.fatal_problems


def run_verification(
    project_dir: str | Path | None = None,
    *,
    expected_main_chapters: int = EXPECTED_MAIN_CHAPTERS,
    expected_total_entries: int = EXPECTED_TOTAL_ENTRIES,
) -> VerificationResult:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    db_path = root / DB_RELATIVE_PATH
    report_path = root / "outputs" / "l1_verify_report.md"
    result = VerificationResult(
        ok=False,
        db_path=db_path,
        report_path=report_path,
        expected_main_chapters=expected_main_chapters,
        expected_total_entries=expected_total_entries,
    )

    if not db_path.exists():
        result.fatal_problems.append("SQLite database does not exist")
        result.forbidden_logic_findings = collect_forbidden_logic_findings()
        evaluate_result(result)
        report_path.write_text(build_report(result), encoding="utf-8")
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        result.tables_exist = {name: object_exists(conn, name, "table") for name in REQUIRED_TABLES}
        result.views_exist = {name: object_exists(conn, name, "view") for name in REQUIRED_VIEWS}
        collect_schema_checks(conn, result)
        if all(result.tables_exist.values()) and all(result.views_exist.values()) and not result.missing_schema_items:
            collect_database_checks(conn, result)
    finally:
        close_conn(conn)

    if all(result.tables_exist.values()) and all(result.views_exist.values()) and not result.missing_schema_items:
        run_idempotency_check(root, result)
        run_single_change_check(root, result)
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            collect_database_checks(conn, result)
        finally:
            close_conn(conn)

    result.forbidden_logic_findings = collect_forbidden_logic_findings()
    evaluate_result(result)
    report_path.write_text(build_report(result), encoding="utf-8")
    return result


def compact(values: list[int] | list[str], empty: str = "none", limit: int = 80) -> str:
    if not values:
        return empty
    shown = values[:limit]
    suffix = "" if len(values) <= limit else f" ... (+{len(values) - limit} more)"
    return ", ".join(str(value) for value in shown) + suffix


def build_report(result: VerificationResult) -> str:
    final_line = "L1 VERIFY PASS，可以进入 L2 原文坐标层。" if result.ok else "L1 VERIFY FAIL，禁止进入 L2。"
    lines = [
        "# L1 原文证据层验证报告",
        "",
        f"- 验证时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 数据库路径：{result.db_path}",
        f"- 最终结论：{'PASS' if result.ok else 'FAIL'}",
        "",
        "## 表和视图",
        "",
    ]
    for name, exists in result.tables_exist.items():
        lines.append(f"- 表 {name}：{'存在' if exists else '缺失'}")
    for name, exists in result.views_exist.items():
        lines.append(f"- 视图 {name}：{'存在' if exists else '缺失'}")
    lines.extend(
        [
            f"- schema 缺失项：{compact(result.missing_schema_items)}",
            "",
            "## 核心统计",
            "",
            f"- 预期正文编号章节数：{result.expected_main_chapters}",
            f"- 预期总条目数：{result.expected_total_entries}",
            f"- 总章节数：{result.total_chapters}",
            f"- 正文编号章节数：{result.main_chapter_entries}",
            f"- 当前版本数：{result.current_versions}",
            f"- 历史版本总数：{result.total_versions}",
            f"- v_current_chapters 行数：{result.view_current_rows}",
            f"- 缺失章节号清单：{compact(result.missing_chapter_nums)}",
            "",
            "## 异常检查",
            "",
            f"- 多 current 版本异常：{result.multiple_current_versions}",
            f"- latest_version_id 异常：{result.latest_version_mismatches}",
            f"- content_length 异常：{result.content_length_mismatches}",
            f"- content_hash 异常：{result.content_hash_mismatches}",
            f"- 空内容异常：{result.empty_content_versions}",
            f"- orphan version 异常：{result.orphan_versions}",
            f"- 未匹配卷册元数据章节：{len(result.unknown_metadata_chapters)}",
            f"- 正文 UNKNOWN 元数据异常：{result.main_unknown_metadata_count}",
            f"- EXTRA 元数据异常：{len(result.extra_metadata_violations)}",
            f"- 标题章号异常：{result.title_number_mismatches}",
            f"- 非正文标题异常：{len(result.non_chapter_title_violations)}",
            f"- 禁止逻辑标记：{compact(result.forbidden_logic_findings)}",
            "",
            "## 异常详情",
            "",
            f"- 多 current：{compact(result.multiple_current_details)}",
            f"- latest_version_id：{compact(result.latest_version_details)}",
            f"- content_length：{compact(result.content_length_details)}",
            f"- content_hash：{compact(result.content_hash_details)}",
            f"- 空内容：{compact(result.empty_content_details)}",
            f"- orphan version：{compact(result.orphan_version_details)}",
            f"- 未匹配卷册元数据章节号：{compact(result.unknown_metadata_chapters)}",
            f"- 正文 UNKNOWN 元数据：{compact(result.main_unknown_metadata_details)}",
            f"- EXTRA 元数据：{compact(result.extra_metadata_violations)}",
            f"- 标题章号：{compact(result.title_number_mismatch_details)}",
            f"- 非正文标题：{compact(result.non_chapter_title_violations)}",
            "",
            "## 随机抽样 5 章预览",
            "",
            "| chapter_id | chapter_num | title | content_length | head | tail |",
            "| --- | ---: | --- | ---: | --- | --- |",
        ]
    )
    if result.sample_previews:
        for item in result.sample_previews:
            lines.append(f"| {item.chapter_id} | {item.chapter_num} | {item.title} | {item.content_length} | {item.head} | {item.tail} |")
    else:
        lines.append("| none | 0 | none | 0 | none | none |")
    lines.extend(
        [
            "",
            "## 幂等测试结果",
            "",
            f"- 是否执行：{'是' if result.idempotency_ran else '否'}",
            f"- 重导入前版本数：{result.idempotency_before_versions}",
            f"- 重导入后版本数：{result.idempotency_after_versions}",
            f"- 新增版本数：{result.idempotency_new_versions}",
            f"- 结果：{result.idempotency_message or 'not run'}",
            "",
            "## 单章修改版本测试结果",
            "",
            f"- 是否执行：{'是' if result.single_change_ran else '否'}",
            f"- 新增版本数：{result.single_change_new_versions}",
            f"- 结果：{result.single_change_message or 'not run'}",
            "",
            "## Warnings",
            "",
            compact(result.warnings),
            "",
            "## 必须修复的问题",
            "",
        ]
    )
    if result.fatal_problems:
        lines.extend(f"- {problem}" for problem in result.fatal_problems)
    else:
        lines.append("- 无")
    lines.extend(["", final_line, ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L1 SQLite acceptance criteria.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--expected-main-chapters", type=int, default=EXPECTED_MAIN_CHAPTERS)
    parser.add_argument("--expected-total-entries", type=int, default=EXPECTED_TOTAL_ENTRIES)
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        expected_main_chapters=args.expected_main_chapters,
        expected_total_entries=args.expected_total_entries,
    )
    print(f"L1 verify report: {result.report_path}")
    print("L1 VERIFY PASS，可以进入 L2 原文坐标层。" if result.ok else "L1 VERIFY FAIL，禁止进入 L2。")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
