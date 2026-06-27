from __future__ import annotations

import argparse
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
from scripts import l3_hybrid_rag_evidence_qa as qa


@dataclass
class VerifyResult:
    ok: bool
    project_dir: Path
    db_path: Path
    input_prefix: str
    output_prefix: str
    chapter_scope: list[int]
    query_count: int = 0
    answer_count: int = 0
    zero_answer_count: int = 0
    hash_mismatch_count: int = 0
    missing_sqlite_child_count: int = 0
    source_table_mutation_count: int = 0
    forbidden_final_table_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def verify_paths(output_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "json": base / f"{output_prefix}_verify_report.json",
        "md": base / f"{output_prefix}_verify_report.md",
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required L3.8 file missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_output_files(root: Path, output_prefix: str, result: VerifyResult) -> None:
    for relative_path in qa.output_paths(output_prefix).values():
        if not (root / relative_path).exists():
            result.errors.append(f"L3.8 output file missing: {relative_path.as_posix()}")


def write_json_report(result: VerifyResult) -> None:
    payload = {
        "verified_at": now_iso(),
        "input_prefix": result.input_prefix,
        "output_prefix": result.output_prefix,
        "chapter_scope": result.chapter_scope,
        "query_count": result.query_count,
        "answer_count": result.answer_count,
        "zero_answer_count": result.zero_answer_count,
        "hash_mismatch_count": result.hash_mismatch_count,
        "missing_sqlite_child_count": result.missing_sqlite_child_count,
        "source_table_mutation_count": result.source_table_mutation_count,
        "forbidden_final_table_count": result.forbidden_final_table_count,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "final_status": "PASS" if result.ok else "FAIL",
    }
    path = result.project_dir / verify_paths(result.output_prefix)["json"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md_report(result: VerifyResult) -> None:
    lines = [
        "# L3.8 Hybrid RAG evidence QA verify report",
        "",
        f"- verified_at: {now_iso()}",
        f"- input_prefix: {result.input_prefix}",
        f"- output_prefix: {result.output_prefix}",
        f"- chapter_scope: {','.join(str(item) for item in result.chapter_scope)}",
        f"- query_count: {result.query_count}",
        f"- answer_count: {result.answer_count}",
        f"- zero_answer_count: {result.zero_answer_count}",
        f"- hash_mismatch_count: {result.hash_mismatch_count}",
        f"- missing_sqlite_child_count: {result.missing_sqlite_child_count}",
        f"- source_table_mutation_count: {result.source_table_mutation_count}",
        f"- forbidden_final_table_count: {result.forbidden_final_table_count}",
        f"- final: {'PASS' if result.ok else 'FAIL'}",
        "",
        "## Errors",
        "",
        *(f"- {error}" for error in result.errors),
        *(["- none"] if not result.errors else []),
        "",
        "L3.8 hybrid RAG evidence QA FULL PASS" if result.ok else "L3.8 hybrid RAG evidence QA FULL FAIL",
        "",
    ]
    path = result.project_dir / verify_paths(result.output_prefix)["md"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def verify_evidence(conn: sqlite3.Connection, answer: dict[str, Any], result: VerifyResult) -> None:
    if answer.get("grounded_answer"):
        if int(answer.get("evidence_count", 0)) < 1:
            result.errors.append(f"grounded_answer without evidence: {answer.get('query_id')}")
        if "evidence_refs:" not in str(answer.get("grounded_answer", "")):
            result.errors.append(f"grounded_answer missing evidence_refs: {answer.get('query_id')}")
    if int(answer.get("evidence_count", 0)) == 0 and answer.get("answer_status") != "insufficient_evidence":
        result.errors.append(f"zero evidence answer not marked insufficient_evidence: {answer.get('query_id')}")
    for evidence in answer.get("evidence", []):
        child_segment_id = str(evidence.get("child_segment_id", ""))
        row = qa.fetch_segment_row(conn, child_segment_id)
        if row is None:
            result.missing_sqlite_child_count += 1
            result.errors.append(f"missing SQLite child_segment: {child_segment_id}")
            continue
        if int(row["chapter_num"]) not in result.chapter_scope:
            result.errors.append(f"evidence chapter out of sample scope: {child_segment_id}")
        expected_hash = qa.evidence_hash_for_row(row)
        if evidence.get("evidence_hash") != expected_hash:
            result.hash_mismatch_count += 1
            result.errors.append(f"evidence_hash mismatch: {child_segment_id}")


def run_verification(
    project_dir: Path | str,
    *,
    input_prefix: str = qa.DEFAULT_INPUT_PREFIX,
    expect_output_prefix: str = qa.DEFAULT_OUTPUT_PREFIX,
    sample_chapters: str = child_builder.DEFAULT_SAMPLE_CHAPTERS,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    scope = child_builder.parse_scope(sample_chapters, None)
    db_path = root / child_builder.DB_RELATIVE_PATH
    result = VerifyResult(False, root, db_path, input_prefix, expect_output_prefix, scope)
    try:
        verify_output_files(root, expect_output_prefix, result)
        output = load_json(root / qa.output_paths(expect_output_prefix)["answers_json"])
        manifest = load_json(root / qa.output_paths(expect_output_prefix)["manifest"])
        retrieval_manifest = load_json(root / qa.input_paths(input_prefix)["manifest"])
        result.query_count = int(manifest.get("query_count", 0))
        result.answer_count = int(manifest.get("answer_count", 0))
        if result.query_count != int(retrieval_manifest.get("query_count", -1)):
            result.errors.append("query_count does not match L3.7b retrieval query_count")
        answers = list(output.get("answers", []))
        if len(answers) != result.query_count:
            result.errors.append("not every query has an answer row")
        result.zero_answer_count = sum(1 for answer in answers if int(answer.get("evidence_count", 0)) == 0)
        if int(manifest.get("hash_mismatch_count", 0)) > 0:
            result.errors.append("manifest hash_mismatch_count > 0")
        if int(manifest.get("missing_sqlite_child_count", 0)) > 0:
            result.errors.append("manifest missing_sqlite_child_count > 0")
        result.source_table_mutation_count = int(manifest.get("source_table_mutation_count", 0))
        if result.source_table_mutation_count:
            result.errors.append("manifest source_table_mutation_count > 0")
        conn = qa.connect_sqlite_readonly(db_path)
        try:
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
            if result.forbidden_final_table_count:
                result.errors.append("forbidden final tables exist")
            for answer in answers:
                verify_evidence(conn, answer, result)
            current_counts = qa.row_counts(conn, list(manifest.get("source_table_names", [])))
            if current_counts != manifest.get("source_row_counts_after", {}):
                result.source_table_mutation_count += 1
                result.errors.append("source table row counts changed after L3.8 QA")
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
    result.error_count = len(result.errors)
    result.ok = result.error_count == 0
    write_json_report(result)
    write_md_report(result)
    return result


def final_status_line(result: VerifyResult) -> str:
    return "L3.8 hybrid RAG evidence QA FULL PASS" if result.ok else "L3.8 hybrid RAG evidence QA FULL FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.8 hybrid RAG evidence QA outputs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--input-prefix", type=str, default=qa.DEFAULT_INPUT_PREFIX)
    parser.add_argument("--expect-output-prefix", type=str, default=qa.DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--sample-chapters", type=str, default=child_builder.DEFAULT_SAMPLE_CHAPTERS)
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        input_prefix=args.input_prefix,
        expect_output_prefix=args.expect_output_prefix,
        sample_chapters=args.sample_chapters,
    )
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
