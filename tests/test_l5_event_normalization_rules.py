import json
import re
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SEED_PATH = PROJECT_ROOT / "config" / "event_normalization_rules.seed.json"
DB_PATH = PROJECT_ROOT / "index" / "novel_story_bible.db"

REQUIRED_ROOT_KEYS = {
    "schema_version",
    "layer",
    "project",
    "status",
    "created_by",
    "description",
    "hard_constraints",
    "review_decision_enum",
    "event_type_catalog",
    "state_change_type_catalog",
    "argument_role_catalog",
    "trigger_category_mapping",
    "candidate_to_normalized_field_mapping",
    "confidence_rules",
    "importance_rules",
    "merge_key_rules",
    "reject_rules",
    "downgrade_rules",
    "warning_flag_catalog",
    "normalization_guardrails",
    "future_tables",
    "seed_checksum",
}

REQUIRED_HARD_CONSTRAINTS = {
    "no_llm",
    "no_embedding",
    "no_chroma",
    "no_source_table_mutation",
    "no_auto_confirm_event",
    "no_auto_merge_event",
    "candidate_is_not_truth",
}

REQUIRED_EVENT_TYPES = {
    "movement",
    "encounter",
    "dialogue",
    "conflict",
    "attack",
    "defense",
    "injury",
    "death",
    "resurrection_or_revival",
    "capture",
    "escape",
    "search",
    "discovery",
    "reveal",
    "decision",
    "promise_or_oath",
    "betrayal",
    "alliance",
    "separation",
    "reunion",
    "identity_change",
    "disguise_or_impersonation",
    "ability_use",
    "power_upgrade",
    "ritual",
    "performance",
    "organization_action",
    "mission_assignment",
    "investigation",
    "trial_or_judgment",
    "world_rule_change",
    "disaster_event",
    "invasion",
    "battle",
    "scene_transition",
    "internal_state",
    "object_transfer",
    "resource_change",
    "knowledge_change",
    "relationship_change",
    "status_change",
}

REQUIRED_STATE_CHANGE_TYPES = {
    "location_change",
    "identity_change",
    "disguise_state_change",
    "relationship_change",
    "injury_change",
    "death_change",
    "revival_change",
    "power_change",
    "power_level_change",
    "organization_membership_change",
    "authority_change",
    "knowledge_state_change",
    "emotional_state_change",
    "mental_state_change",
    "object_ownership_change",
    "resource_change",
    "mission_state_change",
    "captivity_change",
    "alliance_change",
    "hostility_change",
    "world_rule_change",
    "environment_change",
    "disaster_state_change",
    "scene_presence_change",
    "status_change",
}

REQUIRED_ROLES = {
    "subject",
    "object",
    "target",
    "agent",
    "patient",
    "speaker",
    "listener",
    "observer",
    "discoverer",
    "revealed_entity",
    "mover",
    "origin_location",
    "destination_location",
    "current_location",
    "time_hint",
    "organization",
    "faction",
    "power",
    "ability",
    "artifact",
    "weapon",
    "resource",
    "cause",
    "result",
    "method",
    "evidence",
    "scene",
    "chapter",
    "unknown",
}

REQUIRED_CONFIDENCE_RULES = {
    "conf_exact_trigger_with_evidence",
    "conf_has_subject_candidate",
    "conf_has_object_or_target_candidate",
    "conf_has_location_candidate",
    "conf_has_state_change",
    "conf_has_valid_evidence_backcut",
    "conf_duplicate_penalty",
    "conf_missing_evidence_penalty",
    "conf_missing_argument_penalty",
    "conf_weak_trigger_penalty",
    "conf_invalid_scene_block_penalty",
    "conf_review_accept_boost",
    "conf_review_reject_floor",
    "conf_needs_context_penalty",
}

REQUIRED_IMPORTANCE_RULES = {
    "imp_death_or_revival",
    "imp_power_upgrade",
    "imp_identity_change",
    "imp_world_rule_change",
    "imp_disaster_event",
    "imp_major_battle",
    "imp_relationship_change",
    "imp_organization_membership_change",
    "imp_reveal_core_information",
    "imp_scene_transition_minor",
    "imp_dialogue_normal",
    "imp_internal_state_minor",
}

