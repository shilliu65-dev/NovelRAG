import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_apply_evidence_review import run_l3_apply_evidence_review


def make_candidate(seed_item_id: str, candidate_id: str, chapter_num: int, score: int, human_status: str | None = None) -> dict:
    return {
        "candidate_id": candidate_id,
        "status": "candidate",
        "human_status": human_status,
        "evidence_type": "positive_candidate",
        "score": score,
        "chapter_id": f"ch_{chapter_num:04d}",
        "version_id": f"ver_{chapter_num:04d}",
        "chapter_num": chapter_num,
        "chapter_title": f"Chapter {chapter_num}",
        "paragraph_id": f"p_{candidate_id}",
        "paragraph_index": 1,
        "char_start": 10,
        "char_end": 20,
        "paragraph_hash": f"hash-{candidate_id}",
        "paragraph_text": f"text for {candidate_id}",
        "context": [],
        "matched_keywords": [seed_item_id],
        "l1_backcut_check": {
            "checked": True,
            "matched": True,
            "expected_hash": f"hash-{candidate_id}",
            "actual_hash": f"hash-{candidate_id}",
            "cut_length": 10,
        },
        "l2_coordinates": {
            "paragraph_id": f"p_{candidate_id}",
            "paragraph_index": 1,
            "char_start": 10,
            "char_end": 20,
        },
    }


def queue_payload() -> dict:
    return {
        "meta": {
            "tool": "l3_evidence_review_verifier",
            "version": "v1",
            "status_policy": "candidate + human_status review",
        },
        "summary": {
            "seed_items": 2,
            "review_queue_items": 6,
        },
        "review_items": [
            {
                "seed_source": {
                    "seed_file": "config/sample.seed.json",
                    "item_path": "root.items[0]",
                    "item_id": "seed_1",
                    "item_name": "Seed One",
                },
                **make_candidate("seed_1", "cand_1", 1, 90, None),
                "human_note": None,
            },
            {
                "seed_source": {
                    "seed_file": "config/sample.seed.json",
                    "item_path": "root.items[0]",
                    "item_id": "seed_1",
                    "item_name": "Seed One",
                },
                **make_candidate("seed_1", "cand_2", 2, 80, "accepted"),
                "human_note": None,
            },
            {
                "seed_source": {
                    "seed_file": "config/sample.seed.json",
                    "item_path": "root.items[0]",
                    "item_id": "seed_1",
                    "item_name": "Seed One",
                },
                **make_candidate("seed_1", "cand_3", 3, 70, "rejected"),
                "human_note": None,
            },
            {
                "seed_source": {
                    "seed_file": "config/sample.seed.json",
                    "item_path": "root.items[0]",
                    "item_id": "seed_1",
                    "item_name": "Seed One",
                },
                **make_candidate("seed_1", "cand_4", 4, 60, "needs_more"),
                "human_note": None,
            },
            {
                "seed_source": {
                    "seed_file": "config/sample.seed.json",
                    "item_path": "root.items[1]",
                    "item_id": "seed_2",
                    "item_name": "Seed Two",
                },
                **make_candidate("seed_2", "cand_5", 5, 88, "accepted"),
                "human_note": None,
            },
            {
                "seed_source": {
                    "seed_file": "config/sample.seed.json",
                    "item_path": "root.items[1]",
                    "item_id": "seed_2",
                    "item_name": "Seed Two",
                },
                **make_candidate("seed_2", "cand_6", 6, 77, None),
                "human_note": None,
            },
        ],
    }


