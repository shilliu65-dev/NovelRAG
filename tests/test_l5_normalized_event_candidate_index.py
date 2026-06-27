import csv
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_normalized_event_candidate_indexer import (
    NORMALIZED_EVENT_TABLES,
    run_l5_normalized_event_candidate_indexer,
)
from scripts.l5_normalized_event_candidate_reporter import run_l5_normalized_event_candidate_reporter
from scripts.l5_verify_normalized_event_candidate_index import PASS_MESSAGE, verify_l5_normalized_event_candidate_index


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_seed(project_dir: Path) -> str:
    seed = {
        "schema_version": "test",
        "layer": "L5.2 Event Normalization Seed / Rule",
        "event_type_catalog": [
            {"event_type": "movement", "is_major_event_candidate": False},
            {"event_type": "injury", "is_major_event_candidate": True},
        ],
        "state_change_type_catalog": [{"state_change_type": "injury_change", "allowed_event_types": ["injury"]}],
        "argument_role_catalog": [
            {"role": "subject"},
            {"role": "mover"},
            {"role": "patient"},
            {"role": "current_location"},
            {"role": "time_hint"},
            {"role": "scene"},
        ],
        "trigger_category_mapping": [{"trigger_category": "movement", "maps_to_event_type": "movement", "default_subtype": "arrive"}],
        "l5_event_type_candidate_mapping": [
            {"event_type_candidate": "character_arrival", "maps_to_event_type": "movement", "default_subtype": "arrive"},
            {"event_type_candidate": "character_injury", "maps_to_event_type": "injury", "default_subtype": "hurt"},
        ],
        "l5_state_change_type_candidate_mapping": [
            {"state_change_type_candidate": "life_state", "maps_to_state_change_type": "injury_change"}
        ],
        "importance_rules": [{"rule_id": "major_injury", "applies_to_event_types": ["injury"], "importance_level": "major"}],
    }
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(seed, ensure_ascii=False, indent=2, sort_keys=True)
    checksum = sha256_text(raw)
    seed["seed_checksum"] = checksum
    raw = json.dumps(seed, ensure_ascii=False, indent=2, sort_keys=True)
    (config_dir / "event_normalization_rules.seed.json").write_text(raw + "\n", encoding="utf-8")
    return sha256_text(raw + "\n")


