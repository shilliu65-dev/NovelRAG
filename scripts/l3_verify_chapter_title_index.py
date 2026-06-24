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

from scripts import l3_chapter_title_indexer as indexer


REPORT_RELATIVE_PATH = Path("outputs") / "l3_chapter_title_index_verify_report.md"
DEFAULT_EXPECTED_MAIN_CHAPTERS = 1922


@dataclass
class L3VerifyResult:
    ok: bool
    project_dir: Path
    db_path: Path
    report_path: Path
    mode: str
    main_count: int = 0
    fts_count: int = 0
    coverage_count: int = 0
    expected_main_chapters: int | None = None
    expected_indexed_chapters: int | None = None
    fts_sample_query: str = ""
    fts_sample_matches: int = 0
    l2_notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def escape_fts5_query(term: str) -> str:
    stripped = term.strip()
    if not stripped:
        return ""
    return f"\"{stripped.replace('\"', '\"\"')}\""


def scoped_clause(*, chapter_num: int | None, verify_indexed_only: bool) -> tuple[str, tuple[Any, ...]]:
    if chapter_num is not None:
        return "WHERE chapter_num = ?", (chapter_num,)
    if verify_indexed_only:
        return "", ()
    return "WHERE chapter_num BETWEEN ? AND ?", (indexer.MAIN_CHAPTER_MIN, indexer.MAIN_CHAPTER_MAX)


def count_fts_for_scope(conn: sqlite3.Connection, where_sql: str, params: tuple[Any, ...]) -> int:
    if where_sql:
        return int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM l3_chapter_title_fts f
                JOIN l3_chapter_title_index i ON i.title_index_id = f.rowid
                {where_sql.replace('WHERE', 'WHERE i.')}
                """,
                params,
            ).fetchone()[0]
        )
    return int(conn.execute("SELECT COUNT(*) FROM l3_chapter_title_fts").fetchone()[0])


def collect_sample_query(conn: sqlite3.Connection, where_sql: str, params: tuple[Any, ...]) -> tuple[str, int]:
    rows = conn.execute(
        f"""
        SELECT title_norm, title_keywords_json
        FROM l3_chapter_title_index
        {where_sql}
        ORDER BY chapter_num
        """,
        params,
    ).fetchall()
    fallback = ""
    for row in rows:
        if not fallback:
            fallback = row["title_norm"]
        try:
            keywords = json.loads(row["title_keywords_json"])
        except json.JSONDecodeError:
            continue
        for keyword in keywords:
            query = escape_fts5_query(str(keyword))
            if not query:
                continue
            count = int(
                conn.execute(
                    "SELECT COUNT(*) FROM l3_chapter_title_fts WHERE l3_chapter_title_fts MATCH ?",
                    (query,),
                ).fetchone()[0]
            )
            if count >= 1:
                return query, count
    query = escape_fts5_query(fallback)
    if not query:
        return "", 0
    count = int(
        conn.execute(
            "SELECT COUNT(*) FROM l3_chapter_title_fts WHERE l3_chapter_title_fts MATCH ?",
            (query,),
        ).fetchone()[0]
    )
    return query, count


def validate_rows(conn: sqlite3.Connection, result: L3VerifyResult, where_sql: str, params: tuple[Any, ...]) -> None:
    duplicate_chapter_num = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT chapter_num
                FROM l3_chapter_title_index
                GROUP BY chapter_num
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
    )
    duplicate_version = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT chapter_id, version_id
                FROM l3_chapter_title_index
                GROUP BY chapter_id, version_id
                HAVING COUNT(*) > 1
            )
            """
        ).fetchone()[0]
    )
    if duplicate_chapter_num or duplicate_version:
        result.errors.append("duplicate chapter_num or chapter_id/version_id exists")

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
        result.errors.append("FTS has orphan rowid")

    missing_fts = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM l3_chapter_title_index i
            LEFT JOIN l3_chapter_title_fts f ON f.rowid = i.title_index_id
            {where_sql.replace('WHERE', 'WHERE i.') if where_sql else ''}
              {'AND' if where_sql else 'WHERE'} f.rowid IS NULL
            """,
            params,
        ).fetchone()[0]
    )
    if missing_fts:
        result.errors.append("main table rows are missing FTS rows")

    rows = conn.execute(
        f"""
        SELECT *
        FROM l3_chapter_title_index
        {where_sql}
        ORDER BY chapter_num
        """,
        params,
    ).fetchall()
    for row in rows:
        if row["title_index_id"] != row["chapter_num"]:
            result.errors.append(f"title_index_id mismatch for chapter {row['chapter_num']}")
        if indexer.sha256_text(row["title_raw"]) != row["title_hash"]:
            result.errors.append(f"title_hash mismatch for chapter {row['chapter_num']}")
        try:
            keywords = json.loads(row["title_keywords_json"])
        except json.JSONDecodeError:
            result.errors.append(f"title_keywords_json invalid for chapter {row['chapter_num']}")
            continue
        if not isinstance(keywords, list) or any(not isinstance(item, str) or not item.strip() for item in keywords):
            result.errors.append(f"title_keywords_json is not a clean string array for chapter {row['chapter_num']}")

    mismatch = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM l3_chapter_title_index i
            LEFT JOIN v_current_chapters c
              ON c.chapter_id = i.chapter_id
             AND c.latest_version_id = i.version_id
             AND c.chapter_num = i.chapter_num
            {where_sql.replace('WHERE', 'WHERE i.') if where_sql else ''}
              {'AND' if where_sql else 'WHERE'} (
                    c.chapter_id IS NULL
                 OR c.chapter_title_current != i.title_raw
              )
            """,
            params,
        ).fetchone()[0]
    )
    if mismatch:
        result.errors.append("title_raw does not match current L1 title")


