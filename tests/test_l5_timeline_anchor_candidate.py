import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_timeline_anchor_candidate_indexer import run_l5_timeline_anchor_candidate_indexer
from scripts.l5_timeline_anchor_candidate_reporter import run_l5_timeline_anchor_candidate_reporter
from scripts.l5_verify_timeline_anchor_candidate import PASS_MESSAGE, verify_l5_timeline_anchor_candidate


def seed_project(project_dir: Path) -> None:
    (project_dir / "index").mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    db_path = project_dir / "index" / "novel_story_bible.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE l5_confirmed_event_candidate (
                confirmed_event_candidate_id TEXT PRIMARY KEY,
                normalized_event_id TEXT NOT NULL UNIQUE,
                current_decision_id TEXT NOT NULL,
                review_batch_id TEXT NOT NULL,
                review_source_file_hash TEXT NOT NULL,
                confirmation_status TEXT NOT NULL,
                confirmed_candidate_hash TEXT NOT NULL,
                source_event_candidate_id TEXT NOT NULL,
                chapter_id TEXT,
                chapter_num INTEGER,
                chapter_title TEXT,
                version_id TEXT,
                scene_block_id TEXT,
                scene_block_table_name TEXT,
                l5_2_event_type TEXT NOT NULL,
                l5_2_event_subtype TEXT,
                subject_text TEXT NOT NULL,
                subject_entity_kind TEXT,
                normalized_confidence_score REAL,
                evidence_backcut_status TEXT,
                source_normalized_stable_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE l5_confirmed_event_evidence_span (
                confirmed_evidence_span_id TEXT PRIMARY KEY,
                confirmed_event_candidate_id TEXT NOT NULL,
                normalized_evidence_id TEXT NOT NULL,
                normalized_event_id TEXT NOT NULL,
                source_event_candidate_id TEXT NOT NULL,
                evidence_source_kind TEXT,
                l2_sentence_id TEXT,
                l2_paragraph_id TEXT,
                start_offset INTEGER,
                end_offset INTEGER,
                evidence_text TEXT NOT NULL,
                evidence_hash TEXT NOT NULL,
                evidence_backcut_status TEXT NOT NULL,
                l5_2_seed_checksum TEXT NOT NULL,
                stable_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE l5_event_merge_group_candidate (
                merge_group_candidate_id TEXT PRIMARY KEY,
                representative_confirmed_event_candidate_id TEXT NOT NULL,
                group_signature TEXT NOT NULL,
                group_status TEXT NOT NULL,
                group_rule TEXT NOT NULL,
                group_confidence TEXT,
                member_count INTEGER NOT NULL,
                chapter_num_min INTEGER,
                chapter_num_max INTEGER,
                scene_block_id_primary TEXT,
                event_type TEXT,
                event_subtype TEXT,
                predicate_canonical TEXT,
                subject_text TEXT,
                object_text TEXT,
                location_text TEXT,
                time_text TEXT,
                merge_group_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE l5_event_merge_group_member (
                merge_group_member_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT NOT NULL,
                confirmed_event_candidate_id TEXT NOT NULL,
                normalized_event_id TEXT NOT NULL,
                member_role TEXT NOT NULL,
                member_rank INTEGER NOT NULL,
                membership_rule TEXT NOT NULL,
                membership_score REAL,
                member_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        groups = [
            ("mg_1", "c_evt_1", 1, "scene_1", "attack", "Chen"),
            ("mg_2", "c_evt_2", 2, "scene_2", "movement", "Lin"),
        ]
        for group_id, confirmed_id, chapter_num, scene_id, event_type, subject in groups:
            conn.execute(
                "INSERT INTO l5_confirmed_event_candidate VALUES (?, ?, 'decision', 'batch', 'source_hash', 'confirmed_candidate', ?, 'src', 'ch', ?, 'title', 'ver', ?, 'l3_scene_blocks', ?, '', ?, 'character', 0.9, 'ok', ?, '1970-01-01T00:00:00')",
                (confirmed_id, f"n_{confirmed_id}", f"hash_{confirmed_id}", chapter_num, scene_id, event_type, subject, f"stable_{confirmed_id}"),
            )
            conn.execute(
                "INSERT INTO l5_confirmed_event_evidence_span VALUES (?, ?, ?, ?, 'src', 'l2_sentence', ?, ?, ?, ?, ?, ?, 'ok', 'seed', ?, '1970-01-01T00:00:00')",
                (f"evd_{confirmed_id}", confirmed_id, f"n_evd_{confirmed_id}", f"n_{confirmed_id}", f"sent_{chapter_num}", f"para_{chapter_num}", chapter_num * 10, chapter_num * 10 + 5, f"evidence {confirmed_id}", f"ev_hash_{confirmed_id}", f"stable_evd_{confirmed_id}"),
            )
            conn.execute(
                "INSERT INTO l5_event_merge_group_candidate VALUES (?, ?, ?, 'merge_group_candidate', 'singleton_group', 'medium', 1, ?, ?, ?, ?, '', ?, ?, '', '', '', ?, '1970-01-01T00:00:00')",
                (group_id, confirmed_id, f"sig_{group_id}", chapter_num, chapter_num, scene_id, event_type, event_type, subject, f"hash_{group_id}"),
            )
            conn.execute(
                "INSERT INTO l5_event_merge_group_member VALUES (?, ?, ?, ?, 'representative', 1, 'singleton_group', 0.5, ?, '1970-01-01T00:00:00')",
                (f"mem_{group_id}", group_id, confirmed_id, f"n_{confirmed_id}", f"mem_hash_{group_id}"),
            )
        conn.commit()
    finally:
        conn.close()


class L5TimelineAnchorCandidateTests(unittest.TestCase):
    def test_timeline_anchor_creates_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(
                    {
                        "l5_timeline_anchor_candidate",
                        "l5_timeline_relative_order_candidate",
                        "l5_timeline_anchor_audit",
                        "l5_timeline_anchor_run",
                    }.issubset(tables)
                )
            finally:
                conn.close()

    def test_every_merge_group_has_one_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                rows = conn.execute(
                    """
                    SELECT merge_group_candidate_id, COUNT(*)
                    FROM l5_timeline_anchor_candidate
                    GROUP BY merge_group_candidate_id
                    """
                ).fetchall()
                self.assertEqual(sorted(count for _, count in rows), [1, 1])
            finally:
                conn.close()

    def test_anchor_links_back_to_l5_6(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_timeline_anchor_candidate a
                    LEFT JOIN l5_event_merge_group_candidate g
                      ON g.merge_group_candidate_id = a.merge_group_candidate_id
                    WHERE g.merge_group_candidate_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()

    def test_representative_links_back_to_l5_5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_timeline_anchor_candidate a
                    LEFT JOIN l5_confirmed_event_candidate c
                      ON c.confirmed_event_candidate_id = a.representative_confirmed_event_candidate_id
                    WHERE c.confirmed_event_candidate_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()

    def test_fallback_to_chapter_order_when_l3_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            self.assertEqual(manifest["row_counts"]["fallback_anchor_count"], 2)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                types = {row[0] for row in conn.execute("SELECT anchor_type FROM l5_timeline_anchor_candidate")}
                self.assertEqual(types, {"chapter_order_anchor"})
            finally:
                conn.close()

    def test_relative_order_no_self_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                self_refs = conn.execute(
                    """
                    SELECT COUNT(*) FROM l5_timeline_relative_order_candidate
                    WHERE source_timeline_anchor_candidate_id = target_timeline_anchor_candidate_id
                    """
                ).fetchone()[0]
                self.assertEqual(self_refs, 0)
            finally:
                conn.close()

    def test_no_final_timeline_table_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertFalse({"final_timeline", "final_timeline_node", "l5_final_timeline"}.intersection(tables))
            finally:
                conn.close()

    def test_timeline_anchor_reporter_outputs_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)
            report = run_l5_timeline_anchor_candidate_reporter(project_dir)

            self.assertEqual(report["row_counts"]["timeline_anchor_candidate_count"], 2)
            self.assertTrue((project_dir / "outputs" / "l5_timeline_anchor_candidates.csv").exists())
            self.assertTrue((project_dir / "outputs" / "l5_timeline_anchor_candidate_report.md").exists())

    def test_timeline_anchor_verifier_full_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            result = verify_l5_timeline_anchor_candidate(project_dir)

            self.assertTrue(result["ok"])
            self.assertEqual(result["final_message"], PASS_MESSAGE)

    def test_rebuild_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            first = run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)
            second = run_l5_timeline_anchor_candidate_indexer(project_dir, rebuild=True)

            self.assertEqual(first["stable_output_hashes"], second["stable_output_hashes"])
            self.assertFalse(second["source_mutation_detected"])
            self.assertFalse(second["input_mutation_detected"])


if __name__ == "__main__":
    unittest.main()
