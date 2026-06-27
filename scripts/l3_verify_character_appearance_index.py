from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


REPORT_RELATIVE_PATH = Path("outputs") / "l3_character_appearance_verify_report.md"
PASS_MESSAGE = "L3 character appearance FULL PASS"
FAIL_MESSAGE = "L3 character appearance VERIFY FAIL"


@dataclass
class L3CharacterAppearanceVerificationResult:
    ok: bool
    db_path: Path
    report_path: Path
    final_message: str = FAIL_MESSAGE
    character_count: int = 0
    alias_count: int = 0
    appearance_count: int = 0
    checks: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fatal_problems: list[str] = field(default_factory=list)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def object_exists(conn: sqlite3.Connection, name: str, object_type: str) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = ?
          AND name = ?
        LIMIT 1
        """,
        (object_type, name),
    ).fetchone()
    return row is not None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[object, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def add_check(result: L3CharacterAppearanceVerificationResult, key: str, value: int) -> None:
    result.checks[key] = value
    if value:
        result.fatal_problems.append(f"{key}={value}")


def verify_schema(conn: sqlite3.Connection, result: L3CharacterAppearanceVerificationResult) -> bool:
    required_tables = ("l3_character_def", "l3_character_alias", "l3_character_appearance")
    required_views = ("v_l2_current_sentences", "v_l2_current_paragraphs", "v_current_chapters")
    missing_tables = [name for name in required_tables if not object_exists(conn, name, "table")]
    missing_views = [name for name in required_views if not object_exists(conn, name, "view")]
    if missing_tables:
        result.fatal_problems.append(f"missing_tables={', '.join(missing_tables)}")
    if missing_views:
        result.fatal_problems.append(f"missing_views={', '.join(missing_views)}")
    if result.fatal_problems:
        return False

    forbidden_text_columns = 0
    for table in required_tables:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        forbidden_text_columns += len(columns.intersection({"content_full_text", "paragraph_text", "sentence_text"}))
    add_check(result, "forbidden_full_text_columns", forbidden_text_columns)
    return not result.fatal_problems


def duplicate_appearance_count(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM l3_character_appearance
        GROUP BY character_id, alias_id, version_id, sentence_id, match_start_offset, match_end_offset
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    return sum(int(row["n"]) - 1 for row in rows)


def validate_basic_values(conn: sqlite3.Connection, result: L3CharacterAppearanceVerificationResult) -> None:
    result.character_count = scalar(conn, "SELECT COUNT(*) FROM l3_character_def")
    result.alias_count = scalar(conn, "SELECT COUNT(*) FROM l3_character_alias")
    result.appearance_count = scalar(conn, "SELECT COUNT(*) FROM l3_character_appearance")
    add_check(result, "empty_character_name", scalar(conn, "SELECT COUNT(*) FROM l3_character_def WHERE TRIM(canonical_name) = ''"))
    add_check(result, "empty_alias_text", scalar(conn, "SELECT COUNT(*) FROM l3_character_alias WHERE TRIM(alias_text) = ''"))
    add_check(result, "empty_matched_text", scalar(conn, "SELECT COUNT(*) FROM l3_character_appearance WHERE TRIM(matched_text) = ''"))
    add_check(result, "invalid_character_status", scalar(conn, "SELECT COUNT(*) FROM l3_character_def WHERE status NOT IN ('candidate', 'confirmed', 'rejected')"))
    add_check(result, "invalid_alias_status", scalar(conn, "SELECT COUNT(*) FROM l3_character_alias WHERE status NOT IN ('candidate', 'confirmed', 'rejected')"))
    add_check(result, "invalid_appearance_status", scalar(conn, "SELECT COUNT(*) FROM l3_character_appearance WHERE status NOT IN ('candidate', 'confirmed', 'rejected')"))
    add_check(
        result,
        "invalid_temporal_range",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM l3_character_alias
            WHERE valid_from_chapter_num IS NOT NULL
              AND valid_to_chapter_num IS NOT NULL
              AND valid_from_chapter_num > valid_to_chapter_num
            """,
        ),
    )
    add_check(result, "duplicate_appearance_key", duplicate_appearance_count(conn))


