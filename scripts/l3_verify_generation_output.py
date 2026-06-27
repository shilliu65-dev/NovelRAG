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

from scripts import l3_prompt_context_pack_builder as pack_builder
from scripts import l3_real_llm_consumer_sandbox as sandbox

VALID_RESPONSE_STATUSES = {"ready", "partial", "insufficient", "invalid"}
VALID_CLAIM_STATUSES = {"supported", "unsupported", "refused"}
FORBIDDEN_INFERENCE_PATTERNS = [
    "确认发生",
    "已证明",
    "最终时间线",
    "最终关系图谱",
    "状态机已确认",
    "关系已确认",
    "事件已确认",
    "可以确定",
    "必然导致",
    "因此证明",
    "真实因果",
    "官方结论",
    "final_event",
    "final_timeline",
    "final_relationship_graph",
    "final_state_machine",
]


@dataclass
class VerifyResult:
    ok: bool
    project_dir: Path
    context_pack_prefix: str
    llm_output_prefix: str
    response_count: int = 0
    checked_response_count: int = 0
    ready_response_count: int = 0
    partial_response_count: int = 0
    insufficient_response_count: int = 0
    invalid_response_count: int = 0
    unsupported_claim_count: int = 0
    out_of_pack_fact_ref_count: int = 0
    out_of_pack_evidence_ref_count: int = 0
    missing_claim_binding_count: int = 0
    forbidden_inference_violation_count: int = 0
    token_budget_violation_count: int = 0
    schema_error_count: int = 0
    source_table_mutation_count: int = 0
    forbidden_final_table_count: int = 0
    chroma_accessed: bool = False
    embedding_called: bool = False
    sqlite_written: bool = False
    error_count: int = 0
    warning_count: int = 0
    semantic_verification_level: str = "structural_id_bound"
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def verify_paths(llm_output_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "json": base / f"{llm_output_prefix}_verify_report.json",
        "md": base / f"{llm_output_prefix}_verify_report.md",
    }


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def missing_output_error(llm_output_prefix: str) -> str:
    return (
        f"Missing L3.11 output file: outputs/{llm_output_prefix}.json\n"
        "Run L3.11 mock or real sandbox first."
    )


