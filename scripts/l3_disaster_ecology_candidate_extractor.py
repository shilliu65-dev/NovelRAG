from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


DEFAULT_SEED = Path("config") / "l3_disaster_ecology_seed.json"
DEFAULT_OUTPUT_JSON = Path("outputs") / "l3_disaster_ecology_candidates_sample.json"
DEFAULT_REVIEW_TEMPLATE = Path("outputs") / "l3_disaster_ecology_review_template_sample.json"
DEFAULT_REPORT_MD = Path("outputs") / "l3_disaster_ecology_candidates_sample_report.md"
READ_VIEWS = ["v_current_chapters", "v_l2_current_paragraphs", "v_l2_current_sentences"]


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_disaster_seed(seed_file: str | Path) -> dict[str, Any]:
    path = Path(seed_file)
    if not path.exists() and path.name == DEFAULT_SEED.name:
        path = Path(__file__).resolve().parents[1] / DEFAULT_SEED
    return json.loads(path.read_text(encoding="utf-8"))


def parse_sample_chapters(value: str | None) -> list[int]:
    if not value:
        return [1, 2, 1697]
    return [int(item.strip()) for item in value.split(",") if item.strip()]


def rows_for_chapters(conn: sqlite3.Connection, chapter_nums: list[int] | None) -> list[sqlite3.Row]:
    where = ""
    params: list[Any] = []
    if chapter_nums is not None:
        where = "WHERE chapter_num IN (%s)" % ",".join("?" for _ in chapter_nums)
        params.extend(chapter_nums)
    return list(
        conn.execute(
            f"""
            SELECT sentence_id, para_id, chapter_id, chapter_num, version_id,
                   start_offset, end_offset, sentence_text, chapter_title_current
            FROM v_l2_current_sentences
            {where}
            ORDER BY chapter_num, global_sentence_index, sentence_id
            """,
            params,
        )
    )


def has_negative(text: str, patterns: list[str]) -> bool:
    return any(pattern and pattern in text for pattern in patterns)


def candidate_id(prefix: str, *parts: Any) -> str:
    raw = "|".join("" if part is None else str(part) for part in parts)
    return f"{prefix}_{sha256_text(raw)[:16]}"


def base_candidate(candidate_type: str, row: sqlite3.Row, evidence_text: str, start: int, end: int, matched_pattern: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id(candidate_type, row["chapter_id"], row["sentence_id"], start, end, evidence_text, matched_pattern),
        "candidate_type": candidate_type,
        "chapter_id": row["chapter_id"],
        "chapter_num": int(row["chapter_num"]),
        "paragraph_id": row["para_id"],
        "sentence_id": row["sentence_id"],
        "para_start_offset": start,
        "para_end_offset": end,
        "sent_start_offset": start,
        "sent_end_offset": end,
        "evidence_text": evidence_text,
        "context_before": "",
        "context_after": "",
        "matched_pattern": matched_pattern,
        "score": 1.0,
        "status": "candidate",
        "needs_human_review": True,
    }


def iter_matches(text: str, pattern: str) -> list[re.Match[str]]:
    return list(re.finditer(re.escape(pattern), text))


def extract_zone_candidates(seed: dict[str, Any], row: sqlite3.Row) -> list[dict[str, Any]]:
    text = row["sentence_text"]
    candidates: list[dict[str, Any]] = []
    for zone in seed["zone_defs"]:
        if has_negative(text, zone.get("negative_patterns", []) + seed.get("global_negative_patterns", [])):
            continue
        for pattern in zone.get("positive_patterns", []):
            if pattern == "灰":
                continue
            for match in iter_matches(text, pattern):
                item = base_candidate("zone_appearance_candidate", row, match.group(0), match.start(), match.end(), pattern)
                item.update(
                    {
                        "zone_id": zone["zone_id"],
                        "zone_name": zone["zone_name"],
                        "zone_type": zone["zone_type"],
                        "alias_text": match.group(0),
                        "appearance_type_guess": "direct_mention",
                    }
                )
                candidates.append(item)
    return candidates


def extract_disaster_candidates(seed: dict[str, Any], row: sqlite3.Row) -> list[dict[str, Any]]:
    text = row["sentence_text"]
    candidates: list[dict[str, Any]] = []
    for disaster in seed["disaster_defs"]:
        for pattern in disaster.get("positive_patterns", []):
            for match in iter_matches(text, pattern):
                item = base_candidate("disaster_appearance_candidate", row, match.group(0), match.start(), match.end(), pattern)
                item.update(
                    {
                        "disaster_id": disaster["disaster_id"],
                        "disaster_name": disaster["disaster_name"],
                        "alias_text": match.group(0),
                        "appearance_type_guess": "direct_mention",
                    }
                )
                candidates.append(item)
    return candidates


