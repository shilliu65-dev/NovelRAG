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
from scripts import l3_prompt_context_pack_builder as builder
from scripts import l3_prompt_context_pack_reporter as reporter

VALID_PACK_STATUSES = {"ready", "partial", "insufficient"}


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
    context_pack_count: int = 0
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
        raise RuntimeError(f"required L3.9 file missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_report(result: VerifyResult) -> None:
    payload = {
        "verified_at": now_iso(),
        "input_prefix": result.input_prefix,
        "output_prefix": result.output_prefix,
        "chapter_scope": result.chapter_scope,
        "query_count": result.query_count,
        "answer_count": result.answer_count,
        "context_pack_count": result.context_pack_count,
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
        "# L3.9 Prompt Context Pack Verify Report",
        "",
        f"- verified_at: {now_iso()}",
        f"- input_prefix: {result.input_prefix}",
        f"- output_prefix: {result.output_prefix}",
        f"- chapter_scope: {','.join(str(item) for item in result.chapter_scope)}",
        f"- query_count: {result.query_count}",
        f"- answer_count: {result.answer_count}",
        f"- context_pack_count: {result.context_pack_count}",
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
        final_status_line(result),
        "",
    ]
    path = result.project_dir / verify_paths(result.output_prefix)["md"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def verify_output_files(root: Path, output_prefix: str, result: VerifyResult) -> None:
    required = [
        builder.output_paths(output_prefix)["packs"],
        reporter.markdown_paths(output_prefix)["pack_md"],
        builder.output_paths(output_prefix)["manifest"],
        reporter.markdown_paths(output_prefix)["report_md"],
    ]
    for relative_path in required:
        if not (root / relative_path).exists():
            result.errors.append(f"L3.9 output file missing: {relative_path.as_posix()}")


def verify_pack_structure(conn: sqlite3.Connection, pack: dict[str, Any], result: VerifyResult) -> None:
    query_id = str(pack.get("query_id", ""))
    query_text = str(pack.get("query_text", ""))
    status = str(pack.get("context_pack_status", ""))
    allowed_facts = list(pack.get("allowed_facts", []))
    evidence_refs = list(pack.get("evidence_refs", []))
    prompt_context_text = str(pack.get("prompt_context_text", ""))

    if not query_text:
        result.errors.append(f"context pack missing query_text: {query_id}")
    if not status:
        result.errors.append(f"context pack missing context_pack_status: {query_id}")
    elif status not in VALID_PACK_STATUSES:
        result.errors.append(f"invalid context_pack_status: {query_id}:{status}")

    evidence_ids = {str(ref.get("evidence_ref_id", "")) for ref in evidence_refs if ref.get("evidence_ref_id")}

    if status == "ready":
        if len(evidence_refs) < 1:
            result.errors.append(f"ready pack missing evidence_refs: {query_id}")
        if len(allowed_facts) < 1:
            result.errors.append(f"ready pack missing allowed_facts: {query_id}")
        if not prompt_context_text.strip():
            result.errors.append(f"ready pack missing prompt_context_text: {query_id}")

    if status == "insufficient" and not evidence_refs and allowed_facts:
        result.errors.append(f"insufficient pack fabricated allowed_facts without evidence: {query_id}")

    for fact in allowed_facts:
        source_ids = list(fact.get("source_evidence_ref_ids", []))
        if not source_ids:
            result.errors.append(f"allowed_fact missing source_evidence_ref_ids: {query_id}:{fact.get('fact_id', '')}")
            continue
        for source_id in source_ids:
            if str(source_id) not in evidence_ids:
                result.errors.append(f"allowed_fact source_evidence_ref_id not found in pack: {query_id}:{source_id}")

    for evidence in evidence_refs:
        for field_name in ("evidence_ref_id", "child_segment_id", "scene_block_id", "chapter_num", "text_excerpt", "evidence_hash"):
            value = evidence.get(field_name)
            if value in (None, ""):
                result.errors.append(f"evidence_ref missing {field_name}: {query_id}")
        child_segment_id = str(evidence.get("child_segment_id", ""))
        row = conn.execute(
            """
            SELECT chapter_num
            FROM l3_child_segment
            WHERE child_segment_id = ?
            """,
            (child_segment_id,),
        ).fetchone()
        if row is None:
            result.missing_sqlite_child_count += 1
            result.errors.append(f"missing SQLite child_segment: {child_segment_id}")
            continue
        chapter_num = int(evidence.get("chapter_num", -1))
        if chapter_num not in result.chapter_scope:
            result.errors.append(f"evidence chapter out of sample scope: {query_id}:{child_segment_id}")

    for required_text in ("Forbidden inference rules", "Evidence", "Allowed facts"):
        if required_text not in prompt_context_text:
            result.errors.append(f"prompt_context_text missing {required_text}: {query_id}")


def run_verification(
    project_dir: Path | str,
    *,
    input_prefix: str = builder.DEFAULT_INPUT_PREFIX,
    expect_output_prefix: str = builder.DEFAULT_OUTPUT_PREFIX,
    sample_chapters: str = child_builder.DEFAULT_SAMPLE_CHAPTERS,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    scope = child_builder.parse_scope(sample_chapters, None)
    db_path = root / child_builder.DB_RELATIVE_PATH
    result = VerifyResult(False, root, db_path, input_prefix, expect_output_prefix, scope)
    try:
        verify_output_files(root, expect_output_prefix, result)
        pack_payload = load_json(root / builder.output_paths(expect_output_prefix)["packs"])
        manifest = load_json(root / builder.output_paths(expect_output_prefix)["manifest"])
        answers_payload = load_json(root / builder.input_paths(input_prefix)["answers"])
        _answers_manifest = load_json(root / builder.input_paths(input_prefix)["manifest"])

        context_packs = list(pack_payload.get("context_packs", []))
        result.query_count = int(pack_payload.get("query_count", len(context_packs)))
        result.answer_count = int(answers_payload.get("answer_count", len(answers_payload.get("answers", []))))
        result.context_pack_count = len(context_packs)

        if result.context_pack_count != result.answer_count:
            result.errors.append("context_pack_count does not match L3.8 answer_count")

        query_ids = [str(pack.get("query_id", "")) for pack in context_packs]
        if len(set(query_ids)) != len(query_ids):
            result.errors.append("query_id is not unique across context packs")

        if int(manifest.get("hash_mismatch_count", -1)) != 0:
            result.errors.append("manifest hash_mismatch_count != 0")
        if int(manifest.get("missing_sqlite_child_count", -1)) != 0:
            result.errors.append("manifest missing_sqlite_child_count != 0")
        result.source_table_mutation_count = int(manifest.get("source_table_mutation_count", -1))
        if result.source_table_mutation_count != 0:
            result.errors.append("manifest source_table_mutation_count != 0")
        result.forbidden_final_table_count = int(manifest.get("forbidden_final_table_count", -1))
        if result.forbidden_final_table_count != 0:
            result.errors.append("manifest forbidden_final_table_count != 0")
        if int(manifest.get("error_count", -1)) != 0:
            result.errors.append("manifest error_count != 0")

        conn = builder.connect_sqlite_readonly(db_path)
        try:
            forbidden_tables = child_builder.forbidden_final_tables(conn)
            result.forbidden_final_table_count = len(forbidden_tables)
            if forbidden_tables:
                result.errors.append("forbidden final tables exist")
            for pack in context_packs:
                verify_pack_structure(conn, pack, result)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
    result.hash_mismatch_count = 0
    result.error_count = len(result.errors)
    result.ok = result.error_count == 0
    write_json_report(result)
    write_md_report(result)
    return result


def final_status_line(result: VerifyResult) -> str:
    return "L3.9 prompt context pack FULL PASS" if result.ok else "L3.9 prompt context pack FULL FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.9 prompt context pack outputs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--input-prefix", type=str, default=builder.DEFAULT_INPUT_PREFIX)
    parser.add_argument("--expect-output-prefix", type=str, default=builder.DEFAULT_OUTPUT_PREFIX)
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
