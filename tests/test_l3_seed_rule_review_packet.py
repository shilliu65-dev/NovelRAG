import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_seed_rule_review_packet import (
    ALLOWED_HUMAN_STATUS,
    ALLOWED_RULE_STATUS,
    run_l3_seed_rule_review_packet,
)


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def config_payload() -> dict:
    return {
        "version": "v1",
        "godways": [
            {
                "godway_id": "godway_xi",
                "name": "戏神道",
                "category": "godway",
                "core_authority": "戏",
                "representative_characters": [{"name": "陈伶"}],
            },
            {
                "godway_id": "godway_huo",
                "name": "火神道",
                "category": "godway",
                "core_authority": "火",
                "representative_characters": [{"name": "红袖"}],
            },
        ],
        "special_rules": [
            {
                "rule_id": "special_1",
                "name": "陈伶例外",
                "rule_type": "special_rule",
                "description": "特殊规则",
            }
        ],
    }


def make_candidate(seed_item_id: str, paragraph_id: str, chapter_num: int, text: str) -> dict:
    return {
        "status": "candidate",
        "chapter_id": f"ch_{chapter_num:04d}",
        "version_id": f"ver_{chapter_num:04d}",
        "chapter_num": chapter_num,
        "chapter_title": f"Chapter {chapter_num}",
        "paragraph_id": paragraph_id,
        "paragraph_index": chapter_num,
        "char_start": chapter_num * 10,
        "char_end": chapter_num * 10 + len(text),
        "paragraph_hash": f"hash-{seed_item_id}-{paragraph_id}",
        "paragraph_text": text,
        "l1_backcut_check": {
            "checked": True,
            "matched": True,
            "expected_hash": f"hash-{seed_item_id}-{paragraph_id}",
            "actual_hash": f"hash-{seed_item_id}-{paragraph_id}",
            "cut_length": len(text),
        },
    }


def candidates_payload(config_path: Path) -> dict:
    return {
        "meta": {"tool": "l3_seed_evidence_finder", "version": "v1"},
        "items": [
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[0]",
                    "item_id": "godway_xi",
                    "item_name": "戏神道",
                },
                "candidates": [
                    make_candidate("godway_xi", "p1", 1, "证据一"),
                    make_candidate("godway_xi", "p2", 2, "证据二"),
                    make_candidate("godway_xi", "p3", 3, "证据三"),
                    make_candidate("godway_xi", "p4", 4, "证据四"),
                ],
            },
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[1]",
                    "item_id": "godway_huo",
                    "item_name": "火神道",
                },
                "candidates": [
                    make_candidate("godway_huo", "p5", 5, "火证据"),
                ],
            },
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.special_rules[0]",
                    "item_id": "special_1",
                    "item_name": "陈伶例外",
                },
                "candidates": [],
            },
        ],
    }


def review_queue_payload(config_path: Path) -> dict:
    return {
        "meta": {"tool": "l3_evidence_review_verifier", "version": "v1"},
        "summary": {"seed_items": 3},
        "review_items": [
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[0]",
                    "item_id": "godway_xi",
                    "item_name": "戏神道",
                },
                **make_candidate("godway_xi", "p1", 1, "证据一"),
                "human_status": "accepted",
                "human_note": "支持",
                "verifier_backcut_check": {
                    "checked": True,
                    "matched": True,
                    "expected_hash": "hash-godway_xi-p1",
                    "actual_hash": "hash-godway_xi-p1",
                    "cut_length": 3,
                },
            },
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[0]",
                    "item_id": "godway_xi",
                    "item_name": "戏神道",
                },
                **make_candidate("godway_xi", "p2", 2, "证据二"),
                "human_status": None,
                "human_note": None,
                "verifier_backcut_check": {
                    "checked": True,
                    "matched": True,
                    "expected_hash": "hash-godway_xi-p2",
                    "actual_hash": "hash-godway_xi-p2",
                    "cut_length": 3,
                },
            },
        ],
    }


