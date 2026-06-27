from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


INDEXER_VERSION = "l3_character_appearance_indexer_v1"
DEFAULT_CHARACTERS_RELATIVE_PATH = Path("outputs") / "l3_character_candidates.json"
DEFAULT_REPORT_RELATIVE_PATH = Path("outputs") / "l3_character_appearance_index_report.md"
VALID_STATUSES = {"candidate", "confirmed", "rejected"}


@dataclass(frozen=True)
class CharacterAlias:
    alias_id: str
    character_id: str
    canonical_name: str
    alias_text: str
    alias_type: str
    valid_from_chapter_num: int | None
    valid_to_chapter_num: int | None
    status: str
    evidence_ref: str | None = None


@dataclass
class CharacterIndexStats:
    db_path: Path
    report_path: Path
    started_at: str
    character_count: int = 0
    alias_count: int = 0
    appearance_count: int = 0
    skipped_rejected_count: int = 0
    warning_count: int = 0
    error_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def resolve_project_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


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


def require_source_views(conn: sqlite3.Connection) -> None:
    for view_name in ("v_l2_current_sentences", "v_l2_current_paragraphs", "v_current_chapters"):
        if not object_exists(conn, view_name, "view"):
            raise RuntimeError(f"Missing required view: {view_name}")


def init_schema(conn: sqlite3.Connection) -> None:
    require_source_views(conn)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l3_character_def (
            character_id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            status TEXT NOT NULL,
            source TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK(TRIM(canonical_name) != ''),
            CHECK(status IN ('candidate', 'confirmed', 'rejected'))
        );

        CREATE TABLE IF NOT EXISTS l3_character_alias (
            alias_id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            alias_text TEXT NOT NULL,
            alias_type TEXT NOT NULL DEFAULT 'alias',
            valid_from_chapter_num INTEGER,
            valid_to_chapter_num INTEGER,
            status TEXT NOT NULL,
            evidence_ref TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK(TRIM(alias_text) != ''),
            CHECK(status IN ('candidate', 'confirmed', 'rejected')),
            CHECK(valid_from_chapter_num IS NULL OR valid_from_chapter_num >= 1),
            CHECK(valid_to_chapter_num IS NULL OR valid_to_chapter_num >= 1),
            CHECK(
                valid_from_chapter_num IS NULL
                OR valid_to_chapter_num IS NULL
                OR valid_from_chapter_num <= valid_to_chapter_num
            )
        );

        CREATE INDEX IF NOT EXISTS idx_l3_character_alias_character
            ON l3_character_alias(character_id);

        CREATE INDEX IF NOT EXISTS idx_l3_character_alias_text
            ON l3_character_alias(alias_text);

        CREATE TABLE IF NOT EXISTS l3_character_appearance (
            appearance_id TEXT PRIMARY KEY,
            character_id TEXT NOT NULL,
            alias_id TEXT NOT NULL,
            matched_text TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            version_id TEXT NOT NULL,
            para_id TEXT NOT NULL,
            sentence_id TEXT NOT NULL,
            sentence_start_offset INTEGER NOT NULL,
            sentence_end_offset INTEGER NOT NULL,
            match_start_offset INTEGER NOT NULL,
            match_end_offset INTEGER NOT NULL,
            sentence_hash TEXT NOT NULL,
            paragraph_hash TEXT,
            l1_backcut_matched INTEGER NOT NULL,
            status TEXT NOT NULL,
            indexer_version TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK(TRIM(matched_text) != ''),
            CHECK(status IN ('candidate', 'confirmed', 'rejected')),
            CHECK(match_start_offset >= sentence_start_offset),
            CHECK(match_end_offset <= sentence_end_offset),
            CHECK(match_end_offset > match_start_offset),
            UNIQUE(character_id, alias_id, version_id, sentence_id, match_start_offset, match_end_offset)
        );

        CREATE INDEX IF NOT EXISTS idx_l3_character_appearance_character
            ON l3_character_appearance(character_id);

        CREATE INDEX IF NOT EXISTS idx_l3_character_appearance_chapter
            ON l3_character_appearance(chapter_num);

        DROP VIEW IF EXISTS v_l3_character_appearances;
        CREATE VIEW v_l3_character_appearances AS
        SELECT
            a.appearance_id,
            a.character_id,
            d.canonical_name,
            a.alias_id,
            al.alias_text,
            a.matched_text,
            al.alias_type,
            a.chapter_id,
            a.chapter_num,
            a.version_id,
            a.para_id,
            a.sentence_id,
            a.sentence_start_offset,
            a.sentence_end_offset,
            a.match_start_offset,
            a.match_end_offset,
            a.sentence_hash,
            a.paragraph_hash,
            a.l1_backcut_matched,
            a.status,
            al.valid_from_chapter_num,
            al.valid_to_chapter_num
        FROM l3_character_appearance a
        JOIN l3_character_def d ON d.character_id = a.character_id
        JOIN l3_character_alias al ON al.alias_id = a.alias_id;
        """
    )


def status_value(value: Any, default: str = "candidate") -> str:
    status = str(value or default)
    if status not in VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}")
    return status


def int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def load_character_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Character input not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def normalize_characters(payload: dict[str, Any]) -> tuple[list[dict[str, str]], list[CharacterAlias], int]:
    characters: list[dict[str, str]] = []
    aliases: list[CharacterAlias] = []
    skipped_rejected = 0
    for raw in payload.get("characters", []):
        name = str(raw.get("name") or raw.get("canonical_name") or "").strip()
        if not name:
            continue
        character_id = str(raw.get("character_id") or stable_id("char", name, length=12))
        character_status = status_value(raw.get("status"))
        if character_status == "rejected":
            skipped_rejected += 1
            continue
        characters.append(
            {
                "character_id": character_id,
                "canonical_name": name,
                "status": character_status,
                "source": str(raw.get("source") or payload.get("meta", {}).get("tool") or "manual_or_candidate"),
            }
        )

        raw_aliases = raw.get("aliases") or []
        normalized_aliases: list[dict[str, Any]] = [{"alias_text": name, "alias_type": "name", "status": character_status}]
        for item in raw_aliases:
            if isinstance(item, str):
                normalized_aliases.append({"alias_text": item, "alias_type": "alias", "status": character_status})
            elif isinstance(item, dict):
                normalized_aliases.append(item)

        seen: set[tuple[str, int | None, int | None]] = set()
        for alias in normalized_aliases:
            alias_text = str(alias.get("alias_text") or alias.get("name") or alias.get("alias") or "").strip()
            if not alias_text:
                continue
            valid_from = int_or_none(alias.get("valid_from_chapter_num"))
            valid_to = int_or_none(alias.get("valid_to_chapter_num"))
            alias_status = status_value(alias.get("status"), default=character_status)
            if alias_status == "rejected":
                skipped_rejected += 1
                continue
            dedupe_key = (alias_text, valid_from, valid_to)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            aliases.append(
                CharacterAlias(
                    alias_id=str(alias.get("alias_id") or stable_id("alias", character_id, alias_text, valid_from or "", valid_to or "", length=12)),
                    character_id=character_id,
                    canonical_name=name,
                    alias_text=alias_text,
                    alias_type=str(alias.get("alias_type") or "alias"),
                    valid_from_chapter_num=valid_from,
                    valid_to_chapter_num=valid_to,
                    status=alias_status,
                    evidence_ref=str(alias.get("evidence_ref")) if alias.get("evidence_ref") else None,
                )
            )
    return characters, aliases, skipped_rejected


def clear_l3_character_tables(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM l3_character_appearance")
    conn.execute("DELETE FROM l3_character_alias")
    conn.execute("DELETE FROM l3_character_def")


def insert_characters(conn: sqlite3.Connection, characters: list[dict[str, str]], aliases: list[CharacterAlias]) -> None:
    conn.executemany(
        """
        INSERT OR REPLACE INTO l3_character_def (
            character_id, canonical_name, status, source, updated_at
        )
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [(item["character_id"], item["canonical_name"], item["status"], item["source"]) for item in characters],
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO l3_character_alias (
            alias_id, character_id, alias_text, alias_type,
            valid_from_chapter_num, valid_to_chapter_num,
            status, evidence_ref, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        [
            (
                item.alias_id,
                item.character_id,
                item.alias_text,
                item.alias_type,
                item.valid_from_chapter_num,
                item.valid_to_chapter_num,
                item.status,
                item.evidence_ref,
            )
            for item in aliases
        ],
    )


