import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_event_merge_group_candidate_indexer import run_l5_event_merge_group_candidate_indexer
from scripts.l5_event_merge_group_candidate_reporter import run_l5_event_merge_group_candidate_reporter
from scripts.l5_verify_event_merge_group_candidate import PASS_MESSAGE, verify_l5_event_merge_group_candidate


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
            CREATE TABLE l5_confirmed_event_argument_candidate (
                confirmed_argument_candidate_id TEXT PRIMARY KEY,
                confirmed_event_candidate_id TEXT NOT NULL,
                normalized_argument_id TEXT NOT NULL,
                normalized_event_id TEXT NOT NULL,
                source_event_candidate_id TEXT NOT NULL,
                argument_role TEXT NOT NULL,
                entity_kind TEXT,
                argument_text TEXT NOT NULL,
                confidence REAL,
                source_kind TEXT,
                source_rule TEXT,
                evidence_text TEXT,
                l5_2_seed_checksum TEXT NOT NULL,
                stable_hash TEXT NOT NULL,
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
            CREATE TABLE l5_confirmed_event_blocked_audit (
                blocked_audit_id TEXT PRIMARY KEY,
                normalized_event_id TEXT NOT NULL,
                current_decision_id TEXT,
                review_batch_id TEXT,
                review_source_file_hash TEXT,
                block_reason TEXT NOT NULL,
                detail_message TEXT,
                normalized_event_present INTEGER NOT NULL,
                decision_present INTEGER NOT NULL,
                conflict_present INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE l5_confirmed_event_candidate_run (
                run_id TEXT PRIMARY KEY,
                input_current_decision_count INTEGER NOT NULL,
                approved_current_decision_count INTEGER NOT NULL,
                confirmed_event_candidate_count INTEGER NOT NULL,
                confirmed_event_argument_candidate_count INTEGER NOT NULL,
                confirmed_event_evidence_span_count INTEGER NOT NULL,
                blocked_audit_count INTEGER NOT NULL,
                source_fingerprints_before_json TEXT NOT NULL,
                source_fingerprints_after_json TEXT NOT NULL,
                source_mutation_detected INTEGER NOT NULL,
                input_fingerprints_before_json TEXT NOT NULL,
                input_fingerprints_after_json TEXT NOT NULL,
                input_mutation_detected INTEGER NOT NULL,
                stable_output_hashes_json TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT NOT NULL,
                status TEXT NOT NULL,
                note TEXT
            );
            """
        )
        rows = [
            ("c_evt_1", "n_evt_1", 1, "scene_1", "attack", "wound", "Chen"),
            ("c_evt_2", "n_evt_2", 1, "scene_1", "attack", "wound", "Chen"),
            ("c_evt_3", "n_evt_3", 2, "scene_9", "movement", "leave", "Lin"),
        ]
        for confirmed_id, normalized_id, chapter_num, scene_id, event_type, event_subtype, subject in rows:
            conn.execute(
                """
                INSERT INTO l5_confirmed_event_candidate VALUES (
                    ?, ?, ?, 'batch_1', 'source_hash', 'confirmed_candidate', ?,
                    ?, 'ch', ?, 'title', 'ver', ?, 'l3_scene_blocks', ?, ?, ?,
                    'character', 0.9, 'ok', ?, '1970-01-01T00:00:00'
                )
                """,
                (
                    confirmed_id,
                    normalized_id,
                    f"decision_{normalized_id}",
                    f"hash_{confirmed_id}",
                    f"source_{normalized_id}",
                    chapter_num,
                    scene_id,
                    event_type,
                    event_subtype,
                    subject,
                    f"stable_{confirmed_id}",
                ),
            )
            conn.execute(
                "INSERT INTO l5_confirmed_event_argument_candidate VALUES (?, ?, ?, ?, ?, 'subject', 'character', ?, 0.9, 'fixture', 'fixture', '', 'seed', ?, '1970-01-01T00:00:00')",
                (f"arg_{confirmed_id}", confirmed_id, f"n_arg_{confirmed_id}", normalized_id, f"source_{normalized_id}", subject, f"arg_hash_{confirmed_id}"),
            )
            conn.execute(
                "INSERT INTO l5_confirmed_event_argument_candidate VALUES (?, ?, ?, ?, ?, 'object', 'character', ?, 0.8, 'fixture', 'fixture', '', 'seed', ?, '1970-01-01T00:00:00')",
                (f"obj_{confirmed_id}", confirmed_id, f"n_obj_{confirmed_id}", normalized_id, f"source_{normalized_id}", "Target" if confirmed_id != "c_evt_3" else "Gate", f"obj_hash_{confirmed_id}"),
            )
            conn.execute(
                "INSERT INTO l5_confirmed_event_evidence_span VALUES (?, ?, ?, ?, ?, 'l2_sentence', 'sent_1', 'para_1', 0, 20, ?, ?, 'ok', 'seed', ?, '1970-01-01T00:00:00')",
                (f"evd_{confirmed_id}", confirmed_id, f"n_evd_{confirmed_id}", normalized_id, f"source_{normalized_id}", f"evidence for {confirmed_id}", f"evidence_hash_{confirmed_id}", f"evd_hash_{confirmed_id}"),
            )
        conn.execute("INSERT INTO l5_confirmed_event_candidate_run VALUES ('run_1', 3, 3, 3, 6, 3, 0, '{}', '{}', 0, '{}', '{}', 0, '{}', '1970-01-01T00:00:00', '1970-01-01T00:00:00', 'ok', '')")
        conn.commit()
    finally:
        conn.close()


class L5EventMergeGroupCandidateTests(unittest.TestCase):
    def test_merge_group_creates_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(
                    {
                        "l5_event_merge_group_candidate",
                        "l5_event_merge_group_member",
                        "l5_event_merge_group_evidence",
                        "l5_event_merge_group_audit",
                        "l5_event_merge_group_run",
                    }.issubset(tables)
                )
            finally:
                conn.close()

    def test_every_confirmed_candidate_has_one_group(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                rows = conn.execute(
                    """
                    SELECT confirmed_event_candidate_id, COUNT(*)
                    FROM l5_event_merge_group_member
                    GROUP BY confirmed_event_candidate_id
                    """
                ).fetchall()
                self.assertEqual(sorted(count for _, count in rows), [1, 1, 1])
            finally:
                conn.close()

    def test_singleton_group_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            self.assertEqual(manifest["row_counts"]["singleton_group_count"], 1)

    def test_multi_member_group_rule_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute("SELECT * FROM l5_event_merge_group_candidate WHERE member_count = 2").fetchone()
                self.assertIsNotNone(row)
                self.assertTrue(row["group_rule"])
                members = conn.execute("SELECT membership_score FROM l5_event_merge_group_member WHERE merge_group_candidate_id = ?", (row["merge_group_candidate_id"],)).fetchall()
                self.assertTrue(all(member[0] is not None for member in members))
            finally:
                conn.close()

    def test_representative_is_group_member(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            conn.row_factory = sqlite3.Row
            try:
                groups = conn.execute("SELECT * FROM l5_event_merge_group_candidate").fetchall()
                for group in groups:
                    count = conn.execute(
                        """
                        SELECT COUNT(*) FROM l5_event_merge_group_member
                        WHERE merge_group_candidate_id = ? AND confirmed_event_candidate_id = ?
                        """,
                        (group["merge_group_candidate_id"], group["representative_confirmed_event_candidate_id"]),
                    ).fetchone()[0]
                    self.assertEqual(count, 1)
            finally:
                conn.close()

    def test_members_link_back_to_l5_5(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_event_merge_group_member m
                    LEFT JOIN l5_confirmed_event_candidate c
                      ON c.confirmed_event_candidate_id = m.confirmed_event_candidate_id
                    WHERE c.confirmed_event_candidate_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()

    def test_no_final_merged_event_table_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertFalse({"final_merged_event", "l5_merged_event", "final_event"}.intersection(tables))
            finally:
                conn.close()

    def test_merge_group_reporter_outputs_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)
            report = run_l5_event_merge_group_candidate_reporter(project_dir)

            self.assertEqual(report["row_counts"]["merge_group_candidate_count"], 2)
            self.assertTrue((project_dir / "outputs" / "l5_event_merge_group_candidates.csv").exists())
            self.assertTrue((project_dir / "outputs" / "l5_event_merge_group_candidate_report.md").exists())

    def test_merge_group_verifier_full_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            result = verify_l5_event_merge_group_candidate(project_dir)

            self.assertTrue(result["ok"])
            self.assertEqual(result["final_message"], PASS_MESSAGE)

    def test_rebuild_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            first = run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)
            second = run_l5_event_merge_group_candidate_indexer(project_dir, rebuild=True)

            self.assertEqual(first["stable_output_hashes"], second["stable_output_hashes"])
            self.assertFalse(second["source_mutation_detected"])
            self.assertFalse(second["input_mutation_detected"])


if __name__ == "__main__":
    unittest.main()