class L3SeedRuleReviewPacketTests(unittest.TestCase):
    def test_builds_packet_with_top_three_and_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            config_path = project_dir / "config" / "godway_catalog.seed.json"
            write_json(config_path, config_payload())
            write_json(project_dir / "outputs" / "l3_seed_evidence_candidates.json", candidates_payload(config_path))
            write_json(project_dir / "outputs" / "l3_evidence_review_queue.json", review_queue_payload(config_path))

            packet = run_l3_seed_rule_review_packet(project_dir=project_dir)

            self.assertEqual(packet["summary"]["seed_items"], 3)
            self.assertEqual(packet["summary"]["review_evidence_count"], 4)
            self.assertEqual(packet["summary"]["max_evidence_per_seed"], 3)
            first = packet["items"][0]
            self.assertEqual(first["seed_item_id"], "godway_xi")
            self.assertEqual(first["seed_category"], "godway")
            self.assertIsNone(first["rule_status"])
            self.assertEqual(first["rule_note"], "")
            self.assertEqual(len(first["evidence_candidates"]), 3)
            self.assertEqual(
                [item["candidate_id"] for item in first["evidence_candidates"]],
                [
                    "godway_xi:p1:10:13",
                    "godway_xi:p2:20:23",
                    "godway_xi:p3:30:33",
                ],
            )
            self.assertEqual(first["evidence_candidates"][0]["human_status"], "accepted")
            self.assertEqual(first["evidence_candidates"][0]["human_note"], "支持")
            self.assertIsNone(first["evidence_candidates"][2]["human_status"])
            self.assertEqual(first["evidence_candidates"][2]["human_note"], "")
            self.assertEqual(packet["items"][2]["seed_category"], "special_rule")
            self.assertEqual(packet["items"][2]["evidence_candidates"], [])
            for relative in (
                "outputs/l3_seed_rule_review_packet.json",
                "outputs/l3_seed_rule_review_packet.md",
                "outputs/l3_seed_rule_review_summary.json",
            ):
                self.assertTrue((project_dir / relative).exists())

    def test_output_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            config_path = project_dir / "config" / "godway_catalog.seed.json"
            write_json(config_path, config_payload())
            write_json(project_dir / "outputs" / "l3_seed_evidence_candidates.json", candidates_payload(config_path))
            write_json(project_dir / "outputs" / "l3_evidence_review_queue.json", review_queue_payload(config_path))

            run_l3_seed_rule_review_packet(project_dir=project_dir)
            first = {
                "json": (project_dir / "outputs" / "l3_seed_rule_review_packet.json").read_text(encoding="utf-8"),
                "md": (project_dir / "outputs" / "l3_seed_rule_review_packet.md").read_text(encoding="utf-8"),
                "summary": (project_dir / "outputs" / "l3_seed_rule_review_summary.json").read_text(encoding="utf-8"),
            }

            run_l3_seed_rule_review_packet(project_dir=project_dir)
            second = {
                "json": (project_dir / "outputs" / "l3_seed_rule_review_packet.json").read_text(encoding="utf-8"),
                "md": (project_dir / "outputs" / "l3_seed_rule_review_packet.md").read_text(encoding="utf-8"),
                "summary": (project_dir / "outputs" / "l3_seed_rule_review_summary.json").read_text(encoding="utf-8"),
            }

            self.assertEqual(first, second)

    def test_static_safety_constraints(self) -> None:
        source = (PROJECT_ROOT / "scripts" / "l3_seed_rule_review_packet.py").read_text(encoding="utf-8").lower()
        self.assertFalse(any(pattern in source for pattern in ("import openai", "import chromadb", "from openai", "from chromadb")))
        self.assertEqual(ALLOWED_RULE_STATUS, [None, "confirmed", "revise", "needs_more", "unsupported", "conflict"])
        self.assertEqual(ALLOWED_HUMAN_STATUS, [None, "accepted", "rejected", "needs_more"])


if __name__ == "__main__":
    unittest.main()
