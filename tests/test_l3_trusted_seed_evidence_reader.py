import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_trusted_seed_evidence_reader import run_l3_trusted_seed_evidence_reader


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def build_trusted_packet(config_path: Path) -> dict:
    return {
        "meta": {
            "artifact_type": "l3_seed_rule_trusted_evidence_packet",
            "version": "v1",
            "project": "NovelRAG",
            "source_files": ["outputs/reviewed.json"],
            "generated_at": "",
            "notes": [],
        },
        "summary": {
            "seed_items_total": 3,
            "seed_items_with_accepted_evidence": 2,
            "seed_items_without_accepted_evidence": 1,
            "accepted_evidence_count": 3,
            "rejected_evidence_count": 0,
            "needs_more_evidence_count": 0,
            "pending_evidence_count": 0,
            "max_accepted_evidence_per_seed": 2,
            "all_seed_items_lte_3": True,
        },
        "seed_rules": [
            {
                "seed_item_id": "seed_a",
                "seed_file": str(config_path),
                "seed_path": "root.godways[0]",
                "seed_rule_type": "mainstream_14",
                "seed_rule_text": "{\"name\":\"Seed A\"}",
                "trusted_status": "trusted",
                "trusted_evidence": [
                    {
                        "candidate_id": "seed_a:p001",
                        "chapter_id": "ch_0001",
                        "chapter_num": 1,
                        "paragraph_hash": "hash-a1",
                        "evidence_text": "Evidence A1",
                        "backcut": {"matched": True},
                        "human_status": "accepted",
                        "review_note": "",
                        "evidence_hash": "evidence-hash-a1",
                    },
                    {
                        "candidate_id": "seed_a:p002",
                        "chapter_id": "ch_0002",
                        "chapter_num": 2,
                        "paragraph_hash": "hash-a2",
                        "evidence_text": "Evidence A2",
                        "backcut": {"matched": True},
                        "human_status": "accepted",
                        "review_note": "",
                        "evidence_hash": "evidence-hash-a2",
                    },
                ],
            },
            {
                "seed_item_id": "seed_b",
                "seed_file": str(config_path),
                "seed_path": "root.godways[1]",
                "seed_rule_type": "ancient_declined_4",
                "seed_rule_text": "{\"name\":\"Seed B\"}",
                "trusted_status": "trusted",
                "trusted_evidence": [
                    {
                        "candidate_id": "seed_b:p003",
                        "chapter_id": "ch_0002",
                        "chapter_num": 2,
                        "paragraph_hash": "hash-b1",
                        "evidence_text": "Evidence B1",
                        "backcut": {"matched": True},
                        "human_status": "accepted",
                        "review_note": "",
                        "evidence_hash": "evidence-hash-b1",
                    }
                ],
            },
            {
                "seed_item_id": "seed_c",
                "seed_file": str(config_path),
                "seed_path": "root.godways[2]",
                "seed_rule_type": "mainstream_14",
                "seed_rule_text": "{\"name\":\"Seed C\"}",
                "trusted_status": "no_accepted_evidence",
                "trusted_evidence": [],
            },
        ],
    }


