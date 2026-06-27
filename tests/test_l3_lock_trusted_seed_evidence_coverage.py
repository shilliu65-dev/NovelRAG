import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_lock_trusted_seed_evidence_coverage import run_l3_lock_trusted_seed_evidence_coverage


def make_seed_item(seed_index: int) -> dict:
    return {
        "seed_source": {
            "seed_file": "config/sample.seed.json",
            "item_path": f"root.items[{seed_index - 1}]",
            "item_id": f"seed_{seed_index:02d}",
            "item_name": f"Seed {seed_index:02d}",
        },
        "keyword_set": [f"Seed {seed_index:02d}"],
        "positive_keywords": [f"Seed {seed_index:02d}"],
        "rule_keywords": [],
        "candidates": [],
    }


def make_candidate(seed_index: int, candidate_index: int) -> dict:
    candidate_id = f"cand_{seed_index:02d}_{candidate_index}"
    chapter_num = ((seed_index - 1) % 8) + candidate_index
    return {
        "candidate_id": candidate_id,
        "status": "candidate",
        "human_status": "accepted",
        "review_note": f"accepted candidate {candidate_id}",
        "evidence_type": "positive_candidate",
        "score": 100 - candidate_index,
        "chapter_id": f"ch_{chapter_num:04d}",
        "version_id": f"ver_{chapter_num:04d}",
        "chapter_num": chapter_num,
        "chapter_title": f"Chapter {chapter_num}",
        "paragraph_id": f"p_{candidate_id}",
        "paragraph_index": candidate_index,
        "char_start": 10 * candidate_index,
        "char_end": (10 * candidate_index) + 9,
        "paragraph_hash": f"hash-{candidate_id}",
        "evidence_text": f"Evidence text for {candidate_id}",
        "matched_keywords": [f"Seed {seed_index:02d}"],
        "l2_coordinates": {
            "paragraph_id": f"p_{candidate_id}",
            "paragraph_index": candidate_index,
            "char_start": 10 * candidate_index,
            "char_end": (10 * candidate_index) + 9,
        },
        "backcut": {
            "checked": True,
            "matched": True,
            "expected_hash": f"hash-{candidate_id}",
            "actual_hash": f"hash-{candidate_id}",
            "cut_length": 9,
        },
    }


