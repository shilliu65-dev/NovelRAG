from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l3_disaster_ecology_candidate_extractor import load_disaster_seed


DEFAULT_SEED = Path("config") / "l3_disaster_ecology_seed.json"
DEFAULT_APPLY_REPORT_JSON = Path("outputs") / "l3_disaster_ecology_apply_report.json"
DEFAULT_APPLY_REPORT_MD = Path("outputs") / "l3_disaster_ecology_apply_report.md"
ALLOWED_HUMAN_STATUS = {None, "accepted", "rejected", "needs_more"}
L36_TABLES_DELETE_ORDER = [
    "l3_disaster_invasion_event",
    "l3_disaster_habitat_appearance",
    "l3_disaster_entity_appearance",
    "l3_disaster_zone_appearance",
    "l3_disaster_habitat_def",
    "l3_disaster_entity_alias",
    "l3_disaster_entity_def",
    "l3_disaster_zone_alias",
    "l3_disaster_zone_def",
]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def deterministic_id(prefix: str, *parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{sha256_text(raw)[:16]}"


def init_l36_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l3_disaster_zone_def (
            zone_id TEXT PRIMARY KEY,
            zone_name TEXT NOT NULL,
            zone_type TEXT NOT NULL,
            alias_json TEXT NOT NULL DEFAULT '[]',
            disaster_type TEXT,
            pollution_type TEXT,
            danger_level TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            description TEXT,
            source_seed_id TEXT,
            created_from TEXT NOT NULL DEFAULT 'seed',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_zone_alias (
            alias_id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            alias_text TEXT NOT NULL,
            alias_type TEXT NOT NULL DEFAULT 'surface',
            valid_from_chapter_num INTEGER,
            valid_to_chapter_num INTEGER,
            confidence REAL NOT NULL DEFAULT 1.0,
            note TEXT,
            FOREIGN KEY(zone_id) REFERENCES l3_disaster_zone_def(zone_id),
            UNIQUE(zone_id, alias_text, valid_from_chapter_num, valid_to_chapter_num)
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_zone_appearance (
            appearance_id TEXT PRIMARY KEY,
            zone_id TEXT NOT NULL,
            alias_text TEXT NOT NULL,
            zone_type TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            paragraph_id TEXT,
            sentence_id TEXT,
            para_start_offset INTEGER,
            para_end_offset INTEGER,
            sent_start_offset INTEGER,
            sent_end_offset INTEGER,
            evidence_text TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            appearance_type TEXT NOT NULL,
            context_role TEXT,
            confidence REAL NOT NULL DEFAULT 1.0,
            review_status TEXT NOT NULL DEFAULT 'accepted',
            source_candidate_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(zone_id) REFERENCES l3_disaster_zone_def(zone_id),
            UNIQUE(zone_id, chapter_id, paragraph_id, sentence_id, sent_start_offset, sent_end_offset, evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_entity_def (
            disaster_id TEXT PRIMARY KEY,
            disaster_name TEXT NOT NULL,
            disaster_type TEXT NOT NULL,
            alias_json TEXT NOT NULL DEFAULT '[]',
            origin_zone_id TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            is_special_case INTEGER NOT NULL DEFAULT 0,
            description TEXT,
            source_seed_id TEXT,
            created_from TEXT NOT NULL DEFAULT 'seed',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(origin_zone_id) REFERENCES l3_disaster_zone_def(zone_id)
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_entity_alias (
            alias_id TEXT PRIMARY KEY,
            disaster_id TEXT NOT NULL,
            alias_text TEXT NOT NULL,
            alias_type TEXT NOT NULL DEFAULT 'surface',
            valid_from_chapter_num INTEGER,
            valid_to_chapter_num INTEGER,
            confidence REAL NOT NULL DEFAULT 1.0,
            note TEXT,
            FOREIGN KEY(disaster_id) REFERENCES l3_disaster_entity_def(disaster_id),
            UNIQUE(disaster_id, alias_text, valid_from_chapter_num, valid_to_chapter_num)
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_entity_appearance (
            appearance_id TEXT PRIMARY KEY,
            disaster_id TEXT NOT NULL,
            disaster_name TEXT NOT NULL,
            alias_text TEXT NOT NULL,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            paragraph_id TEXT,
            sentence_id TEXT,
            para_start_offset INTEGER,
            para_end_offset INTEGER,
            sent_start_offset INTEGER,
            sent_end_offset INTEGER,
            evidence_text TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            appearance_type TEXT NOT NULL,
            context_role TEXT,
            confidence REAL NOT NULL DEFAULT 1.0,
            review_status TEXT NOT NULL DEFAULT 'accepted',
            source_candidate_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(disaster_id) REFERENCES l3_disaster_entity_def(disaster_id),
            UNIQUE(disaster_id, chapter_id, paragraph_id, sentence_id, sent_start_offset, sent_end_offset, evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_habitat_def (
            habitat_id TEXT PRIMARY KEY,
            disaster_id TEXT NOT NULL,
            parent_zone_id TEXT NOT NULL DEFAULT 'dz_gray_realm',
            habitat_name TEXT NOT NULL,
            habitat_type TEXT NOT NULL,
            alias_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'unknown',
            description TEXT,
            source_seed_id TEXT,
            created_from TEXT NOT NULL DEFAULT 'seed',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(disaster_id) REFERENCES l3_disaster_entity_def(disaster_id),
            FOREIGN KEY(parent_zone_id) REFERENCES l3_disaster_zone_def(zone_id)
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_habitat_appearance (
            appearance_id TEXT PRIMARY KEY,
            habitat_id TEXT NOT NULL,
            disaster_id TEXT NOT NULL,
            habitat_text TEXT NOT NULL,
            alias_text TEXT,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            paragraph_id TEXT,
            sentence_id TEXT,
            para_start_offset INTEGER,
            para_end_offset INTEGER,
            sent_start_offset INTEGER,
            sent_end_offset INTEGER,
            evidence_text TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            appearance_type TEXT NOT NULL,
            context_role TEXT,
            confidence REAL NOT NULL DEFAULT 1.0,
            review_status TEXT NOT NULL DEFAULT 'accepted',
            source_candidate_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(habitat_id) REFERENCES l3_disaster_habitat_def(habitat_id),
            FOREIGN KEY(disaster_id) REFERENCES l3_disaster_entity_def(disaster_id),
            UNIQUE(habitat_id, disaster_id, chapter_id, paragraph_id, sentence_id, sent_start_offset, sent_end_offset, evidence_hash)
        );
        CREATE TABLE IF NOT EXISTS l3_disaster_invasion_event (
            invasion_event_id TEXT PRIMARY KEY,
            source_disaster_id TEXT,
            source_zone_id TEXT,
            invasion_actor_text TEXT NOT NULL,
            affected_location_text TEXT NOT NULL,
            affected_location_type TEXT,
            affected_l35_location_id TEXT,
            chapter_id TEXT NOT NULL,
            chapter_num INTEGER NOT NULL,
            paragraph_id TEXT,
            sentence_id TEXT,
            para_start_offset INTEGER,
            para_end_offset INTEGER,
            sent_start_offset INTEGER,
            sent_end_offset INTEGER,
            evidence_text TEXT NOT NULL,
            evidence_hash TEXT NOT NULL,
            invasion_type TEXT NOT NULL,
            invasion_state TEXT NOT NULL,
            area_change_text TEXT,
            boundary_change_text TEXT,
            pollution_effect_text TEXT,
            confidence REAL NOT NULL DEFAULT 1.0,
            review_status TEXT NOT NULL DEFAULT 'accepted',
            source_candidate_id TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(source_disaster_id) REFERENCES l3_disaster_entity_def(disaster_id),
            FOREIGN KEY(source_zone_id) REFERENCES l3_disaster_zone_def(zone_id),
            UNIQUE(invasion_actor_text, affected_location_text, chapter_id, paragraph_id, sentence_id, sent_start_offset, sent_end_offset, evidence_hash)
        );
        """
    )


def clear_l36_tables(conn: sqlite3.Connection) -> None:
    for table in L36_TABLES_DELETE_ORDER:
        conn.execute(f"DELETE FROM {table}")


def seed_defs(conn: sqlite3.Connection, seed: dict[str, Any]) -> None:
    for zone in seed["zone_defs"]:
        conn.execute(
            """
            INSERT OR IGNORE INTO l3_disaster_zone_def
                (zone_id, zone_name, zone_type, alias_json, description, source_seed_id)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (zone["zone_id"], zone["zone_name"], zone["zone_type"], json.dumps(zone.get("aliases", []), ensure_ascii=False), zone.get("description"), seed["seed_version"]),
        )
        for alias in zone.get("aliases", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO l3_disaster_zone_alias
                    (alias_id, zone_id, alias_text)
                VALUES (?, ?, ?)
                """,
                (deterministic_id("dza", zone["zone_id"], alias), zone["zone_id"], alias),
            )
    for disaster in seed["disaster_defs"]:
        conn.execute(
            """
            INSERT OR IGNORE INTO l3_disaster_entity_def
                (disaster_id, disaster_name, disaster_type, alias_json, origin_zone_id, is_special_case, description, source_seed_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                disaster["disaster_id"],
                disaster["disaster_name"],
                disaster["disaster_type"],
                json.dumps(disaster.get("aliases", []), ensure_ascii=False),
                disaster.get("origin_zone_id"),
                1 if disaster.get("is_special_case") else 0,
                disaster.get("description"),
                seed["seed_version"],
            ),
        )
        for alias in disaster.get("aliases", []):
            conn.execute(
                """
                INSERT OR IGNORE INTO l3_disaster_entity_alias
                    (alias_id, disaster_id, alias_text)
                VALUES (?, ?, ?)
                """,
                (deterministic_id("dea", disaster["disaster_id"], alias), disaster["disaster_id"], alias),
            )
    for habitat in seed["habitat_defs"]:
        conn.execute(
            """
            INSERT OR IGNORE INTO l3_disaster_habitat_def
                (habitat_id, disaster_id, parent_zone_id, habitat_name, habitat_type, alias_json, status, description, source_seed_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                habitat["habitat_id"],
                habitat["disaster_id"],
                habitat.get("parent_zone_id", "dz_gray_realm"),
                habitat["habitat_name"],
                habitat["habitat_type"],
                json.dumps(habitat.get("aliases", []), ensure_ascii=False),
                habitat.get("status", "unknown"),
                habitat.get("description"),
                seed["seed_version"],
            ),
        )


def validate_statuses(candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"accepted": 0, "rejected": 0, "needs_more": 0, "null": 0, "invalid": 0}
    for candidate in candidates:
        status = candidate.get("human_status")
        if status not in ALLOWED_HUMAN_STATUS:
            counts["invalid"] += 1
            raise ValueError(f"Invalid human_status for candidate_id={candidate.get('candidate_id')}: {status!r}")
        if status is None:
            counts["null"] += 1
        else:
            counts[status] += 1
    return counts


def load_review_candidates(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("review_candidates", []))


def verify_backcut(conn: sqlite3.Connection, candidate: dict[str, Any]) -> tuple[str, str]:
    evidence_text = candidate["evidence_text"]
    sentence_id = candidate.get("sentence_id")
    if sentence_id:
        row = conn.execute(
            """
            SELECT sentence_text
            FROM v_l2_current_sentences
            WHERE sentence_id = ? AND chapter_id = ?
            """,
            (sentence_id, candidate["chapter_id"]),
        ).fetchone()
        if row is None:
            raise ValueError(f"Missing L2 sentence for candidate_id={candidate.get('candidate_id')}")
        start = int(candidate.get("sent_start_offset") or 0)
        end = int(candidate.get("sent_end_offset") or 0)
        if row["sentence_text"][start:end] != evidence_text:
            raise ValueError(f"Evidence backcut mismatch for candidate_id={candidate.get('candidate_id')}")
        return evidence_text, sha256_text(evidence_text)
    row = conn.execute(
        """
        SELECT para_text
        FROM v_l2_current_paragraphs
        WHERE para_id = ? AND chapter_id = ?
        """,
        (candidate.get("paragraph_id"), candidate["chapter_id"]),
    ).fetchone()
    if row is None:
        raise ValueError(f"Missing L2 paragraph for candidate_id={candidate.get('candidate_id')}")
    start = int(candidate.get("para_start_offset") or 0)
    end = int(candidate.get("para_end_offset") or 0)
    if row["para_text"][start:end] != evidence_text:
        raise ValueError(f"Evidence backcut mismatch for candidate_id={candidate.get('candidate_id')}")
    return evidence_text, sha256_text(evidence_text)


def insert_zone_appearance(conn: sqlite3.Connection, candidate: dict[str, Any], evidence_hash: str) -> int:
    before = conn.total_changes
    zone_id = candidate.get("human_zone_id") or candidate.get("zone_id")
    zone_type = candidate.get("human_zone_type") or candidate.get("zone_type")
    appearance_type = candidate.get("human_appearance_type") or candidate.get("appearance_type_guess") or "unknown"
    conn.execute(
        """
        INSERT OR IGNORE INTO l3_disaster_zone_appearance
            (appearance_id, zone_id, alias_text, zone_type, chapter_id, chapter_num, paragraph_id, sentence_id,
             para_start_offset, para_end_offset, sent_start_offset, sent_end_offset, evidence_text, evidence_hash,
             appearance_type, confidence, review_status, source_candidate_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)
        """,
        (
            deterministic_id("dzaa", candidate.get("candidate_id")),
            zone_id,
            candidate.get("alias_text") or candidate["evidence_text"],
            zone_type,
            candidate["chapter_id"],
            candidate["chapter_num"],
            candidate.get("paragraph_id"),
            candidate.get("sentence_id"),
            candidate.get("para_start_offset"),
            candidate.get("para_end_offset"),
            candidate.get("sent_start_offset"),
            candidate.get("sent_end_offset"),
            candidate["evidence_text"],
            evidence_hash,
            appearance_type,
            candidate.get("score", 1.0),
            candidate.get("candidate_id"),
        ),
    )
    return conn.total_changes - before


def insert_disaster_appearance(conn: sqlite3.Connection, candidate: dict[str, Any], evidence_hash: str) -> int:
    before = conn.total_changes
    disaster_id = candidate.get("human_disaster_id") or candidate.get("disaster_id")
    appearance_type = candidate.get("human_appearance_type") or candidate.get("appearance_type_guess") or "unknown"
    conn.execute(
        """
        INSERT OR IGNORE INTO l3_disaster_entity_appearance
            (appearance_id, disaster_id, disaster_name, alias_text, chapter_id, chapter_num, paragraph_id, sentence_id,
             para_start_offset, para_end_offset, sent_start_offset, sent_end_offset, evidence_text, evidence_hash,
             appearance_type, confidence, review_status, source_candidate_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)
        """,
        (
            deterministic_id("deaap", candidate.get("candidate_id")),
            disaster_id,
            candidate.get("disaster_name") or candidate.get("alias_text") or candidate["evidence_text"],
            candidate.get("alias_text") or candidate["evidence_text"],
            candidate["chapter_id"],
            candidate["chapter_num"],
            candidate.get("paragraph_id"),
            candidate.get("sentence_id"),
            candidate.get("para_start_offset"),
            candidate.get("para_end_offset"),
            candidate.get("sent_start_offset"),
            candidate.get("sent_end_offset"),
            candidate["evidence_text"],
            evidence_hash,
            appearance_type,
            candidate.get("score", 1.0),
            candidate.get("candidate_id"),
        ),
    )
    return conn.total_changes - before


def insert_habitat_appearance(conn: sqlite3.Connection, candidate: dict[str, Any], evidence_hash: str) -> int:
    before = conn.total_changes
    habitat_id = candidate.get("human_habitat_id") or candidate.get("habitat_id")
    disaster_id = candidate.get("human_disaster_id") or candidate.get("disaster_id")
    appearance_type = candidate.get("human_appearance_type") or candidate.get("appearance_type_guess") or "unknown"
    conn.execute(
        """
        INSERT OR IGNORE INTO l3_disaster_habitat_appearance
            (appearance_id, habitat_id, disaster_id, habitat_text, alias_text, chapter_id, chapter_num, paragraph_id, sentence_id,
             para_start_offset, para_end_offset, sent_start_offset, sent_end_offset, evidence_text, evidence_hash,
             appearance_type, confidence, review_status, source_candidate_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)
        """,
        (
            deterministic_id("dha", candidate.get("candidate_id")),
            habitat_id,
            disaster_id,
            candidate.get("human_habitat_text") or candidate.get("habitat_text") or candidate["evidence_text"],
            candidate.get("alias_text"),
            candidate["chapter_id"],
            candidate["chapter_num"],
            candidate.get("paragraph_id"),
            candidate.get("sentence_id"),
            candidate.get("para_start_offset"),
            candidate.get("para_end_offset"),
            candidate.get("sent_start_offset"),
            candidate.get("sent_end_offset"),
            candidate["evidence_text"],
            evidence_hash,
            appearance_type,
            candidate.get("score", 1.0),
            candidate.get("candidate_id"),
        ),
    )
    return conn.total_changes - before


def insert_invasion_event(conn: sqlite3.Connection, candidate: dict[str, Any], evidence_hash: str) -> int:
    before = conn.total_changes
    conn.execute(
        """
        INSERT OR IGNORE INTO l3_disaster_invasion_event
            (invasion_event_id, source_disaster_id, source_zone_id, invasion_actor_text, affected_location_text,
             affected_location_type, affected_l35_location_id, chapter_id, chapter_num, paragraph_id, sentence_id,
             para_start_offset, para_end_offset, sent_start_offset, sent_end_offset, evidence_text, evidence_hash,
             invasion_type, invasion_state, area_change_text, boundary_change_text, pollution_effect_text,
             confidence, review_status, source_candidate_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?)
        """,
        (
            deterministic_id("die", candidate.get("candidate_id")),
            candidate.get("human_source_disaster_id") or candidate.get("source_disaster_id"),
            candidate.get("human_source_zone_id") or candidate.get("source_zone_id"),
            candidate.get("human_invasion_actor_text") or candidate.get("invasion_actor_text"),
            candidate.get("human_affected_location_text") or candidate.get("affected_location_text") or "unknown",
            candidate.get("affected_location_type_guess"),
            candidate.get("human_affected_l35_location_id") or candidate.get("affected_l35_location_id"),
            candidate["chapter_id"],
            candidate["chapter_num"],
            candidate.get("paragraph_id"),
            candidate.get("sentence_id"),
            candidate.get("para_start_offset"),
            candidate.get("para_end_offset"),
            candidate.get("sent_start_offset"),
            candidate.get("sent_end_offset"),
            candidate["evidence_text"],
            evidence_hash,
            candidate.get("human_invasion_type") or candidate.get("invasion_type_guess") or "unknown",
            candidate.get("human_invasion_state") or candidate.get("invasion_state_guess") or "unknown",
            candidate.get("area_change_text"),
            candidate.get("boundary_change_text"),
            candidate.get("pollution_effect_text"),
            candidate.get("score", 1.0),
            candidate.get("candidate_id"),
        ),
    )
    return conn.total_changes - before


def markdown_report(report: dict[str, Any]) -> str:
    lines = ["# L3.6 Disaster Ecology Apply Report", ""]
    for key, value in report.items():
        lines.append(f"- {key}: {value}")
    return "\n".join(lines) + "\n"


def run_l3_apply_disaster_ecology_review(
    project_dir: str | Path | None = None,
    *,
    review_file: str | Path,
    seed_file: str | Path | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    review_path = resolve_path(root, review_file, Path(review_file))
    seed = load_disaster_seed(resolve_path(root, seed_file, DEFAULT_SEED))
    candidates = load_review_candidates(review_path)
    status_counts = validate_statuses(candidates)
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "accepted_count": status_counts["accepted"],
        "rejected_count": status_counts["rejected"],
        "needs_more_count": status_counts["needs_more"],
        "null_count": status_counts["null"],
        "inserted_zone_appearance_count": 0,
        "inserted_disaster_appearance_count": 0,
        "inserted_habitat_appearance_count": 0,
        "inserted_invasion_event_count": 0,
        "skipped_count": status_counts["rejected"] + status_counts["needs_more"] + status_counts["null"],
        "invalid_status_count": status_counts["invalid"],
        "backcut_error_count": 0,
        "integrity_check_result": "passed",
    }
    conn = sqlite3.connect(root / DB_RELATIVE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        init_l36_schema(conn)
        if rebuild:
            clear_l36_tables(conn)
        seed_defs(conn, seed)
        for candidate in candidates:
            if candidate.get("human_status") != "accepted":
                continue
            try:
                _, evidence_hash = verify_backcut(conn, candidate)
            except ValueError:
                report["backcut_error_count"] += 1
                report["integrity_check_result"] = "failed"
                continue
            ctype = candidate["candidate_type"]
            if ctype == "zone_appearance_candidate":
                report["inserted_zone_appearance_count"] += insert_zone_appearance(conn, candidate, evidence_hash)
            elif ctype == "disaster_appearance_candidate":
                report["inserted_disaster_appearance_count"] += insert_disaster_appearance(conn, candidate, evidence_hash)
            elif ctype == "habitat_appearance_candidate":
                report["inserted_habitat_appearance_count"] += insert_habitat_appearance(conn, candidate, evidence_hash)
            elif ctype == "disaster_invasion_candidate":
                report["inserted_invasion_event_count"] += insert_invasion_event(conn, candidate, evidence_hash)
        conn.commit()
    finally:
        conn.close()
    write_json(root / DEFAULT_APPLY_REPORT_JSON, report)
    (root / DEFAULT_APPLY_REPORT_MD).write_text(markdown_report(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply reviewed L3.6 disaster ecology candidates to SQLite.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--review-file", type=Path, required=True)
    parser.add_argument("--seed-file", type=Path, default=None)
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    report = run_l3_apply_disaster_ecology_review(
        project_dir=args.project_dir,
        review_file=args.review_file,
        seed_file=args.seed_file,
        rebuild=args.rebuild,
    )
    print(f"L3.6 apply accepted={report['accepted_count']} backcut_errors={report['backcut_error_count']}")


if __name__ == "__main__":
    main()