REQUIRED_MERGE_RULES = {
    "merge_same_sentence_same_trigger",
    "merge_same_evidence_hash_same_trigger",
    "merge_same_scene_same_subject_same_type",
    "merge_state_change_into_event",
    "merge_forbidden_without_review",
}

REQUIRED_REJECT_RULES = {
    "reject_missing_evidence",
    "reject_invalid_evidence_offset",
    "reject_hash_mismatch",
    "reject_no_trigger_text",
    "reject_source_chapter_missing",
    "reject_review_rejected",
    "reject_non_event_description",
    "reject_duplicate_noise",
}

REQUIRED_DOWNGRADE_RULES = {
    "downgrade_missing_subject",
    "downgrade_missing_object_when_required",
    "downgrade_missing_location_for_movement",
    "downgrade_missing_scene_block",
    "downgrade_multiple_evidence_spans",
    "downgrade_optional_source_missing",
    "downgrade_low_confidence_trigger",
}

REQUIRED_WARNING_FLAGS = {
    "missing_evidence_span",
    "multiple_evidence_spans",
    "missing_trigger_text",
    "missing_argument",
    "missing_subject_candidate",
    "missing_object_candidate",
    "missing_location_candidate",
    "missing_scene_block",
    "invalid_scene_block_ref",
    "evidence_backcut_not_ok",
    "hash_mismatch",
    "weak_candidate",
    "duplicate_trigger_same_sentence",
    "duplicate_event_same_evidence",
    "state_change_without_event",
    "event_without_state_change",
    "optional_location_source_missing",
    "optional_scene_block_source_missing",
    "review_rejected",
    "review_needs_context",
    "review_uncertain",
    "normalization_not_allowed",
    "manual_review_required",
}

REQUIRED_GUARDRAILS = {
    "candidate_is_not_truth",
    "manual_review_before_confirmed_event",
    "no_auto_entity_resolution",
    "evidence_required",
    "source_table_read_only",
}

VALID_IMPORTANCE_LEVELS = {"minor", "normal", "major", "critical"}
VALID_MERGE_STRATEGIES = {"manual_review_required", "future_deterministic_candidate_grouping", "forbidden"}
VALID_REJECT_LEVELS = {"soft", "hard", "manual_only"}
VALID_DOWNGRADE_TARGETS = {"candidate", "weak_candidate", "needs_context", "uncertain"}
VALID_WARNING_SEVERITIES = {"info", "warning", "suspect", "error"}


def load_seed() -> dict:
    return json.loads(SEED_PATH.read_text(encoding="utf-8"))


