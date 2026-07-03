from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_real_llm_consumer_sandbox as sandbox
from scripts import l3_verify_generation_output as verifier

DEFAULT_SANDBOX_OUTPUT_PREFIX = "l3_real_llm_consumer_sandbox_sample"
DEFAULT_INPUT_PREFIX = "l3_prompt_context_pack_sample"
DEFAULT_PROVIDER = "openai_compatible"
DEFAULT_TEMPERATURE = 1.0
DEFAULT_MAX_OUTPUT_TOKENS = 500
DEFAULT_REPORT_OUTPUT = Path("outputs/l3_real_llm_regression_suite_report.json")
DEFAULT_MARKDOWN_OUTPUT = Path("outputs/l3_real_llm_regression_suite_report.md")
DEFAULT_MANIFEST_OUTPUT = Path("outputs/l3_real_llm_regression_suite_manifest.json")


@dataclass
class RegressionSuiteResult:
    ok: bool
    project_dir: Path
    status: str
    mode: str
    sandbox_output_prefix: str
    input_prefix: str
    provider: str
    model: str = ""
    base_url_host: str = ""
    temperature: float = DEFAULT_TEMPERATURE
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    sandbox_status: str = ""
    verifier_status: str = ""
    response_count: int = 0
    request_sent_count: int = 0
    api_success_count: int = 0
    valid_response_count: int = 0
    invalid_response_count: int = 0
    empty_raw_response_count: int = 0
    json_parse_error_count: int = 0
    unsupported_claim_count: int = 0
    out_of_pack_fact_ref_count: int = 0
    out_of_pack_evidence_ref_count: int = 0
    source_table_mutation_count: int = 0
    forbidden_final_table_count: int = 0
    api_key_leak_detected: bool = False
    read_only: bool = False
    sqlite_write: bool = False
    chroma_write: bool = False
    embedding_call: bool = False
    source_mutation: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    case_results: list[dict[str, Any]] = field(default_factory=list)
    checked_files: list[str] = field(default_factory=list)
    generated_files: list[str] = field(default_factory=list)
    reason: str = ""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def resolve_output_path(project_dir: Path, path: Path | str) -> Path:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate
    return project_dir / candidate


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_json_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sandbox_paths(prefix: str) -> dict[str, Path]:
    return {
        "json": Path("outputs") / f"{prefix}.json",
        "manifest": Path("outputs") / f"{prefix}_manifest.json",
        "verify_json": Path("outputs") / f"{prefix}_verify_report.json",
        "verify_md": Path("outputs") / f"{prefix}_verify_report.md",
    }


