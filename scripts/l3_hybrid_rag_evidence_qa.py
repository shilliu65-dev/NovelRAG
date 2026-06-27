from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as child_builder
from scripts import l3_hybrid_rag_retriever as retriever


DEFAULT_INPUT_PREFIX = "l3_hybrid_rag_real_embedding_retrieval"
DEFAULT_OUTPUT_PREFIX = "l3_hybrid_rag_evidence_qa_sample"
DEFAULT_MAX_EVIDENCE_PER_QUERY = 5
DEFAULT_MIN_EVIDENCE_PER_ANSWER = 1
FORBIDDEN_SQL_TOKENS = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE", "TRUNCATE")


@dataclass
class EvidenceQaResult:
    ok: bool
    project_dir: Path
    db_path: Path
    input_prefix: str
    output_prefix: str
    chapter_scope: list[int]
    query_count: int = 0
    answer_count: int = 0
    grounded_draft_count: int = 0
    insufficient_evidence_count: int = 0
    conflict_evidence_count: int = 0
    total_evidence_count: int = 0
    avg_evidence_per_answer: float = 0.0
    good_answer_count: int = 0
    acceptable_answer_count: int = 0
    weak_answer_count: int = 0
    hash_mismatch_count: int = 0
    missing_sqlite_child_count: int = 0
    source_table_mutation_count: int = 0
    forbidden_final_table_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    qa_run_id: str = ""
    answers: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def output_paths(output_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "answers_json": base / f"{output_prefix}_answers.json",
        "answers_csv": base / f"{output_prefix}_answers.csv",
        "report": base / f"{output_prefix}_report.md",
        "manifest": base / f"{output_prefix}_manifest.json",
    }


def input_paths(input_prefix: str) -> dict[str, Path]:
    paths = retriever.relative_paths_for_prefix(input_prefix)
    return {"results": paths["results_json"], "manifest": paths["manifest"]}


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


