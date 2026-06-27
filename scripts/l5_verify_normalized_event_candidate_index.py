from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_extractor import discover_scene_block_source
from scripts.l5_event_candidate_review_exporter import object_exists
from scripts.l5_normalized_event_candidate_indexer import (
    DEFAULT_SAMPLE_CHAPTERS,
    FORBIDDEN_TABLES,
    MANIFEST_JSON,
    NORMALIZED_EVENT_TABLES,
    SAMPLE_CSV,
    SAMPLE_JSON,
    SAMPLE_REPORT,
    SEED_PATH,
    STATE_CSV,
    STATE_JSON,
    load_seed,
    run_l5_normalized_event_candidate_indexer,
)
from scripts.l5_normalized_event_candidate_reporter import run_l5_normalized_event_candidate_reporter


PASS_MESSAGE = "L5.3 normalized event candidate index FULL PASS"
FAIL_MESSAGE = "L5.3 normalized event candidate index VERIFY FAIL"
VERIFY_JSON = "l5_normalized_event_candidate_verify_report.json"
VERIFY_MD = "l5_normalized_event_candidate_verify_report.md"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def add_problem(problems: list[str], condition: bool, message: str) -> None:
    if condition:
        problems.append(message)


def add_warning(warnings: list[str], condition: bool, message: str) -> None:
    if condition:
        warnings.append(message)


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not object_exists(conn, table, "table"):
        return []
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    order_sql = ", ".join(columns)
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_sql}")]


def scene_ids(conn: sqlite3.Connection, table_name: str, id_column: str) -> set[str]:
    rows = conn.execute(f"SELECT {id_column} FROM {table_name}").fetchall()
    return {str(row[0]) for row in rows if row[0] is not None}


def validate_database(project_dir: Path, seed: dict[str, Any], problems: list[str], warnings: list[str]) -> dict[str, Any]:
    db_path = project_dir / DB_RELATIVE_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = set(NORMALIZED_EVENT_TABLES).difference(tables)
        add_problem(problems, bool(missing), f"missing L5.3 tables: {sorted(missing)}")
        forbidden = FORBIDDEN_TABLES.intersection(tables)
        add_problem(problems, bool(forbidden), f"forbidden tables exist: {sorted(forbidden)}")

        events = fetch_rows(conn, "l5_normalized_event_candidate")
        arguments = fetch_rows(conn, "l5_normalized_event_argument")
        states = fetch_rows(conn, "l5_normalized_state_change_candidate")
        evidences = fetch_rows(conn, "l5_normalized_event_evidence")
        source_maps = fetch_rows(conn, "l5_normalized_event_source_map")

        for row in events:
            event_id = row.get("normalized_event_candidate_id")
            add_problem(problems, row.get("source_layer") != "L5.1a", f"{event_id} source_layer is not L5.1a")
            add_problem(problems, row.get("normalization_status") != "normalized_candidate", f"{event_id} status is not normalized_candidate")
            add_problem(problems, int(row.get("subject_is_confirmed") or 0) != 0, f"{event_id} subject_is_confirmed is not 0")
            add_problem(problems, row.get("l5_2_seed_checksum") != seed["checksum"], f"{event_id} seed checksum mismatch")
            add_problem(problems, row.get("l5_2_event_type") not in seed["event_types"], f"{event_id} invalid L5.2 event type")
            add_problem(problems, row.get("evidence_backcut_status") != "ok", f"{event_id} evidence is not ok")
            add_problem(problems, not row.get("stable_hash"), f"{event_id} missing stable_hash")
        for row in arguments:
            argument_id = row.get("normalized_argument_id")
            add_problem(problems, int(row.get("is_confirmed") or 0) != 0, f"{argument_id} is_confirmed is not 0")
            add_problem(problems, row.get("l5_2_seed_checksum") != seed["checksum"], f"{argument_id} seed checksum mismatch")
            add_problem(problems, row.get("argument_role") not in seed["argument_roles"], f"{argument_id} invalid argument role")
        for row in states:
            state_id = row.get("normalized_state_change_candidate_id")
            add_problem(problems, int(row.get("is_confirmed") or 0) != 0, f"{state_id} is_confirmed is not 0")
            add_problem(problems, row.get("l5_2_seed_checksum") != seed["checksum"], f"{state_id} seed checksum mismatch")
            add_problem(problems, bool(seed["state_types"]) and row.get("state_change_type") not in seed["state_types"], f"{state_id} invalid state change type")
        for row in evidences:
            evidence_id = row.get("normalized_evidence_id")
            add_problem(problems, row.get("evidence_backcut_status") != "ok", f"{evidence_id} evidence_backcut_status is not ok")
            add_problem(problems, row.get("l5_2_seed_checksum") != seed["checksum"], f"{evidence_id} seed checksum mismatch")
            add_problem(problems, not row.get("evidence_text"), f"{evidence_id} missing evidence text")
        add_problem(problems, bool(events) and not evidences, "events exist without evidence rows")
        add_problem(problems, bool(events) and not source_maps, "events exist without source map rows")

        scene_source = discover_scene_block_source(conn)
        add_warning(warnings, scene_source.status == "missing_optional", "scene block source missing_optional")
        if scene_source.detected and scene_source.table_name and scene_source.id_column:
            valid_ids = scene_ids(conn, scene_source.table_name, scene_source.id_column)
            for row in events:
                scene_block_id = row.get("scene_block_id") or ""
                if scene_block_id:
                    add_problem(
                        problems,
                        scene_block_id not in valid_ids,
                        f"{row.get('normalized_event_candidate_id')} invalid scene_block_id {scene_block_id}",
                    )
        return {
            "event_count": len(events),
            "argument_count": len(arguments),
            "state_change_count": len(states),
            "evidence_count": len(evidences),
            "source_map_count": len(source_maps),
            "scene_block_source_status": scene_source.status,
            "scene_block_table_name": scene_source.table_name,
        }
    finally:
        conn.close()


