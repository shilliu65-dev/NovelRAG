from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l3_seed_evidence_finder import sha256


TOOL_VERSION = "v1"
ALLOWED_SUGGESTED_STATUSES = {"needs_review", "likely_relevant", "likely_irrelevant", "invalid"}
REQUIRED_CANDIDATE_FIELDS = {
    "status",
    "evidence_type",
    "score",
    "score_breakdown",
    "matched_keywords",
    "chapter_id",
    "version_id",
    "chapter_num",
    "chapter_title",
    "paragraph_id",
    "paragraph_index",
    "char_start",
    "char_end",
    "paragraph_hash",
    "paragraph_text",
    "l1_backcut_check",
}
DEFAULT_INPUT_RELATIVE_PATH = Path("outputs") / "l3_seed_evidence_candidates.json"
DEFAULT_QUEUE_JSON_RELATIVE_PATH = Path("outputs") / "l3_evidence_review_queue.json"
DEFAULT_QUEUE_MD_RELATIVE_PATH = Path("outputs") / "l3_evidence_review_queue.md"
DEFAULT_QUEUE_CSV_RELATIVE_PATH = Path("outputs") / "l3_evidence_review_queue.csv"
DEFAULT_REPORT_RELATIVE_PATH = Path("outputs") / "l3_evidence_review_verifier_report.md"


def resolve_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def make_paragraph_evidence_key(candidate: dict[str, Any]) -> str:
    return sha256(
        "".join(
            [
                str(candidate["paragraph_id"]),
                str(candidate["char_start"]),
                str(candidate["char_end"]),
                str(candidate["paragraph_hash"]),
            ]
        )
    )


def make_evidence_id(seed_source: dict[str, Any], candidate: dict[str, Any]) -> str:
    return sha256(
        "".join(
            [
                str(seed_source.get("seed_file")),
                str(seed_source.get("item_path")),
                str(candidate["paragraph_id"]),
                str(candidate["char_start"]),
                str(candidate["char_end"]),
                str(candidate["paragraph_hash"]),
            ]
        )
    )


def make_review_id(evidence_id: str) -> str:
    return sha256(f"review:{evidence_id}")


