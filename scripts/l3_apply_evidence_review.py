from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import ensure_dirs, project_root_from_env


TOOL_VERSION = "v1"
ALLOWED_HUMAN_STATUS = {None, "accepted", "rejected", "needs_more"}
DEFAULT_TOP_K_PER_SEED = 3
DEFAULT_TEMPLATE_OUTPUT = Path("outputs") / "l3_evidence_review_manual_template.json"
DEFAULT_ACCEPTED_OUTPUT = Path("outputs") / "l3_seed_evidence_accepted.json"
DEFAULT_REJECTED_OUTPUT = Path("outputs") / "l3_seed_evidence_rejected.json"
DEFAULT_NEEDS_MORE_OUTPUT = Path("outputs") / "l3_seed_evidence_needs_more.json"
DEFAULT_SUMMARY_OUTPUT = Path("outputs") / "l3_seed_evidence_review_summary.json"


def resolve_output_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_human_status(value: Any, *, seed_item_id: str, candidate_id: str) -> str | None:
    if value not in ALLOWED_HUMAN_STATUS:
        raise ValueError(f"Invalid human_status for seed_item_id={seed_item_id} candidate_id={candidate_id}: {value!r}")
    return value


def resolve_input_path(project_dir: Path, value: str | Path) -> Path:
    source_path = Path(value)
    if not source_path.is_absolute():
        source_path = project_dir / source_path
    source_path = source_path.resolve()
    if source_path.exists():
        return source_path

    outputs_dir = project_dir / "outputs"
    review_candidates = sorted(
        path
        for path in outputs_dir.glob("l3_evidence_review_*.json")
        if path.name != "l3_evidence_review_manual_template.json"
    )
    if review_candidates:
        return review_candidates[0].resolve()

    fallback = outputs_dir / "l3_seed_evidence_candidates.json"
    if fallback.exists():
        return fallback.resolve()
    raise FileNotFoundError(f"Input JSON not found: {source_path}")


def seed_info(item: dict[str, Any]) -> dict[str, Any]:
    source = item.get("seed_source", {})
    item_id = item.get("seed_item_id") or source.get("item_id") or source.get("item_path") or "unknown_seed_item"
    return {
        "seed_file": item.get("seed_file") or source.get("seed_file"),
        "seed_item_id": item_id,
        "seed_item_path": item.get("seed_item_path") or source.get("item_path"),
        "seed_item_name": item.get("seed_item_name") or source.get("item_name"),
    }


def review_entry_to_candidate(entry: dict[str, Any]) -> dict[str, Any]:
    candidate = {k: v for k, v in entry.items() if k not in {"human_status", "human_note", "reviewed_at", "eligible_for_human_confirm", "review_flags", "review_id", "evidence_id", "duplicate_group_id", "suggested_status"}}
    if "evidence_text" not in candidate and "paragraph_text" in candidate:
        candidate["evidence_text"] = candidate["paragraph_text"]
    if "l2_coordinates" not in candidate:
        candidate["l2_coordinates"] = {
            "paragraph_id": candidate.get("paragraph_id"),
            "paragraph_index": candidate.get("paragraph_index"),
            "char_start": candidate.get("char_start"),
            "char_end": candidate.get("char_end"),
        }
    if "backcut" not in candidate:
        backcut = entry.get("l1_backcut_check") or {}
        candidate["backcut"] = backcut
    return candidate


def candidate_identity(seed_item_id: str, candidate: dict[str, Any]) -> str:
    existing = candidate.get("candidate_id")
    if existing:
        return str(existing)
    paragraph_id = candidate.get("paragraph_id")
    if paragraph_id:
        return f"{seed_item_id}:{paragraph_id}"
    chapter_id = candidate.get("chapter_id", "unknown_chapter")
    paragraph_hash = candidate.get("paragraph_hash", "unknown_hash")
    return f"{seed_item_id}:{chapter_id}:{paragraph_hash}"


def normalize_candidate(seed: dict[str, Any], candidate: dict[str, Any], human_status: Any, review_note: Any) -> dict[str, Any]:
    candidate_id = candidate_identity(seed["seed_item_id"], candidate)
    status = candidate.get("status", "candidate")
    if status != "candidate":
        raise ValueError(f"candidate_status must be 'candidate', got {status!r} for candidate_id={candidate_id}")

    normalized_human_status = validate_human_status(human_status, seed_item_id=seed["seed_item_id"], candidate_id=candidate_id)
    l2_coordinates = candidate.get("l2_coordinates") or {
        "paragraph_id": candidate.get("paragraph_id"),
        "paragraph_index": candidate.get("paragraph_index"),
        "char_start": candidate.get("char_start"),
        "char_end": candidate.get("char_end"),
    }
    backcut = candidate.get("backcut") or candidate.get("l1_backcut_check") or {}

    return {
        "seed_file": seed["seed_file"],
        "seed_item_id": seed["seed_item_id"],
        "seed_item_path": seed["seed_item_path"],
        "seed_item_name": seed["seed_item_name"],
        "candidate_id": candidate_id,
        "candidate": {
            **candidate,
            "candidate_id": candidate_id,
            "status": "candidate",
            "human_status": normalized_human_status,
            "review_note": review_note,
            "evidence_text": candidate.get("evidence_text") or candidate.get("paragraph_text"),
            "l2_coordinates": l2_coordinates,
            "backcut": backcut,
        },
        "human_status": normalized_human_status,
        "review_note": review_note,
    }


