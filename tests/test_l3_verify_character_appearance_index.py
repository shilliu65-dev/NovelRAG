import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_character_appearance_indexer import run_l3_character_appearance_indexer
from scripts.l3_verify_character_appearance_index import run_l3_character_appearance_verification


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_project(project_dir: Path, text: str = "先生在台下沉默。") -> Path:
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
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
            CREATE TABLE l2_current_paragraphs_fixture (
                para_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                para_index INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                char_length INTEGER NOT NULL,
                para_hash TEXT NOT NULL,
                para_kind TEXT NOT NULL,
                para_text TEXT NOT NULL,
                chapter_title_current TEXT NOT NULL
            );
            CREATE TABLE l2_current_sentences_fixture (
                sentence_id TEXT PRIMARY KEY,
                para_id TEXT NOT NULL,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                para_index INTEGER NOT NULL,
                sentence_index INTEGER NOT NULL,
                global_sentence_index INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                char_length INTEGER NOT NULL,
                sentence_hash TEXT NOT NULL,
                sentence_text TEXT NOT NULL,
                chapter_title_current TEXT NOT NULL
            );
            CREATE VIEW v_current_chapters AS SELECT * FROM current_chapters_fixture;
            CREATE VIEW v_l2_current_paragraphs AS SELECT * FROM l2_current_paragraphs_fixture;
            CREATE VIEW v_l2_current_sentences AS SELECT * FROM l2_current_sentences_fixture;
            """
        )
        conn.execute(
            """
            INSERT INTO current_chapters_fixture (
                chapter_id, chapter_num, chapter_title_current, latest_version_id,
                content_full_text, content_hash, content_length
            )
            VALUES ('ch_0001', 1, '第一章', 'ver_0001', ?, ?, ?)
            """,
            (text, sha256_text(text), len(text)),
        )
        conn.execute(
            """
            INSERT INTO l2_current_paragraphs_fixture (
                para_id, chapter_id, chapter_num, version_id, para_index,
                start_offset, end_offset, char_length, para_hash, para_kind,
                para_text, chapter_title_current
            )
            VALUES ('p_1', 'ch_0001', 1, 'ver_0001', 1, 0, ?, ?, ?, 'body', ?, '第一章')
            """,
            (len(text), len(text), sha256_text(text), text),
        )
        conn.execute(
            """
            INSERT INTO l2_current_sentences_fixture (
                sentence_id, para_id, chapter_id, chapter_num, version_id,
                para_index, sentence_index, global_sentence_index,
                start_offset, end_offset, char_length, sentence_hash,
                sentence_text, chapter_title_current
            )
            VALUES ('s_1', 'p_1', 'ch_0001', 1, 'ver_0001', 1, 1, 0, 0, ?, ?, ?, ?, '第一章')
            """,
            (len(text), len(text), sha256_text(text), text),
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


def write_characters(project_dir: Path, payload: dict | None = None) -> Path:
    path = project_dir / "outputs" / "characters.json"
    if payload is None:
        payload = {
            "characters": [
                {
                    "character_id": "char_lou_yu",
                    "name": "楼羽",
                    "status": "confirmed",
                    "aliases": [{"alias_id": "alias_sir_lou", "alias_text": "先生", "valid_from_chapter_num": 1, "valid_to_chapter_num": 1, "status": "confirmed"}],
                }
            ]
        }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def build_valid_index(project_dir: Path, text: str = "先生在台下沉默。") -> None:
    seed_project(project_dir, text)
    run_l3_character_appearance_indexer(project_dir, write_characters(project_dir), rebuild=True)


class L3VerifyCharacterAppearanceIndexTests(unittest.TestCase):
    def test_normal_small_sample_full_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            build_valid_index(project_dir)

            result = run_l3_character_appearance_verification(project_dir)

            self.assertTrue(result.ok)
            self.assertEqual(result.final_message, "L3 character appearance FULL PASS")
            self.assertEqual(result.appearance_count, 1)

    def test_duplicate_appearance_key_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            build_valid_index(project_dir)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                conn.executescript(
                    """
                    CREATE TABLE l3_character_appearance_copy AS SELECT * FROM l3_character_appearance;
                    DROP TABLE l3_character_appearance;
                    CREATE TABLE l3_character_appearance AS SELECT * FROM l3_character_appearance_copy;
                    INSERT INTO l3_character_appearance SELECT * FROM l3_character_appearance_copy;
                    DROP TABLE l3_character_appearance_copy;
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = run_l3_character_appearance_verification(project_dir)

            self.assertFalse(result.ok)
            self.assertEqual(result.final_message, "L3 character appearance VERIFY FAIL")
            self.assertGreater(result.checks["duplicate_appearance_key"], 0)

    def test_offset_out_of_bounds_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            build_valid_index(project_dir)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                conn.executescript(
                    """
                    CREATE TABLE l3_character_appearance_copy AS
                    SELECT * FROM l3_character_appearance;
                    DROP TABLE l3_character_appearance;
                    CREATE TABLE l3_character_appearance AS
                    SELECT * FROM l3_character_appearance_copy;
                    DROP TABLE l3_character_appearance_copy;
                    """
                )
                conn.execute("UPDATE l3_character_appearance SET match_end_offset = sentence_end_offset + 10")
                conn.commit()
            finally:
                conn.close()

            result = run_l3_character_appearance_verification(project_dir)

            self.assertFalse(result.ok)
            self.assertGreater(result.checks["offset_out_of_bounds"], 0)

    def test_l1_l2_hash_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            build_valid_index(project_dir)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                conn.execute("UPDATE l3_character_appearance SET sentence_hash = 'bad_hash'")
                conn.commit()
            finally:
                conn.close()

            result = run_l3_character_appearance_verification(project_dir)

            self.assertFalse(result.ok)
            self.assertGreater(result.checks["l1_backcut_mismatch"], 0)

    def test_overlapping_alias_range_warns_by_default_but_does_not_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir)
            characters = {
                "characters": [
                    {"character_id": "char_lou_yu", "name": "楼羽", "status": "confirmed", "aliases": [{"alias_id": "alias_sir_lou", "alias_text": "先生", "valid_from_chapter_num": 1, "valid_to_chapter_num": 3, "status": "confirmed"}]},
                    {"character_id": "char_han", "name": "韩先生", "status": "confirmed", "aliases": [{"alias_id": "alias_sir_han", "alias_text": "先生", "valid_from_chapter_num": 2, "valid_to_chapter_num": 4, "status": "confirmed"}]},
                ]
            }
            run_l3_character_appearance_indexer(project_dir, write_characters(project_dir, characters), rebuild=True)

            result = run_l3_character_appearance_verification(project_dir)

            self.assertTrue(result.ok)
            self.assertTrue(any("overlapping_alias_range" in warning for warning in result.warnings))


if __name__ == "__main__":
    unittest.main()
