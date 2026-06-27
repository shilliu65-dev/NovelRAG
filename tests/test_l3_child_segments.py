import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def seed_child_segment_project(root: Path, *, oversized_sentence: bool = False) -> Path:
    db_path = root / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (root / "outputs").mkdir(parents=True, exist_ok=True)
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
                source_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE l3_scene_block_status (
                chapter_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                status TEXT NOT NULL,
                scene_count INTEGER NOT NULL,
                paragraph_count INTEGER NOT NULL,
                sentence_count INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chapter_id, version_id)
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
        for chapter_num in (1, 2, 1697):
            chapter_id = f"ch_{chapter_num:04d}"
            version_id = f"ver_{chapter_num:04d}"
            if oversized_sentence and chapter_num == 1:
                sentences = ["x" * 1301, "tail sentence."]
            else:
                sentences = [
                    f"Chapter {chapter_num} sentence {idx} " + ("x" * 180) + "."
                    for idx in range(1, 7)
                ]
            separator = "\n"
            content = separator.join(sentences)
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
            cursor = 0
            para_ids: list[str] = []
            sentence_ids: list[str] = []
            for idx, sentence in enumerate(sentences, start=1):
                if idx > 1:
                    cursor += len(separator)
                start = cursor
                end = start + len(sentence)
                para_id = f"p_{chapter_num}_{idx}"
                sentence_id = f"s_{chapter_num}_{idx}"
                para_ids.append(para_id)
                sentence_ids.append(sentence_id)
                conn.execute(
                    """
                    INSERT INTO l2_paragraph_units (
                        para_id, chapter_id, chapter_num, version_id, para_index,
                        start_offset, end_offset, char_length, para_hash, para_kind
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'body')
                    """,
                    (
                        para_id,
                        chapter_id,
                        chapter_num,
                        version_id,
                        idx,
                        start,
                        end,
                        len(sentence),
                        sha256_text(sentence),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO l2_sentence_units (
                        sentence_id, para_id, chapter_id, chapter_num, version_id,
                        para_index, sentence_index, global_sentence_index,
                        start_offset, end_offset, char_length, sentence_hash
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sentence_id,
                        para_id,
                        chapter_id,
                        chapter_num,
                        version_id,
                        idx,
                        idx,
                        chapter_num * 100 + idx,
                        start,
                        end,
                        len(sentence),
                        sha256_text(sentence),
                    ),
                )
                cursor = end
            conn.execute(
                """
                INSERT INTO l2_index_status (
                    chapter_id, chapter_num, version_id, content_hash,
                    paragraph_count, sentence_count, indexer_version, status
                )
                VALUES (?, ?, ?, ?, ?, ?, 'test_l2', 'indexed')
                """,
                (chapter_id, chapter_num, version_id, content_hash, len(sentences), len(sentences)),
            )
            conn.execute(
                """
                INSERT INTO l3_scene_blocks (
                    scene_id, scene_key, chapter_id, version_id, chapter_num,
                    scene_index_in_chapter, start_para_id, end_para_id,
                    start_para_index, end_para_index, start_sentence_id,
                    end_sentence_id, start_offset, end_offset, length,
                    scene_kind, split_reason, summary_short, source_hash
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, 1, ?, ?, ?, 0, ?, ?, 'normal', 'chapter_end', NULL, ?)
                """,
                (
                    chapter_num,
                    f"{chapter_id}:{version_id}:scene:1",
                    chapter_id,
                    version_id,
                    chapter_num,
                    para_ids[0],
                    para_ids[-1],
                    len(para_ids),
                    sentence_ids[0],
                    sentence_ids[-1],
                    len(content),
                    len(content),
                    sha256_text(content),
                ),
            )
            conn.execute(
                """
                INSERT INTO l3_scene_block_status (
                    chapter_id, version_id, chapter_num, status,
                    scene_count, paragraph_count, sentence_count
                )
                VALUES (?, ?, ?, 'indexed', 1, ?, ?)
                """,
                (chapter_id, version_id, chapter_num, len(sentences), len(sentences)),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


class L3ChildSegmentTests(unittest.TestCase):
    def test_builder_splits_on_sentence_boundaries_and_marks_overlap(self) -> None:
        from scripts import l3_child_segment_builder as builder

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_child_segment_project(root)

            result = builder.run_build(root, sample_chapters="1,2,1697", rebuild=True)

            self.assertTrue(result.ok, result.errors)
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM l3_child_segment
                    WHERE chapter_num = 1
                    ORDER BY segment_index_in_scene
                    """
                ).fetchall()
                links = conn.execute(
                    """
                    SELECT *
                    FROM l3_child_segment_sentence_link
                    WHERE child_segment_id = ?
                    ORDER BY position_in_segment
                    """,
                    (rows[1]["child_segment_id"],),
                ).fetchall()
                source_sentences = conn.execute(
                    """
                    SELECT sentence_id, start_offset, end_offset
                    FROM v_l2_current_sentences
                    WHERE chapter_num = 1
                    ORDER BY start_offset
                    """
                ).fetchall()
            finally:
                conn.close()

            self.assertGreaterEqual(len(rows), 2)
            self.assertEqual(rows[0]["has_overlap"], 0)
            self.assertEqual(rows[1]["has_overlap"], 1)
            self.assertEqual(rows[1]["overlap_from_child_segment_id"], rows[0]["child_segment_id"])
            self.assertEqual(links[0]["sentence_id"], rows[0]["end_sentence_id"])
            self.assertEqual(links[0]["is_overlap_sentence"], 1)
            sentence_starts = {row["start_offset"] for row in source_sentences}
            sentence_ends = {row["end_offset"] for row in source_sentences}
            for row in rows:
                self.assertIn(row["start_char_offset"], sentence_starts)
                self.assertIn(row["end_char_offset"], sentence_ends)
                self.assertEqual(row["segment_text_hash"], sha256_text(row["segment_text"]))
                self.assertGreaterEqual(row["sentence_count"], 1)

    def test_reporter_writes_sample_outputs_and_required_metrics(self) -> None:
        from scripts import l3_child_segment_builder as builder
        from scripts import l3_child_segment_reporter as reporter

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_child_segment_project(root, oversized_sentence=True)
            self.assertTrue(builder.run_build(root, sample_chapters="1,2,1697", rebuild=True).ok)

            metrics = reporter.run_report(root)

            for name in (
                "l3_child_segments_sample.json",
                "l3_child_segments_sample_report.md",
                "l3_child_segments_manifest.json",
            ):
                self.assertTrue((root / "outputs" / name).exists(), name)
            self.assertEqual(metrics["chapter_count"], 3)
            self.assertEqual(metrics["scene_block_count"], 3)
            self.assertGreater(metrics["child_segment_count"], 0)
            self.assertGreaterEqual(metrics["oversized_sentence_warning_count"], 1)
            self.assertFalse(metrics["source_mutation_detected"])

    def test_verifier_detects_hash_tampering_and_rebuild_idempotence(self) -> None:
        from scripts import l3_child_segment_builder as builder
        from scripts import l3_verify_child_segments as verifier

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_child_segment_project(root)
            first = builder.run_build(root, sample_chapters="1,2,1697", rebuild=True)
            second = builder.run_build(root, sample_chapters="1,2,1697", rebuild=True)
            verified = verifier.run_verification(root)

            self.assertTrue(first.ok, first.errors)
            self.assertTrue(second.ok, second.errors)
            self.assertTrue(verified.ok, verified.errors)
            self.assertTrue(verified.rebuild_idempotent)

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute(
                    """
                    UPDATE l3_child_segment
                    SET segment_text_hash = 'bad'
                    WHERE child_segment_id = (
                        SELECT child_segment_id
                        FROM l3_child_segment
                        WHERE chapter_num = 1
                        ORDER BY child_segment_id
                        LIMIT 1
                    )
                    """
                )
                conn.commit()
            finally:
                conn.close()

            tampered = verifier.run_verification(root)
            self.assertFalse(tampered.ok)
            self.assertTrue(any("segment_text_hash" in error for error in tampered.errors))

    def test_cli_verifier_stdout_is_exact_pass_line_and_forbidden_tables_absent(self) -> None:
        from scripts import l3_child_segment_builder as builder

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_child_segment_project(root)
            self.assertTrue(builder.run_build(root, sample_chapters="1,2,1697", rebuild=True).ok)

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "l3_verify_child_segments.py"),
                    "--project-dir",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "L3.5 child segments FULL PASS")
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                forbidden = {
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'table'
                          AND name IN (
                              'final_event',
                              'final_timeline',
                              'final_relationship_graph',
                              'final_state_machine'
                          )
                        """
                    )
                }
            finally:
                conn.close()
            self.assertEqual(forbidden, set())

    def test_manifest_contains_stable_row_fingerprint(self) -> None:
        from scripts import l3_child_segment_builder as builder
        from scripts import l3_child_segment_reporter as reporter

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            seed_child_segment_project(root)
            self.assertTrue(builder.run_build(root, sample_chapters="1,2,1697", rebuild=True).ok)

            reporter.run_report(root)
            manifest = json.loads((root / "outputs" / "l3_child_segments_manifest.json").read_text(encoding="utf-8"))

            self.assertIn("stable_row_fingerprint", manifest)
            self.assertEqual(manifest["forbidden_final_tables"], [])


if __name__ == "__main__":
    unittest.main()