def row_counts(conn: sqlite3.Connection, table_names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in table_names:
        if not child_builder.object_exists(conn, name, "table"):
            counts[name] = -1
            continue
        counts[name] = int(conn.execute(f"SELECT COUNT(*) FROM {child_builder.quote_ident(name)}").fetchone()[0])
    return counts


def evidence_hash_for_row(row: sqlite3.Row) -> str:
    return child_builder.sha256_json(
        {
            "child_segment_id": row["child_segment_id"],
            "scene_id": str(row["scene_id"]),
            "chapter_id": row["chapter_id"],
            "chapter_num": int(row["chapter_num"]),
            "start_para_id": row["start_para_id"],
            "end_para_id": row["end_para_id"],
            "start_sentence_id": row["start_sentence_id"],
            "end_sentence_id": row["end_sentence_id"],
            "segment_text_hash": row["segment_text_hash"],
            "source_fingerprint": row["source_fingerprint"],
        }
    )


def snippet(text: str, max_chars: int = 180) -> str:
    compact = " ".join(str(text).split())
    return compact[:max_chars]


def fetch_segment_row(conn: sqlite3.Connection, child_segment_id: str) -> sqlite3.Row | None:
    reject_write_sql("SELECT * FROM l3_child_segment WHERE child_segment_id = ?")
    return conn.execute(
        """
        SELECT *
        FROM l3_child_segment
        WHERE child_segment_id = ?
        """,
        (child_segment_id,),
    ).fetchone()


def id_index(value: str, *, prefix: str) -> int:
    marker = f"_{prefix}"
    if marker in value:
        suffix = value.rsplit(marker, 1)[-1]
        digits = "".join(ch for ch in suffix if ch.isdigit())
        if digits:
            return int(digits)
    digits = "".join(ch for ch in value if ch.isdigit())
    return int(digits[-6:]) if digits else 0


def coordinate_range(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, int]:
    paragraph_start = id_index(str(row["start_para_id"]), prefix="p")
    paragraph_end = id_index(str(row["end_para_id"]), prefix="p")
    sentence_start = id_index(str(row["start_sentence_id"]), prefix="s")
    sentence_end = id_index(str(row["end_sentence_id"]), prefix="s")
    if child_builder.object_exists(conn, "v_l2_current_paragraphs"):
        para_rows = conn.execute(
            """
            SELECT para_id, para_index
            FROM v_l2_current_paragraphs
            WHERE para_id IN (?, ?)
            """,
            (row["start_para_id"], row["end_para_id"]),
        ).fetchall()
        para_map = {str(item["para_id"]): int(item["para_index"]) for item in para_rows}
        paragraph_start = para_map.get(str(row["start_para_id"]), paragraph_start)
        paragraph_end = para_map.get(str(row["end_para_id"]), paragraph_end)
    if child_builder.object_exists(conn, "v_l2_current_sentences"):
        sentence_rows = conn.execute(
            """
            SELECT sentence_id, sentence_index
            FROM v_l2_current_sentences
            WHERE sentence_id IN (?, ?)
            """,
            (row["start_sentence_id"], row["end_sentence_id"]),
        ).fetchall()
        sentence_map = {str(item["sentence_id"]): int(item["sentence_index"]) for item in sentence_rows}
        sentence_start = sentence_map.get(str(row["start_sentence_id"]), sentence_start)
        sentence_end = sentence_map.get(str(row["end_sentence_id"]), sentence_end)
    return {
        "paragraph_start": paragraph_start,
        "paragraph_end": paragraph_end,
        "sentence_start": sentence_start,
        "sentence_end": sentence_end,
    }


def source_for_hit(hit: dict[str, Any]) -> str:
    sources = list(hit.get("recall_sources", []))
    if "sqlite_child_keyword" in sources and "chroma_vector" in sources:
        return "merged"
    if "sqlite_child_keyword" in sources:
        return "sqlite_child_keyword"
    if "chroma_vector" in sources:
        return "chroma_vector"
    return "merged"


def evidence_from_hit(conn: sqlite3.Connection, hit: dict[str, Any], *, rank: int, scope: list[int]) -> tuple[dict[str, Any] | None, str | None]:
    child_segment_id = str(hit.get("child_segment_id", ""))
    row = fetch_segment_row(conn, child_segment_id)
    if row is None:
        return None, f"missing_sqlite_child: {child_segment_id}"
    if int(row["chapter_num"]) not in scope:
        return None, f"out_of_scope_child: {child_segment_id}"
    if child_builder.sha256_text(str(row["segment_text"])) != str(row["segment_text_hash"]):
        return None, f"segment_text_hash_mismatch: {child_segment_id}"
    coords = coordinate_range(conn, row)
    return (
        {
            "rank": rank,
            "child_segment_id": child_segment_id,
            "scene_block_id": str(row["scene_id"]),
            "chapter_id": row["chapter_id"],
            "chapter_num": int(row["chapter_num"]),
            **coords,
            "source": source_for_hit(hit),
            "score": float(hit.get("hybrid_score", hit.get("vector_score", 0.0)) or 0.0),
            "text_excerpt": snippet(str(row["segment_text"])),
            "evidence_hash": evidence_hash_for_row(row),
        },
        None,
    )


def build_answer(query: dict[str, str], evidence: list[dict[str, Any]], *, min_evidence_per_answer: int) -> dict[str, Any]:
    warnings: list[str] = []
    if len(evidence) < min_evidence_per_answer:
        return {
            "query_id": query["query_id"],
            "query_text": query["query_text"],
            "answer_status": "insufficient_evidence",
            "grounded_answer": "",
            "confidence_level": "weak",
            "evidence_count": len(evidence),
            "evidence": evidence,
            "warnings": [f"evidence_count below min_evidence_per_answer={min_evidence_per_answer}"],
        }
    refs = ", ".join(f"[E{item['rank']}:{item['child_segment_id']}]" for item in evidence)
    excerpts = "；".join(item["text_excerpt"] for item in evidence[:3])
    if sum(len(item["text_excerpt"]) for item in evidence) < 40:
        confidence = "acceptable"
        warnings.append("evidence excerpts are short; confidence capped at acceptable")
        prefix = f"当前小样本证据不足，只能确认与“{query['query_text']}”相关，不能形成完整回答。"
    else:
        confidence = "good" if len(evidence) >= 3 else "acceptable"
        prefix = "根据召回证据，当前小样本中可确认的信息是："
    return {
        "query_id": query["query_id"],
        "query_text": query["query_text"],
        "answer_status": "grounded_draft",
        "grounded_answer": f"{prefix}{excerpts}\n\nevidence_refs: {refs}",
        "confidence_level": confidence,
        "evidence_count": len(evidence),
        "evidence": evidence,
        "warnings": warnings,
    }


def build_answers(
    conn: sqlite3.Connection,
    *,
    retrieval_results: dict[str, Any],
    scope: list[int],
    max_evidence_per_query: int,
    min_evidence_per_answer: int,
) -> tuple[list[dict[str, Any]], int, int]:
    answers: list[dict[str, Any]] = []
    missing_sqlite_child_count = 0
    hash_mismatch_count = 0
    merged_hits = list(retrieval_results.get("merged_hits", []))
    for query in retrieval_results.get("queries", []):
        query_hits = [hit for hit in merged_hits if hit.get("query_id") == query["query_id"]]
        query_hits.sort(key=lambda hit: (int(hit.get("rank", 9999)), -float(hit.get("hybrid_score", 0.0))))
        evidence: list[dict[str, Any]] = []
        seen: set[str] = set()
        warnings: list[str] = []
        for hit in query_hits:
            child_segment_id = str(hit.get("child_segment_id", ""))
            if child_segment_id in seen:
                continue
            seen.add(child_segment_id)
            item, warning = evidence_from_hit(conn, hit, rank=len(evidence) + 1, scope=scope)
            if warning:
                warnings.append(warning)
                if warning.startswith("missing_sqlite_child"):
                    missing_sqlite_child_count += 1
                if "hash_mismatch" in warning:
                    hash_mismatch_count += 1
                continue
            assert item is not None
            evidence.append(item)
            if len(evidence) >= max_evidence_per_query:
                break
        answer = build_answer(query, evidence, min_evidence_per_answer=min_evidence_per_answer)
        answer["warnings"].extend(warnings)
        answers.append(answer)
    return answers, hash_mismatch_count, missing_sqlite_child_count


def answers_csv_fields() -> list[str]:
    return [
        "query_id",
        "query_text",
        "answer_status",
        "grounded_answer",
        "confidence_level",
        "evidence_count",
        "evidence_refs",
        "warnings",
    ]


def write_answers_json(root: Path, output_prefix: str, payload: dict[str, Any]) -> None:
    path = root / output_paths(output_prefix)["answers_json"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_answers_csv(root: Path, output_prefix: str, answers: list[dict[str, Any]]) -> None:
    path = root / output_paths(output_prefix)["answers_csv"]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=answers_csv_fields())
        writer.writeheader()
        for answer in answers:
            writer.writerow(
                {
                    "query_id": answer["query_id"],
                    "query_text": answer["query_text"],
                    "answer_status": answer["answer_status"],
                    "grounded_answer": answer["grounded_answer"],
                    "confidence_level": answer["confidence_level"],
                    "evidence_count": answer["evidence_count"],
                    "evidence_refs": json.dumps(
                        [f"E{item['rank']}:{item['child_segment_id']}" for item in answer["evidence"]],
                        ensure_ascii=False,
                    ),
                    "warnings": json.dumps(answer["warnings"], ensure_ascii=False),
                }
            )


def manifest_payload(
    result: EvidenceQaResult,
    *,
    retrieval_manifest: dict[str, Any],
    source_table_names: list[str],
    source_row_counts_before: dict[str, int],
    source_row_counts_after: dict[str, int],
    created_at: str,
    max_evidence_per_query: int,
    min_evidence_per_answer: int,
) -> dict[str, Any]:
    return {
        "layer": "L3.8",
        "qa_run_id": result.qa_run_id,
        "input_prefix": result.input_prefix,
        "output_prefix": result.output_prefix,
        "retrieval_run_id": retrieval_manifest.get("retrieval_run_id", ""),
        "chapter_scope": ",".join(str(item) for item in result.chapter_scope),
        "query_count": result.query_count,
        "answer_count": result.answer_count,
        "grounded_draft_count": result.grounded_draft_count,
        "insufficient_evidence_count": result.insufficient_evidence_count,
        "conflict_evidence_count": result.conflict_evidence_count,
        "total_evidence_count": result.total_evidence_count,
        "avg_evidence_per_answer": result.avg_evidence_per_answer,
        "good_answer_count": result.good_answer_count,
        "acceptable_answer_count": result.acceptable_answer_count,
        "weak_answer_count": result.weak_answer_count,
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
        "max_evidence_per_query": max_evidence_per_query,
        "min_evidence_per_answer": min_evidence_per_answer,
        "no_llm_calls": True,
        "embedding_model_calls": False,
        "read_only": True,
    }


def write_manifest(root: Path, output_prefix: str, payload: dict[str, Any]) -> None:
    path = root / output_paths(output_prefix)["manifest"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(root: Path, output_prefix: str, manifest: dict[str, Any], answers: list[dict[str, Any]]) -> None:
    lines = [
        "# L3.8 Hybrid RAG evidence QA report",
        "",
        f"- created_at: {manifest['created_at']}",
        f"- input_prefix: {manifest['input_prefix']}",
        f"- output_prefix: {manifest['output_prefix']}",
        f"- chapter_scope: {manifest['chapter_scope']}",
        f"- query_count: {manifest['query_count']}",
        f"- answer_count: {manifest['answer_count']}",
        f"- grounded_draft_count: {manifest['grounded_draft_count']}",
        f"- insufficient_evidence_count: {manifest['insufficient_evidence_count']}",
        f"- conflict_evidence_count: {manifest['conflict_evidence_count']}",
        f"- total_evidence_count: {manifest['total_evidence_count']}",
        f"- avg_evidence_per_answer: {manifest['avg_evidence_per_answer']}",
        f"- hash_mismatch_count: {manifest['hash_mismatch_count']}",
        f"- missing_sqlite_child_count: {manifest['missing_sqlite_child_count']}",
        f"- source_table_mutation_count: {manifest['source_table_mutation_count']}",
        f"- forbidden_final_table_count: {manifest['forbidden_final_table_count']}",
        f"- error_count: {manifest['error_count']}",
        f"- warning_count: {manifest['warning_count']}",
        "",
        "## Answers",
        "",
    ]
    for answer in answers:
        lines.append(
            f"- {answer['query_id']} `{answer['query_text']}`: status={answer['answer_status']}, "
            f"confidence={answer['confidence_level']}, evidence_count={answer['evidence_count']}"
        )
    lines.extend(
        [
            "",
            "No LLM calls. No embedding calls. Grounded answers are deterministic drafts and are not final facts.",
            "",
        ]
    )
    path = root / output_paths(output_prefix)["report"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def compute_metrics(result: EvidenceQaResult) -> None:
    result.answer_count = len(result.answers)
    result.grounded_draft_count = sum(1 for item in result.answers if item["answer_status"] == "grounded_draft")
    result.insufficient_evidence_count = sum(1 for item in result.answers if item["answer_status"] == "insufficient_evidence")
    result.conflict_evidence_count = sum(1 for item in result.answers if item["answer_status"] == "conflict_evidence")
    result.total_evidence_count = sum(int(item["evidence_count"]) for item in result.answers)
    result.avg_evidence_per_answer = round(result.total_evidence_count / result.answer_count, 2) if result.answer_count else 0.0
    result.good_answer_count = sum(1 for item in result.answers if item["confidence_level"] == "good")
    result.acceptable_answer_count = sum(1 for item in result.answers if item["confidence_level"] == "acceptable")
    result.weak_answer_count = sum(1 for item in result.answers if item["confidence_level"] == "weak")
    result.warning_count = len(result.warnings) + sum(len(item["warnings"]) for item in result.answers)


def run_evidence_qa(
    project_dir: Path | str,
    *,
    input_prefix: str = DEFAULT_INPUT_PREFIX,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    sample_chapters: str = child_builder.DEFAULT_SAMPLE_CHAPTERS,
    read_only: bool = True,
    max_evidence_per_query: int = DEFAULT_MAX_EVIDENCE_PER_QUERY,
    min_evidence_per_answer: int = DEFAULT_MIN_EVIDENCE_PER_ANSWER,
) -> EvidenceQaResult:
    root = Path(project_dir).resolve()
    child_builder.ensure_dirs(root)
    scope = child_builder.parse_scope(sample_chapters, None)
    db_path = root / child_builder.DB_RELATIVE_PATH
    result = EvidenceQaResult(False, root, db_path, input_prefix, output_prefix, scope)
    created_at = now_iso()
    try:
        if not read_only:
            raise RuntimeError("L3.8 must run with --read-only")
        if max_evidence_per_query <= 0:
            raise ValueError("--max-evidence-per-query must be positive")
        if min_evidence_per_answer < 0:
            raise ValueError("--min-evidence-per-answer must be >= 0")
        retrieval_paths = input_paths(input_prefix)
        retrieval_results = read_json(root / retrieval_paths["results"])
        retrieval_manifest = read_json(root / retrieval_paths["manifest"])
        result.query_count = int(retrieval_manifest.get("query_count", len(retrieval_results.get("queries", []))))
        conn = connect_sqlite_readonly(db_path)
        try:
            source_table_names = child_builder.discover_source_table_names(conn)
            source_counts_before = row_counts(conn, source_table_names)
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
            if result.forbidden_final_table_count:
                result.errors.append("forbidden final tables exist before L3.8 QA")
            answers, result.hash_mismatch_count, result.missing_sqlite_child_count = build_answers(
                conn,
                retrieval_results=retrieval_results,
                scope=scope,
                max_evidence_per_query=max_evidence_per_query,
                min_evidence_per_answer=min_evidence_per_answer,
            )
            result.answers = answers
            source_counts_after = row_counts(conn, source_table_names)
            result.source_table_mutation_count = sum(
                1 for name in sorted(set(source_counts_before) | set(source_counts_after))
                if source_counts_before.get(name) != source_counts_after.get(name)
            )
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
        finally:
            conn.close()
        compute_metrics(result)
        if result.query_count != result.answer_count:
            result.errors.append("query_count does not match answer_count")
        if result.hash_mismatch_count:
            result.errors.append("evidence hash mismatch detected")
        if result.missing_sqlite_child_count:
            result.errors.append("missing SQLite child_segment detected")
        if result.source_table_mutation_count:
            result.errors.append("source table row count mutation detected")
        if result.forbidden_final_table_count:
            result.errors.append("forbidden final tables exist after L3.8 QA")
        result.qa_run_id = "l3qa_" + child_builder.sha256_json(
            {"input_prefix": input_prefix, "output_prefix": output_prefix, "queries": retrieval_results.get("queries", [])}
        )[:24]
        result.error_count = len(result.errors)
        result.ok = result.error_count == 0
        payload = {
            "layer": "L3.8",
            "qa_run_id": result.qa_run_id,
            "input_prefix": input_prefix,
            "output_prefix": output_prefix,
            "chapter_scope": scope,
            "query_count": result.query_count,
            "answers": result.answers,
        }
        manifest = manifest_payload(
            result,
            retrieval_manifest=retrieval_manifest,
            source_table_names=source_table_names,
            source_row_counts_before=source_counts_before,
            source_row_counts_after=source_counts_after,
            created_at=created_at,
            max_evidence_per_query=max_evidence_per_query,
            min_evidence_per_answer=min_evidence_per_answer,
        )
        write_answers_json(root, output_prefix, payload)
        write_answers_csv(root, output_prefix, result.answers)
        write_manifest(root, output_prefix, manifest)
        write_report(root, output_prefix, manifest, result.answers)
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        result.error_count = len(result.errors)
        result.warning_count = len(result.warnings)
        result.ok = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L3.8 grounded evidence QA drafts from L3.7b retrieval results.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--input-prefix", type=str, default=DEFAULT_INPUT_PREFIX)
    parser.add_argument("--output-prefix", type=str, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--sample-chapters", type=str, default=child_builder.DEFAULT_SAMPLE_CHAPTERS)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--max-evidence-per-query", type=int, default=DEFAULT_MAX_EVIDENCE_PER_QUERY)
    parser.add_argument("--min-evidence-per-answer", type=int, default=DEFAULT_MIN_EVIDENCE_PER_ANSWER)
    args = parser.parse_args()
    result = run_evidence_qa(
        args.project_dir,
        input_prefix=args.input_prefix,
        output_prefix=args.output_prefix,
        sample_chapters=args.sample_chapters,
        read_only=args.read_only,
        max_evidence_per_query=args.max_evidence_per_query,
        min_evidence_per_answer=args.min_evidence_per_answer,
    )
    print("L3.8 hybrid RAG evidence QA PASS" if result.ok else "L3.8 hybrid RAG evidence QA FAIL")
    if result.ok:
        print(f"answer_count={result.answer_count}")
        print(f"total_evidence_count={result.total_evidence_count}")
    else:
        for error in result.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
