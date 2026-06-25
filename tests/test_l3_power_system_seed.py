import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts import l3_verify_power_system_seed as verifier


def branch_checksum(branches: list[object]) -> str:
    return verifier.sha256_text(verifier.canonical_json(branches))


def base_seed() -> dict[str, object]:
    xi_branches = ["生", "旦", "净"]
    bing_branches = ["审判", "修罗", "天狼", "止戈"]
    seed = {
        "power_realm.seed.json": {
            "version": "l3_power_realm_seed_v1.1.0",
            "source": "user_seed",
            "normal_realm_rule": {
                "realm_type": "normal_godway",
                "max_normal_rank": 9,
                "rank_rules": [
                    {"rank_start": 1, "rank_end": 3, "realm_name": "入门境", "status": "user_seed"},
                    {"rank_start": 4, "rank_end": 4, "realm_name": "领域觉醒", "status": "user_seed"},
                    {"rank_start": 5, "rank_end": 7, "realm_name": "高阶境", "status": "user_seed"},
                    {"rank_start": 8, "rank_end": 8, "realm_name": "半步半神", "status": "user_seed"},
                    {"rank_start": 9, "rank_end": 9, "realm_name": "半神境", "status": "user_seed"},
                ],
            },
            "capability_order_rules": [
                {
                    "capability_tag": "domain_expansion",
                    "first_allowed_rank": 5,
                    "allowed_in_normal_progression": True,
                    "severity": "warning",
                },
                {
                    "capability_tag": "disasterization",
                    "first_allowed_rank": 10,
                    "allowed_in_normal_progression": False,
                    "severity": "error",
                },
            ],
        },
        "godway_catalog.seed.json": {
            "version": "l3_godway_catalog_seed_v1.1.0",
            "source": "user_seed",
            "global_enums": {
                "godway_group_values": ["mainstream_14", "ancient_declined_4"],
                "godway_category_values": ["mainstream_14", "ancient_declined_4"],
            },
            "godways": [
                {
                    "godway_id": "godway_xi",
                    "name": "戏神道",
                    "group": "mainstream_14",
                    "category": "mainstream_14",
                    "status": "active",
                    "branches": xi_branches,
                    "branches_checksum": branch_checksum(xi_branches),
                    "representative_characters": [
                        {
                            "name": "陈伶",
                            "role_description": "戏神道特殊路线待证",
                            "exclusive": False,
                            "allow_cross_godway": True,
                            "allowed_cross_godway_ids": [],
                            "source_kind": "user_seed",
                        }
                    ],
                    "seed_status": "user_seed",
                },
                {
                    "godway_id": "godway_bing",
                    "name": "兵神道",
                    "group": "mainstream_14",
                    "category": "mainstream_14",
                    "status": "active",
                    "branches": bing_branches,
                    "branches_checksum": branch_checksum(bing_branches),
                    "representative_characters": [],
                    "seed_status": "user_seed",
                },
            ],
        },
        "godway_progression.seed.json": {
            "version": "l3_godway_progression_seed_v1.1.0",
            "source": "user_seed",
            "progressions": [
                {
                    "godway_id": "godway_xi",
                    "name": "戏神道",
                    "catalog_branches_checksum": branch_checksum(xi_branches),
                    "normal_progression_note": "普通戏神道进阶线，不并入特殊路线。",
                    "ranks": [
                        {
                            "rank": rank,
                            "ability_summary": f"普通戏神道第{rank}阶能力",
                            "description": f"普通戏神道第{rank}阶描述",
                            "domain": "剧场" if rank >= 4 else "",
                            "capability_tags": ["domain_expansion"] if rank == 5 else [],
                            "prerequisites": [],
                            "forbidden_tags": [],
                            "status": "user_seed",
                        }
                        for rank in range(1, 10)
                    ],
                }
            ],
        },
        "special_power_rules.seed.json": {
            "version": "l3_special_power_rules_seed_v1.1.0",
            "source": "user_seed",
            "base_godway_constraints": {
                "allowed_godway_categories": ["mainstream_14"],
                "allowed_status": ["active"],
                "disallowed_status": ["deprecated", "deprecating"],
            },
            "disasterization_rules": {
                "normal_progression_allowed": False,
                "belongs_to_rank_10": False,
                "trigger_type": "special_event",
                "trigger_conditions": ["特殊事件触发"],
            },
            "special_rules": [
                {
                    "rule_id": "chenling_twisted_xi_god_route",
                    "rule_type": "chenling_special_route",
                    "name": "陈伶专属扭曲戏神路线",
                    "related_character": "陈伶",
                    "base_godway_id": "godway_xi",
                    "forbidden_patterns": [
                        {
                            "term": "嘲灾",
                            "pattern": "嘲灾",
                            "severity": "error",
                            "allowed_context_patterns": ["不涉及嘲灾"],
                            "blocked_context_patterns": ["融合嘲灾"],
                            "description": "嘲灾融合只属于陈伶特殊路线",
                        }
                    ],
                    "should_not_merge_into_normal_progression": True,
                    "status": "user_seed",
                },
                {
                    "rule_id": "disasterization_special_layer",
                    "rule_type": "disasterization_rule",
                    "name": "灾厄化特殊层",
                    "forbidden_patterns": [
                        {
                            "term": "灾厄化",
                            "pattern": "灾厄化",
                            "severity": "error",
                            "allowed_context_patterns": ["不属于灾厄化"],
                            "blocked_context_patterns": ["进入灾厄化"],
                            "description": "灾厄化不得进入普通进阶线",
                        }
                    ],
                    "should_not_merge_into_normal_progression": True,
                    "status": "user_seed",
                },
            ],
        },
    }
    catalog = seed["godway_catalog.seed.json"]["godways"]
    for index in range(3, 15):
        catalog.append(
            {
                "godway_id": f"godway_main_{index:02d}",
                "name": f"主流测试神道{index}",
                "group": "mainstream_14",
                "category": "mainstream_14",
                "status": "active",
                "branches": [],
                "branches_checksum": branch_checksum([]),
                "representative_characters": [],
                "seed_status": "user_seed",
            }
        )
    for index in range(1, 5):
        catalog.append(
            {
                "godway_id": f"godway_ancient_{index:02d}",
                "name": f"古神道测试{index}",
                "group": "ancient_declined_4",
                "category": "ancient_declined_4",
                "status": "active",
                "branches": [],
                "branches_checksum": branch_checksum([]),
                "representative_characters": [],
                "seed_status": "user_seed",
            }
        )
    return seed


