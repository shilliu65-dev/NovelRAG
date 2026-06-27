import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_character_relation_candidate_graph_builder import run_l3_character_relation_graph_build
from scripts.l3_verify_character_relation_candidate_graph import run_l3_character_relation_graph_verification


def seed_relation_project(project_dir: Path, *, include_scene: bool = False) -> Path:
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE l3_character_def (
                character_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE l3_character_alias (
                alias_id TEXT PRIMARY KEY,
                character_id TEXT NOT NULL,
                alias_text TEXT NOT NULL,
                alias_type TEXT,
                status TEXT NOT NULL
            );
            CREATE TABLE l3_character_appearance (
                appearance_id TEXT PRIMARY KEY,
                character_id TEXT NOT NULL,
                alias_id TEXT NOT NULL,
                matched_text TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                para_id TEXT NOT NULL,
                sentence_id TEXT NOT NULL,
                sentence_start_offset INTEGER NOT NULL,
                sentence_end_offset INTEGER NOT NULL,
                match_start_offset INTEGER NOT NULL,
                match_end_offset INTEGER NOT NULL,
                sentence_hash TEXT NOT NULL,
                paragraph_hash TEXT,
                l1_backcut_matched INTEGER NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE current_chapters_fixture (
                chapter_id TEXT PRIMARY KEY,
                chapter_num INTEGER NOT NULL,
                latest_version_id TEXT NOT NULL,
                content_full_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                content_length INTEGER NOT NULL
            );
            CREATE TABLE l2_current_paragraphs_fixture (
                para_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                para_index INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                para_hash TEXT NOT NULL,
                para_text TEXT
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
                sentence_text TEXT
            );
            CREATE VIEW v_current_chapters AS SELECT * FROM current_chapters_fixture;
            CREATE VIEW v_l2_current_paragraphs AS SELECT * FROM l2_current_paragraphs_fixture;
            CREATE VIEW v_l2_current_sentences AS SELECT * FROM l2_current_sentences_fixture;
            """
        )
        conn.executemany(
            "INSERT INTO l3_character_def (character_id, canonical_name, status) VALUES (?, ?, 'confirmed')",
            [
                ("char_chen_ling", "陈伶"),
                ("char_wang", "王黑子"),
                ("char_han", "韩先生"),
            ],
        )
        conn.executemany(
            "INSERT INTO l3_character_alias (alias_id, character_id, alias_text, alias_type, status) VALUES (?, ?, ?, 'name', 'confirmed')",
            [
                ("alias_chen", "char_chen_ling", "陈伶"),
                ("alias_wang", "char_wang", "王黑子"),
                ("alias_han", "char_han", "韩先生"),
            ],
        )
        conn.execute(
            "INSERT INTO current_chapters_fixture VALUES ('ch_0001', 1, 'ver_0001', 'fixture text', 'chapter_hash', 120)"
        )
        conn.executemany(
            "INSERT INTO l2_current_paragraphs_fixture VALUES (?, 'ch_0001', 1, 'ver_0001', ?, ?, ?, ?, NULL)",
            [
                ("p1", 1, 0, 60, "ph1"),
                ("p2", 2, 61, 120, "ph2"),
            ],
        )
        conn.executemany(
            "INSERT INTO l2_current_sentences_fixture VALUES (?, ?, 'ch_0001', 1, 'ver_0001', ?, ?, ?, NULL)",
            [
                ("s1", "p1", 0, 30, "sh1"),
                ("s2", "p1", 31, 60, "sh2"),
                ("s3", "p2", 61, 90, "sh3"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO l3_character_appearance (
                appearance_id, character_id, alias_id, matched_text,
                chapter_id, chapter_num, version_id, para_id, sentence_id,
                sentence_start_offset, sentence_end_offset,
                match_start_offset, match_end_offset,
                sentence_hash, paragraph_hash, l1_backcut_matched, status
            )
            VALUES (?, ?, ?, ?, 'ch_0001', 1, 'ver_0001', ?, ?, ?, ?, ?, ?, ?, ?, 1, 'candidate')
            """,
            [
                ("app_chen_1", "char_chen_ling", "alias_chen", "陈伶", "p1", "s1", 0, 30, 2, 4, "sh1", "ph1"),
                ("app_chen_2", "char_chen_ling", "alias_chen", "陈伶", "p1", "s1", 0, 30, 20, 22, "sh1", "ph1"),
                ("app_wang_1", "char_wang", "alias_wang", "王黑子", "p1", "s1", 0, 30, 8, 11, "sh1", "ph1"),
                ("app_han_1", "char_han", "alias_han", "韩先生", "p1", "s2", 31, 60, 36, 39, "sh2", "ph1"),
                ("app_wang_2", "char_wang", "alias_wang", "王黑子", "p2", "s3", 61, 90, 70, 73, "sh3", "ph2"),
            ],
        )
        if include_scene:
            conn.executescript(
                """
                CREATE TABLE l3_scene_blocks (
                    scene_id INTEGER PRIMARY KEY,
                    scene_key TEXT NOT NULL UNIQUE,
                    chapter_id TEXT NOT NULL,
                    version_id TEXT NOT NULL,
                    chapter_num INTEGER NOT NULL,
                    scene_index_in_chapter INTEGER NOT NULL,
                    start_para_id TEXT NOT NULL,
                    end_para_id TEXT NOT NULL,
                    start_para_index INTEGER NOT NULL,
                    end_para_index INTEGER NOT NULL,
                    start_sentence_id TEXT,
                    end_sentence_id TEXT,
                    start_offset INTEGER NOT NULL,
                    end_offset INTEGER NOT NULL,
                    length INTEGER NOT NULL,
                    scene_kind TEXT NOT NULL,
                    split_reason TEXT NOT NULL,
                    summary_short TEXT,
                    source_hash TEXT,
                    created_at TEXT
                );
                INSERT INTO l3_scene_blocks (
                    scene_id, scene_key, chapter_id, version_id, chapter_num,
                    scene_index_in_chapter, start_para_id, end_para_id,
                    start_para_index, end_para_index, start_sentence_id,
                    end_sentence_id, start_offset, end_offset, length,
                    scene_kind, split_reason, summary_short, source_hash, created_at
                )
                VALUES (
                    1, 'ch_0001:ver_0001:scene:1', 'ch_0001', 'ver_0001', 1,
                    1, 'p1', 'p1', 1, 1, 's1', 's2', 0, 60, 60,
                    'normal', 'chapter_end', NULL, 'scene_hash', 'now'
                );
                """
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


class L3CharacterRelationCandidateGraphTests(unittest.TestCase):
    def test_builder_creates_only_candidate_mechanical_relation_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_relation_project(project_dir)

            result = run_l3_character_relation_graph_build(
                project_dir,
                rebuild=True,
                scopes=["same_sentence", "same_paragraph"],
            )

            self.assertGreater(result.evidence_count, 0)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                conn.row_factory = sqlite3.Row
                rows = list(conn.execute("SELECT * FROM l3_character_relation_evidence ORDER BY evidence_scope, character_id_a, character_id_b"))
                columns = {row[1] for row in conn.execute("PRAGMA table_info(l3_character_relation_evidence)")}
            finally:
                conn.close()
            self.assertTrue(all(row["status"] == "candidate" for row in rows))
            self.assertTrue(all(row["evidence_scope"] in {"same_sentence", "same_paragraph"} for row in rows))
            self.assertTrue(all(row["character_id_a"] < row["character_id_b"] for row in rows))
            self.assertFalse({"chapter_text", "paragraph_text", "sentence_text", "scene_text", "content_full_text"}.intersection(columns))
            self.assertFalse({"father", "mother", "lover", "enemy", "ally", "teacher", "disciple"}.intersection({row["evidence_scope"] for row in rows}))

    def test_same_sentence_records_multiple_mentions_and_min_offset_distance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_relation_project(project_dir)

            run_l3_character_relation_graph_build(project_dir, rebuild=True, scopes=["same_sentence"])

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                row = conn.execute(
                    """
                    SELECT mention_count_a, mention_count_b, offset_distance_min,
                           appearance_ids_a_json, appearance_ids_b_json
                    FROM l3_character_relation_evidence
                    WHERE evidence_scope = 'same_sentence'
                      AND character_id_a = 'char_chen_ling'
                      AND character_id_b = 'char_wang'
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(row[0], 2)
            self.assertEqual(row[1], 1)
            self.assertEqual(row[2], 6)
            self.assertEqual(json.loads(row[3]), ["app_chen_1", "app_chen_2"])
            self.assertEqual(json.loads(row[4]), ["app_wang_1"])

    def test_same_chapter_nearby_window_and_scene_block_scopes_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_relation_project(project_dir, include_scene=True)

            run_l3_character_relation_graph_build(
                project_dir,
                rebuild=True,
                scopes=["same_chapter", "nearby_window", "same_scene_block"],
                nearby_window_chars=80,
            )

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                scopes = {row[0] for row in conn.execute("SELECT DISTINCT evidence_scope FROM l3_character_relation_evidence")}
                scene_rows = list(conn.execute("SELECT scene_block_id FROM l3_character_relation_evidence WHERE evidence_scope = 'same_scene_block'"))
            finally:
                conn.close()
            self.assertTrue({"same_chapter", "nearby_window", "same_scene_block"}.issubset(scopes))
            self.assertTrue(all(row[0] == "ch_0001:ver_0001:scene:1" for row in scene_rows))

    def test_verifier_fails_for_semantic_relation_type_and_duplicate_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_relation_project(project_dir)
            run_l3_character_relation_graph_build(project_dir, rebuild=True, scopes=["same_sentence"])
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                conn.executescript(
                    """
                    CREATE TABLE relation_copy AS SELECT * FROM l3_character_relation_evidence;
                    DROP TABLE l3_character_relation_evidence;
                    CREATE TABLE l3_character_relation_evidence AS SELECT * FROM relation_copy;
                    INSERT INTO l3_character_relation_evidence SELECT * FROM relation_copy;
                    UPDATE l3_character_relation_evidence SET evidence_scope = 'enemy' WHERE rowid = 1;
                    DROP TABLE relation_copy;
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = run_l3_character_relation_graph_verification(project_dir)

            self.assertFalse(result.ok)
            self.assertGreater(result.checks["forbidden_relation_type"], 0)
            self.assertGreater(result.checks["duplicate_relation_evidence_key"], 0)

    def test_verifier_passes_normal_small_sample(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_relation_project(project_dir, include_scene=True)
            run_l3_character_relation_graph_build(project_dir, rebuild=True, scopes=["same_sentence", "same_paragraph", "same_scene_block"])

            result = run_l3_character_relation_graph_verification(project_dir)

            self.assertTrue(result.ok)
            self.assertEqual(result.final_message, "L3 character relation candidate graph FULL PASS")


if __name__ == "__main__":
    unittest.main()
