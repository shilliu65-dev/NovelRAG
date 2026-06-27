import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_confirmed_event_candidate_indexer import run_l5_confirmed_event_candidate_indexer
from scripts.l5_confirmed_event_candidate_reporter import run_l5_confirmed_event_candidate_reporter
from scripts.l5_verify_confirmed_event_candidate_index import PASS_MESSAGE, verify_l5_confirmed_event_candidate_index


def seed_project(project_dir: Path) -> None:
    (project_dir / "index").mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    db_path = project_dir / "index" / "novel_story_bible.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE l5_normalized_event_candidate (
                normalized_event_candidate_id TEXT PRIMARY KEY,
                source_event_candidate_id TEXT,
                source_event_candidate_hash TEXT,
                source_review_row_id TEXT,
                source_layer TEXT,
                chapter_id TEXT,
                chapter_num INTEGER,
                chapter_title TEXT,
                version_id TEXT,
                scene_block_id TEXT,
                scene_block_table_name TEXT,
                scene_block_source_status TEXT,
                scene_block_validity TEXT,
                trigger_text TEXT,
                trigger_rule_id TEXT,
                trigger_category TEXT,
                event_type_candidate TEXT,
                event_subtype_candidate TEXT,
                l5_2_event_type TEXT,
                l5_2_event_subtype TEXT,
                normalization_status TEXT,
                normalization_rule_id TEXT,
                subject_text TEXT,
                subject_entity_kind TEXT,
                subject_is_confirmed INTEGER,
                confidence_score REAL,
                normalized_confidence_score REAL,
                importance_level TEXT,
                evidence_backcut_status TEXT,
                evidence_backcut_hash TEXT,
                evidence_text_backcut TEXT,
                l5_2_seed_checksum TEXT,
                stable_hash TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_normalized_event_argument (
                normalized_argument_id TEXT PRIMARY KEY,
                normalized_event_candidate_id TEXT,
                source_event_candidate_id TEXT,
                argument_role TEXT,
                entity_kind TEXT,
                argument_text TEXT,
                confidence REAL,
                source_kind TEXT,
                source_rule TEXT,
                evidence_text TEXT,
                is_confirmed INTEGER,
                l5_2_seed_checksum TEXT,
                stable_hash TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_normalized_event_evidence (
                normalized_evidence_id TEXT PRIMARY KEY,
                normalized_event_candidate_id TEXT,
                source_event_candidate_id TEXT,
                evidence_source_kind TEXT,
                l2_sentence_id TEXT,
                l2_paragraph_id TEXT,
                start_offset INTEGER,
                end_offset INTEGER,
                evidence_text TEXT,
                evidence_hash TEXT,
                evidence_backcut_status TEXT,
                l5_2_seed_checksum TEXT,
                stable_hash TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_review_decision_current (
                normalized_event_id TEXT PRIMARY KEY,
                review_import_id TEXT,
                review_batch_id TEXT,
                human_decision TEXT,
                human_confidence TEXT,
                human_confidence_normalized REAL,
                human_notes TEXT,
                duplicate_of_normalized_event_id TEXT,
                needs_context_reason TEXT,
                reject_reason TEXT,
                reviewer_name TEXT,
                source_file_hash TEXT,
                current_hash TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_review_decision_conflict_audit (
                conflict_id TEXT PRIMARY KEY,
                conflict_type TEXT,
                normalized_event_id TEXT,
                related_normalized_event_id TEXT,
                severity TEXT,
                message TEXT,
                review_batch_id TEXT,
                source_file_hash TEXT,
                created_at TEXT
            );
            """
        )
        for idx in range(1, 5):
            event_id = f"l5n_evt_{idx}"
            conn.execute(
                """
                INSERT INTO l5_normalized_event_candidate VALUES (
                    ?, ?, 'src_hash', 'review_row', 'L5.1a', 'ch_1', 1, 'chapter one', 'ver_1',
                    'scene_1', 'l3_scene_blocks', 'compatible', 'valid', 'trigger', 'rule', 'movement',
                    'movement', 'arrive', 'movement', 'arrive', 'normalized_candidate', 'rule_map',
                    ?, 'character', 0, 0.8, 0.9, 'normal', 'ok', 'ev_hash', 'evidence', 'seed', ?, '1970-01-01T00:00:00'
                )
                """,
                (event_id, f"src_evt_{idx}", f"subject_{idx}", f"stable_{idx}"),
            )
            conn.execute(
                "INSERT INTO l5_normalized_event_argument VALUES (?, ?, ?, 'subject', 'character', ?, 0.8, 'fixture', 'fixture', '', 0, 'seed', ?, '1970-01-01T00:00:00')",
                (f"arg_{idx}", event_id, f"src_evt_{idx}", f"subject_{idx}", f"arg_stable_{idx}"),
            )
            conn.execute(
                "INSERT INTO l5_normalized_event_evidence VALUES (?, ?, ?, 'l2_sentence', 'sent_1', 'para_1', 0, 10, ?, ?, 'ok', 'seed', ?, '1970-01-01T00:00:00')",
                (f"evd_{idx}", event_id, f"src_evt_{idx}", f"evidence_{idx}", f"evidence_hash_{idx}", f"evd_stable_{idx}"),
            )

        decisions = [
            ("l5n_evt_1", "imp_1", "approved_candidate"),
            ("l5n_evt_2", "imp_2", "duplicate_candidate"),
            ("l5n_evt_3", "imp_3", "approved_candidate"),
        ]
        for event_id, import_id, decision in decisions:
            conn.execute(
                "INSERT INTO l5_review_decision_current VALUES (?, ?, 'batch_1', ?, 'high', 0.9, '', '', '', '', 'reviewer', 'filehash', 'currhash', '1970-01-01T00:00:00')",
                (event_id, import_id, decision),
            )
        conn.execute(
            "INSERT INTO l5_review_decision_conflict_audit VALUES ('conf_1', 'conflicting_decisions', 'l5n_evt_3', '', 'error', 'fixture', 'batch_1', 'filehash', '1970-01-01T00:00:00')"
        )
        conn.commit()
    finally:
        conn.close()


class L5ConfirmedEventCandidateIndexTests(unittest.TestCase):
    def test_indexer_creates_confirmed_and_blocked_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_confirmed_event_candidate_indexer(project_dir, rebuild=True)

            self.assertEqual(manifest["row_counts"]["confirmed_event_candidate_count"], 1)
            self.assertEqual(manifest["row_counts"]["confirmed_event_argument_candidate_count"], 1)
            self.assertEqual(manifest["row_counts"]["confirmed_event_evidence_span_count"], 1)
            self.assertEqual(manifest["blocked_reason_counts"]["blocked_by_duplicate"], 1)
            self.assertEqual(manifest["blocked_reason_counts"]["blocked_by_conflict"], 1)
            self.assertEqual(manifest["blocked_reason_counts"]["blocked_by_missing_review"], 1)

    def test_reporter_matches_indexed_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_confirmed_event_candidate_indexer(project_dir, rebuild=True)
            report = run_l5_confirmed_event_candidate_reporter(project_dir)

            self.assertEqual(report["row_counts"]["confirmed_event_candidate_count"], 1)
            self.assertEqual(report["row_counts"]["blocked_audit_count"], 3)

    def test_verifier_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            result = verify_l5_confirmed_event_candidate_index(project_dir)

            self.assertTrue(result["ok"])
            self.assertEqual(result["final_message"], PASS_MESSAGE)


if __name__ == "__main__":
    unittest.main()