def validate_backcuts(conn: sqlite3.Connection, result: L3CharacterAppearanceVerificationResult) -> None:
    noncurrent_version_reference = 0
    offset_out_of_bounds = 0
    l1_backcut_mismatch = 0
    matched_text_mismatch = 0
    stored_backcut_flag_mismatch = 0
    rows = conn.execute(
        """
        SELECT
            a.*,
            c.latest_version_id,
            c.content_full_text,
            c.content_length
        FROM l3_character_appearance a
        LEFT JOIN v_current_chapters c
          ON c.chapter_id = a.chapter_id
        ORDER BY a.chapter_num, a.sentence_id, a.match_start_offset
        """
    )
    for row in rows:
        if row["content_full_text"] is None or row["latest_version_id"] != row["version_id"]:
            noncurrent_version_reference += 1
            continue
        content = row["content_full_text"]
        content_length = int(row["content_length"])
        sentence_start = int(row["sentence_start_offset"])
        sentence_end = int(row["sentence_end_offset"])
        match_start = int(row["match_start_offset"])
        match_end = int(row["match_end_offset"])
        if (
            sentence_start < 0
            or sentence_start >= sentence_end
            or sentence_end > content_length
            or match_start < sentence_start
            or match_start >= match_end
            or match_end > sentence_end
        ):
            offset_out_of_bounds += 1
            continue
        sentence_cut = content[sentence_start:sentence_end]
        match_cut = content[match_start:match_end]
        sentence_ok = sha256_text(sentence_cut) == row["sentence_hash"]
        matched_ok = match_cut == row["matched_text"]
        actual_flag = int(sentence_ok and matched_ok)
        if not sentence_ok:
            l1_backcut_mismatch += 1
        if not matched_ok:
            matched_text_mismatch += 1
        if actual_flag != int(row["l1_backcut_matched"]):
            stored_backcut_flag_mismatch += 1
    add_check(result, "noncurrent_version_reference", noncurrent_version_reference)
    add_check(result, "offset_out_of_bounds", offset_out_of_bounds)
    add_check(result, "l1_backcut_mismatch", l1_backcut_mismatch)
    add_check(result, "matched_text_mismatch", matched_text_mismatch)
    add_check(result, "stored_backcut_flag_mismatch", stored_backcut_flag_mismatch)


def detect_overlapping_alias_ranges(
    conn: sqlite3.Connection,
    result: L3CharacterAppearanceVerificationResult,
    *,
    strict_alias_overlap: bool,
) -> None:
    rows = conn.execute(
        """
        SELECT alias_text, character_id, valid_from_chapter_num, valid_to_chapter_num
        FROM l3_character_alias
        WHERE status != 'rejected'
        ORDER BY alias_text, character_id
        """
    ).fetchall()
    overlap_count = 0
    for index, left in enumerate(rows):
        for right in rows[index + 1 :]:
            if left["alias_text"] != right["alias_text"]:
                break
            if left["character_id"] == right["character_id"]:
                continue
            left_start = int(left["valid_from_chapter_num"] or 1)
            left_end = int(left["valid_to_chapter_num"] or 10**9)
            right_start = int(right["valid_from_chapter_num"] or 1)
            right_end = int(right["valid_to_chapter_num"] or 10**9)
            if left_start <= right_end and right_start <= left_end:
                overlap_count += 1
                result.warnings.append(
                    "overlapping_alias_range "
                    f"alias={left['alias_text']} characters={left['character_id']},{right['character_id']}"
                )
    if strict_alias_overlap:
        add_check(result, "overlapping_alias_range", overlap_count)
    else:
        result.checks["overlapping_alias_range"] = overlap_count


def build_report(result: L3CharacterAppearanceVerificationResult) -> str:
    lines = [
        "# L3 Character Appearance Verification Report",
        "",
        f"- Verified at: {datetime.now().isoformat(timespec='seconds')}",
        f"- DB path: {result.db_path}",
        f"- Final: {'PASS' if result.ok else 'FAIL'}",
        f"- character_count: {result.character_count}",
        f"- alias_count: {result.alias_count}",
        f"- appearance_count: {result.appearance_count}",
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
    return "\n".join(lines)


def run_l3_character_appearance_verification(
    project_dir: Path | str | None = None,
    *,
    strict_alias_overlap: bool = False,
) -> L3CharacterAppearanceVerificationResult:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    db_path = root / DB_RELATIVE_PATH
    report_path = root / REPORT_RELATIVE_PATH
    result = L3CharacterAppearanceVerificationResult(ok=False, db_path=db_path, report_path=report_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if verify_schema(conn, result):
            validate_basic_values(conn, result)
            validate_backcuts(conn, result)
            detect_overlapping_alias_ranges(conn, result, strict_alias_overlap=strict_alias_overlap)
    finally:
        conn.close()
    result.ok = not result.fatal_problems
    result.final_message = PASS_MESSAGE if result.ok else FAIL_MESSAGE
    report_path.write_text(build_report(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.4 character appearance index.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--strict-alias-overlap", action="store_true", help="Promote overlapping alias ranges from warning to failure.")
    args = parser.parse_args()
    result = run_l3_character_appearance_verification(args.project_dir, strict_alias_overlap=args.strict_alias_overlap)
    print(f"L3 character appearance verify report: {result.report_path}")
    print(result.final_message)
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