def build_fixture_payloads() -> tuple[dict, dict, dict, dict]:
    candidates_items = [make_seed_item(seed_index) for seed_index in range(1, 44)]
    review_items: list[dict] = []
    accepted_items: list[dict] = []

    total_accepted = 0
    for seed_index in range(1, 42):
        accepted_for_seed = 3 if seed_index <= 33 else 2
        review_candidates = []
        candidate_list = []
        for candidate_index in range(1, accepted_for_seed + 1):
            candidate = make_candidate(seed_index, candidate_index)
            review_candidates.append(
                {
                    "human_status": "accepted",
                    "review_note": candidate["review_note"],
                    "candidate": candidate,
                }
            )
            candidate_list.append({**candidate, "human_status": "accepted"})
            accepted_items.append(
                {
                    "seed_file": "config/sample.seed.json",
                    "seed_item_id": f"seed_{seed_index:02d}",
                    "seed_item_path": f"root.items[{seed_index - 1}]",
                    "seed_item_name": f"Seed {seed_index:02d}",
                    "candidate_id": candidate["candidate_id"],
                    "candidate_status": "candidate",
                    "human_status": "accepted",
                    "review_note": candidate["review_note"],
                    "evidence_type": candidate["evidence_type"],
                    "score": candidate["score"],
                    "chapter_id": candidate["chapter_id"],
                    "version_id": candidate["version_id"],
                    "chapter_num": candidate["chapter_num"],
                    "chapter_title": candidate["chapter_title"],
                    "paragraph_id": candidate["paragraph_id"],
                    "paragraph_index": candidate["paragraph_index"],
                    "char_start": candidate["char_start"],
                    "char_end": candidate["char_end"],
                    "paragraph_hash": candidate["paragraph_hash"],
                    "evidence_text": candidate["evidence_text"],
                    "matched_keywords": candidate["matched_keywords"],
                    "l2_coordinates": candidate["l2_coordinates"],
                    "hash_info": {"paragraph_hash": candidate["paragraph_hash"]},
                    "backcut": candidate["backcut"],
                }
            )
            total_accepted += 1
        candidates_items[seed_index - 1]["candidates"] = candidate_list
        review_items.append(
            {
                "seed_file": "config/sample.seed.json",
                "seed_item_id": f"seed_{seed_index:02d}",
                "seed_item_path": f"root.items[{seed_index - 1}]",
                "seed_item_name": f"Seed {seed_index:02d}",
                "review_candidates": review_candidates,
            }
        )

    for seed_index in (42, 43):
        review_items.append(
            {
                "seed_file": "config/sample.seed.json",
                "seed_item_id": f"seed_{seed_index:02d}",
                "seed_item_path": f"root.items[{seed_index - 1}]",
                "seed_item_name": f"Seed {seed_index:02d}",
                "review_candidates": [],
            }
        )

    assert total_accepted == 115

    trusted_packet = {
        "meta": {
            "tool": "l3_build_trusted_evidence_packet",
            "version": "v1",
        },
        "items": accepted_items,
    }
    trusted_summary = {
        "meta": {
            "tool": "l3_build_trusted_evidence_packet",
            "version": "v1",
        },
        "status_counts": {
            "accepted": 115,
            "rejected": 0,
            "needs_more": 0,
            "pending": 0,
        },
    }
    review_template = {
        "meta": {
            "tool": "l3_apply_evidence_review",
            "version": "v1",
            "top_k_per_seed": 3,
        },
        "items": review_items,
    }
    candidates = {
        "meta": {
            "tool": "l3_seed_evidence_finder",
            "version": "v1",
        },
        "items": candidates_items,
    }
    return trusted_packet, trusted_summary, review_template, candidates


