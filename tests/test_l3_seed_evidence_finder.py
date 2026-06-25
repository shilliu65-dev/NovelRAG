import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_seed_evidence_finder import (
    build_keywords,
    escape_like,
    extract_seed_items,
    run_l3_seed_evidence_finder,
    sha256,
)


def paragraph_rows(chapter_id: str, version_id: str, chapter_num: int, chapter_title: str, paragraphs: list[str]) -> tuple[str, list[tuple[object, ...]]]:
    content = "\n\n".join(paragraphs)
    rows: list[tuple[object, ...]] = []
    cursor = 0
    for para_index, paragraph in enumerate(paragraphs):
        start = content.index(paragraph, cursor)
        end = start + len(paragraph)
        rows.append(
            (
                f"{chapter_id}_p{para_index:04d}",
                chapter_id,
                chapter_num,
                version_id,
                para_index,
                start,
                end,
                end - start,
                sha256(paragraph),
                paragraph,
                chapter_title,
            )
        )
        cursor = end
    return content, rows


def create_minimal_project_db(project_dir: Path) -> None:
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(exist_ok=True)

    chapter_1_text, chapter_1_paragraphs = paragraph_rows(
        "ch_0001",
        "ver_ch_0001",
        1,
        "测试章一",
        [
            "前文：舞台尚未亮起。",
            "陈伶在台上确认戏神道的面具与分支。",
            "后文：观众席重新陷入安静。",
        ],
    )
    chapter_2_text, chapter_2_paragraphs = paragraph_rows(
        "ch_0002",
        "ver_ch_0002",
        2,
        "测试章二",
        [
            "另一章前文。",
            "另一章也提到戏神道，但不能成为第一章命中段的上下文。",
            "另一章后文。",
        ],
    )

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE current_chapters_fixture (
                chapter_id TEXT PRIMARY KEY,
                chapter_num INTEGER NOT NULL,
                chapter_title_current TEXT NOT NULL,
                latest_version_id TEXT NOT NULL,
                content_full_text TEXT NOT NULL
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
                para_text TEXT NOT NULL,
                chapter_title_current TEXT NOT NULL
            );

            CREATE VIEW v_current_chapters AS
            SELECT
                chapter_id,
                chapter_num,
                chapter_title_current,
                latest_version_id,
                content_full_text
            FROM current_chapters_fixture;

            CREATE VIEW v_l2_current_paragraphs AS
            SELECT
                para_id,
                chapter_id,
                chapter_num,
                version_id,
                para_index,
                start_offset,
                end_offset,
                char_length,
                para_hash,
                para_text,
                chapter_title_current
            FROM l2_current_paragraphs_fixture;

            CREATE VIEW v_l2_current_sentences AS
            SELECT
                para_id || '_s0000' AS sentence_id,
                para_id,
                chapter_id,
                chapter_num,
                version_id,
                para_index,
                0 AS sentence_index,
                para_index AS global_sentence_index,
                start_offset,
                end_offset,
                char_length,
                para_hash AS sentence_hash,
                para_text AS sentence_text,
                chapter_title_current
            FROM l2_current_paragraphs_fixture;
            """
        )
        conn.execute(
            """
            INSERT INTO current_chapters_fixture (
                chapter_id, chapter_num, chapter_title_current, latest_version_id, content_full_text
            )
            VALUES
                ('ch_0001', 1, '测试章一', 'ver_ch_0001', ?),
                ('ch_0002', 2, '测试章二', 'ver_ch_0002', ?)
            """,
            (chapter_1_text, chapter_2_text),
        )
        conn.executemany(
            """
            INSERT INTO l2_current_paragraphs_fixture (
                para_id, chapter_id, chapter_num, version_id, para_index,
                start_offset, end_offset, char_length, para_hash, para_text,
                chapter_title_current
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            chapter_1_paragraphs + chapter_2_paragraphs,
        )
        conn.commit()
    finally:
        conn.close()


