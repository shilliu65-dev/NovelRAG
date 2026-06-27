from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PASS_MESSAGE = "L5.2 event normalization seed/rule FULL PASS"
FAIL_MESSAGE = "L5.2 event normalization seed/rule VERIFY FAIL"
SEED_RELATIVE_PATH = Path("config") / "event_normalization_rules.seed.json"
DB_RELATIVE_PATH = Path("index") / "novel_story_bible.db"
VERIFY_JSON_RELATIVE_PATH = Path("outputs") / "l5_event_normalization_rules_verify_report.json"
VERIFY_MD_RELATIVE_PATH = Path("outputs") / "l5_event_normalization_rules_verify_report.md"

SNAKE_RE = re.compile(r"^[a-z][a-z0-9]*(?:_[a-z0-9]+)*$")

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

REQUIRED_REVIEW_DECISIONS = {"", "accept", "reject", "merge", "split", "needs_context", "uncertain"}

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

REQUIRED_ARGUMENT_ROLES = {
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

ENTITY_KIND_ENUM = {
    "character",
    "location",
    "organization",
    "power",
    "artifact",
    "disaster",
    "world_rule",
    "time",
    "chapter",
    "scene",
    "unknown",
}

REQUIRED_TRIGGER_CATEGORIES = {
    "movement",
    "dialogue",
    "conflict",
    "attack",
    "death",
    "reveal",
    "decision",
    "state_change",
    "ability",
    "organization",
    "disaster",
    "location",
    "time",
    "identity",
    "relationship",
    "unknown",
}

REQUIRED_CANDIDATE_FIELD_MAPPING = {
    "event_candidate_id": "source_event_candidate_id",
    "event_candidate_hash": "source_event_candidate_hash",
    "chapter_id": "chapter_id",
    "chapter_num": "chapter_num",
    "chapter_title": "chapter_title",
    "version_id": "version_id",
    "scene_block_id": "scene_block_id",
    "trigger_text": "trigger_text",
    "trigger_rule_id": "trigger_rule_id",
    "trigger_category": "trigger_category",
    "event_type_candidate": "event_type",
    "event_subtype_candidate": "event_subtype",
    "subject_candidates_json": "subject_candidates",
    "object_candidates_json": "object_candidates",
    "location_candidates_json": "location_candidates",
    "time_hint_candidates_json": "time_hint_candidates",
    "organization_candidates_json": "organization_candidates",
    "power_candidates_json": "power_candidates",
    "evidence_text_backcut": "evidence_text",
    "evidence_backcut_hash": "evidence_hash",
    "confidence_score": "confidence_score",
    "importance_candidate": "importance_level",
    "warning_flags_json": "warning_flags",
    "review_status": "review_status",
    "review_decision": "review_decision",
    "review_note": "review_note",
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

FUTURE_TABLES = {"l5_normalized_event", "l5_normalized_state_change", "l5_event_merge_group"}
FORBIDDEN_CREATED_TABLES = FUTURE_TABLES | {"confirmed_event"}
VALID_IMPORTANCE_LEVELS = {"minor", "normal", "major", "critical"}
VALID_MERGE_STRATEGIES = {"manual_review_required", "future_deterministic_candidate_grouping", "forbidden"}
VALID_REJECT_LEVELS = {"soft", "hard", "manual_only"}
VALID_DOWNGRADE_TARGETS = {"candidate", "weak_candidate", "needs_context", "uncertain"}
VALID_WARNING_SEVERITIES = {"info", "warning", "suspect", "error"}
L5_CANDIDATE_TABLES = {
    "l5_event_candidate",
    "l5_event_argument_candidate",
    "l5_event_state_change_candidate",
    "l5_event_evidence_span",
}
EVENT_REVIEW_FILES = {
    "outputs/l5_event_candidate_review.csv",
    "outputs/l5_event_candidate_review.json",
}
STATE_REVIEW_FILES = {
    "outputs/l5_state_change_candidate_review.csv",
    "outputs/l5_state_change_candidate_review.json",
}


def resolve_project_paths(project_dir: Path | str | None) -> dict[str, Path]:
    root = Path(project_dir).resolve() if project_dir is not None else Path.cwd().resolve()
    outputs = root / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "seed": root / SEED_RELATIVE_PATH,
        "db": root / DB_RELATIVE_PATH,
        "json_report": root / VERIFY_JSON_RELATIVE_PATH,
        "md_report": root / VERIFY_MD_RELATIVE_PATH,
    }


def load_seed(seed_path: Path, errors: list[str]) -> dict[str, Any]:
    if not seed_path.exists():
        errors.append(f"missing_seed_file={seed_path}")
        return {}
    try:
        return json.loads(seed_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"invalid_seed_json={exc}")
    return {}


def add_missing(errors: list[str], label: str, required: set[str], actual: set[str]) -> None:
    missing = sorted(required.difference(actual))
    if missing:
        errors.append(f"{label}_missing={missing}")


def duplicate_values(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def require_snake(value: str, errors: list[str], label: str) -> None:
    if not SNAKE_RE.fullmatch(value):
        errors.append(f"{label}_not_lowercase_snake_case={value}")


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def compute_seed_checksum(seed: dict[str, Any]) -> str:
    seed_without_checksum = dict(seed)
    seed_without_checksum["seed_checksum"] = ""
    payload = json.dumps(seed_without_checksum, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_root_keys(seed: dict[str, Any], errors: list[str]) -> None:
    add_missing(errors, "root_keys", REQUIRED_ROOT_KEYS, set(seed))
    if seed.get("schema_version") != "1.0":
        errors.append(f"schema_version_invalid={seed.get('schema_version')!r}")
    if seed.get("layer") != "L5.2 Event Normalization Seed / Rule":
        errors.append(f"layer_invalid={seed.get('layer')!r}")


def validate_hard_constraints(seed: dict[str, Any], errors: list[str]) -> None:
    constraints = seed.get("hard_constraints")
    if not isinstance(constraints, dict):
        errors.append("hard_constraints_not_object")
        return
    add_missing(errors, "hard_constraints", REQUIRED_HARD_CONSTRAINTS, set(constraints))
    for key in sorted(REQUIRED_HARD_CONSTRAINTS):
        if constraints.get(key) is not True:
            errors.append(f"hard_constraint_not_true={key}")


def validate_review_decisions(seed: dict[str, Any], errors: list[str]) -> None:
    decisions = [str(item.get("decision", "")) for item in as_list(seed.get("review_decision_enum")) if isinstance(item, dict)]
    add_missing(errors, "review_decision_enum", REQUIRED_REVIEW_DECISIONS, set(decisions))
    duplicates = duplicate_values(decisions)
    if duplicates:
        errors.append(f"duplicate_review_decisions={duplicates}")


def validate_event_types(seed: dict[str, Any], errors: list[str]) -> tuple[set[str], dict[str, set[str]]]:
    catalog = as_list(seed.get("event_type_catalog"))
    event_types = [str(item.get("event_type", "")) for item in catalog if isinstance(item, dict)]
    event_type_set = set(event_types)
    add_missing(errors, "event_types", REQUIRED_EVENT_TYPES, event_type_set)
    duplicates = duplicate_values(event_types)
    if duplicates:
        errors.append(f"duplicate_event_types={duplicates}")
    subtype_by_event: dict[str, set[str]] = {}
    required_item_keys = {
        "event_type",
        "label_zh",
        "description",
        "allowed_subtypes",
        "default_argument_roles",
        "state_change_types_allowed",
        "is_major_event_candidate",
        "normalization_notes",
    }
    for item in catalog:
        if not isinstance(item, dict):
            errors.append("event_type_catalog_item_not_object")
            continue
        missing = sorted(required_item_keys.difference(item))
        event_type = str(item.get("event_type", ""))
        if missing:
            errors.append(f"event_type_item_missing_keys={event_type}:{missing}")
        require_snake(event_type, errors, "event_type")
        subtypes = [str(value) for value in as_list(item.get("allowed_subtypes"))]
        duplicate_subtypes = duplicate_values(subtypes)
        if duplicate_subtypes:
            errors.append(f"duplicate_subtypes={event_type}:{duplicate_subtypes}")
        for subtype in subtypes:
            require_snake(subtype, errors, f"subtype_for_{event_type}")
        subtype_by_event[event_type] = set(subtypes)
    return event_type_set, subtype_by_event


def validate_state_change_types(seed: dict[str, Any], event_types: set[str], errors: list[str]) -> set[str]:
    catalog = as_list(seed.get("state_change_type_catalog"))
    state_types = [str(item.get("state_change_type", "")) for item in catalog if isinstance(item, dict)]
    state_type_set = set(state_types)
    add_missing(errors, "state_change_types", REQUIRED_STATE_CHANGE_TYPES, state_type_set)
    duplicates = duplicate_values(state_types)
    if duplicates:
        errors.append(f"duplicate_state_change_types={duplicates}")
    required_item_keys = {
        "state_change_type",
        "label_zh",
        "description",
        "allowed_event_types",
        "default_before_field",
        "default_after_field",
        "normalization_notes",
    }
    for item in catalog:
        if not isinstance(item, dict):
            errors.append("state_change_type_catalog_item_not_object")
            continue
        state_type = str(item.get("state_change_type", ""))
        missing = sorted(required_item_keys.difference(item))
        if missing:
            errors.append(f"state_change_type_item_missing_keys={state_type}:{missing}")
        require_snake(state_type, errors, "state_change_type")
        invalid_events = sorted(set(str(value) for value in as_list(item.get("allowed_event_types"))).difference(event_types))
        if invalid_events:
            errors.append(f"state_change_type_invalid_event_refs={state_type}:{invalid_events}")
    return state_type_set


def validate_argument_roles(seed: dict[str, Any], event_types: set[str], errors: list[str]) -> set[str]:
    declared_entity_kinds = set(str(value) for value in as_list(seed.get("entity_kind_enum")))
    if declared_entity_kinds != ENTITY_KIND_ENUM:
        errors.append(f"entity_kind_enum_invalid={sorted(declared_entity_kinds)}")
    catalog = as_list(seed.get("argument_role_catalog"))
    roles = [str(item.get("role", "")) for item in catalog if isinstance(item, dict)]
    role_set = set(roles)
    add_missing(errors, "argument_roles", REQUIRED_ARGUMENT_ROLES, role_set)
    duplicates = duplicate_values(roles)
    if duplicates:
        errors.append(f"duplicate_argument_roles={duplicates}")
    required_item_keys = {
        "role",
        "label_zh",
        "description",
        "allowed_entity_kinds",
        "required_for_event_types",
        "optional_for_event_types",
        "normalization_notes",
    }
    for item in catalog:
        if not isinstance(item, dict):
            errors.append("argument_role_catalog_item_not_object")
            continue
        role = str(item.get("role", ""))
        missing = sorted(required_item_keys.difference(item))
        if missing:
            errors.append(f"argument_role_item_missing_keys={role}:{missing}")
        require_snake(role, errors, "argument_role")
        invalid_kinds = sorted(set(str(value) for value in as_list(item.get("allowed_entity_kinds"))).difference(ENTITY_KIND_ENUM))
        if invalid_kinds:
            errors.append(f"argument_role_invalid_entity_kinds={role}:{invalid_kinds}")
        for ref_key in ("required_for_event_types", "optional_for_event_types"):
            invalid_events = sorted(set(str(value) for value in as_list(item.get(ref_key))).difference(event_types))
            if invalid_events:
                errors.append(f"argument_role_invalid_{ref_key}={role}:{invalid_events}")
    return role_set


def validate_event_role_and_state_refs(seed: dict[str, Any], roles: set[str], state_types: set[str], errors: list[str]) -> None:
    for item in as_list(seed.get("event_type_catalog")):
        if not isinstance(item, dict):
            continue
        event_type = str(item.get("event_type", ""))
        invalid_roles = sorted(set(str(value) for value in as_list(item.get("default_argument_roles"))).difference(roles))
        if invalid_roles:
            errors.append(f"event_type_invalid_default_roles={event_type}:{invalid_roles}")
        invalid_states = sorted(set(str(value) for value in as_list(item.get("state_change_types_allowed"))).difference(state_types))
        if invalid_states:
            errors.append(f"event_type_invalid_state_refs={event_type}:{invalid_states}")


def validate_trigger_mappings(seed: dict[str, Any], event_types: set[str], subtype_by_event: dict[str, set[str]], errors: list[str]) -> set[str]:
    mappings = as_list(seed.get("trigger_category_mapping"))
    categories = [str(item.get("trigger_category", "")) for item in mappings if isinstance(item, dict)]
    category_set = set(categories)
    add_missing(errors, "trigger_categories", REQUIRED_TRIGGER_CATEGORIES, category_set)
    duplicates = duplicate_values(categories)
    if duplicates:
        errors.append(f"duplicate_trigger_categories={duplicates}")
    for item in mappings:
        if not isinstance(item, dict):
            errors.append("trigger_category_mapping_item_not_object")
            continue
        category = str(item.get("trigger_category", ""))
        event_type = str(item.get("maps_to_event_type", ""))
        default_subtype = str(item.get("default_subtype", ""))
        require_snake(category, errors, "trigger_category")
        if event_type not in event_types:
            errors.append(f"trigger_category_invalid_event_ref={category}:{event_type}")
        if default_subtype and default_subtype not in subtype_by_event.get(event_type, set()):
            errors.append(f"trigger_category_invalid_default_subtype={category}:{event_type}:{default_subtype}")
        if not isinstance(item.get("confidence_delta"), (int, float)):
            errors.append(f"trigger_category_confidence_delta_not_numeric={category}")
    unknown = next((item for item in mappings if isinstance(item, dict) and item.get("trigger_category") == "unknown"), None)
    if not unknown or unknown.get("maps_to_event_type") not in {"status_change", "internal_state", "knowledge_change"}:
        errors.append("unknown_trigger_category_missing_safe_mapping")
    return category_set


def validate_candidate_event_type_mappings(seed: dict[str, Any], event_types: set[str], state_types: set[str], subtype_by_event: dict[str, set[str]], errors: list[str]) -> tuple[set[str], set[str]]:
    event_candidates: set[str] = set()
    for item in as_list(seed.get("l5_event_type_candidate_mapping")):
        if not isinstance(item, dict):
            errors.append("l5_event_type_candidate_mapping_item_not_object")
            continue
        candidate = str(item.get("event_type_candidate", ""))
        event_candidates.add(candidate)
        mapped = str(item.get("maps_to_event_type", ""))
        subtype = str(item.get("default_subtype", ""))
        require_snake(candidate, errors, "event_type_candidate")
        if mapped not in event_types:
            errors.append(f"event_type_candidate_invalid_event_ref={candidate}:{mapped}")
        if subtype and subtype not in subtype_by_event.get(mapped, set()):
            errors.append(f"event_type_candidate_invalid_subtype={candidate}:{mapped}:{subtype}")
    state_candidates: set[str] = set()
    for item in as_list(seed.get("l5_state_change_type_candidate_mapping")):
        if not isinstance(item, dict):
            errors.append("l5_state_change_type_candidate_mapping_item_not_object")
            continue
        candidate = str(item.get("state_change_type_candidate", ""))
        state_candidates.add(candidate)
        mapped = str(item.get("maps_to_state_change_type", ""))
        require_snake(candidate, errors, "state_change_type_candidate")
        if mapped not in state_types:
            errors.append(f"state_change_type_candidate_invalid_ref={candidate}:{mapped}")
    return event_candidates, state_candidates


def validate_candidate_field_mapping(seed: dict[str, Any], errors: list[str]) -> None:
    mapping = seed.get("candidate_to_normalized_field_mapping")
    if not isinstance(mapping, dict):
        errors.append("candidate_to_normalized_field_mapping_not_object")
        return
    for source, target in sorted(REQUIRED_CANDIDATE_FIELD_MAPPING.items()):
        if mapping.get(source) != target:
            errors.append(f"candidate_field_mapping_invalid={source}:{mapping.get(source)!r}:{target!r}")


def validate_confidence_rules(seed: dict[str, Any], errors: list[str]) -> None:
    rules = as_list(seed.get("confidence_rules"))
    rule_ids = [str(item.get("rule_id", "")) for item in rules if isinstance(item, dict)]
    rule_set = set(rule_ids)
    add_missing(errors, "confidence_rules", REQUIRED_CONFIDENCE_RULES, rule_set)
    duplicates = duplicate_values(rule_ids)
    if duplicates:
        errors.append(f"duplicate_confidence_rules={duplicates}")
    for item in rules:
        if not isinstance(item, dict):
            errors.append("confidence_rule_item_not_object")
            continue
        rule_id = str(item.get("rule_id", ""))
        require_snake(rule_id, errors, "confidence_rule_id")
        delta = item.get("score_delta")
        floor = item.get("floor")
        ceiling = item.get("ceiling")
        if not isinstance(delta, (int, float)):
            errors.append(f"confidence_rule_score_delta_not_numeric={rule_id}")
        if not isinstance(floor, (int, float)) or not isinstance(ceiling, (int, float)):
            errors.append(f"confidence_rule_bounds_not_numeric={rule_id}")
            continue
        if not (0.0 <= float(floor) <= float(ceiling) <= 1.0):
            errors.append(f"confidence_rule_bounds_invalid={rule_id}:{floor}:{ceiling}")
        if ("penalty" in rule_id or rule_id == "conf_review_reject_floor") and isinstance(delta, (int, float)) and delta >= 0:
            errors.append(f"confidence_penalty_delta_not_negative={rule_id}:{delta}")
        if "boost" in rule_id and isinstance(delta, (int, float)) and delta <= 0:
            errors.append(f"confidence_boost_delta_not_positive={rule_id}:{delta}")
    reject_rule = next((item for item in rules if isinstance(item, dict) and item.get("rule_id") == "conf_review_reject_floor"), None)
    if not reject_rule or float(reject_rule.get("ceiling", 1.0)) > 0.1:
        errors.append("conf_review_reject_floor_missing_low_ceiling")


def validate_importance_rules(seed: dict[str, Any], event_types: set[str], errors: list[str]) -> None:
    rules = as_list(seed.get("importance_rules"))
    rule_ids = [str(item.get("rule_id", "")) for item in rules if isinstance(item, dict)]
    add_missing(errors, "importance_rules", REQUIRED_IMPORTANCE_RULES, set(rule_ids))
    duplicates = duplicate_values(rule_ids)
    if duplicates:
        errors.append(f"duplicate_importance_rules={duplicates}")
    for item in rules:
        if not isinstance(item, dict):
            errors.append("importance_rule_item_not_object")
            continue
        rule_id = str(item.get("rule_id", ""))
        require_snake(rule_id, errors, "importance_rule_id")
        if item.get("importance_level") not in VALID_IMPORTANCE_LEVELS:
            errors.append(f"importance_level_invalid={rule_id}:{item.get('importance_level')}")
        invalid_events = sorted(set(str(value) for value in as_list(item.get("applies_to_event_types"))).difference(event_types))
        if invalid_events:
            errors.append(f"importance_rule_invalid_event_refs={rule_id}:{invalid_events}")


def validate_merge_rules(seed: dict[str, Any], errors: list[str]) -> None:
    rules = as_list(seed.get("merge_key_rules"))
    rule_ids = [str(item.get("rule_id", "")) for item in rules if isinstance(item, dict)]
    by_id = {str(item.get("rule_id", "")): item for item in rules if isinstance(item, dict)}
    add_missing(errors, "merge_rules", REQUIRED_MERGE_RULES, set(rule_ids))
    duplicates = duplicate_values(rule_ids)
    if duplicates:
        errors.append(f"duplicate_merge_rules={duplicates}")
    for item in rules:
        if not isinstance(item, dict):
            errors.append("merge_rule_item_not_object")
            continue
        rule_id = str(item.get("rule_id", ""))
        require_snake(rule_id, errors, "merge_rule_id")
        if item.get("merge_strategy") not in VALID_MERGE_STRATEGIES:
            errors.append(f"merge_strategy_invalid={rule_id}:{item.get('merge_strategy')}")
    forbidden = by_id.get("merge_forbidden_without_review")
    if not forbidden or forbidden.get("merge_strategy") != "forbidden":
        errors.append("merge_forbidden_without_review_not_forbidden")


def validate_reject_rules(seed: dict[str, Any], errors: list[str]) -> None:
    rules = as_list(seed.get("reject_rules"))
    rule_ids = [str(item.get("rule_id", "")) for item in rules if isinstance(item, dict)]
    add_missing(errors, "reject_rules", REQUIRED_REJECT_RULES, set(rule_ids))
    duplicates = duplicate_values(rule_ids)
    if duplicates:
        errors.append(f"duplicate_reject_rules={duplicates}")
    for item in rules:
        if not isinstance(item, dict):
            errors.append("reject_rule_item_not_object")
            continue
        rule_id = str(item.get("rule_id", ""))
        require_snake(rule_id, errors, "reject_rule_id")
        if item.get("reject_level") not in VALID_REJECT_LEVELS:
            errors.append(f"reject_level_invalid={rule_id}:{item.get('reject_level')}")


def validate_downgrade_rules(seed: dict[str, Any], errors: list[str]) -> None:
    rules = as_list(seed.get("downgrade_rules"))
    rule_ids = [str(item.get("rule_id", "")) for item in rules if isinstance(item, dict)]
    add_missing(errors, "downgrade_rules", REQUIRED_DOWNGRADE_RULES, set(rule_ids))
    duplicates = duplicate_values(rule_ids)
    if duplicates:
        errors.append(f"duplicate_downgrade_rules={duplicates}")
    for item in rules:
        if not isinstance(item, dict):
            errors.append("downgrade_rule_item_not_object")
            continue
        rule_id = str(item.get("rule_id", ""))
        require_snake(rule_id, errors, "downgrade_rule_id")
        if item.get("downgrade_to") not in VALID_DOWNGRADE_TARGETS:
            errors.append(f"downgrade_target_invalid={rule_id}:{item.get('downgrade_to')}")


def validate_warning_flags(seed: dict[str, Any], errors: list[str]) -> set[str]:
    flags = as_list(seed.get("warning_flag_catalog"))
    flag_names = [str(item.get("flag", "")) for item in flags if isinstance(item, dict)]
    flag_set = set(flag_names)
    add_missing(errors, "warning_flags", REQUIRED_WARNING_FLAGS, flag_set)
    duplicates = duplicate_values(flag_names)
    if duplicates:
        errors.append(f"duplicate_warning_flags={duplicates}")
    for item in flags:
        if not isinstance(item, dict):
            errors.append("warning_flag_item_not_object")
            continue
        flag = str(item.get("flag", ""))
        require_snake(flag, errors, "warning_flag")
        if item.get("severity") not in VALID_WARNING_SEVERITIES:
            errors.append(f"warning_flag_severity_invalid={flag}:{item.get('severity')}")
    return flag_set


def validate_guardrails(seed: dict[str, Any], errors: list[str]) -> None:
    guardrails = {str(item.get("guardrail_id", "")) for item in as_list(seed.get("normalization_guardrails")) if isinstance(item, dict)}
    add_missing(errors, "guardrails", REQUIRED_GUARDRAILS, guardrails)


def validate_future_tables(seed: dict[str, Any], errors: list[str]) -> None:
    tables = as_list(seed.get("future_tables"))
    by_name = {str(item.get("table_name", "")): item for item in tables if isinstance(item, dict)}
    add_missing(errors, "future_tables", FUTURE_TABLES, set(by_name))
    for table_name, item in by_name.items():
        if table_name in FUTURE_TABLES and item.get("status") != "future_not_created_in_l5_2":
            errors.append(f"future_table_status_invalid={table_name}:{item.get('status')}")


def object_exists(conn: sqlite3.Connection, name: str, object_type: str | None = None) -> bool:
    if object_type is None:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ? LIMIT 1", (name,)).fetchone()
    else:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type = ? AND name = ? LIMIT 1", (object_type, name)).fetchone()
    return row is not None


def validate_future_tables_not_created(db_path: Path, errors: list[str], warnings: list[str]) -> None:
    if not db_path.exists():
        warnings.append(f"sqlite_db_missing_for_future_table_check={db_path}")
        return
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        placeholders = ",".join("?" for _ in FORBIDDEN_CREATED_TABLES)
        rows = conn.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
            tuple(sorted(FORBIDDEN_CREATED_TABLES)),
        ).fetchall()
    finally:
        conn.close()
    found = sorted(str(row[0]) for row in rows)
    if found:
        errors.append(f"future_or_confirmed_tables_created={found}")


def validate_checksum(seed: dict[str, Any], errors: list[str], warnings: list[str]) -> str:
    expected = compute_seed_checksum(seed)
    actual = str(seed.get("seed_checksum", ""))
    if not actual:
        warnings.append(f"seed_checksum_empty_expected={expected}")
    elif actual != expected:
        errors.append(f"seed_checksum_mismatch=actual:{actual}:expected:{expected}")
    return expected


def table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})")}


