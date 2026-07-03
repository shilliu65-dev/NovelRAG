from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

DEFAULT_BASE_URL = "https://api.moonshot.cn/v1"
DEFAULT_MODEL = "kimi-k2.6"
MAX_COMPLETION_TOKENS = 128


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value[:4] + "****"
    return value[:4] + "****" + value[-4:]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def build_payload(model: str) -> dict[str, Any]:
    return {
        "model": model,
        "temperature": 0,
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "messages": [
            {"role": "system", "content": "你只返回严格 JSON，不要 markdown，不要解释。"},
            {"role": "user", "content": '只返回 {"ok": true, "source": "kimi_debug"}。'},
        ],
    }


def send_request(base_url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_content(response: dict[str, Any]) -> tuple[str, str, str, dict[str, Any] | None]:
    choices = response.get("choices")
    finish_reason = ""
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
    if isinstance(choices, list) and choices:
        first = choices[0] if isinstance(choices[0], dict) else {}
        finish_reason = str(first.get("finish_reason", ""))
        message = first.get("message") if isinstance(first.get("message"), dict) else {}
        if message.get("content") is not None:
            return str(message.get("content", "")), "choices[0].message.content", finish_reason, usage
        if first.get("text") is not None:
            return str(first.get("text", "")), "choices[0].text", finish_reason, usage
    if response.get("output_text") is not None:
        return str(response.get("output_text", "")), "output_text", finish_reason, usage
    if response.get("content") is not None:
        return str(response.get("content", "")), "response.content", finish_reason, usage
    return "", "", finish_reason, usage


def run_debug(
    project_dir: Path | str,
    *,
    real_llm: bool,
    request_sender: Callable[[str, str, dict[str, Any]], dict[str, Any]] = send_request,
) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    load_project_env(root)
    outputs = root / "outputs"
    base_url = os.environ.get("NOVELRAG_LLM_BASE_URL", DEFAULT_BASE_URL).strip()
    model = os.environ.get("NOVELRAG_LLM_MODEL", DEFAULT_MODEL).strip()
    api_key = os.environ.get("NOVELRAG_LLM_API_KEY", "").strip()
    allow_real_llm = os.environ.get("NOVELRAG_ALLOW_REAL_LLM") == "1"
    payload = build_payload(model)
    request_will_be_sent = bool(real_llm and allow_real_llm and api_key)
    request_sanitized = {
        "created_at": now_iso(),
        "base_url": base_url,
        "model": model,
        "api_key_found": bool(api_key),
        "api_key_length": len(api_key),
        "api_key_masked": mask_secret(api_key),
        "temperature": 0,
        "max_completion_tokens": MAX_COMPLETION_TOKENS,
        "message_count": len(payload["messages"]),
        "request_will_be_sent": request_will_be_sent,
    }
    write_json(outputs / "debug_kimi_request_sanitized.json", request_sanitized)

    result: dict[str, Any] = {
        "status": "FAIL",
        "request_attempted": bool(real_llm),
        "request_sent": False,
        "api_success": False,
        "exception_type": "",
        "exception_message": "",
        "content_path_used": "",
        "raw_content": "",
        "parsed_json": None,
        "json_parse_ok": False,
        "finish_reason": "",
        "usage": None,
        "usage_missing": True,
        "empty_raw_content": False,
    }
    response_raw: dict[str, Any] = {}

    if not real_llm:
        result["status"] = "BLOCKED"
        result["exception_type"] = "real_llm_flag_required"
        result["exception_message"] = "run with --real-llm to send a real request"
    elif not allow_real_llm:
        result["status"] = "BLOCKED"
        result["exception_type"] = "real_llm_not_allowed"
        result["exception_message"] = "NOVELRAG_ALLOW_REAL_LLM must be 1"
    elif not api_key:
        result["status"] = "BLOCKED"
        result["exception_type"] = "missing_env_var"
        result["exception_message"] = "missing NOVELRAG_LLM_API_KEY"
    else:
        try:
            result["request_sent"] = True
            response_raw = request_sender(base_url, api_key, payload)
            result["api_success"] = True
            raw_content, path_used, finish_reason, usage = extract_content(response_raw)
            result["content_path_used"] = path_used
            result["raw_content"] = raw_content
            result["finish_reason"] = finish_reason
            result["usage"] = usage
            result["usage_missing"] = usage is None
            result["empty_raw_content"] = not raw_content.strip()
            if raw_content.strip():
                try:
                    parsed = json.loads(raw_content)
                    result["parsed_json"] = parsed
                    result["json_parse_ok"] = isinstance(parsed, dict)
                except json.JSONDecodeError as exc:
                    result["exception_type"] = type(exc).__name__
                    result["exception_message"] = str(exc)
            result["status"] = "PASS" if (
                result["api_success"]
                and raw_content.strip()
                and result["json_parse_ok"]
                and isinstance(result["parsed_json"], dict)
                and result["parsed_json"].get("ok") is True
            ) else "FAIL"
        except urllib.error.HTTPError as exc:
            result["exception_type"] = "HTTPError"
            result["exception_message"] = f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}"
        except Exception as exc:  # noqa: BLE001
            result["exception_type"] = type(exc).__name__
            result["exception_message"] = str(exc)

    write_json(outputs / "debug_kimi_response_raw.json", response_raw)
    write_json(outputs / "debug_kimi_result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Debug a minimal Kimi OpenAI-compatible chat completion.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--real-llm", action="store_true")
    args = parser.parse_args()
    result = run_debug(args.project_dir, real_llm=args.real_llm)
    print(f"Kimi debug {result['status']}")
    if result["exception_type"]:
        print(f"reason={result['exception_type']}")
    raise SystemExit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
