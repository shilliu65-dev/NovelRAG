from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import ensure_dirs, project_root_from_env
from scripts.l3_seed_rule_review_packet import (
    DEFAULT_CANDIDATES_RELATIVE_PATH,
    DEFAULT_CONFIG_GLOB,
    DEFAULT_TOP_K_PER_SEED,
    derive_seed_category,
    derive_seed_item_id,
    load_seed_rule_index,
    normalize_seed_file_key,
)


TOOL_VERSION = "v1"
DEFAULT_REVIEW_TEMPLATE_RELATIVE_PATH = Path("outputs") / "l3_evidence_review_manual_template_top3.json"
DEFAULT_CANDIDATES_INPUT_RELATIVE_PATH = DEFAULT_CANDIDATES_RELATIVE_PATH
DEFAULT_OUTPUT_JSON_RELATIVE_PATH = Path("outputs") / "l3_seed_rule_trusted_evidence_packet.json"
DEFAULT_OUTPUT_MD_RELATIVE_PATH = Path("outputs") / "l3_seed_rule_trusted_evidence_packet.md"
DEFAULT_SUMMARY_RELATIVE_PATH = Path("outputs") / "l3_seed_rule_trusted_evidence_summary.json"
ALLOWED_HUMAN_STATUS = {None, "accepted", "rejected", "needs_more"}


def resolve_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def rule_text(seed_rule_content: dict[str, Any]) -> str:
    return json.dumps(seed_rule_content, ensure_ascii=False, indent=2)


def evidence_hash(seed_item_id: str, candidate_id: str, paragraph_hash: Any, evidence_text: Any) -> str:
    return sha256(
        "".join(
            [
                str(seed_item_id),
                str(candidate_id),
                str(paragraph_hash),
                str(evidence_text),
            ]
        )
    )


def validate_human_status(value: Any, *, seed_item_id: str, candidate_id: str) -> str | None:
    if value not in ALLOWED_HUMAN_STATUS:
        raise ValueError(f"Invalid human_status for seed_item_id={seed_item_id} candidate_id={candidate_id}: {value!r}")
    return value


