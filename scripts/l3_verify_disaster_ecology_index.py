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
from scripts.l3_apply_disaster_ecology_review import init_l36_schema
from scripts.l3_disaster_ecology_candidate_extractor import load_disaster_seed


DEFAULT_SEED = Path("config") / "l3_disaster_ecology_seed.json"
DEFAULT_VERIFY_REPORT_JSON = Path("outputs") / "l3_disaster_ecology_verify_report.json"
DEFAULT_VERIFY_REPORT_MD = Path("outputs") / "l3_disaster_ecology_verify_report.md"
REQUIRED_TABLES = [
    "l3_disaster_zone_def",
    "l3_disaster_zone_alias",
    "l3_disaster_zone_appearance",
    "l3_disaster_entity_def",
    "l3_disaster_entity_alias",
    "l3_disaster_entity_appearance",
    "l3_disaster_habitat_def",
    "l3_disaster_habitat_appearance",
    "l3_disaster_invasion_event",
]
ALLOWED_ZONE_TYPES = {
    "gray_realm",
    "gray_tide",
    "disaster_zone",
    "polluted_space",
    "disaster_ecology",
    "disaster_lair",
    "rule_anomaly_space",
    "mental_pollution_space",
    "time_distortion_space",
    "space_distortion_space",
    "unknown_disaster_space",
}
ALLOWED_HABITAT_TYPES = {"territory", "lair", "habitat", "domain", "ecology_zone", "nest", "sealed_area", "unknown"}
ALLOWED_APPEARANCE_TYPES = {
    "direct_mention",
    "description",
    "entry",
    "exit",
    "spread",
    "collapse",
    "battlefield",
    "ecology_description",
    "rule_explanation",
    "pollution_effect",
    "boundary_description",
    "habitat_description",
    "unknown",
}
ALLOWED_INVASION_TYPES = {
    "erosion",
    "invasion",
    "attack",
    "expansion",
    "coverage",
    "pollution",
    "assimilation",
    "boundary_advance",
    "boundary_retreat",
    "occupation",
    "containment",
    "purification",
    "collapse",
    "unknown",
}
ALLOWED_INVASION_STATES = {
    "beginning",
    "invading",
    "spreading",
    "occupied",
    "covered",
    "polluted",
    "worsened",
    "contained",
    "retreated",
    "collapsed",
    "cleared",
    "unknown",
}
ORDINARY_LOCATION_TERMS = ["天枢界域", "极光界域", "红尘界域", "城市", "普通建筑"]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> int:
    return int(conn.execute(sql, params).fetchone()[0])


def count_not_in(conn: sqlite3.Connection, table: str, column: str, allowed: set[str]) -> int:
    placeholders = ",".join("?" for _ in allowed)
    return scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE {column} NOT IN ({placeholders})", tuple(sorted(allowed)))


def count_orphans(conn: sqlite3.Connection, child_table: str, child_col: str, parent_table: str, parent_col: str, *, nullable: bool = False) -> int:
    null_clause = f"AND c.{child_col} IS NOT NULL" if nullable else ""
    return scalar(
        conn,
        f"""
        SELECT COUNT(*)
        FROM {child_table} c
        LEFT JOIN {parent_table} p ON p.{parent_col} = c.{child_col}
        WHERE p.{parent_col} IS NULL {null_clause}
        """,
    )


def backcut_errors_for_table(conn: sqlite3.Connection, table: str) -> tuple[int, int]:
    errors = 0
    hash_errors = 0
    rows = conn.execute(
        f"""
        SELECT chapter_id, paragraph_id, sentence_id, sent_start_offset, sent_end_offset,
               para_start_offset, para_end_offset, evidence_text, evidence_hash
        FROM {table}
        """
    ).fetchall()
    for row in rows:
        evidence = row["evidence_text"]
        if row["sentence_id"]:
            source = conn.execute(
                "SELECT sentence_text FROM v_l2_current_sentences WHERE sentence_id=? AND chapter_id=?",
                (row["sentence_id"], row["chapter_id"]),
            ).fetchone()
            start = int(row["sent_start_offset"] or 0)
            end = int(row["sent_end_offset"] or 0)
            cut = source["sentence_text"][start:end] if source else None
        else:
            source = conn.execute(
                "SELECT para_text FROM v_l2_current_paragraphs WHERE para_id=? AND chapter_id=?",
                (row["paragraph_id"], row["chapter_id"]),
            ).fetchone()
            start = int(row["para_start_offset"] or 0)
            end = int(row["para_end_offset"] or 0)
            cut = source["para_text"][start:end] if source else None
        if cut != evidence:
            errors += 1
        if sha256_text(evidence) != row["evidence_hash"]:
            hash_errors += 1
    return errors, hash_errors


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_report(report: dict[str, Any]) -> str:
    lines = ["# L3.6 Disaster Ecology Verification Report", ""]
    for key, value in report.items():
        if key != "errors":
            lines.append(f"- {key}: {value}")
    lines.extend(["", "## Errors", ""])
    lines.extend(f"- {item}" for item in report.get("errors", [])) if report.get("errors") else lines.append("- none")
    return "\n".join(lines) + "\n"