def alias_where(alias: CharacterAlias) -> tuple[str, list[object]]:
    clauses = ["sentence_text LIKE ? ESCAPE '\\'"]
    params: list[object] = [f"%{escape_like(alias.alias_text)}%"]
    if alias.valid_from_chapter_num is not None:
        clauses.append("chapter_num >= ?")
        params.append(alias.valid_from_chapter_num)
    if alias.valid_to_chapter_num is not None:
        clauses.append("chapter_num <= ?")
        params.append(alias.valid_to_chapter_num)
    return " AND ".join(clauses), params


def paragraph_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        row["para_id"]: row["para_hash"]
        for row in conn.execute("SELECT para_id, para_hash FROM v_l2_current_paragraphs")
    }


def content_for_chapter(conn: sqlite3.Connection, chapter_id: str, version_id: str) -> str | None:
    row = conn.execute(
        """
        SELECT content_full_text
        FROM v_current_chapters
        WHERE chapter_id = ?
          AND latest_version_id = ?
        """,
        (chapter_id, version_id),
    ).fetchone()
    return None if row is None else str(row["content_full_text"])


def iter_alias_matches(text: str, alias_text: str) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(alias_text, start)
        if index < 0:
            return matches
        matches.append((index, index + len(alias_text)))
        start = index + max(1, len(alias_text))