def write_verify_reports(out_dir: Path, result: dict[str, Any]) -> None:
    (out_dir / VERIFY_JSON).write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    lines = [
        "# L5.3 Normalized Event Candidate Index Verification",
        "",
        f"- ok: {result['ok']}",
        f"- final_message: {result['final_message']}",
        f"- event_count: {result.get('event_count', 0)}",
        f"- scene_block_source_status: {result.get('scene_block_source_status', '')}",
        f"- scene_block_table_name: {result.get('scene_block_table_name', '')}",
        "",
        "## Problems",
        "",
    ]
    lines.extend(f"- {problem}" for problem in result["problems"]) if result["problems"] else lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in result["warnings"]) if result["warnings"] else lines.append("- none")
    lines.extend(["", result["final_message"], ""])
    (out_dir / VERIFY_MD).write_text("\n".join(lines), encoding="utf-8")


def verify_l5_normalized_event_candidate_index(
    project_dir: Path | str | None = None,
    *,
    sample_chapters: str | None = DEFAULT_SAMPLE_CHAPTERS,
    output_dir: Path | str = "outputs",
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    warnings: list[str] = []

    seed = load_seed(root)
    first = run_l5_normalized_event_candidate_indexer(root, sample_chapters=sample_chapters, output_dir=out_dir, rebuild=True)
    stable_files = [out_dir / SAMPLE_JSON, out_dir / SAMPLE_CSV, out_dir / STATE_JSON, out_dir / STATE_CSV]
    first_hashes = {path.name: sha256_file(path) for path in stable_files if path.exists()}
    second = run_l5_normalized_event_candidate_indexer(root, sample_chapters=sample_chapters, output_dir=out_dir, rebuild=True)
    second_hashes = {path.name: sha256_file(path) for path in stable_files if path.exists()}
    add_problem(problems, first_hashes != second_hashes, "stable output files are not deterministic")
    add_problem(problems, first.get("stable_output_hashes") != second.get("stable_output_hashes"), "stable DB output hashes differ")
    add_problem(problems, bool(second.get("source_mutation_detected")), "source mutation detected")
    add_problem(problems, bool(second.get("input_mutation_detected")), "input L5.1a mutation detected")

    report = run_l5_normalized_event_candidate_reporter(root, output_dir=out_dir)
    for filename in (SAMPLE_JSON, SAMPLE_CSV, SAMPLE_REPORT, STATE_JSON, STATE_CSV, MANIFEST_JSON):
        add_problem(problems, not (out_dir / filename).exists(), f"missing output file {filename}")

    db_metrics = validate_database(root, seed, problems, warnings)
    manifest = json.loads((out_dir / MANIFEST_JSON).read_text(encoding="utf-8")) if (out_dir / MANIFEST_JSON).exists() else {}
    add_problem(problems, manifest.get("l5_2_seed_checksum") not in (None, seed["checksum"]), "manifest seed checksum mismatch")
    report_text = (out_dir / SAMPLE_REPORT).read_text(encoding="utf-8") if (out_dir / SAMPLE_REPORT).exists() else ""
    for expected in (
        "detected_scene_block_source",
        "scene_block_source_status",
        "scene_block_table_name",
        "scene_block_linked_event_count",
        "event_without_scene_block_count",
    ):
        add_problem(problems, expected not in report_text, f"report missing {expected}")

    result = {
        "ok": not problems,
        "problems": problems,
        "warnings": warnings,
        "final_message": PASS_MESSAGE if not problems else FAIL_MESSAGE,
        "seed_path": str(root / SEED_PATH),
        "l5_2_seed_checksum": seed["checksum"],
        "reporter_row_counts": report.get("row_counts", {}),
        **db_metrics,
    }
    write_verify_reports(out_dir, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5.3 normalized event candidate index sample.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--sample-chapters", type=str, default=DEFAULT_SAMPLE_CHAPTERS)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    result = verify_l5_normalized_event_candidate_index(args.project_dir, sample_chapters=args.sample_chapters, output_dir=args.output_dir)
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
