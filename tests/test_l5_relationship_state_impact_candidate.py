import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l5_relationship_state_impact_candidate_indexer import run_l5_relationship_state_impact_candidate_indexer
from scripts.l5_relationship_state_impact_candidate_reporter import run_l5_relationship_state_impact_candidate_reporter
from scripts.l5_verify_relationship_state_impact_candidate import PASS_MESSAGE, verify_l5_relationship_state_impact_candidate


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
            CREATE TABLE l5_timeline_anchor_candidate (
                timeline_anchor_candidate_id TEXT PRIMARY KEY,
                merge_group_candidate_id TEXT NOT NULL,
                representative_confirmed_event_candidate_id TEXT NOT NULL,
                anchor_status TEXT NOT NULL,
                anchor_type TEXT NOT NULL,
                era_id TEXT,
                chapter_num INTEGER,
                narrative_order_key TEXT,
                absolute_order_key TEXT,
                scene_order_index INTEGER,
                evidence_order_index INTEGER,
                is_flashback_candidate INTEGER NOT NULL,
                time_text TEXT,
                location_text TEXT,
                confidence TEXT,
                anchor_rule TEXT NOT NULL,
                anchor_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE l5_timeline_relative_order_candidate (
                relative_order_candidate_id TEXT PRIMARY KEY,
                source_timeline_anchor_candidate_id TEXT NOT NULL,
                target_timeline_anchor_candidate_id TEXT NOT NULL,
                relation_type TEXT NOT NULL,
                relation_rule TEXT NOT NULL,
                confidence TEXT,
                relation_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        events = [
            ("c_evt_1", "n_evt_1", "attack", "Chen", "Lin", ""),
            ("c_evt_2", "n_evt_2", "movement", "Lin", "Lin", "Gate"),
            ("c_evt_3", "n_evt_3", "power_upgrade", "Bai", "", ""),
        ]
        for idx, (confirmed_id, normalized_id, event_type, subject, obj, location) in enumerate(events, start=1):
            group_id = f"mg_{idx}"
            anchor_id = f"ta_{idx}"
            conn.execute(
                "INSERT INTO l5_confirmed_event_candidate VALUES (?, ?, 'decision', 'batch', 'source_hash', 'confirmed_candidate', ?, 'src', 'ch', ?, 'title', 'ver', ?, 'l3_scene_blocks', ?, '', ?, 'character', 0.9, 'ok', ?, '1970-01-01T00:00:00')",
                (confirmed_id, normalized_id, f"hash_{confirmed_id}", idx, f"scene_{idx}", event_type, subject, f"stable_{confirmed_id}"),
            )
            conn.execute(
                "INSERT INTO l5_confirmed_event_argument_candidate VALUES (?, ?, ?, ?, 'src', 'subject', 'character', ?, 0.9, 'fixture', 'fixture', '', 'seed', ?, '1970-01-01T00:00:00')",
                (f"arg_sub_{idx}", confirmed_id, f"n_arg_sub_{idx}", normalized_id, subject, f"arg_hash_sub_{idx}"),
            )
            if obj:
                conn.execute(
                    "INSERT INTO l5_confirmed_event_argument_candidate VALUES (?, ?, ?, ?, 'src', 'object', 'character', ?, 0.8, 'fixture', 'fixture', '', 'seed', ?, '1970-01-01T00:00:00')",
                    (f"arg_obj_{idx}", confirmed_id, f"n_arg_obj_{idx}", normalized_id, obj, f"arg_hash_obj_{idx}"),
                )
            if location:
                conn.execute(
                    "INSERT INTO l5_confirmed_event_argument_candidate VALUES (?, ?, ?, ?, 'src', 'location', 'location', ?, 0.8, 'fixture', 'fixture', '', 'seed', ?, '1970-01-01T00:00:00')",
                    (f"arg_loc_{idx}", confirmed_id, f"n_arg_loc_{idx}", normalized_id, location, f"arg_hash_loc_{idx}"),
                )
            conn.execute(
                "INSERT INTO l5_confirmed_event_evidence_span VALUES (?, ?, ?, ?, 'src', 'l2_sentence', ?, ?, 0, 20, ?, ?, 'ok', 'seed', ?, '1970-01-01T00:00:00')",
                (f"evd_{idx}", confirmed_id, f"n_evd_{idx}", normalized_id, f"sent_{idx}", f"para_{idx}", f"evidence {confirmed_id}", f"ev_hash_{idx}", f"evd_hash_{idx}"),
            )
            conn.execute(
                "INSERT INTO l5_event_merge_group_candidate VALUES (?, ?, ?, 'merge_group_candidate', 'singleton_group', 'medium', 1, ?, ?, ?, ?, '', ?, ?, ?, '', '', ?, '1970-01-01T00:00:00')",
                (group_id, confirmed_id, f"sig_{idx}", idx, idx, f"scene_{idx}", event_type, event_type, subject, obj, f"mg_hash_{idx}"),
            )
            conn.execute(
                "INSERT INTO l5_event_merge_group_member VALUES (?, ?, ?, ?, 'representative', 1, 'singleton_group', 0.5, ?, '1970-01-01T00:00:00')",
                (f"mem_{idx}", group_id, confirmed_id, normalized_id, f"mem_hash_{idx}"),
            )
            conn.execute(
                "INSERT INTO l5_timeline_anchor_candidate VALUES (?, ?, ?, 'timeline_anchor_candidate', 'chapter_order_anchor', '', ?, ?, '', ?, 0, 0, '', ?, 'low', 'chapter_order_anchor', ?, '1970-01-01T00:00:00')",
                (anchor_id, group_id, confirmed_id, idx, f"ch{idx:06d}", idx, location, f"anchor_hash_{idx}"),
            )
        conn.execute("INSERT INTO l5_timeline_relative_order_candidate VALUES ('rel_1', 'ta_1', 'ta_2', 'before', 'narrative_order_key', 'medium', 'rel_hash_1', '1970-01-01T00:00:00')")
        conn.commit()
    finally:
        conn.close()


class L5RelationshipStateImpactCandidateTests(unittest.TestCase):
    def test_impact_candidate_creates_tables(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue(
                    {
                        "l5_relationship_impact_candidate",
                        "l5_state_impact_candidate",
                        "l5_impact_candidate_evidence",
                        "l5_relationship_state_impact_audit",
                        "l5_relationship_state_impact_run",
                    }.issubset(tables)
                )
            finally:
                conn.close()

    def test_relationship_candidates_link_back_to_l5_5_l5_6(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_relationship_impact_candidate r
                    LEFT JOIN l5_confirmed_event_candidate c
                      ON c.confirmed_event_candidate_id = r.source_confirmed_event_candidate_id
                    LEFT JOIN l5_event_merge_group_candidate g
                      ON g.merge_group_candidate_id = r.merge_group_candidate_id
                    WHERE c.confirmed_event_candidate_id IS NULL OR g.merge_group_candidate_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()

    def test_state_candidates_link_back_to_l5_5_l5_6(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_state_impact_candidate s
                    LEFT JOIN l5_confirmed_event_candidate c
                      ON c.confirmed_event_candidate_id = s.source_confirmed_event_candidate_id
                    LEFT JOIN l5_event_merge_group_candidate g
                      ON g.merge_group_candidate_id = s.merge_group_candidate_id
                    WHERE c.confirmed_event_candidate_id IS NULL OR g.merge_group_candidate_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()

    def test_timeline_anchor_optional_link(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_state_impact_candidate s
                    LEFT JOIN l5_timeline_anchor_candidate a
                      ON a.timeline_anchor_candidate_id = s.timeline_anchor_candidate_id
                    WHERE s.timeline_anchor_candidate_id <> '' AND a.timeline_anchor_candidate_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(missing, 0)
            finally:
                conn.close()

    def test_same_subject_object_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            self.assertGreaterEqual(manifest["audit_type_counts"].get("same_subject_object", 0), 1)

    def test_raw_text_entity_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            manifest = run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            self.assertGreaterEqual(manifest["audit_type_counts"].get("raw_text_entity_fallback", 0), 1)

    def test_impact_evidence_links_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                relationship_missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_impact_candidate_evidence e
                    LEFT JOIN l5_relationship_impact_candidate r
                      ON r.relationship_impact_candidate_id = e.impact_candidate_id
                    WHERE e.impact_candidate_type = 'relationship'
                      AND r.relationship_impact_candidate_id IS NULL
                    """
                ).fetchone()[0]
                state_missing = conn.execute(
                    """
                    SELECT COUNT(*)
                    FROM l5_impact_candidate_evidence e
                    LEFT JOIN l5_state_impact_candidate s
                      ON s.state_impact_candidate_id = e.impact_candidate_id
                    WHERE e.impact_candidate_type = 'state'
                      AND s.state_impact_candidate_id IS NULL
                    """
                ).fetchone()[0]
                self.assertEqual(relationship_missing, 0)
                self.assertEqual(state_missing, 0)
            finally:
                conn.close()

    def test_no_final_relationship_graph_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertFalse({"final_relationship_graph", "relationship_graph", "l5_final_relationship_graph"}.intersection(tables))
            finally:
                conn.close()

    def test_no_final_state_machine_created(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertFalse({"final_state_machine", "state_machine", "l5_final_state_machine"}.intersection(tables))
            finally:
                conn.close()

    def test_impact_candidate_verifier_full_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            result = verify_l5_relationship_state_impact_candidate(project_dir)

            self.assertTrue(result["ok"])
            self.assertEqual(result["final_message"], PASS_MESSAGE)

    def test_rebuild_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)

            first = run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)
            second = run_l5_relationship_state_impact_candidate_indexer(project_dir, rebuild=True)

            self.assertEqual(first["stable_output_hashes"], second["stable_output_hashes"])
            self.assertFalse(second["source_mutation_detected"])
            self.assertFalse(second["input_mutation_detected"])


if __name__ == "__main__":
    unittest.main()