def extract_habitat_candidates(seed: dict[str, Any], row: sqlite3.Row) -> list[dict[str, Any]]:
    text = row["sentence_text"]
    candidates: list[dict[str, Any]] = []
    habitat_words = [word for words in seed["habitat_patterns"].values() for word in words]
    for disaster in seed["disaster_defs"]:
        if not any(alias in text for alias in disaster.get("positive_patterns", [])):
            continue
        habitat = next(item for item in seed["habitat_defs"] if item["disaster_id"] == disaster["disaster_id"])
        for word in habitat_words:
            for match in iter_matches(text, word):
                item = base_candidate("habitat_appearance_candidate", row, match.group(0), match.start(), match.end(), word)
                item.update(
                    {
                        "habitat_id": habitat["habitat_id"],
                        "disaster_id": disaster["disaster_id"],
                        "disaster_name": disaster["disaster_name"],
                        "habitat_text": match.group(0),
                        "habitat_type_guess": habitat["habitat_type"],
                        "appearance_type_guess": "habitat_description",
                    }
                )
                candidates.append(item)
    return candidates


def first_present(text: str, values: list[str]) -> str | None:
    found = [(text.find(value), value) for value in values if value and text.find(value) >= 0]
    if not found:
        return None
    return min(found, key=lambda item: item[0])[1]


def extract_invasion_candidates(seed: dict[str, Any], row: sqlite3.Row) -> list[dict[str, Any]]:
    text = row["sentence_text"]
    actor_zone = None
    actor_zone_id = None
    for zone in seed["zone_defs"]:
        actor_zone = first_present(text, [p for p in zone.get("positive_patterns", []) if p != "灰"])
        if actor_zone:
            actor_zone_id = zone["zone_id"]
            break
    actor_disaster = None
    actor_disaster_id = None
    for disaster in seed["disaster_defs"]:
        actor_disaster = first_present(text, disaster.get("positive_patterns", []))
        if actor_disaster:
            actor_disaster_id = disaster["disaster_id"]
            break
    actor = actor_disaster or actor_zone
    if not actor:
        return []
    invasion_type = None
    invasion_word = None
    for kind, words in seed["invasion_patterns"].items():
        invasion_word = first_present(text, words)
        if invasion_word:
            invasion_type = kind
            break
    if not invasion_type or not invasion_word:
        return []
    affected = first_present(text, seed["affected_location_triggers"]) or "unknown"
    start = text.find(invasion_word)
    end = start + len(invasion_word)
    item = base_candidate("disaster_invasion_candidate", row, invasion_word, start, end, invasion_word)
    item.update(
        {
            "source_disaster_id": actor_disaster_id,
            "source_zone_id": actor_zone_id,
            "invasion_actor_text": actor,
            "affected_location_text": affected,
            "affected_location_type_guess": "unknown",
            "affected_l35_location_id": None,
            "matched_disaster_pattern": actor_disaster,
            "matched_zone_pattern": actor_zone,
            "matched_invasion_pattern": invasion_word,
            "invasion_type_guess": invasion_type,
            "invasion_state_guess": "spreading" if invasion_type in {"erosion", "expansion", "pollution", "coverage"} else "unknown",
            "area_change_text": None,
            "boundary_change_text": None,
            "pollution_effect_text": invasion_word if invasion_type == "pollution" else None,
        }
    )
    if affected == "unknown":
        item["score"] = 0.5
    return [item]


def add_review_fields(candidate: dict[str, Any]) -> dict[str, Any]:
    item = dict(candidate)
    item["human_status"] = None
    item["human_note"] = None
    ctype = item["candidate_type"]
    if ctype == "zone_appearance_candidate":
        item.update({"human_zone_id": item.get("zone_id"), "human_zone_type": item.get("zone_type"), "human_appearance_type": item.get("appearance_type_guess")})
    elif ctype == "disaster_appearance_candidate":
        item.update({"human_disaster_id": item.get("disaster_id"), "human_appearance_type": item.get("appearance_type_guess")})
    elif ctype == "habitat_appearance_candidate":
        item.update({"human_habitat_id": item.get("habitat_id"), "human_disaster_id": item.get("disaster_id"), "human_habitat_text": item.get("habitat_text"), "human_habitat_type": item.get("habitat_type_guess"), "human_appearance_type": item.get("appearance_type_guess")})
    elif ctype == "disaster_invasion_candidate":
        item.update({"human_source_disaster_id": item.get("source_disaster_id"), "human_source_zone_id": item.get("source_zone_id"), "human_invasion_actor_text": item.get("invasion_actor_text"), "human_affected_location_text": item.get("affected_location_text"), "human_affected_l35_location_id": item.get("affected_l35_location_id"), "human_invasion_type": item.get("invasion_type_guess"), "human_invasion_state": item.get("invasion_state_guess")})
    return item