def fetch_distinct(conn: sqlite3.Connection, table_name: str, column: str) -> set[str]:
    return {str(row[0]) for row in conn.execute(f"SELECT DISTINCT {column} FROM {table_name} WHERE {column} IS NOT NULL AND {column} != ''")}


def add_coverage_issue(message: str, errors: list[str], warnings: list[str], strict: bool) -> None:
    if strict:
        errors.append(message)
    else:
        warnings.append(message)


def validate_l5_candidate_coverage(
    db_path: Path,
    trigger_categories: set[str],
    event_candidate_types: set[str],
    state_candidate_types: set[str],
    warning_flags: set[str],
    errors: list[str],
    warnings: list[str],
    strict: bool,
) -> dict[str, Any]:
    coverage: dict[str, Any] = {"checked": True, "db_path": str(db_path)}
    if not db_path.exists():
        add_coverage_issue(f"l5_candidate_db_missing={db_path}", errors, warnings, strict)
        coverage["status"] = "missing_db"
        return coverage
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        existing_tables = {table for table in L5_CANDIDATE_TABLES if object_exists(conn, table, "table")}
        missing_tables = sorted(L5_CANDIDATE_TABLES.difference(existing_tables))
        if missing_tables:
            add_coverage_issue(f"l5_candidate_tables_missing={missing_tables}", errors, warnings, strict)
        coverage["existing_tables"] = sorted(existing_tables)
        coverage["missing_tables"] = missing_tables
        if object_exists(conn, "l5_event_candidate", "table"):
            columns = table_columns(conn, "l5_event_candidate")
            required_columns = {"event_candidate_id", "event_family", "event_type", "trigger_text", "evidence_text", "evidence_hash", "chapter_id", "version_id"}
            missing_columns = sorted(required_columns.difference(columns))
            if missing_columns:
                add_coverage_issue(f"l5_event_candidate_missing_fields={missing_columns}", errors, warnings, strict)
            unknown_categories = sorted(fetch_distinct(conn, "l5_event_candidate", "event_family").difference(trigger_categories))
            if unknown_categories:
                add_coverage_issue(f"unknown_l5_trigger_categories={unknown_categories}", errors, warnings, strict)
            known_event_types = event_candidate_types | REQUIRED_EVENT_TYPES
            unknown_events = sorted(fetch_distinct(conn, "l5_event_candidate", "event_type").difference(known_event_types))
            if unknown_events:
                add_coverage_issue(f"unmapped_l5_event_type_candidates={unknown_events}", errors, warnings, strict)
            coverage["unknown_trigger_categories"] = unknown_categories
            coverage["unmapped_event_type_candidates"] = unknown_events
        if object_exists(conn, "l5_event_state_change_candidate", "table"):
            columns = table_columns(conn, "l5_event_state_change_candidate")
            required_columns = {"state_change_candidate_id", "event_candidate_id", "state_type", "from_state", "to_state", "evidence_text", "evidence_hash"}
            missing_columns = sorted(required_columns.difference(columns))
            if missing_columns:
                add_coverage_issue(f"l5_event_state_change_candidate_missing_fields={missing_columns}", errors, warnings, strict)
            known_states = state_candidate_types | REQUIRED_STATE_CHANGE_TYPES
            unknown_states = sorted(fetch_distinct(conn, "l5_event_state_change_candidate", "state_type").difference(known_states))
            if unknown_states:
                add_coverage_issue(f"unmapped_l5_state_change_type_candidates={unknown_states}", errors, warnings, strict)
            coverage["unmapped_state_change_type_candidates"] = unknown_states
        if object_exists(conn, "l5_event_candidate", "table") and "warning_flags_json" in table_columns(conn, "l5_event_candidate"):
            unknown_warning_flags: set[str] = set()
            for row in conn.execute("SELECT warning_flags_json FROM l5_event_candidate WHERE warning_flags_json IS NOT NULL AND warning_flags_json != ''"):
                try:
                    flags = json.loads(str(row[0]))
                except json.JSONDecodeError:
                    add_coverage_issue("l5_event_candidate_warning_flags_json_invalid", errors, warnings, strict)
                    continue
                unknown_warning_flags.update(str(flag) for flag in as_list(flags) if str(flag) not in warning_flags)
            if unknown_warning_flags:
                add_coverage_issue(f"unknown_l5_warning_flags={sorted(unknown_warning_flags)}", errors, warnings, strict)
            coverage["unknown_warning_flags"] = sorted(unknown_warning_flags)
    finally:
        conn.close()
    coverage.setdefault("status", "checked")
    return coverage