def sentence_rows_for_alias(conn: sqlite3.Connection, alias: CharacterAlias) -> list[sqlite3.Row]:
    where, params = alias_where(alias)
    return list(
        conn.execute(
            f"""
            SELECT
                sentence_id,
                para_id,
                chapter_id,
                chapter_num,
                version_id,
                start_offset,
                end_offset,
                sentence_hash,
                sentence_text
            FROM v_l2_current_sentences
            WHERE {where}
            ORDER BY chapter_num, global_sentence_index, sentence_id
            """,
            params,
        )
    )


def all_sentence_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                sentence_id,
                para_id,
                chapter_id,
                chapter_num,
                version_id,
                start_offset,
                end_offset,
                sentence_hash,
                sentence_text
            FROM v_l2_current_sentences
            ORDER BY chapter_num, global_sentence_index, sentence_id
            """
        )
    )


def alias_in_chapter(alias: CharacterAlias, chapter_num: int) -> bool:
    if alias.valid_from_chapter_num is not None and chapter_num < alias.valid_from_chapter_num:
        return False
    if alias.valid_to_chapter_num is not None and chapter_num > alias.valid_to_chapter_num:
        return False
    return True


def build_alias_pattern(alias_texts: list[str]) -> re.Pattern[str] | None:
    unique = sorted({text for text in alias_texts if text}, key=lambda value: (-len(value), value))
    if not unique:
        return None
    return re.compile("|".join(re.escape(text) for text in unique))


def build_appearance_rows(conn: sqlite3.Connection, alias: CharacterAlias, paragraph_hash_by_id: dict[str, str]) -> list[tuple[object, ...]]:
    rows: list[tuple[object, ...]] = []
    content_cache: dict[tuple[str, str], str | None] = {}
    for row in sentence_rows_for_alias(conn, alias):
        sentence_text = str(row["sentence_text"])
        for local_start, local_end in iter_alias_matches(sentence_text, alias.alias_text):
            sentence_start = int(row["start_offset"])
            sentence_end = int(row["end_offset"])
            match_start = sentence_start + local_start
            match_end = sentence_start + local_end
            cache_key = (row["chapter_id"], row["version_id"])
            if cache_key not in content_cache:
                content_cache[cache_key] = content_for_chapter(conn, row["chapter_id"], row["version_id"])
            content = content_cache[cache_key]
            l1_backcut_matched = 0
            if content is not None:
                sentence_cut = content[sentence_start:sentence_end]
                matched_cut = content[match_start:match_end]
                l1_backcut_matched = int(sha256_text(sentence_cut) == row["sentence_hash"] and matched_cut == alias.alias_text)
            appearance_id = stable_id(
                "app",
                alias.character_id,
                alias.alias_id,
                row["version_id"],
                row["sentence_id"],
                match_start,
                match_end,
            )
            rows.append(
                (
                    appearance_id,
                    alias.character_id,
                    alias.alias_id,
                    alias.alias_text,
                    row["chapter_id"],
                    int(row["chapter_num"]),
                    row["version_id"],
                    row["para_id"],
                    row["sentence_id"],
                    sentence_start,
                    sentence_end,
                    match_start,
                    match_end,
                    row["sentence_hash"],
                    paragraph_hash_by_id.get(row["para_id"]),
                    l1_backcut_matched,
                    alias.status,
                    INDEXER_VERSION,
                )
            )
    return rows


def build_all_appearance_rows(conn: sqlite3.Connection, aliases: list[CharacterAlias], paragraph_hash_by_id: dict[str, str]) -> list[tuple[object, ...]]:
    aliases_by_text: dict[str, list[CharacterAlias]] = {}
    for alias in aliases:
        if alias.status == "rejected":
            continue
        aliases_by_text.setdefault(alias.alias_text, []).append(alias)
    pattern = build_alias_pattern(list(aliases_by_text))
    if pattern is None:
        return []

    rows: list[tuple[object, ...]] = []
    content_cache: dict[tuple[str, str], str | None] = {}
    for row in all_sentence_rows(conn):
        sentence_text = str(row["sentence_text"])
        chapter_num = int(row["chapter_num"])
        for match in pattern.finditer(sentence_text):
            matched_text = match.group(0)
            for alias in aliases_by_text.get(matched_text, []):
                if not alias_in_chapter(alias, chapter_num):
                    continue
                sentence_start = int(row["start_offset"])
                sentence_end = int(row["end_offset"])
                match_start = sentence_start + match.start()
                match_end = sentence_start + match.end()
                cache_key = (row["chapter_id"], row["version_id"])
                if cache_key not in content_cache:
                    content_cache[cache_key] = content_for_chapter(conn, row["chapter_id"], row["version_id"])
                content = content_cache[cache_key]
                l1_backcut_matched = 0
                if content is not None:
                    sentence_cut = content[sentence_start:sentence_end]
                    matched_cut = content[match_start:match_end]
                    l1_backcut_matched = int(sha256_text(sentence_cut) == row["sentence_hash"] and matched_cut == matched_text)
                appearance_id = stable_id(
                    "app",
                    alias.character_id,
                    alias.alias_id,
                    row["version_id"],
                    row["sentence_id"],
                    match_start,
                    match_end,
                )
                rows.append(
                    (
                        appearance_id,
                        alias.character_id,
                        alias.alias_id,
                        matched_text,
                        row["chapter_id"],
                        chapter_num,
                        row["version_id"],
                        row["para_id"],
                        row["sentence_id"],
                        sentence_start,
                        sentence_end,
                        match_start,
                        match_end,
                        row["sentence_hash"],
                        paragraph_hash_by_id.get(row["para_id"]),
                        l1_backcut_matched,
                        alias.status,
                        INDEXER_VERSION,
                    )
                )
    return rows


def insert_appearances(conn: sqlite3.Connection, rows: list[tuple[object, ...]]) -> None:
    conn.executemany(
        """
        INSERT OR IGNORE INTO l3_character_appearance (
            appearance_id, character_id, alias_id, matched_text,
            chapter_id, chapter_num, version_id, para_id, sentence_id,
            sentence_start_offset, sentence_end_offset,
            match_start_offset, match_end_offset,
            sentence_hash, paragraph_hash, l1_backcut_matched,
            status, indexer_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def build_report(stats: CharacterIndexStats) -> str:
    lines = [
        "# L3 Character Appearance Index Report",
        "",
        f"- Started at: {stats.started_at}",
        f"- DB path: {stats.db_path}",
        f"- character_count: {stats.character_count}",
        f"- alias_count: {stats.alias_count}",
        f"- appearance_count: {stats.appearance_count}",
        f"- skipped_rejected_count: {stats.skipped_rejected_count}",
        f"- warning_count: {stats.warning_count}",
        f"- error_count: {stats.error_count}",
        "- L1/L2 mutation: none",
        "",
    ]
    if stats.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {item}" for item in stats.warnings)
        lines.append("")
    if stats.errors:
        lines.extend(["## Errors", ""])
        lines.extend(f"- {item}" for item in stats.errors)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_l3_character_appearance_indexer(
    project_dir: Path | str | None = None,
    characters: Path | str | None = None,
    *,
    rebuild: bool = False,
    report: Path | str | None = None,
) -> CharacterIndexStats:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    characters_path = resolve_project_path(root, characters, DEFAULT_CHARACTERS_RELATIVE_PATH)
    report_path = resolve_project_path(root, report, DEFAULT_REPORT_RELATIVE_PATH)
    stats = CharacterIndexStats(
        db_path=root / DB_RELATIVE_PATH,
        report_path=report_path,
        started_at=datetime.now().isoformat(timespec="seconds"),
    )
    payload = load_character_payload(characters_path)
    characters_payload, aliases, skipped_rejected = normalize_characters(payload)
    stats.character_count = len(characters_payload)
    stats.alias_count = len(aliases)
    stats.skipped_rejected_count = skipped_rejected

    conn = sqlite3.connect(stats.db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            init_schema(conn)
            if rebuild:
                clear_l3_character_tables(conn)
            insert_characters(conn, characters_payload, aliases)
            paragraph_hash_by_id = paragraph_hashes(conn)
            all_appearance_rows = build_all_appearance_rows(conn, aliases, paragraph_hash_by_id)
            insert_appearances(conn, all_appearance_rows)
            stats.appearance_count = int(conn.execute("SELECT COUNT(*) FROM l3_character_appearance").fetchone()[0])
    except Exception as exc:  # noqa: BLE001 - report setup/index failures.
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
    parser = argparse.ArgumentParser(description="Build L3 character appearance index from temporal aliases.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--characters", type=Path, default=None, help="Character candidate/review JSON path. Defaults to outputs/l3_character_candidates.json.")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild L3.4 character tables.")
    parser.add_argument("--report", type=Path, default=None, help="Markdown report path.")
    args = parser.parse_args()
    stats = run_l3_character_appearance_indexer(args.project_dir, args.characters, rebuild=args.rebuild, report=args.report)
    print(f"L3 character appearances: {stats.appearance_count}")


if __name__ == "__main__":
    main()