class L3LockTrustedSeedEvidenceCoverageTests(unittest.TestCase):
    def write_inputs(self, project_dir: Path) -> tuple[Path, Path, Path, Path]:
        outputs_dir = project_dir / "outputs"
        outputs_dir.mkdir(parents=True, exist_ok=True)
        trusted_packet, trusted_summary, review_template, candidates = build_fixture_payloads()

        trusted_packet_path = outputs_dir / "l3_seed_rule_trusted_evidence_packet.json"
        trusted_summary_path = outputs_dir / "l3_seed_rule_trusted_evidence_summary.json"
        review_template_path = outputs_dir / "l3_evidence_review_manual_template_top3_reviewed_all_accepted.json"
        candidates_path = outputs_dir / "l3_seed_evidence_candidates.json"

        trusted_packet_path.write_text(json.dumps(trusted_packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        trusted_summary_path.write_text(json.dumps(trusted_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        review_template_path.write_text(json.dumps(review_template, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        candidates_path.write_text(json.dumps(candidates, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return trusted_packet_path, trusted_summary_path, review_template_path, candidates_path

    def test_generates_coverage_lock_gap_review_and_validation_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, trusted_summary_path, review_template_path, candidates_path = self.write_inputs(project_dir)

            result = run_l3_lock_trusted_seed_evidence_coverage(
                project_dir=project_dir,
                trusted_packet=trusted_packet_path,
                trusted_summary=trusted_summary_path,
                review_template=review_template_path,
                candidates=candidates_path,
                fixed_generated_at="2026-06-25T13:00:00",
            )

            coverage_lock = result["coverage_lock"]
            gap_review = result["gap_review"]
            validation_report = result["validation_report"]

            self.assertEqual(coverage_lock["summary"]["seed_items_total"], 43)
            self.assertEqual(coverage_lock["summary"]["seed_items_with_accepted_evidence"], 41)
            self.assertEqual(coverage_lock["summary"]["seed_items_without_accepted_evidence"], 2)
            self.assertEqual(coverage_lock["summary"]["accepted_evidence_count"], 115)
            self.assertEqual(coverage_lock["summary"]["pending_evidence_count"], 0)
            self.assertEqual(coverage_lock["summary"]["max_accepted_evidence_per_seed"], 3)
            self.assertAlmostEqual(coverage_lock["summary"]["coverage_rate"], 41 / 43, places=10)
            self.assertEqual(len(coverage_lock["seed_coverage_items"]), 43)
            self.assertEqual(len(gap_review["gap_items"]), 2)
            self.assertTrue(coverage_lock["integrity"]["lock_validation_passed"])
            self.assertTrue(validation_report["coverage_lock_passed"])
            self.assertTrue(validation_report["gap_review_generated"])
            self.assertEqual(validation_report["modifies_index_db"], False)
            self.assertEqual(validation_report["modifies_seed_config"], False)
            self.assertEqual(validation_report["test_results"]["commands"][0]["command"], "python -m unittest tests.test_l3_lock_trusted_seed_evidence_coverage -v")

            uncovered_ids = [item["seed_item_id"] for item in gap_review["gap_items"]]
            self.assertEqual(uncovered_ids, ["seed_42", "seed_43"])
            self.assertTrue(all(item["gap_reason"] == "no_candidate_evidence" for item in gap_review["gap_items"]))
            self.assertTrue(all(item["current_status"] == "uncovered" for item in gap_review["gap_items"]))

            seed_01 = coverage_lock["seed_coverage_items"][0]
            self.assertEqual(seed_01["seed_item_id"], "seed_01")
            self.assertEqual(seed_01["coverage_status"], "covered")
            self.assertEqual(len(seed_01["candidate_ids"]), 3)
            self.assertEqual(seed_01["next_action"], "keep_as_coverage_locked")

            seed_43 = coverage_lock["seed_coverage_items"][-1]
            self.assertEqual(seed_43["seed_item_id"], "seed_43")
            self.assertEqual(seed_43["coverage_status"], "uncovered")
            self.assertEqual(seed_43["accepted_evidence_count"], 0)
            self.assertEqual(seed_43["gap_reason"], "no_candidate_evidence")
            self.assertEqual(seed_43["next_action"], "schedule_followup_evidence_search")

    def test_outputs_are_idempotent_with_fixed_generated_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, trusted_summary_path, review_template_path, candidates_path = self.write_inputs(project_dir)

            kwargs = {
                "project_dir": project_dir,
                "trusted_packet": trusted_packet_path,
                "trusted_summary": trusted_summary_path,
                "review_template": review_template_path,
                "candidates": candidates_path,
                "fixed_generated_at": "2026-06-25T13:00:00",
            }
            run_l3_lock_trusted_seed_evidence_coverage(**kwargs)
            first = {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted((project_dir / "outputs").glob("l3_trusted_seed_evidence_*.*"))
            }
            run_l3_lock_trusted_seed_evidence_coverage(**kwargs)
            second = {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted((project_dir / "outputs").glob("l3_trusted_seed_evidence_*.*"))
            }
            self.assertEqual(first, second)

    def test_rejects_missing_candidate_back_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, trusted_summary_path, review_template_path, candidates_path = self.write_inputs(project_dir)

            trusted_packet = json.loads(trusted_packet_path.read_text(encoding="utf-8"))
            trusted_packet["items"][0]["candidate_id"] = "missing_candidate"
            trusted_packet_path.write_text(json.dumps(trusted_packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            with self.assertRaises(ValueError):
                run_l3_lock_trusted_seed_evidence_coverage(
                    project_dir=project_dir,
                    trusted_packet=trusted_packet_path,
                    trusted_summary=trusted_summary_path,
                    review_template=review_template_path,
                    candidates=candidates_path,
                    fixed_generated_at="2026-06-25T13:00:00",
                )

    def test_supports_stage_c_seed_rules_trusted_packet_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, trusted_summary_path, review_template_path, candidates_path = self.write_inputs(project_dir)

            original_packet = json.loads(trusted_packet_path.read_text(encoding="utf-8"))
            stage_c_packet = {
                "meta": original_packet["meta"],
                "summary": {
                    "seed_items_total": 43,
                    "seed_items_with_accepted_evidence": 41,
                    "seed_items_without_accepted_evidence": 2,
                    "accepted_evidence_count": 115,
                    "rejected_evidence_count": 0,
                    "needs_more_evidence_count": 0,
                    "pending_evidence_count": 0,
                    "max_accepted_evidence_per_seed": 3,
                    "all_seed_items_lte_3": True,
                },
                "seed_rules": [],
            }
            grouped: dict[str, list[dict]] = {}
            for item in original_packet["items"]:
                grouped.setdefault(item["seed_item_id"], []).append(item)
            for seed_item_id, evidence_items in sorted(grouped.items()):
                stage_c_packet["seed_rules"].append(
                    {
                        "seed_item_id": seed_item_id,
                        "seed_file": evidence_items[0]["seed_file"],
                        "seed_path": evidence_items[0]["seed_item_path"],
                        "seed_rule_type": "test_seed_rule",
                        "seed_rule_text": evidence_items[0]["seed_item_name"],
                        "trusted_status": "trusted" if evidence_items else "uncovered",
                        "trusted_evidence": [
                            {
                                "candidate_id": evidence["candidate_id"],
                                "chapter_id": evidence["chapter_id"],
                                "chapter_num": evidence["chapter_num"],
                                "paragraph_hash": evidence["paragraph_hash"],
                                "evidence_text": evidence["evidence_text"],
                                "backcut": evidence["backcut"],
                                "human_status": "accepted",
                                "review_note": evidence["review_note"],
                                "evidence_hash": f"hash-{evidence['candidate_id']}",
                            }
                            for evidence in evidence_items
                        ],
                    }
                )
            trusted_packet_path.write_text(json.dumps(stage_c_packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_l3_lock_trusted_seed_evidence_coverage(
                project_dir=project_dir,
                trusted_packet=trusted_packet_path,
                trusted_summary=trusted_summary_path,
                review_template=review_template_path,
                candidates=candidates_path,
                fixed_generated_at="2026-06-25T13:00:00",
            )

            self.assertEqual(result["coverage_lock"]["summary"]["seed_items_with_accepted_evidence"], 41)
            self.assertEqual(result["coverage_lock"]["summary"]["accepted_evidence_count"], 115)

    def test_resolves_candidate_back_reference_from_paragraph_id_when_candidate_id_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, trusted_summary_path, review_template_path, candidates_path = self.write_inputs(project_dir)

            trusted_packet = json.loads(trusted_packet_path.read_text(encoding="utf-8"))
            for item in trusted_packet["items"]:
                item["candidate_id"] = f"{item['seed_item_id']}:{item['paragraph_id']}"
            trusted_packet_path.write_text(json.dumps(trusted_packet, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            candidates_payload = json.loads(candidates_path.read_text(encoding="utf-8"))
            for item in candidates_payload["items"]:
                for candidate in item["candidates"]:
                    candidate.pop("candidate_id", None)
            candidates_path.write_text(json.dumps(candidates_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            result = run_l3_lock_trusted_seed_evidence_coverage(
                project_dir=project_dir,
                trusted_packet=trusted_packet_path,
                trusted_summary=trusted_summary_path,
                review_template=review_template_path,
                candidates=candidates_path,
                fixed_generated_at="2026-06-25T13:00:00",
            )

            self.assertEqual(result["coverage_lock"]["summary"]["seed_items_with_accepted_evidence"], 41)
            self.assertEqual(result["coverage_lock"]["summary"]["accepted_evidence_count"], 115)


if __name__ == "__main__":
    unittest.main()
