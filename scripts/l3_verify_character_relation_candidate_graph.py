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

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


REPORT_RELATIVE_PATH = Path("outputs") / "l3_character_relation_candidate_graph_verify_report.md"
PASS_MESSAGE = "L3 character relation candidate graph FULL PASS"
FAIL_MESSAGE = "L3 character relation candidate graph VERIFY FAIL"
ALLOWED_SCOPES = {"same_sentence", "same_paragraph", "same_scene_block", "same_chapter", "nearby_window"}
FORBIDDEN_RELATION_TYPES = {
    "father",
    "mother",
    "lover",
    "enemy",
    "ally",
    "teacher",
    "disciple",
    "member_of",
    "leader_of",
    "betrayed",
    "killed",
    "saved",
    "controlled",
    "identity_is",
}
FORBIDDEN_FULL_TEXT_COLUMNS = {
    "chapter_text",
    "paragraph_text",
    "para_text",
    "sentence_text",
    "scene_text",
    "content_full_text",
    "full_text",
    "text",
}
REQUIRED_TABLES = (
    "l3_character_def",
    "l3_character_alias",
    "l3_character_appearance",
    "l3_character_relation_evidence",
)
REQUIRED_VIEWS = ("v_l2_current_sentences", "v_l2_current_paragraphs", "v_current_chapters")


@dataclass
class RelationGraphVerificationResult:
    ok: bool
    db_path: Path
    report_path: Path
    final_message: str = FAIL_MESSAGE
    evidence_count: int = 0
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


def add_check(result: RelationGraphVerificationResult, key: str, value: int) -> None:
    result.checks[key] = value
    if value:
        result.fatal_problems.append(f"{key}={value}")


def verify_schema(conn: sqlite3.Connection, result: RelationGraphVerificationResult) -> bool:
    missing_tables = [name for name in REQUIRED_TABLES if not object_exists(conn, name, "table")]
    missing_views = [name for name in REQUIRED_VIEWS if not object_exists(conn, name, "view")]
    if missing_tables:
        result.fatal_problems.append(f"missing_tables={', '.join(missing_tables)}")
    if missing_views:
        result.fatal_problems.append(f"missing_views={', '.join(missing_views)}")
    if missing_tables or missing_views:
        return False

    columns = {row["name"] for row in conn.execute("PRAGMA table_info(l3_character_relation_evidence)")}
    add_check(result, "forbidden_full_text_columns", len(columns.intersection(FORBIDDEN_FULL_TEXT_COLUMNS)))
    return not result.fatal_problems


def duplicate_count(rows: list[sqlite3.Row]) -> int:
    return sum(int(row["n"]) - 1 for row in rows)


def duplicate_relation_key_count(conn: sqlite3.Connection) -> int:
    duplicate_evidence_ids = duplicate_count(
        conn.execute(
            """
            SELECT evidence_id, COUNT(*) AS n
            FROM l3_character_relation_evidence
            GROUP BY evidence_id
            HAVING COUNT(*) > 1
            """
        ).fetchall()
    )
    duplicate_composite_keys = duplicate_count(
        conn.execute(
            """
            SELECT
                character_id_a,
                character_id_b,
                chapter_id,
                version_id,
                evidence_scope,
                COALESCE(scene_block_id, '') AS scene_block_id_key,
                COALESCE(para_id, '') AS para_id_key,
                COALESCE(sentence_id, '') AS sentence_id_key,
                COUNT(*) AS n
            FROM l3_character_relation_evidence
            GROUP BY
                character_id_a,
                character_id_b,
                chapter_id,
                version_id,
                evidence_scope,
                COALESCE(scene_block_id, ''),
                COALESCE(para_id, ''),
                COALESCE(sentence_id, '')
            HAVING COUNT(*) > 1
            """
        ).fetchall()
    )
    return duplicate_evidence_ids + duplicate_composite_keys


