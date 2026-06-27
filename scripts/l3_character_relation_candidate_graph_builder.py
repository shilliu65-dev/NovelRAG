from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from itertools import combinations
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


BUILDER_VERSION = "l3_character_relation_candidate_graph_builder_v1"
REPORT_RELATIVE_PATH = Path("outputs") / "l3_character_relation_candidate_graph_report.md"
ALLOWED_SCOPES = {"same_sentence", "same_paragraph", "same_scene_block", "same_chapter", "nearby_window"}
DEFAULT_SCOPES = ("same_sentence", "same_paragraph")
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


@dataclass(frozen=True)
class Appearance:
    appearance_id: str
    character_id: str
    chapter_id: str
    chapter_num: int
    version_id: str
    para_id: str
    sentence_id: str
    match_start_offset: int
    match_end_offset: int


@dataclass
class RelationBuildStats:
    db_path: Path
    report_path: Path
    started_at: str
    scopes: list[str]
    evidence_count: int = 0
    scope_counts: dict[str, int] = field(default_factory=dict)
    warning_count: int = 0
    error_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 20) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


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


def require_sources(conn: sqlite3.Connection) -> None:
    for name, object_type in (
        ("l3_character_def", "table"),
        ("l3_character_alias", "table"),
        ("l3_character_appearance", "table"),
        ("v_l2_current_sentences", "view"),
        ("v_l2_current_paragraphs", "view"),
        ("v_current_chapters", "view"),
    ):
        if not object_exists(conn, name, object_type):
            raise RuntimeError(f"Missing required {object_type}: {name}")