def load_review_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get("rows", [])
    return rows if isinstance(rows, list) else []


def inspect_review_rows(
    path: Path,
    rows: list[dict[str, Any]],
    trigger_categories: set[str],
    event_candidate_types: set[str],
    state_candidate_types: set[str],
    warning_flags: set[str],
    errors: list[str],
    warnings: list[str],
    strict: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {"path": str(path), "row_count": len(rows)}
    if not rows:
        return result
    columns = set(rows[0])
    if "event_candidate_review" in path.name:
        required = {"trigger_category", "event_type_candidate", "warning_flags_json"}
        missing = sorted(required.difference(columns))
        if missing:
            add_coverage_issue(f"review_event_export_missing_fields={path.name}:{missing}", errors, warnings, strict)
        unknown_categories = sorted({str(row.get("trigger_category", "")) for row in rows if row.get("trigger_category")}.difference(trigger_categories))
        unknown_event_types = sorted({str(row.get("event_type_candidate", "")) for row in rows if row.get("event_type_candidate")}.difference(event_candidate_types | REQUIRED_EVENT_TYPES))
        if unknown_categories:
            add_coverage_issue(f"review_export_unknown_trigger_categories={path.name}:{unknown_categories}", errors, warnings, strict)
        if unknown_event_types:
            add_coverage_issue(f"review_export_unmapped_event_type_candidates={path.name}:{unknown_event_types}", errors, warnings, strict)
        result["unknown_trigger_categories"] = unknown_categories
        result["unmapped_event_type_candidates"] = unknown_event_types
    if "state_change_candidate_review" in path.name:
        required = {"state_change_type_candidate", "warning_flags_json"}
        missing = sorted(required.difference(columns))
        if missing:
            add_coverage_issue(f"review_state_export_missing_fields={path.name}:{missing}", errors, warnings, strict)
        unknown_state_types = sorted({str(row.get("state_change_type_candidate", "")) for row in rows if row.get("state_change_type_candidate")}.difference(state_candidate_types | REQUIRED_STATE_CHANGE_TYPES))
        if unknown_state_types:
            add_coverage_issue(f"review_export_unmapped_state_change_type_candidates={path.name}:{unknown_state_types}", errors, warnings, strict)
        result["unmapped_state_change_type_candidates"] = unknown_state_types
    unknown_flags: set[str] = set()
    invalid_flag_rows = 0
    for row in rows:
        value = row.get("warning_flags_json")
        if value in (None, ""):
            continue
        try:
            flags = json.loads(str(value))
        except json.JSONDecodeError:
            invalid_flag_rows += 1
            continue
        unknown_flags.update(str(flag) for flag in as_list(flags) if str(flag) not in warning_flags)
    if invalid_flag_rows:
        add_coverage_issue(f"review_export_invalid_warning_flags_json={path.name}:{invalid_flag_rows}", errors, warnings, strict)
    if unknown_flags:
        add_coverage_issue(f"review_export_unknown_warning_flags={path.name}:{sorted(unknown_flags)}", errors, warnings, strict)
    result["unknown_warning_flags"] = sorted(unknown_flags)
    result["invalid_warning_flag_rows"] = invalid_flag_rows
    return result


def validate_l5_review_export_coverage(
    root: Path,
    trigger_categories: set[str],
    event_candidate_types: set[str],
    state_candidate_types: set[str],
    warning_flags: set[str],
    errors: list[str],
    warnings: list[str],
    strict: bool,
) -> dict[str, Any]:
    coverage: dict[str, Any] = {"checked": True, "files": []}
    review_paths = [root / relative for relative in sorted(EVENT_REVIEW_FILES | STATE_REVIEW_FILES)]
    existing = [path for path in review_paths if path.exists()]
    if not existing:
        add_coverage_issue("l5_review_export_files_missing", errors, warnings, strict)
        coverage["status"] = "missing_files"
        return coverage
    for path in existing:
        try:
            rows = load_review_rows(path)
        except (json.JSONDecodeError, OSError) as exc:
            add_coverage_issue(f"review_export_unreadable={path}:{exc}", errors, warnings, strict)
            continue
        coverage["files"].append(
            inspect_review_rows(path, rows, trigger_categories, event_candidate_types, state_candidate_types, warning_flags, errors, warnings, strict)
        )
    coverage["status"] = "checked"
    return coverage


def write_verify_reports(result: dict[str, Any], json_report_path: Path, md_report_path: Path) -> None:
    json_report_path.parent.mkdir(parents=True, exist_ok=True)
    json_report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# L5.2 Event Normalization Seed / Rule Verification Report",
        "",
        f"- created_at: {result['created_at']}",
        f"- ok: {result['ok']}",
        f"- final_message: {result['final_message']}",
        f"- expected_seed_checksum: {result['expected_seed_checksum']}",
        f"- error_count: {len(result['errors'])}",
        f"- warning_count: {len(result['warnings'])}",
        "",
        "## Counts",
        "",
    ]
    for key, value in sorted(result.get("counts", {}).items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Errors", ""])
    lines.extend(f"- {item}" for item in result["errors"]) if result["errors"] else lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in result["warnings"]) if result["warnings"] else lines.append("- none")
    lines.extend(["", "## Coverage", "", "```json", json.dumps(result.get("coverage", {}), ensure_ascii=False, indent=2, sort_keys=True), "```", "", result["final_message"], ""])
    md_report_path.write_text("\n".join(lines), encoding="utf-8")