def build_review_template(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "meta": {
            "layer": "L3.6",
            "review_type": "disaster_ecology_review",
            "allowed_human_status": [None, "accepted", "rejected", "needs_more"],
        },
        "review_candidates": [add_review_fields(candidate) for candidate in candidates],
    }


def markdown_report(payload: dict[str, Any]) -> str:
    counts = defaultdict(int)
    for candidate in payload["candidates"]:
        counts[candidate["candidate_type"]] += 1
    lines = [
        "# L3.6 Disaster Ecology Candidates Sample Report",
        "",
        f"- Generated at: {payload['meta']['generated_at']}",
        f"- Total candidates: {len(payload['candidates'])}",
        "- Writes SQLite: false",
        "- LLM: false",
        "- Embedding: false",
        "- Chroma: false",
        "",
        "## Counts",
        "",
    ]
    for key in ("zone_appearance_candidate", "disaster_appearance_candidate", "habitat_appearance_candidate", "disaster_invasion_candidate"):
        lines.append(f"- {key}: {counts[key]}")
    return "\n".join(lines) + "\n"


def run_l3_disaster_ecology_candidate_extractor(
    project_dir: str | Path | None = None,
    *,
    sample_chapters: str | None = "1,2,1697",
    all_current_chapters: bool = False,
    seed_file: str | Path | None = None,
    output_json: str | Path | None = None,
    emit_review_template: bool = False,
    top_k_per_type: int = 30,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    seed_path = resolve_path(root, seed_file, DEFAULT_SEED)
    seed = load_disaster_seed(seed_path)
    chapter_nums = None if all_current_chapters else parse_sample_chapters(sample_chapters)
    conn = sqlite3.connect(root / DB_RELATIVE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = rows_for_chapters(conn, chapter_nums)
    finally:
        conn.close()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        for candidate in (
            extract_zone_candidates(seed, row)
            + extract_disaster_candidates(seed, row)
            + extract_habitat_candidates(seed, row)
            + extract_invasion_candidates(seed, row)
        ):
            grouped[candidate["candidate_type"]].append(candidate)
    candidates: list[dict[str, Any]] = []
    for ctype in ("zone_appearance_candidate", "disaster_appearance_candidate", "habitat_appearance_candidate", "disaster_invasion_candidate"):
        candidates.extend(grouped[ctype][:top_k_per_type])
    payload = {
        "meta": {
            "layer": "L3.6",
            "task": "disaster_ecology_candidate_extraction",
            "project_dir": str(root),
            "seed_version": seed["seed_version"],
            "strict_no_llm": True,
            "strict_no_embedding": True,
            "strict_no_chroma": True,
            "source_views": READ_VIEWS,
            "candidate_types": ["zone_appearance_candidate", "disaster_appearance_candidate", "habitat_appearance_candidate", "disaster_invasion_candidate"],
            "writes_sqlite": False,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        },
        "candidates": candidates,
    }
    out_path = resolve_path(root, output_json, DEFAULT_OUTPUT_JSON)
    write_json(out_path, payload)
    report_path = root / DEFAULT_REPORT_MD
    report_path.write_text(markdown_report(payload), encoding="utf-8")
    if emit_review_template:
        write_json(root / DEFAULT_REVIEW_TEMPLATE, build_review_template(candidates))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract deterministic L3.6 disaster ecology candidates.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--sample-chapters", default="1,2,1697")
    parser.add_argument("--all-current-chapters", action="store_true")
    parser.add_argument("--seed-file", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--emit-review-template", action="store_true")
    parser.add_argument("--top-k-per-type", type=int, default=30)
    args = parser.parse_args()
    payload = run_l3_disaster_ecology_candidate_extractor(
        project_dir=args.project_dir,
        sample_chapters=args.sample_chapters,
        all_current_chapters=args.all_current_chapters,
        seed_file=args.seed_file,
        output_json=args.output_json,
        emit_review_template=args.emit_review_template,
        top_k_per_type=args.top_k_per_type,
    )
    print(f"L3.6 candidates: {len(payload['candidates'])}")


if __name__ == "__main__":
    main()