def load_candidate_payload(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise ValueError("candidate input must contain an items array")
    return payload


def validate_candidate(candidate: dict[str, Any], item_path: str) -> None:
    missing = sorted(REQUIRED_CANDIDATE_FIELDS - set(candidate))
    if missing:
        raise ValueError(f"{item_path}: candidate missing required fields: {', '.join(missing)}")
    if candidate["status"] != "candidate":
        raise ValueError(f"{item_path}: input evidence status must be candidate, got {candidate['status']!r}")


def verifier_backcut_check(conn: sqlite3.Connection, candidate: dict[str, Any]) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT content_full_text
        FROM v_current_chapters
        WHERE chapter_id = ?
        """,
        (candidate["chapter_id"],),
    ).fetchone()
    expected_hash = str(candidate["paragraph_hash"])
    if row is None:
        return {
            "checked": True,
            "matched": False,
            "reason": "chapter_not_found",
            "expected_hash": expected_hash,
            "actual_hash": None,
            "cut_length": 0,
        }
    content = row["content_full_text"]
    start = int(candidate["char_start"])
    end = int(candidate["char_end"])
    cut_text = content[start:end]
    actual_hash = sha256(cut_text)
    return {
        "checked": True,
        "matched": actual_hash == expected_hash,
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
        "cut_length": len(cut_text),
    }


def initial_review_flags(candidate: dict[str, Any], backcut: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    score = int(candidate.get("score") or 0)
    matched_keywords = [str(item) for item in candidate.get("matched_keywords", [])]
    paragraph_text = str(candidate.get("paragraph_text") or "")
    if not backcut["matched"]:
        flags.append("invalid_backcut")
    if score < 20:
        flags.append("low_score")
    if not matched_keywords or (len(matched_keywords) == 1 and len(matched_keywords[0]) <= 1):
        flags.append("weak_keyword_match")
    if candidate.get("evidence_type") == "rule_violation_candidate":
        flags.append("rule_violation_candidate")
    if len(paragraph_text.strip()) < 20:
        flags.append("short_paragraph")
    if backcut["matched"] and score >= 50 and len(set(matched_keywords)) >= 2:
        flags.append("high_confidence_candidate")
    return flags


def suggest_status(candidate: dict[str, Any], flags: list[str], backcut: dict[str, Any]) -> str:
    score = int(candidate.get("score") or 0)
    matched_keywords = [str(item) for item in candidate.get("matched_keywords", [])]
    if not backcut["matched"]:
        return "invalid"
    if candidate.get("evidence_type") == "rule_violation_candidate":
        return "needs_review"
    if score >= 50 and len(set(matched_keywords)) >= 2:
        return "likely_relevant"
    if score < 20 or "weak_keyword_match" in flags:
        return "likely_irrelevant"
    return "needs_review"


def review_item_from_candidate(
    conn: sqlite3.Connection,
    seed_source: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    backcut = verifier_backcut_check(conn, candidate)
    flags = initial_review_flags(candidate, backcut)
    evidence_id = make_evidence_id(seed_source, candidate)
    duplicate_group_id = make_paragraph_evidence_key(candidate)
    item = {
        "review_id": make_review_id(evidence_id),
        "evidence_id": evidence_id,
        "duplicate_group_id": duplicate_group_id,
        "seed_source": seed_source,
        "candidate_status": candidate["status"],
        "suggested_status": suggest_status(candidate, flags, backcut),
        "human_status": None,
        "human_reviewer": None,
        "human_note": None,
        "reviewed_at": None,
        "eligible_for_human_confirm": bool(backcut["matched"]),
        "review_flags": flags,
        "score": candidate["score"],
        "score_breakdown": candidate.get("score_breakdown", {}),
        "matched_keywords": candidate.get("matched_keywords", []),
        "chapter_id": candidate["chapter_id"],
        "version_id": candidate["version_id"],
        "chapter_num": candidate["chapter_num"],
        "chapter_title": candidate["chapter_title"],
        "paragraph_id": candidate["paragraph_id"],
        "paragraph_index": candidate["paragraph_index"],
        "char_start": candidate["char_start"],
        "char_end": candidate["char_end"],
        "paragraph_hash": candidate["paragraph_hash"],
        "paragraph_text": candidate["paragraph_text"],
        "context": candidate.get("context", []),
        "verifier_backcut_check": backcut,
    }
    if item["suggested_status"] not in ALLOWED_SUGGESTED_STATUSES:
        raise ValueError(f"unexpected suggested_status: {item['suggested_status']}")
    return item


def collect_review_items(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    top_per_item: int | None,
) -> tuple[list[dict[str, Any]], int, int]:
    review_items: list[dict[str, Any]] = []
    input_candidates = 0
    seed_items = 0
    for item in payload["items"]:
        seed_items += 1
        seed_source = dict(item.get("seed_source") or {})
        candidates = item.get("candidates") or []
        if not isinstance(candidates, list):
            raise ValueError("item candidates must be an array")
        seen_for_seed: set[tuple[str, int, int]] = set()
        kept_for_seed = 0
        for candidate in candidates:
            input_candidates += 1
            validate_candidate(candidate, str(seed_source.get("item_path", "")))
            dedupe_key = (
                str(candidate["paragraph_id"]),
                int(candidate["char_start"]),
                int(candidate["char_end"]),
            )
            if dedupe_key in seen_for_seed:
                continue
            seen_for_seed.add(dedupe_key)
            if top_per_item is not None and kept_for_seed >= top_per_item:
                continue
            review_items.append(review_item_from_candidate(conn, seed_source, candidate))
            kept_for_seed += 1
    review_items.sort(
        key=lambda item: (
            str(item["seed_source"].get("seed_file")),
            str(item["seed_source"].get("item_path")),
            int(item["chapter_num"]),
            int(item["paragraph_index"]),
            str(item["paragraph_id"]),
            str(item["evidence_id"]),
        )
    )
    return review_items, seed_items, input_candidates


def mark_duplicate_groups(review_items: list[dict[str, Any]]) -> int:
    counts: dict[str, int] = {}
    for item in review_items:
        group_id = str(item["duplicate_group_id"])
        counts[group_id] = counts.get(group_id, 0) + 1
    duplicate_groups = sum(1 for count in counts.values() if count > 1)
    for item in review_items:
        if counts[str(item["duplicate_group_id"])] > 1 and "duplicate_paragraph" not in item["review_flags"]:
            item["review_flags"].append("duplicate_paragraph")
    return duplicate_groups


def build_output(input_path: Path, seed_items: int, input_candidates: int, review_items: list[dict[str, Any]], duplicate_groups: int) -> dict[str, Any]:
    valid_backcut = sum(1 for item in review_items if item["verifier_backcut_check"]["matched"])
    invalid_backcut = sum(1 for item in review_items if not item["verifier_backcut_check"]["matched"])
    return {
        "meta": {
            "tool": "l3_evidence_review_verifier",
            "version": TOOL_VERSION,
            "input": str(input_path),
            "status_policy": "finder_status_must_be_candidate; final_human_status_reserved",
            "writes_l1_l2": False,
            "llm_used": False,
            "embedding_used": False,
            "chroma_used": False,
        },
        "summary": {
            "seed_items": seed_items,
            "input_candidates": input_candidates,
            "valid_backcut": valid_backcut,
            "invalid_backcut": invalid_backcut,
            "review_queue_items": len(review_items),
            "duplicate_groups": duplicate_groups,
        },
        "review_items": review_items,
    }


def markdown_queue(output: dict[str, Any]) -> str:
    summary = output["summary"]
    lines = [
        "# L3 Evidence Review Queue",
        "",
        "## Summary",
        "",
        f"- Input candidates: {summary['input_candidates']}",
        f"- Valid backcut: {summary['valid_backcut']}",
        f"- Invalid backcut: {summary['invalid_backcut']}",
        f"- Review queue: {summary['review_queue_items']}",
        "- Final status policy: human only",
        "",
    ]
    for index, item in enumerate(output["review_items"], start=1):
        lines.extend(
            [
                f"## Review Item {index}",
                "",
                f"- Suggested status: {item['suggested_status']}",
                f"- Human status: {item['human_status']}",
                f"- Flags: {', '.join(item['review_flags'])}",
                f"- Seed item: {item['seed_source'].get('item_path')}",
                f"- Score: {item['score']}",
                f"- Matched keywords: {', '.join(str(value) for value in item['matched_keywords'])}",
                f"- Chapter: {item['chapter_num']} {item['chapter_title']}",
                f"- Paragraph: {item['paragraph_index']}",
                f"- Char range: {item['char_start']}..{item['char_end']}",
                f"- Backcut matched: {str(item['verifier_backcut_check']['matched']).lower()}",
                "",
                "Original paragraph:",
                "",
                f"> {item['paragraph_text']}",
                "",
                "Human review fields:",
                "",
                "- confirmed / rejected / uncertain:",
                "- note:",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def verifier_report(output: dict[str, Any]) -> str:
    summary = output["summary"]
    return "\n".join(
        [
            "# L3 Evidence Review Verifier Report",
            "",
            f"- seed_items: {summary['seed_items']}",
            f"- input_candidates: {summary['input_candidates']}",
            f"- valid_backcut: {summary['valid_backcut']}",
            f"- invalid_backcut: {summary['invalid_backcut']}",
            f"- review_queue_items: {summary['review_queue_items']}",
            f"- duplicate_groups: {summary['duplicate_groups']}",
            "- final_status_policy: human only",
            "- writes_l1_l2: false",
            "",
        ]
    )


def write_csv(path: Path, review_items: list[dict[str, Any]]) -> None:
    fields = [
        "review_id",
        "evidence_id",
        "seed_file",
        "item_path",
        "item_id",
        "item_name",
        "candidate_status",
        "suggested_status",
        "human_status",
        "eligible_for_human_confirm",
        "review_flags",
        "score",
        "matched_keywords",
        "chapter_num",
        "chapter_title",
        "paragraph_index",
        "char_start",
        "char_end",
        "paragraph_hash",
        "paragraph_text_preview",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for item in review_items:
            source = item["seed_source"]
            writer.writerow(
                {
                    "review_id": item["review_id"],
                    "evidence_id": item["evidence_id"],
                    "seed_file": source.get("seed_file"),
                    "item_path": source.get("item_path"),
                    "item_id": source.get("item_id"),
                    "item_name": source.get("item_name"),
                    "candidate_status": item["candidate_status"],
                    "suggested_status": item["suggested_status"],
                    "human_status": item["human_status"],
                    "eligible_for_human_confirm": item["eligible_for_human_confirm"],
                    "review_flags": "|".join(item["review_flags"]),
                    "score": item["score"],
                    "matched_keywords": "|".join(str(value) for value in item["matched_keywords"]),
                    "chapter_num": item["chapter_num"],
                    "chapter_title": item["chapter_title"],
                    "paragraph_index": item["paragraph_index"],
                    "char_start": item["char_start"],
                    "char_end": item["char_end"],
                    "paragraph_hash": item["paragraph_hash"],
                    "paragraph_text_preview": str(item["paragraph_text"]).replace("\n", " ")[:160],
                }
            )


def run_l3_evidence_review_verifier(
    project_dir: Path | str | None = None,
    input_path: Path | str | None = None,
    *,
    output_json: Path | str | None = None,
    output_md: Path | str | None = None,
    output_csv: Path | str | None = None,
    report: Path | str | None = None,
    top_per_item: int | None = 3,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    resolved_input = resolve_path(root, input_path, DEFAULT_INPUT_RELATIVE_PATH)
    resolved_json = resolve_path(root, output_json, DEFAULT_QUEUE_JSON_RELATIVE_PATH)
    resolved_md = resolve_path(root, output_md, DEFAULT_QUEUE_MD_RELATIVE_PATH)
    resolved_csv = resolve_path(root, output_csv, DEFAULT_QUEUE_CSV_RELATIVE_PATH)
    resolved_report = resolve_path(root, report, DEFAULT_REPORT_RELATIVE_PATH)
    for path in (resolved_json, resolved_md, resolved_csv, resolved_report):
        path.parent.mkdir(parents=True, exist_ok=True)

    payload = load_candidate_payload(resolved_input)
    conn = sqlite3.connect(root / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        review_items, seed_items, input_candidates = collect_review_items(conn, payload, top_per_item)
    finally:
        conn.close()
    duplicate_groups = mark_duplicate_groups(review_items)
    output = build_output(resolved_input, seed_items, input_candidates, review_items, duplicate_groups)
    resolved_json.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    resolved_md.write_text(markdown_queue(output), encoding="utf-8")
    write_csv(resolved_csv, review_items)
    resolved_report.write_text(verifier_report(output), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a human review queue from L3 seed evidence candidates.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--input", type=Path, default=None, help="Candidate JSON input path.")
    parser.add_argument("--output-json", type=Path, default=None, help="Review queue JSON output path.")
    parser.add_argument("--output-md", type=Path, default=None, help="Review queue Markdown output path.")
    parser.add_argument("--output-csv", type=Path, default=None, help="Review queue CSV output path.")
    parser.add_argument("--report", type=Path, default=None, help="Verifier report Markdown output path.")
    parser.add_argument("--top-per-item", type=int, default=3, help="Max review items retained per seed item after per-item dedupe.")
    args = parser.parse_args()
    output = run_l3_evidence_review_verifier(
        args.project_dir,
        args.input,
        output_json=args.output_json,
        output_md=args.output_md,
        output_csv=args.output_csv,
        report=args.report,
        top_per_item=args.top_per_item,
    )
    summary = output["summary"]
    print(f"L3 evidence review items: {summary['review_queue_items']}")
    print(f"Valid backcut: {summary['valid_backcut']}")
    print(f"Invalid backcut: {summary['invalid_backcut']}")


if __name__ == "__main__":
    main()