def run_l3_disaster_ecology_verification(
    project_dir: str | Path | None = None,
    *,
    seed_file: str | Path | None = None,
    output_json: str | Path | None = None,
    output_md: str | Path | None = None,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    seed = load_disaster_seed(resolve_path(root, seed_file, DEFAULT_SEED))
    conn = sqlite3.connect(root / DB_RELATIVE_PATH)
    conn.row_factory = sqlite3.Row
    report: dict[str, Any] = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "missing_table_count": 0,
        "error_count": 0,
        "integrity_check_result": "failed",
        "backcut_error_count": 0,
        "evidence_hash_error_count": 0,
        "orphan_zone_id_count": 0,
        "orphan_disaster_id_count": 0,
        "orphan_habitat_id_count": 0,
        "invalid_zone_type_count": 0,
        "invalid_habitat_type_count": 0,
        "invalid_appearance_type_count": 0,
        "invalid_invasion_type_count": 0,
        "invalid_invasion_state_count": 0,
        "ordinary_location_leak_count": 0,
        "single_gray_alias_count": 0,
        "pass": False,
        "errors": [],
    }
    try:
        missing = [table for table in REQUIRED_TABLES if not table_exists(conn, table)]
        report["missing_table_count"] = len(missing)
        if missing:
            report["errors"].append("missing_tables=" + ",".join(missing))
        else:
            expected_disasters = {item["disaster_id"] for item in seed["disaster_defs"]}
            actual_disasters = {row["disaster_id"] for row in conn.execute("SELECT disaster_id FROM l3_disaster_entity_def")}
            if not expected_disasters.issubset(actual_disasters):
                report["errors"].append("seven_disasters_incomplete")
            chao = conn.execute("SELECT is_special_case FROM l3_disaster_entity_def WHERE disaster_id='disaster_chao'").fetchone()
            if chao is None or int(chao["is_special_case"]) != 1:
                report["errors"].append("chao_special_case_missing")
            by_id = {row["disaster_id"]: row["disaster_name"] for row in conn.execute("SELECT disaster_id, disaster_name FROM l3_disaster_entity_def")}
            if by_id.get("disaster_ji") != "忌灾" or by_id.get("disaster_ji_mie") != "寂灾":
                report["errors"].append("ji_and_ji_mie_confused")
            expected_habitats = {item["habitat_id"] for item in seed["habitat_defs"]}
            actual_habitats = {row["habitat_id"] for row in conn.execute("SELECT habitat_id FROM l3_disaster_habitat_def")}
            if not expected_habitats.issubset(actual_habitats):
                report["errors"].append("placeholder_habitats_incomplete")
            report["orphan_zone_id_count"] += count_orphans(conn, "l3_disaster_zone_alias", "zone_id", "l3_disaster_zone_def", "zone_id")
            report["orphan_zone_id_count"] += count_orphans(conn, "l3_disaster_zone_appearance", "zone_id", "l3_disaster_zone_def", "zone_id")
            report["orphan_disaster_id_count"] += count_orphans(conn, "l3_disaster_entity_alias", "disaster_id", "l3_disaster_entity_def", "disaster_id")
            report["orphan_disaster_id_count"] += count_orphans(conn, "l3_disaster_entity_appearance", "disaster_id", "l3_disaster_entity_def", "disaster_id")
            report["orphan_disaster_id_count"] += count_orphans(conn, "l3_disaster_habitat_def", "disaster_id", "l3_disaster_entity_def", "disaster_id")
            report["orphan_disaster_id_count"] += count_orphans(conn, "l3_disaster_habitat_appearance", "disaster_id", "l3_disaster_entity_def", "disaster_id")
            report["orphan_disaster_id_count"] += count_orphans(conn, "l3_disaster_invasion_event", "source_disaster_id", "l3_disaster_entity_def", "disaster_id", nullable=True)
            report["orphan_habitat_id_count"] += count_orphans(conn, "l3_disaster_habitat_appearance", "habitat_id", "l3_disaster_habitat_def", "habitat_id")
            report["orphan_zone_id_count"] += count_orphans(conn, "l3_disaster_habitat_def", "parent_zone_id", "l3_disaster_zone_def", "zone_id")
            report["orphan_zone_id_count"] += count_orphans(conn, "l3_disaster_invasion_event", "source_zone_id", "l3_disaster_zone_def", "zone_id", nullable=True)
            report["invalid_zone_type_count"] = count_not_in(conn, "l3_disaster_zone_def", "zone_type", ALLOWED_ZONE_TYPES) + count_not_in(conn, "l3_disaster_zone_appearance", "zone_type", ALLOWED_ZONE_TYPES)
            report["invalid_habitat_type_count"] = count_not_in(conn, "l3_disaster_habitat_def", "habitat_type", ALLOWED_HABITAT_TYPES)
            report["invalid_appearance_type_count"] = sum(
                count_not_in(conn, table, "appearance_type", ALLOWED_APPEARANCE_TYPES)
                for table in ("l3_disaster_zone_appearance", "l3_disaster_entity_appearance", "l3_disaster_habitat_appearance")
            )
            report["invalid_invasion_type_count"] = count_not_in(conn, "l3_disaster_invasion_event", "invasion_type", ALLOWED_INVASION_TYPES)
            report["invalid_invasion_state_count"] = count_not_in(conn, "l3_disaster_invasion_event", "invasion_state", ALLOWED_INVASION_STATES)
            report["single_gray_alias_count"] = scalar(conn, "SELECT COUNT(*) FROM l3_disaster_zone_alias WHERE alias_text='灰'")
            report["ordinary_location_leak_count"] = sum(
                scalar(conn, "SELECT COUNT(*) FROM l3_disaster_zone_def WHERE zone_name=? OR alias_json LIKE ?", (term, f"%{term}%"))
                for term in ORDINARY_LOCATION_TERMS
            )
            for table in ("l3_disaster_zone_appearance", "l3_disaster_entity_appearance", "l3_disaster_habitat_appearance", "l3_disaster_invasion_event"):
                backcut, hashes = backcut_errors_for_table(conn, table)
                report["backcut_error_count"] += backcut
                report["evidence_hash_error_count"] += hashes
                bad_review = scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE review_status!='accepted'")
                if bad_review:
                    report["errors"].append(f"{table}_review_status_not_accepted={bad_review}")
        fatal_keys = [
            "missing_table_count",
            "backcut_error_count",
            "evidence_hash_error_count",
            "orphan_zone_id_count",
            "orphan_disaster_id_count",
            "orphan_habitat_id_count",
            "invalid_zone_type_count",
            "invalid_habitat_type_count",
            "invalid_appearance_type_count",
            "invalid_invasion_type_count",
            "invalid_invasion_state_count",
            "ordinary_location_leak_count",
            "single_gray_alias_count",
        ]
        report["error_count"] = len(report["errors"]) + sum(int(report[key]) for key in fatal_keys)
        report["integrity_check_result"] = "passed" if report["error_count"] == 0 else "failed"
        report["pass"] = report["integrity_check_result"] == "passed"
    finally:
        conn.close()
    json_path = resolve_path(root, output_json, DEFAULT_VERIFY_REPORT_JSON)
    md_path = resolve_path(root, output_md, DEFAULT_VERIFY_REPORT_MD)
    write_json(json_path, report)
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.6 disaster ecology index.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--seed-file", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    args = parser.parse_args()
    report = run_l3_disaster_ecology_verification(
        project_dir=args.project_dir,
        seed_file=args.seed_file,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(f"L3.6 verifier PASS={report['pass']} errors={report['error_count']}")
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