def write_seed(project_dir: Path, seed: dict[str, object]) -> Path:
    config_dir = project_dir / "config"
    config_dir.mkdir(parents=True)
    for file_name, payload in seed.items():
        (config_dir / file_name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return config_dir


def run_seed(seed: dict[str, object]) -> verifier.L3VerificationResult:
    with tempfile.TemporaryDirectory() as temp_dir:
        project_dir = Path(temp_dir)
        write_seed(project_dir, seed)
        return verifier.run_l3_verification(project_dir)


class L3PowerSystemSeedTests(unittest.TestCase):
    def test_valid_seed_passes_with_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            project_dir = Path(temp_dir)
            write_seed(project_dir, base_seed())
            result = verifier.run_l3_verification(project_dir)

            self.assertTrue(result.ok)
            self.assertEqual(result.error_count, 0)
            self.assertTrue(result.report_path.exists())

    def test_forbidden_term_in_allowed_context_only_warns(self) -> None:
        seed = base_seed()
        seed["godway_progression.seed.json"]["progressions"][0]["ranks"][1]["description"] = "说明：不涉及嘲灾。"

        result = run_seed(seed)

        self.assertTrue(result.ok)
        self.assertEqual(result.error_count, 0)
        self.assertGreaterEqual(result.warning_count, 1)
        self.assertTrue(any(item.code == "FORBIDDEN_ALLOWED_CONTEXT" for item in result.findings))

    def test_forbidden_term_in_blocked_context_errors(self) -> None:
        seed = base_seed()
        seed["godway_progression.seed.json"]["progressions"][0]["ranks"][1]["description"] = "普通阶段融合嘲灾。"

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertTrue(any(item.code == "FORBIDDEN_BLOCKED_CONTEXT" for item in result.findings))

    def test_exclusive_duplicate_character_errors(self) -> None:
        seed = base_seed()
        for godway in seed["godway_catalog.seed.json"]["godways"]:
            godway["representative_characters"] = [
                {
                    "name": "重复角色",
                    "role_description": f"{godway['name']}代表",
                    "exclusive": True,
                    "allow_cross_godway": False,
                    "allowed_cross_godway_ids": [],
                    "source_kind": "user_seed",
                }
            ]

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertTrue(any(item.code == "DUPLICATE_CHARACTER_EXCLUSIVE" for item in result.findings))

    def test_allow_cross_godway_duplicate_character_only_warns(self) -> None:
        seed = base_seed()
        for godway in seed["godway_catalog.seed.json"]["godways"]:
            godway["representative_characters"] = [
                {
                    "name": "跨界角色",
                    "role_description": f"{godway['name']}关联身份",
                    "exclusive": False,
                    "allow_cross_godway": True,
                    "allowed_cross_godway_ids": ["godway_xi", "godway_bing"],
                    "source_kind": "user_seed",
                }
            ]

        result = run_seed(seed)

        self.assertTrue(result.ok)
        self.assertTrue(any(item.code == "DUPLICATE_CHARACTER_ALLOWED" for item in result.findings))

    def test_empty_branches_allowed_but_missing_branches_errors(self) -> None:
        seed = base_seed()
        seed["godway_catalog.seed.json"]["godways"][1]["branches"] = []
        seed["godway_catalog.seed.json"]["godways"][1]["branches_checksum"] = branch_checksum([])
        del seed["godway_catalog.seed.json"]["godways"][0]["branches"]

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertTrue(any(item.code == "CATALOG_BRANCHES_MISSING" for item in result.findings))

    def test_branch_checksum_mismatch_errors(self) -> None:
        seed = base_seed()
        seed["godway_progression.seed.json"]["progressions"][0]["catalog_branches_checksum"] = "bad_checksum"

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertGreaterEqual(result.branch_checksum_error_count, 1)

    def test_deprecated_godway_reference_errors(self) -> None:
        seed = base_seed()
        seed["godway_catalog.seed.json"]["godways"][0]["status"] = "deprecated"

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertGreaterEqual(result.deprecated_reference_error_count, 1)

    def test_deprecating_without_replacement_errors(self) -> None:
        seed = base_seed()
        seed["godway_catalog.seed.json"]["godways"][0]["status"] = "deprecating"

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertTrue(any(item.code == "BASE_GODWAY_DEPRECATING_NO_REPLACEMENT" for item in result.findings))

    def test_disasterized_true_in_normal_progression_errors(self) -> None:
        seed = base_seed()
        seed["godway_progression.seed.json"]["progressions"][0]["ranks"][0]["disasterized"] = True

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertTrue(any(item.code == "NORMAL_PROGRESSION_DISASTERIZED" for item in result.findings))

    def test_rank_ten_in_normal_progression_errors(self) -> None:
        seed = base_seed()
        seed["godway_progression.seed.json"]["progressions"][0]["ranks"].append(
            {
                "rank": 10,
                "ability_summary": "十阶真神",
                "description": "十阶真神",
                "capability_tags": [],
                "prerequisites": [],
                "forbidden_tags": [],
                "status": "user_seed",
            }
        )

        result = run_seed(seed)

        self.assertFalse(result.ok)
        self.assertTrue(any(item.code == "PROGRESSION_RANK_OUT_OF_RANGE" for item in result.findings))

    def test_capability_tag_before_first_allowed_rank_uses_rule_severity(self) -> None:
        seed = base_seed()
        seed["godway_progression.seed.json"]["progressions"][0]["ranks"][2]["capability_tags"] = ["domain_expansion"]

        result = run_seed(seed)

        self.assertTrue(result.ok)
        self.assertTrue(any(item.code == "CAPABILITY_TAG_TOO_EARLY" and item.severity == "warning" for item in result.findings))

    def test_real_seed_passes(self) -> None:
        result = verifier.run_l3_verification(PROJECT_ROOT)

        self.assertTrue(result.ok)
        self.assertEqual(result.error_count, 0)


if __name__ == "__main__":
    unittest.main()
