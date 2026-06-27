from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_extractor import discover_scene_block_source, scene_block_source_payload
from scripts.l5_event_candidate_review_exporter import EVENT_COLUMNS, EVENT_CSV, EVENT_JSON, MANIFEST_JSON, json_cell, object_exists, table_columns


EXPORT_CREATED_AT = "1970-01-01T00:00:00"
ENHANCED_CSV = "l5_event_candidate_review_enhanced.csv"
ENHANCED_JSON = "l5_event_candidate_review_enhanced.json"
QUALITY_REPORT = "l5_event_argument_quality_report.md"
QUALITY_MANIFEST = "l5_event_argument_quality_manifest.json"

ENHANCED_COLUMNS = [
    "enhanced_subject_candidates_json",
    "enhanced_object_candidates_json",
    "enhanced_location_candidates_json",
    "enhanced_time_hint_candidates_json",
    "enhanced_argument_candidates_json",
    "enhanced_subject_source_rule",
    "enhanced_subject_source_kind",
    "enhanced_subject_confidence",
    "enhanced_subject_evidence_text",
    "enhanced_subject_evidence_span_json",
    "enhanced_argument_count",
    "enhanced_subject_candidate_added",
    "enhanced_warning_flags_json",
    "enhanced_quality_score",
    "enhanced_review_recommendation",
    "enhanced_notes",
]