def build_counts(seed: dict[str, Any]) -> dict[str, int]:
    return {
        "event_type_count": len(as_list(seed.get("event_type_catalog"))),
        "state_change_type_count": len(as_list(seed.get("state_change_type_catalog"))),
        "argument_role_count": len(as_list(seed.get("argument_role_catalog"))),
        "confidence_rule_count": len(as_list(seed.get("confidence_rules"))),
        "importance_rule_count": len(as_list(seed.get("importance_rules"))),
        "merge_rule_count": len(as_list(seed.get("merge_key_rules"))),
        "reject_rule_count": len(as_list(seed.get("reject_rules"))),
        "downgrade_rule_count": len(as_list(seed.get("downgrade_rules"))),
        "warning_flag_count": len(as_list(seed.get("warning_flag_catalog"))),
    }


def run_l5_event_normalization_rules_verifier(
    project_dir: Path | str | None = None,
    *,
    strict: bool = False,
    check_l5_candidates: bool = False,
    check_l5_review_export: bool = False,
) -> dict[str, Any]:
    paths = resolve_project_paths(project_dir)
    errors: list[str] = []
    warnings: list[str] = []
    coverage: dict[str, Any] = {}
    seed = load_seed(paths["seed"], errors)
    expected_checksum = ""
    if seed:
        validate_root_keys(seed, errors)
        validate_hard_constraints(seed, errors)
        validate_review_decisions(seed, errors)
        event_types, subtype_by_event = validate_event_types(seed, errors)
        state_types = validate_state_change_types(seed, event_types, errors)
        roles = validate_argument_roles(seed, event_types, errors)
        validate_event_role_and_state_refs(seed, roles, state_types, errors)
        trigger_categories = validate_trigger_mappings(seed, event_types, subtype_by_event, errors)
        event_candidate_types, state_candidate_types = validate_candidate_event_type_mappings(seed, event_types, state_types, subtype_by_event, errors)
        validate_candidate_field_mapping(seed, errors)
        validate_confidence_rules(seed, errors)
        validate_importance_rules(seed, event_types, errors)
        validate_merge_rules(seed, errors)
        validate_reject_rules(seed, errors)
        validate_downgrade_rules(seed, errors)
        warning_flags = validate_warning_flags(seed, errors)
        validate_guardrails(seed, errors)
        validate_future_tables(seed, errors)
        validate_future_tables_not_created(paths["db"], errors, warnings)
        expected_checksum = validate_checksum(seed, errors, warnings)
        if check_l5_candidates:
            coverage["l5_candidates"] = validate_l5_candidate_coverage(
                paths["db"],
                trigger_categories,
                event_candidate_types,
                state_candidate_types,
                warning_flags,
                errors,
                warnings,
                strict,
            )
        if check_l5_review_export:
            coverage["l5_review_export"] = validate_l5_review_export_coverage(
                paths["root"],
                trigger_categories,
                event_candidate_types,
                state_candidate_types,
                warning_flags,
                errors,
                warnings,
                strict,
            )
    ok = not errors
    result = {
        "ok": ok,
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "project_dir": str(paths["root"]),
        "seed_path": str(paths["seed"]),
        "db_path": str(paths["db"]),
        "strict": strict,
        "check_l5_candidates": check_l5_candidates,
        "check_l5_review_export": check_l5_review_export,
        "expected_seed_checksum": expected_checksum,
        "counts": build_counts(seed) if seed else {},
        "coverage": coverage,
        "errors": errors,
        "warnings": warnings,
        "final_message": PASS_MESSAGE if ok else FAIL_MESSAGE,
    }
    write_verify_reports(result, paths["json_report"], paths["md_report"])
    return result


def write_seed_checksum(project_dir: Path | str | None = None) -> str:
    paths = resolve_project_paths(project_dir)
    errors: list[str] = []
    seed = load_seed(paths["seed"], errors)
    if errors:
        raise RuntimeError("; ".join(errors))
    checksum = compute_seed_checksum(seed)
    seed["seed_checksum"] = checksum
    paths["seed"].write_text(json.dumps(seed, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return checksum


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L5.2 event normalization seed/rule catalog.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--check-l5-candidates", action="store_true")
    parser.add_argument("--check-l5-review-export", action="store_true")
    parser.add_argument("--write-checksum", action="store_true")
    args = parser.parse_args()
    if args.write_checksum:
        write_seed_checksum(args.project_dir)
    result = run_l5_event_normalization_rules_verifier(
        args.project_dir,
        strict=args.strict,
        check_l5_candidates=args.check_l5_candidates,
        check_l5_review_export=args.check_l5_review_export,
    )
    print(result["final_message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
