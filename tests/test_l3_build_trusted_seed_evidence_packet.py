import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.l3_build_trusted_seed_evidence_packet import run_l3_build_trusted_seed_evidence_packet


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def config_payload() -> dict:
    return {
        "version": "v1",
        "godways": [
            {
                "godway_id": "seed_a",
                "name": "Seed A",
                "category": "godway",
                "representative_characters": [{"name": "A"}],
            },
            {
                "godway_id": "seed_b",
                "name": "Seed B",
                "category": "godway",
                "representative_characters": [{"name": "B"}],
            },
            {
                "godway_id": "seed_c",
                "name": "Seed C",
                "category": "godway",
                "representative_characters": [{"name": "C"}],
            },
            {
                "godway_id": "seed_d",
                "name": "Seed D",
                "category": "godway",
                "representative_characters": [{"name": "D"}],
            },
        ]
    }


def candidate(candidate_id: str, chapter_num: int, human_status: str | None, review_note: str | None = None) -> dict:
    return {
        "human_status": human_status,
        "review_note": review_note,
        "candidate": {
            "candidate_id": candidate_id,
            "status": "candidate",
            "chapter_id": f"ch_{chapter_num:04d}",
            "chapter_num": chapter_num,
            "paragraph_hash": f"hash-{candidate_id}",
            "evidence_text": f"evidence {candidate_id}",
            "backcut": {"matched": True},
        },
    }


def template_payload(config_path: Path) -> dict:
    return {
        "meta": {"tool": "l3_apply_evidence_review", "version": "v1", "top_k_per_seed": 3},
        "items": [
            {
                "seed_file": str(config_path),
                "seed_item_id": "seed_a",
                "seed_item_path": "root.godways[0]",
                "seed_item_name": "Seed A",
                "review_candidates": [
                    candidate("a1", 1, "accepted", "ok"),
                    candidate("a2", 2, "rejected", "no"),
                    candidate("a3", 3, "needs_more", "later"),
                    candidate("a4", 4, "accepted", "trimmed"),
                ],
            },
            {
                "seed_file": str(config_path),
                "seed_item_id": "seed_b",
                "seed_item_path": "root.godways[1]",
                "seed_item_name": "Seed B",
                "review_candidates": [
                    candidate("b1", 1, None, None),
                    candidate("b2", 2, None, None),
                ],
            },
            {
                "seed_file": str(config_path),
                "seed_item_id": "seed_c",
                "seed_item_path": "root.godways[2]",
                "seed_item_name": "Seed C",
                "review_candidates": [
                    candidate("c1", 1, "accepted", "good"),
                    candidate("c2", 2, "accepted", "good"),
                    candidate("c3", 3, "accepted", "good"),
                    candidate("c4", 4, "accepted", "trimmed"),
                ],
            },
        ],
    }


def candidates_payload(config_path: Path) -> dict:
    return {
        "meta": {"tool": "l3_seed_evidence_finder", "version": "v1"},
        "items": [
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[0]",
                    "item_id": "seed_a",
                    "item_name": "Seed A",
                },
                "candidates": [],
            },
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[1]",
                    "item_id": "seed_b",
                    "item_name": "Seed B",
                },
                "candidates": [],
            },
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[2]",
                    "item_id": "seed_c",
                    "item_name": "Seed C",
                },
                "candidates": [],
            },
            {
                "seed_source": {
                    "seed_file": str(config_path),
                    "item_path": "root.godways[3]",
                    "item_id": "seed_d",
                    "item_name": "Seed D",
                },
                "candidates": [],
            },
        ],
    }


