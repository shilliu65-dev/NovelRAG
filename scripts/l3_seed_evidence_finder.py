from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


TOOL_VERSION = "v1"
DEFAULT_JSON_RELATIVE_PATH = Path("outputs") / "l3_seed_evidence_candidates.json"
DEFAULT_MD_RELATIVE_PATH = Path("outputs") / "l3_seed_evidence_report.md"
POSITIVE_FIELDS = ("name", "alias", "aliases", "keywords", "representative_characters", "branches")
RULE_FIELDS = ("forbidden_patterns",)
SEED_MARKER_FIELDS = {
    "id",
    "name",
    "alias",
    "aliases",
    "keywords",
    "godway_id",
    "rule_id",
    "domain_id",
    "lord_id",
    "rank",
}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def safe_flatten(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(safe_flatten(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(safe_flatten(item))
        return out
    return [str(value)]


def extract_seed_items(seed_obj: Any, path: str = "root") -> list[tuple[str, dict[str, Any]]]:
    items: list[tuple[str, dict[str, Any]]] = []
    if isinstance(seed_obj, dict):
        if any(key in seed_obj for key in SEED_MARKER_FIELDS):
            items.append((path, seed_obj))
        for key, value in seed_obj.items():
            items.extend(extract_seed_items(value, f"{path}.{key}"))
    elif isinstance(seed_obj, list):
        for index, value in enumerate(seed_obj):
            items.extend(extract_seed_items(value, f"{path}[{index}]"))
    return items


def normalize_keyword(value: str) -> str | None:
    keyword = value.strip()
    if not keyword:
        return None
    if len(keyword) == 1 and keyword.isascii():
        return None
    return keyword


def unique_keywords(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        keyword = normalize_keyword(value)
        if keyword is None or keyword in seen:
            continue
        seen.add(keyword)
        out.append(keyword)
    return out


def object_keywords(value: Any, preferred_keys: tuple[str, ...]) -> list[str]:
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(object_keywords(item, preferred_keys))
        return out
    if isinstance(value, dict):
        preferred: list[str] = []
        for key in preferred_keys:
            preferred.extend(safe_flatten(value.get(key)))
        return preferred if preferred else safe_flatten(value)
    return safe_flatten(value)


def build_keywords(item: dict[str, Any]) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    rule: list[str] = []
    for field in ("name", "alias", "aliases", "keywords", "branches"):
        positive.extend(safe_flatten(item.get(field)))
    positive.extend(object_keywords(item.get("representative_characters"), ("name",)))
    for field in RULE_FIELDS:
        rule.extend(object_keywords(item.get(field), ("term", "pattern")))
    return unique_keywords(positive), unique_keywords(rule)


def resolve_output_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def row_to_paragraph(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "chapter_id": row["chapter_id"],
        "version_id": row["version_id"],
        "chapter_num": row["chapter_num"],
        "chapter_title": row["chapter_title_current"],
        "paragraph_id": row["para_id"],
        "paragraph_index": row["para_index"],
        "char_start": row["start_offset"],
        "char_end": row["end_offset"],
        "paragraph_hash": row["para_hash"],
        "paragraph_text": row["para_text"],
    }


def query_paragraphs(conn: sqlite3.Connection, keyword: str) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                para_id,
                chapter_id,
                chapter_num,
                version_id,
                para_index,
                start_offset,
                end_offset,
                para_hash,
                para_text,
                chapter_title_current
            FROM v_l2_current_paragraphs
            WHERE para_text LIKE ? ESCAPE '\\'
            """,
            (f"%{escape_like(keyword)}%",),
        )
    )


def query_context(conn: sqlite3.Connection, row: sqlite3.Row, context_paragraphs: int) -> list[dict[str, Any]]:
    if context_paragraphs <= 0:
        return []
    start_index = int(row["para_index"]) - context_paragraphs
    end_index = int(row["para_index"]) + context_paragraphs
    rows = conn.execute(
        """
        SELECT
            para_id,
            chapter_id,
            chapter_num,
            version_id,
            para_index,
            start_offset,
            end_offset,
            para_hash,
            para_text,
            chapter_title_current
        FROM v_l2_current_paragraphs
        WHERE chapter_id = ?
          AND version_id = ?
          AND para_index BETWEEN ? AND ?
        ORDER BY para_index
        """,
        (row["chapter_id"], row["version_id"], start_index, end_index),
    )
    return [row_to_paragraph(item) for item in rows]


def backcut_check(conn: sqlite3.Connection, chapter_id: str, version_id: str, start: int, end: int, expected_hash: str) -> dict[str, Any]:
    row = conn.execute(
        """
        SELECT content_full_text
        FROM v_current_chapters
        WHERE chapter_id = ?
          AND latest_version_id = ?
        """,
        (chapter_id, version_id),
    ).fetchone()
    if row is None:
        return {
            "checked": True,
            "matched": False,
            "reason": "chapter_not_found",
            "expected_hash": expected_hash,
            "actual_hash": None,
        }
    cut_text = row["content_full_text"][start:end]
    actual_hash = sha256(cut_text)
    return {
        "checked": True,
        "matched": actual_hash == expected_hash,
        "cut_length": len(cut_text),
        "expected_hash": expected_hash,
        "actual_hash": actual_hash,
    }


def field_hits(item: dict[str, Any], text: str) -> tuple[dict[str, int], list[str], list[str], set[str]]:
    breakdown: dict[str, int] = {}
    categories: set[str] = set()
    positive_keywords, rule_keywords = build_keywords(item)
    matched_positive = [keyword for keyword in positive_keywords if keyword in text]
    matched_rule = [keyword for keyword in rule_keywords if keyword in text]

    name = normalize_keyword(str(item.get("name", "")))
    if name and name in text:
        breakdown["name_hit"] = 30
        categories.add("name")

    aliases = unique_keywords(safe_flatten(item.get("alias")) + safe_flatten(item.get("aliases")))
    alias_hits = [keyword for keyword in aliases if keyword in text]
    if alias_hits:
        breakdown["alias_hit"] = 25
        categories.add("alias")

    characters = unique_keywords(safe_flatten(item.get("representative_characters")))
    character_hits = [keyword for keyword in characters if keyword in text]
    if character_hits:
        breakdown["character_hit"] = 15
        categories.add("character")

    branches = unique_keywords(safe_flatten(item.get("branches")))
    branch_hits = [keyword for keyword in branches if keyword in text]
    if branch_hits:
        breakdown["branch_hit"] = 15
        categories.add("branch")

    keyword_values = unique_keywords(safe_flatten(item.get("keywords")))
    keyword_hits = [keyword for keyword in keyword_values if keyword in text]
    if keyword_hits:
        breakdown["keyword_hits"] = len(keyword_hits) * 10
        categories.add("keyword")

    if matched_rule:
        breakdown["rule_keyword_hits"] = len(matched_rule) * 8
        categories.add("rule")

    distinct_count = len(set(matched_positive + matched_rule))
    if distinct_count:
        breakdown["distinct_keyword_bonus"] = distinct_count * 3
    if len(categories) > 1:
        breakdown["multi_field_bonus"] = 5
    if len(text.strip()) < 20:
        breakdown["short_paragraph_penalty"] = -5

    return breakdown, matched_positive, matched_rule, categories


def score_candidate(item: dict[str, Any], text: str) -> tuple[int, dict[str, int], list[str], list[str], str]:
    breakdown, matched_positive, matched_rule, _ = field_hits(item, text)
    score = sum(breakdown.values())
    evidence_type = "rule_violation_candidate" if matched_rule else "positive_candidate"
    return score, breakdown, matched_positive, matched_rule, evidence_type


def build_candidate(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    row: sqlite3.Row,
    context_paragraphs: int,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    paragraph = row_to_paragraph(row)
    score, breakdown, matched_positive, matched_rule, evidence_type = score_candidate(item, paragraph["paragraph_text"])
    check = backcut_check(
        conn,
        paragraph["chapter_id"],
        paragraph["version_id"],
        int(paragraph["char_start"]),
        int(paragraph["char_end"]),
        paragraph["paragraph_hash"],
    )
    if not check["matched"]:
        return None, {
            "paragraph_id": paragraph["paragraph_id"],
            "chapter_id": paragraph["chapter_id"],
            "reason": check.get("reason", "hash_mismatch"),
            "l1_backcut_check": check,
        }
    candidate = {
        "status": "candidate",
        "evidence_type": evidence_type,
        "score": score,
        "score_breakdown": breakdown,
        "matched_keywords": sorted(set(matched_positive + matched_rule)),
        **paragraph,
        "context": query_context(conn, row, context_paragraphs),
        "l1_backcut_check": check,
    }
    return candidate, None


def load_seed(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_json_output(
    seed_path: Path,
    top_k: int,
    context_paragraphs: int,
    items: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "meta": {
            "tool": "l3_seed_evidence_finder",
            "version": TOOL_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "seed_file": str(seed_path),
            "top_k": top_k,
            "context_paragraphs": context_paragraphs,
            "read_views": ["v_l2_current_paragraphs", "v_current_chapters"],
            "writes_l1_l2": False,
            "status_policy": "candidate only",
            "backcut_rejected_count": len(rejected),
            "backcut_rejected": rejected,
        },
        "items": items,
    }


def markdown_report(output: dict[str, Any]) -> str:
    meta = output["meta"]
    lines = [
        "# L3 Seed Evidence Candidates Report",
        "",
        "## Meta",
        "",
        f"- Seed file: {meta['seed_file']}",
        f"- Top K: {meta['top_k']}",
        f"- Context paragraphs: {meta['context_paragraphs']}",
        "- Status policy: candidate only",
        "- L1/L2 mutation: none",
        f"- Backcut rejected count: {meta['backcut_rejected_count']}",
        "",
    ]
    for item in output["items"]:
        source = item["seed_source"]
        lines.extend(
            [
                f"## Seed Item: {source.get('item_name') or source.get('item_id') or source['item_path']}",
                "",
                f"- Item path: {source['item_path']}",
                f"- Item id: {source.get('item_id')}",
                f"- Keywords: {', '.join(item['keyword_set'])}",
                "",
            ]
        )
        if not item["candidates"]:
            lines.extend(["- No candidates", ""])
            continue
        for index, candidate in enumerate(item["candidates"], start=1):
            before = [ctx for ctx in candidate["context"] if ctx["paragraph_index"] < candidate["paragraph_index"]]
            after = [ctx for ctx in candidate["context"] if ctx["paragraph_index"] > candidate["paragraph_index"]]
            lines.extend(
                [
                    f"### Candidate {index}",
                    "",
                    f"- Status: {candidate['status']}",
                    f"- Evidence type: {candidate['evidence_type']}",
                    f"- Score: {candidate['score']}",
                    f"- Matched keywords: {', '.join(candidate['matched_keywords'])}",
                    f"- Chapter: {candidate['chapter_num']} {candidate['chapter_title']}",
                    f"- Paragraph: {candidate['paragraph_index']}",
                    f"- Char range: {candidate['char_start']}..{candidate['char_end']}",
                    f"- Hash: {candidate['paragraph_hash']}",
                    f"- L1 backcut matched: {str(candidate['l1_backcut_check']['matched']).lower()}",
                    "",
                    "Original paragraph:",
                    "",
                    f"> {candidate['paragraph_text']}",
                    "",
                    "Context:",
                    "",
                ]
            )
            if before:
                lines.extend(["Before:", ""])
                lines.extend(f"> {ctx['paragraph_text']}" for ctx in before)
                lines.append("")
            if after:
                lines.extend(["After:", ""])
                lines.extend(f"> {ctx['paragraph_text']}" for ctx in after)
                lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_l3_seed_evidence_finder(
    project_dir: Path | str | None = None,
    seed: Path | str | None = None,
    *,
    top_k: int = 30,
    context_paragraphs: int = 1,
    output_json: Path | str | None = None,
    output_md: Path | str | None = None,
) -> dict[str, Any]:
    if seed is None:
        raise ValueError("seed path is required")
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    seed_path = Path(seed)
    if not seed_path.is_absolute():
        seed_path = root / seed_path
    seed_path = seed_path.resolve()
    json_path = resolve_output_path(root, output_json, DEFAULT_JSON_RELATIVE_PATH)
    md_path = resolve_output_path(root, output_md, DEFAULT_MD_RELATIVE_PATH)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(root / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    rejected: list[dict[str, Any]] = []
    output_items: list[dict[str, Any]] = []
    try:
        seed_obj = load_seed(seed_path)
        for item_path, item in extract_seed_items(seed_obj):
            positive_keywords, rule_keywords = build_keywords(item)
            keyword_set = unique_keywords(positive_keywords + rule_keywords)
            rows_by_id: dict[str, sqlite3.Row] = {}
            for keyword in keyword_set:
                for row in query_paragraphs(conn, keyword):
                    rows_by_id.setdefault(row["para_id"], row)
            candidates: list[dict[str, Any]] = []
            for row in rows_by_id.values():
                candidate, reject = build_candidate(conn, item, row, context_paragraphs)
                if reject is not None:
                    rejected.append(
                        {
                            "seed_item_path": item_path,
                            **reject,
                        }
                    )
                if candidate is not None:
                    candidates.append(candidate)
            candidates.sort(
                key=lambda value: (
                    -int(value["score"]),
                    int(value["chapter_num"]),
                    int(value["paragraph_index"]),
                    str(value["paragraph_id"]),
                )
            )
            output_items.append(
                {
                    "seed_source": {
                        "seed_file": str(seed_path),
                        "item_path": item_path,
                        "item_id": item.get("id") or item.get("godway_id") or item.get("rule_id") or item.get("domain_id") or item.get("lord_id"),
                        "item_name": item.get("name"),
                    },
                    "keyword_set": keyword_set,
                    "positive_keywords": positive_keywords,
                    "rule_keywords": rule_keywords,
                    "candidates": candidates[:top_k],
                }
            )
    finally:
        conn.close()

    output = build_json_output(seed_path, top_k, context_paragraphs, output_items, rejected)
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(output), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Find deterministic L3 seed evidence candidates from L2 paragraphs.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--seed", type=Path, required=True, help="Seed JSON file path.")
    parser.add_argument("--top-k", type=int, default=30, help="Max candidates per seed item.")
    parser.add_argument("--context-paragraphs", type=int, default=1, help="Neighbor paragraphs to include from the same chapter.")
    parser.add_argument("--output-json", type=Path, default=None, help="JSON output path.")
    parser.add_argument("--output-md", type=Path, default=None, help="Markdown report path.")
    args = parser.parse_args()
    output = run_l3_seed_evidence_finder(
        args.project_dir,
        args.seed,
        top_k=args.top_k,
        context_paragraphs=args.context_paragraphs,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    candidate_count = sum(len(item["candidates"]) for item in output["items"])
    print(f"L3 seed evidence candidates: {candidate_count}")
    print(f"Backcut rejected: {output['meta']['backcut_rejected_count']}")


if __name__ == "__main__":
    main()