class L3SeedEvidenceFinderTests(unittest.TestCase):
    def test_escape_like_and_sha256_are_deterministic(self) -> None:
        self.assertEqual(escape_like("a%b"), "a\\%b")
        self.assertEqual(escape_like("a_b"), "a\\_b")
        self.assertEqual(escape_like(r"a\b"), r"a\\b")
        self.assertEqual(sha256("abc"), sha256("abc"))

    def test_seed_keyword_extraction_flattens_supported_fields(self) -> None:
        seed = {
            "items": [
                {
                    "id": "seed_1",
                    "name": "戏神道",
                    "aliases": ["舞台神道", ["戏剧神道"]],
                    "keywords": [{"main": "面具"}],
                    "representative_characters": [{"name": "陈伶"}],
                    "branches": ["分支"],
                    "forbidden_patterns": [{"pattern": "扭曲戏神"}],
                }
            ]
        }

        items = extract_seed_items(seed)
        positive, rule = build_keywords(items[0][1])

        self.assertEqual(items[0][0], "root.items[0]")
        self.assertIn("戏神道", positive)
        self.assertIn("舞台神道", positive)
        self.assertIn("戏剧神道", positive)
        self.assertIn("面具", positive)
        self.assertIn("陈伶", positive)
        self.assertIn("分支", positive)
        self.assertEqual(rule, ["扭曲戏神"])

    def test_run_finds_candidates_context_and_backcut_verifies_l1(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            create_minimal_project_db(project_dir)
            seed_path = project_dir / "config" / "sample.seed.json"
            seed_path.parent.mkdir()
            seed_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "seed_1",
                                "name": "戏神道",
                                "aliases": ["舞台神道"],
                                "keywords": ["面具"],
                                "representative_characters": [{"name": "陈伶"}],
                                "branches": ["分支"],
                                "forbidden_patterns": [{"pattern": "扭曲戏神"}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            result = run_l3_seed_evidence_finder(
                project_dir,
                seed_path,
                top_k=5,
                context_paragraphs=1,
            )

            output_json = project_dir / "outputs" / "l3_seed_evidence_candidates.json"
            output_md = project_dir / "outputs" / "l3_seed_evidence_report.md"
            self.assertTrue(output_json.exists())
            self.assertTrue(output_md.exists())
            self.assertEqual(result["meta"]["read_views"], ["v_l2_current_paragraphs", "v_current_chapters"])
            candidates = result["items"][0]["candidates"]
            self.assertGreaterEqual(len(candidates), 1)
            top = candidates[0]
            self.assertEqual(top["status"], "candidate")
            self.assertEqual(top["evidence_type"], "positive_candidate")
            for key in ("chapter_num", "paragraph_index", "paragraph_id", "char_start", "char_end", "paragraph_hash"):
                self.assertIn(key, top)
            self.assertTrue(top["l1_backcut_check"]["matched"])
            self.assertIn("戏神道", top["paragraph_text"])
            self.assertEqual(top["paragraph_index"], 1)
            self.assertEqual([item["paragraph_index"] for item in top["context"]], [0, 1, 2])
            self.assertTrue(all(item["chapter_id"] == top["chapter_id"] for item in top["context"]))
            self.assertTrue(all(item["chapter_id"] != "ch_0002" for item in top["context"]))

    def test_rule_keyword_produces_rule_violation_candidate_with_candidate_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            create_minimal_project_db(project_dir)
            seed_path = project_dir / "config" / "sample.seed.json"
            seed_path.parent.mkdir()
            seed_path.write_text(
                json.dumps(
                    {
                        "items": [
                            {
                                "id": "seed_1",
                                "name": "missing positive",
                                "forbidden_patterns": [{"pattern": "戏神道"}],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            result = run_l3_seed_evidence_finder(project_dir, seed_path)
            candidates = result["items"][0]["candidates"]

            self.assertGreaterEqual(len(candidates), 1)
            self.assertTrue(all(candidate["status"] == "candidate" for candidate in candidates))
            self.assertTrue(all(candidate["evidence_type"] == "rule_violation_candidate" for candidate in candidates))

    def test_source_does_not_contain_forbidden_dependencies_or_l1_l2_writes(self) -> None:
        source = (PROJECT_ROOT / "scripts" / "l3_seed_evidence_finder.py").read_text(encoding="utf-8").lower()
        for forbidden in ("chroma", "embedding", "openai", "llm"):
            self.assertNotIn(forbidden, source)
        for forbidden_sql in ("insert into", "update ", "delete from", "drop ", "alter "):
            self.assertNotIn(forbidden_sql, source)


if __name__ == "__main__":
    unittest.main()