class L3ApplyEvidenceReviewTests(unittest.TestCase):
    def test_emit_review_template_generates_top_k_with_null_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            input_path = project_dir / "outputs" / "l3_evidence_review_queue.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(queue_payload(), ensure_ascii=False, indent=2), encoding="utf-8")

            result = run_l3_apply_evidence_review(
                project_dir=project_dir,
                input_path=input_path,
                emit_review_template=True,
                output=project_dir / "outputs" / "l3_evidence_review_manual_template.json",
                top_k_per_seed=3,
            )

            template = result["template"]
            self.assertEqual(template["meta"]["top_k_per_seed"], 3)
            self.assertEqual([item["seed_item_id"] for item in template["items"]], ["seed_1", "seed_2"])
            self.assertEqual(len(template["items"][0]["review_candidates"]), 3)
            self.assertTrue(all(item["human_status"] is None for item in template["items"][0]["review_candidates"]))
            self.assertEqual(
                [item["candidate"]["candidate_id"] for item in template["items"][0]["review_candidates"]],
                ["cand_1", "cand_2", "cand_3"],
            )

    def test_invalid_human_status_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            payload = queue_payload()
            payload["review_items"][0]["human_status"] = "bad_status"
            input_path = project_dir / "outputs" / "review.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            with self.assertRaises(ValueError):
                run_l3_apply_evidence_review(project_dir=project_dir, input_path=input_path)

    def test_classifies_accepted_rejected_and_needs_more_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            template = {
                "meta": {"tool": "l3_apply_evidence_review", "version": "v1", "top_k_per_seed": 3},
                "items": [
                    {
                        "seed_item_id": "seed_1",
                        "seed_item_path": "root.items[0]",
                        "seed_item_name": "Seed One",
                        "review_candidates": [
                            {"human_status": "accepted", "review_note": None, "candidate": make_candidate("seed_1", "cand_1", 1, 90)},
                            {"human_status": "rejected", "review_note": "not enough", "candidate": make_candidate("seed_1", "cand_2", 2, 80)},
                            {"human_status": "needs_more", "review_note": "check later", "candidate": make_candidate("seed_1", "cand_3", 3, 70)},
                            {"human_status": None, "review_note": None, "candidate": make_candidate("seed_1", "cand_4", 4, 60)},
                        ],
                    }
                ],
            }
            input_path = project_dir / "outputs" / "template.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")

            result = run_l3_apply_evidence_review(
                project_dir=project_dir,
                input_path=input_path,
                output_accepted=project_dir / "outputs" / "accepted.json",
                output_rejected=project_dir / "outputs" / "rejected.json",
                output_needs_more=project_dir / "outputs" / "needs_more.json",
                summary=project_dir / "outputs" / "summary.json",
            )

            self.assertEqual(result["accepted"]["meta"]["accepted_count"], 1)
            self.assertEqual(result["rejected"]["meta"]["rejected_count"], 1)
            self.assertEqual(result["needs_more"]["meta"]["needs_more_count"], 1)
            accepted = result["accepted"]["items"][0]
            self.assertEqual(accepted["seed_item_id"], "seed_1")
            self.assertEqual(accepted["candidate_id"], "cand_1")
            self.assertIn("chapter_id", accepted)
            self.assertIn("chapter_num", accepted)
            self.assertIn("evidence_text", accepted)
            self.assertIn("l2_coordinates", accepted)
            self.assertIn("backcut", accepted)
            self.assertTrue(accepted["backcut"]["matched"])
            self.assertEqual(result["summary"]["status_counts"]["unreviewed"], 1)

    def test_top_k_per_seed_limits_classification_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            payload = queue_payload()
            input_path = project_dir / "outputs" / "review.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

            result = run_l3_apply_evidence_review(
                project_dir=project_dir,
                input_path=input_path,
                top_k_per_seed=2,
                output_accepted=project_dir / "outputs" / "accepted.json",
                output_rejected=project_dir / "outputs" / "rejected.json",
                output_needs_more=project_dir / "outputs" / "needs_more.json",
                summary=project_dir / "outputs" / "summary.json",
            )

            self.assertEqual(result["summary"]["candidate_counts_by_seed"]["seed_1"], 2)
            self.assertEqual(result["summary"]["status_counts"]["accepted"], 2)
            self.assertEqual(result["summary"]["status_counts"]["rejected"], 0)
            self.assertEqual(result["summary"]["status_counts"]["needs_more"], 0)

    def test_outputs_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            input_path = project_dir / "outputs" / "review.json"
            input_path.parent.mkdir(parents=True, exist_ok=True)
            input_path.write_text(json.dumps(queue_payload(), ensure_ascii=False, indent=2), encoding="utf-8")

            accepted_path = project_dir / "outputs" / "accepted.json"
            rejected_path = project_dir / "outputs" / "rejected.json"
            needs_more_path = project_dir / "outputs" / "needs_more.json"
            summary_path = project_dir / "outputs" / "summary.json"

            run_l3_apply_evidence_review(
                project_dir=project_dir,
                input_path=input_path,
                output_accepted=accepted_path,
                output_rejected=rejected_path,
                output_needs_more=needs_more_path,
                summary=summary_path,
            )
            first = {
                "accepted": accepted_path.read_text(encoding="utf-8"),
                "rejected": rejected_path.read_text(encoding="utf-8"),
                "needs_more": needs_more_path.read_text(encoding="utf-8"),
                "summary": summary_path.read_text(encoding="utf-8"),
            }

            run_l3_apply_evidence_review(
                project_dir=project_dir,
                input_path=input_path,
                output_accepted=accepted_path,
                output_rejected=rejected_path,
                output_needs_more=needs_more_path,
                summary=summary_path,
            )
            second = {
                "accepted": accepted_path.read_text(encoding="utf-8"),
                "rejected": rejected_path.read_text(encoding="utf-8"),
                "needs_more": needs_more_path.read_text(encoding="utf-8"),
                "summary": summary_path.read_text(encoding="utf-8"),
            }

            self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
