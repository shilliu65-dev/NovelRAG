from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as child_builder
from scripts import l3_prompt_consumer_dryrun as dryrun
from scripts import l3_prompt_context_pack_builder as pack_builder


@dataclass
class VerifyResult:
    ok: bool
    project_dir: Path
    db_path: Path
    input_prefix: str
    output_prefix: str
    query_count: int = 0
    context_pack_count: int = 0
    response_count: int = 0
    token_budget: int = 0
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
        raise RuntimeError(f"required L3.10 file missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_report(result: VerifyResult) -> None:
    payload = {
        "verified_at": now_iso(),
        "input_prefix": result.input_prefix,
        "output_prefix": result.output_prefix,
        "query_count": result.query_count,
        "context_pack_count": result.context_pack_count,
        "response_count": result.response_count,
        "token_budget": result.token_budget,
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
        "# L3.10 Prompt Consumer Dryrun Verify Report",
        "",
        f"- verified_at: {now_iso()}",
        f"- input_prefix: {result.input_prefix}",
        f"- output_prefix: {result.output_prefix}",
        f"- query_count: {result.query_count}",
        f"- context_pack_count: {result.context_pack_count}",
        f"- response_count: {result.response_count}",
        f"- token_budget: {result.token_budget}",
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
        dryrun.output_paths(output_prefix)["json"],
        dryrun.output_paths(output_prefix)["md"],
        dryrun.output_paths(output_prefix)["manifest"],
    ]
    for relative_path in required:
        if not (root / relative_path).exists():
            result.errors.append(f"L3.10 output file missing: {relative_path.as_posix()}")


def response_contains_forbidden_inference(text: str) -> bool:
    patterns = (
        r"发明",
        r"虚构",
        r"推测",
        r"猜测",
        r"最终事实",
        r"final_event",
        r"final_timeline",
        r"final_relationship_graph",
        r"final_state_machine",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)


def verify_response(response: dict[str, Any], pack: dict[str, Any], result: VerifyResult) -> None:
    context_pack_id = str(response.get("context_pack_id", ""))
    allowed_fact_ids = {str(item.get("fact_id", "")) for item in pack.get("allowed_facts", [])}
    evidence_ids = {str(item.get("evidence_ref_id", "")) for item in pack.get("evidence_refs", [])}
    used_fact_ids = [str(item) for item in response.get("used_fact_ids", [])]
    used_evidence_ids = [str(item) for item in response.get("used_evidence_ids", [])]
    response_text = str(response.get("response_text", ""))

    for fact_id in used_fact_ids:
        if fact_id not in allowed_fact_ids:
            result.errors.append(f"used_fact_id not in allowed_facts: {context_pack_id}:{fact_id}")
    for evidence_id in used_evidence_ids:
        if evidence_id not in evidence_ids:
            result.errors.append(f"used_evidence_id not in evidence_refs: {context_pack_id}:{evidence_id}")

    if response_contains_forbidden_inference(response_text):
        result.errors.append(f"response_text contains forbidden inference pattern: {context_pack_id}")

    for line in response_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("Dry-run response") or stripped.startswith("Insufficient evidence"):
            continue
        if stripped.startswith("- ") and not re.search(r"\[EVID-\d+(,EVID-\d+)*\]$", stripped):
            result.errors.append(f"response fact line missing evidence_ref citation: {context_pack_id}")

    if int(response.get("token_budget_estimate", 0)) > result.token_budget:
        result.errors.append(f"response exceeds token_budget: {context_pack_id}")

    if not pack.get("allowed_facts") and response.get("response_status") != "insufficient":
        result.errors.append(f"empty allowed_facts must produce insufficient response: {context_pack_id}")

    if pack.get("context_pack_status") != "ready" and response.get("response_status") == "ready":
        result.errors.append(f"non-ready context pack produced ready response: {context_pack_id}")


def run_verification(
    project_dir: Path | str,
    *,
    input_prefix: str = dryrun.DEFAULT_INPUT_PREFIX,
    expect_output_prefix: str = dryrun.DEFAULT_OUTPUT_PREFIX,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    db_path = root / child_builder.DB_RELATIVE_PATH
    result = VerifyResult(False, root, db_path, input_prefix, expect_output_prefix)
    try:
        verify_output_files(root, expect_output_prefix, result)
        pack_payload = load_json(root / dryrun.input_paths(input_prefix)["json"])
        pack_manifest = load_json(root / dryrun.input_paths(input_prefix)["manifest"])
        response_payload = load_json(root / dryrun.output_paths(expect_output_prefix)["json"])
        response_manifest = load_json(root / dryrun.output_paths(expect_output_prefix)["manifest"])

        context_packs = list(pack_payload.get("context_packs", []))
        responses = list(response_payload.get("responses", []))
        result.query_count = int(response_payload.get("query_count", len(responses)))
        result.context_pack_count = len(context_packs)
        result.response_count = len(responses)
        result.token_budget = int(pack_manifest.get("token_budget", 0))
        result.source_table_mutation_count = int(response_manifest.get("source_table_mutation_count", -1))
        result.forbidden_final_table_count = int(response_manifest.get("forbidden_final_table_count", -1))

        if result.response_count != result.context_pack_count:
            result.errors.append("response_count does not match input context_pack_count")
        if int(response_manifest.get("error_count", -1)) != 0:
            result.errors.append("manifest error_count != 0")
        if result.source_table_mutation_count != 0:
            result.errors.append("manifest source_table_mutation_count != 0")
        if result.forbidden_final_table_count != 0:
            result.errors.append("manifest forbidden_final_table_count != 0")

        pack_by_id = {str(pack.get("query_id", "")): pack for pack in context_packs}
        for response in responses:
            context_pack_id = str(response.get("context_pack_id", ""))
            if context_pack_id not in pack_by_id:
                result.errors.append(f"response has unknown context_pack_id: {context_pack_id}")
                continue
            verify_response(response, pack_by_id[context_pack_id], result)

        conn = dryrun.connect_sqlite_readonly(db_path)
        try:
            forbidden_tables = child_builder.forbidden_final_tables(conn)
            if forbidden_tables:
                result.forbidden_final_table_count = len(forbidden_tables)
                result.errors.append("forbidden final tables exist")
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
    return "L3.10 prompt consumer dryrun FULL PASS" if result.ok else "L3.10 prompt consumer dryrun FULL FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.10 prompt consumer dryrun outputs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--input-prefix", type=str, default=dryrun.DEFAULT_INPUT_PREFIX)
    parser.add_argument("--expect-output-prefix", type=str, default=dryrun.DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        input_prefix=args.input_prefix,
        expect_output_prefix=args.expect_output_prefix,
    )
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