def build_report(result: L3VerifyResult) -> str:
    final_line = "L3 CHAPTER TITLE INDEX VERIFY PASS" if result.ok else "L3 CHAPTER TITLE INDEX VERIFY FAIL"
    lines = [
        "# L3 章节标题入口索引验证报告",
        "",
        f"- 验证时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 数据库路径：{result.db_path}",
        f"- 验证模式：{result.mode}",
        f"- 主表行数：{result.main_count}",
        f"- FTS 行数：{result.fts_count}",
        f"- 覆盖章节数：{result.coverage_count}",
        f"- expected-main-chapters：{result.expected_main_chapters if result.expected_main_chapters is not None else '无'}",
        f"- expected-indexed-chapters：{result.expected_indexed_chapters if result.expected_indexed_chapters is not None else '无'}",
        f"- FTS 查询样例：{result.fts_sample_query or '无'}",
        f"- FTS 查询命中：{result.fts_sample_matches}",
        f"- 最终结论：{'PASS' if result.ok else 'FAIL'}",
        "",
        "## L1/L2 当前状态检查",
        "",
    ]
    lines.extend(f"- {note}" for note in result.l2_notes) if result.l2_notes else lines.append("- 无")
    lines.extend(["", "## 错误列表", ""])
    lines.extend(f"- {error}" for error in result.errors) if result.errors else lines.append("- 无")
    lines.extend(["", final_line, ""])
    return "\n".join(lines)


def write_report(result: L3VerifyResult) -> None:
    result.report_path.parent.mkdir(parents=True, exist_ok=True)
    result.report_path.write_text(build_report(result), encoding="utf-8")


def run_verification(
    project_dir: Path | str | None = None,
    *,
    expected_main_chapters: int | None = DEFAULT_EXPECTED_MAIN_CHAPTERS,
    verify_indexed_only: bool = False,
    expected_indexed_chapters: int | None = None,
    chapter_num: int | None = None,
) -> L3VerifyResult:
    root = Path(project_dir).resolve() if project_dir is not None else indexer.project_root_from_env()
    indexer.ensure_dirs(root)
    db_path = root / indexer.DB_RELATIVE_PATH
    mode = "chapter-num" if chapter_num is not None else "indexed-only" if verify_indexed_only else "full"
    result = L3VerifyResult(
        ok=False,
        project_dir=root,
        db_path=db_path,
        report_path=root / REPORT_RELATIVE_PATH,
        mode=mode,
        expected_main_chapters=expected_main_chapters,
        expected_indexed_chapters=expected_indexed_chapters,
    )
    if not db_path.exists():
        result.errors.append("SQLite database does not exist")
        write_report(result)
        return result

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        try:
            result.l2_notes.extend(indexer.precheck_l2(conn))
            schema_errors = indexer.validate_l3_schema(conn)
            if schema_errors:
                result.errors.extend(schema_errors)
                raise RuntimeError("L3 schema validation failed")
            where_sql, params = scoped_clause(chapter_num=chapter_num, verify_indexed_only=verify_indexed_only)
            result.main_count = int(conn.execute(f"SELECT COUNT(*) FROM l3_chapter_title_index {where_sql}", params).fetchone()[0])
            result.fts_count = count_fts_for_scope(conn, where_sql, params)
            result.coverage_count = int(
                conn.execute(f"SELECT COUNT(DISTINCT chapter_num) FROM l3_chapter_title_index {where_sql}", params).fetchone()[0]
            )
            if chapter_num is not None:
                if result.main_count != 1 or result.fts_count != 1:
                    result.errors.append(f"chapter-num verification expected 1 row, got main={result.main_count} fts={result.fts_count}")
            elif verify_indexed_only:
                if expected_indexed_chapters is not None and result.main_count != expected_indexed_chapters:
                    result.errors.append(
                        f"indexed-only count mismatch: expected={expected_indexed_chapters} actual={result.main_count}"
                    )
                if result.main_count != result.fts_count:
                    result.errors.append("indexed-only main/FTS count mismatch")
            else:
                if expected_main_chapters is not None and result.coverage_count != expected_main_chapters:
                    result.errors.append(
                        f"full verification requires {expected_main_chapters} chapters, actual coverage={result.coverage_count}"
                    )
                if result.main_count != result.fts_count:
                    result.errors.append("full main/FTS count mismatch")
            validate_rows(conn, result, where_sql, params)
            result.fts_sample_query, result.fts_sample_matches = collect_sample_query(conn, where_sql, params)
            if not result.fts_sample_query:
                result.errors.append("no FTS sample query could be built")
            elif result.fts_sample_matches < 1:
                result.errors.append("FTS sample query returned no rows")
            result.ok = not result.errors
        except Exception as exc:  # noqa: BLE001
            if not result.errors or str(exc) not in result.errors:
                result.errors.append(str(exc))
            result.ok = False
    finally:
        conn.close()
    write_report(result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3 chapter title entry index.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--expected-main-chapters", type=int, default=DEFAULT_EXPECTED_MAIN_CHAPTERS)
    parser.add_argument("--verify-indexed-only", action="store_true")
    parser.add_argument("--expected-indexed-chapters", type=int, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        expected_main_chapters=args.expected_main_chapters,
        verify_indexed_only=args.verify_indexed_only,
        expected_indexed_chapters=args.expected_indexed_chapters,
        chapter_num=args.chapter_num,
    )
    print(f"L3 chapter title index verify report: {result.report_path}")
    print("L3 CHAPTER TITLE INDEX VERIFY PASS" if result.ok else "L3 CHAPTER TITLE INDEX VERIFY FAIL")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