def subject(text: str = "Chen") -> str:
    return json.dumps(
        [
            {
                "text": text,
                "role": "subject",
                "entity_kind": "character",
                "confidence": 0.82,
                "is_confirmed": False,
                "source_kind": "fixture",
                "source_rule": "fixture_subject",
            }
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def location(text: str = "hall") -> str:
    return json.dumps(
        [{"text": text, "role": "current_location", "entity_kind": "location", "confidence": 0.7, "is_confirmed": False}],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def base_row(event_id: str, recommendation: str = "ready_for_l5_3_candidate", *, subject_json: str | None = None) -> dict[str, str]:
    evidence = f"{event_id} Chen entered the hall."
    return {
        "review_row_id": f"review_{event_id}",
        "review_status": "pending",
        "review_decision": "",
        "review_note": "",
        "event_candidate_id": event_id,
        "event_candidate_hash": sha256_text(event_id),
        "event_source_status": "ok",
        "chapter_id": "ch_0001",
        "chapter_num": "1",
        "chapter_title": "chapter one",
        "version_id": "ver_0001",
        "scene_block_source_status": "compatible_fallback",
        "scene_block_table_name": "l3_scene_blocks",
        "scene_block_id": "scene_1",
        "scene_block_validity": "valid",
        "trigger_text": "entered",
        "trigger_rule_id": "rule_movement",
        "trigger_category": "movement",
        "event_type_candidate": "character_arrival",
        "event_subtype_candidate": "",
        "subject_candidates_json": "[]",
        "object_candidates_json": "[]",
        "location_candidates_json": "[]",
        "time_hint_candidates_json": "[]",
        "organization_candidates_json": "[]",
        "power_candidates_json": "[]",
        "other_argument_candidates_json": "[]",
        "argument_count": "0",
        "state_change_candidate_count": "0",
        "evidence_span_count": "1",
        "evidence_source_kind": "l2_sentence",
        "evidence_l2_sentence_id": "s1",
        "evidence_l2_paragraph_id": "p1",
        "evidence_start_offset": "0",
        "evidence_end_offset": str(len(evidence)),
        "evidence_text_backcut": evidence,
        "evidence_backcut_hash": sha256_text(evidence),
        "evidence_backcut_status": "ok",
        "confidence_score": "0.72",
        "confidence_rule": "fixture",
        "importance_candidate": "normal",
        "warning_flags_json": "[]",
        "source_run_id": "run1",
        "source_created_at": "1970-01-01T00:00:00",
        "export_created_at": "1970-01-01T00:00:00",
        "enhanced_subject_candidates_json": subject() if subject_json is None else subject_json,
        "enhanced_object_candidates_json": "[]",
        "enhanced_location_candidates_json": location(),
        "enhanced_time_hint_candidates_json": "[]",
        "enhanced_argument_candidates_json": "[]",
        "enhanced_subject_source_rule": "fixture_subject",
        "enhanced_subject_source_kind": "fixture",
        "enhanced_subject_confidence": "0.82",
        "enhanced_subject_evidence_text": "Chen",
        "enhanced_subject_evidence_span_json": "{}",
        "enhanced_argument_count": "1",
        "enhanced_subject_candidate_added": "true",
        "enhanced_warning_flags_json": "[]",
        "enhanced_quality_score": "0.8",
        "enhanced_review_recommendation": recommendation,
        "enhanced_notes": "",
    }


def seed_project(project_dir: Path, rows: list[dict[str, str]]) -> Path:
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (project_dir / "index").mkdir(parents=True, exist_ok=True)
    write_seed(project_dir)
    db_path = project_dir / "index" / "novel_story_bible.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE l5_event_candidate (
                event_candidate_id TEXT PRIMARY KEY,
                chapter_num INTEGER,
                scene_block_id TEXT,
                evidence_text TEXT
            );
            CREATE TABLE l5_event_argument_candidate (
                argument_id TEXT PRIMARY KEY,
                event_candidate_id TEXT,
                argument_role TEXT,
                entity_text TEXT
            );
            CREATE TABLE l5_event_state_change_candidate (
                state_change_candidate_id TEXT PRIMARY KEY,
                event_candidate_id TEXT,
                state_type TEXT,
                evidence_text TEXT
            );
            CREATE TABLE l5_event_evidence_span (
                evidence_span_id TEXT PRIMARY KEY,
                event_candidate_id TEXT,
                span_text TEXT
            );
            CREATE TABLE l5_event_extraction_run (
                extraction_run_id TEXT PRIMARY KEY,
                sample_chapters TEXT,
                status TEXT
            );
            CREATE TABLE l3_scene_blocks (
                scene_block_id TEXT PRIMARY KEY,
                chapter_num INTEGER,
                start_offset INTEGER,
                end_offset INTEGER
            );
            """
        )
        conn.execute("INSERT INTO l3_scene_blocks VALUES ('scene_1', 1, 0, 100)")
        for row in rows:
            conn.execute(
                "INSERT INTO l5_event_candidate VALUES (?, ?, ?, ?)",
                (row["event_candidate_id"], int(row["chapter_num"]), row["scene_block_id"], row["evidence_text_backcut"]),
            )
        conn.execute("INSERT INTO l5_event_extraction_run VALUES ('run1', '1', 'passed')")
        conn.commit()
    finally:
        conn.close()

    columns = list(rows[0].keys())
    csv_path = project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    payload = {
        "export_name": "l5_event_candidate_review_enhanced",
        "row_count": len(rows),
        "columns": columns,
        "rows": rows,
    }
    (project_dir / "outputs" / "l5_event_candidate_review_enhanced.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (project_dir / "outputs" / "l5_event_argument_quality_manifest.json").write_text(
        json.dumps({"row_counts": {"enhanced_event_review_rows": len(rows)}}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return db_path


class L5NormalizedEventCandidateIndexTests(unittest.TestCase):
    def test_ready_rows_are_normalized_candidates_not_confirmed_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, [base_row("evt_ready"), base_row("evt_dup", "likely_duplicate")])

            manifest = run_l5_normalized_event_candidate_indexer(project_dir, sample_chapters="1", rebuild=True)

            self.assertEqual(manifest["row_counts"]["normalized_event_candidate_count"], 1)
            self.assertEqual(manifest["skip_counts"]["likely_duplicate"], 1)
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(set(NORMALIZED_EVENT_TABLES).issubset(tables))
                self.assertFalse({"confirmed_event", "l5_confirmed_event", "l5_event_timeline", "l5_relationship_graph"}.intersection(tables))
                event = conn.execute("SELECT * FROM l5_normalized_event_candidate").fetchone()
                args = conn.execute("SELECT * FROM l5_normalized_event_argument").fetchall()
            finally:
                conn.close()
            self.assertEqual(event["normalization_status"], "normalized_candidate")
            self.assertEqual(event["source_layer"], "L5.1a")
            self.assertEqual(event["subject_is_confirmed"], 0)
            self.assertEqual(event["l5_2_event_type"], "movement")
            self.assertTrue(event["l5_2_seed_checksum"])
            self.assertTrue(args)
            self.assertTrue(all(row["is_confirmed"] == 0 for row in args))

    def test_missing_subject_is_skipped_and_source_tables_are_not_mutated(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, [base_row("evt_missing", subject_json="[]")])
            before_db_hash = file_hash(db_path)
            before_csv_hash = file_hash(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")

            manifest = run_l5_normalized_event_candidate_indexer(project_dir, sample_chapters="1", rebuild=True)

            conn = sqlite3.connect(db_path)
            try:
                count = conn.execute("SELECT COUNT(*) FROM l5_normalized_event_candidate").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(count, 0)
            self.assertEqual(manifest["skip_counts"]["missing_subject"], 1)
            self.assertFalse(manifest["source_mutation_detected"])
            self.assertEqual(before_csv_hash, file_hash(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv"))
            self.assertNotEqual(before_db_hash, file_hash(db_path))

    def test_rebuild_is_deterministic_and_reporter_outputs_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [base_row("evt_ready")])

            first = run_l5_normalized_event_candidate_indexer(project_dir, sample_chapters="1", rebuild=True)
            first_hash = file_hash(project_dir / "outputs" / "l5_normalized_event_candidates_sample.json")
            second = run_l5_normalized_event_candidate_indexer(project_dir, sample_chapters="1", rebuild=True)
            report = run_l5_normalized_event_candidate_reporter(project_dir)

            self.assertEqual(first["stable_output_hashes"], second["stable_output_hashes"])
            self.assertEqual(first_hash, file_hash(project_dir / "outputs" / "l5_normalized_event_candidates_sample.json"))
            csv_rows = read_csv_rows(project_dir / "outputs" / "l5_normalized_event_candidates_sample.csv")
            self.assertEqual(len(csv_rows), report["row_counts"]["normalized_event_candidate_count"])
            report_text = (project_dir / "outputs" / "l5_normalized_event_candidates_sample_report.md").read_text(encoding="utf-8")
            self.assertIn("## Source Discovery", report_text)
            self.assertIn("detected_scene_block_source", report_text)

    def test_blob_source_table_does_not_break_source_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, [base_row("evt_ready")])
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("CREATE TABLE l3_blob_fixture (id TEXT PRIMARY KEY, payload BLOB)")
                conn.execute("INSERT INTO l3_blob_fixture VALUES ('blob1', ?)", (sqlite3.Binary(b'\x00\x01fixture'),))
                conn.commit()
            finally:
                conn.close()

            manifest = run_l5_normalized_event_candidate_indexer(project_dir, sample_chapters="1", rebuild=True)

            self.assertFalse(manifest["source_mutation_detected"])
            self.assertEqual(manifest["row_counts"]["normalized_event_candidate_count"], 1)

    def test_verifier_exact_pass_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, [base_row("evt_ready")])

            result = verify_l5_normalized_event_candidate_index(project_dir)
            cli = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "l5_verify_normalized_event_candidate_index.py"), "--project-dir", str(project_dir)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertTrue(result["ok"], result["problems"])
            self.assertEqual(result["final_message"], PASS_MESSAGE)
            self.assertEqual(cli.returncode, 0, cli.stderr)
            self.assertEqual(cli.stdout.strip(), "L5.3 normalized event candidate index FULL PASS")


if __name__ == "__main__":
    unittest.main()