def validate_basic_values(conn: sqlite3.Connection, result: RelationGraphVerificationResult) -> None:
    result.evidence_count = scalar(conn, "SELECT COUNT(*) FROM l3_character_relation_evidence")
    placeholders = ",".join("?" for _ in ALLOWED_SCOPES)
    forbidden_placeholders = ",".join("?" for _ in FORBIDDEN_RELATION_TYPES)
    add_check(result, "invalid_evidence_scope", scalar(conn, f"SELECT COUNT(*) FROM l3_character_relation_evidence WHERE evidence_scope NOT IN ({placeholders})", tuple(sorted(ALLOWED_SCOPES))))
    add_check(result, "forbidden_relation_type", scalar(conn, f"SELECT COUNT(*) FROM l3_character_relation_evidence WHERE evidence_scope IN ({forbidden_placeholders})", tuple(sorted(FORBIDDEN_RELATION_TYPES))))
    add_check(result, "invalid_status", scalar(conn, "SELECT COUNT(*) FROM l3_character_relation_evidence WHERE status != 'candidate'"))
    add_check(result, "self_relation", scalar(conn, "SELECT COUNT(*) FROM l3_character_relation_evidence WHERE character_id_a = character_id_b"))
    add_check(result, "unordered_character_pair", scalar(conn, "SELECT COUNT(*) FROM l3_character_relation_evidence WHERE character_id_a >= character_id_b"))
    add_check(
        result,
        "missing_character_reference",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM l3_character_relation_evidence e
            LEFT JOIN l3_character_def a
              ON a.character_id = e.character_id_a
            LEFT JOIN l3_character_def b
              ON b.character_id = e.character_id_b
            WHERE a.character_id IS NULL
               OR b.character_id IS NULL
            """,
        ),
    )
    add_check(result, "duplicate_relation_evidence_key", duplicate_relation_key_count(conn))
    add_check(
        result,
        "missing_scope_location",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM l3_character_relation_evidence
            WHERE (evidence_scope = 'same_sentence' AND (sentence_id IS NULL OR para_id IS NULL))
               OR (evidence_scope = 'same_paragraph' AND para_id IS NULL)
               OR (evidence_scope = 'same_scene_block' AND scene_block_id IS NULL)
            """,
        ),
    )
    add_check(
        result,
        "invalid_mention_count",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM l3_character_relation_evidence
            WHERE mention_count_a < 1
               OR mention_count_b < 1
            """,
        ),
    )


def validate_scene_references(conn: sqlite3.Connection, result: RelationGraphVerificationResult) -> None:
    scene_evidence_count = scalar(
        conn,
        "SELECT COUNT(*) FROM l3_character_relation_evidence WHERE evidence_scope = 'same_scene_block'",
    )
    if not scene_evidence_count:
        result.checks["missing_scene_block_reference"] = 0
        return
    if not object_exists(conn, "l3_scene_blocks", "table"):
        add_check(result, "scene_block_table_missing_for_scene_evidence", scene_evidence_count)
        result.checks["missing_scene_block_reference"] = 0
        return
    add_check(
        result,
        "missing_scene_block_reference",
        scalar(
            conn,
            """
            SELECT COUNT(*)
            FROM l3_character_relation_evidence e
            LEFT JOIN l3_scene_blocks s
              ON s.scene_key = e.scene_block_id
             AND s.chapter_id = e.chapter_id
             AND s.version_id = e.version_id
            WHERE e.evidence_scope = 'same_scene_block'
              AND s.scene_key IS NULL
            """,
        ),
    )


def parse_appearance_ids(raw_value: str) -> tuple[list[str], bool]:
    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return [], False
    if not isinstance(parsed, list) or any(not isinstance(item, str) or not item for item in parsed):
        return [], False
    return parsed, True


def expected_evidence_hash(row: sqlite3.Row, ids_a: list[str], ids_b: list[str]) -> str:
    payload: dict[str, Any] = {
        "character_id_a": row["character_id_a"],
        "character_id_b": row["character_id_b"],
        "chapter_id": row["chapter_id"],
        "version_id": row["version_id"],
        "evidence_scope": row["evidence_scope"],
        "scene_block_id": row["scene_block_id"],
        "para_id": row["para_id"],
        "sentence_id": row["sentence_id"],
        "appearance_ids_a": ids_a,
        "appearance_ids_b": ids_b,
    }
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def validate_appearance_json_and_hash(conn: sqlite3.Connection, result: RelationGraphVerificationResult) -> None:
    invalid_json = 0
    missing_appearance_reference = 0
    wrong_character_appearance_reference = 0
    evidence_hash_mismatch = 0
    mention_count_mismatch = 0
    appearance_lookup = {
        row["appearance_id"]: row
        for row in conn.execute(
            """
            SELECT appearance_id, character_id, chapter_id, version_id
            FROM l3_character_appearance
            """
        )
    }
    rows = conn.execute("SELECT * FROM l3_character_relation_evidence ORDER BY evidence_id")
    for row in rows:
        ids_a, ok_a = parse_appearance_ids(row["appearance_ids_a_json"])
        ids_b, ok_b = parse_appearance_ids(row["appearance_ids_b_json"])
        if not ok_a or not ok_b:
            invalid_json += 1
            continue
        if len(ids_a) != int(row["mention_count_a"]) or len(ids_b) != int(row["mention_count_b"]):
            mention_count_mismatch += 1
        if expected_evidence_hash(row, ids_a, ids_b) != row["evidence_hash"]:
            evidence_hash_mismatch += 1
        for appearance_id in ids_a:
            appearance = appearance_lookup.get(appearance_id)
            if appearance is None:
                missing_appearance_reference += 1
                continue
            if (
                appearance["character_id"] != row["character_id_a"]
                or appearance["chapter_id"] != row["chapter_id"]
                or appearance["version_id"] != row["version_id"]
            ):
                wrong_character_appearance_reference += 1
        for appearance_id in ids_b:
            appearance = appearance_lookup.get(appearance_id)
            if appearance is None:
                missing_appearance_reference += 1
                continue
            if (
                appearance["character_id"] != row["character_id_b"]
                or appearance["chapter_id"] != row["chapter_id"]
                or appearance["version_id"] != row["version_id"]
            ):
                wrong_character_appearance_reference += 1
    add_check(result, "invalid_json", invalid_json)
    add_check(result, "missing_appearance_reference", missing_appearance_reference)
    add_check(result, "wrong_character_appearance_reference", wrong_character_appearance_reference)
    add_check(result, "mention_count_mismatch", mention_count_mismatch)
    add_check(result, "evidence_hash_mismatch", evidence_hash_mismatch)


def build_report(result: RelationGraphVerificationResult) -> str:
    lines = [
        "# L3 Character Relation Candidate Graph Verification Report",
        "",
        f"- Verified at: {datetime.now().isoformat(timespec='seconds')}",
        f"- DB path: {result.db_path}",
        f"- Final: {'PASS' if result.ok else 'FAIL'}",
        f"- evidence_count: {result.evidence_count}",
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


def run_l3_character_relation_graph_verification(project_dir: Path | str | None = None) -> RelationGraphVerificationResult:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    db_path = root / DB_RELATIVE_PATH
    report_path = root / REPORT_RELATIVE_PATH
    result = RelationGraphVerificationResult(ok=False, db_path=db_path, report_path=report_path)
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        if verify_schema(conn, result):
            validate_basic_values(conn, result)
            validate_scene_references(conn, result)
            validate_appearance_json_and_hash(conn, result)
    finally:
        conn.close()
    result.ok = not result.fatal_problems
    result.final_message = PASS_MESSAGE if result.ok else FAIL_MESSAGE
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(result), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify deterministic L3.4R character relation candidate graph.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    args = parser.parse_args()
    result = run_l3_character_relation_graph_verification(args.project_dir)
    print(f"L3 character relation candidate graph verify report: {result.report_path}")
    print(result.final_message)
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
