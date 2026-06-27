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


def seed_project(project_dir: Path, chapters: dict[int, list[str]]) -> Path:
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
        for chapter_num, sentences in chapters.items():
            chapter_id = f"ch_{chapter_num:04d}"
            version_id = f"ver_{chapter_num:04d}"
            title = f"第{chapter_num}章"
            content = "\n".join(sentences)
            conn.execute(
                """
                INSERT INTO current_chapters_fixture (
                    chapter_id, chapter_num, chapter_title_current, latest_version_id,
                    content_full_text, content_hash, content_length
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (chapter_id, chapter_num, title, version_id, content, sha256_text(content), len(content)),
            )
            cursor = 0
            for index, sentence in enumerate(sentences, start=1):
                start = content.index(sentence, cursor)
                end = start + len(sentence)
                para_id = f"{chapter_id}_p{index:04d}"
                sentence_id = f"{chapter_id}_s{index:04d}"
                conn.execute(
                    """
                    INSERT INTO l2_current_paragraphs_fixture (
                        para_id, chapter_id, chapter_num, version_id, para_index,
                        start_offset, end_offset, char_length, para_hash, para_kind,
                        para_text, chapter_title_current
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'body', ?, ?)
                    """,
                    (para_id, chapter_id, chapter_num, version_id, index, start, end, end - start, sha256_text(sentence), sentence, title),
                )
                conn.execute(
                    """
                    INSERT INTO l2_current_sentences_fixture (
                        sentence_id, para_id, chapter_id, chapter_num, version_id,
                        para_index, sentence_index, global_sentence_index,
                        start_offset, end_offset, char_length, sentence_hash,
                        sentence_text, chapter_title_current
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (sentence_id, para_id, chapter_id, chapter_num, version_id, index, index - 1, start, end, end - start, sha256_text(sentence), sentence, title),
                )
                cursor = end
        conn.commit()
    finally:
        conn.close()
    return db_path


def write_characters(project_dir: Path, payload: dict) -> Path:
    path = project_dir / "outputs" / "characters.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


class L3CharacterAppearanceIndexerTests(unittest.TestCase):
    def test_temporal_alias_range_controls_appearance_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["先生走进屋内。"], 6: ["先生在城门外等候。"]})
            characters_path = write_characters(
                project_dir,
                {
                    "characters": [
                        {
                            "character_id": "char_lou_yu",
                            "name": "楼羽",
                            "status": "confirmed",
                            "aliases": [{"alias_id": "alias_sir_lou", "alias_text": "先生", "alias_type": "title", "valid_from_chapter_num": 1, "valid_to_chapter_num": 5, "status": "confirmed"}],
                        },
                        {
                            "character_id": "char_han",
                            "name": "韩先生",
                            "status": "confirmed",
                            "aliases": [{"alias_id": "alias_sir_han", "alias_text": "先生", "alias_type": "title", "valid_from_chapter_num": 6, "valid_to_chapter_num": 10, "status": "confirmed"}],
                        },
                    ]
                },
            )

            stats = run_l3_character_appearance_indexer(project_dir, characters_path, rebuild=True)

            self.assertEqual(stats.appearance_count, 2)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                rows = conn.execute("SELECT character_id, matched_text, chapter_num FROM l3_character_appearance ORDER BY chapter_num").fetchall()
                columns = {row[1] for row in conn.execute("PRAGMA table_info(l3_character_appearance)")}
            finally:
                conn.close()
            self.assertEqual(rows, [("char_lou_yu", "先生", 1), ("char_han", "先生", 6)])
            self.assertFalse({"content_full_text", "paragraph_text", "sentence_text"}.intersection(columns))

    def test_same_alias_multiple_times_in_one_sentence_creates_multiple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["先生看着先生，先生没有说话。"]})
            characters_path = write_characters(
                project_dir,
                {"characters": [{"character_id": "char_lou_yu", "name": "楼羽", "status": "confirmed", "aliases": [{"alias_id": "alias_sir_lou", "alias_text": "先生", "valid_from_chapter_num": 1, "valid_to_chapter_num": 1, "status": "confirmed"}]}]},
            )

            stats = run_l3_character_appearance_indexer(project_dir, characters_path, rebuild=True)
            verification = run_l3_character_appearance_verification(project_dir)

            self.assertEqual(stats.appearance_count, 3)
            self.assertTrue(verification.ok)
            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                rows = conn.execute("SELECT matched_text, match_start_offset, match_end_offset FROM l3_character_appearance ORDER BY match_start_offset").fetchall()
            finally:
                conn.close()
            self.assertEqual([row[0] for row in rows], ["先生", "先生", "先生"])
            self.assertEqual(len({(row[1], row[2]) for row in rows}), 3)

    def test_matched_text_is_original_alias_not_canonical_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(project_dir, {1: ["先生在台下沉默。"]})
            characters_path = write_characters(
                project_dir,
                {"characters": [{"character_id": "char_lou_yu", "name": "楼羽", "status": "confirmed", "aliases": [{"alias_id": "alias_sir_lou", "alias_text": "先生", "valid_from_chapter_num": 1, "valid_to_chapter_num": 1, "status": "confirmed"}]}]},
            )

            run_l3_character_appearance_indexer(project_dir, characters_path, rebuild=True)

            conn = sqlite3.connect(project_dir / "index" / "novel_story_bible.db")
            try:
                row = conn.execute("SELECT d.canonical_name, a.matched_text FROM l3_character_appearance a JOIN l3_character_def d ON d.character_id = a.character_id").fetchone()
            finally:
                conn.close()
            self.assertEqual(row, ("楼羽", "先生"))


if __name__ == "__main__":
    unittest.main()
