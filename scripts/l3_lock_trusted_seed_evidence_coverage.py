from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import ensure_dirs, project_root_from_env


CONTRACT_VERSION = "v1"
SCRIPT_NAME = "l3_lock_trusted_seed_evidence_coverage.py"
EXPECTED_SEED_ITEMS_TOTAL = 43
ALLOWED_COVERAGE_STATUS = {"covered", "uncovered"}
ALLOWED_NEXT_ACTION = {
    "keep_as_coverage_locked",
    "keep_as_uncovered_for_now",
    "schedule_followup_evidence_search",
    "revise_seed_later_if_no_evidence",
}
DEFAULT_TRUSTED_PACKET = Path("outputs") / "l3_seed_rule_trusted_evidence_packet.json"
DEFAULT_TRUSTED_SUMMARY = Path("outputs") / "l3_seed_rule_trusted_evidence_summary.json"
DEFAULT_REVIEW_TEMPLATE = Path("outputs") / "l3_evidence_review_manual_template_top3_reviewed_all_accepted.json"
DEFAULT_CANDIDATES = Path("outputs") / "l3_seed_evidence_candidates.json"
DEFAULT_COVERAGE_LOCK_JSON = Path("outputs") / "l3_trusted_seed_evidence_coverage_lock.json"
DEFAULT_COVERAGE_LOCK_MD = Path("outputs") / "l3_trusted_seed_evidence_coverage_lock.md"
DEFAULT_GAP_REVIEW_JSON = Path("outputs") / "l3_trusted_seed_evidence_gap_review.json"
DEFAULT_GAP_REVIEW_MD = Path("outputs") / "l3_trusted_seed_evidence_gap_review.md"
DEFAULT_VALIDATION_JSON = Path("outputs") / "l3_trusted_seed_evidence_coverage_lock_validation_report.json"
DEFAULT_VALIDATION_MD = Path("outputs") / "l3_trusted_seed_evidence_coverage_lock_validation_report.md"


