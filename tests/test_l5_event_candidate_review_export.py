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

from scripts.l5_event_candidate_review_exporter import EVENT_COLUMNS, STATE_COLUMNS, run_l5_event_candidate_review_exporter


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def seed_project(
    project_dir: Path,
    *,
    invalid_offset: bool = False,
    duplicate_event: bool = False,
    scene_source: str | None = None,
) -> Path:
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    sentence = "Chen enter room."
    chapter_id = "ch_0001"
    version_id = "ver_0001"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE current_chapters_fixture (
                chapter_id TEXT PRIMARY KEY,
                chapter_num INTEGER NOT NULL,
                chapter_title_current TEXT NOT NULL,
                latest_version_id TEXT NOT NULL,
                content_full_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                content_length INTEGER NOT NULL
            );
            CREATE TABLE l2_current_sentences_fixture (
                sentence_id TEXT PRIMARY KEY,
                para_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                sentence_hash TEXT NOT NULL,
                sentence_text TEXT NOT NULL,
                chapter_title_current TEXT NOT NULL
            );
            CREATE TABLE l2_current_paragraphs_fixture (
                para_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                para_hash TEXT NOT NULL,
                para_text TEXT NOT NULL,
                chapter_title_current TEXT NOT NULL
            );
            CREATE VIEW v_current_chapters AS SELECT * FROM current_chapters_fixture;
            CREATE VIEW v_l2_current_sentences AS SELECT * FROM l2_current_sentences_fixture;
            CREATE VIEW v_l2_current_paragraphs AS SELECT * FROM l2_current_paragraphs_fixture;

            CREATE TABLE l5_event_candidate (
                event_candidate_id TEXT PRIMARY KEY,
                event_type TEXT,
                event_family TEXT,
                trigger_text TEXT,
                trigger_rule_id TEXT,
                status TEXT,
                confidence_level TEXT,
                chapter_id TEXT,
                chapter_num INTEGER,
                version_id TEXT,
                scene_block_id TEXT,
                para_id TEXT,
                sentence_id TEXT,
                sentence_start_offset INTEGER,
                sentence_end_offset INTEGER,
                trigger_start_offset INTEGER,
                trigger_end_offset INTEGER,
                sentence_hash TEXT,
                paragraph_hash TEXT,
                l1_backcut_matched INTEGER,
                evidence_text TEXT,
                evidence_hash TEXT,
                source_layer TEXT,
                extraction_run_id TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_event_argument_candidate (
                argument_id TEXT PRIMARY KEY,
                event_candidate_id TEXT,
                argument_role TEXT,
                entity_layer TEXT,
                entity_id TEXT,
                entity_text TEXT,
                entity_status TEXT,
                distance_scope TEXT,
                status TEXT,
                source_note TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_event_state_change_candidate (
                state_change_candidate_id TEXT PRIMARY KEY,
                event_candidate_id TEXT,
                entity_layer TEXT,
                entity_id TEXT,
                entity_text TEXT,
                state_type TEXT,
                from_state TEXT,
                to_state TEXT,
                status TEXT,
                evidence_text TEXT,
                evidence_hash TEXT,
                source_note TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_event_evidence_span (
                evidence_span_id TEXT PRIMARY KEY,
                event_candidate_id TEXT,
                chapter_id TEXT,
                chapter_num INTEGER,
                version_id TEXT,
                scene_block_id TEXT,
                para_id TEXT,
                sentence_id TEXT,
                span_start_offset INTEGER,
                span_end_offset INTEGER,
                span_text TEXT,
                span_hash TEXT,
                l1_backcut_matched INTEGER,
                created_at TEXT
            );
            CREATE TABLE l5_event_extraction_run (
                extraction_run_id TEXT PRIMARY KEY,
                run_scope TEXT,
                sample_chapters TEXT,
                rule_seed_hash TEXT,
                started_at TEXT,
                finished_at TEXT,
                candidate_count INTEGER,
                state_change_candidate_count INTEGER,
                status TEXT,
                note TEXT
            );
            """
        )
        conn.execute(
            "INSERT INTO current_chapters_fixture VALUES (?, 1, 'chapter one', ?, ?, ?, ?)",
            (chapter_id, version_id, sentence, sha256_text(sentence), len(sentence)),
        )
        conn.execute(
            "INSERT INTO l2_current_sentences_fixture VALUES ('s1', 'p1', ?, 1, ?, 0, ?, ?, ?, 'chapter one')",
            (chapter_id, version_id, len(sentence), sha256_text(sentence), sentence),
        )
        conn.execute(
            "INSERT INTO l2_current_paragraphs_fixture VALUES ('p1', ?, 1, ?, 0, ?, ?, ?, 'chapter one')",
            (chapter_id, version_id, len(sentence), sha256_text(sentence), sentence),
        )
        if scene_source in {"l3", "both"}:
            conn.execute(
                """
                CREATE TABLE l3_scene_blocks (
                    scene_id INTEGER PRIMARY KEY,
                    scene_key TEXT,
                    chapter_id TEXT,
                    version_id TEXT,
                    chapter_num INTEGER,
                    start_offset INTEGER,
                    end_offset INTEGER
                )
                """
            )
            conn.execute("INSERT INTO l3_scene_blocks VALUES (1, 'l3scene', ?, ?, 1, 0, ?)", (chapter_id, version_id, len(sentence)))
        if scene_source in {"l4", "both"}:
            conn.execute(
                """
                CREATE TABLE l4_scene_blocks (
                    scene_block_id TEXT PRIMARY KEY,
                    chapter_id TEXT,
                    version_id TEXT,
                    chapter_num INTEGER,
                    start_offset INTEGER,
                    end_offset INTEGER
                )
                """
            )
            conn.execute("INSERT INTO l4_scene_blocks VALUES ('l4scene', ?, ?, 1, 0, ?)", (chapter_id, version_id, len(sentence)))

        scene_id = "l4scene" if scene_source in {"l4", "both"} else ("1" if scene_source == "l3" else "")
        event_count = 2 if duplicate_event else 1
        for index in range(event_count):
            event_id = f"evt_{index + 1}"
            conn.execute(
                """
                INSERT INTO l5_event_candidate VALUES (
                    ?, 'character_arrival', 'character_event', 'enter', 'arrival_test',
                    'candidate', 'medium', ?, 1, ?, ?, 'p1', 's1', 0, ?,
                    5, 10, ?, ?, 1, ?, ?, 'l2_sentence', 'run1', '1970-01-01T00:00:00'
                )
                """,
                (event_id, chapter_id, version_id, scene_id, len(sentence), sha256_text(sentence), sha256_text(sentence), sentence, sha256_text(sentence)),
            )
            conn.execute(
                "INSERT INTO l5_event_argument_candidate VALUES (?, ?, 'subject', 'raw_text', NULL, 'Chen', 'candidate', 'same_sentence', 'candidate', 'fixture', '1970-01-01T00:00:00')",
                (f"arg_{index + 1}", event_id),
            )
            span_end = len(sentence) + 20 if invalid_offset and index == 0 else len(sentence)
            conn.execute(
                """
                INSERT INTO l5_event_evidence_span VALUES (
                    ?, ?, ?, 1, ?, ?, 'p1', 's1', 0, ?, ?, ?, 1, '1970-01-01T00:00:00'
                )
                """,
                (f"span_{index + 1}", event_id, chapter_id, version_id, scene_id, span_end, sentence, sha256_text(sentence)),
            )
        conn.execute(
            """
            INSERT INTO l5_event_state_change_candidate VALUES (
                'stc_1', 'evt_1', 'raw_text', NULL, 'enter', 'location_state',
                'absent', 'arrived', 'candidate', ?, ?, 'fixture', '1970-01-01T00:00:00'
            )
            """,
            (sentence, sha256_text(sentence)),
        )
        conn.execute("INSERT INTO l5_event_extraction_run VALUES ('run1', 'sample', '1', 'seed', 'start', 'finish', ?, 1, 'passed', 'fixture')", (event_count,))
        conn.commit()
    finally:
        conn.close()
    return db_path


class L5EventCandidateReviewExportTests(unittest.TestCase):
    def test_required_output_files_and_review_columns_exist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_candidate_review_exporter(project_dir)

            output_names = {
                "l5_event_candidate_review.csv",
                "l5_event_candidate_review.json",
                "l5_event_candidate_review_report.md",
                "l5_state_change_candidate_review.csv",
                "l5_state_change_candidate_review.json",
                "l5_state_change_candidate_review_report.md",
                "l5_event_candidate_review_manifest.json",
            }
            for name in output_names:
                self.assertTrue((project_dir / "outputs" / name).exists(), name)
            event_rows = read_csv(project_dir / "outputs" / "l5_event_candidate_review.csv")
            state_rows = read_csv(project_dir / "outputs" / "l5_state_change_candidate_review.csv")
            self.assertTrue(set(EVENT_COLUMNS).issubset(event_rows[0]))
            self.assertTrue(set(STATE_COLUMNS).issubset(state_rows[0]))

    def test_json_row_count_parity(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_candidate_review_exporter(project_dir)

            event_rows = read_csv(project_dir / "outputs" / "l5_event_candidate_review.csv")
            event_json = json.loads((project_dir / "outputs" / "l5_event_candidate_review.json").read_text(encoding="utf-8"))
            state_rows = read_csv(project_dir / "outputs" / "l5_state_change_candidate_review.csv")
            state_json = json.loads((project_dir / "outputs" / "l5_state_change_candidate_review.json").read_text(encoding="utf-8"))
            self.assertEqual(len(event_rows), event_json["row_count"])
            self.assertEqual(len(state_rows), state_json["row_count"])

    def test_missing_optional_scene_blocks_is_manifest_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_candidate_review_exporter(project_dir)

            self.assertEqual(manifest["detected_scene_block_source"]["scene_block_source_status"], "missing_optional")
            event_rows = read_csv(project_dir / "outputs" / "l5_event_candidate_review.csv")
            self.assertIn("optional_scene_block_source_missing", json.loads(event_rows[0]["warning_flags_json"]))

    def test_l3_scene_blocks_compatibility_and_l4_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, scene_source="l3")
            manifest = run_l5_event_candidate_review_exporter(project_dir)
            self.assertEqual(manifest["detected_scene_block_source"]["scene_block_table_name"], "l3_scene_blocks")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, scene_source="both")
            manifest = run_l5_event_candidate_review_exporter(project_dir)
            self.assertEqual(manifest["detected_scene_block_source"]["scene_block_table_name"], "l4_scene_blocks")

    def test_evidence_backcut_ok_and_invalid_offset_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            run_l5_event_candidate_review_exporter(project_dir)
            event_rows = read_csv(project_dir / "outputs" / "l5_event_candidate_review.csv")
            self.assertEqual(event_rows[0]["evidence_backcut_status"], "ok")

        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, invalid_offset=True)
            run_l5_event_candidate_review_exporter(project_dir)
            event_rows = read_csv(project_dir / "outputs" / "l5_event_candidate_review.csv")
            self.assertEqual(event_rows[0]["evidence_backcut_status"], "offset_invalid")
            self.assertIn("evidence_backcut_not_ok", json.loads(event_rows[0]["warning_flags_json"]))

    def test_duplicate_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, duplicate_event=True)

            run_l5_event_candidate_review_exporter(project_dir)

            event_rows = read_csv(project_dir / "outputs" / "l5_event_candidate_review.csv")
            self.assertEqual(len(event_rows), 2)
            self.assertTrue(all("duplicate_trigger_same_sentence" in json.loads(row["warning_flags_json"]) for row in event_rows))

    def test_source_mutation_guard(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, scene_source="l3")
            conn = sqlite3.connect(db_path)
            try:
                before = {
                    "l5_event_candidate": conn.execute("SELECT COUNT(*) FROM l5_event_candidate").fetchone()[0],
                    "l3_scene_blocks": conn.execute("SELECT COUNT(*) FROM l3_scene_blocks").fetchone()[0],
                }
            finally:
                conn.close()

            manifest = run_l5_event_candidate_review_exporter(project_dir)

            conn = sqlite3.connect(db_path)
            try:
                after = {
                    "l5_event_candidate": conn.execute("SELECT COUNT(*) FROM l5_event_candidate").fetchone()[0],
                    "l3_scene_blocks": conn.execute("SELECT COUNT(*) FROM l3_scene_blocks").fetchone()[0],
                }
            finally:
                conn.close()
            self.assertFalse(manifest["source_mutation_detected"])
            self.assertEqual(before, after)

    def test_verifier_pass_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, scene_source="l3")

            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "l5_verify_event_candidate_review_export.py"), "--project-dir", str(project_dir)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().splitlines()[-1], "L5.1 event candidate review export FULL PASS")


if __name__ == "__main__":
    unittest.main()
