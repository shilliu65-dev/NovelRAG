from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as child_builder
from scripts import l3_prompt_context_pack_builder as pack_builder

DEFAULT_INPUT_PREFIX = "l3_prompt_context_pack_sample"
DEFAULT_OUTPUT_PREFIX = "l3_real_llm_consumer_sandbox_sample"
DEFAULT_PROVIDER = "openai_compatible"
DEFAULT_MAX_OUTPUT_TOKENS = 1200
FORBIDDEN_SQL_TOKENS = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE")
VALID_RESPONSE_STATUSES = {"ready", "partial", "insufficient", "invalid"}


@dataclass
class SandboxResult:
    ok: bool
    project_dir: Path
    db_path: Path
    input_prefix: str
    output_prefix: str
    sample_chapters: list[int]
    mock_llm: bool
    provider: str
    temperature: float
    max_output_tokens: int
    status: str = "FAIL"
    reason: str = ""
    request_attempt_count: int = 0
    request_sent_count: int = 0
    api_success_count: int = 0
    api_error_count: int = 0
    exception_count: int = 0
    skipped_by_guard_count: int = 0
    context_pack_count: int = 0
    llm_call_count: int = 0
    response_count: int = 0
    valid_response_count: int = 0
    ready_response_count: int = 0
    partial_response_count: int = 0
    insufficient_response_count: int = 0
    invalid_response_count: int = 0
    empty_raw_response_count: int = 0
    unsupported_claim_count: int = 0
    out_of_pack_fact_ref_count: int = 0
    out_of_pack_evidence_ref_count: int = 0
    json_parse_error_count: int = 0
    json_extraction_warning_count: int = 0
    raw_response_wrapped_count: int = 0
    source_table_mutation_count: int = 0
    forbidden_final_table_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    chroma_accessed: bool = False
    embedding_called: bool = False
    sqlite_written: bool = False
    usage_total_input_tokens: int = 0
    usage_total_output_tokens: int = 0
    usage_total_tokens: int = 0
    responses: list[dict[str, Any]] = field(default_factory=list)
    prompt_audit_rows: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class LlmCallResult:
    request_was_attempted: bool = False
    request_was_sent: bool = False
    skipped_by_guard: bool = False
    api_success: bool = False
    api_error: str = ""
    http_status: int | None = None
    exception_type: str = ""
    exception_message: str = ""
    response_field_path_used: str = ""
    raw_response: str = ""
    finish_reason: str = ""
    usage: dict[str, Any] | None = None


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def output_paths(output_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "json": base / f"{output_prefix}.json",
        "md": base / f"{output_prefix}.md",
        "manifest": base / f"{output_prefix}_manifest.json",
        "prompt_audit": base / f"{output_prefix}_prompt_audit.json",
    }


