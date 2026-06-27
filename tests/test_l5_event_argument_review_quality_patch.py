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

from scripts.l5_event_argument_review_quality_patch import ENHANCED_COLUMNS, run_l5_event_argument_review_quality_patch
from scripts.l5_event_candidate_review_exporter import EVENT_COLUMNS, run_l5_event_candidate_review_exporter


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seed_project(
    project_dir: Path,
    *,
    sentence: str = "Chen enter room.",
    trigger_text: str = "enter",
    argument_role: str | None = None,
    argument_text: str = "Chen",
    subject_in_review: bool = False,
    previous_sentence: str | None = None,
) -> Path:
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    chapter_id = "ch_0001"
    version_id = "ver_0001"
    content = f"{previous_sentence}\n{sentence}" if previous_sentence else sentence
    sentence_start = len(previous_sentence) + 1 if previous_sentence else 0
    sentence_end = sentence_start + len(sentence)
    trigger_start = sentence_start + sentence.index(trigger_text)
    trigger_end = trigger_start + len(trigger_text)
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
            (chapter_id, version_id, content, sha256_text(content), len(content)),
        )
        if previous_sentence:
            conn.execute(
                "INSERT INTO l2_current_sentences_fixture VALUES ('s0', 'p1', ?, 1, ?, 0, ?, ?, ?, 'chapter one')",
                (chapter_id, version_id, len(previous_sentence), sha256_text(previous_sentence), previous_sentence),
            )
        conn.execute(
            "INSERT INTO l2_current_sentences_fixture VALUES ('s1', 'p1', ?, 1, ?, ?, ?, ?, ?, 'chapter one')",
            (chapter_id, version_id, sentence_start, sentence_end, sha256_text(sentence), sentence),
        )
        conn.execute(
            "INSERT INTO l2_current_paragraphs_fixture VALUES ('p1', ?, 1, ?, 0, ?, ?, ?, 'chapter one')",
            (chapter_id, version_id, len(content), sha256_text(content), content),
        )
        conn.execute(
            """
            INSERT INTO l5_event_candidate VALUES (
                'evt_1', 'character_arrival', 'character_event', ?, 'rule_1',
                'candidate', 'medium', ?, 1, ?, '', 'p1', 's1', ?, ?,
                ?, ?, ?, ?, 1, ?, ?, 'l2_sentence', 'run1', '1970-01-01T00:00:00'
            )
            """,
            (
                trigger_text,
                chapter_id,
                version_id,
                sentence_start,
                sentence_end,
                trigger_start,
                trigger_end,
                sha256_text(sentence),
                sha256_text(content),
                sentence,
                sha256_text(sentence),
            ),
        )
        if argument_role:
            conn.execute(
                "INSERT INTO l5_event_argument_candidate VALUES ('arg_1', 'evt_1', ?, 'raw_text', NULL, ?, 'candidate', 'same_sentence', 'candidate', 'fixture', '1970-01-01T00:00:00')",
                (argument_role, argument_text),
            )
        if subject_in_review:
            conn.execute(
                "INSERT INTO l5_event_argument_candidate VALUES ('arg_subject', 'evt_1', 'subject', 'raw_text', NULL, ?, 'candidate', 'same_sentence', 'candidate', 'fixture', '1970-01-01T00:00:00')",
                (argument_text,),
            )
        conn.execute(
            """
            INSERT INTO l5_event_evidence_span VALUES (
                'span_1', 'evt_1', ?, 1, ?, '', 'p1', 's1', ?, ?, ?, ?, 1, '1970-01-01T00:00:00'
            )
            """,
            (chapter_id, version_id, sentence_start, sentence_end, sentence, sha256_text(sentence)),
        )
        conn.execute("INSERT INTO l5_event_extraction_run VALUES ('run1', 'sample', '1', 'seed', 'start', 'finish', 1, 0, 'passed', 'fixture')")
        conn.commit()
    finally:
        conn.close()
    run_l5_event_candidate_review_exporter(project_dir)
    return db_path