def safe_int(payload: dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(payload.get(key, default))
    except (TypeError, ValueError):
        return default


def host_from_base_url(base_url: str) -> str:
    if not base_url:
        return ""
    parsed = urlparse(base_url)
    return parsed.hostname or ""


def append_error(result: RegressionSuiteResult, message: str) -> None:
    if message not in result.errors:
        result.errors.append(message)


def evaluate_raw_response_case(name: str, raw_content: str) -> dict[str, Any]:
    parsed, parse_failed, extracted, warnings = sandbox.parse_json_response(raw_content)
    empty_raw = not raw_content.strip()
    ok_value = isinstance(parsed, dict) and parsed.get("ok") is True
    status = "PASS" if (not empty_raw and not parse_failed and ok_value) else "FAIL"
    return {
        "name": name,
        "status": status,
        "raw_content_empty": empty_raw,
        "json_parse_ok": not parse_failed,
        "parsed_json": parsed,
        "extracted_json_text": extracted,
        "warnings": warnings,
    }


def fixture_content(project_dir: Path, fixture_name: str) -> str:
    del project_dir
    fixture_path = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "llm_responses" / fixture_name
    payload = load_json_file(fixture_path)
    if "response" not in payload:
        return ""
    content, _path_used, _finish_reason, _usage = sandbox.content_from_response(payload["response"])
    return content


def run_fake_and_fixture_cases(project_dir: Path) -> list[dict[str, Any]]:
    return [
        evaluate_raw_response_case("fake_valid_json", '{"ok": true, "source": "fake"}'),
        evaluate_raw_response_case("fake_markdown_wrapped_json", '```json\n{"ok": true, "source": "fake"}\n```'),
        evaluate_raw_response_case("fake_empty_response", ""),
        evaluate_raw_response_case("fake_invalid_json", "not json"),
        evaluate_raw_response_case("fixture_valid_json", fixture_content(project_dir, "valid_json_response.json")),
        evaluate_raw_response_case("fixture_empty_response", fixture_content(project_dir, "empty_response.json")),
        evaluate_raw_response_case("fixture_invalid_json", fixture_content(project_dir, "invalid_json_response.json")),
    ]


def require_real_llm_gate(result: RegressionSuiteResult) -> bool:
    if os.environ.get("NOVELRAG_ALLOW_REAL_LLM") != "1":
        result.status = "BLOCKED"
        result.reason = "real_llm_not_allowed"
        append_error(result, "real LLM mode requires NOVELRAG_ALLOW_REAL_LLM=1")
        return False
    if not os.environ.get("NOVELRAG_LLM_API_KEY", "").strip():
        result.status = "BLOCKED"
        result.reason = "missing_api_key"
        append_error(result, "missing required real LLM environment variable: NOVELRAG_LLM_API_KEY")
        return False
    return True


def run_real_llm_pipeline(
    project_dir: Path,
    *,
    sandbox_output_prefix: str,
    input_prefix: str,
    sample_chapters: str,
    provider: str,
    temperature: float,
    max_output_tokens: int,
    read_only: bool,
) -> None:
    sandbox_result = sandbox.run_sandbox(
        project_dir,
        input_prefix=input_prefix,
        output_prefix=sandbox_output_prefix,
        sample_chapters=sample_chapters,
        provider=provider,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        mock_llm=False,
        real_llm=True,
        read_only=read_only,
    )
    if not sandbox_result.ok:
        raise RuntimeError("real LLM sandbox failed: " + "; ".join(sandbox_result.errors))
    verify_result = verifier.run_verification(
        project_dir,
        context_pack_prefix=input_prefix,
        llm_output_prefix=sandbox_output_prefix,
    )
    if not verify_result.ok:
        raise RuntimeError("L3.12 verifier failed: " + "; ".join(verify_result.errors))


def read_replay_inputs(result: RegressionSuiteResult) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    paths = sandbox_paths(result.sandbox_output_prefix)
    sandbox_json_path = result.project_dir / paths["json"]
    manifest_path = result.project_dir / paths["manifest"]
    verify_json_path = result.project_dir / paths["verify_json"]
    verify_md_path = result.project_dir / paths["verify_md"]

    for path in (sandbox_json_path, manifest_path):
        result.checked_files.append(str(path))
    result.checked_files.append(str(verify_json_path if verify_json_path.exists() else verify_md_path))

    if not sandbox_json_path.exists():
        raise RuntimeError(f"missing sandbox output: {sandbox_json_path}")
    if not manifest_path.exists():
        raise RuntimeError(f"missing sandbox manifest: {manifest_path}")
    if not verify_json_path.exists() and not verify_md_path.exists():
        raise RuntimeError(f"missing verifier report: {verify_json_path} or {verify_md_path}")

    sandbox_payload = load_json_file(sandbox_json_path)
    manifest = load_json_file(manifest_path)
    verify_payload = load_json_file(verify_json_path) if verify_json_path.exists() else {"status": "UNKNOWN", "warnings": [], "errors": []}
    return sandbox_payload, manifest, verify_payload


def summarize_inputs(
    result: RegressionSuiteResult,
    *,
    sandbox_payload: dict[str, Any],
    manifest: dict[str, Any],
    verify_payload: dict[str, Any],
) -> None:
    result.sandbox_status = "PASS" if safe_int(manifest, "error_count") == 0 else "FAIL"
    result.verifier_status = str(verify_payload.get("status", ""))
    result.response_count = safe_int(manifest, "response_count", len(sandbox_payload.get("responses", [])))
    result.request_sent_count = safe_int(manifest, "request_sent_count")
    result.api_success_count = safe_int(manifest, "api_success_count")
    result.valid_response_count = safe_int(manifest, "valid_response_count", result.response_count - safe_int(manifest, "invalid_response_count"))
    result.invalid_response_count = safe_int(manifest, "invalid_response_count")
    result.empty_raw_response_count = safe_int(manifest, "empty_raw_response_count")
    result.json_parse_error_count = safe_int(manifest, "json_parse_error_count")
    result.unsupported_claim_count = max(safe_int(manifest, "unsupported_claim_count"), safe_int(verify_payload, "unsupported_claim_count"))
    result.out_of_pack_fact_ref_count = max(safe_int(manifest, "out_of_pack_fact_ref_count"), safe_int(verify_payload, "out_of_pack_fact_ref_count"))
    result.out_of_pack_evidence_ref_count = max(safe_int(manifest, "out_of_pack_evidence_ref_count"), safe_int(verify_payload, "out_of_pack_evidence_ref_count"))
    result.source_table_mutation_count = max(safe_int(manifest, "source_table_mutation_count"), safe_int(verify_payload, "source_table_mutation_count"))
    result.forbidden_final_table_count = max(safe_int(manifest, "forbidden_final_table_count"), safe_int(verify_payload, "forbidden_final_table_count"))
    result.sqlite_write = bool(manifest.get("sqlite_written", False) or verify_payload.get("sqlite_written", False))
    result.chroma_write = bool(manifest.get("chroma_accessed", False) or verify_payload.get("chroma_accessed", False))
    result.embedding_call = bool(manifest.get("embedding_called", False) or verify_payload.get("embedding_called", False))
    result.source_mutation = result.source_table_mutation_count > 0
    result.model = os.environ.get("NOVELRAG_LLM_MODEL", "")
    result.base_url_host = host_from_base_url(os.environ.get("NOVELRAG_LLM_BASE_URL", ""))

    for warning in manifest.get("warnings", []) + verify_payload.get("warnings", []):
        result.warnings.append(str(warning))
    if result.response_count != 1:
        result.warnings.append("response_count does not exactly match default sample_chapters=1; verify configured scope")
    if result.temperature != 1:
        result.warnings.append("model temperature should be fixed at 1 for L3.14 regression")


def apply_pass_conditions(result: RegressionSuiteResult) -> None:
    if result.status == "BLOCKED":
        result.ok = False
        return
    checks = [
        (result.sandbox_status == "PASS", "sandbox_status must be PASS"),
        (result.verifier_status == "FULL PASS", "verifier_status must be FULL PASS"),
        (result.response_count > 0, "response_count must be > 0"),
        (result.valid_response_count > 0, "valid_response_count must be > 0"),
        (result.invalid_response_count == 0, "invalid_response_count must be 0"),
        (result.empty_raw_response_count == 0, "empty_raw_response_count must be 0"),
        (result.json_parse_error_count == 0, "json_parse_error_count must be 0"),
        (result.out_of_pack_fact_ref_count == 0, "out_of_pack_fact_ref_count must be 0"),
        (result.out_of_pack_evidence_ref_count == 0, "out_of_pack_evidence_ref_count must be 0"),
        (result.source_table_mutation_count == 0, "source_table_mutation_count must be 0"),
        (result.forbidden_final_table_count == 0, "forbidden_final_table_count must be 0"),
        (not result.api_key_leak_detected, "api key leak detected in generated or checked artifacts"),
        (result.read_only, "read_only must be true"),
        (not result.sqlite_write, "sqlite_write must be false"),
        (not result.chroma_write, "chroma_write must be false"),
        (not result.embedding_call, "embedding_call must be false"),
        (not result.source_mutation, "source_mutation must be false"),
    ]
    for ok, message in checks:
        if not ok:
            append_error(result, message)
    for case in result.case_results:
        if case["name"] in {"fake_empty_response", "fake_invalid_json", "fixture_empty_response", "fixture_invalid_json"}:
            if case["status"] != "FAIL":
                append_error(result, f"{case['name']} must FAIL")
        else:
            if case["status"] != "PASS":
                append_error(result, f"{case['name']} must PASS")
    result.status = "FAIL" if result.errors else "PASS"
    result.ok = not result.errors


def secret_needles() -> list[str]:
    needles = ["sk-"]
    for name in ("NOVELRAG_LLM_API_KEY", "MOONSHOT_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            needles.append(value)
    return needles


def scan_api_key_leaks(paths: list[str]) -> bool:
    needles = secret_needles()
    for path_text in paths:
        path = Path(path_text)
        if not path.exists() or not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if any(needle in text for needle in needles):
            return True
    return False


def report_payload(result: RegressionSuiteResult) -> dict[str, Any]:
    return {
        "layer": "L3.14",
        "suite_name": "real_llm_regression_suite",
        "status": result.status,
        "reason": result.reason,
        "mode": result.mode,
        "sandbox_output_prefix": result.sandbox_output_prefix,
        "input_prefix": result.input_prefix,
        "provider": result.provider,
        "model": result.model,
        "base_url_host": result.base_url_host,
        "temperature": result.temperature,
        "max_output_tokens": result.max_output_tokens,
        "sandbox_status": result.sandbox_status,
        "verifier_status": result.verifier_status,
        "response_count": result.response_count,
        "request_sent_count": result.request_sent_count,
        "api_success_count": result.api_success_count,
        "valid_response_count": result.valid_response_count,
        "invalid_response_count": result.invalid_response_count,
        "empty_raw_response_count": result.empty_raw_response_count,
        "json_parse_error_count": result.json_parse_error_count,
        "unsupported_claim_count": result.unsupported_claim_count,
        "out_of_pack_fact_ref_count": result.out_of_pack_fact_ref_count,
        "out_of_pack_evidence_ref_count": result.out_of_pack_evidence_ref_count,
        "source_table_mutation_count": result.source_table_mutation_count,
        "forbidden_final_table_count": result.forbidden_final_table_count,
        "api_key_leak_detected": result.api_key_leak_detected,
        "read_only": result.read_only,
        "sqlite_write": result.sqlite_write,
        "chroma_write": result.chroma_write,
        "embedding_call": result.embedding_call,
        "source_mutation": result.source_mutation,
        "errors": result.errors,
        "warnings": result.warnings,
        "case_results": result.case_results,
        "checked_files": result.checked_files,
        "generated_files": result.generated_files,
    }


def render_markdown(result: RegressionSuiteResult) -> str:
    errors = result.errors or ["none"]
    warnings = result.warnings or ["none"]
    return "\n".join(
        [
            "# L3.14 Real LLM Regression Suite Report",
            "",
            "## Status",
            "",
            f"- status: {result.status}",
            f"- reason: {result.reason or 'none'}",
            "",
            "## Mode",
            "",
            f"- mode: {result.mode}",
            f"- provider: {result.provider}",
            f"- model: {result.model or 'unset'}",
            f"- base_url_host: {result.base_url_host or 'unset'}",
            "",
            "## Input files",
            "",
            *(f"- {path}" for path in result.checked_files),
            "",
            "## Sandbox output summary",
            "",
            f"- sandbox_status: {result.sandbox_status}",
            f"- response_count: {result.response_count}",
            f"- request_sent_count: {result.request_sent_count}",
            f"- api_success_count: {result.api_success_count}",
            f"- valid_response_count: {result.valid_response_count}",
            f"- invalid_response_count: {result.invalid_response_count}",
            f"- empty_raw_response_count: {result.empty_raw_response_count}",
            f"- json_parse_error_count: {result.json_parse_error_count}",
            "",
            "## Verifier summary",
            "",
            f"- verifier_status: {result.verifier_status}",
            f"- unsupported_claim_count: {result.unsupported_claim_count}",
            f"- out_of_pack_fact_ref_count: {result.out_of_pack_fact_ref_count}",
            f"- out_of_pack_evidence_ref_count: {result.out_of_pack_evidence_ref_count}",
            "",
            "## Safety flags",
            "",
            f"- read_only: {str(result.read_only).lower()}",
            f"- sqlite_write: {str(result.sqlite_write).lower()}",
            f"- chroma_write: {str(result.chroma_write).lower()}",
            f"- embedding_call: {str(result.embedding_call).lower()}",
            f"- source_mutation: {str(result.source_mutation).lower()}",
            f"- api_key_leak_detected: {str(result.api_key_leak_detected).lower()}",
            "",
            "## Errors",
            "",
            *(f"- {error}" for error in errors),
            "",
            "## Warnings",
            "",
            *(f"- {warning}" for warning in warnings),
            "",
            "## Final verdict",
            "",
            "L3.14 real llm regression suite FULL PASS" if result.ok else "L3.14 real llm regression suite FAIL",
            "",
        ]
    )


def manifest_payload(result: RegressionSuiteResult) -> dict[str, Any]:
    payload = report_payload(result)
    payload["created_at"] = now_iso()
    payload["contract"] = "docs/l3_real_llm_regression_suite_contract.md"
    return payload


def write_outputs(
    result: RegressionSuiteResult,
    *,
    report_output: Path | str,
    markdown_output: Path | str,
    manifest_output: Path | str,
) -> None:
    report_path = resolve_output_path(result.project_dir, report_output)
    markdown_path = resolve_output_path(result.project_dir, markdown_output)
    manifest_path = resolve_output_path(result.project_dir, manifest_output)
    result.generated_files = [str(report_path), str(markdown_path), str(manifest_path)]
    write_json(report_path, report_payload(result))
    write_text(markdown_path, render_markdown(result))
    write_json(manifest_path, manifest_payload(result))


def run_regression_suite(
    project_dir: Path | str,
    *,
    sandbox_output_prefix: str = DEFAULT_SANDBOX_OUTPUT_PREFIX,
    input_prefix: str = DEFAULT_INPUT_PREFIX,
    sample_chapters: str = "1",
    provider: str = DEFAULT_PROVIDER,
    temperature: float = DEFAULT_TEMPERATURE,
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS,
    real_llm: bool = False,
    read_only: bool = True,
    report_output: Path | str = DEFAULT_REPORT_OUTPUT,
    markdown_output: Path | str = DEFAULT_MARKDOWN_OUTPUT,
    manifest_output: Path | str = DEFAULT_MANIFEST_OUTPUT,
) -> RegressionSuiteResult:
    root = Path(project_dir).resolve()
    sandbox.load_project_env(root)
    result = RegressionSuiteResult(
        ok=False,
        project_dir=root,
        status="FAIL",
        mode="real_llm" if real_llm else "replay",
        sandbox_output_prefix=sandbox_output_prefix,
        input_prefix=input_prefix,
        provider=provider,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        read_only=read_only,
    )
    try:
        result.case_results = run_fake_and_fixture_cases(root)
        if real_llm:
            if require_real_llm_gate(result):
                run_real_llm_pipeline(
                    root,
                    sandbox_output_prefix=sandbox_output_prefix,
                    input_prefix=input_prefix,
                    sample_chapters=sample_chapters,
                    provider=provider,
                    temperature=temperature,
                    max_output_tokens=max_output_tokens,
                    read_only=read_only,
                )
        sandbox_payload, manifest, verify_payload = read_replay_inputs(result)
        summarize_inputs(result, sandbox_payload=sandbox_payload, manifest=manifest, verify_payload=verify_payload)
    except Exception as exc:  # noqa: BLE001
        append_error(result, str(exc))

    scan_paths = list(result.checked_files)
    result.api_key_leak_detected = scan_api_key_leaks(scan_paths)
    apply_pass_conditions(result)
    write_outputs(result, report_output=report_output, markdown_output=markdown_output, manifest_output=manifest_output)
    result.api_key_leak_detected = scan_api_key_leaks(scan_paths + result.generated_files)
    apply_pass_conditions(result)
    write_outputs(result, report_output=report_output, markdown_output=markdown_output, manifest_output=manifest_output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run L3.14 real LLM regression suite.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--sandbox-output-prefix", type=str, default=DEFAULT_SANDBOX_OUTPUT_PREFIX)
    parser.add_argument("--input-prefix", type=str, default=DEFAULT_INPUT_PREFIX)
    parser.add_argument("--sample-chapters", type=str, default="1")
    parser.add_argument("--provider", type=str, default=DEFAULT_PROVIDER)
    parser.add_argument("--temperature", type=float, default=DEFAULT_TEMPERATURE)
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--real-llm", action="store_true")
    parser.add_argument("--read-only", action="store_true", default=True)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT_OUTPUT)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN_OUTPUT)
    parser.add_argument("--manifest-output", type=Path, default=DEFAULT_MANIFEST_OUTPUT)
    args = parser.parse_args()
    result = run_regression_suite(
        args.project_dir,
        sandbox_output_prefix=args.sandbox_output_prefix,
        input_prefix=args.input_prefix,
        sample_chapters=args.sample_chapters,
        provider=args.provider,
        temperature=args.temperature,
        max_output_tokens=args.max_output_tokens,
        real_llm=args.real_llm,
        read_only=args.read_only,
        report_output=args.report_output,
        markdown_output=args.markdown_output,
        manifest_output=args.manifest_output,
    )
    print("L3.14 real llm regression suite FULL PASS" if result.ok else f"L3.14 real llm regression suite {result.status}")
    if result.reason:
        print(f"reason={result.reason}")
    for error in result.errors:
        print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