def build_coverage_lock(config_path: Path) -> dict:
    return {
        "metadata": {
            "generated_at": "2026-06-25T14:00:00",
            "project_dir": str(config_path.parents[1]),
            "input_files": {},
            "input_file_sha256": {},
            "script_name": "l3_lock_trusted_seed_evidence_coverage.py",
            "contract_version": "v1",
        },
        "summary": {
            "seed_items_total": 3,
            "seed_items_with_accepted_evidence": 2,
            "seed_items_without_accepted_evidence": 1,
            "accepted_evidence_count": 3,
            "pending_evidence_count": 0,
            "rejected_evidence_count": 0,
            "needs_more_evidence_count": 0,
            "max_accepted_evidence_per_seed": 2,
            "coverage_rate": 2 / 3,
        },
        "seed_coverage_items": [
            {
                "seed_item_id": "seed_a",
                "seed_source_file": str(config_path),
                "seed_category": "godway",
                "seed_label": "Seed A",
                "coverage_status": "covered",
                "accepted_evidence_count": 2,
                "candidate_ids": ["seed_a:p001", "seed_a:p002"],
                "chapter_nums": [1, 2],
                "evidence_refs": ["Evidence A1", "Evidence A2"],
                "gap_reason": None,
                "next_action": "keep_as_coverage_locked",
            },
            {
                "seed_item_id": "seed_b",
                "seed_source_file": str(config_path),
                "seed_category": "godway",
                "seed_label": "Seed B",
                "coverage_status": "covered",
                "accepted_evidence_count": 1,
                "candidate_ids": ["seed_b:p003"],
                "chapter_nums": [2],
                "evidence_refs": ["Evidence B1"],
                "gap_reason": None,
                "next_action": "keep_as_coverage_locked",
            },
            {
                "seed_item_id": "seed_c",
                "seed_source_file": str(config_path),
                "seed_category": "godway",
                "seed_label": "Seed C",
                "coverage_status": "uncovered",
                "accepted_evidence_count": 0,
                "candidate_ids": [],
                "chapter_nums": [],
                "evidence_refs": [],
                "gap_reason": "no_candidate_evidence",
                "next_action": "schedule_followup_evidence_search",
            },
        ],
        "integrity": {
            "duplicate_seed_item_id_detected": False,
            "duplicate_seed_item_ids": [],
            "accepted_evidence_missing_candidate_detected": False,
            "accepted_evidence_missing_candidate_refs": [],
            "illegal_human_status_detected": False,
            "illegal_human_status_refs": [],
            "pending_evidence_detected": False,
            "accepted_evidence_exceeds_top3_limit_detected": False,
            "lock_validation_passed": True,
        },
    }


def build_gap_review(config_path: Path) -> dict:
    return {
        "metadata": {
            "generated_at": "2026-06-25T14:00:00",
            "project_dir": str(config_path.parents[1]),
            "input_files": {},
            "input_file_sha256": {},
            "script_name": "l3_lock_trusted_seed_evidence_coverage.py",
            "contract_version": "v1",
        },
        "summary": {
            "uncovered_seed_count": 1,
        },
        "gap_items": [
            {
                "seed_item_id": "seed_c",
                "seed_source_file": str(config_path),
                "seed_category": "godway",
                "seed_content_summary": "Seed C",
                "current_status": "uncovered",
                "gap_reason": "no_candidate_evidence",
                "risk_level": "medium",
                "recommended_action": "schedule_followup_evidence_search",
            }
        ],
    }