class L5EventNormalizationRulesTests(unittest.TestCase):
    def test_seed_file_exists(self) -> None:
        self.assertTrue(SEED_PATH.exists())

    def test_required_root_keys(self) -> None:
        seed = load_seed()
        self.assertTrue(REQUIRED_ROOT_KEYS.issubset(seed))

    def test_required_hard_constraints(self) -> None:
        constraints = load_seed()["hard_constraints"]
        self.assertTrue(REQUIRED_HARD_CONSTRAINTS.issubset(constraints))
        self.assertTrue(all(constraints[key] is True for key in REQUIRED_HARD_CONSTRAINTS))

    def test_required_event_types(self) -> None:
        event_types = {item["event_type"] for item in load_seed()["event_type_catalog"]}
        self.assertTrue(REQUIRED_EVENT_TYPES.issubset(event_types))

    def test_event_type_uniqueness(self) -> None:
        event_types = [item["event_type"] for item in load_seed()["event_type_catalog"]]
        self.assertEqual(len(event_types), len(set(event_types)))

    def test_event_type_id_format(self) -> None:
        for item in load_seed()["event_type_catalog"]:
            self.assertRegex(item["event_type"], r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

    def test_required_state_change_types(self) -> None:
        state_types = {item["state_change_type"] for item in load_seed()["state_change_type_catalog"]}
        self.assertTrue(REQUIRED_STATE_CHANGE_TYPES.issubset(state_types))

    def test_required_argument_roles(self) -> None:
        roles = {item["role"] for item in load_seed()["argument_role_catalog"]}
        self.assertTrue(REQUIRED_ROLES.issubset(roles))

    def test_trigger_mapping_references_valid_event_types(self) -> None:
        seed = load_seed()
        event_types = {item["event_type"] for item in seed["event_type_catalog"]}
        for mapping in seed["trigger_category_mapping"]:
            self.assertIn(mapping["maps_to_event_type"], event_types)

    def test_confidence_rules(self) -> None:
        rules = load_seed()["confidence_rules"]
        rule_ids = {item["rule_id"] for item in rules}
        self.assertTrue(REQUIRED_CONFIDENCE_RULES.issubset(rule_ids))
        for rule in rules:
            self.assertIsInstance(rule["score_delta"], (int, float))
            self.assertGreaterEqual(rule["floor"], 0.0)
            self.assertLessEqual(rule["floor"], 1.0)
            self.assertGreaterEqual(rule["ceiling"], 0.0)
            self.assertLessEqual(rule["ceiling"], 1.0)
            self.assertLessEqual(rule["floor"], rule["ceiling"])

    def test_importance_rules(self) -> None:
        rules = load_seed()["importance_rules"]
        rule_ids = {item["rule_id"] for item in rules}
        self.assertTrue(REQUIRED_IMPORTANCE_RULES.issubset(rule_ids))
        for rule in rules:
            self.assertIn(rule["importance_level"], VALID_IMPORTANCE_LEVELS)

    def test_merge_rules(self) -> None:
        rules = load_seed()["merge_key_rules"]
        by_id = {item["rule_id"]: item for item in rules}
        self.assertTrue(REQUIRED_MERGE_RULES.issubset(by_id))
        for rule in rules:
            self.assertIn(rule["merge_strategy"], VALID_MERGE_STRATEGIES)
        self.assertEqual(by_id["merge_forbidden_without_review"]["merge_strategy"], "forbidden")

    def test_reject_rules(self) -> None:
        rules = load_seed()["reject_rules"]
        rule_ids = {item["rule_id"] for item in rules}
        self.assertTrue(REQUIRED_REJECT_RULES.issubset(rule_ids))
        for rule in rules:
            self.assertIn(rule["reject_level"], VALID_REJECT_LEVELS)

    def test_downgrade_rules(self) -> None:
        rules = load_seed()["downgrade_rules"]
        rule_ids = {item["rule_id"] for item in rules}
        self.assertTrue(REQUIRED_DOWNGRADE_RULES.issubset(rule_ids))
        for rule in rules:
            self.assertIn(rule["downgrade_to"], VALID_DOWNGRADE_TARGETS)

    def test_warning_flags(self) -> None:
        flags = load_seed()["warning_flag_catalog"]
        by_flag = {item["flag"]: item for item in flags}
        self.assertTrue(REQUIRED_WARNING_FLAGS.issubset(by_flag))
        self.assertEqual(len(flags), len(by_flag))
        for flag in flags:
            self.assertIn(flag["severity"], VALID_WARNING_SEVERITIES)

    def test_guardrails(self) -> None:
        guardrails = {item["guardrail_id"] for item in load_seed()["normalization_guardrails"]}
        self.assertTrue(REQUIRED_GUARDRAILS.issubset(guardrails))

    def test_future_tables_not_created(self) -> None:
        if not DB_PATH.exists():
            self.skipTest("database not present")
        future_tables = {
            "l5_normalized_event",
            "l5_normalized_state_change",
            "l5_event_merge_group",
            "confirmed_event",
        }
        conn = sqlite3.connect(DB_PATH)
        try:
            actual = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ({})".format(
                        ",".join("?" for _ in future_tables)
                    ),
                    tuple(sorted(future_tables)),
                )
            }
        finally:
            conn.close()
        self.assertEqual(actual, set())

    def test_verifier_pass_line(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "l5_verify_event_normalization_rules.py"),
                "--project-dir",
                str(PROJECT_ROOT),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip().splitlines()[-1], "L5.2 event normalization seed/rule FULL PASS")


if __name__ == "__main__":
    unittest.main()
