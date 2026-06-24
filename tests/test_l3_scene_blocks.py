import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_scene_project(root: Path, chapters: dict[int, list[tuple[str, str]]]) -> None:
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
                latest_version_id TEXT NOT NULL
            );

            CREATE TABLE chapter_contents (
                version_id TEXT PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                content_full_text TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                content_length INTEGER NOT NULL,
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
                para_kind TEXT
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
                sentence_hash TEXT NOT NULL
            );

            CREATE TABLE l2_index_status (
                chapter_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                version_id TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                paragraph_count INTEGER NOT NULL,
                sentence_count INTEGER NOT NULL,
                indexer_version TEXT NOT NULL,
                status TEXT NOT NULL,
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

            CREATE VIEW v_l2_current_paragraphs AS
            SELECT
                p.para_id,
                p.chapter_id,
                p.chapter_num,
                p.version_id,
                p.para_index,
                p.start_offset,
                p.end_offset,
                p.char_length,
                p.para_hash,
                p.para_kind,
                substr(c.content_full_text, p.start_offset + 1, p.end_offset - p.start_offset) AS para_text,
                r.chapter_title_current,
                r.volume_code,
                r.volume_name,
                r.book_code,
                r.book_name,
                r.core_stage
            FROM l2_paragraph_units p
            JOIN chapter_registry r
              ON r.chapter_id = p.chapter_id
             AND r.latest_version_id = p.version_id
            JOIN chapter_contents c
              ON c.chapter_id = p.chapter_id
             AND c.version_id = p.version_id
             AND c.is_current = 1;

            CREATE VIEW v_l2_current_sentences AS
            SELECT
                s.sentence_id,
                s.para_id,
                s.chapter_id,
                s.chapter_num,
                s.version_id,
                s.para_index,
                s.sentence_index,
                s.global_sentence_index,
                s.start_offset,
                s.end_offset,
                s.char_length,
                s.sentence_hash,
                substr(c.content_full_text, s.start_offset + 1, s.end_offset - s.start_offset) AS sentence_text,
                r.chapter_title_current,
                r.volume_code,
                r.volume_name,
                r.book_code,
                r.book_name,
                r.core_stage
            FROM l2_sentence_units s
            JOIN chapter_registry r
              ON r.chapter_id = s.chapter_id
             AND r.latest_version_id = s.version_id
            JOIN chapter_contents c
              ON c.chapter_id = s.chapter_id
             AND c.version_id = s.version_id
             AND c.is_current = 1;
            """
        )
        global_sentence = 0
        for chapter_num, paragraphs in chapters.items():
            chapter_id = f"ch_{chapter_num:04d}"
            version_id = f"ver_{chapter_num:04d}"
            text_parts: list[str] = []
            offsets: list[tuple[int, int, str, str]] = []
            cursor = 0
            for idx, (para_kind, para_text) in enumerate(paragraphs, start=1):
                if idx > 1:
                    text_parts.append("\n")
                    cursor += 1
                start = cursor
                text_parts.append(para_text)
                cursor += len(para_text)
                offsets.append((start, cursor, para_kind, para_text))
            content = "".join(text_parts)
            content_hash = sha256_text(content)
            conn.execute(
                """
                INSERT INTO chapter_registry (
                    chapter_id, chapter_num, chapter_title_current, volume_code,
                    volume_name, book_code, book_name, core_stage, latest_version_id
                )
                VALUES (?, ?, ?, 'V01', 'Volume', 'B01', 'Book', 'test', ?)
                """,
                (chapter_id, chapter_num, f"Chapter {chapter_num}", version_id),
            )
            conn.execute(
                """
                INSERT INTO chapter_contents (
                    version_id, chapter_id, chapter_num, content_full_text,
                    content_hash, content_length, clean_rules_version, is_current
                )
                VALUES (?, ?, ?, ?, ?, ?, 'test', 1)
                """,
                (version_id, chapter_id, chapter_num, content, content_hash, len(content)),
            )
            for para_index, (start, end, para_kind, para_text) in enumerate(offsets, start=1):
                para_id = f"p_{chapter_num}_{para_index}"
                conn.execute(
                    """
                    INSERT INTO l2_paragraph_units (
                        para_id, chapter_id, chapter_num, version_id, para_index,
                        start_offset, end_offset, char_length, para_hash, para_kind
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        para_id,
                        chapter_id,
                        chapter_num,
                        version_id,
                        para_index,
                        start,
                        end,
                        end - start,
                        sha256_text(para_text),
                        para_kind,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO l2_sentence_units (
                        sentence_id, para_id, chapter_id, chapter_num, version_id,
                        para_index, sentence_index, global_sentence_index,
                        start_offset, end_offset, char_length, sentence_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"s_{chapter_num}_{para_index}",
                        para_id,
                        chapter_id,
                        chapter_num,
                        version_id,
                        para_index,
                        global_sentence,
                        start,
                        end,
                        end - start,
                        sha256_text(para_text),
                    ),
                )
                global_sentence += 1
            conn.execute(
                """
                INSERT INTO l2_index_status (
                    chapter_id, chapter_num, version_id, content_hash,
                    paragraph_count, sentence_count, indexer_version, status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'test_l2', 'indexed')
                """,
                (chapter_id, chapter_num, version_id, content_hash, len(offsets), len(offsets)),
            )
        conn.commit()
    finally:
        conn.close()


class L3SceneBlockTests(unittest.TestCase):
    def test_parse_scope_requires_explicit_allowed_sample_scope(self) -> None:
        from scripts import l3_scene_block_builder as builder

        self.assertEqual(builder.parse_scope("1,2,1697", None), [1, 2, 1697])
        self.assertEqual(builder.parse_scope(None, 1697), [1697])

        with self.assertRaises(ValueError):
            builder.parse_scope(None, None)
        with self.assertRaises(ValueError):
            builder.parse_scope("1,2,3", None)
        with self.assertRaises(ValueError):
            builder.parse_scope(None, 3)
        with self.assertRaises(ValueError):
            builder.parse_scope("1,2,1697", 1)

    def test_parse_scope_supports_explicit_all_only_when_requested(self) -> None:
        from scripts import l3_scene_block_builder as builder

        scope = builder.parse_scope(None, None, all_chapters=True)

        self.assertEqual(scope[0], 1)
        self.assertEqual(scope[-1], 1922)
        self.assertEqual(len(scope), 1922)
        with self.assertRaises(ValueError):
            builder.parse_scope("1,2,1697", None, all_chapters=True)

    def test_all_dry_run_plans_full_scope_without_writing_l3_rows(self) -> None:
        from scripts import l3_scene_block_builder as builder

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_scene_project(root, {chapter_num: [("body", f"chapter {chapter_num}")] for chapter_num in range(1, 1923)})

            result = builder.run_build(root, all_chapters=True, dry_run=True, target_min_chars=1, target_max_chars=1000)

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(len(result.scope), 1922)
            self.assertEqual(len(result.chapters), 1922)
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                exists = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'l3_scene_blocks'").fetchone()[0]
            finally:
                conn.close()
            self.assertEqual(exists, 0)

    def test_all_rebuild_and_verify_support_full_scope(self) -> None:
        from scripts import l3_scene_block_builder as builder
        from scripts import l3_verify_scene_blocks as verifier

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_scene_project(root, {chapter_num: [("body", f"chapter {chapter_num}")] for chapter_num in range(1, 1923)})

            build = builder.run_build(root, all_chapters=True, rebuild=True, target_min_chars=1, target_max_chars=1000)
            verified = verifier.run_verification(root, all_chapters=True)

            self.assertTrue(build.ok, build.errors)
            self.assertTrue(verified.ok, verified.errors)
            self.assertEqual(build.report_path.name, "l3_scene_blocks_full_report.md")
            self.assertEqual(verified.report_path.name, "l3_scene_blocks_full_verify_report.md")
            self.assertIn("L3 scene_blocks FULL PASS", build.report_path.read_text(encoding="utf-8"))
            self.assertIn("L3 scene_blocks FULL PASS", verified.report_path.read_text(encoding="utf-8"))

    def test_builder_creates_hashable_blocks_with_fixed_enums(self) -> None:
        from scripts import l3_scene_block_builder as builder

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_scene_project(
                root,
                {
                    1: [
                        ("body", "a" * 900),
                        ("dialogue", "b" * 700),
                        ("separator", "---"),
                        ("author_note", "note"),
                        ("body", "c" * 1200),
                    ],
                    2: [("body", "d" * 3100)],
                    1697: [("promo", "ad"), ("body", "e" * 1000), ("noise", "noise")],
                },
            )

            result = builder.run_build(root, sample_chapters="1,2,1697", rebuild=True, target_min_chars=800, target_max_chars=1500)

            self.assertTrue(result.ok, result.errors)
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            conn.row_factory = sqlite3.Row
            try:
                kinds = {row[0] for row in conn.execute("SELECT DISTINCT scene_kind FROM l3_scene_blocks")}
                reasons = {row[0] for row in conn.execute("SELECT DISTINCT split_reason FROM l3_scene_blocks")}
                statuses = [tuple(row) for row in conn.execute("SELECT status, COUNT(*) FROM l3_scene_block_status GROUP BY status")]
                first = conn.execute(
                    """
                    SELECT b.*, c.content_full_text
                    FROM l3_scene_blocks b
                    JOIN v_current_chapters c ON c.chapter_id = b.chapter_id AND c.latest_version_id = b.version_id
                    ORDER BY b.chapter_num, b.scene_index_in_chapter
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertLessEqual(kinds, {"normal", "separator", "author_note", "promo", "noise"})
            self.assertLessEqual(
                reasons,
                {
                    "target_max_reached",
                    "chapter_end",
                    "single_long_paragraph",
                    "strong_separator_trigger",
                    "strong_separator_block",
                },
            )
            self.assertEqual(statuses, [("indexed", 3)])
            sliced = first["content_full_text"][first["start_offset"] : first["end_offset"]]
            self.assertEqual(first["source_hash"], sha256_text(sliced))

    def test_chapter_rebuild_only_replaces_current_scope(self) -> None:
        from scripts import l3_scene_block_builder as builder

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_scene_project(root, {1: [("body", "a" * 100)], 2: [("body", "b" * 100)], 1697: [("body", "c" * 100)]})
            self.assertTrue(builder.run_build(root, sample_chapters="1,2,1697", rebuild=True, target_min_chars=1, target_max_chars=1000).ok)
            self.assertTrue(builder.run_build(root, chapter_num=1, rebuild=True, target_min_chars=1, target_max_chars=1000).ok)

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                chapters = [row[0] for row in conn.execute("SELECT DISTINCT chapter_num FROM l3_scene_blocks ORDER BY chapter_num")]
                status_chapters = [row[0] for row in conn.execute("SELECT chapter_num FROM l3_scene_block_status ORDER BY chapter_num")]
            finally:
                conn.close()

            self.assertEqual(chapters, [1, 2, 1697])
            self.assertEqual(status_chapters, [1, 2, 1697])

    def test_verify_reads_l1_l2_authority_and_fails_on_l2_drift(self) -> None:
        from scripts import l3_scene_block_builder as builder
        from scripts import l3_verify_scene_blocks as verifier

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_scene_project(root, {1: [("body", "a" * 100), ("body", "b" * 100)]})
            self.assertTrue(builder.run_build(root, chapter_num=1, rebuild=True, target_min_chars=1, target_max_chars=1000).ok)

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute("UPDATE l2_paragraph_units SET end_offset = end_offset - 1 WHERE para_id = 'p_1_2'")
                conn.commit()
            finally:
                conn.close()

            result = verifier.run_verification(root, chapter_num=1)

            self.assertFalse(result.ok)
            self.assertTrue(any("L2" in error or "paragraph" in error for error in result.errors))

    def test_verify_passes_sample_scope_and_requires_l3_rows(self) -> None:
        from scripts import l3_scene_block_builder as builder
        from scripts import l3_verify_scene_blocks as verifier

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_scene_project(root, {1: [("body", "a" * 100)], 2: [("separator", "---")], 1697: [("body", "c" * 100)]})
            missing = verifier.run_verification(root, sample_chapters="1,2,1697")
            self.assertFalse(missing.ok)

            build = builder.run_build(root, sample_chapters="1,2,1697", rebuild=True, target_min_chars=1, target_max_chars=1000)
            verified = verifier.run_verification(root, sample_chapters="1,2,1697")

            self.assertTrue(build.ok, build.errors)
            self.assertTrue(verified.ok, verified.errors)
            self.assertEqual(verified.chapter_count, 3)


if __name__ == "__main__":
    unittest.main()