def init_schema(conn: sqlite3.Connection) -> None:
    require_sources(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l3_character_relation_evidence (
            evidence_id TEXT PRIMARY KEY,
            character_id_a TEXT NOT NULL,
            character_id_b TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            version_id TEXT NOT NULL,
            evidence_scope TEXT NOT NULL,
            scene_block_id TEXT,
            para_id TEXT,
            sentence_id TEXT,
            appearance_ids_a_json TEXT NOT NULL,
            appearance_ids_b_json TEXT NOT NULL,
            mention_count_a INTEGER NOT NULL,
            mention_count_b INTEGER NOT NULL,
            offset_distance_min INTEGER,
            evidence_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status = 'candidate'),
            source TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK(character_id_a < character_id_b),
            CHECK(evidence_scope IN ('same_sentence', 'same_paragraph', 'same_scene_block', 'same_chapter', 'nearby_window')),
            UNIQUE(
                character_id_a,
                character_id_b,
                chapter_id,
                version_id,
                evidence_scope,
                scene_block_id,
                para_id,
                sentence_id
            )
        );

        CREATE INDEX IF NOT EXISTS idx_l3_relation_evidence_a
            ON l3_character_relation_evidence(character_id_a);

        CREATE INDEX IF NOT EXISTS idx_l3_relation_evidence_b
            ON l3_character_relation_evidence(character_id_b);

        CREATE INDEX IF NOT EXISTS idx_l3_relation_evidence_scope
            ON l3_character_relation_evidence(evidence_scope);

        CREATE INDEX IF NOT EXISTS idx_l3_relation_evidence_chapter
            ON l3_character_relation_evidence(chapter_num);
        """
    )


def parse_scopes(value: str | None) -> list[str]:
    if value is None or not value.strip():
        return list(DEFAULT_SCOPES)
    scopes = [item.strip() for item in value.split(",") if item.strip()]
    if scopes == ["all"]:
        return sorted(ALLOWED_SCOPES)
    unknown = sorted(set(scopes) - ALLOWED_SCOPES)
    if unknown:
        raise ValueError(f"Unknown relation evidence scopes: {', '.join(unknown)}")
    return scopes


def fetch_appearances(conn: sqlite3.Connection) -> list[Appearance]:
    rows = conn.execute(
        """
        SELECT
            appearance_id,
            character_id,
            chapter_id,
            chapter_num,
            version_id,
            para_id,
            sentence_id,
            match_start_offset,
            match_end_offset
        FROM l3_character_appearance
        WHERE status != 'rejected'
          AND l1_backcut_matched = 1
        ORDER BY chapter_num, match_start_offset, appearance_id
        """
    )
    return [
        Appearance(
            appearance_id=row["appearance_id"],
            character_id=row["character_id"],
            chapter_id=row["chapter_id"],
            chapter_num=int(row["chapter_num"]),
            version_id=row["version_id"],
            para_id=row["para_id"],
            sentence_id=row["sentence_id"],
            match_start_offset=int(row["match_start_offset"]),
            match_end_offset=int(row["match_end_offset"]),
        )
        for row in rows
    ]


def group_by(items: list[Appearance], key_fn: Any) -> dict[tuple[object, ...], list[Appearance]]:
    grouped: dict[tuple[object, ...], list[Appearance]] = defaultdict(list)
    for item in items:
        grouped[key_fn(item)].append(item)
    return grouped


def offset_distance_min(left: list[Appearance], right: list[Appearance]) -> int | None:
    if not left or not right:
        return None
    return min(abs(a.match_start_offset - b.match_start_offset) for a in left for b in right)


def ordered_ids(items: list[Appearance]) -> list[str]:
    return [item.appearance_id for item in sorted(items, key=lambda value: (value.match_start_offset, value.appearance_id))]


def evidence_hash(payload: dict[str, Any]) -> str:
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def build_relation_row(
    *,
    scope: str,
    chapter_id: str,
    chapter_num: int,
    version_id: str,
    character_id_a: str,
    character_id_b: str,
    appearances_a: list[Appearance],
    appearances_b: list[Appearance],
    scene_block_id: str | None = None,
    para_id: str | None = None,
    sentence_id: str | None = None,
) -> tuple[object, ...]:
    if character_id_a > character_id_b:
        character_id_a, character_id_b = character_id_b, character_id_a
        appearances_a, appearances_b = appearances_b, appearances_a
    ids_a = ordered_ids(appearances_a)
    ids_b = ordered_ids(appearances_b)
    payload = {
        "character_id_a": character_id_a,
        "character_id_b": character_id_b,
        "chapter_id": chapter_id,
        "version_id": version_id,
        "evidence_scope": scope,
        "scene_block_id": scene_block_id,
        "para_id": para_id,
        "sentence_id": sentence_id,
        "appearance_ids_a": ids_a,
        "appearance_ids_b": ids_b,
    }
    digest = evidence_hash(payload)
    evidence_id = stable_id(
        "rel",
        scope,
        chapter_id,
        version_id,
        scene_block_id or "",
        para_id or "",
        sentence_id or "",
        character_id_a,
        character_id_b,
    )
    return (
        evidence_id,
        character_id_a,
        character_id_b,
        chapter_id,
        chapter_num,
        version_id,
        scope,
        scene_block_id,
        para_id,
        sentence_id,
        json.dumps(ids_a, ensure_ascii=False),
        json.dumps(ids_b, ensure_ascii=False),
        len(ids_a),
        len(ids_b),
        offset_distance_min(appearances_a, appearances_b),
        digest,
        "candidate",
        BUILDER_VERSION,
    )


def rows_for_group(
    *,
    scope: str,
    appearances: list[Appearance],
    scene_block_id: str | None = None,
    para_id: str | None = None,
    sentence_id: str | None = None,
) -> list[tuple[object, ...]]:
    by_character: dict[str, list[Appearance]] = defaultdict(list)
    for appearance in appearances:
        by_character[appearance.character_id].append(appearance)
    if len(by_character) < 2:
        return []
    sample = appearances[0]
    rows: list[tuple[object, ...]] = []
    for character_id_a, character_id_b in combinations(sorted(by_character), 2):
        rows.append(
            build_relation_row(
                scope=scope,
                chapter_id=sample.chapter_id,
                chapter_num=sample.chapter_num,
                version_id=sample.version_id,
                character_id_a=character_id_a,
                character_id_b=character_id_b,
                appearances_a=by_character[character_id_a],
                appearances_b=by_character[character_id_b],
                scene_block_id=scene_block_id,
                para_id=para_id,
                sentence_id=sentence_id,
            )
        )
    return rows


def build_same_sentence_rows(appearances: list[Appearance]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for (_chapter_id, _version_id, sentence_id), group in group_by(
        appearances,
        lambda item: (item.chapter_id, item.version_id, item.sentence_id),
    ).items():
        para_id = group[0].para_id
        rows.extend(rows_for_group(scope="same_sentence", appearances=group, para_id=para_id, sentence_id=str(sentence_id)))
    return rows


def build_same_paragraph_rows(appearances: list[Appearance]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for (_chapter_id, _version_id, para_id), group in group_by(
        appearances,
        lambda item: (item.chapter_id, item.version_id, item.para_id),
    ).items():
        rows.extend(rows_for_group(scope="same_paragraph", appearances=group, para_id=str(para_id)))
    return rows


def build_same_chapter_rows(appearances: list[Appearance]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    for _key, group in group_by(appearances, lambda item: (item.chapter_id, item.version_id)).items():
        rows.extend(rows_for_group(scope="same_chapter", appearances=group))
    return rows


def scene_blocks_available(conn: sqlite3.Connection) -> bool:
    return object_exists(conn, "l3_scene_blocks", "table")


def build_same_scene_rows(conn: sqlite3.Connection, appearances: list[Appearance]) -> list[tuple[object, ...]]:
    if not scene_blocks_available(conn):
        return []
    scenes = conn.execute(
        """
        SELECT scene_key, chapter_id, version_id, start_offset, end_offset
        FROM l3_scene_blocks
        ORDER BY chapter_num, scene_index_in_chapter
        """
    ).fetchall()
    grouped: dict[str, list[Appearance]] = defaultdict(list)
    by_chapter: dict[tuple[str, str], list[Appearance]] = defaultdict(list)
    for appearance in appearances:
        by_chapter[(appearance.chapter_id, appearance.version_id)].append(appearance)
    for scene in scenes:
        start = int(scene["start_offset"])
        end = int(scene["end_offset"])
        for appearance in by_chapter.get((scene["chapter_id"], scene["version_id"]), []):
            if start <= appearance.match_start_offset < end:
                grouped[str(scene["scene_key"])].append(appearance)
    rows: list[tuple[object, ...]] = []
    for scene_key, group in grouped.items():
        rows.extend(rows_for_group(scope="same_scene_block", appearances=group, scene_block_id=scene_key))
    return rows


def build_nearby_window_rows(appearances: list[Appearance], nearby_window_chars: int) -> list[tuple[object, ...]]:
    aggregate: dict[tuple[str, str, str, str], dict[str, list[Appearance]]] = defaultdict(lambda: defaultdict(list))
    for (_chapter_id, _version_id), group in group_by(appearances, lambda item: (item.chapter_id, item.version_id)).items():
        ordered = sorted(group, key=lambda item: (item.match_start_offset, item.appearance_id))
        for index, left in enumerate(ordered):
            for right in ordered[index + 1 :]:
                distance = right.match_start_offset - left.match_start_offset
                if distance > nearby_window_chars:
                    break
                if left.character_id == right.character_id:
                    continue
                character_a, character_b = sorted((left.character_id, right.character_id))
                key = (left.chapter_id, left.version_id, character_a, character_b)
                aggregate[key][left.character_id].append(left)
                aggregate[key][right.character_id].append(right)

    rows: list[tuple[object, ...]] = []
    for (_chapter_id, _version_id, character_a, character_b), by_character in aggregate.items():
        appearances_a = by_character[character_a]
        appearances_b = by_character[character_b]
        sample = (appearances_a or appearances_b)[0]
        rows.append(
            build_relation_row(
                scope="nearby_window",
                chapter_id=sample.chapter_id,
                chapter_num=sample.chapter_num,
                version_id=sample.version_id,
                character_id_a=character_a,
                character_id_b=character_b,
                appearances_a=appearances_a,
                appearances_b=appearances_b,
            )
        )
    return rows


def insert_rows(conn: sqlite3.Connection, rows: list[tuple[object, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR IGNORE INTO l3_character_relation_evidence (
            evidence_id, character_id_a, character_id_b,
            chapter_id, chapter_num, version_id, evidence_scope,
            scene_block_id, para_id, sentence_id,
            appearance_ids_a_json, appearance_ids_b_json,
            mention_count_a, mention_count_b, offset_distance_min,
            evidence_hash, status, source
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def clear_relation_rows(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM l3_character_relation_evidence")


def build_report(stats: RelationBuildStats) -> str:
    lines = [
        "# L3 Character Relation Candidate Graph Report",
        "",
        f"- Started at: {stats.started_at}",
        f"- DB path: {stats.db_path}",
        f"- scopes: {', '.join(stats.scopes)}",
        f"- evidence_count: {stats.evidence_count}",
        f"- warning_count: {stats.warning_count}",
        f"- error_count: {stats.error_count}",
        "- relation truth inference: none",
        "- L1/L2 mutation: none",
        "",
        "## Scope Counts",
        "",
    ]
    if stats.scope_counts:
        lines.extend(f"- {scope}: {count}" for scope, count in sorted(stats.scope_counts.items()))
    else:
        lines.append("- none")
    if stats.warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- {item}" for item in stats.warnings)
    if stats.errors:
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in stats.errors)
    return "\n".join(lines).rstrip() + "\n"


def run_l3_character_relation_graph_build(
    project_dir: Path | str | None = None,
    *,
    rebuild: bool = False,
    scopes: list[str] | None = None,
    nearby_window_chars: int = 120,
    report: Path | str | None = None,
) -> RelationBuildStats:
    selected_scopes = list(scopes or DEFAULT_SCOPES)
    unknown = sorted(set(selected_scopes) - ALLOWED_SCOPES)
    if unknown:
        raise ValueError(f"Unknown relation evidence scopes: {', '.join(unknown)}")
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    report_path = Path(report) if report is not None else REPORT_RELATIVE_PATH
    if not report_path.is_absolute():
        report_path = root / report_path
    stats = RelationBuildStats(
        db_path=root / DB_RELATIVE_PATH,
        report_path=report_path,
        started_at=datetime.now().isoformat(timespec="seconds"),
        scopes=selected_scopes,
    )

    conn = sqlite3.connect(stats.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            init_schema(conn)
            if rebuild:
                clear_relation_rows(conn)
            appearances = fetch_appearances(conn)
            if "same_sentence" in selected_scopes:
                insert_rows(conn, build_same_sentence_rows(appearances))
            if "same_paragraph" in selected_scopes:
                insert_rows(conn, build_same_paragraph_rows(appearances))
            if "same_scene_block" in selected_scopes:
                insert_rows(conn, build_same_scene_rows(conn, appearances))
            if "same_chapter" in selected_scopes:
                insert_rows(conn, build_same_chapter_rows(appearances))
            if "nearby_window" in selected_scopes:
                insert_rows(conn, build_nearby_window_rows(appearances, nearby_window_chars))
            stats.evidence_count = int(conn.execute("SELECT COUNT(*) FROM l3_character_relation_evidence").fetchone()[0])
            stats.scope_counts = {
                row["evidence_scope"]: int(row["n"])
                for row in conn.execute(
                    """
                    SELECT evidence_scope, COUNT(*) AS n
                    FROM l3_character_relation_evidence
                    GROUP BY evidence_scope
                    """
                )
            }
    except Exception as exc:  # noqa: BLE001 - report setup/build failures.
        stats.error_count += 1
        stats.errors.append(str(exc))
    finally:
        conn.close()

    stats.warning_count = len(stats.warnings)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(build_report(stats), encoding="utf-8")
    if stats.error_count:
        raise RuntimeError("; ".join(stats.errors))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Build deterministic L3.4R character relation candidate graph.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild relation evidence table.")
    parser.add_argument(
        "--scopes",
        type=str,
        default=",".join(DEFAULT_SCOPES),
        help="Comma-separated scopes or 'all'. Default: same_sentence,same_paragraph.",
    )
    parser.add_argument("--nearby-window-chars", type=int, default=120, help="Character offset window for nearby_window scope.")
    parser.add_argument("--report", type=Path, default=None, help="Markdown report output path.")
    args = parser.parse_args()
    stats = run_l3_character_relation_graph_build(
        args.project_dir,
        rebuild=args.rebuild,
        scopes=parse_scopes(args.scopes),
        nearby_window_chars=args.nearby_window_chars,
        report=args.report,
    )
    print(f"L3 character relation evidence: {stats.evidence_count}")


if __name__ == "__main__":
    main()
