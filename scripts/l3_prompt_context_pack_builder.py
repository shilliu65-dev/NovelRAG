from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as child_builder
from scripts import l3_hybrid_rag_evidence_qa as evidence_qa


DEFAULT_INPUT_PREFIX = "l3_hybrid_rag_evidence_qa_sample"
DEFAULT_OUTPUT_PREFIX = "l3_prompt_context_pack_sample"
DEFAULT_TOKEN_BUDGET = 2500
DEFAULT_MAX_EVIDENCE_PER_PACK = 5
DEFAULT_MAX_ALLOWED_FACTS_PER_PACK = 8
FORBIDDEN_SQL_TOKENS = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE")
FORBIDDEN_INFERENCE_RULES = [
    "Do not use facts outside the provided evidence.",
    "Do not invent characters, locations, organizations, events, powers, or relationships.",
    "Do not merge evidence from different chapters as a confirmed single event unless evidence explicitly supports it.",
    "Do not treat candidate or draft answers as final truth.",
    "If evidence is insufficient, say evidence is insufficient.",
    "Preserve chapter_num and evidence_ref_id when producing any answer.",
    "Do not create final_event, final_timeline, final_relationship_graph, or final_state_machine.",
]


@dataclass
class PackBuildResult:
    ok: bool
    project_dir: Path
    db_path: Path
    input_prefix: str
    output_prefix: str
    chapter_scope: list[int]
    token_budget: int
    query_count: int = 0
    context_pack_count: int = 0
    ready_pack_count: int = 0
    partial_pack_count: int = 0
    insufficient_pack_count: int = 0
    total_allowed_fact_count: int = 0
    total_evidence_ref_count: int = 0
    avg_evidence_refs_per_pack: float = 0.0
    over_token_budget_count: int = 0
    hash_mismatch_count: int = 0
    missing_sqlite_child_count: int = 0
    source_table_mutation_count: int = 0
    forbidden_final_table_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    context_packs: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def output_paths(output_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "packs": base / f"{output_prefix}.json",
        "manifest": base / f"{output_prefix}_manifest.json",
    }


def input_paths(input_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "answers": base / f"{input_prefix}_answers.json",
        "manifest": base / f"{input_prefix}_manifest.json",
    }