def resolve_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def short_ref(text: str | None, limit: int = 80) -> str:
    if not text:
        return ""
    compact = " ".join(str(text).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def seed_identity(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("seed_source", {})
    seed_item_id = source.get("item_id") or item.get("seed_item_id") or source.get("item_path")
    if not seed_item_id:
        raise ValueError("seed_item_id is required in candidates payload")
    return {
        "seed_item_id": str(seed_item_id),
        "seed_source_file": item.get("seed_file") or source.get("seed_file"),
        "seed_item_path": item.get("seed_item_path") or source.get("item_path"),
        "seed_label": item.get("seed_item_name") or source.get("item_name") or str(seed_item_id),
        "seed_category": infer_seed_category(item.get("seed_item_path") or source.get("item_path")),
        "seed_content_summary": build_seed_content_summary(item),
    }


def infer_seed_category(seed_item_path: str | None) -> str | None:
    if not seed_item_path:
        return None
    normalized = seed_item_path.lower()
    if ".rules[" in normalized or ".rule" in normalized:
        return "rule"
    if ".godways[" in normalized:
        return "godway"
    if ".domains[" in normalized:
        return "domain"
    if ".lords[" in normalized:
        return "lord"
    if ".items[" in normalized:
        return "item"
    return "unknown"


def build_seed_content_summary(item: dict[str, Any]) -> str:
    source = item.get("seed_source", {})
    label = source.get("item_name") or item.get("seed_item_name") or source.get("item_id") or source.get("item_path") or "unknown"
    keyword_set = item.get("keyword_set") or []
    keywords = ", ".join(str(keyword) for keyword in keyword_set[:5])
    return f"{label}; keywords={keywords}" if keywords else str(label)


def flatten_review_candidates(review_template: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    per_seed: dict[str, list[dict[str, Any]]] = {}
    flattened: list[dict[str, Any]] = []
    for item in review_template.get("items", []):
        seed_item_id = str(item.get("seed_item_id"))
        candidates = item.get("review_candidates", [])
        per_seed[seed_item_id] = candidates
        for review_entry in candidates:
            candidate = review_entry.get("candidate", {})
            flattened.append(
                {
                    "seed_item_id": seed_item_id,
                    "candidate_id": candidate.get("candidate_id"),
                    "human_status": review_entry.get("human_status", candidate.get("human_status")),
                }
            )
    return per_seed, flattened


def accepted_lookup(trusted_packet: dict[str, Any]) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    per_seed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    accepted_items: list[dict[str, Any]] = []

    packet_items = trusted_packet.get("items")
    if isinstance(packet_items, list):
        for item in packet_items:
            seed_item_id = str(item.get("seed_item_id"))
            per_seed[seed_item_id].append(item)
            accepted_items.append(item)

    seed_rules = trusted_packet.get("seed_rules")
    if isinstance(seed_rules, list):
        for rule in seed_rules:
            seed_item_id = str(rule.get("seed_item_id"))
            for evidence in rule.get("trusted_evidence", []):
                normalized = {
                    "seed_item_id": seed_item_id,
                    "seed_file": rule.get("seed_file"),
                    "seed_item_path": rule.get("seed_path"),
                    "seed_item_name": rule.get("seed_rule_text"),
                    "candidate_id": evidence.get("candidate_id"),
                    "chapter_id": evidence.get("chapter_id"),
                    "chapter_num": evidence.get("chapter_num"),
                    "paragraph_hash": evidence.get("paragraph_hash"),
                    "evidence_text": evidence.get("evidence_text"),
                    "backcut": evidence.get("backcut"),
                    "human_status": evidence.get("human_status"),
                    "review_note": evidence.get("review_note"),
                }
                per_seed[seed_item_id].append(normalized)
                accepted_items.append(normalized)

    for items in per_seed.values():
        items.sort(key=lambda value: str(value.get("candidate_id")))
    return dict(sorted(per_seed.items())), accepted_items


def candidate_lookup(candidates_payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[str]]:
    seed_items: list[dict[str, Any]] = []
    candidates_by_key: dict[str, dict[str, Any]] = {}
    duplicate_seed_item_ids: list[str] = []
    seen_seed_ids: set[str] = set()

    for item in candidates_payload.get("items", []):
        seed = seed_identity(item)
        if seed["seed_item_id"] in seen_seed_ids:
            duplicate_seed_item_ids.append(seed["seed_item_id"])
        seen_seed_ids.add(seed["seed_item_id"])
        seed_items.append({**seed, "raw_candidates": item.get("candidates", [])})
        for candidate in item.get("candidates", []):
            candidate_id = derive_candidate_id(seed["seed_item_id"], candidate)
            if candidate_id is None:
                continue
            key = accepted_candidate_key(seed["seed_item_id"], candidate_id)
            candidates_by_key[key] = candidate

    seed_items.sort(key=lambda value: value["seed_item_id"])
    duplicate_seed_item_ids.sort()
    return seed_items, candidates_by_key, duplicate_seed_item_ids


def accepted_candidate_key(seed_item_id: str, candidate_id: Any) -> str:
    return f"{seed_item_id}::{candidate_id}"


def derive_candidate_id(seed_item_id: str, candidate: dict[str, Any]) -> str | None:
    existing = candidate.get("candidate_id")
    if existing:
        return str(existing)
    paragraph_id = candidate.get("paragraph_id")
    if paragraph_id:
        return f"{seed_item_id}:{paragraph_id}"
    return None


def compute_gap_reason(seed: dict[str, Any], review_candidates_by_seed: dict[str, list[dict[str, Any]]], accepted_by_seed: dict[str, list[dict[str, Any]]]) -> str | None:
    seed_item_id = seed["seed_item_id"]
    if not seed["raw_candidates"]:
        return "no_candidate_evidence"
    if not review_candidates_by_seed.get(seed_item_id):
        return "no_review_evidence"
    if not accepted_by_seed.get(seed_item_id):
        return "no_accepted_evidence"
    return None


def compute_risk_level(seed: dict[str, Any]) -> str:
    seed_category = seed.get("seed_category")
    if seed_category == "rule":
        return "high"
    if seed_category in {"godway", "domain", "lord", "item"}:
        return "medium"
    return "low"


def compute_gap_next_action(gap_reason: str, risk_level: str) -> str:
    if gap_reason == "no_candidate_evidence":
        return "schedule_followup_evidence_search"
    if risk_level == "high":
        return "schedule_followup_evidence_search"
    return "keep_as_uncovered_for_now"


def compute_summary(
    seed_coverage_items: list[dict[str, Any]],
    accepted_items: list[dict[str, Any]],
    review_flat: list[dict[str, Any]],
    trusted_summary: dict[str, Any],
) -> dict[str, Any]:
    review_status_counts = Counter(entry.get("human_status") for entry in review_flat)
    accepted_from_summary = trusted_summary.get("status_counts", {}).get("accepted")
    accepted_count = len(accepted_items)
    if accepted_from_summary is not None and int(accepted_from_summary) != accepted_count:
        raise ValueError(
            f"accepted_evidence_count mismatch: trusted_summary={accepted_from_summary} trusted_packet={accepted_count}"
        )

    covered_count = sum(1 for item in seed_coverage_items if item["coverage_status"] == "covered")
    uncovered_count = sum(1 for item in seed_coverage_items if item["coverage_status"] == "uncovered")
    max_accepted = max((item["accepted_evidence_count"] for item in seed_coverage_items), default=0)
    return {
        "seed_items_total": len(seed_coverage_items),
        "seed_items_with_accepted_evidence": covered_count,
        "seed_items_without_accepted_evidence": uncovered_count,
        "accepted_evidence_count": accepted_count,
        "pending_evidence_count": int(review_status_counts.get(None, 0)),
        "rejected_evidence_count": int(review_status_counts.get("rejected", 0)),
        "needs_more_evidence_count": int(review_status_counts.get("needs_more", 0)),
        "max_accepted_evidence_per_seed": max_accepted,
        "coverage_rate": covered_count / len(seed_coverage_items) if seed_coverage_items else 0.0,
    }


def build_metadata(
    generated_at: str,
    project_dir: Path,
    script_path: Path,
    input_paths: dict[str, Path],
) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "project_dir": str(project_dir),
        "input_files": {key: str(path) for key, path in sorted(input_paths.items())},
        "input_file_sha256": {key: sha256_file(path) for key, path in sorted(input_paths.items())},
        "script_name": script_path.name,
        "contract_version": CONTRACT_VERSION,
    }


def build_integrity(
    duplicate_seed_item_ids: list[str],
    missing_candidate_refs: list[dict[str, str]],
    invalid_human_status_refs: list[dict[str, str]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    lock_validation_passed = (
        not duplicate_seed_item_ids
        and not missing_candidate_refs
        and not invalid_human_status_refs
        and summary["pending_evidence_count"] == 0
        and summary["max_accepted_evidence_per_seed"] <= 3
        and summary["seed_items_total"] == EXPECTED_SEED_ITEMS_TOTAL
    )
    return {
        "duplicate_seed_item_id_detected": bool(duplicate_seed_item_ids),
        "duplicate_seed_item_ids": duplicate_seed_item_ids,
        "accepted_evidence_missing_candidate_detected": bool(missing_candidate_refs),
        "accepted_evidence_missing_candidate_refs": missing_candidate_refs,
        "illegal_human_status_detected": bool(invalid_human_status_refs),
        "illegal_human_status_refs": invalid_human_status_refs,
        "pending_evidence_detected": summary["pending_evidence_count"] > 0,
        "accepted_evidence_exceeds_top3_limit_detected": summary["max_accepted_evidence_per_seed"] > 3,
        "lock_validation_passed": lock_validation_passed,
    }


def build_coverage_items(
    seed_items: list[dict[str, Any]],
    accepted_by_seed: dict[str, list[dict[str, Any]]],
    review_candidates_by_seed: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    coverage_items: list[dict[str, Any]] = []
    for seed in seed_items:
        seed_item_id = seed["seed_item_id"]
        accepted_items = accepted_by_seed.get(seed_item_id, [])
        coverage_status = "covered" if accepted_items else "uncovered"
        if coverage_status not in ALLOWED_COVERAGE_STATUS:
            raise ValueError(f"Invalid coverage status: {coverage_status}")
        gap_reason = None if coverage_status == "covered" else compute_gap_reason(seed, review_candidates_by_seed, accepted_by_seed)
        next_action = "keep_as_coverage_locked" if coverage_status == "covered" else compute_gap_next_action(gap_reason or "no_accepted_evidence", compute_risk_level(seed))
        if next_action not in ALLOWED_NEXT_ACTION:
            raise ValueError(f"Invalid next action: {next_action}")
        coverage_items.append(
            {
                "seed_item_id": seed_item_id,
                "seed_source_file": seed.get("seed_source_file"),
                "seed_category": seed.get("seed_category"),
                "seed_label": seed.get("seed_label"),
                "coverage_status": coverage_status,
                "accepted_evidence_count": len(accepted_items),
                "candidate_ids": [item.get("candidate_id") for item in accepted_items],
                "chapter_nums": sorted({int(item["chapter_num"]) for item in accepted_items if item.get("chapter_num") is not None}),
                "evidence_refs": [short_ref(item.get("evidence_text")) for item in accepted_items],
                "gap_reason": gap_reason,
                "next_action": next_action,
            }
        )
    coverage_items.sort(key=lambda value: value["seed_item_id"])
    return coverage_items


def build_gap_review(metadata: dict[str, Any], coverage_items: list[dict[str, Any]], seed_items: list[dict[str, Any]]) -> dict[str, Any]:
    seed_lookup = {item["seed_item_id"]: item for item in seed_items}
    gap_items: list[dict[str, Any]] = []
    for coverage_item in coverage_items:
        if coverage_item["coverage_status"] != "uncovered":
            continue
        seed = seed_lookup[coverage_item["seed_item_id"]]
        risk_level = compute_risk_level(seed)
        gap_reason = coverage_item["gap_reason"] or "no_accepted_evidence"
        gap_items.append(
            {
                "seed_item_id": coverage_item["seed_item_id"],
                "seed_source_file": seed.get("seed_source_file"),
                "seed_category": seed.get("seed_category"),
                "seed_content_summary": seed.get("seed_content_summary"),
                "current_status": "uncovered",
                "gap_reason": gap_reason,
                "risk_level": risk_level,
                "recommended_action": compute_gap_next_action(gap_reason, risk_level),
            }
        )
    gap_items.sort(key=lambda value: value["seed_item_id"])
    return {
        "metadata": metadata,
        "summary": {
            "uncovered_seed_count": len(gap_items),
        },
        "gap_items": gap_items,
    }


def coverage_lock_markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# L3 Trusted Seed Evidence Coverage Lock",
        "",
        "## Summary",
        "",
        f"- Seed items total: {summary['seed_items_total']}",
        f"- Covered seed items: {summary['seed_items_with_accepted_evidence']}",
        f"- Uncovered seed items: {summary['seed_items_without_accepted_evidence']}",
        f"- Accepted evidence count: {summary['accepted_evidence_count']}",
        f"- Pending evidence count: {summary['pending_evidence_count']}",
        f"- Rejected evidence count: {summary['rejected_evidence_count']}",
        f"- Needs-more evidence count: {summary['needs_more_evidence_count']}",
        f"- Max accepted evidence per seed: {summary['max_accepted_evidence_per_seed']}",
        f"- Coverage rate: {summary['coverage_rate']:.10f}",
        "",
        "## Seed Coverage Items",
        "",
    ]
    for item in payload["seed_coverage_items"]:
        lines.extend(
            [
                f"### {item['seed_item_id']}",
                "",
                f"- Seed label: {item.get('seed_label')}",
                f"- Seed source file: {item.get('seed_source_file')}",
                f"- Seed category: {item.get('seed_category')}",
                f"- Coverage status: {item['coverage_status']}",
                f"- Accepted evidence count: {item['accepted_evidence_count']}",
                f"- Candidate ids: {', '.join(item['candidate_ids']) if item['candidate_ids'] else '(none)'}",
                f"- Chapter nums: {', '.join(str(num) for num in item['chapter_nums']) if item['chapter_nums'] else '(none)'}",
                f"- Evidence refs: {' | '.join(item['evidence_refs']) if item['evidence_refs'] else '(none)'}",
                f"- Gap reason: {item['gap_reason'] or '(none)'}",
                f"- Next action: {item['next_action']}",
                "",
            ]
        )
    return "\n".join(lines)


def gap_review_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# L3 Trusted Seed Evidence Gap Review",
        "",
        f"- Uncovered seed count: {payload['summary']['uncovered_seed_count']}",
        "",
    ]
    for item in payload["gap_items"]:
        lines.extend(
            [
                f"## {item['seed_item_id']}",
                "",
                f"- Seed source file: {item.get('seed_source_file')}",
                f"- Seed category: {item.get('seed_category')}",
                f"- Seed content summary: {item.get('seed_content_summary')}",
                f"- Current status: {item['current_status']}",
                f"- Gap reason: {item['gap_reason']}",
                f"- Risk level: {item['risk_level']}",
                f"- Recommended action: {item['recommended_action']}",
                "",
            ]
        )
    return "\n".join(lines)


def validation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# L3 Trusted Seed Evidence Coverage Lock Validation Report",
        "",
        "## Inputs",
        "",
    ]
    for key, value in payload["input_files"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(
        [
            "",
            "## Validation",
            "",
            f"- Modified index/*.db: {'yes' if payload['modifies_index_db'] else 'no'}",
            f"- Modified config/*.seed.json: {'yes' if payload['modifies_seed_config'] else 'no'}",
            f"- Pending cleared in trusted packet: {'yes' if payload['trusted_packet_pending_cleared'] else 'no'}",
            f"- Seed items total: {payload['seed_items_total']}",
            f"- Covered seed items: {payload['covered_seed_count']}",
            f"- Uncovered seed items: {payload['uncovered_seed_count']}",
            f"- Accepted evidence count for covered seeds: {payload['accepted_evidence_count']}",
            f"- Uncovered seed ids: {', '.join(payload['uncovered_seed_ids']) if payload['uncovered_seed_ids'] else '(none)'}",
            f"- Coverage lock passed: {'yes' if payload['coverage_lock_passed'] else 'no'}",
            f"- Gap review generated: {'yes' if payload['gap_review_generated'] else 'no'}",
            "",
            "## Tests",
            "",
        ]
    )
    for command in payload["test_results"]["commands"]:
        lines.append(f"- {command['command']}: {command['result']}")
    lines.extend(
        [
            "",
            "## Next Step",
            "",
            f"- {payload['next_step_recommendation']}",
            "",
        ]
    )
    return "\n".join(lines)


def run_l3_lock_trusted_seed_evidence_coverage(
    project_dir: Path | str | None = None,
    *,
    trusted_packet: Path | str | None = None,
    trusted_summary: Path | str | None = None,
    review_template: Path | str | None = None,
    candidates: Path | str | None = None,
    output_coverage_lock_json: Path | str | None = None,
    output_coverage_lock_md: Path | str | None = None,
    output_gap_review_json: Path | str | None = None,
    output_gap_review_md: Path | str | None = None,
    output_validation_json: Path | str | None = None,
    output_validation_md: Path | str | None = None,
    fixed_generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    script_path = Path(__file__).resolve()

    input_paths = {
        "trusted_packet": resolve_path(root, trusted_packet, DEFAULT_TRUSTED_PACKET).resolve(),
        "trusted_summary": resolve_path(root, trusted_summary, DEFAULT_TRUSTED_SUMMARY).resolve(),
        "review_template": resolve_path(root, review_template, DEFAULT_REVIEW_TEMPLATE).resolve(),
        "candidates": resolve_path(root, candidates, DEFAULT_CANDIDATES).resolve(),
    }
    for path in input_paths.values():
        if not path.exists():
            raise FileNotFoundError(f"Required input file not found: {path}")

    generated_at = fixed_generated_at or datetime.now().isoformat(timespec="seconds")
    metadata = build_metadata(generated_at, root, script_path, input_paths)

    trusted_packet_payload = load_json(input_paths["trusted_packet"])
    trusted_summary_payload = load_json(input_paths["trusted_summary"])
    review_template_payload = load_json(input_paths["review_template"])
    candidates_payload = load_json(input_paths["candidates"])

    seed_items, candidates_by_key, duplicate_seed_item_ids = candidate_lookup(candidates_payload)
    review_candidates_by_seed, review_flat = flatten_review_candidates(review_template_payload)
    accepted_by_seed, accepted_items = accepted_lookup(trusted_packet_payload)

    invalid_human_status_refs = [
        {"seed_item_id": str(entry.get("seed_item_id")), "candidate_id": str(entry.get("candidate_id")), "human_status": str(entry.get("human_status"))}
        for entry in review_flat
        if entry.get("human_status") not in {"accepted", "rejected", "needs_more", None}
    ]
    missing_candidate_refs = [
        {"seed_item_id": str(item.get("seed_item_id")), "candidate_id": str(item.get("candidate_id"))}
        for item in accepted_items
        if accepted_candidate_key(str(item.get("seed_item_id")), item.get("candidate_id")) not in candidates_by_key
    ]
    if missing_candidate_refs:
        raise ValueError(f"Accepted evidence missing candidate back-reference: {missing_candidate_refs[0]}")
    if invalid_human_status_refs:
        raise ValueError(f"Invalid human_status detected: {invalid_human_status_refs[0]}")

    coverage_items = build_coverage_items(seed_items, accepted_by_seed, review_candidates_by_seed)
    summary = compute_summary(coverage_items, accepted_items, review_flat, trusted_summary_payload)
    integrity = build_integrity(duplicate_seed_item_ids, missing_candidate_refs, invalid_human_status_refs, summary)

    if summary["seed_items_total"] != EXPECTED_SEED_ITEMS_TOTAL:
        raise ValueError(f"seed_items_total must be {EXPECTED_SEED_ITEMS_TOTAL}, got {summary['seed_items_total']}")
    if len(coverage_items) != EXPECTED_SEED_ITEMS_TOTAL:
        raise ValueError(f"seed_coverage_items must be {EXPECTED_SEED_ITEMS_TOTAL}, got {len(coverage_items)}")
    if summary["pending_evidence_count"] != 0:
        raise ValueError(f"pending_evidence_count must be 0, got {summary['pending_evidence_count']}")
    if summary["max_accepted_evidence_per_seed"] > 3:
        raise ValueError(
            f"max_accepted_evidence_per_seed must be <= 3, got {summary['max_accepted_evidence_per_seed']}"
        )

    coverage_lock = {
        "metadata": metadata,
        "summary": summary,
        "seed_coverage_items": coverage_items,
        "integrity": integrity,
    }
    gap_review = build_gap_review(metadata, coverage_items, seed_items)
    if summary["seed_items_without_accepted_evidence"] != len(gap_review["gap_items"]):
        raise ValueError("uncovered seed count must equal gap review item count")

    uncovered_seed_ids = [item["seed_item_id"] for item in gap_review["gap_items"]]
    validation_report = {
        "metadata": metadata,
        "input_files": metadata["input_files"],
        "modifies_index_db": False,
        "modifies_seed_config": False,
        "trusted_packet_pending_cleared": summary["pending_evidence_count"] == 0,
        "seed_items_total": summary["seed_items_total"],
        "covered_seed_count": summary["seed_items_with_accepted_evidence"],
        "accepted_evidence_count": summary["accepted_evidence_count"],
        "uncovered_seed_count": summary["seed_items_without_accepted_evidence"],
        "uncovered_seed_ids": uncovered_seed_ids,
        "coverage_lock_passed": integrity["lock_validation_passed"],
        "gap_review_generated": bool(gap_review["gap_items"]) or summary["seed_items_without_accepted_evidence"] == 0,
        "test_results": {
            "commands": [
                {
                    "command": "python -m unittest tests.test_l3_lock_trusted_seed_evidence_coverage -v",
                    "result": "not_run_by_script",
                },
                {
                    "command": "python -m unittest discover -s tests -v",
                    "result": "not_run_by_script",
                },
                {
                    "command": "python -m compileall scripts tests",
                    "result": "not_run_by_script",
                },
            ]
        },
        "next_step_recommendation": (
            "进入阶段 E：针对 uncovered seed 的补证队列；或者若暂不补证，则进入 Story Bible seed evidence 引用层。"
        ),
    }

    coverage_lock_json_path = resolve_path(root, output_coverage_lock_json, DEFAULT_COVERAGE_LOCK_JSON)
    coverage_lock_md_path = resolve_path(root, output_coverage_lock_md, DEFAULT_COVERAGE_LOCK_MD)
    gap_review_json_path = resolve_path(root, output_gap_review_json, DEFAULT_GAP_REVIEW_JSON)
    gap_review_md_path = resolve_path(root, output_gap_review_md, DEFAULT_GAP_REVIEW_MD)
    validation_json_path = resolve_path(root, output_validation_json, DEFAULT_VALIDATION_JSON)
    validation_md_path = resolve_path(root, output_validation_md, DEFAULT_VALIDATION_MD)

    write_json(coverage_lock_json_path, coverage_lock)
    write_markdown(coverage_lock_md_path, coverage_lock_markdown(coverage_lock))
    write_json(gap_review_json_path, gap_review)
    write_markdown(gap_review_md_path, gap_review_markdown(gap_review))
    write_json(validation_json_path, validation_report)
    write_markdown(validation_md_path, validation_markdown(validation_report))

    return {
        "coverage_lock": coverage_lock,
        "gap_review": gap_review,
        "validation_report": validation_report,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Lock trusted seed evidence coverage and generate uncovered seed gap review.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--trusted-packet", type=Path, default=DEFAULT_TRUSTED_PACKET, help="Trusted evidence packet JSON path.")
    parser.add_argument("--trusted-summary", type=Path, default=DEFAULT_TRUSTED_SUMMARY, help="Trusted evidence summary JSON path.")
    parser.add_argument("--review-template", type=Path, default=DEFAULT_REVIEW_TEMPLATE, help="Reviewed top3 manual review template JSON path.")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES, help="Seed evidence candidates JSON path.")
    parser.add_argument("--output-coverage-lock-json", type=Path, default=DEFAULT_COVERAGE_LOCK_JSON, help="Coverage lock JSON output path.")
    parser.add_argument("--output-coverage-lock-md", type=Path, default=DEFAULT_COVERAGE_LOCK_MD, help="Coverage lock Markdown output path.")
    parser.add_argument("--output-gap-review-json", type=Path, default=DEFAULT_GAP_REVIEW_JSON, help="Gap review JSON output path.")
    parser.add_argument("--output-gap-review-md", type=Path, default=DEFAULT_GAP_REVIEW_MD, help="Gap review Markdown output path.")
    parser.add_argument("--output-validation-json", type=Path, default=DEFAULT_VALIDATION_JSON, help="Validation report JSON output path.")
    parser.add_argument("--output-validation-md", type=Path, default=DEFAULT_VALIDATION_MD, help="Validation report Markdown output path.")
    parser.add_argument("--fixed-generated-at", type=str, default=None, help="Fixed timestamp for deterministic test output.")
    args = parser.parse_args()

    result = run_l3_lock_trusted_seed_evidence_coverage(
        project_dir=args.project_dir,
        trusted_packet=args.trusted_packet,
        trusted_summary=args.trusted_summary,
        review_template=args.review_template,
        candidates=args.candidates,
        output_coverage_lock_json=args.output_coverage_lock_json,
        output_coverage_lock_md=args.output_coverage_lock_md,
        output_gap_review_json=args.output_gap_review_json,
        output_gap_review_md=args.output_gap_review_md,
        output_validation_json=args.output_validation_json,
        output_validation_md=args.output_validation_md,
        fixed_generated_at=args.fixed_generated_at,
    )
    print(f"Coverage lock passed: {result['coverage_lock']['integrity']['lock_validation_passed']}")
    print(f"Covered seeds: {result['coverage_lock']['summary']['seed_items_with_accepted_evidence']}")
    print(f"Uncovered seeds: {result['coverage_lock']['summary']['seed_items_without_accepted_evidence']}")


if __name__ == "__main__":
    main()
