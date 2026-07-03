import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class DebugKimiOpenAICompatibleTests(unittest.TestCase):
    def test_missing_api_key_blocks_without_request(self) -> None:
        from scripts import debug_kimi_openai_compatible as debug_kimi

        root = Path(tempfile.mkdtemp(prefix="novelrag_kimi_debug_"))
        with mock.patch.dict(os.environ, {"NOVELRAG_LLM_API_KEY": "", "NOVELRAG_ALLOW_REAL_LLM": "1"}, clear=False):
            result = debug_kimi.run_debug(root, real_llm=True, request_sender=lambda *_args, **_kwargs: self.fail("request sent"))

        self.assertEqual(result["status"], "BLOCKED")
        self.assertFalse(result["request_sent"])
        self.assertEqual(result["exception_type"], "missing_env_var")

    def test_valid_response_passes_and_writes_sanitized_request(self) -> None:
        from scripts import debug_kimi_openai_compatible as debug_kimi

        root = Path(tempfile.mkdtemp(prefix="novelrag_kimi_debug_"))
        response = {
            "choices": [{"message": {"content": "{\"ok\": true, \"source\": \"kimi_debug\"}"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        env = {
            "NOVELRAG_LLM_BASE_URL": "https://api.moonshot.cn/v1",
            "NOVELRAG_LLM_MODEL": "kimi-k2.6",
            "NOVELRAG_LLM_API_KEY": "sk-test-secret-value",
            "NOVELRAG_ALLOW_REAL_LLM": "1",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            result = debug_kimi.run_debug(root, real_llm=True, request_sender=lambda *_args, **_kwargs: response)

        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["request_sent"])
        request_payload = json.loads((root / "outputs" / "debug_kimi_request_sanitized.json").read_text(encoding="utf-8"))
        self.assertEqual(request_payload["api_key_masked"], "sk-t****alue")
        self.assertNotIn("sk-test-secret-value", json.dumps(request_payload))
        self.assertEqual(request_payload["max_completion_tokens"], 128)


if __name__ == "__main__":
    unittest.main()