def normalize_input(payload: dict[str, Any], *, top_k_per_seed: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for raw_item in payload.get("items", []):
        seed = seed_info(raw_item)
        raw_candidates = raw_item.get("review_candidates")
        limit_candidates = raw_candidates is None
        if raw_candidates is None:
            raw_candidates = raw_item.get("candidates", [])

        seed_candidates: list[dict[str, Any]] = []
        for entry in raw_candidates:
            if isinstance(entry, dict) and "candidate" in entry:
                candidate = entry["candidate"]
                human_status = entry.get("human_status", candidate.get("human_status"))
                review_note = entry.get("review_note", candidate.get("review_note"))
            else:
                candidate = entry
                human_status = candidate.get("human_status")
                review_note = candidate.get("review_note")
            seed_candidates.append(normalize_candidate(seed, candidate, human_status, review_note))

        normalized.extend(seed_candidates[:top_k_per_seed] if limit_candidates else seed_candidates)
    if normalized:
        return normalized
    grouped_review_items: dict[str, list[dict[str, Any]]] = {}
    for raw_item in payload.get("review_items", []):
        seed = seed_info(raw_item)
        candidate = review_entry_to_candidate(raw_item)
        grouped_review_items.setdefault(seed["seed_item_id"], []).append(
            normalize_candidate(
                seed,
                candidate,
                raw_item.get("human_status"),
                raw_item.get("human_note"),
            )
        )
    for seed_item_id in sorted(grouped_review_items):
        normalized.extend(grouped_review_items[seed_item_id][:top_k_per_seed])
    return normalized


def build_template(input_path: Path, candidates: list[dict[str, Any]], *, top_k_per_seed: int) -> dict[str, Any]:
    grouped: dict[str, dict[str, Any]] = {}
    for entry in candidates:
        seed_item_id = entry["seed_item_id"]
        group = grouped.setdefault(
            seed_item_id,
            {
                "seed_file": entry["seed_file"],
                "seed_item_id": seed_item_id,
                "seed_item_path": entry["seed_item_path"],
                "seed_item_name": entry["seed_item_name"],
                "review_candidates": [],
            },
        )
        group["review_candidates"].append(
            {
                "human_status": None,
                "review_note": None,
                "candidate": {
                    **entry["candidate"],
                    "human_status": None,
                    "review_note": None,
                },
            }
        )

    return {
        "meta": {
            "tool": "l3_apply_evidence_review",
            "version": TOOL_VERSION,
            "mode": "review_template",
            "source_input": str(input_path),
            "top_k_per_seed": top_k_per_seed,
            "writes_l1_l2": False,
            "allowed_human_status": [None, "accepted", "rejected", "needs_more"],
        },
        "items": list(grouped.values()),
    }


def compact_review_item(entry: dict[str, Any]) -> dict[str, Any]:
    candidate = entry["candidate"]
    return {
        "seed_file": entry["seed_file"],
        "seed_item_id": entry["seed_item_id"],
        "seed_item_path": entry["seed_item_path"],
        "seed_item_name": entry["seed_item_name"],
        "candidate_id": candidate["candidate_id"],
        "candidate_status": candidate["status"],
        "human_status": entry["human_status"],
        "review_note": entry["review_note"],
        "evidence_type": candidate.get("evidence_type"),
        "score": candidate.get("score"),
        "chapter_id": candidate.get("chapter_id"),
        "version_id": candidate.get("version_id"),
        "chapter_num": candidate.get("chapter_num"),
        "chapter_title": candidate.get("chapter_title"),
        "paragraph_id": candidate.get("paragraph_id"),
        "paragraph_index": candidate.get("paragraph_index"),
        "char_start": candidate.get("char_start"),
        "char_end": candidate.get("char_end"),
        "paragraph_hash": candidate.get("paragraph_hash"),
        "evidence_text": candidate.get("evidence_text"),
        "matched_keywords": candidate.get("matched_keywords", []),
        "l2_coordinates": candidate.get("l2_coordinates"),
        "hash_info": {
            "paragraph_hash": candidate.get("paragraph_hash"),
        },
        "backcut": candidate.get("backcut"),
    }


def build_status_output(input_path: Path, top_k_per_seed: int, status_name: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "meta": {
            "tool": "l3_apply_evidence_review",
            "version": TOOL_VERSION,
            "mode": "review_apply",
            "source_input": str(input_path),
            "top_k_per_seed": top_k_per_seed,
            "writes_l1_l2": False,
            f"{status_name}_count": len(items),
        },
        "items": items,
    }


def build_summary(input_path: Path, top_k_per_seed: int, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"accepted": 0, "rejected": 0, "needs_more": 0, "unreviewed": 0}
    by_seed: dict[str, int] = {}
    for entry in candidates:
        by_seed[entry["seed_item_id"]] = by_seed.get(entry["seed_item_id"], 0) + 1
        human_status = entry["human_status"]
        if human_status is None:
            counts["unreviewed"] += 1
        else:
            counts[human_status] += 1

    return {
        "meta": {
            "tool": "l3_apply_evidence_review",
            "version": TOOL_VERSION,
            "mode": "review_apply",
            "source_input": str(input_path),
            "top_k_per_seed": top_k_per_seed,
            "writes_l1_l2": False,
            "total_candidates": len(candidates),
        },
        "candidate_counts_by_seed": by_seed,
        "status_counts": counts,
    }


def run_l3_apply_evidence_review(
    project_dir: Path | str | None = None,
    input_path: Path | str | None = None,
    *,
    emit_review_template: bool = False,
    top_k_per_seed: int = DEFAULT_TOP_K_PER_SEED,
    output: Path | str | None = None,
    output_accepted: Path | str | None = None,
    output_rejected: Path | str | None = None,
    output_needs_more: Path | str | None = None,
    summary: Path | str | None = None,
) -> dict[str, Any]:
    if input_path is None:
        raise ValueError("input_path is required")
    if top_k_per_seed <= 0:
        raise ValueError("top_k_per_seed must be > 0")

    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    source_path = resolve_input_path(root, input_path)

    payload = load_json(source_path)
    normalized = normalize_input(payload, top_k_per_seed=top_k_per_seed)
    result: dict[str, Any] = {}

    if emit_review_template:
        template = build_template(source_path, normalized, top_k_per_seed=top_k_per_seed)
        template_path = resolve_output_path(root, output, DEFAULT_TEMPLATE_OUTPUT)
        write_json(template_path, template)
        result["template"] = template

    accepted_items = [compact_review_item(entry) for entry in normalized if entry["human_status"] == "accepted"]
    rejected_items = [compact_review_item(entry) for entry in normalized if entry["human_status"] == "rejected"]
    needs_more_items = [compact_review_item(entry) for entry in normalized if entry["human_status"] == "needs_more"]

    if not emit_review_template or output_accepted is not None or output_rejected is not None or output_needs_more is not None or summary is not None:
        accepted_payload = build_status_output(source_path, top_k_per_seed, "accepted", accepted_items)
        rejected_payload = build_status_output(source_path, top_k_per_seed, "rejected", rejected_items)
        needs_more_payload = build_status_output(source_path, top_k_per_seed, "needs_more", needs_more_items)
        summary_payload = build_summary(source_path, top_k_per_seed, normalized)

        accepted_path = resolve_output_path(root, output_accepted, DEFAULT_ACCEPTED_OUTPUT)
        rejected_path = resolve_output_path(root, output_rejected, DEFAULT_REJECTED_OUTPUT)
        needs_more_path = resolve_output_path(root, output_needs_more, DEFAULT_NEEDS_MORE_OUTPUT)
        summary_path = resolve_output_path(root, summary, DEFAULT_SUMMARY_OUTPUT)

        write_json(accepted_path, accepted_payload)
        write_json(rejected_path, rejected_payload)
        write_json(needs_more_path, needs_more_payload)
        write_json(summary_path, summary_payload)

        result["accepted"] = accepted_payload
        result["rejected"] = rejected_payload
        result["needs_more"] = needs_more_payload
        result["summary"] = summary_payload

    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply deterministic human review decisions to L3 evidence review candidates.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--input", type=Path, required=True, help="Evidence review queue or manual review template JSON path.")
    parser.add_argument("--emit-review-template", action="store_true", help="Emit a manual review template with null human_status values.")
    parser.add_argument("--top-k-per-seed", type=int, default=DEFAULT_TOP_K_PER_SEED, help="Max candidates kept per seed item.")
    parser.add_argument("--output", type=Path, default=None, help="Manual review template output path.")
    parser.add_argument("--output-accepted", type=Path, default=None, help="Accepted evidence output path.")
    parser.add_argument("--output-rejected", type=Path, default=None, help="Rejected evidence output path.")
    parser.add_argument("--output-needs-more", type=Path, default=None, help="Needs-more evidence output path.")
    parser.add_argument("--summary", type=Path, default=None, help="Review summary output path.")
    args = parser.parse_args()

    result = run_l3_apply_evidence_review(
        project_dir=args.project_dir,
        input_path=args.input,
        emit_review_template=args.emit_review_template,
        top_k_per_seed=args.top_k_per_seed,
        output=args.output,
        output_accepted=args.output_accepted,
        output_rejected=args.output_rejected,
        output_needs_more=args.output_needs_more,
        summary=args.summary,
    )

    if "template" in result:
        template_count = sum(len(item["review_candidates"]) for item in result["template"]["items"])
        print(f"Review template candidates: {template_count}")
    if "summary" in result:
        print(f"Accepted: {result['summary']['status_counts']['accepted']}")
        print(f"Rejected: {result['summary']['status_counts']['rejected']}")
        print(f"Needs more: {result['summary']['status_counts']['needs_more']}")
        print(f"Unreviewed: {result['summary']['status_counts']['unreviewed']}")


if __name__ == "__main__":
    main()
