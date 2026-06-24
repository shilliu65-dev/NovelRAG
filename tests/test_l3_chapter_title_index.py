import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def seed_project(root: Path, chapters: list[tuple[int, str]]) -> None:
    db_path = root / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE chapter_registry (
                chapter_id TEXT PRIMARY KEY,
                chapter_num INTEGER NOT NULL UNIQUE,
                chapter_title_current TEXT NOT NULL,
                volume_code TEXT NOT NULL,
                volume_name TEXT NOT NULL,
                book_code TEXT NOT NULL,
                book_name TEXT NOT NULL,
                core_stage TEXT NOT NULL,
                latest_version_id TEXT NOT NULL,
                source_file_name TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE chapter_contents (
                version_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                content_full_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                content_length INTEGER NOT NULL,
                source_file_path TEXT,
                source_file_hash TEXT,
                file_mtime REAL DEFAULT 0,
                clean_rules_version TEXT NOT NULL,
                imported_at TEXT DEFAULT CURRENT_TIMESTAMP,
                is_current INTEGER NOT NULL
            );

            CREATE TABLE l2_paragraph_units (
                para_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                para_index INTEGER NOT NULL,
                start_offset INTEGER NOT NULL,
                end_offset INTEGER NOT NULL,
                char_length INTEGER NOT NULL,
                para_hash TEXT NOT NULL,
                para_kind TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE l2_sentence_units (
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
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE l2_index_status (
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                paragraph_count INTEGER NOT NULL,
                sentence_count INTEGER NOT NULL,
                indexer_version TEXT NOT NULL,
                indexed_at TEXT DEFAULT CURRENT_TIMESTAMP,
                status TEXT NOT NULL,
                warnings TEXT,
                PRIMARY KEY(chapter_id, version_id)
            );

            CREATE VIEW v_current_chapters AS
            SELECT
                r.chapter_id,
                r.chapter_num,
                r.chapter_title_current,
                r.volume_code,
                r.volume_name,
                r.book_code,
                r.book_name,
                r.core_stage,
                r.latest_version_id,
                c.content_full_text,
                c.content_hash,
                c.content_length,
                c.imported_at
            FROM chapter_registry r
            JOIN chapter_contents c
              ON c.chapter_id = r.chapter_id
             AND c.version_id = r.latest_version_id
             AND c.is_current = 1;
            """
        )
        for chapter_num, title in chapters:
            chapter_id = f"ch_{chapter_num:04d}"
            version_id = f"ver_{chapter_num:04d}"
            content_hash = f"hash_{chapter_num:04d}"
            body = f"# {title}\n\n正文 {chapter_num}"
            conn.execute(
                """
                INSERT INTO chapter_registry (
                    chapter_id, chapter_num, chapter_title_current, volume_code,
                    volume_name, book_code, book_name, core_stage, latest_version_id
                )
                VALUES (?, ?, ?, 'V01', '第一卷', 'B01', '第1册', 'test', ?)
                """,
                (chapter_id, chapter_num, title, version_id),
            )
            conn.execute(
                """
                INSERT INTO chapter_contents (
                    version_id, chapter_id, chapter_num, content_full_text,
                    content_hash, content_length, clean_rules_version, is_current
                )
                VALUES (?, ?, ?, ?, ?, ?, 'test', 1)
                """,
                (version_id, chapter_id, chapter_num, body, content_hash, len(body)),
            )
            conn.execute(
                """
                INSERT INTO l2_paragraph_units (
                    para_id, chapter_id, chapter_num, version_id, para_index,
                    start_offset, end_offset, char_length, para_hash, para_kind
                )
                VALUES (?, ?, ?, ?, 1, 0, 1, 1, ?, 'body')
                """,
                (f"p_{chapter_num}", chapter_id, chapter_num, version_id, f"ph_{chapter_num}"),
            )
            conn.execute(
                """
                INSERT INTO l2_sentence_units (
                    sentence_id, para_id, chapter_id, chapter_num, version_id,
                    para_index, sentence_index, global_sentence_index,
                    start_offset, end_offset, char_length, sentence_hash
                )
                VALUES (?, ?, ?, ?, ?, 1, 1, 0, 0, 1, 1, ?)
                """,
                (f"s_{chapter_num}", f"p_{chapter_num}", chapter_id, chapter_num, version_id, f"sh_{chapter_num}"),
            )
            conn.execute(
                """
                INSERT INTO l2_index_status (
                    chapter_id, chapter_num, version_id, content_hash,
                    paragraph_count, sentence_count, indexer_version, status
                )
                VALUES (?, ?, ?, ?, 1, 1, 'test_l2', 'indexed')
                """,
                (chapter_id, chapter_num, version_id, content_hash),
            )
        conn.commit()
    finally:
        conn.close()


class L3ChapterTitleIndexTests(unittest.TestCase):
    def test_time_axis_seed_is_static_json_and_does_not_create_time_tables(self) -> None:
        seed_path = PROJECT_ROOT / "config" / "narrative_time_axis.seed.json"
        self.assertTrue(seed_path.exists())
        data = json.loads(seed_path.read_text(encoding="utf-8"))
        eras = data["eras"]

        self.assertEqual([item["era_id"] for item in eras], [f"era_{idx:02d}" for idx in range(1, 7)])
        for item in eras:
            self.assertIsInstance(item["era_aliases"], list)
            self.assertIsInstance(item["evidence_refs"], list)
            self.assertEqual(item["evidence_status"], "manual_seed")
            self.assertEqual(item["created_at"], "2026-06-24T00:00:00+08:00")

        self.assertFalse((PROJECT_ROOT / "scripts" / "l3_chapter_time_mapping.py").exists())

    def test_indexer_rebuild_indexes_limit_in_chapter_order_and_stable_rowids(self) -> None:
        from scripts import l3_chapter_title_indexer as indexer

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_project(root, [(2, "第2章 红王"), (1, "第1章 陈伶"), (1923, "完本感言")])

            stats = indexer.run_index(root, rebuild=True, limit=2)

            self.assertTrue(stats.ok, stats.errors)
            self.assertEqual(stats.selected_chapters, 2)
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                rows = conn.execute(
                    """
                    SELECT title_index_id, chapter_num, title_raw
                    FROM l3_chapter_title_index
                    ORDER BY chapter_num
                    """
                ).fetchall()
                fts_count = conn.execute("SELECT COUNT(*) FROM l3_chapter_title_fts").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(rows, [(1, 1, "第1章 陈伶"), (2, 2, "第2章 红王")])
            self.assertEqual(fts_count, 2)

    def test_non_rebuild_scope_checks_are_not_full_scope(self) -> None:
        from scripts import l3_chapter_title_indexer as indexer

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_project(root, [(1, "第1章 陈伶"), (2, "第2章 红王"), (3, "第3章 黄昏社")])

            first = indexer.run_index(root, rebuild=True, limit=2)
            second = indexer.run_index(root, rebuild=False, limit=2)

            self.assertTrue(first.ok, first.errors)
            self.assertTrue(second.ok, second.errors)
            self.assertEqual(second.skipped_indexes, 2)

    def test_non_rebuild_fails_when_main_and_fts_are_inconsistent(self) -> None:
        from scripts import l3_chapter_title_indexer as indexer

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_project(root, [(1, "第1章 陈伶")])
            self.assertTrue(indexer.run_index(root, rebuild=True, limit=1).ok)
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute("DELETE FROM l3_chapter_title_fts WHERE rowid = 1")
                conn.commit()
            finally:
                conn.close()

            stats = indexer.run_index(root, rebuild=False, limit=1)

            self.assertFalse(stats.ok)
            self.assertTrue(any("--rebuild" in error for error in stats.errors))

    def test_verify_chapter_num_scope_and_fts_query_escape(self) -> None:
        from scripts import l3_chapter_title_indexer as indexer
        from scripts import l3_verify_chapter_title_index as verifier

        self.assertEqual(verifier.escape_fts5_query(' 红王-"6" '), '"红王-""6"""')
        self.assertEqual(verifier.escape_fts5_query("  "), "")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_project(root, [(1697, "第1697章 红王-6"), (1698, "第1698章 陈伶")])

            build = indexer.run_index(root, rebuild=True, chapter_num=1697)
            result = verifier.run_verification(
                root,
                verify_indexed_only=True,
                expected_indexed_chapters=1,
                chapter_num=1697,
            )

            self.assertTrue(build.ok, build.errors)
            self.assertTrue(result.ok, result.errors)
            self.assertEqual(result.main_count, 1)
            self.assertGreaterEqual(result.fts_sample_matches, 1)

    def test_verify_full_scope_fails_when_only_small_sample_exists(self) -> None:
        from scripts import l3_chapter_title_indexer as indexer
        from scripts import l3_verify_chapter_title_index as verifier

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_project(root, [(1, "第1章 陈伶"), (2, "第2章 红王")])
            self.assertTrue(indexer.run_index(root, rebuild=True, limit=2).ok)

            result = verifier.run_verification(root, expected_main_chapters=1922)

            self.assertFalse(result.ok)
            self.assertTrue(any("1922" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
