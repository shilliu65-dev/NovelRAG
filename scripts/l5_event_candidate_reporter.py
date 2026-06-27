from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_extractor import (
    JSON_REPORT_RELATIVE_PATH,
    MD_REPORT_RELATIVE_PATH,
    build_json_report,
    build_markdown_report,
    collect_missing_optional_sources,
    discover_scene_block_source,
    scene_block_source_payload,
)


def run_l5_event_candidate_reporter(project_dir: Path | str | None = None) -> tuple[Path, Path]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    json_path = root / JSON_REPORT_RELATIVE_PATH
    md_path = root / MD_REPORT_RELATIVE_PATH
    conn = sqlite3.connect(root / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            """
            SELECT extraction_run_id, sample_chapters, rule_seed_hash, candidate_count, state_change_candidate_count
            FROM l5_event_extraction_run
            ORDER BY extraction_run_id
            LIMIT 1
            """
        ).fetchone()
        if run is None:
            raise RuntimeError("No l5_event_extraction_run row exists. Run the extractor first.")
        sample = [int(item) for item in str(run["sample_chapters"]).split(",") if item]
        stats = type(
            "ReporterStats",
            (),
            {
                "extraction_run_id": run["extraction_run_id"],
                "rule_seed_hash": run["rule_seed_hash"],
                "sample_chapters": sample,
                "candidate_count": int(run["candidate_count"]),
                "state_change_candidate_count": int(run["state_change_candidate_count"]),
                "missing_optional_sources": collect_missing_optional_sources(conn),
                "duplicate_skips": [],
                "detected_scene_block_source": scene_block_source_payload(discover_scene_block_source(conn)),
            },
        )()
        payload = build_json_report(conn, stats)
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        md_path.write_text(build_markdown_report(payload), encoding="utf-8")
    finally:
        conn.close()
    return json_path, md_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate L5 event candidate reports from L5 tables.")
    parser.add_argument("--project-dir", type=Path, default=None)
    args = parser.parse_args()
    json_path, md_path = run_l5_event_candidate_reporter(args.project_dir)
    print(f"L5 event candidate reports: {json_path}, {md_path}")


if __name__ == "__main__":
    main()
