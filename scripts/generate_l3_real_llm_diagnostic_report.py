from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_real_llm_consumer_sandbox as sandbox


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[:4] + "****"
    return value[:4] + "****" + value[-4:]


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def env_snapshot() -> dict[str, Any]:
    api_key = os.environ.get("NOVELRAG_LLM_API_KEY", "").strip()
    return {
        "NOVELRAG_LLM_BASE_URL": os.environ.get("NOVELRAG_LLM_BASE_URL", "").strip() or "missing",
        "NOVELRAG_LLM_MODEL": os.environ.get("NOVELRAG_LLM_MODEL", "").strip() or "missing",
        "NOVELRAG_LLM_API_KEY": {
            "status": "found" if api_key else "missing",
            "length": len(api_key),
            "masked": mask_secret(api_key),
        },
        "NOVELRAG_ALLOW_REAL_LLM": os.environ.get("NOVELRAG_ALLOW_REAL_LLM", "").strip() or "missing",
    }


def choose_final_status(env: dict[str, Any], kimi: dict[str, Any], l313: dict[str, Any], l314: dict[str, Any]) -> str:
    if env["NOVELRAG_LLM_API_KEY"]["status"] == "missing":
        return "API_KEY_MISSING"
    if kimi.get("status") == "BLOCKED" or l313.get("status") == "BLOCKED" or l314.get("status") == "BLOCKED":
        return "REAL_LLM_NOT_CALLED"
    if kimi.get("status") == "FAIL" and kimi.get("request_sent"):
        return "API_ERROR" if not kimi.get("api_success") else "REAL_LLM_CONNECTED_JSON_FAILED"
    if l314.get("status") == "FAIL":
        return "REGRESSION_FAILED"
    if kimi.get("status") == "PASS" and l313.get("status") == "PASS" and l314.get("status") == "PASS":
        return "FULL_PASS"
    return "ENGINEERING_PASS_REAL_LLM_BLOCKED"


def render_report(payload: dict[str, Any]) -> str:
    env = payload["environment"]
    kimi = payload["kimi_debug"]
    l313 = payload["l3_13b"]
    l314 = payload["l3_14"]
    blockers = payload["blockers"] or ["none"]
    return "\n".join(
        [
            "# L3 Real LLM Diagnostic Report",
            "",
            f"- created_at: {payload['created_at']}",
            f"- final_status: {payload['final_status']}",
            "",
            "## Environment",
            "",
            f"- NOVELRAG_LLM_BASE_URL: {env['NOVELRAG_LLM_BASE_URL']}",
            f"- NOVELRAG_LLM_MODEL: {env['NOVELRAG_LLM_MODEL']}",
            f"- NOVELRAG_ALLOW_REAL_LLM: {env['NOVELRAG_ALLOW_REAL_LLM']}",
            f"- NOVELRAG_LLM_API_KEY: {env['NOVELRAG_LLM_API_KEY']['status']} length={env['NOVELRAG_LLM_API_KEY']['length']} masked={env['NOVELRAG_LLM_API_KEY']['masked']}",
            "",
            "## Kimi Debug",
            "",
            f"- status: {kimi.get('status', 'missing')}",
            f"- request_sent: {kimi.get('request_sent', False)}",
            f"- api_success: {kimi.get('api_success', False)}",
            f"- json_parse_ok: {kimi.get('json_parse_ok', False)}",
            f"- usage_present: {bool(kimi.get('usage'))}",
            f"- exception_type: {kimi.get('exception_type', '') or 'none'}",
            "",
            "## L3.13b",
            "",
            f"- status: {l313.get('status', 'missing')}",
            f"- request_sent_count: {l313.get('request_sent_count', 0)}",
            f"- api_success_count: {l313.get('api_success_count', 0)}",
            f"- valid_response_count: {l313.get('valid_response_count', 0)}",
            f"- invalid_response_count: {l313.get('invalid_response_count', 0)}",
            f"- empty_raw_response_count: {l313.get('empty_raw_response_count', 0)}",
            f"- json_parse_error_count: {l313.get('json_parse_error_count', 0)}",
            "",
            "## L3.14",
            "",
            f"- status: {l314.get('status', 'missing')}",
            f"- mode: {l314.get('mode', 'missing')}",
            f"- request_sent_count: {l314.get('request_sent_count', 0)}",
            f"- api_success_count: {l314.get('api_success_count', 0)}",
            f"- valid_response_count: {l314.get('valid_response_count', 0)}",
            f"- invalid_response_count: {l314.get('invalid_response_count', 0)}",
            "",
            "## RAG Real LLM Loop",
            "",
            f"- can_enter_real_llm_generation_loop: {str(payload['can_enter_real_llm_generation_loop']).lower()}",
            "",
            "## Blockers",
            "",
            *(f"- {item}" for item in blockers),
            "",
            "## Next Steps",
            "",
            *(f"- {item}" for item in payload["next_steps"]),
            "",
        ]
    )


def build_report(project_dir: Path | str) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    sandbox.load_project_env(root)
    outputs = root / "outputs"
    env = env_snapshot()
    kimi = read_json(outputs / "debug_kimi_result.json")
    l313 = read_json(outputs / "l3_real_llm_consumer_sandbox_sample_manifest.json")
    l314 = read_json(outputs / "l3_real_llm_regression_suite_manifest.json")
    blockers: list[str] = []
    if env["NOVELRAG_LLM_API_KEY"]["status"] == "missing":
        blockers.append("missing_api_key")
    if env["NOVELRAG_ALLOW_REAL_LLM"] != "1":
        blockers.append("real_llm_not_allowed")
    if not kimi.get("request_sent", False):
        blockers.append("kimi_debug_request_not_sent")
    can_enter_loop = (
        kimi.get("status") == "PASS"
        and l313.get("status") == "PASS"
        and l313.get("request_sent_count", 0) > 0
        and l314.get("status") == "PASS"
    )
    payload = {
        "created_at": now_iso(),
        "environment": env,
        "kimi_debug": kimi,
        "l3_13b": l313,
        "l3_14": l314,
        "can_enter_real_llm_generation_loop": can_enter_loop,
        "blockers": blockers,
        "next_steps": [
            "Set NOVELRAG_LLM_BASE_URL, NOVELRAG_LLM_MODEL, NOVELRAG_LLM_API_KEY, and NOVELRAG_ALLOW_REAL_LLM=1 in the shell or .env.",
            "Run scripts\\debug_kimi_openai_compatible.py --project-dir D:\\NovelRAG --real-llm.",
            "Run scripts\\l3_real_llm_consumer_sandbox.py --project-dir D:\\NovelRAG --real-llm --output-prefix l3_real_llm_consumer_sandbox_sample.",
            "Run scripts\\l3_real_llm_regression_suite.py --project-dir D:\\NovelRAG --real-llm.",
        ],
    }
    payload["final_status"] = choose_final_status(env, kimi, l313, l314)
    write_json(outputs / "l3_real_llm_diagnostic_manifest.json", payload)
    write_text(outputs / "l3_real_llm_diagnostic_report.md", render_report(payload))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate L3 real LLM diagnostic report.")
    parser.add_argument("--project-dir", type=Path, required=True)
    args = parser.parse_args()
    payload = build_report(args.project_dir)
    print(f"L3 real LLM diagnostic final_status={payload['final_status']}")


if __name__ == "__main__":
    main()
