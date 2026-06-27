from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import ensure_dirs, project_root_from_env
from scripts.l3_seed_evidence_finder import extract_seed_items


TOOL_VERSION = "v1"
DEFAULT_CONFIG_GLOB = "config/*.seed.json"
DEFAULT_REVIEW_QUEUE_RELATIVE_PATH = Path("outputs") / "l3_evidence_review_queue.json"
DEFAULT_CANDIDATES_RELATIVE_PATH = Path("outputs") / "l3_seed_evidence_candidates.json"
DEFAULT_PACKET_JSON_RELATIVE_PATH = Path("outputs") / "l3_seed_rule_review_packet.json"
DEFAULT_PACKET_MD_RELATIVE_PATH = Path("outputs") / "l3_seed_rule_review_packet.md"
DEFAULT_SUMMARY_RELATIVE_PATH = Path("outputs") / "l3_seed_rule_review_summary.json"
DEFAULT_TOP_K_PER_SEED = 3
ALLOWED_RULE_STATUS = [None, "confirmed", "revise", "needs_more", "unsupported", "conflict"]
ALLOWED_HUMAN_STATUS = [None, "accepted", "rejected", "needs_more"]


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


def candidate_identity(seed_item_id: str, candidate: dict[str, Any]) -> str:
    existing = candidate.get("candidate_id")
    if existing:
        return str(existing)
    paragraph_id = str(candidate.get("paragraph_id") or "unknown_paragraph")
    char_start = int(candidate.get("char_start") or 0)
    char_end = int(candidate.get("char_end") or 0)
    return f"{seed_item_id}:{paragraph_id}:{char_start}:{char_end}"


def normalize_seed_file_key(project_dir: Path, seed_file: Any) -> str:
    if seed_file is None:
        return ""
    path = Path(str(seed_file))
    if not path.is_absolute():
        path = project_dir / path
    return str(path.resolve())


def derive_seed_item_id(seed_source: dict[str, Any], seed_rule_content: dict[str, Any]) -> str:
    return str(
        seed_source.get("item_id")
        or seed_rule_content.get("id")
        or seed_rule_content.get("godway_id")
        or seed_rule_content.get("rule_id")
        or seed_rule_content.get("domain_id")
        or seed_rule_content.get("lord_id")
        or seed_source.get("item_path")
        or "unknown_seed_item"
    )


def derive_seed_category(seed_file: Path, item_path: str, seed_rule_content: dict[str, Any]) -> str:
    for field in ("category", "rule_type"):
        value = seed_rule_content.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    if item_path.startswith("root.godways["):
        return "godway_catalog"
    if item_path.startswith("root.progressions["):
        return "godway_progression"
    if item_path.startswith("root.special_rules["):
        return "special_rule"
    return seed_file.stem.replace(".seed", "")


def load_seed_rule_index(project_dir: Path, config_glob: str) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for seed_path in sorted(project_dir.glob(config_glob)):
        seed_payload = json.loads(seed_path.read_text(encoding="utf-8"))
        seed_file_key = str(seed_path.resolve())
        for item_path, item in extract_seed_items(seed_payload):
            if isinstance(item, dict):
                index[(seed_file_key, item_path)] = item
    return index


