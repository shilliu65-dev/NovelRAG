import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import l1_chapter_importer as importer
from scripts import l1_verify_import as verifier


def write_chapter(root: Path, number: int, title: str, body: str) -> Path:
    path = root / "chapters" / f"chapter_{number:03d}_第{number}章_{title}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                f'chapter_id: "chapter_{number:03d}"',
                f"order: {number}",
                f'title: "第{number}章 {title}"',
                "source: test.txt",
                "char_count: 0",
                "---",
                "",
                f"# 第{number}章 {title}",
                "",
                body,
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


class L1MarkdownPipelineTests(unittest.TestCase):
    def test_metadata_by_chapter_uses_l1_1_ranges_and_extra_entries(self) -> None:
        self.assertEqual(importer.metadata_by_chapter(1)["volume_code"], "V01")
        self.assertEqual(importer.metadata_by_chapter(1)["book_code"], "B01")
        self.assertEqual(importer.metadata_by_chapter(161)["volume_code"], "V01")
        self.assertEqual(importer.metadata_by_chapter(161)["book_code"], "B02")
        self.assertEqual(importer.metadata_by_chapter(1701)["volume_code"], "V06")
        self.assertEqual(importer.metadata_by_chapter(1701)["book_code"], "B12")
        self.assertEqual(importer.metadata_by_chapter(1923)["volume_code"], "EXTRA")
        self.assertEqual(importer.metadata_by_chapter(1923)["book_code"], "EXTRA")

    def test_markdown_content_strips_frontmatter_and_keeps_title(self) -> None:
        raw = """---
order: 1
title: "第1章 戏鬼回家"
---

# 第1章 戏鬼回家

正文第一段。
"""

        content = importer.markdown_to_l1_content(raw)

        self.assertTrue(content.startswith("# 第1章 戏鬼回家"))
        self.assertIn("正文第一段。", content)
        self.assertNotIn("order: 1", content)

    def test_import_md_is_idempotent_and_single_chapter_change_creates_one_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_chapter(root, 1, "开始", "第一章正文。")
            chapter_two = write_chapter(root, 2, "继续", "第二章正文。")
            write_chapter(root, 3, "结束", "第三章正文。")

            first = importer.run_l1_import(root)
            second = importer.run_l1_import(root)
            chapter_two.write_text(chapter_two.read_text(encoding="utf-8") + "\n第二章追加修订。\n", encoding="utf-8")
            third = importer.run_l1_import(root)

            self.assertEqual(first.inserted_chapters, 3)
            self.assertEqual(second.unchanged_chapters, 3)
            self.assertEqual(second.new_versions, 0)
            self.assertEqual(third.new_versions, 1)

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                total_versions = conn.execute("SELECT COUNT(*) FROM chapter_contents").fetchone()[0]
                chapter_two_versions = conn.execute(
                    "SELECT COUNT(*) FROM chapter_contents WHERE chapter_id = 'ch_0002'"
                ).fetchone()[0]
                current_versions = conn.execute("SELECT COUNT(*) FROM chapter_contents WHERE is_current = 1").fetchone()[0]
                current_text = conn.execute(
                    "SELECT content_full_text FROM v_current_chapters WHERE chapter_num = 2"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(total_versions, 4)
            self.assertEqual(chapter_two_versions, 2)
            self.assertEqual(current_versions, 3)
            self.assertIn("第二章追加修订。", current_text)

    def test_import_uses_h1_chapter_number_before_filename_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_chapter(root, 1, "开始", "第一章正文。")
            write_chapter(root, 2, "继续", "第二章正文。")
            shifted = write_chapter(root, 4, "真实第三章", "第三章正文。")
            shifted.write_text(
                shifted.read_text(encoding="utf-8").replace("第4章 真实第三章", "第3章 真实第三章"),
                encoding="utf-8",
            )

            first = importer.run_l1_import(root)
            second = importer.run_l1_import(root)

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                row = conn.execute(
                    """
                    SELECT chapter_id, chapter_num, chapter_title_current, source_file_name
                    FROM chapter_registry
                    WHERE chapter_num = 3
                    """
                ).fetchone()
                total_versions = conn.execute("SELECT COUNT(*) FROM chapter_contents").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(first.inserted_chapters, 3)
            self.assertEqual(second.unchanged_chapters, 3)
            self.assertEqual(second.new_versions, 0)
            self.assertEqual(row[0], "ch_0003")
            self.assertEqual(row[1], 3)
            self.assertEqual(row[2], "第3章 真实第三章")
            self.assertEqual(row[3], shifted.name)
            self.assertEqual(total_versions, 3)

    def test_import_skips_earlier_duplicate_h1_chapter_numbers_stably(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_chapter(root, 1, "开始", "第一章正文。")
            duplicate = write_chapter(root, 2, "重复第一章", "重复正文。")
            duplicate.write_text(
                duplicate.read_text(encoding="utf-8").replace("第2章 重复第一章", "第1章 重复第一章"),
                encoding="utf-8",
            )
            write_chapter(root, 3, "结束", "第三章正文。")

            first = importer.run_l1_import(root)
            second = importer.run_l1_import(root)

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                total_chapters = conn.execute("SELECT COUNT(*) FROM chapter_registry").fetchone()[0]
                total_versions = conn.execute("SELECT COUNT(*) FROM chapter_contents").fetchone()[0]
                current_title = conn.execute(
                    "SELECT chapter_title_current FROM chapter_registry WHERE chapter_num = 1"
                ).fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(first.inserted_chapters, 2)
            self.assertEqual(first.skipped_files, 1)
            self.assertEqual(second.unchanged_chapters, 2)
            self.assertEqual(second.new_versions, 0)
            self.assertEqual(total_chapters, 2)
            self.assertEqual(total_versions, 2)
            self.assertEqual(current_title, "第1章 重复第一章")

    def test_verifier_reports_pass_for_valid_temp_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_chapter(root, 1, "开始", "第一章正文。")
            write_chapter(root, 2, "继续", "第二章正文。")
            write_chapter(root, 3, "结束", "第三章正文。")
            importer.run_l1_import(root)

            result = verifier.run_verification(root, expected_main_chapters=3, expected_total_entries=99)
            report = (root / "outputs" / "l1_verify_report.md").read_text(encoding="utf-8")

            self.assertTrue(result.ok, report)
            self.assertEqual(result.total_chapters, 3)
            self.assertEqual(result.current_versions, 3)
            self.assertEqual(result.total_versions, 3)
            self.assertTrue(result.total_entry_mismatch)
            self.assertEqual(result.idempotency_new_versions, 0)
            self.assertEqual(result.single_change_new_versions, 1)
            self.assertIn("L1 VERIFY PASS，可以进入 L2 原文坐标层。", report)

    def test_verifier_fails_when_main_chapter_metadata_is_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_chapter(root, 1, "开始", "第一章正文。")
            write_chapter(root, 2, "继续", "第二章正文。")
            write_chapter(root, 3, "结束", "第三章正文。")
            importer.run_l1_import(root)
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute(
                    """
                    UPDATE chapter_registry
                    SET volume_code = 'UNKNOWN',
                        volume_name = 'UNKNOWN',
                        book_code = 'UNKNOWN',
                        book_name = 'UNKNOWN',
                        core_stage = 'UNKNOWN'
                    WHERE chapter_num = 2
                    """
                )
                conn.commit()
            finally:
                conn.close()

            result = verifier.run_verification(root, expected_main_chapters=3, expected_total_entries=3)
            report = (root / "outputs" / "l1_verify_report.md").read_text(encoding="utf-8")

            self.assertFalse(result.ok)
            self.assertEqual(result.main_unknown_metadata_count, 1)
            self.assertIn("正文 UNKNOWN 元数据异常", report)
            self.assertIn("main chapter metadata is UNKNOWN", report)

    def test_verifier_fails_when_main_chapter_title_number_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_chapter(root, 1, "开始", "第一章正文。")
            write_chapter(root, 2, "错位", "第二章正文。")
            write_chapter(root, 3, "结束", "第三章正文。")
            importer.run_l1_import(root)
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute(
                    "UPDATE chapter_registry SET chapter_title_current = '第1章 错位' WHERE chapter_num = 2"
                )
                conn.commit()
            finally:
                conn.close()

            result = verifier.run_verification(root, expected_main_chapters=3, expected_total_entries=3)
            report = (root / "outputs" / "l1_verify_report.md").read_text(encoding="utf-8")

            self.assertFalse(result.ok)
            self.assertGreaterEqual(result.title_number_mismatches, 1)
            self.assertIn("标题章号异常", report)
            self.assertIn("ch_0002", report)
            self.assertIn("第1章 错位", report)

    def test_verifier_direct_script_returns_nonzero_for_empty_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "index").mkdir()
            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                importer.init_schema(conn)
            finally:
                conn.close()

            completed = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "l1_verify_import.py"),
                    "--project-dir",
                    str(root),
                    "--expected-main-chapters",
                    "3",
                    "--expected-total-entries",
                    "3",
                ],
                cwd=PROJECT_ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 1)
            self.assertIn("L1 VERIFY FAIL，禁止进入 L2。", completed.stdout)


if __name__ == "__main__":
    unittest.main()