def input_paths(input_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "json": base / f"{input_prefix}.json",
        "manifest": base / f"{input_prefix}_manifest.json",
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required input missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_project_env(project_dir: Path) -> None:
    for name in (".env", ".env.local"):
        path = project_dir / name
        if not path.exists() or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


def reject_write_sql(sql: str) -> None:
    upper = " ".join(sql.upper().split())
    for token in FORBIDDEN_SQL_TOKENS:
        if upper.startswith(token) or f" {token} " in upper:
            raise RuntimeError(f"read_only SQL guard rejected statement containing {token}")


def connect_sqlite_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def row_counts(conn: sqlite3.Connection, table_names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in table_names:
        if not child_builder.object_exists(conn, name, "table"):
            counts[name] = -1
            continue
        reject_write_sql(f"SELECT COUNT(*) FROM {name}")
        counts[name] = int(conn.execute(f"SELECT COUNT(*) FROM {child_builder.quote_ident(name)}").fetchone()[0])
    return counts


def build_prompt(pack: dict[str, Any], *, max_output_tokens: int) -> str:
    schema_text = """Return exactly one valid JSON object with this shape:
{
  "response_status": "ready | partial | insufficient | invalid",
  "response_text": "...",
  "claim_units": [
    {
      "claim_id": "CLAIM-1",
      "claim_text": "...",
      "used_fact_ids": ["FACT-1"],
      "used_evidence_ids": ["EVID-1"],
      "claim_status": "supported | unsupported"
    }
  ],
  "used_fact_ids": ["FACT-1"],
  "used_evidence_ids": ["EVID-1"],
  "unsupported_claims": [],
  "refusal_reason": ""
}"""
    return "\n\n".join(
        [
            "You are a sandboxed grounded generation agent.",
            "Use only the provided prompt context pack. Do not invent facts or references.",
            "JSON-only output rules:",
            "- Output exactly one JSON object and nothing else.",
            "- Do not output Markdown.",
            "- Do not output ```json code blocks.",
            "- Do not output explanatory text, prefixes, or suffixes.",
            "- The response must start with { and end with }.",
            "- All object keys and string values must use double quotes.",
            "- Do not use trailing commas.",
            "- Do not include facts outside the provided evidence.",
            "- If evidence is insufficient, response_status must be insufficient.",
            f"Max output tokens: {max_output_tokens}",
            schema_text,
            str(pack.get("prompt_context_text", "")),
        ]
    )


def mock_llm_raw_response(pack: dict[str, Any], *, force_invalid: bool = False) -> str:
    if force_invalid:
        return '{"response_status": "ready", "response_text": "bad"'
    allowed_facts = list(pack.get("allowed_facts", []))
    source_status = str(pack.get("context_pack_status", "insufficient"))
    if not allowed_facts:
        payload = {
            "response_status": "insufficient",
            "response_text": "Insufficient evidence. evidence_refs: none",
            "claim_units": [],
            "used_fact_ids": [],
            "used_evidence_ids": [],
            "unsupported_claims": [],
            "refusal_reason": "no_allowed_facts",
        }
        return json.dumps(payload, ensure_ascii=False)

    claim_units: list[dict[str, Any]] = []
    used_fact_ids: list[str] = []
    used_evidence_ids: list[str] = []
    lines: list[str] = []
    for index, fact in enumerate(allowed_facts, start=1):
        fact_id = str(fact.get("fact_id", ""))
        fact_evidence_ids = [str(item) for item in fact.get("source_evidence_ref_ids", [])]
        used_fact_ids.append(fact_id)
        for evidence_id in fact_evidence_ids:
            if evidence_id not in used_evidence_ids:
                used_evidence_ids.append(evidence_id)
        claim_units.append(
            {
                "claim_id": f"CLAIM-{index}",
                "claim_text": str(fact.get("fact_text", "")),
                "used_fact_ids": [fact_id],
                "used_evidence_ids": fact_evidence_ids,
                "claim_status": "supported",
            }
        )
        lines.append(f"- {fact.get('fact_text', '')} [{' ,'.join(fact_evidence_ids).replace(' ,', ',')}]")
    payload = {
        "response_status": "ready" if source_status == "ready" else "partial",
        "response_text": "Mock sandbox response from allowed facts only:\n\n" + "\n".join(lines),
        "claim_units": claim_units,
        "used_fact_ids": used_fact_ids,
        "used_evidence_ids": used_evidence_ids,
        "unsupported_claims": [],
        "refusal_reason": "",
    }
    return json.dumps(payload, ensure_ascii=False)


def llm_payload(
    *,
    model: str,
    prompt: str,
    temperature: float,
    max_output_tokens: int,
    include_response_format: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "max_completion_tokens": max_output_tokens,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Return JSON only. Output exactly one JSON object. "
                    "Do not use Markdown, code fences, explanations, prefixes, or suffixes."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    if include_response_format:
        payload["response_format"] = {"type": "json_object"}
    return payload


def post_openai_compatible(url: str, api_key: str, payload: dict[str, Any]) -> tuple[dict[str, Any], int]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8")), int(response.status)


def response_format_unsupported(body: str) -> bool:
    normalized = body.lower()
    return "response_format" in normalized and any(
        marker in normalized
        for marker in (
            "unsupported",
            "not support",
            "not_supported",
            "unknown parameter",
            "unrecognized",
            "invalid parameter",
        )
    )


def content_from_response(raw: dict[str, Any]) -> tuple[str, str, str, dict[str, Any] | None]:
    choices = raw.get("choices", [])
    finish_reason = ""
    usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = str(first.get("finish_reason", ""))
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        content = message.get("content") if isinstance(message, dict) else None
        if content is not None:
            if isinstance(content, list):
                content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
            return str(content), "choices[0].message.content", finish_reason, usage
        if first.get("text") is not None:
            return str(first.get("text", "")), "choices[0].text", finish_reason, usage
    if raw.get("output_text") is not None:
        return str(raw.get("output_text", "")), "output_text", finish_reason, usage
    if raw.get("content") is not None:
        content = raw.get("content")
        if isinstance(content, list):
            content = "".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
        return str(content), "response.content", finish_reason, usage
    return "", "", finish_reason, usage


def call_openai_compatible(*, prompt: str, temperature: float, max_output_tokens: int) -> LlmCallResult:
    call_result = LlmCallResult(request_was_attempted=True)
    base_url = os.environ.get("NOVELRAG_LLM_BASE_URL", "").strip()
    api_key = os.environ.get("NOVELRAG_LLM_API_KEY", "").strip()
    model = os.environ.get("NOVELRAG_LLM_MODEL", "").strip()
    if not base_url or not api_key or not model:
        call_result.skipped_by_guard = True
        call_result.exception_type = "missing_env_var"
        call_result.exception_message = "missing NOVELRAG_LLM_BASE_URL, NOVELRAG_LLM_API_KEY, or NOVELRAG_LLM_MODEL"
        return call_result
    url = base_url.rstrip("/") + "/chat/completions"
    payload = llm_payload(
        model=model,
        prompt=prompt,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        include_response_format=True,
    )
    try:
        call_result.request_was_sent = True
        raw, status = post_openai_compatible(url, api_key, payload)
        call_result.http_status = status
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code == 400 and response_format_unsupported(body):
            fallback_payload = llm_payload(
                model=model,
                prompt=prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                include_response_format=False,
            )
            try:
                raw, status = post_openai_compatible(url, api_key, fallback_payload)
                call_result.http_status = status
            except Exception as fallback_exc:  # noqa: BLE001
                call_result.exception_type = type(fallback_exc).__name__
                call_result.exception_message = str(fallback_exc)
                call_result.api_error = call_result.exception_message
                return call_result
        else:
            call_result.http_status = int(exc.code)
            call_result.exception_type = "HTTPError"
            call_result.exception_message = f"LLM HTTP error {exc.code}: {body}"
            call_result.api_error = call_result.exception_message
            return call_result
    except Exception as exc:  # noqa: BLE001
        call_result.exception_type = type(exc).__name__
        call_result.exception_message = str(exc)
        call_result.api_error = call_result.exception_message
        return call_result
    content, path_used, finish_reason, usage = content_from_response(raw)
    call_result.raw_response = content
    call_result.response_field_path_used = path_used
    call_result.finish_reason = finish_reason
    call_result.usage = usage
    call_result.api_success = True
    return call_result


def is_json_object_text(text: str) -> bool:
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(parsed, dict)


def first_balanced_json_object(text: str) -> str | None:
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start : index + 1]
                    if is_json_object_text(candidate):
                        return candidate
                    break
        start = text.find("{", start + 1)
    return None


def extract_json_object(raw_text: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    stripped = raw_text.strip()
    if is_json_object_text(stripped):
        return stripped, warnings

    fence_match = re.fullmatch(r"```(?:json|JSON)?\s*(.*?)\s*```", stripped, flags=re.DOTALL)
    if fence_match:
        fenced_text = fence_match.group(1).strip()
        if is_json_object_text(fenced_text):
            return fenced_text, ["extracted JSON from markdown code block"]
        warnings.append("markdown code block did not contain a valid JSON object")

    balanced = first_balanced_json_object(stripped)
    if balanced is not None:
        return balanced, ["extracted balanced JSON object from wrapped text"]

    warnings.append("could not extract a valid JSON object from raw response")
    return raw_text, warnings


def parse_json_response(raw_response: str) -> tuple[dict[str, Any] | None, bool, str, list[str]]:
    extracted_json_text, extraction_warnings = extract_json_object(raw_response)
    try:
        parsed = json.loads(extracted_json_text)
    except json.JSONDecodeError:
        return None, True, extracted_json_text, extraction_warnings
    if not isinstance(parsed, dict):
        return None, True, extracted_json_text, extraction_warnings + ["extracted JSON was not an object"]
    return parsed, False, extracted_json_text, extraction_warnings


def invalid_response_row(
    pack: dict[str, Any],
    *,
    request_id: str,
    provider: str,
    model: str,
    call_result: LlmCallResult,
    prompt_hash: str,
    raw_response: str,
    extracted_json_text: str,
    json_extraction_warnings: list[str],
    refusal_reason: str,
) -> dict[str, Any]:
    response_text = "Invalid response. evidence_refs: none"
    row = {
        "request_id": request_id,
        "context_pack_id": str(pack.get("query_id", "")),
        "query_id": str(pack.get("query_id", "")),
        "provider": provider,
        "model": model,
        "request_was_attempted": call_result.request_was_attempted,
        "request_was_sent": call_result.request_was_sent,
        "skipped_by_guard": call_result.skipped_by_guard,
        "api_success": call_result.api_success,
        "api_error": call_result.api_error,
        "http_status": call_result.http_status,
        "exception_type": call_result.exception_type,
        "exception_message": call_result.exception_message,
        "response_field_path_used": call_result.response_field_path_used,
        "response_status": "invalid",
        "response_text": response_text,
        "claim_units": [],
        "used_fact_ids": [],
        "used_evidence_ids": [],
        "unused_fact_ids": [str(item.get("fact_id", "")) for item in pack.get("allowed_facts", [])],
        "unsupported_claims": [],
        "refusal_reason": refusal_reason,
        "raw_response": raw_response,
        "extracted_json_text": extracted_json_text,
        "json_extraction_warnings": json_extraction_warnings,
        "raw_response_was_wrapped": bool(json_extraction_warnings and extracted_json_text != raw_response),
        "finish_reason": call_result.finish_reason,
        "usage": call_result.usage or {},
        "prompt_hash": prompt_hash,
        "response_hash": sha256_text(raw_response),
        "token_budget_estimate": pack_builder.estimate_tokens(response_text),
    }
    return row


def normalize_response(
    pack: dict[str, Any],
    parsed: dict[str, Any],
    *,
    request_id: str,
    provider: str,
    model: str,
    call_result: LlmCallResult,
    raw_response: str,
    extracted_json_text: str,
    json_extraction_warnings: list[str],
    prompt_hash: str,
    result: SandboxResult,
) -> dict[str, Any]:
    allowed_fact_ids = {str(item.get("fact_id", "")) for item in pack.get("allowed_facts", [])}
    evidence_by_id = {str(item.get("evidence_ref_id", "")): item for item in pack.get("evidence_refs", [])}
    used_fact_ids = [str(item) for item in parsed.get("used_fact_ids", []) if str(item)]
    used_evidence_ids = [str(item) for item in parsed.get("used_evidence_ids", []) if str(item)]
    unsupported_claims = [str(item) for item in parsed.get("unsupported_claims", []) if str(item)]
    claim_units_in = parsed.get("claim_units", [])
    if not isinstance(claim_units_in, list):
        claim_units_in = []
    claim_units: list[dict[str, Any]] = []

    out_of_pack_fact_ids = [fact_id for fact_id in used_fact_ids if fact_id not in allowed_fact_ids]
    out_of_pack_evidence_ids = [evidence_id for evidence_id in used_evidence_ids if evidence_id not in evidence_by_id]
    result.out_of_pack_fact_ref_count += len(out_of_pack_fact_ids)
    result.out_of_pack_evidence_ref_count += len(out_of_pack_evidence_ids)
    result.unsupported_claim_count += len(unsupported_claims)

    filtered_used_fact_ids: list[str] = []
    for fact_id in used_fact_ids:
        if fact_id in allowed_fact_ids and fact_id not in filtered_used_fact_ids:
            filtered_used_fact_ids.append(fact_id)
    filtered_used_evidence_ids: list[str] = []
    for evidence_id in used_evidence_ids:
        if evidence_id in evidence_by_id and evidence_id not in filtered_used_evidence_ids:
            filtered_used_evidence_ids.append(evidence_id)

    for index, claim in enumerate(claim_units_in, start=1):
        if not isinstance(claim, dict):
            continue
        claim_fact_ids = [str(item) for item in claim.get("used_fact_ids", []) if str(item) in allowed_fact_ids]
        claim_evidence_ids = [str(item) for item in claim.get("used_evidence_ids", []) if str(item) in evidence_by_id]
        claim_status = str(claim.get("claim_status", "supported"))
        if not claim_fact_ids or not claim_evidence_ids:
            claim_status = "unsupported"
        claim_units.append(
            {
                "claim_id": str(claim.get("claim_id", f"CLAIM-{index}")),
                "claim_text": str(claim.get("claim_text", "")),
                "used_fact_ids": claim_fact_ids,
                "used_evidence_ids": claim_evidence_ids,
                "claim_status": claim_status,
            }
        )

    response_status = str(parsed.get("response_status", "invalid"))
    if response_status not in VALID_RESPONSE_STATUSES:
        response_status = "invalid"
    if pack.get("context_pack_status") != "ready" and response_status == "ready":
        response_status = "partial"
    if unsupported_claims and response_status == "ready":
        response_status = "partial"
    if not pack.get("allowed_facts") and response_status not in {"insufficient", "invalid"}:
        response_status = "insufficient"
    if out_of_pack_fact_ids or out_of_pack_evidence_ids:
        response_status = "invalid"
    if not claim_units and pack.get("allowed_facts") and response_status == "ready":
        response_status = "partial"

    unused_fact_ids = [
        str(item.get("fact_id", ""))
        for item in pack.get("allowed_facts", [])
        if str(item.get("fact_id", "")) not in filtered_used_fact_ids
    ]

    row = {
        "request_id": request_id,
        "context_pack_id": str(pack.get("query_id", "")),
        "query_id": str(pack.get("query_id", "")),
        "provider": provider,
        "model": model,
        "request_was_attempted": call_result.request_was_attempted,
        "request_was_sent": call_result.request_was_sent,
        "skipped_by_guard": call_result.skipped_by_guard,
        "api_success": call_result.api_success,
        "api_error": call_result.api_error,
        "http_status": call_result.http_status,
        "exception_type": call_result.exception_type,
        "exception_message": call_result.exception_message,
        "response_field_path_used": call_result.response_field_path_used,
        "response_status": response_status,
        "response_text": str(parsed.get("response_text", "")),
        "claim_units": claim_units,
        "used_fact_ids": filtered_used_fact_ids,
        "used_evidence_ids": filtered_used_evidence_ids,
        "unused_fact_ids": unused_fact_ids,
        "unsupported_claims": unsupported_claims,
        "refusal_reason": str(parsed.get("refusal_reason", "")),
        "raw_response": raw_response,
        "extracted_json_text": extracted_json_text,
        "json_extraction_warnings": json_extraction_warnings,
        "raw_response_was_wrapped": bool(json_extraction_warnings and extracted_json_text != raw_response),
        "finish_reason": call_result.finish_reason,
        "usage": call_result.usage or {},
        "prompt_hash": prompt_hash,
        "response_hash": sha256_text(raw_response),
        "token_budget_estimate": pack_builder.estimate_tokens(str(parsed.get("response_text", ""))),
    }
    return row


def render_markdown(responses: list[dict[str, Any]]) -> str:
    lines = ["# L3.11 Real LLM Consumer Sandbox", ""]
    for response in responses:
        lines.extend(
            [
                f"## Query ID: {response.get('query_id', '')}",
                "",
                f"Status: {response.get('response_status', '')}  ",
                f"Used fact ids: {', '.join(response.get('used_fact_ids', [])) or 'none'}  ",
                f"Used evidence ids: {', '.join(response.get('used_evidence_ids', [])) or 'none'}",
                "",
                "```text",
                str(response.get("response_text", "")),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def compute_counts(result: SandboxResult) -> None:
    result.response_count = len(result.responses)
    result.ready_response_count = sum(1 for item in result.responses if item["response_status"] == "ready")
    result.partial_response_count = sum(1 for item in result.responses if item["response_status"] == "partial")
    result.insufficient_response_count = sum(1 for item in result.responses if item["response_status"] == "insufficient")
    result.invalid_response_count = sum(1 for item in result.responses if item["response_status"] == "invalid")
    result.valid_response_count = result.response_count - result.invalid_response_count
    result.empty_raw_response_count = sum(1 for item in result.responses if not str(item.get("raw_response", "")).strip())
    result.request_attempt_count = sum(1 for item in result.responses if item.get("request_was_attempted"))
    result.request_sent_count = sum(1 for item in result.responses if item.get("request_was_sent"))
    result.api_success_count = sum(1 for item in result.responses if item.get("api_success"))
    result.api_error_count = sum(1 for item in result.responses if item.get("api_error"))
    result.exception_count = sum(1 for item in result.responses if item.get("exception_type"))
    result.skipped_by_guard_count = sum(1 for item in result.responses if item.get("skipped_by_guard"))
    result.usage_total_input_tokens = 0
    result.usage_total_output_tokens = 0
    result.usage_total_tokens = 0
    for item in result.responses:
        usage = item.get("usage") if isinstance(item.get("usage"), dict) else {}
        result.usage_total_input_tokens += int(usage.get("prompt_tokens", usage.get("input_tokens", 0)) or 0)
        result.usage_total_output_tokens += int(usage.get("completion_tokens", usage.get("output_tokens", 0)) or 0)
        result.usage_total_tokens += int(usage.get("total_tokens", 0) or 0)
    result.warning_count = len(result.warnings)


def apply_result_status(result: SandboxResult) -> None:
    if result.mock_llm:
        if result.response_count > 0 and result.invalid_response_count == result.response_count:
            result.status = "FAIL"
            result.reason = "all_responses_invalid"
        elif result.response_count > 0 and result.empty_raw_response_count == result.response_count:
            result.status = "FAIL"
            result.reason = "empty_raw_response"
        elif result.valid_response_count > 0 and result.json_parse_error_count == 0 and not result.errors:
            result.status = "PASS"
            result.reason = ""
        else:
            result.status = "FAIL"
            result.reason = result.reason or "mock_validation_failed"
    else:
        if result.request_attempt_count == 0:
            result.status = "BLOCKED"
            result.reason = result.reason or "request_not_attempted"
        elif result.request_sent_count == 0:
            result.status = "BLOCKED"
            result.reason = result.reason or "request_not_sent"
        elif result.api_success_count == 0:
            result.status = "FAIL"
            result.reason = result.reason or "api_error"
        elif result.response_count > 0 and result.invalid_response_count == result.response_count:
            result.status = "FAIL"
            result.reason = "all_responses_invalid"
        elif result.response_count > 0 and result.empty_raw_response_count == result.response_count:
            result.status = "FAIL"
            result.reason = "empty_raw_response"
        elif (
            result.request_sent_count > 0
            and result.api_success_count > 0
            and result.valid_response_count > 0
            and result.empty_raw_response_count == 0
            and result.json_parse_error_count == 0
            and result.invalid_response_count == 0
            and not result.errors
        ):
            result.status = "PASS"
            result.reason = ""
        else:
            result.status = "FAIL"
            result.reason = result.reason or "validation_failed"
    result.ok = result.status == "PASS"


def build_manifest(
    result: SandboxResult,
    *,
    created_at: str,
    source_table_names: list[str],
    source_row_counts_before: dict[str, int],
    source_row_counts_after: dict[str, int],
) -> dict[str, Any]:
    return {
        "layer": "L3.11",
        "input_prefix": result.input_prefix,
        "output_prefix": result.output_prefix,
        "context_pack_count": result.context_pack_count,
        "status": result.status,
        "reason": result.reason,
        "request_attempt_count": result.request_attempt_count,
        "request_sent_count": result.request_sent_count,
        "api_success_count": result.api_success_count,
        "api_error_count": result.api_error_count,
        "exception_count": result.exception_count,
        "skipped_by_guard_count": result.skipped_by_guard_count,
        "llm_call_count": result.llm_call_count,
        "response_count": result.response_count,
        "valid_response_count": result.valid_response_count,
        "ready_response_count": result.ready_response_count,
        "partial_response_count": result.partial_response_count,
        "insufficient_response_count": result.insufficient_response_count,
        "invalid_response_count": result.invalid_response_count,
        "empty_raw_response_count": result.empty_raw_response_count,
        "unsupported_claim_count": result.unsupported_claim_count,
        "out_of_pack_fact_ref_count": result.out_of_pack_fact_ref_count,
        "out_of_pack_evidence_ref_count": result.out_of_pack_evidence_ref_count,
        "json_parse_error_count": result.json_parse_error_count,
        "json_extraction_warning_count": result.json_extraction_warning_count,
        "raw_response_wrapped_count": result.raw_response_wrapped_count,
        "source_table_mutation_count": result.source_table_mutation_count,
        "forbidden_final_table_count": result.forbidden_final_table_count,
        "chroma_accessed": result.chroma_accessed,
        "embedding_called": result.embedding_called,
        "sqlite_written": result.sqlite_written,
        "usage_total_input_tokens": result.usage_total_input_tokens,
        "usage_total_output_tokens": result.usage_total_output_tokens,
        "usage_total_tokens": result.usage_total_tokens,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "created_at": created_at,
        "source_table_names": source_table_names,
        "source_row_counts_before": source_row_counts_before,
        "source_row_counts_after": source_row_counts_after,
        "mock_llm": result.mock_llm,
        "provider": result.provider,
        "temperature": result.temperature,
        "max_output_tokens": result.max_output_tokens,
    }


def run_sandbox(
    project_dir: Path | str,
    *,
    input_prefix: str = DEFAULT_INPUT_PREFIX,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    sample_chapters: str = child_builder.DEFAULT_SAMPLE_CHAPTERS,
    provider: str = DEFAULT_PROVIDER,
    temperature: float = 0.0,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    mock_llm: bool = False,
    real_llm: bool = False,
    read_only: bool = True,
    mock_invalid_query_ids: set[str] | None = None,
) -> SandboxResult:
    root = Path(project_dir).resolve()
    load_project_env(root)
    child_builder.ensure_dirs(root)
    scope = child_builder.parse_scope(sample_chapters, None)
    db_path = root / child_builder.DB_RELATIVE_PATH
    result = SandboxResult(False, root, db_path, input_prefix, output_prefix, scope, mock_llm, provider, temperature, max_output_tokens)
    created_at = now_iso()
    mock_invalid_query_ids = mock_invalid_query_ids or set()
    try:
        if not read_only:
            raise RuntimeError("L3.11 sandbox must run with --read-only")
        if provider != DEFAULT_PROVIDER:
            raise RuntimeError(f"unsupported provider: {provider}")
        if not mock_llm:
            if not real_llm:
                result.reason = "real_llm_flag_required"
                raise RuntimeError("real LLM mode requires --real-llm")
            if os.environ.get("NOVELRAG_ALLOW_REAL_LLM") != "1":
                result.reason = "real_llm_not_allowed"
                raise RuntimeError("real LLM mode requires NOVELRAG_ALLOW_REAL_LLM=1")
            if not os.environ.get("NOVELRAG_LLM_API_KEY", "").strip():
                result.reason = "missing_api_key"
                raise RuntimeError("missing NOVELRAG_LLM_API_KEY")

        pack_payload = load_json(root / input_paths(input_prefix)["json"])
        _pack_manifest = load_json(root / input_paths(input_prefix)["manifest"])
        context_packs = list(pack_payload.get("context_packs", []))
        result.context_pack_count = len(context_packs)

        conn = connect_sqlite_readonly(db_path)
        try:
            source_table_names = child_builder.discover_source_table_names(conn)
            source_counts_before = row_counts(conn, source_table_names)
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
            if result.forbidden_final_table_count:
                result.errors.append("forbidden final tables exist before L3.11 sandbox")
            model = os.environ.get("NOVELRAG_LLM_MODEL", "").strip()
            for request_index, pack in enumerate(context_packs, start=1):
                prompt = build_prompt(pack, max_output_tokens=max_output_tokens)
                prompt_hash = sha256_text(prompt)
                query_id = str(pack.get("query_id", ""))
                if mock_llm:
                    raw_response = mock_llm_raw_response(pack, force_invalid=query_id in mock_invalid_query_ids)
                    call_result = LlmCallResult(
                        request_was_attempted=True,
                        request_was_sent=True,
                        api_success=True,
                        response_field_path_used="mock.raw_response",
                        raw_response=raw_response,
                    )
                else:
                    call_result = call_openai_compatible(
                        prompt=prompt,
                        temperature=temperature,
                        max_output_tokens=max_output_tokens,
                    )
                    raw_response = call_result.raw_response
                result.llm_call_count += 1
                parsed, parse_failed, extracted_json_text, extraction_warnings = parse_json_response(raw_response)
                result.json_extraction_warning_count += len(extraction_warnings)
                if extraction_warnings and extracted_json_text != raw_response:
                    result.raw_response_wrapped_count += 1
                if parse_failed or parsed is None:
                    result.json_parse_error_count += 1
                    response = invalid_response_row(
                        pack,
                        request_id=f"REQ-{request_index:04d}",
                        provider=provider,
                        model=model,
                        call_result=call_result,
                        prompt_hash=prompt_hash,
                        raw_response=raw_response,
                        extracted_json_text=extracted_json_text,
                        json_extraction_warnings=extraction_warnings,
                        refusal_reason="json_parse_failed",
                    )
                else:
                    response = normalize_response(
                        pack,
                        parsed,
                        request_id=f"REQ-{request_index:04d}",
                        provider=provider,
                        model=model,
                        call_result=call_result,
                        raw_response=raw_response,
                        extracted_json_text=extracted_json_text,
                        json_extraction_warnings=extraction_warnings,
                        prompt_hash=prompt_hash,
                        result=result,
                    )
                result.responses.append(response)
                result.prompt_audit_rows.append(
                    {
                        "context_pack_id": query_id,
                        "query_id": query_id,
                        "prompt_hash": prompt_hash,
                        "response_hash": response["response_hash"],
                        "mock_llm": mock_llm,
                        "provider": provider,
                    }
                )
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
        if result.source_table_mutation_count:
            result.errors.append("source table row count mutation detected")
        if result.forbidden_final_table_count:
            result.errors.append("forbidden final tables exist after L3.11 sandbox")
        result.error_count = len(result.errors)
        apply_result_status(result)

        write_json(
            root / output_paths(output_prefix)["json"],
            {
                "layer": "L3.11",
                "input_prefix": input_prefix,
                "output_prefix": output_prefix,
                "context_pack_count": result.context_pack_count,
                "responses": result.responses,
            },
        )
        write_text(root / output_paths(output_prefix)["md"], render_markdown(result.responses))
        write_json(
            root / output_paths(output_prefix)["manifest"],
            build_manifest(
                result,
                created_at=created_at,
                source_table_names=source_table_names,
                source_row_counts_before=source_counts_before,
                source_row_counts_after=source_counts_after,
            ),
        )
        write_json(
            root / output_paths(output_prefix)["prompt_audit"],
            {
                "layer": "L3.11",
                "input_prefix": input_prefix,
                "output_prefix": output_prefix,
                "prompt_audit_rows": result.prompt_audit_rows,
            },
        )
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        if result.reason in {"real_llm_flag_required", "real_llm_not_allowed", "missing_api_key"}:
            result.status = "BLOCKED"
        result.error_count = len(result.errors)
        result.warning_count = len(result.warnings)
        if result.status != "BLOCKED":
            result.status = "FAIL"
        result.ok = False
        write_json(
            root / output_paths(output_prefix)["manifest"],
            {
                "layer": "L3.11",
                "input_prefix": input_prefix,
                "output_prefix": output_prefix,
                "status": result.status,
                "reason": result.reason,
                "request_attempt_count": result.request_attempt_count,
                "request_sent_count": result.request_sent_count,
                "api_success_count": result.api_success_count,
                "api_error_count": result.api_error_count,
                "exception_count": result.exception_count,
                "skipped_by_guard_count": result.skipped_by_guard_count,
                "response_count": result.response_count,
                "valid_response_count": result.valid_response_count,
                "invalid_response_count": result.invalid_response_count,
                "empty_raw_response_count": result.empty_raw_response_count,
                "json_parse_error_count": result.json_parse_error_count,
                "json_extraction_warning_count": result.json_extraction_warning_count,
                "raw_response_wrapped_count": result.raw_response_wrapped_count,
                "usage_total_input_tokens": result.usage_total_input_tokens,
                "usage_total_output_tokens": result.usage_total_output_tokens,
                "usage_total_tokens": result.usage_total_tokens,
                "error_count": result.error_count,
                "warning_count": result.warning_count,
                "errors": result.errors,
                "warnings": result.warnings,
                "mock_llm": result.mock_llm,
                "provider": result.provider,
                "temperature": result.temperature,
                "max_output_tokens": result.max_output_tokens,
                "created_at": created_at,
            },
        )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run L3.11 real LLM consumer sandbox from L3.9 context packs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--input-prefix", type=str, default=DEFAULT_INPUT_PREFIX)
    parser.add_argument("--output-prefix", type=str, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--sample-chapters", type=str, default=child_builder.DEFAULT_SAMPLE_CHAPTERS)
    parser.add_argument("--provider", type=str, default=DEFAULT_PROVIDER)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--mock-llm", action="store_true")
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--read-only", action="store_true", default=True)
    args = parser.parse_args()
    result = run_sandbox(
        args.project_dir,
        input_prefix=args.input_prefix,
        output_prefix=args.output_prefix,
        sample_chapters=args.sample_chapters,
        provider=args.provider,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        mock_llm=args.mock_llm,
        real_llm=args.real_llm,
        read_only=args.read_only,
    )
    if args.mock_llm:
        print("L3.11 real llm consumer sandbox MOCK PASS" if result.ok else f"L3.11 real llm consumer sandbox MOCK {result.status}")
    else:
        print("L3.11 real llm consumer sandbox PASS" if result.ok else f"L3.11 real llm consumer sandbox {result.status}")
    if result.reason:
        print(f"reason={result.reason}")
    if result.ok:
        print(f"response_count={result.response_count}")
    else:
        for error in result.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