def load_review_queue_index(project_dir: Path, payload: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    review_index: dict[tuple[str, str, str], dict[str, Any]] = {}
    review_items = payload.get("review_items")
    if not isinstance(review_items, list):
        raise ValueError("review queue must contain a review_items array")
    for item in review_items:
        if not isinstance(item, dict):
            continue
        seed_source = dict(item.get("seed_source") or {})
        seed_file_key = normalize_seed_file_key(project_dir, seed_source.get("seed_file"))
        item_path = str(seed_source.get("item_path") or "")
        seed_item_id = derive_seed_item_id(seed_source, {})
        review_index[(seed_file_key, item_path, candidate_identity(seed_item_id, item))] = item
    return review_index


def build_l2_coordinates(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "paragraph_id": candidate.get("paragraph_id"),
        "paragraph_index": candidate.get("paragraph_index"),
        "char_start": candidate.get("char_start"),
        "char_end": candidate.get("char_end"),
    }


def build_evidence_entry(
    seed_item_id: str,
    candidate: dict[str, Any],
    review_item: dict[str, Any] | None,
) -> dict[str, Any]:
    selected = review_item if review_item is not None else candidate
    human_status = selected.get("human_status")
    if human_status not in ALLOWED_HUMAN_STATUS:
        raise ValueError(f"Invalid human_status for seed_item_id={seed_item_id}: {human_status!r}")
    human_note = selected.get("human_note")
    if human_note is None:
        human_note = selected.get("review_note")
    if human_note is None:
        human_note = ""
    return {
        "candidate_id": candidate_identity(seed_item_id, candidate),
        "chapter_id": candidate.get("chapter_id"),
        "chapter_num": candidate.get("chapter_num"),
        "evidence_text": candidate.get("evidence_text") or candidate.get("paragraph_text") or "",
        "l2_coordinates": build_l2_coordinates(candidate),
        "paragraph_hash": candidate.get("paragraph_hash"),
        "backcut": selected.get("verifier_backcut_check") or candidate.get("l1_backcut_check") or {},
        "human_status": human_status,
        "human_note": str(human_note),
    }


def build_packet(
    project_dir: Path,
    seed_rule_index: dict[tuple[str, str], dict[str, Any]],
    review_index: dict[tuple[str, str, str], dict[str, Any]],
    candidates_payload: dict[str, Any],
    input_review_queue: Path,
    input_candidates: Path,
    *,
    top_k_per_seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate_items = candidates_payload.get("items")
    if not isinstance(candidate_items, list):
        raise ValueError("candidate payload must contain an items array")

    packet_items: list[dict[str, Any]] = []
    review_evidence_count = 0
    seed_items_with_evidence = 0

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
        candidates = raw_item.get("candidates") or []
        if not isinstance(candidates, list):
            raise ValueError(f"candidates must be an array for seed_item_id={seed_item_id}")

        evidence_items: list[dict[str, Any]] = []
        for candidate in candidates[:top_k_per_seed]:
            if not isinstance(candidate, dict):
                continue
            candidate_id = candidate_identity(seed_item_id, candidate)
            review_item = review_index.get((seed_file_key, item_path, candidate_id))
            evidence_items.append(build_evidence_entry(seed_item_id, candidate, review_item))

        if evidence_items:
            seed_items_with_evidence += 1
        review_evidence_count += len(evidence_items)

        packet_items.append(
            {
                "seed_item_id": seed_item_id,
                "seed_category": derive_seed_category(Path(seed_file_key), item_path, seed_rule_content),
                "seed_file": seed_file_key,
                "seed_item_path": item_path,
                "seed_item_name": seed_source.get("item_name") or seed_rule_content.get("name"),
                "seed_rule_content": seed_rule_content,
                "rule_status": None,
                "rule_note": "",
                "evidence_candidates": evidence_items,
            }
        )

    summary = {
        "meta": {
            "tool": "l3_seed_rule_review_packet",
            "version": TOOL_VERSION,
            "inputs": {
                "review_queue": str(input_review_queue),
                "seed_evidence_candidates": str(input_candidates),
                "config_glob": DEFAULT_CONFIG_GLOB,
            },
            "top_k_per_seed": top_k_per_seed,
            "writes_l1_l2": False,
            "seed_files_modified": False,
            "llm_used": False,
            "embedding_used": False,
            "chroma_used": False,
            "allowed_rule_status": ALLOWED_RULE_STATUS,
            "allowed_human_status": ALLOWED_HUMAN_STATUS,
        },
        "counts": {
            "seed_items": len(packet_items),
            "review_evidence_count": review_evidence_count,
            "seed_items_with_evidence": seed_items_with_evidence,
            "seed_items_without_evidence": len(packet_items) - seed_items_with_evidence,
            "max_evidence_per_seed": max((len(item["evidence_candidates"]) for item in packet_items), default=0),
            "input_candidate_items": len(candidate_items),
            "input_candidates": sum(len((item.get("candidates") or [])) for item in candidate_items if isinstance(item, dict)),
        },
    }
    packet = {
        "meta": summary["meta"],
        "summary": summary["counts"],
        "items": packet_items,
    }
    return packet, summary


def render_markdown(packet: dict[str, Any]) -> str:
    summary = packet["summary"]
    lines = [
        "# L3 Seed Rule Review Packet",
        "",
        "## Summary",
        "",
        f"- Seed items: {summary['seed_items']}",
        f"- Review evidence count: {summary['review_evidence_count']}",
        f"- Seed items with evidence: {summary['seed_items_with_evidence']}",
        f"- Seed items without evidence: {summary['seed_items_without_evidence']}",
        f"- Max evidence per seed: {summary['max_evidence_per_seed']}",
        "",
    ]
    for index, item in enumerate(packet["items"], start=1):
        lines.extend(
            [
                f"## Seed Item {index}",
                "",
                f"- seed_item_id: {item['seed_item_id']}",
                f"- seed_category: {item['seed_category']}",
                f"- seed_file: {item['seed_file']}",
                f"- seed_item_path: {item['seed_item_path']}",
                f"- seed_item_name: {item['seed_item_name']}",
                f"- rule_status: {item['rule_status']}",
                f"- rule_note: {item['rule_note']}",
                "",
                "Seed rule content:",
                "",
                "```json",
                json.dumps(item["seed_rule_content"], ensure_ascii=False, indent=2),
                "```",
                "",
            ]
        )
        if not item["evidence_candidates"]:
            lines.extend(["- No evidence candidates", ""])
            continue
        for evidence_index, evidence in enumerate(item["evidence_candidates"], start=1):
            lines.extend(
                [
                    f"### Evidence {evidence_index}",
                    "",
                    f"- candidate_id: {evidence['candidate_id']}",
                    f"- chapter_id: {evidence['chapter_id']}",
                    f"- chapter_num: {evidence['chapter_num']}",
                    f"- paragraph_hash: {evidence['paragraph_hash']}",
                    f"- l2_coordinates: {json.dumps(evidence['l2_coordinates'], ensure_ascii=False)}",
                    f"- backcut: {json.dumps(evidence['backcut'], ensure_ascii=False)}",
                    f"- human_status: {evidence['human_status']}",
                    f"- human_note: {evidence['human_note']}",
                    "",
                    "Evidence text:",
                    "",
                    f"> {str(evidence['evidence_text']).replace(chr(10), ' ')}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def run_l3_seed_rule_review_packet(
    project_dir: Path | str | None = None,
    *,
    config_glob: str = DEFAULT_CONFIG_GLOB,
    review_queue: Path | str | None = None,
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
    review_queue_path = resolve_path(root, review_queue, DEFAULT_REVIEW_QUEUE_RELATIVE_PATH)
    candidates_path = resolve_path(root, candidates, DEFAULT_CANDIDATES_RELATIVE_PATH)
    packet_json_path = resolve_path(root, output_json, DEFAULT_PACKET_JSON_RELATIVE_PATH)
    packet_md_path = resolve_path(root, output_md, DEFAULT_PACKET_MD_RELATIVE_PATH)
    summary_path = resolve_path(root, summary_output, DEFAULT_SUMMARY_RELATIVE_PATH)

    seed_rule_index = load_seed_rule_index(root, config_glob)
    review_index = load_review_queue_index(root, load_json(review_queue_path))
    packet, summary = build_packet(
        root,
        seed_rule_index,
        review_index,
        load_json(candidates_path),
        review_queue_path,
        candidates_path,
        top_k_per_seed=top_k_per_seed,
    )

    write_json(packet_json_path, packet)
    packet_md_path.parent.mkdir(parents=True, exist_ok=True)
    packet_md_path.write_text(render_markdown(packet), encoding="utf-8")
    write_json(summary_path, summary)
    return packet


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic L3 seed rule review packet for manual review.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--config-glob", default=DEFAULT_CONFIG_GLOB, help="Glob for seed config files.")
    parser.add_argument("--review-queue", type=Path, default=None, help="Review queue JSON path.")
    parser.add_argument("--candidates", type=Path, default=None, help="Seed evidence candidates JSON path.")
    parser.add_argument("--output-json", type=Path, default=None, help="Review packet JSON output path.")
    parser.add_argument("--output-md", type=Path, default=None, help="Review packet Markdown output path.")
    parser.add_argument("--summary-output", type=Path, default=None, help="Review summary JSON output path.")
    parser.add_argument("--top-k-per-seed", type=int, default=DEFAULT_TOP_K_PER_SEED, help="Max evidence items shown per seed.")
    args = parser.parse_args()

    packet = run_l3_seed_rule_review_packet(
        project_dir=args.project_dir,
        config_glob=args.config_glob,
        review_queue=args.review_queue,
        candidates=args.candidates,
        output_json=args.output_json,
        output_md=args.output_md,
        summary_output=args.summary_output,
        top_k_per_seed=args.top_k_per_seed,
    )
    summary = packet["summary"]
    print(f"Seed items: {summary['seed_items']}")
    print(f"Review evidence count: {summary['review_evidence_count']}")
    print(f"Max evidence per seed: {summary['max_evidence_per_seed']}")


if __name__ == "__main__":
    main()
