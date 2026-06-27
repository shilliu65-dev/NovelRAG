import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_character_candidate_extractor import run_l3_character_candidate_extractor


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_project(project_dir: Path, sentences: list[str]) -> Path:
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(parents=True, exist_ok=True)
    content = "\n".join(sentences)
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
            (content, sha256_text(content), len(content)),
        )
        cursor = 0
        for index, sentence in enumerate(sentences, start=1):
            start = content.index(sentence, cursor)
            end = start + len(sentence)
            conn.execute(
                """
                INSERT INTO l2_current_paragraphs_fixture (
                    para_id, chapter_id, chapter_num, version_id, para_index,
                    start_offset, end_offset, char_length, para_hash, para_kind,
                    para_text, chapter_title_current
                )
                VALUES (?, 'ch_0001', 1, 'ver_0001', ?, ?, ?, ?, ?, 'body', ?, '第一章')
                """,
                (f"p_{index}", index, start, end, end - start, sha256_text(sentence), sentence),
            )
            conn.execute(
                """
                INSERT INTO l2_current_sentences_fixture (
                    sentence_id, para_id, chapter_id, chapter_num, version_id,
                    para_index, sentence_index, global_sentence_index,
                    start_offset, end_offset, char_length, sentence_hash,
                    sentence_text, chapter_title_current
                )
                VALUES (?, ?, 'ch_0001', 1, 'ver_0001', ?, 1, ?, ?, ?, ?, ?, ?, '第一章')
                """,
                (
                    f"s_{index}",
                    f"p_{index}",
                    index,
                    index - 1,
                    start,
                    end,
                    end - start,
                    sha256_text(sentence),
                    sentence,
                ),
            )
            cursor = end
        conn.commit()
    finally:
        conn.close()
    return db_path


def sqlite_objects(db_path: Path) -> set[tuple[str, str]]:
    conn = sqlite3.connect(db_path)
    try:
        return {(row[0], row[1]) for row in conn.execute("SELECT type, name FROM sqlite_master")}
    finally:
        conn.close()


class L3CharacterCandidateExtractorTests(unittest.TestCase):
    def test_candidate_extractor_does_not_write_database_and_only_outputs_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            db_path = seed_project(
                project_dir,
                [
                    "陈伶说道：“王黑子，你看见戏神道了吗？”",
                    "王黑子看向陈伶，天枢界域仍旧沉默。",
                ],
            )
            before = sqlite_objects(db_path)

            result = run_l3_character_candidate_extractor(project_dir, min_count=1)

            self.assertEqual(before, sqlite_objects(db_path))
            self.assertTrue(result["characters"])
            self.assertTrue(all(item["status"] == "candidate" for item in result["characters"]))
            self.assertFalse(result["meta"]["writes_l1_l2"])
            self.assertEqual(result["meta"]["read_views"], ["v_l2_current_sentences", "v_l2_current_paragraphs", "v_current_chapters"])
            self.assertTrue((project_dir / "outputs" / "l3_character_candidates.json").exists())
            self.assertTrue((project_dir / "outputs" / "l3_character_candidates_report.md").exists())

    def test_candidate_extractor_filters_obvious_non_person_terms(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            seed_project(
                project_dir,
                [
                    "陈伶说道：“王黑子来自极光城。”",
                    "王黑子看向陈伶，戏神道、天枢界域、灰界、灾厄、能力、规则、红尘组织、红纸伞都在文本中。",
                ],
            )

            result = run_l3_character_candidate_extractor(project_dir, min_count=1)

            names = {item["name"] for item in result["characters"]}
            self.assertIn("陈伶", names)
            self.assertIn("王黑子", names)
            for forbidden in ("戏神道", "天枢界域", "灰界", "灾厄", "能力", "规则", "红尘组织", "极光城", "红纸伞"):
                self.assertNotIn(forbidden, names)


if __name__ == "__main__":
    unittest.main()
