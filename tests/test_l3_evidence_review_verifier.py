import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_evidence_review_verifier import (
    ALLOWED_SUGGESTED_STATUSES,
    make_evidence_id,
    run_l3_evidence_review_verifier,
)
from scripts.l3_seed_evidence_finder import sha256


def create_project_db(project_dir: Path) -> tuple[str, list[dict[str, object]]]:
    db_path = project_dir / "index" / "novel_story_bible.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    (project_dir / "outputs").mkdir(exist_ok=True)

    paragraphs = [
        "前文段落。",
        "戏神道证据段落，陈伶携带面具登场。",
        "后文段落。",
    ]
    content = "\n\n".join(paragraphs)
    rows: list[dict[str, object]] = []
    cursor = 0
    for index, paragraph in enumerate(paragraphs):
        start = content.index(paragraph, cursor)
        end = start + len(paragraph)
        rows.append(
            {
                "chapter_id": "ch_0001",
                "version_id": "ver_0001",
                "chapter_num": 1,
                "chapter_title": "测试章",
                "paragraph_id": f"ch_0001_p{index:04d}",
                "paragraph_index": index,
                "char_start": start,
                "char_end": end,
                "paragraph_hash": sha256(paragraph),
                "paragraph_text": paragraph,
            }
        )
        cursor = end

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE current_chapters_fixture (
                chapter_id TEXT PRIMARY KEY,
                latest_version_id TEXT NOT NULL,
                content_full_text TEXT NOT NULL
            );

            CREATE VIEW v_current_chapters AS
            SELECT chapter_id, latest_version_id, content_full_text
            FROM current_chapters_fixture;

            CREATE VIEW v_l2_current_paragraphs AS
            SELECT 1 AS unused;

            CREATE VIEW v_l2_current_sentences AS
            SELECT 1 AS unused;
            """
        )
        conn.execute(
            "INSERT INTO current_chapters_fixture (chapter_id, latest_version_id, content_full_text) VALUES (?, ?, ?)",
            ("ch_0001", "ver_0001", content),
        )
        conn.commit()
    finally:
        conn.close()
    return content, rows


def make_candidate(row: dict[str, object], *, status: str = "candidate", score: int = 80, evidence_type: str = "positive_candidate") -> dict[str, object]:
    return {
        "status": status,
        "evidence_type": evidence_type,
        "score": score,
        "score_breakdown": {"name_hit": 30},
        "matched_keywords": ["戏神道", "陈伶"],
        **row,
        "context": [],
        "l1_backcut_check": {
            "checked": True,
            "matched": True,
            "expected_hash": row["paragraph_hash"],
            "actual_hash": row["paragraph_hash"],
        },
    }


def write_candidates(path: Path, candidates: list[dict[str, object]], *, item_path: str = "root.items[0]") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"tool": "l3_seed_evidence_finder", "version": "v1"},
        "items": [
            {
                "seed_source": {
                    "seed_file": "config/sample.seed.json",
                    "item_path": item_path,
                    "item_id": "seed_1",
                    "item_name": "戏神道",
                },
                "keyword_set": ["戏神道"],
                "candidates": candidates,
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


class L3EvidenceReviewVerifierTests(unittest.TestCase):
    def test_review_queue_verifies_backcut_outputs_files_and_human_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            _, rows = create_project_db(project_dir)
            input_path = project_dir / "outputs" / "l3_seed_evidence_candidates.json"
            write_candidates(input_path, [make_candidate(rows[1])])

            result = run_l3_evidence_review_verifier(project_dir, input_path)

            self.assertEqual(result["summary"]["input_candidates"], 1)
            self.assertEqual(result["summary"]["valid_backcut"], 1)
            self.assertEqual(result["summary"]["invalid_backcut"], 0)
            self.assertEqual(result["summary"]["review_queue_items"], 1)
            item = result["review_items"][0]
            self.assertEqual(item["candidate_status"], "candidate")
            self.assertIn(item["suggested_status"], ALLOWED_SUGGESTED_STATUSES)
            self.assertEqual(item["suggested_status"], "likely_relevant")
            self.assertIsNone(item["human_status"])
            self.assertIsNone(item["human_reviewer"])
            self.assertIsNone(item["human_note"])
            self.assertIsNone(item["reviewed_at"])
            self.assertTrue(item["eligible_for_human_confirm"])
            self.assertTrue(item["verifier_backcut_check"]["matched"])
            for key in ("chapter_id", "version_id", "chapter_num", "chapter_title", "paragraph_id", "paragraph_index", "char_start", "char_end", "paragraph_hash"):
                self.assertIn(key, item)
            for relative in (
                "outputs/l3_evidence_review_queue.json",
                "outputs/l3_evidence_review_queue.md",
                "outputs/l3_evidence_review_queue.csv",
                "outputs/l3_evidence_review_verifier_report.md",
            ):
                self.assertTrue((project_dir / relative).exists())

    def test_input_status_must_be_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            _, rows = create_project_db(project_dir)
            input_path = project_dir / "outputs" / "l3_seed_evidence_candidates.json"
            write_candidates(input_path, [make_candidate(rows[1], status="confirmed")])

            with self.assertRaises(ValueError):
                run_l3_evidence_review_verifier(project_dir, input_path)

    def test_invalid_backcut_is_flagged_and_not_eligible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            _, rows = create_project_db(project_dir)
            bad = dict(rows[1])
            bad["paragraph_hash"] = sha256("wrong text")
            input_path = project_dir / "outputs" / "l3_seed_evidence_candidates.json"
            write_candidates(input_path, [make_candidate(bad, score=10)])

            result = run_l3_evidence_review_verifier(project_dir, input_path)

            item = result["review_items"][0]
            self.assertEqual(result["summary"]["valid_backcut"], 0)
            self.assertEqual(result["summary"]["invalid_backcut"], 1)
            self.assertIn("invalid_backcut", item["review_flags"])
            self.assertFalse(item["eligible_for_human_confirm"])
            self.assertEqual(item["suggested_status"], "invalid")
            self.assertFalse(item["verifier_backcut_check"]["matched"])

    def test_evidence_id_is_stable_and_duplicate_paragraph_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            _, rows = create_project_db(project_dir)
            input_path = project_dir / "outputs" / "l3_seed_evidence_candidates.json"
            candidate = make_candidate(rows[1])
            payload = {
                "meta": {},
                "items": [
                    {
                        "seed_source": {"seed_file": "config/a.seed.json", "item_path": "root.a", "item_id": "a", "item_name": "A"},
                        "keyword_set": ["戏神道"],
                        "candidates": [candidate, dict(candidate)],
                    },
                    {
                        "seed_source": {"seed_file": "config/b.seed.json", "item_path": "root.b", "item_id": "b", "item_name": "B"},
                        "keyword_set": ["戏神道"],
                        "candidates": [dict(candidate)],
                    },
                ],
            }
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            result = run_l3_evidence_review_verifier(project_dir, input_path)

            self.assertEqual(result["summary"]["input_candidates"], 3)
            self.assertEqual(result["summary"]["review_queue_items"], 2)
            self.assertEqual(result["summary"]["duplicate_groups"], 1)
            self.assertTrue(all("duplicate_paragraph" in item["review_flags"] for item in result["review_items"]))
            first = result["review_items"][0]
            expected = make_evidence_id(first["seed_source"], first)
            self.assertEqual(first["evidence_id"], expected)
            second_run = run_l3_evidence_review_verifier(project_dir, input_path)
            self.assertEqual(
                [item["evidence_id"] for item in result["review_items"]],
                [item["evidence_id"] for item in second_run["review_items"]],
            )

    def test_suggested_status_values_and_static_safety(self) -> None:
        source = (PROJECT_ROOT / "scripts" / "l3_evidence_review_verifier.py").read_text(encoding="utf-8").lower()
        self.assertFalse(any(pattern in source for pattern in ("import openai", "import chromadb", "from openai", "from chromadb")))
        for forbidden_sql in ("insert into", "update ", "delete from", "drop ", "alter "):
            self.assertNotIn(forbidden_sql, source)
        self.assertEqual(ALLOWED_SUGGESTED_STATUSES, {"needs_review", "likely_relevant", "likely_irrelevant", "invalid"})


if __name__ == "__main__":
    unittest.main()
