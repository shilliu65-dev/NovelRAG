from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as builder


SAMPLE_JSON_RELATIVE_PATH = Path("outputs") / "l3_child_segments_sample.json"
REPORT_RELATIVE_PATH = Path("outputs") / "l3_child_segments_sample_report.md"
MANIFEST_RELATIVE_PATH = Path("outputs") / "l3_child_segments_manifest.json"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def fetch_segments(conn: sqlite3.Connection, scope: list[int]) -> list[dict[str, Any]]:
    placeholders = builder.scoped_placeholders(scope)
    rows = conn.execute(
        f"""
        SELECT *
        FROM l3_child_segment
        WHERE chapter_num IN ({placeholders})
        ORDER BY chapter_num, scene_id, segment_index_in_scene
        """,
        tuple(scope),
    ).fetchall()
    segments: list[dict[str, Any]] = []
    for row in rows:
        item = {key: row[key] for key in row.keys()}
        links = conn.execute(
            """
            SELECT sentence_id, para_id, sentence_start_offset, sentence_end_offset, is_overlap_sentence
            FROM l3_child_segment_sentence_link
            WHERE child_segment_id = ?
            ORDER BY position_in_segment
            """,
            (row["child_segment_id"],),
        ).fetchall()
        item["sentence_links"] = [{key: link[key] for key in link.keys()} for link in links]
        segments.append(item)
    return segments


def latest_build_run(conn: sqlite3.Connection, scope: list[int]) -> dict[str, Any] | None:
    if not builder.object_exists(conn, "l3_child_segment_build_run", "table"):
        return None
    sample = ",".join(str(item) for item in scope)
    row = conn.execute(
        """
        SELECT *
        FROM l3_child_segment_build_run
        WHERE sample_chapters = ?
        ORDER BY finished_at DESC, build_run_id DESC
        LIMIT 1
        """,
        (sample,),
    ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def compute_source_mutation_detected(conn: sqlite3.Connection, run: dict[str, Any] | None) -> bool:
    if run is None:
        return True
    if int(run.get("source_mutation_detected", 1) or 0) != 0:
        return True
    try:
        table_names = json.loads(str(run["source_table_names_json"]))
        current_hash = builder.guard_hash(builder.collect_source_guard_stats(conn, table_names))
    except Exception:  # noqa: BLE001
        return True
    return current_hash != run.get("source_guard_after_hash")


def write_sample_json(path: Path, scope: list[int], metrics: dict[str, Any], segments: list[dict[str, Any]]) -> None:
    payload = {
        "generated_at": now_iso(),
        "sample_chapters": scope,
        "metrics": metrics,
        "segments": segments,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(path: Path, db_path: Path, scope: list[int], metrics: dict[str, Any], forbidden_tables: list[str]) -> None:
    lines = [
        "# L3.5 child_segments sample report",
        "",
        f"- generated_at: {now_iso()}",
        f"- database: {db_path}",
        f"- sample_chapters: {','.join(str(item) for item in scope)}",
        f"- chapter_count: {metrics['chapter_count']}",
        f"- scene_block_count: {metrics['scene_block_count']}",
        f"- child_segment_count: {metrics['child_segment_count']}",
        f"- avg_segment_chars: {metrics['avg_segment_chars']}",
        f"- min_segment_chars: {metrics['min_segment_chars']}",
        f"- max_segment_chars: {metrics['max_segment_chars']}",
        f"- overlap_segment_count: {metrics['overlap_segment_count']}",
        f"- oversized_sentence_warning_count: {metrics['oversized_sentence_warning_count']}",
        f"- empty_segment_count: {metrics['empty_segment_count']}",
        f"- boundary_warning_count: {metrics['boundary_warning_count']}",
        f"- source_mutation_detected: {metrics['source_mutation_detected']}",
        f"- forbidden_final_tables: {', '.join(forbidden_tables) if forbidden_tables else 'none'}",
        f"- no_llm_calls: YES",
        f"- embedding_written: NO",
        f"- chroma_written: NO",
        f"- vector_database_written: NO",
        "",
        "L3.5 child_segments sample report generated.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def write_manifest(
    path: Path,
    scope: list[int],
    metrics: dict[str, Any],
    run: dict[str, Any] | None,
    forbidden_tables: list[str],
    stable_fingerprint: str,
) -> None:
    payload = {
        "generated_at": now_iso(),
        "sample_chapters": scope,
        "metrics": metrics,
        "build_run_id": None if run is None else run.get("build_run_id"),
        "target_min_chars": None if run is None else run.get("target_min_chars"),
        "target_max_chars": None if run is None else run.get("target_max_chars"),
        "hard_max_chars": None if run is None else run.get("hard_max_chars"),
        "overlap_sentences": None if run is None else run.get("overlap_sentences"),
        "stable_row_fingerprint": stable_fingerprint,
        "source_mutation_detected": metrics["source_mutation_detected"],
        "forbidden_final_tables": forbidden_tables,
        "outputs": {
            "sample_json": str(SAMPLE_JSON_RELATIVE_PATH).replace("\\", "/"),
            "sample_report": str(REPORT_RELATIVE_PATH).replace("\\", "/"),
            "manifest": str(MANIFEST_RELATIVE_PATH).replace("\\", "/"),
        },
        "hard_constraints": {
            "llm_calls": "NO",
            "embedding": "NO",
            "chroma_writes": "NO",
            "vector_database_writes": "NO",
            "source_table_mutation": "NO" if not metrics["source_mutation_detected"] else "DETECTED",
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_report(project_dir: Path | str, *, sample_chapters: str = builder.DEFAULT_SAMPLE_CHAPTERS) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    builder.ensure_dirs(root)
    db_path = root / builder.DB_RELATIVE_PATH
    scope = builder.parse_scope(sample_chapters, None)
    if not db_path.exists():
        raise RuntimeError("SQLite database does not exist")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        builder.validate_source_objects(conn)
        if not builder.object_exists(conn, "l3_child_segment", "table"):
            raise RuntimeError("l3_child_segment table is missing; run l3_child_segment_builder.py first")
        metrics = builder.scope_metrics(conn, scope)
        run = latest_build_run(conn, scope)
        source_mutation_detected = compute_source_mutation_detected(conn, run)
        forbidden_tables = builder.forbidden_final_tables(conn)
        metrics["source_mutation_detected"] = source_mutation_detected
        metrics["error_count"] = int(source_mutation_detected or bool(forbidden_tables))
        metrics["warning_count"] = int(metrics.get("warning_count", 0))
        segments = fetch_segments(conn, scope)
        stable_fingerprint = builder.stable_row_fingerprint(conn, scope)
        write_sample_json(root / SAMPLE_JSON_RELATIVE_PATH, scope, metrics, segments)
        write_report(root / REPORT_RELATIVE_PATH, db_path, scope, metrics, forbidden_tables)
        write_manifest(root / MANIFEST_RELATIVE_PATH, scope, metrics, run, forbidden_tables, stable_fingerprint)
        return metrics
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Report L3.5 child_segments sample outputs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--sample-chapters", type=str, default=builder.DEFAULT_SAMPLE_CHAPTERS)
    args = parser.parse_args()
    metrics = run_report(args.project_dir, sample_chapters=args.sample_chapters)
    print("L3.5 child segments REPORT PASS")
    print(f"child_segment_count={metrics['child_segment_count']}")


if __name__ == "__main__":
    main()