class L3TrustedSeedEvidenceReaderTests(unittest.TestCase):
    def write_inputs(self, project_dir: Path) -> tuple[Path, Path, Path]:
        config_path = project_dir / "config" / "godway_catalog.seed.json"
        write_json(config_path, {"godways": []})

        trusted_packet_path = project_dir / "outputs" / "l3_seed_rule_trusted_evidence_packet.json"
        coverage_lock_path = project_dir / "outputs" / "l3_trusted_seed_evidence_coverage_lock.json"
        gap_review_path = project_dir / "outputs" / "l3_trusted_seed_evidence_gap_review.json"
        write_json(trusted_packet_path, build_trusted_packet(config_path))
        write_json(coverage_lock_path, build_coverage_lock(config_path))
        write_json(gap_review_path, build_gap_review(config_path))
        return trusted_packet_path, coverage_lock_path, gap_review_path

    def test_reads_trusted_packet_coverage_lock_and_gap_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, coverage_lock_path, gap_review_path = self.write_inputs(project_dir)

            result = run_l3_trusted_seed_evidence_reader(
                project_dir=project_dir,
                trusted_packet=trusted_packet_path,
                coverage_lock=coverage_lock_path,
                gap_review=gap_review_path,
                fixed_generated_at="2026-06-25T14:00:00",
            )

            self.assertEqual(result["sample"]["summary"]["seed_items_total"], 3)
            self.assertEqual(result["sample"]["summary"]["covered_seed_items"], 2)
            self.assertEqual(result["sample"]["summary"]["uncovered_seed_items"], 1)
            self.assertEqual(result["sample"]["summary"]["accepted_evidence_count"], 3)
            self.assertEqual(len(result["sample"]["results"]), 2)
            self.assertEqual(len(result["sample"]["uncovered_seed_items"]), 1)
            self.assertEqual(result["validation_report"]["integrity_check_result"], "passed")

    def test_filters_by_seed_item_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, coverage_lock_path, gap_review_path = self.write_inputs(project_dir)

            result = run_l3_trusted_seed_evidence_reader(
                project_dir=project_dir,
                trusted_packet=trusted_packet_path,
                coverage_lock=coverage_lock_path,
                gap_review=gap_review_path,
                seed_item_id="seed_b",
                fixed_generated_at="2026-06-25T14:00:00",
            )

            self.assertEqual(result["sample"]["summary"]["query_result_count"], 1)
            self.assertEqual(result["sample"]["results"][0]["seed_item_id"], "seed_b")

    def test_filters_by_seed_file_rule_type_trusted_status_and_chapter_num(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, coverage_lock_path, gap_review_path = self.write_inputs(project_dir)
            config_path = project_dir / "config" / "godway_catalog.seed.json"

            result = run_l3_trusted_seed_evidence_reader(
                project_dir=project_dir,
                trusted_packet=trusted_packet_path,
                coverage_lock=coverage_lock_path,
                gap_review=gap_review_path,
                seed_file=str(config_path),
                seed_rule_type="mainstream_14",
                trusted_status="trusted",
                chapter_num=2,
                include_evidence_text=True,
                fixed_generated_at="2026-06-25T14:00:00",
            )

            self.assertEqual(result["sample"]["summary"]["query_result_count"], 1)
            self.assertEqual(result["sample"]["results"][0]["seed_item_id"], "seed_a")
            self.assertEqual(len(result["sample"]["results"][0]["evidence_refs"]), 1)
            self.assertEqual(result["sample"]["results"][0]["evidence_refs"][0]["chapter_num"], 2)
            self.assertEqual(result["sample"]["results"][0]["evidence_refs"][0]["evidence_text"], "Evidence A2")

    def test_query_results_exclude_unaccepted_evidence_and_returns_uncovered(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, coverage_lock_path, gap_review_path = self.write_inputs(project_dir)

            result = run_l3_trusted_seed_evidence_reader(
                project_dir=project_dir,
                trusted_packet=trusted_packet_path,
                coverage_lock=coverage_lock_path,
                gap_review=gap_review_path,
                fixed_generated_at="2026-06-25T14:00:00",
            )

            all_statuses = [
                evidence["human_status"]
                for item in result["sample"]["results"]
                for evidence in item["evidence_refs"]
            ]
            self.assertEqual(all_statuses, ["accepted", "accepted", "accepted"])
            self.assertEqual(result["sample"]["uncovered_seed_items"][0]["seed_item_id"], "seed_c")

    def test_fails_fast_when_packet_and_coverage_lock_counts_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, coverage_lock_path, gap_review_path = self.write_inputs(project_dir)
            coverage_lock = json.loads(coverage_lock_path.read_text(encoding="utf-8"))
            coverage_lock["summary"]["accepted_evidence_count"] = 999
            write_json(coverage_lock_path, coverage_lock)

            with self.assertRaises(ValueError):
                run_l3_trusted_seed_evidence_reader(
                    project_dir=project_dir,
                    trusted_packet=trusted_packet_path,
                    coverage_lock=coverage_lock_path,
                    gap_review=gap_review_path,
                    fixed_generated_at="2026-06-25T14:00:00",
                )

    def test_outputs_are_idempotent_with_fixed_generated_at(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            trusted_packet_path, coverage_lock_path, gap_review_path = self.write_inputs(project_dir)

            kwargs = {
                "project_dir": project_dir,
                "trusted_packet": trusted_packet_path,
                "coverage_lock": coverage_lock_path,
                "gap_review": gap_review_path,
                "fixed_generated_at": "2026-06-25T14:00:00",
            }
            run_l3_trusted_seed_evidence_reader(**kwargs)
            first = {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted((project_dir / "outputs").glob("l3_trusted_seed_evidence_reader_*.*"))
            }
            run_l3_trusted_seed_evidence_reader(**kwargs)
            second = {
                path.name: path.read_text(encoding="utf-8")
                for path in sorted((project_dir / "outputs").glob("l3_trusted_seed_evidence_reader_*.*"))
            }
            self.assertEqual(first, second)

    def test_static_safety_constraints(self) -> None:
        source = (PROJECT_ROOT / "scripts" / "l3_trusted_seed_evidence_reader.py").read_text(encoding="utf-8").lower()
        self.assertFalse(any(pattern in source for pattern in ("import openai", "from openai", "import chromadb", "from chromadb")))
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("insert into", source)
        self.assertNotIn("update ", source)


if __name__ == "__main__":
    unittest.main()