def write_json_report(result: VerifyResult) -> None:
    payload = {
        "layer": "L3.12",
        "verified_layer": "L3.11",
        "context_pack_prefix": result.context_pack_prefix,
        "llm_output_prefix": result.llm_output_prefix,
        "response_count": result.response_count,
        "checked_response_count": result.checked_response_count,
        "ready_response_count": result.ready_response_count,
        "partial_response_count": result.partial_response_count,
        "insufficient_response_count": result.insufficient_response_count,
        "invalid_response_count": result.invalid_response_count,
        "unsupported_claim_count": result.unsupported_claim_count,
        "out_of_pack_fact_ref_count": result.out_of_pack_fact_ref_count,
        "out_of_pack_evidence_ref_count": result.out_of_pack_evidence_ref_count,
        "missing_claim_binding_count": result.missing_claim_binding_count,
        "forbidden_inference_violation_count": result.forbidden_inference_violation_count,
        "token_budget_violation_count": result.token_budget_violation_count,
        "schema_error_count": result.schema_error_count,
        "source_table_mutation_count": result.source_table_mutation_count,
        "forbidden_final_table_count": result.forbidden_final_table_count,
        "chroma_accessed": result.chroma_accessed,
        "embedding_called": result.embedding_called,
        "sqlite_written": result.sqlite_written,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "semantic_verification_level": result.semantic_verification_level,
        "status": "FULL PASS" if result.ok else "FAIL",
        "errors": result.errors,
        "warnings": result.warnings,
        "verified_at": now_iso(),
    }
    path = result.project_dir / verify_paths(result.llm_output_prefix)["json"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md_report(result: VerifyResult) -> None:
    lines = [
        "# L3.12 Generation Verifier Report",
        "",
        "## Summary",
        "",
        f"- status: {'FULL PASS' if result.ok else 'FAIL'}",
        f"- verified_layer: L3.11",
        f"- semantic_verification_level: {result.semantic_verification_level}",
        "",
        "## Inputs",
        "",
        f"- context_pack_prefix: {result.context_pack_prefix}",
        f"- llm_output_prefix: {result.llm_output_prefix}",
        "",
        "## Metrics",
        "",
        f"- response_count: {result.response_count}",
        f"- checked_response_count: {result.checked_response_count}",
        f"- unsupported_claim_count: {result.unsupported_claim_count}",
        f"- out_of_pack_fact_ref_count: {result.out_of_pack_fact_ref_count}",
        f"- out_of_pack_evidence_ref_count: {result.out_of_pack_evidence_ref_count}",
        f"- missing_claim_binding_count: {result.missing_claim_binding_count}",
        f"- forbidden_inference_violation_count: {result.forbidden_inference_violation_count}",
        f"- token_budget_violation_count: {result.token_budget_violation_count}",
        f"- schema_error_count: {result.schema_error_count}",
        "",
        "## Response status breakdown",
        "",
        f"- ready_response_count: {result.ready_response_count}",
        f"- partial_response_count: {result.partial_response_count}",
        f"- insufficient_response_count: {result.insufficient_response_count}",
        f"- invalid_response_count: {result.invalid_response_count}",
        "",
        "## Schema violations",
        "",
        *(f"- {error}" for error in result.errors if "schema" in error or "missing" in error or "invalid response_status" in error),
        *(["- none"] if not any("schema" in error or "missing" in error or "invalid response_status" in error for error in result.errors) else []),
        "",
        "## Out-of-pack references",
        "",
        *(f"- {error}" for error in result.errors if "out-of-pack" in error),
        *(["- none"] if not any("out-of-pack" in error for error in result.errors) else []),
        "",
        "## Unsupported claims",
        "",
        *(f"- {error}" for error in result.errors if "unsupported_claims" in error or "unsupported claim" in error),
        *(["- none"] if not any("unsupported_claims" in error or "unsupported claim" in error for error in result.errors) else []),
        "",
        "## Forbidden inference violations",
        "",
        *(f"- {error}" for error in result.errors if "forbidden inference" in error),
        *(["- none"] if not any("forbidden inference" in error for error in result.errors) else []),
        "",
        "## Token budget check",
        "",
        *(f"- {error}" for error in result.errors if "token_budget" in error),
        *(["- none"] if not any("token_budget" in error for error in result.errors) else []),
        "",
        "## Mutation guard",
        "",
        f"- source_table_mutation_count: {result.source_table_mutation_count}",
        f"- sqlite_written: {result.sqlite_written}",
        f"- chroma_accessed: {result.chroma_accessed}",
        f"- embedding_called: {result.embedding_called}",
        "",
        "## Final table guard",
        "",
        f"- forbidden_final_table_count: {result.forbidden_final_table_count}",
        "",
        "## PASS / FAIL conclusion",
        "",
        final_status_line(result),
        "",
    ]
    path = result.project_dir / verify_paths(result.llm_output_prefix)["md"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def contains_factual_content(text: str) -> bool:
    normalized = text.strip()
    if not normalized:
        return False
    boilerplate = {
        "Insufficient evidence. evidence_refs: none",
        "Invalid response. evidence_refs: none",
    }
    if normalized in boilerplate:
        return False
    return True


def collect_pack_ids(pack: dict[str, Any]) -> tuple[set[str], set[str], set[str], set[str], set[int]]:
    fact_ids = {str(item.get("fact_id", "")) for item in pack.get("allowed_facts", [])}
    evidence_ids = {str(item.get("evidence_ref_id", "")) for item in pack.get("evidence_refs", [])}
    child_segment_ids = {str(item.get("child_segment_id", "")) for item in pack.get("evidence_refs", [])}
    scene_block_ids = {str(item.get("scene_block_id", "")) for item in pack.get("evidence_refs", [])}
    chapter_nums = {int(item.get("chapter_num", -1)) for item in pack.get("evidence_refs", []) if item.get("chapter_num") is not None}
    return fact_ids, evidence_ids, child_segment_ids, scene_block_ids, chapter_nums


def scan_text_id_leaks(
    text: str,
    *,
    fact_ids: set[str],
    evidence_ids: set[str],
    child_segment_ids: set[str],
    scene_block_ids: set[str],
    chapter_nums: set[int],
) -> list[str]:
    errors: list[str] = []
    for fact_id in re.findall(r"FACT-\d+", text):
        if fact_id not in fact_ids:
            errors.append(f"out-of-pack fact id in text: {fact_id}")
    for evidence_id in re.findall(r"EVID-\d+", text):
        if evidence_id not in evidence_ids:
            errors.append(f"out-of-pack evidence id in text: {evidence_id}")
    for child_segment_id in re.findall(r"child_segment_id=([A-Za-z0-9_:\-]+)", text):
        if child_segment_id not in child_segment_ids:
            errors.append(f"out-of-pack child_segment_id in text: {child_segment_id}")
    for scene_block_id in re.findall(r"scene(?:_block)?_id=([A-Za-z0-9_:\-]+)", text):
        if scene_block_id not in scene_block_ids:
            errors.append(f"out-of-pack scene_id in text: {scene_block_id}")
    for chapter_text in re.findall(r"chapter(?:_num)?=(\d+)", text):
        chapter_num = int(chapter_text)
        if chapter_num not in chapter_nums:
            errors.append(f"out-of-pack chapter_num in text: {chapter_num}")
    return errors


def check_forbidden_inference(text: str) -> int:
    count = 0
    for pattern in FORBIDDEN_INFERENCE_PATTERNS:
        if pattern in text:
            count += 1
    return count


def verify_response(
    response: dict[str, Any],
    pack: dict[str, Any],
    result: VerifyResult,
) -> None:
    response_id = str(response.get("context_pack_id", ""))
    response_status = str(response.get("response_status", ""))
    response_text = str(response.get("response_text", ""))
    claim_units = response.get("claim_units", [])
    if not isinstance(claim_units, list):
        claim_units = []
        result.schema_error_count += 1
        result.errors.append(f"schema error: claim_units must be an array: {response_id}")
    unsupported_claims = [str(item) for item in response.get("unsupported_claims", [])] if isinstance(response.get("unsupported_claims", []), list) else []

    if response_status not in VALID_RESPONSE_STATUSES:
        result.schema_error_count += 1
        result.errors.append(f"invalid response_status: {response_id}:{response_status}")
        return

    fact_ids, evidence_ids, child_segment_ids, scene_block_ids, chapter_nums = collect_pack_ids(pack)
    used_fact_ids = [str(item) for item in response.get("used_fact_ids", [])] if isinstance(response.get("used_fact_ids", []), list) else []
    used_evidence_ids = [str(item) for item in response.get("used_evidence_ids", [])] if isinstance(response.get("used_evidence_ids", []), list) else []

    if pack.get("context_pack_status") != "ready" and response_status == "ready":
        result.errors.append(f"non-ready context pack produced ready response: {response_id}")
    if not pack.get("allowed_facts") and response_status == "ready":
        result.errors.append(f"empty allowed_facts produced ready response: {response_id}")
    if not response_text.strip() and response_status == "ready":
        result.errors.append(f"empty response_text with ready status: {response_id}")
    if unsupported_claims and response_status == "ready":
        result.errors.append(f"unsupported_claims present with ready status: {response_id}")
    if not claim_units and contains_factual_content(response_text):
        result.errors.append(f"missing claim_units for factual response_text: {response_id}")
        result.schema_error_count += 1

    for fact_id in used_fact_ids:
        if fact_id not in fact_ids:
            result.out_of_pack_fact_ref_count += 1
            result.errors.append(f"out-of-pack fact reference: {response_id}:{fact_id}")
    for evidence_id in used_evidence_ids:
        if evidence_id not in evidence_ids:
            result.out_of_pack_evidence_ref_count += 1
            result.errors.append(f"out-of-pack evidence reference: {response_id}:{evidence_id}")

    forbidden_hits = check_forbidden_inference(response_text)
    if forbidden_hits:
        result.forbidden_inference_violation_count += forbidden_hits
        result.errors.append(f"forbidden inference in response_text: {response_id}")

    for leak in scan_text_id_leaks(
        response_text,
        fact_ids=fact_ids,
        evidence_ids=evidence_ids,
        child_segment_ids=child_segment_ids,
        scene_block_ids=scene_block_ids,
        chapter_nums=chapter_nums,
    ):
        if "fact id" in leak:
            result.out_of_pack_fact_ref_count += 1
        elif "evidence id" in leak:
            result.out_of_pack_evidence_ref_count += 1
        result.errors.append(f"{response_id}: {leak}")

    if int(response.get("token_budget_estimate", pack_builder.estimate_tokens(response_text))) > int(pack.get("token_budget_estimate", 0)):
        result.token_budget_violation_count += 1
        result.errors.append(f"token_budget violation: {response_id}")

    for index, claim in enumerate(claim_units, start=1):
        if not isinstance(claim, dict):
            result.schema_error_count += 1
            result.errors.append(f"schema error: claim_unit must be object: {response_id}:{index}")
            continue
        missing_fields = [field_name for field_name in ("claim_id", "claim_text", "used_fact_ids", "used_evidence_ids", "claim_status") if field_name not in claim]
        if missing_fields:
            result.schema_error_count += 1
            result.errors.append(f"schema error: claim_unit missing {','.join(missing_fields)}: {response_id}:{index}")
        claim_status = str(claim.get("claim_status", ""))
        if claim_status not in VALID_CLAIM_STATUSES:
            result.schema_error_count += 1
            result.errors.append(f"schema error: invalid claim_status: {response_id}:{index}:{claim_status}")

        claim_text = str(claim.get("claim_text", ""))
        claim_fact_ids = [str(item) for item in claim.get("used_fact_ids", [])] if isinstance(claim.get("used_fact_ids", []), list) else []
        claim_evidence_ids = [str(item) for item in claim.get("used_evidence_ids", [])] if isinstance(claim.get("used_evidence_ids", []), list) else []
        claim_forbidden_hits = check_forbidden_inference(claim_text)
        if claim_forbidden_hits:
            result.forbidden_inference_violation_count += claim_forbidden_hits
            result.errors.append(f"forbidden inference in claim_text: {response_id}:{index}")
        for fact_id in claim_fact_ids:
            if fact_id not in fact_ids:
                result.out_of_pack_fact_ref_count += 1
                result.errors.append(f"out-of-pack claim fact reference: {response_id}:{fact_id}")
        for evidence_id in claim_evidence_ids:
            if evidence_id not in evidence_ids:
                result.out_of_pack_evidence_ref_count += 1
                result.errors.append(f"out-of-pack claim evidence reference: {response_id}:{evidence_id}")

        if claim_status == "supported":
            if not claim_fact_ids:
                result.missing_claim_binding_count += 1
                result.errors.append(f"supported claim missing fact binding: {response_id}:{index}")
            if not claim_evidence_ids:
                result.missing_claim_binding_count += 1
                result.errors.append(f"supported claim missing evidence binding: {response_id}:{index}")
        if claim_status == "unsupported":
            result.unsupported_claim_count += 1
            if not unsupported_claims and response_status == "ready":
                result.errors.append(f"unsupported claim not reflected in unsupported_claims: {response_id}:{index}")

    result.checked_response_count += 1


def run_verification(
    project_dir: Path | str,
    *,
    context_pack_prefix: str = pack_builder.DEFAULT_OUTPUT_PREFIX,
    llm_output_prefix: str = sandbox.DEFAULT_OUTPUT_PREFIX,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    result = VerifyResult(False, root, context_pack_prefix, llm_output_prefix)
    try:
        output_json_path = root / sandbox.output_paths(llm_output_prefix)["json"]
        if not output_json_path.exists():
            raise RuntimeError(missing_output_error(llm_output_prefix))

        context_payload = load_json(root / pack_builder.output_paths(context_pack_prefix)["packs"])
        output_payload = load_json(output_json_path)
        manifest = load_json(root / sandbox.output_paths(llm_output_prefix)["manifest"])

        responses = output_payload.get("responses")
        if not isinstance(responses, list):
            raise RuntimeError("L3.11 output missing responses array")
        context_packs = list(context_payload.get("context_packs", []))
        pack_by_id = {str(pack.get("query_id", "")): pack for pack in context_packs}

        result.response_count = len(responses)
        if len(responses) > len(context_packs):
            result.errors.append("responses count exceeds context_pack_count")
        result.ready_response_count = sum(1 for response in responses if response.get("response_status") == "ready")
        result.partial_response_count = sum(1 for response in responses if response.get("response_status") == "partial")
        result.insufficient_response_count = sum(1 for response in responses if response.get("response_status") == "insufficient")
        result.invalid_response_count = sum(1 for response in responses if response.get("response_status") == "invalid")

        result.chroma_accessed = bool(manifest.get("chroma_accessed", False))
        result.embedding_called = bool(manifest.get("embedding_called", False))
        result.sqlite_written = bool(manifest.get("sqlite_written", False))
        result.source_table_mutation_count = int(manifest.get("source_table_mutation_count", 0))
        result.forbidden_final_table_count = int(manifest.get("forbidden_final_table_count", 0))

        if result.chroma_accessed:
            result.errors.append("manifest chroma_accessed must be false")
        if result.embedding_called:
            result.errors.append("manifest embedding_called must be false")
        if result.sqlite_written:
            result.errors.append("manifest sqlite_written must be false")
        if result.source_table_mutation_count != 0:
            result.errors.append("manifest source_table_mutation_count must be 0")
        if result.forbidden_final_table_count != 0:
            result.errors.append("manifest forbidden_final_table_count must be 0")

        for response in responses:
            context_pack_id = str(response.get("context_pack_id", ""))
            query_id = str(response.get("query_id", ""))
            if context_pack_id not in pack_by_id:
                result.schema_error_count += 1
                result.errors.append(f"schema error: unknown context_pack_id: {context_pack_id}")
                continue
            pack = pack_by_id[context_pack_id]
            if query_id != str(pack.get("query_id", "")):
                result.schema_error_count += 1
                result.errors.append(f"schema error: query_id mismatch for context_pack_id {context_pack_id}")
            verify_response(response, pack, result)
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))

    result.error_count = len(result.errors)
    result.warning_count = len(result.warnings)
    result.ok = result.error_count == 0
    write_json_report(result)
    write_md_report(result)
    return result


def final_status_line(result: VerifyResult) -> str:
    return "L3.12 generation verifier FULL PASS" if result.ok else "L3.12 generation verifier FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.11 sandbox output against L3.9 context packs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--context-pack-prefix", type=str, default=pack_builder.DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--llm-output-prefix", type=str, default=sandbox.DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        context_pack_prefix=args.context_pack_prefix,
        llm_output_prefix=args.llm_output_prefix,
    )
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