class L3BuildTrustedSeedEvidencePacketTests(unittest.TestCase):
    def test_builds_trusted_packet_with_status_filters_and_full_seed_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            config_path = project_dir / "config" / "godway_catalog.seed.json"
            template_path = project_dir / "outputs" / "l3_evidence_review_manual_template_top3.json"
            candidates_path = project_dir / "outputs" / "l3_seed_evidence_candidates.json"
            write_json(config_path, config_payload())
            write_json(template_path, template_payload(config_path))
            write_json(candidates_path, candidates_payload(config_path))

            packet = run_l3_build_trusted_seed_evidence_packet(project_dir=project_dir, review_template=template_path, candidates=candidates_path)

            self.assertEqual(packet["summary"]["seed_items_total"], 4)
            self.assertEqual(packet["summary"]["seed_items_with_accepted_evidence"], 2)
            self.assertEqual(packet["summary"]["seed_items_without_accepted_evidence"], 2)
            self.assertEqual(packet["summary"]["accepted_evidence_count"], 4)
            self.assertEqual(packet["summary"]["rejected_evidence_count"], 1)
            self.assertEqual(packet["summary"]["needs_more_evidence_count"], 1)
            self.assertEqual(packet["summary"]["pending_evidence_count"], 2)
            self.assertEqual(packet["summary"]["max_accepted_evidence_per_seed"], 3)
            self.assertTrue(packet["summary"]["all_seed_items_lte_3"])
            self.assertEqual([item["seed_item_id"] for item in packet["seed_rules"]], ["seed_a", "seed_b", "seed_c", "seed_d"])
            seed_a = packet["seed_rules"][0]
            self.assertEqual(seed_a["trusted_status"], "trusted")
            self.assertEqual([item["candidate_id"] for item in seed_a["trusted_evidence"]], ["a1"])
            seed_b = packet["seed_rules"][1]
            self.assertEqual(seed_b["trusted_status"], "pending")
            self.assertEqual(seed_b["trusted_evidence"], [])
            seed_c = packet["seed_rules"][2]
            self.assertEqual(seed_c["trusted_status"], "trusted")
            self.assertEqual(len(seed_c["trusted_evidence"]), 3)
            seed_d = packet["seed_rules"][3]
            self.assertEqual(seed_d["trusted_status"], "no_accepted_evidence")
            self.assertEqual(seed_d["trusted_evidence"], [])
            for relative in (
                "outputs/l3_seed_rule_trusted_evidence_packet.json",
                "outputs/l3_seed_rule_trusted_evidence_packet.md",
                "outputs/l3_seed_rule_trusted_evidence_summary.json",
            ):
                self.assertTrue((project_dir / relative).exists())

    def test_output_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            config_path = project_dir / "config" / "godway_catalog.seed.json"
            template_path = project_dir / "outputs" / "l3_evidence_review_manual_template_top3.json"
            candidates_path = project_dir / "outputs" / "l3_seed_evidence_candidates.json"
            write_json(config_path, config_payload())
            write_json(template_path, template_payload(config_path))
            write_json(candidates_path, candidates_payload(config_path))

            run_l3_build_trusted_seed_evidence_packet(project_dir=project_dir, review_template=template_path, candidates=candidates_path)
            first = {
                "json": (project_dir / "outputs" / "l3_seed_rule_trusted_evidence_packet.json").read_text(encoding="utf-8"),
                "md": (project_dir / "outputs" / "l3_seed_rule_trusted_evidence_packet.md").read_text(encoding="utf-8"),
                "summary": (project_dir / "outputs" / "l3_seed_rule_trusted_evidence_summary.json").read_text(encoding="utf-8"),
            }

            run_l3_build_trusted_seed_evidence_packet(project_dir=project_dir, review_template=template_path, candidates=candidates_path)
            second = {
                "json": (project_dir / "outputs" / "l3_seed_rule_trusted_evidence_packet.json").read_text(encoding="utf-8"),
                "md": (project_dir / "outputs" / "l3_seed_rule_trusted_evidence_packet.md").read_text(encoding="utf-8"),
                "summary": (project_dir / "outputs" / "l3_seed_rule_trusted_evidence_summary.json").read_text(encoding="utf-8"),
            }

            self.assertEqual(first, second)

    def test_static_safety_constraints(self) -> None:
        source = (PROJECT_ROOT / "scripts" / "l3_build_trusted_seed_evidence_packet.py").read_text(encoding="utf-8").lower()
        self.assertFalse(any(pattern in source for pattern in ("import openai", "import chromadb", "from openai", "from chromadb")))
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("insert into", source)
        self.assertNotIn("update ", source)


if __name__ == "__main__":
    unittest.main()