def candidate_payload_from_entry(entry: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(entry.get("candidate") or {})
    if not candidate:
        candidate = {
            key: value
            for key, value in entry.items()
            if key not in {"human_status", "review_note"}
        }
    return candidate


def derive_seed_rules_from_template(
    project_dir: Path,
    seed_rule_index: dict[tuple[str, str], dict[str, Any]],
    template_payload: dict[str, Any],
    candidates_payload: dict[str, Any],
    *,
    top_k_per_seed: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    items = template_payload.get("items")
    if not isinstance(items, list):
        raise ValueError("review template must contain an items array")

    template_groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    warnings: list[str] = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        seed_file_key = normalize_seed_file_key(project_dir, raw_item.get("seed_file"))
        seed_path = str(raw_item.get("seed_item_path") or "")
        seed_item_id = str(raw_item.get("seed_item_id") or seed_path or "unknown_seed_item")
        template_groups[(seed_file_key, seed_path, seed_item_id)] = raw_item

    seed_rules: list[dict[str, Any]] = []
    accepted_evidence_count = 0
    rejected_evidence_count = 0
    needs_more_evidence_count = 0
    pending_evidence_count = 0
    seed_items_with_accepted_evidence = 0
    max_accepted_evidence_per_seed = 0

    candidate_items = candidates_payload.get("items")
    if not isinstance(candidate_items, list):
        raise ValueError("candidates payload must contain an items array")
    sorted_keys = []
    for raw_item in candidate_items:
        if not isinstance(raw_item, dict):
            continue
        seed_source = dict(raw_item.get("seed_source") or {})
        seed_file_key = normalize_seed_file_key(project_dir, seed_source.get("seed_file"))
        item_path = str(seed_source.get("item_path") or "")
        seed_rule_content = seed_rule_index.get((seed_file_key, item_path))
        if seed_rule_content is None:
            raise ValueError(f"Seed rule not found for seed_file={seed_file_key} item_path={item_path}")
        seed_item_id = derive_seed_item_id(seed_source, seed_rule_content)
        sorted_keys.append((seed_file_key, item_path, seed_item_id))
    sorted_keys.sort(key=lambda value: (value[0], value[2]))

    for seed_file_key, item_path, seed_item_id in sorted_keys:
        seed_rule_content = seed_rule_index[(seed_file_key, item_path)]
        template_group = template_groups.get((seed_file_key, item_path, seed_item_id))
        review_candidates = []
        if template_group is not None:
            raw_candidates = template_group.get("review_candidates") or []
            if not isinstance(raw_candidates, list):
                raise ValueError(f"review_candidates must be an array for seed_item_id={seed_item_id}")
            review_candidates = raw_candidates[:top_k_per_seed]

        trusted_evidence: list[dict[str, Any]] = []
        seed_has_needs_more = False
        seed_has_pending = False

        for entry in review_candidates:
            if not isinstance(entry, dict):
                continue
            candidate = candidate_payload_from_entry(entry)
            candidate_id = str(candidate.get("candidate_id") or f"{seed_item_id}:unknown")
            human_status = validate_human_status(entry.get("human_status"), seed_item_id=seed_item_id, candidate_id=candidate_id)
            review_note = entry.get("review_note")
            if review_note is None:
                review_note = candidate.get("review_note")

            if human_status == "accepted":
                accepted_evidence_count += 1
                trusted_evidence.append(
                    {
                        "candidate_id": candidate_id,
                        "chapter_id": candidate.get("chapter_id"),
                        "chapter_num": candidate.get("chapter_num"),
                        "paragraph_hash": candidate.get("paragraph_hash"),
                        "evidence_text": candidate.get("evidence_text") or candidate.get("paragraph_text") or "",
                        "backcut": candidate.get("backcut") or candidate.get("l1_backcut_check") or {},
                        "human_status": "accepted",
                        "review_note": review_note or "",
                        "evidence_hash": evidence_hash(
                            seed_item_id,
                            candidate_id,
                            candidate.get("paragraph_hash"),
                            candidate.get("evidence_text") or candidate.get("paragraph_text") or "",
                        ),
                    }
                )
            elif human_status == "rejected":
                rejected_evidence_count += 1
            elif human_status == "needs_more":
                needs_more_evidence_count += 1
                seed_has_needs_more = True
            else:
                pending_evidence_count += 1
                seed_has_pending = True

            for missing_field in ("chapter_id", "chapter_num", "paragraph_hash"):
                if candidate.get(missing_field) in (None, ""):
                    warnings.append(f"seed_item_id={seed_item_id} candidate_id={candidate_id} missing optional field {missing_field}")

        trusted_evidence = trusted_evidence[:top_k_per_seed]
        if trusted_evidence:
            trusted_status = "trusted"
            seed_items_with_accepted_evidence += 1
        elif seed_has_needs_more:
            trusted_status = "needs_more"
        elif seed_has_pending:
            trusted_status = "pending"
        else:
            trusted_status = "no_accepted_evidence"

        max_accepted_evidence_per_seed = max(max_accepted_evidence_per_seed, len(trusted_evidence))
        seed_rules.append(
            {
                "seed_item_id": seed_item_id,
                "seed_file": seed_file_key,
                "seed_path": item_path,
                "seed_rule_type": derive_seed_category(Path(seed_file_key), item_path, seed_rule_content),
                "seed_rule_text": rule_text(seed_rule_content),
                "trusted_status": trusted_status,
                "trusted_evidence": trusted_evidence,
            }
        )

    summary = {
        "seed_items_total": len(seed_rules),
        "seed_items_with_accepted_evidence": seed_items_with_accepted_evidence,
        "seed_items_without_accepted_evidence": len(seed_rules) - seed_items_with_accepted_evidence,
        "accepted_evidence_count": accepted_evidence_count,
        "rejected_evidence_count": rejected_evidence_count,
        "needs_more_evidence_count": needs_more_evidence_count,
        "pending_evidence_count": pending_evidence_count,
        "max_accepted_evidence_per_seed": max_accepted_evidence_per_seed,
        "all_seed_items_lte_3": max_accepted_evidence_per_seed <= top_k_per_seed,
    }
    return seed_rules, {"warnings": warnings, **summary}


def build_packet(
    review_template_path: Path,
    seed_rules: list[dict[str, Any]],
    summary_info: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    notes = []
    if summary_info["accepted_evidence_count"] == 0:
        notes.append("No accepted evidence yet; this is expected when manual accepted/rejected/needs_more labeling has not been completed.")
    if summary_info["warnings"]:
        notes.append(f"Warnings recorded: {len(summary_info['warnings'])}")

    packet = {
        "meta": {
            "artifact_type": "l3_seed_rule_trusted_evidence_packet",
            "version": TOOL_VERSION,
            "project": "NovelRAG",
            "source_files": [str(review_template_path)],
            "generated_at": "",
            "notes": notes,
        },
        "summary": {
            key: summary_info[key]
            for key in (
                "seed_items_total",
                "seed_items_with_accepted_evidence",
                "seed_items_without_accepted_evidence",
                "accepted_evidence_count",
                "rejected_evidence_count",
                "needs_more_evidence_count",
                "pending_evidence_count",
                "max_accepted_evidence_per_seed",
                "all_seed_items_lte_3",
            )
        },
        "seed_rules": seed_rules,
    }
    summary_payload = {
        "meta": packet["meta"],
        "summary": packet["summary"],
        "warnings": summary_info["warnings"],
    }
    return packet, summary_payload


def render_markdown(packet: dict[str, Any], summary_payload: dict[str, Any]) -> str:
    summary = packet["summary"]
    lines = [
        "# L3 Trusted Seed Evidence Packet",
        "",
        "## Summary",
        "",
        f"- Seed items total: {summary['seed_items_total']}",
        f"- Seed items with accepted evidence: {summary['seed_items_with_accepted_evidence']}",
        f"- Seed items without accepted evidence: {summary['seed_items_without_accepted_evidence']}",
        f"- Accepted evidence count: {summary['accepted_evidence_count']}",
        f"- Rejected evidence count: {summary['rejected_evidence_count']}",
        f"- Needs-more evidence count: {summary['needs_more_evidence_count']}",
        f"- Pending evidence count: {summary['pending_evidence_count']}",
        f"- Max accepted evidence per seed: {summary['max_accepted_evidence_per_seed']}",
        f"- All seed items lte 3: {str(summary['all_seed_items_lte_3']).lower()}",
        "",
    ]
    if summary_payload["warnings"]:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {warning}" for warning in summary_payload["warnings"])
        lines.append("")
    for index, item in enumerate(packet["seed_rules"], start=1):
        lines.extend(
            [
                f"## Seed Rule {index}",
                "",
                f"- seed_item_id: {item['seed_item_id']}",
                f"- seed_file: {item['seed_file']}",
                f"- seed_path: {item['seed_path']}",
                f"- seed_rule_type: {item['seed_rule_type']}",
                f"- trusted_status: {item['trusted_status']}",
                "",
                "Seed rule text:",
                "",
                "```json",
                item["seed_rule_text"],
                "```",
                "",
            ]
        )
        if not item["trusted_evidence"]:
            lines.extend(["- No trusted evidence", ""])
            continue
        for evidence_index, evidence in enumerate(item["trusted_evidence"], start=1):
            lines.extend(
                [
                    f"### Trusted Evidence {evidence_index}",
                    "",
                    f"- candidate_id: {evidence['candidate_id']}",
                    f"- chapter_id: {evidence['chapter_id']}",
                    f"- chapter_num: {evidence['chapter_num']}",
                    f"- paragraph_hash: {evidence['paragraph_hash']}",
                    f"- human_status: {evidence['human_status']}",
                    f"- review_note: {evidence['review_note']}",
                    f"- evidence_hash: {evidence['evidence_hash']}",
                    f"- backcut: {json.dumps(evidence['backcut'], ensure_ascii=False)}",
                    "",
                    "Evidence text:",
                    "",
                    f"> {str(evidence['evidence_text']).replace(chr(10), ' ')}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def run_l3_build_trusted_seed_evidence_packet(
    project_dir: Path | str | None = None,
    *,
    config_glob: str = DEFAULT_CONFIG_GLOB,
    review_template: Path | str | None = None,
    candidates: Path | str | None = None,
    output_json: Path | str | None = None,
    output_md: Path | str | None = None,
    summary_output: Path | str | None = None,
    top_k_per_seed: int = DEFAULT_TOP_K_PER_SEED,
) -> dict[str, Any]:
    if top_k_per_seed <= 0:
        raise ValueError("top_k_per_seed must be > 0")

    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    review_template_path = resolve_path(root, review_template, DEFAULT_REVIEW_TEMPLATE_RELATIVE_PATH)
    candidates_path = resolve_path(root, candidates, DEFAULT_CANDIDATES_INPUT_RELATIVE_PATH)
    output_json_path = resolve_path(root, output_json, DEFAULT_OUTPUT_JSON_RELATIVE_PATH)
    output_md_path = resolve_path(root, output_md, DEFAULT_OUTPUT_MD_RELATIVE_PATH)
    summary_path = resolve_path(root, summary_output, DEFAULT_SUMMARY_RELATIVE_PATH)

    template_payload = load_json(review_template_path)
    seed_rule_index = load_seed_rule_index(root, config_glob)
    seed_rules, summary_info = derive_seed_rules_from_template(
        root,
        seed_rule_index,
        template_payload,
        load_json(candidates_path),
        top_k_per_seed=top_k_per_seed,
    )
    packet, summary_payload = build_packet(review_template_path, seed_rules, summary_info)
    write_json(output_json_path, packet)
    output_md_path.parent.mkdir(parents=True, exist_ok=True)
    output_md_path.write_text(render_markdown(packet, summary_payload), encoding="utf-8")
    write_json(summary_path, summary_payload)
    return packet


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a trusted L3 seed evidence packet from reviewed top3 evidence.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--config-glob", default=DEFAULT_CONFIG_GLOB, help="Glob for seed config files.")
    parser.add_argument("--review-template", type=Path, default=None, help="Reviewed manual template JSON path.")
    parser.add_argument("--candidates", type=Path, default=None, help="Seed evidence candidates JSON path for full seed coverage.")
    parser.add_argument("--output-json", type=Path, default=None, help="Trusted packet JSON output path.")
    parser.add_argument("--output-md", type=Path, default=None, help="Trusted packet Markdown output path.")
    parser.add_argument("--summary-output", type=Path, default=None, help="Trusted packet summary JSON output path.")
    parser.add_argument("--top-k-per-seed", type=int, default=DEFAULT_TOP_K_PER_SEED, help="Max accepted evidence retained per seed.")
    args = parser.parse_args()

    packet = run_l3_build_trusted_seed_evidence_packet(
        project_dir=args.project_dir,
        config_glob=args.config_glob,
        review_template=args.review_template,
        candidates=args.candidates,
        output_json=args.output_json,
        output_md=args.output_md,
        summary_output=args.summary_output,
        top_k_per_seed=args.top_k_per_seed,
    )
    summary = packet["summary"]
    print(f"Seed items total: {summary['seed_items_total']}")
    print(f"Accepted evidence count: {summary['accepted_evidence_count']}")
    print(f"Max accepted evidence per seed: {summary['max_accepted_evidence_per_seed']}")


if __name__ == "__main__":
    main()