ENHANCED_ALL_COLUMNS = EVENT_COLUMNS + ENHANCED_COLUMNS
SUBJECT_LIKE_ROLES = {"subject", "agent", "speaker", "mover", "discoverer", "attacker", "defender", "actor", "performer", "observer"}
SPEECH_TRIGGERS = {"said", "asked", "answered", "shouted", "whispered", "说道", "说", "问", "喊", "开口", "回答", "冷笑"}
PRONOUNS = {"he", "she", "they", "him", "her", "他", "她", "它", "他们", "她们", "对方"}
SOURCE_TABLES = (
    "l5_event_candidate",
    "l5_event_argument_candidate",
    "l5_event_state_change_candidate",
    "l5_event_evidence_span",
    "l5_event_extraction_run",
    "l3_character_appearance",
    "l3_character_alias",
    "l3_character_candidate",
    "l3_character_identity",
    "l3_character_def",
    "l4_scene_blocks",
    "l3_scene_blocks",
    "scene_blocks",
)
FUTURE_TABLES = {"l5_normalized_event", "l5_normalized_state_change", "l5_event_merge_group", "confirmed_event"}
CHARACTER_STOPLIST = {
    "the",
    "this",
    "that",
    "room",
    "door",
    "view",
    "chapter",
    "he",
    "she",
    "they",
    "him",
    "her",
    "他们",
    "我们",
    "你们",
    "众人",
    "所有人",
    "男人",
    "女人",
    "少年",
    "少女",
    "老人",
    "孩子",
    "灾厄",
    "怪物",
    "声音",
    "目光",
    "身体",
    "空气",
    "世界",
}
RECOMMENDATIONS = {"review_candidate", "needs_context", "weak_candidate", "likely_duplicate", "ready_for_l5_3_candidate"}


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def write_json(path: Path, project_dir: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    payload = {
        "export_name": "l5_event_candidate_review_enhanced",
        "project_dir": str(project_dir),
        "created_at": EXPORT_CREATED_AT,
        "row_count": len(rows),
        "columns": columns,
        "rows": [{column: row.get(column, "") for column in columns} for row in rows],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    return sha256_text(path.read_text(encoding="utf-8-sig"))


def source_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for table in SOURCE_TABLES:
        if not object_exists(conn, table, "table"):
            continue
        columns = table_columns(conn, table)
        if not columns:
            continue
        order_col = columns[0]
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_col}")]
        result[table] = {
            "row_count": len(rows),
            "key_column": order_col,
            "aggregate_hash": sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        }
    return result


def load_l5_arguments(conn: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    if not object_exists(conn, "l5_event_argument_candidate", "table"):
        return grouped
    for row in conn.execute("SELECT * FROM l5_event_argument_candidate ORDER BY event_candidate_id, argument_id"):
        grouped[str(row["event_candidate_id"])].append(row)
    return grouped


def detect_character_sources(conn: sqlite3.Connection) -> dict[str, Any]:
    names = ("l3_character_appearance", "l3_character_alias", "l3_character_candidate", "l3_character_identity", "l3_character_def")
    return {name: {"exists": object_exists(conn, name, "table"), "columns": table_columns(conn, name) if object_exists(conn, name, "table") else []} for name in names}


def l5_2_seed_status(project_dir: Path) -> dict[str, Any]:
    path = project_dir / "config" / "event_normalization_rules.seed.json"
    if not path.exists():
        return {"path": "config/event_normalization_rules.seed.json", "exists": False, "seed_checksum": ""}
    return {"path": "config/event_normalization_rules.seed.json", "exists": True, "seed_checksum": sha256_text(path.read_text(encoding="utf-8"))}


def optional_source_status(conn: sqlite3.Connection) -> dict[str, Any]:
    names = ("l3_character_appearance", "l3_character_alias", "l3_character_candidate", "l3_character_identity", "l3_character_def", "l4_scene_blocks", "l3_scene_blocks", "scene_blocks")
    return {name: {"exists": object_exists(conn, name, "table")} for name in names}


def parse_json_array(value: str) -> list[Any]:
    try:
        parsed = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    return parsed if isinstance(parsed, list) else []


def candidate_text(candidate: dict[str, Any]) -> str:
    return str(candidate.get("text") or candidate.get("entity_text") or candidate.get("matched_text") or "")


def make_subject_candidate(
    row: dict[str, str],
    *,
    text: str,
    source_rule: str,
    source_kind: str,
    confidence: float,
    evidence_text: str,
    start: int | str = "",
    end: int | str = "",
    source_character_id_candidate: str = "",
    notes: str = "",
) -> dict[str, Any]:
    span = {
        "chapter_num": int(row.get("chapter_num") or 0),
        "sentence_id": row.get("evidence_l2_sentence_id", ""),
        "start_offset": start,
        "end_offset": end,
    }
    candidate = {
        "text": text,
        "role": "subject",
        "entity_kind": "character" if text else "unknown",
        "source_rule": source_rule,
        "source_kind": source_kind,
        "confidence": round(float(confidence), 2),
        "evidence_text": evidence_text,
        "evidence_span": span,
        "is_confirmed": False,
        "notes": notes,
    }
    if source_character_id_candidate:
        candidate["source_character_id_candidate"] = source_character_id_candidate
    return candidate


def named_candidates_before(text: str, trigger_text: str, argument_texts: list[str]) -> list[tuple[str, int, int]]:
    trigger_index = text.find(trigger_text) if trigger_text else -1
    before = text if trigger_index < 0 else text[:trigger_index]
    candidates: list[tuple[str, int, int]] = []
    for argument in argument_texts:
        if not argument or argument.lower() in CHARACTER_STOPLIST:
            continue
        index = before.rfind(argument)
        if index >= 0:
            candidates.append((argument, index, index + len(argument)))
    for match in re.finditer(r"\b[A-Z][a-z]{1,32}\b", before):
        value = match.group(0)
        if value.lower() not in CHARACTER_STOPLIST:
            candidates.append((value, match.start(), match.end()))
    for match in re.finditer(r"[\u4e00-\u9fff]{2,4}", before):
        value = match.group(0)
        if value not in CHARACTER_STOPLIST and not value.startswith("第"):
            candidates.append((value, match.start(), match.end()))
    deduped = {(text_value, start, end) for text_value, start, end in candidates}
    return sorted(deduped, key=lambda item: (item[1], item[2]))


def previous_sentence_text(conn: sqlite3.Connection, row: dict[str, str]) -> str:
    if not object_exists(conn, "v_l2_current_sentences", "view"):
        return ""
    try:
        current_start = int(row.get("evidence_start_offset") or 0)
    except ValueError:
        return ""
    sentence_id = row.get("evidence_l2_sentence_id", "")
    para_id = row.get("evidence_l2_paragraph_id", "")
    version_id = row.get("version_id", "")
    if not sentence_id or not version_id:
        return ""
    sql = """
        SELECT sentence_text
        FROM v_l2_current_sentences
        WHERE version_id = ?
          AND (? = '' OR para_id = ?)
          AND end_offset <= ?
          AND sentence_id != ?
        ORDER BY end_offset DESC
        LIMIT 1
    """
    found = conn.execute(sql, (version_id, para_id, para_id, current_start, sentence_id)).fetchone()
    return "" if found is None else str(found["sentence_text"])


def choose_subject(conn: sqlite3.Connection, row: dict[str, str], arguments: list[sqlite3.Row]) -> tuple[list[dict[str, Any]], str, str, float, str, dict[str, Any], list[str]]:
    original_subjects = [item for item in parse_json_array(row.get("subject_candidates_json", "")) if isinstance(item, dict)]
    evidence_text = row.get("evidence_text_backcut", "")
    warnings: list[str] = []
    if original_subjects:
        subject = original_subjects[0]
        text = candidate_text(subject)
        candidate = make_subject_candidate(row, text=text, source_rule="existing_subject_candidate", source_kind="l5_review_export", confidence=0.9, evidence_text=evidence_text)
        return [candidate], "existing_subject_candidate", "l5_review_export", 0.9, evidence_text, candidate["evidence_span"], warnings

    for argument in arguments:
        role = str(argument["argument_role"])
        if role in SUBJECT_LIKE_ROLES and argument["entity_text"]:
            rule = f"l5_argument_{role}" if role in {"agent", "speaker"} else "l5_argument_subject"
            candidate = make_subject_candidate(
                row,
                text=str(argument["entity_text"]),
                source_rule=rule,
                source_kind="l5_argument_candidate",
                confidence=0.9 if role == "subject" else 0.82,
                evidence_text=evidence_text,
                source_character_id_candidate=str(argument["entity_id"] or ""),
            )
            return [candidate], rule, "l5_argument_candidate", float(candidate["confidence"]), evidence_text, candidate["evidence_span"], warnings

    argument_texts = [str(argument["entity_text"]) for argument in arguments if argument["entity_text"]]
    trigger_text = row.get("trigger_text", "")
    first_word = re.match(r"\s*([A-Za-z\u4e00-\u9fff]+)", evidence_text)
    if first_word and first_word.group(1).lower() in PRONOUNS:
        previous = previous_sentence_text(conn, row)
        previous_named = named_candidates_before(previous + " " + trigger_text, trigger_text, argument_texts)
        if previous_named:
            text, start, end = previous_named[-1]
            candidate = make_subject_candidate(row, text=text, source_rule="pronoun_back_reference", source_kind="l2_paragraph_context", confidence=0.45, evidence_text=previous, start=start, end=end)
            warnings.extend(["enhanced_subject_from_weak_rule", "enhanced_subject_pronoun_only", "enhanced_subject_low_confidence"])
            return [candidate], "pronoun_back_reference", "l2_paragraph_context", 0.45, previous, candidate["evidence_span"], warnings

    named = named_candidates_before(evidence_text, trigger_text, argument_texts)
    if trigger_text in SPEECH_TRIGGERS and named:
        text, start, end = named[-1]
        candidate = make_subject_candidate(row, text=text, source_rule="quoted_speech_speaker_pattern", source_kind="rule_pattern", confidence=0.82, evidence_text=evidence_text, start=start, end=end)
        return [candidate], "quoted_speech_speaker_pattern", "rule_pattern", 0.82, evidence_text, candidate["evidence_span"], warnings
    if named:
        text, start, end = named[-1]
        rule = "nearest_character_before_trigger"
        candidate = make_subject_candidate(row, text=text, source_rule=rule, source_kind="l2_sentence_context", confidence=0.72, evidence_text=evidence_text, start=start, end=end)
        return [candidate], rule, "l2_sentence_context", 0.72, evidence_text, candidate["evidence_span"], warnings

    warnings.append("enhanced_subject_not_found")
    return [], "not_found", "not_found", 0.0, "", {}, warnings


def enhanced_quality_score(row: dict[str, str], subject_confidence: float, has_subject: bool, warnings: list[str]) -> float:
    try:
        score = float(row.get("confidence_score") or 0.5)
    except ValueError:
        score = 0.5
    if has_subject:
        score += 0.15
    if row.get("evidence_backcut_status") == "ok":
        score += 0.05
    if row.get("scene_block_validity") == "valid":
        score += 0.05
    if has_subject and subject_confidence < 0.6:
        score -= 0.10
    if row.get("evidence_backcut_status") != "ok":
        score -= 0.15
    if "duplicate_event_same_evidence" in warnings:
        score -= 0.10
    return max(0.0, min(1.0, round(score, 2)))


def recommendation(row: dict[str, str], subject_confidence: float, has_subject: bool, warnings: list[str]) -> str:
    if "duplicate_event_same_evidence" in warnings or "duplicate_trigger_same_sentence" in warnings:
        return "likely_duplicate"
    if not has_subject or subject_confidence < 0.5:
        return "needs_context"
    if "weak_candidate" in warnings or "enhanced_subject_from_weak_rule" in warnings:
        return "weak_candidate"
    if has_subject and row.get("evidence_backcut_status") == "ok":
        return "ready_for_l5_3_candidate"
    return "review_candidate"


def build_enhanced_rows(conn: sqlite3.Connection, input_rows: list[dict[str, str]], arguments_by_event: dict[str, list[sqlite3.Row]], optional_sources: dict[str, Any]) -> list[dict[str, Any]]:
    enhanced_rows: list[dict[str, Any]] = []
    for row in input_rows:
        event_id = row.get("event_candidate_id", "")
        arguments = arguments_by_event.get(event_id, [])
        original_flags = [str(item) for item in parse_json_array(row.get("warning_flags_json", ""))]
        subjects, source_rule, source_kind, subject_confidence, subject_evidence, subject_span, new_flags = choose_subject(conn, row, arguments)
        subject_added = not parse_json_array(row.get("subject_candidates_json", "")) and bool(subjects)
        flags = list(dict.fromkeys(original_flags + new_flags))
        if subject_added:
            flags.append("enhanced_subject_added")
            flags = [flag for flag in flags if flag != "missing_subject_candidate"]
        if not subjects and "enhanced_subject_not_found" not in flags:
            flags.append("enhanced_subject_not_found")
        if len(subjects) > 1:
            flags.append("enhanced_subject_multiple_candidates")
        if subject_confidence and subject_confidence < 0.6 and "enhanced_subject_low_confidence" not in flags:
            flags.append("enhanced_subject_low_confidence")
        if not any(info.get("exists") for name, info in optional_sources.items() if name.startswith("l3_character")):
            flags.append("optional_character_source_missing")
        if row.get("scene_block_source_status") == "missing_optional":
            flags.append("optional_scene_block_source_missing")
        quality = enhanced_quality_score(row, subject_confidence, bool(subjects), flags)
        output = dict(row)
        output.update(
            {
                "enhanced_subject_candidates_json": json_cell(subjects),
                "enhanced_object_candidates_json": row.get("object_candidates_json", "[]"),
                "enhanced_location_candidates_json": row.get("location_candidates_json", "[]"),
                "enhanced_time_hint_candidates_json": row.get("time_hint_candidates_json", "[]"),
                "enhanced_argument_candidates_json": json_cell(parse_json_array(row.get("other_argument_candidates_json", ""))),
                "enhanced_subject_source_rule": source_rule,
                "enhanced_subject_source_kind": source_kind,
                "enhanced_subject_confidence": f"{subject_confidence:.2f}",
                "enhanced_subject_evidence_text": subject_evidence,
                "enhanced_subject_evidence_span_json": json_cell(subject_span),
                "enhanced_argument_count": int(row.get("argument_count") or 0) + len(subjects),
                "enhanced_subject_candidate_added": "true" if subject_added else "false",
                "enhanced_warning_flags_json": json_cell(sorted(set(flags))),
                "enhanced_quality_score": f"{quality:.2f}",
                "enhanced_review_recommendation": recommendation(row, subject_confidence, bool(subjects), flags),
                "enhanced_notes": "",
            }
        )
        enhanced_rows.append(output)
    return sorted(enhanced_rows, key=lambda item: (int(item.get("chapter_num") or 0), item.get("scene_block_id") or "\uffff", item.get("evidence_l2_paragraph_id") or "\uffff", item.get("evidence_l2_sentence_id") or "\uffff", item.get("event_candidate_id") or ""))


def warning_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for flag in parse_json_array(str(row.get("enhanced_warning_flags_json", "[]"))):
            counts[str(flag)] += 1
    return dict(sorted(counts.items()))


def count_missing_subject(rows: list[dict[str, Any]], column: str) -> int:
    return sum(1 for row in rows if "missing_subject_candidate" in parse_json_array(str(row.get(column, "[]"))))


def quality_metrics(input_rows: list[dict[str, str]], enhanced_rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "original_missing_subject_candidate_count": count_missing_subject(input_rows, "warning_flags_json"),
        "enhanced_missing_subject_candidate_count": count_missing_subject(enhanced_rows, "enhanced_warning_flags_json"),
        "enhanced_subject_candidate_added_count": sum(1 for row in enhanced_rows if row.get("enhanced_subject_candidate_added") == "true"),
        "ready_for_l5_3_candidate_count": sum(1 for row in enhanced_rows if row.get("enhanced_review_recommendation") == "ready_for_l5_3_candidate"),
    }


def write_report(path: Path, manifest: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    rule_usage = dict(sorted(Counter(row.get("enhanced_subject_source_rule", "") for row in rows).items()))
    rec_usage = dict(sorted(Counter(row.get("enhanced_review_recommendation", "") for row in rows).items()))
    lines = [
        "# L5.1a Event Argument Review Quality Patch Report",
        "",
        "## Summary",
        "",
        f"- created_at: {EXPORT_CREATED_AT}",
        f"- enhanced_event_review_rows: {manifest['row_counts']['enhanced_event_review_rows']}",
        "",
        "## Input Files",
        "",
    ]
    lines.extend(f"- {item}" for item in manifest["input_files"])
    lines.extend(["", "## Source Tables", ""])
    lines.extend(f"- {name}: present={name in manifest['source_fingerprints_before']}" for name in SOURCE_TABLES)
    lines.extend(["", "## Optional Source Status", ""])
    lines.extend(f"- {name}: exists={info.get('exists')}" for name, info in manifest["optional_sources"].items())
    lines.extend(["", "## L5.2 Seed Status", ""])
    lines.extend(f"- {key}: {value}" for key, value in manifest["l5_2_seed"].items())
    lines.extend(["", "## Scene Block Source Detection", ""])
    lines.extend(f"- {key}: {value}" for key, value in manifest["detected_scene_block_source"].items())
    lines.extend(["", "## Character Source Detection", ""])
    lines.extend(f"- {name}: exists={info.get('exists')}" for name, info in manifest["detected_character_sources"].items())
    lines.extend(["", "## Quality Metrics", ""])
    lines.extend(f"- {key}: {value}" for key, value in manifest["quality_metrics"].items())
    lines.extend(["", "## Subject Candidate Enhancement", ""])
    lines.extend(f"- {key}: {value}" for key, value in rule_usage.items())
    lines.extend(["", "## Warning Counts", ""])
    warnings = manifest["warning_counts"]
    lines.extend(f"- {key}: {value}" for key, value in warnings.items()) if warnings else lines.append("- none")
    lines.extend(["", "## Rule Usage Counts", ""])
    lines.extend(f"- {key}: {value}" for key, value in rule_usage.items()) if rule_usage else lines.append("- none")
    lines.extend(["", "## Review Recommendation Counts", ""])
    lines.extend(f"- {key}: {value}" for key, value in rec_usage.items()) if rec_usage else lines.append("- none")
    lines.extend(["", "## Output Files", ""])
    lines.extend(f"- {item}" for item in manifest["output_files"])
    lines.extend(["", "## PASS / WARNING / FAIL", ""])
    lines.append("- PASS" if not manifest["source_mutation_detected"] and not manifest["original_l5_1_export_mutation_detected"] else "- FAIL mutation detected")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_event_argument_review_quality_patch(
    project_dir: Path | str | None = None,
    *,
    sample_chapters: str | None = None,
    chapter_num: int | None = None,
    output_dir: Path | str = "outputs",
    strict: bool = False,
    max_context_sentences: int = 2,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    input_files = [out_dir / EVENT_CSV, out_dir / EVENT_JSON, out_dir / MANIFEST_JSON]
    missing = [str(path) for path in input_files if not path.exists()]
    if missing:
        raise RuntimeError("Missing L5.1 review export files. Run: python scripts\\l5_event_candidate_review_exporter.py --project-dir D:\\NovelRAG --sample-chapters 1,2,1697")
    original_hashes_before = {path.name: file_hash(path) for path in input_files}
    input_rows = read_csv_rows(out_dir / EVENT_CSV)
    if sample_chapters:
        chapters = {part.strip() for part in sample_chapters.split(",") if part.strip()}
        input_rows = [row for row in input_rows if str(row.get("chapter_num")) in chapters]
    if chapter_num is not None:
        input_rows = [row for row in input_rows if str(row.get("chapter_num")) == str(chapter_num)]

    db_path = root / DB_RELATIVE_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        before = source_fingerprints(conn)
        arguments_by_event = load_l5_arguments(conn)
        optional_sources = optional_source_status(conn)
        character_sources = detect_character_sources(conn)
        scene_source = scene_block_source_payload(discover_scene_block_source(conn))
        enhanced_rows = build_enhanced_rows(conn, input_rows, arguments_by_event, optional_sources)
        after = source_fingerprints(conn)
    finally:
        conn.close()
    original_hashes_after = {path.name: file_hash(path) for path in input_files}

    output_files = [
        str(out_dir / ENHANCED_CSV),
        str(out_dir / ENHANCED_JSON),
        str(out_dir / QUALITY_REPORT),
        str(out_dir / QUALITY_MANIFEST),
    ]
    metrics = quality_metrics(input_rows, enhanced_rows)
    manifest = {
        "export_layer": "L5.1a Event Argument Review Quality Patch",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": EXPORT_CREATED_AT,
        "input_files": [str(path) for path in input_files],
        "output_files": output_files,
        "source_tables": {name: {"exists": name in before} for name in SOURCE_TABLES},
        "optional_sources": optional_sources,
        "detected_scene_block_source": scene_source,
        "detected_character_sources": character_sources,
        "l5_2_seed": l5_2_seed_status(root),
        "row_counts": {"input_event_review_rows": len(input_rows), "enhanced_event_review_rows": len(enhanced_rows)},
        "quality_metrics": metrics,
        "warning_counts": warning_counts(enhanced_rows),
        "source_fingerprints_before": before,
        "source_fingerprints_after": after,
        "source_mutation_detected": before != after,
        "original_l5_1_hashes_before": original_hashes_before,
        "original_l5_1_hashes_after": original_hashes_after,
        "original_l5_1_export_mutation_detected": original_hashes_before != original_hashes_after,
    }
    if strict and metrics["enhanced_subject_candidate_added_count"] == 0:
        raise RuntimeError("Strict L5.1a failed: no enhanced subject candidate was added")
    if manifest["source_mutation_detected"] or manifest["original_l5_1_export_mutation_detected"]:
        raise RuntimeError("L5.1a mutation guard failed")

    write_csv(out_dir / ENHANCED_CSV, ENHANCED_ALL_COLUMNS, enhanced_rows)
    write_json(out_dir / ENHANCED_JSON, root, ENHANCED_ALL_COLUMNS, enhanced_rows)
    (out_dir / QUALITY_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    write_report(out_dir / QUALITY_REPORT, manifest, enhanced_rows)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Enhance L5.1 event argument review quality without mutating source data.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--sample-chapters", type=str, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--max-context-sentences", type=int, default=2)
    args = parser.parse_args()
    manifest = run_l5_event_argument_review_quality_patch(
        args.project_dir,
        sample_chapters=args.sample_chapters,
        chapter_num=args.chapter_num,
        output_dir=args.output_dir,
        strict=args.strict,
        max_context_sentences=args.max_context_sentences,
    )
    print(f"L5.1a enhanced event review rows: {manifest['row_counts']['enhanced_event_review_rows']}")
    print(f"L5.1a enhanced subject candidates added: {manifest['quality_metrics']['enhanced_subject_candidate_added_count']}")


if __name__ == "__main__":
    main()
