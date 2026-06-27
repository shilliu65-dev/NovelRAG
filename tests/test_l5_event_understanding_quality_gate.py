import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_event_understanding_quality_gate import run_l5_event_understanding_quality_gate
from scripts.l5_event_understanding_quality_gate_reporter import run_l5_event_understanding_quality_gate_reporter
from scripts.l5_verify_event_understanding_quality_gate import PASS_MESSAGE, verify_l5_event_understanding_quality_gate


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
                subject_text TEXT,
                evidence_text_backcut TEXT
            );
            CREATE TABLE l5_normalized_event_argument (
                normalized_argument_id TEXT PRIMARY KEY,
                normalized_event_candidate_id TEXT,
                argument_role TEXT,
                argument_text TEXT
            );
            CREATE TABLE l5_normalized_event_evidence (
                normalized_evidence_id TEXT PRIMARY KEY,
                normalized_event_candidate_id TEXT,
                evidence_text TEXT,
                evidence_hash TEXT
            );
            CREATE TABLE l5_review_decision_import (
                review_import_id TEXT PRIMARY KEY,
                review_batch_id TEXT,
                normalized_event_id TEXT,
                human_decision TEXT,
                source_file_hash TEXT,
                is_valid INTEGER,
                validation_errors_json TEXT
            );
            CREATE TABLE l5_review_decision_current (
                normalized_event_id TEXT PRIMARY KEY,
                review_import_id TEXT,
                review_batch_id TEXT,
                human_decision TEXT,
                source_file_hash TEXT,
                current_hash TEXT
            );
            CREATE TABLE l5_review_decision_conflict_audit (
                conflict_id TEXT PRIMARY KEY,
                normalized_event_id TEXT,
                severity TEXT
            );
            CREATE TABLE l5_review_decision_run (
                run_id TEXT PRIMARY KEY,
                source_mutation_detected INTEGER,
                input_mutation_detected INTEGER
            );
            CREATE TABLE l5_confirmed_event_candidate (
                confirmed_event_candidate_id TEXT PRIMARY KEY,
                normalized_event_id TEXT NOT NULL UNIQUE,
                confirmation_status TEXT,
                confirmed_candidate_hash TEXT,
                chapter_num INTEGER,
                scene_block_id TEXT,
                l5_2_event_type TEXT,
                subject_text TEXT,
                created_at TEXT
            );
            CREATE TABLE l5_confirmed_event_argument_candidate (
                confirmed_argument_candidate_id TEXT PRIMARY KEY,
                confirmed_event_candidate_id TEXT,
                normalized_event_id TEXT,
                argument_role TEXT,
                argument_text TEXT
            );
            CREATE TABLE l5_confirmed_event_evidence_span (
                confirmed_evidence_span_id TEXT PRIMARY KEY,
                confirmed_event_candidate_id TEXT,
                normalized_event_id TEXT,
                evidence_text TEXT,
                evidence_hash TEXT
            );
            CREATE TABLE l5_confirmed_event_blocked_audit (
                blocked_audit_id TEXT PRIMARY KEY,
                normalized_event_id TEXT,
                block_reason TEXT
            );
            CREATE TABLE l5_confirmed_event_candidate_run (
                run_id TEXT PRIMARY KEY,
                source_mutation_detected INTEGER,
                input_mutation_detected INTEGER
            );
            CREATE TABLE l5_event_merge_group_candidate (
                merge_group_candidate_id TEXT PRIMARY KEY,
                representative_confirmed_event_candidate_id TEXT,
                group_status TEXT,
                group_rule TEXT,
                member_count INTEGER,
                merge_group_hash TEXT
            );
            CREATE TABLE l5_event_merge_group_member (
                merge_group_member_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT,
                confirmed_event_candidate_id TEXT,
                normalized_event_id TEXT
            );
            CREATE TABLE l5_event_merge_group_evidence (
                merge_group_evidence_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT,
                confirmed_event_candidate_id TEXT
            );
            CREATE TABLE l5_event_merge_group_audit (
                audit_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT,
                audit_type TEXT,
                severity TEXT
            );
            CREATE TABLE l5_event_merge_group_run (
                run_id TEXT PRIMARY KEY,
                source_mutation_detected INTEGER,
                input_mutation_detected INTEGER
            );
            CREATE TABLE l5_timeline_anchor_candidate (
                timeline_anchor_candidate_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT,
                representative_confirmed_event_candidate_id TEXT,
                anchor_status TEXT,
                anchor_type TEXT,
                anchor_hash TEXT
            );
            CREATE TABLE l5_timeline_relative_order_candidate (
                relative_order_candidate_id TEXT PRIMARY KEY,
                source_timeline_anchor_candidate_id TEXT,
                target_timeline_anchor_candidate_id TEXT,
                relation_type TEXT
            );
            CREATE TABLE l5_timeline_anchor_audit (
                audit_id TEXT PRIMARY KEY,
                timeline_anchor_candidate_id TEXT,
                merge_group_candidate_id TEXT,
                audit_type TEXT,
                severity TEXT
            );
            CREATE TABLE l5_timeline_anchor_run (
                run_id TEXT PRIMARY KEY,
                source_mutation_detected INTEGER,
                input_mutation_detected INTEGER
            );
            CREATE TABLE l5_relationship_impact_candidate (
                relationship_impact_candidate_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT,
                timeline_anchor_candidate_id TEXT,
                source_confirmed_event_candidate_id TEXT,
                relationship_impact_hash TEXT
            );
            CREATE TABLE l5_state_impact_candidate (
                state_impact_candidate_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT,
                timeline_anchor_candidate_id TEXT,
                source_confirmed_event_candidate_id TEXT,
                state_impact_hash TEXT
            );
            CREATE TABLE l5_impact_candidate_evidence (
                impact_evidence_id TEXT PRIMARY KEY,
                impact_candidate_type TEXT,
                impact_candidate_id TEXT,
                source_confirmed_event_candidate_id TEXT
            );
            CREATE TABLE l5_relationship_state_impact_audit (
                audit_id TEXT PRIMARY KEY,
                source_confirmed_event_candidate_id TEXT,
                merge_group_candidate_id TEXT,
                audit_type TEXT,
                severity TEXT
            );
            CREATE TABLE l5_relationship_state_impact_run (
                run_id TEXT PRIMARY KEY,
                source_mutation_detected INTEGER,
                input_mutation_detected INTEGER
            );
            """
        )
        for normalized_id, decision in (("n_evt_1", "approved_candidate"), ("n_evt_2", "approved_candidate"), ("n_evt_3", "weak_candidate")):
            conn.execute("INSERT INTO l5_normalized_event_candidate VALUES (?, ?, ?)", (normalized_id, f"subject {normalized_id}", f"evidence {normalized_id}"))
            conn.execute("INSERT INTO l5_normalized_event_evidence VALUES (?, ?, ?, ?)", (f"ne_{normalized_id}", normalized_id, f"evidence {normalized_id}", f"hash_{normalized_id}"))
            conn.execute("INSERT INTO l5_review_decision_import VALUES (?, 'batch', ?, ?, 'filehash', 1, '[]')", (f"ri_{normalized_id}", normalized_id, decision))
            conn.execute("INSERT INTO l5_review_decision_current VALUES (?, ?, 'batch', ?, 'filehash', 'currenthash')", (normalized_id, f"ri_{normalized_id}", decision))

        for idx in (1, 2):
            confirmed_id = f"c_evt_{idx}"
            normalized_id = f"n_evt_{idx}"
            group_id = f"mg_{idx}"
            anchor_id = f"ta_{idx}"
            state_id = f"state_{idx}"
            conn.execute("INSERT INTO l5_confirmed_event_candidate VALUES (?, ?, 'confirmed_candidate', ?, ?, ?, ?, ?, '1970-01-01T00:00:00')", (confirmed_id, normalized_id, f"chash_{idx}", idx, f"scene_{idx}", "attack", f"subject {idx}"))
            conn.execute("INSERT INTO l5_confirmed_event_evidence_span VALUES (?, ?, ?, ?, ?)", (f"ce_{idx}", confirmed_id, normalized_id, f"confirmed evidence {idx}", f"cehash_{idx}"))
            conn.execute("INSERT INTO l5_event_merge_group_candidate VALUES (?, ?, 'merge_group_candidate', 'singleton_group', 1, ?)", (group_id, confirmed_id, f"mhash_{idx}"))
            conn.execute("INSERT INTO l5_event_merge_group_member VALUES (?, ?, ?, ?)", (f"mem_{idx}", group_id, confirmed_id, normalized_id))
            conn.execute("INSERT INTO l5_timeline_anchor_candidate VALUES (?, ?, ?, 'timeline_anchor_candidate', 'chapter_order_anchor', ?)", (anchor_id, group_id, confirmed_id, f"ahash_{idx}"))
            conn.execute("INSERT INTO l5_state_impact_candidate VALUES (?, ?, ?, ?, ?)", (state_id, group_id, anchor_id, confirmed_id, f"shash_{idx}"))
            conn.execute("INSERT INTO l5_impact_candidate_evidence VALUES (?, 'state', ?, ?)", (f"ie_{idx}", state_id, confirmed_id))
        conn.execute("INSERT INTO l5_timeline_relative_order_candidate VALUES ('rel_1', 'ta_1', 'ta_2', 'before')")
        for table in (
            "l5_review_decision_run",
            "l5_confirmed_event_candidate_run",
            "l5_event_merge_group_run",
            "l5_timeline_anchor_run",
            "l5_relationship_state_impact_run",
        ):
            conn.execute(f"INSERT INTO {table} VALUES ('run', 0, 0)")
        conn.commit()
    finally:
        conn.close()


class L5EventUnderstandingQualityGateTests(unittest.TestCase):
    def test_quality_gate_creates_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(
                    {
                        "l5_event_quality_gate_summary",
                        "l5_event_quality_gate_chain",
                        "l5_event_quality_gate_issue",
                        "l5_event_quality_gate_metric",
                        "l5_event_quality_gate_run",
                    }.issubset(tables)
                )
            finally:
                conn.close()

    def test_quality_gate_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            counts = manifest["row_counts"]
            self.assertEqual(counts["normalized_event_count"], 3)
            self.assertEqual(counts["confirmed_event_candidate_count"], 2)
            self.assertEqual(counts["merge_group_candidate_count"], 2)
            self.assertEqual(counts["timeline_anchor_candidate_count"], 2)
            self.assertEqual(counts["relationship_impact_candidate_count"], 0)
            self.assertEqual(counts["state_impact_candidate_count"], 2)
            self.assertEqual(counts["partial_chain_count"], 2)
            self.assertEqual(counts["blocked_chain_count"], 1)
            self.assertNotEqual(manifest["quality_gate_status"], "quality_fail")

    def test_quality_gate_chain_covers_confirmed_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_confirmed_event_candidate c
                    LEFT JOIN l5_event_quality_gate_chain q
                      ON q.confirmed_event_candidate_id = c.confirmed_event_candidate_id
                    WHERE q.chain_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()

    def test_quality_gate_detects_singleton_only_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            self.assertGreaterEqual(manifest["issue_type_counts"].get("singleton_only", 0), 1)

    def test_quality_gate_detects_fallback_anchor_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            self.assertGreaterEqual(manifest["issue_type_counts"].get("fallback_timeline_anchor", 0), 1)

    def test_quality_gate_detects_missing_relationship_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            self.assertGreaterEqual(manifest["issue_type_counts"].get("missing_relationship_impact", 0), 1)

    def test_quality_gate_forbidden_final_table_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            self.assertEqual(manifest["forbidden_final_table_count"], 0)

    def test_quality_gate_mutation_flags_false(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            self.assertFalse(manifest["source_mutation_detected"])
            self.assertFalse(manifest["input_mutation_detected"])

    def test_quality_gate_reporter_outputs_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_event_understanding_quality_gate(project_dir, rebuild=True)
            report = run_l5_event_understanding_quality_gate_reporter(project_dir)

            self.assertEqual(report["row_counts"]["chain_count"], 3)
            self.assertTrue((project_dir / "outputs" / "l5_event_quality_gate_summary.json").exists())
            self.assertTrue((project_dir / "outputs" / "l5_event_quality_gate_summary.md").exists())
            self.assertTrue((project_dir / "outputs" / "l5_event_quality_gate_metrics.csv").exists())

    def test_quality_gate_verifier_full_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            result = verify_l5_event_understanding_quality_gate(project_dir)

            self.assertTrue(result["ok"])
            self.assertEqual(result["final_message"], PASS_MESSAGE)

    def test_rebuild_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            first = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)
            second = run_l5_event_understanding_quality_gate(project_dir, rebuild=True)

            self.assertEqual(first["stable_output_hashes"], second["stable_output_hashes"])
            self.assertFalse(second["source_mutation_detected"])
            self.assertFalse(second["input_mutation_detected"])


if __name__ == "__main__":
    unittest.main()