class L5EventArgumentReviewQualityPatchTests(unittest.TestCase):
    def test_outputs_preserve_original_columns_and_add_enhanced_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, argument_role="character")

            run_l5_event_argument_review_quality_patch(project_dir)

            outputs = [
                "l5_event_candidate_review_enhanced.csv",
                "l5_event_candidate_review_enhanced.json",
                "l5_event_argument_quality_report.md",
                "l5_event_argument_quality_manifest.json",
            ]
            for name in outputs:
                self.assertTrue((project_dir / "outputs" / name).exists(), name)
            rows = read_csv(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")
            self.assertTrue(set(EVENT_COLUMNS).issubset(rows[0]))
            self.assertTrue(set(ENHANCED_COLUMNS).issubset(rows[0]))

    def test_existing_subject_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, subject_in_review=True)

            run_l5_event_argument_review_quality_patch(project_dir)

            row = read_csv(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")[0]
            subjects = json.loads(row["enhanced_subject_candidates_json"])
            self.assertEqual(row["enhanced_subject_source_rule"], "existing_subject_candidate")
            self.assertEqual(subjects[0]["text"], "Chen")
            self.assertEqual(subjects[0]["is_confirmed"], False)

    def test_l5_argument_subject_like_role_adds_subject(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, argument_role="agent", argument_text="Chen")

            manifest = run_l5_event_argument_review_quality_patch(project_dir)

            row = read_csv(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")[0]
            self.assertEqual(row["enhanced_subject_source_rule"], "l5_argument_agent")
            self.assertEqual(row["enhanced_subject_candidate_added"], "true")
            self.assertGreater(manifest["quality_metrics"]["enhanced_subject_candidate_added_count"], 0)

    def test_same_sentence_named_subject_adds_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, sentence="Chen enter room.", trigger_text="enter")

            run_l5_event_argument_review_quality_patch(project_dir)

            row = read_csv(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")[0]
            self.assertIn(row["enhanced_subject_source_rule"], {"nearest_character_before_trigger", "nearest_character_same_sentence"})
            self.assertEqual(json.loads(row["enhanced_subject_candidates_json"])[0]["text"], "Chen")

    def test_quoted_speech_speaker_pattern_adds_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, sentence="Chen said stop.", trigger_text="said")

            run_l5_event_argument_review_quality_patch(project_dir)

            row = read_csv(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")[0]
            self.assertEqual(row["enhanced_subject_source_rule"], "quoted_speech_speaker_pattern")

    def test_pronoun_back_reference_adds_weak_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, previous_sentence="Chen waited.", sentence="He enter room.", trigger_text="enter")

            run_l5_event_argument_review_quality_patch(project_dir)

            row = read_csv(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")[0]
            flags = json.loads(row["enhanced_warning_flags_json"])
            self.assertEqual(row["enhanced_subject_source_rule"], "pronoun_back_reference")
            self.assertIn("enhanced_subject_from_weak_rule", flags)

    def test_no_subject_found_keeps_empty_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, sentence="the door enter view.", trigger_text="enter")

            run_l5_event_argument_review_quality_patch(project_dir)

            row = read_csv(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")[0]
            self.assertEqual(row["enhanced_subject_source_rule"], "not_found")
            self.assertEqual(json.loads(row["enhanced_subject_candidates_json"]), [])
            self.assertIn("enhanced_subject_not_found", json.loads(row["enhanced_warning_flags_json"]))

    def test_no_future_tables_source_mutation_and_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(project_dir, argument_role="agent")
            original_hashes = {
                name: file_hash(project_dir / "outputs" / name)
                for name in ["l5_event_candidate_review.csv", "l5_event_candidate_review.json", "l5_event_candidate_review_manifest.json"]
            }

            first = run_l5_event_argument_review_quality_patch(project_dir)
            csv_hash = file_hash(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv")
            json_hash = file_hash(project_dir / "outputs" / "l5_event_candidate_review_enhanced.json")
            second = run_l5_event_argument_review_quality_patch(project_dir)

            self.assertFalse(first["source_mutation_detected"])
            self.assertFalse(second["source_mutation_detected"])
            self.assertEqual(csv_hash, file_hash(project_dir / "outputs" / "l5_event_candidate_review_enhanced.csv"))
            self.assertEqual(json_hash, file_hash(project_dir / "outputs" / "l5_event_candidate_review_enhanced.json"))
            self.assertEqual(
                original_hashes,
                {
                    name: file_hash(project_dir / "outputs" / name)
                    for name in ["l5_event_candidate_review.csv", "l5_event_candidate_review.json", "l5_event_candidate_review_manifest.json"]
                },
            )
            conn = sqlite3.connect(db_path)
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                conn.close()
            self.assertFalse({"l5_normalized_event", "l5_normalized_state_change", "l5_event_merge_group", "confirmed_event"}.intersection(tables))

    def test_verifier_pass_line(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, argument_role="agent")

            result = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "l5_verify_event_argument_review_quality_patch.py"), "--project-dir", str(project_dir)],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip().splitlines()[-1], "L5.1a event argument review quality patch FULL PASS")


if __name__ == "__main__":
    unittest.main()