def connect_sqlite_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def reject_write_sql(sql: str) -> None:
    upper = " ".join(sql.upper().split())
    for token in FORBIDDEN_SQL_TOKENS:
        if upper.startswith(token) or f" {token} " in upper:
            raise RuntimeError(f"read_only SQL guard rejected statement containing {token}")


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required input missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def row_counts(conn: sqlite3.Connection, table_names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in table_names:
        if not child_builder.object_exists(conn, name, "table"):
            counts[name] = -1
            continue
        reject_write_sql(f"SELECT COUNT(*) FROM {name}")
        counts[name] = int(conn.execute(f"SELECT COUNT(*) FROM {child_builder.quote_ident(name)}").fetchone()[0])
    return counts


def estimate_tokens(text: str) -> int:
    if re.search(r"[\u4e00-\u9fff]", text):
        return int(len(text) / 1.5) + 1
    return int(len(text.split()) * 1.3) + 1


def split_fact_candidates(grounded_answer: str) -> list[str]:
    body = grounded_answer.split("evidence_refs:", 1)[0]
    body = body.replace("根据召回证据，当前小样本中可确认的信息是：", "")
    parts = re.split(r"[。；;\n]+", body)
    banned = ("证据不足", "不能确认", "无法确认")
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = " ".join(part.strip().split())
        if not value:
            continue
        if any(token in value for token in banned):
            continue
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def keywords_for_text(text: str) -> set[str]:
    keywords: set[str] = set()
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(chunk) <= 8:
            keywords.add(chunk)
        else:
            for index in range(0, len(chunk) - 1):
                keywords.add(chunk[index : index + 2])
    for token in re.findall(r"[A-Za-z0-9_]{2,}", text):
        keywords.add(token.lower())
    return keywords


def matching_evidence_refs(fact_text: str, evidence_refs: list[dict[str, Any]]) -> list[str]:
    fact_keywords = keywords_for_text(fact_text)
    if not fact_keywords:
        return []
    matched: list[str] = []
    for evidence in evidence_refs:
        excerpt = str(evidence.get("text_excerpt", ""))
        evidence_keywords = keywords_for_text(excerpt)
        if fact_keywords & evidence_keywords:
            matched.append(str(evidence["evidence_ref_id"]))
    return matched


def validate_evidence_ref(
    conn: sqlite3.Connection,
    evidence: dict[str, Any],
    *,
    evidence_ref_id: str,
    scope: list[int],
) -> tuple[dict[str, Any] | None, str | None]:
    child_segment_id = str(evidence.get("child_segment_id", ""))
    reject_write_sql("SELECT * FROM l3_child_segment WHERE child_segment_id = ?")
    row = conn.execute(
        """
        SELECT *
        FROM l3_child_segment
        WHERE child_segment_id = ?
        """,
        (child_segment_id,),
    ).fetchone()
    if row is None:
        return None, "missing_sqlite_child"
    if int(evidence.get("chapter_num", -1)) not in scope or int(row["chapter_num"]) not in scope:
        return None, "chapter_out_of_scope"
    expected_hash = evidence_qa.evidence_hash_for_row(row)
    if str(evidence.get("evidence_hash", "")) != expected_hash:
        return None, "hash_mismatch"
    return (
        {
            "evidence_ref_id": evidence_ref_id,
            "child_segment_id": child_segment_id,
            "scene_block_id": str(evidence.get("scene_block_id", "")),
            "chapter_num": int(evidence.get("chapter_num", row["chapter_num"])),
            "paragraph_start": int(evidence.get("paragraph_start", 0)),
            "paragraph_end": int(evidence.get("paragraph_end", 0)),
            "sentence_start": int(evidence.get("sentence_start", 0)),
            "sentence_end": int(evidence.get("sentence_end", 0)),
            "text_excerpt": str(evidence.get("text_excerpt", "")),
            "evidence_hash": str(evidence.get("evidence_hash", "")),
            "source": str(evidence.get("source", "merged")),
        },
        None,
    )


def build_allowed_facts(
    answer: dict[str, Any],
    evidence_refs: list[dict[str, Any]],
    *,
    max_allowed_facts_per_pack: int,
    chapter_scope: str,
) -> list[dict[str, Any]]:
    facts: list[dict[str, Any]] = []
    for candidate in split_fact_candidates(str(answer.get("grounded_answer", ""))):
        refs = matching_evidence_refs(candidate, evidence_refs)
        if not refs:
            continue
        facts.append(
            {
                "fact_id": f"FACT-{len(facts) + 1}",
                "fact_text": candidate,
                "source_evidence_ref_ids": refs,
                "fact_status": "evidence_supported" if answer.get("confidence_level") == "good" else "weakly_supported",
                "chapter_scope": chapter_scope,
            }
        )
        if len(facts) >= max_allowed_facts_per_pack:
            break
    return facts


def render_prompt_context(query_text: str, allowed_facts: list[dict[str, Any]], evidence_refs: list[dict[str, Any]]) -> str:
    fact_lines = [f"[{fact['fact_id']}] {fact['fact_text']}" for fact in allowed_facts] or ["[FACT-0] No allowed facts were derived from evidence."]
    evidence_lines: list[str] = []
    for evidence in evidence_refs:
        evidence_lines.append(
            f"[{evidence['evidence_ref_id']}] chapter={evidence['chapter_num']}, child_segment_id={evidence['child_segment_id']}\n"
            f"{evidence['text_excerpt']}"
        )
    rules = "\n".join(f"{index}. {rule}" for index, rule in enumerate(FORBIDDEN_INFERENCE_RULES, start=1))
    return "\n".join(
        [
            "You are a grounded generation agent.",
            "",
            "Task query:",
            query_text,
            "",
            "You must only use the evidence below.",
            "",
            "Allowed facts:",
            *fact_lines,
            "",
            "Evidence:",
            *evidence_lines,
            "",
            "Forbidden inference rules:",
            "",
            rules,
            "",
            "Required response style:",
            "",
            "* Answer only from evidence.",
            "* Cite evidence_ref_id.",
            "* Say insufficient evidence when evidence is insufficient.",
        ]
    )


def context_status(answer: dict[str, Any], evidence_refs: list[dict[str, Any]], allowed_facts: list[dict[str, Any]], prompt_context_text: str) -> str:
    if answer.get("answer_status") == "insufficient_evidence" or not evidence_refs:
        return "insufficient"
    if (
        answer.get("answer_status") == "grounded_draft"
        and evidence_refs
        and allowed_facts
        and prompt_context_text
        and answer.get("confidence_level") == "good"
    ):
        return "ready"
    return "partial"


def build_pack(
    conn: sqlite3.Connection,
    answer: dict[str, Any],
    *,
    scope: list[int],
    max_evidence_per_pack: int,
    max_allowed_facts_per_pack: int,
    token_budget: int,
    chapter_scope_text: str,
) -> tuple[dict[str, Any], int, int, bool]:
    warnings = list(answer.get("warnings", []))
    evidence_refs: list[dict[str, Any]] = []
    hash_mismatch_count = 0
    missing_sqlite_child_count = 0
    for evidence in answer.get("evidence", [])[:max_evidence_per_pack]:
        evidence_ref_id = f"EVID-{len(evidence_refs) + 1}"
        ref, error = validate_evidence_ref(conn, evidence, evidence_ref_id=evidence_ref_id, scope=scope)
        if error == "missing_sqlite_child":
            missing_sqlite_child_count += 1
            warnings.append(f"missing_sqlite_child: {evidence.get('child_segment_id', '')}")
            continue
        if error == "hash_mismatch":
            hash_mismatch_count += 1
            warnings.append(f"hash_mismatch: {evidence.get('child_segment_id', '')}")
            continue
        if error:
            warnings.append(f"{error}: {evidence.get('child_segment_id', '')}")
            continue
        assert ref is not None
        evidence_refs.append(ref)

    allowed_facts = build_allowed_facts(
        answer,
        evidence_refs,
        max_allowed_facts_per_pack=max_allowed_facts_per_pack,
        chapter_scope=chapter_scope_text,
    )
    prompt_context_text = render_prompt_context(str(answer.get("query_text", "")), allowed_facts, evidence_refs)
    token_estimate = estimate_tokens(prompt_context_text)
    was_over_budget = token_estimate > token_budget
    while token_estimate > token_budget and len(evidence_refs) > 1:
        evidence_refs = evidence_refs[:-1]
        allowed_facts = build_allowed_facts(
            answer,
            evidence_refs,
            max_allowed_facts_per_pack=max_allowed_facts_per_pack,
            chapter_scope=chapter_scope_text,
        )
        prompt_context_text = render_prompt_context(str(answer.get("query_text", "")), allowed_facts, evidence_refs)
        token_estimate = estimate_tokens(prompt_context_text)
    if was_over_budget and token_estimate > token_budget:
        warnings.append("token_budget_exceeded_after_minimum_evidence")
    elif was_over_budget:
        warnings.append("evidence_refs_truncated_to_fit_token_budget")

    status = context_status(answer, evidence_refs, allowed_facts, prompt_context_text)
    if status == "insufficient":
        allowed_facts = []
        prompt_context_text = render_prompt_context(str(answer.get("query_text", "")), allowed_facts, evidence_refs)
        token_estimate = estimate_tokens(prompt_context_text)

    return (
        {
            "query_id": answer.get("query_id", ""),
            "query_text": answer.get("query_text", ""),
            "task_type": "qa",
            "context_pack_status": status,
            "source_answer_status": answer.get("answer_status", "insufficient_evidence"),
            "confidence_level": answer.get("confidence_level", "weak"),
            "allowed_facts": allowed_facts,
            "evidence_refs": evidence_refs,
            "forbidden_inference_rules": FORBIDDEN_INFERENCE_RULES,
            "prompt_context_text": prompt_context_text,
            "token_budget_estimate": token_estimate,
            "warnings": warnings,
        },
        hash_mismatch_count,
        missing_sqlite_child_count,
        was_over_budget,
    )


def build_manifest(
    result: PackBuildResult,
    *,
    created_at: str,
    source_table_names: list[str],
    source_row_counts_before: dict[str, int],
    source_row_counts_after: dict[str, int],
) -> dict[str, Any]:
    return {
        "layer": "L3.9",
        "input_prefix": result.input_prefix,
        "output_prefix": result.output_prefix,
        "chapter_scope": ",".join(str(item) for item in result.chapter_scope),
        "query_count": result.query_count,
        "context_pack_count": result.context_pack_count,
        "ready_pack_count": result.ready_pack_count,
        "partial_pack_count": result.partial_pack_count,
        "insufficient_pack_count": result.insufficient_pack_count,
        "total_allowed_fact_count": result.total_allowed_fact_count,
        "total_evidence_ref_count": result.total_evidence_ref_count,
        "avg_evidence_refs_per_pack": result.avg_evidence_refs_per_pack,
        "token_budget": result.token_budget,
        "over_token_budget_count": result.over_token_budget_count,
        "hash_mismatch_count": result.hash_mismatch_count,
        "missing_sqlite_child_count": result.missing_sqlite_child_count,
        "source_table_mutation_count": result.source_table_mutation_count,
        "forbidden_final_table_count": result.forbidden_final_table_count,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "created_at": created_at,
        "source_table_names": source_table_names,
        "source_row_counts_before": source_row_counts_before,
        "source_row_counts_after": source_row_counts_after,
        "no_llm_calls": True,
        "embedding_model_calls": False,
        "chroma_access": False,
        "read_only": True,
    }


def compute_counts(result: PackBuildResult) -> None:
    result.context_pack_count = len(result.context_packs)
    result.ready_pack_count = sum(1 for pack in result.context_packs if pack["context_pack_status"] == "ready")
    result.partial_pack_count = sum(1 for pack in result.context_packs if pack["context_pack_status"] == "partial")
    result.insufficient_pack_count = sum(1 for pack in result.context_packs if pack["context_pack_status"] == "insufficient")
    result.total_allowed_fact_count = sum(len(pack["allowed_facts"]) for pack in result.context_packs)
    result.total_evidence_ref_count = sum(len(pack["evidence_refs"]) for pack in result.context_packs)
    result.avg_evidence_refs_per_pack = round(result.total_evidence_ref_count / result.context_pack_count, 2) if result.context_pack_count else 0.0
    result.warning_count = len(result.warnings) + sum(len(pack.get("warnings", [])) for pack in result.context_packs)


def run_builder(
    project_dir: Path | str,
    *,
    input_prefix: str = DEFAULT_INPUT_PREFIX,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    sample_chapters: str = child_builder.DEFAULT_SAMPLE_CHAPTERS,
    read_only: bool = True,
    max_evidence_per_pack: int = DEFAULT_MAX_EVIDENCE_PER_PACK,
    max_allowed_facts_per_pack: int = DEFAULT_MAX_ALLOWED_FACTS_PER_PACK,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> PackBuildResult:
    root = Path(project_dir).resolve()
    child_builder.ensure_dirs(root)
    db_path = root / child_builder.DB_RELATIVE_PATH
    scope = child_builder.parse_scope(sample_chapters, None)
    chapter_scope_text = ",".join(str(item) for item in scope)
    result = PackBuildResult(False, root, db_path, input_prefix, output_prefix, scope, token_budget)
    created_at = now_iso()
    try:
        if not read_only:
            raise RuntimeError("L3.9 builder must run with --read-only")
        if max_evidence_per_pack <= 0:
            raise ValueError("--max-evidence-per-pack must be positive")
        if max_allowed_facts_per_pack < 0:
            raise ValueError("--max-allowed-facts-per-pack must be >= 0")
        if token_budget <= 0:
            raise ValueError("--token-budget must be positive")

        answers_payload = read_json(root / input_paths(input_prefix)["answers"])
        _answers_manifest = read_json(root / input_paths(input_prefix)["manifest"])
        answers = list(answers_payload.get("answers", []))
        result.query_count = int(answers_payload.get("query_count", len(answers)))

        conn = connect_sqlite_readonly(db_path)
        try:
            source_table_names = child_builder.discover_source_table_names(conn)
            source_counts_before = row_counts(conn, source_table_names)
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
            if result.forbidden_final_table_count:
                result.errors.append("forbidden final tables exist before L3.9 builder")
            for answer in answers:
                pack, hash_mismatch_count, missing_sqlite_child_count, over_budget = build_pack(
                    conn,
                    answer,
                    scope=scope,
                    max_evidence_per_pack=max_evidence_per_pack,
                    max_allowed_facts_per_pack=max_allowed_facts_per_pack,
                    token_budget=token_budget,
                    chapter_scope_text=chapter_scope_text,
                )
                result.context_packs.append(pack)
                result.hash_mismatch_count += hash_mismatch_count
                result.missing_sqlite_child_count += missing_sqlite_child_count
                if over_budget:
                    result.over_token_budget_count += 1
            source_counts_after = row_counts(conn, source_table_names)
            result.source_table_mutation_count = sum(
                1
                for name in sorted(set(source_counts_before) | set(source_counts_after))
                if source_counts_before.get(name) != source_counts_after.get(name)
            )
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
        finally:
            conn.close()

        compute_counts(result)
        if result.query_count != result.context_pack_count:
            result.errors.append("query_count does not match context_pack_count")
        if result.hash_mismatch_count:
            result.errors.append("hash mismatches detected while building context packs")
        if result.missing_sqlite_child_count:
            result.errors.append("missing SQLite child segments detected while building context packs")
        if result.source_table_mutation_count:
            result.errors.append("source table row count mutation detected")
        if result.forbidden_final_table_count:
            result.errors.append("forbidden final tables exist after L3.9 builder")

        result.error_count = len(result.errors)
        result.ok = result.error_count == 0
        manifest = build_manifest(
            result,
            created_at=created_at,
            source_table_names=source_table_names,
            source_row_counts_before=source_counts_before,
            source_row_counts_after=source_counts_after,
        )
        write_json(
            root / output_paths(output_prefix)["packs"],
            {
                "layer": "L3.9",
                "input_prefix": input_prefix,
                "output_prefix": output_prefix,
                "chapter_scope": scope,
                "query_count": result.query_count,
                "context_packs": result.context_packs,
            },
        )
        write_json(root / output_paths(output_prefix)["manifest"], manifest)
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        result.error_count = len(result.errors)
        result.warning_count = len(result.warnings)
        result.ok = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L3.9 prompt context packs from L3.8 evidence QA answers.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--input-prefix", type=str, default=DEFAULT_INPUT_PREFIX)
    parser.add_argument("--output-prefix", type=str, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--sample-chapters", type=str, default=child_builder.DEFAULT_SAMPLE_CHAPTERS)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--max-evidence-per-pack", type=int, default=DEFAULT_MAX_EVIDENCE_PER_PACK)
    parser.add_argument("--max-allowed-facts-per-pack", type=int, default=DEFAULT_MAX_ALLOWED_FACTS_PER_PACK)
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    args = parser.parse_args()
    result = run_builder(
        args.project_dir,
        input_prefix=args.input_prefix,
        output_prefix=args.output_prefix,
        sample_chapters=args.sample_chapters,
        read_only=args.read_only,
        max_evidence_per_pack=args.max_evidence_per_pack,
        max_allowed_facts_per_pack=args.max_allowed_facts_per_pack,
        token_budget=args.token_budget,
    )
    print("L3.9 prompt context pack BUILD PASS" if result.ok else "L3.9 prompt context pack BUILD FAIL")
    if result.ok:
        print(f"context_pack_count={result.context_pack_count}")
        print(f"ready_pack_count={result.ready_pack_count}")
    else:
        for error in result.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
