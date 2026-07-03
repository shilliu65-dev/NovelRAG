import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def build_fake_regression_project(
    *,
    sandbox_manifest_overrides: dict | None = None,
    verifier_overrides: dict | None = None,
    sandbox_payload_overrides: dict | None = None,
) -> Path:
    root = Path(tempfile.mkdtemp(prefix="novelrag_l314_"))
    outputs = root / "outputs"
    sandbox_payload = {
        "layer": "L3.11",
        "input_prefix": "l3_prompt_context_pack_sample",
        "output_prefix": "l3_real_llm_consumer_sandbox_sample",
        "context_pack_count": 1,
        "responses": [
            {
                "context_pack_id": "q001",
                "query_id": "q001",
                "response_status": "invalid",
                "response_text": "Invalid response. evidence_refs: none",
                "claim_units": [],
                "used_fact_ids": [],
                "used_evidence_ids": [],
                "unsupported_claims": [],
                "raw_response": "{}",
            }
        ],
    }
    sandbox_payload.update(sandbox_payload_overrides or {})
    sandbox_manifest = {
        "layer": "L3.11",
        "input_prefix": "l3_prompt_context_pack_sample",
        "output_prefix": "l3_real_llm_consumer_sandbox_sample",
        "response_count": 1,
        "unsupported_claim_count": 0,
        "out_of_pack_fact_ref_count": 0,
        "out_of_pack_evidence_ref_count": 0,
        "json_parse_error_count": 0,
        "source_table_mutation_count": 0,
        "forbidden_final_table_count": 0,
        "chroma_accessed": False,
        "embedding_called": False,
        "sqlite_written": False,
        "error_count": 0,
        "warning_count": 0,
        "errors": [],
        "warnings": [],
        "mock_llm": False,
        "provider": "openai_compatible",
        "temperature": 1,
        "max_output_tokens": 500,
    }
    sandbox_manifest.update(sandbox_manifest_overrides or {})
    verifier_report = {
        "layer": "L3.12",
        "verified_layer": "L3.11",
        "context_pack_prefix": "l3_prompt_context_pack_sample",
        "llm_output_prefix": "l3_real_llm_consumer_sandbox_sample",
        "response_count": 1,
        "unsupported_claim_count": 0,
        "out_of_pack_fact_ref_count": 0,
        "out_of_pack_evidence_ref_count": 0,
        "source_table_mutation_count": 0,
        "forbidden_final_table_count": 0,
        "chroma_accessed": False,
        "embedding_called": False,
        "sqlite_written": False,
        "error_count": 0,
        "warning_count": 0,
        "status": "FULL PASS",
        "errors": [],
        "warnings": [],
    }
    verifier_report.update(verifier_overrides or {})
    write_json(outputs / "l3_real_llm_consumer_sandbox_sample.json", sandbox_payload)
    write_json(outputs / "l3_real_llm_consumer_sandbox_sample_manifest.json", sandbox_manifest)
    write_json(outputs / "l3_real_llm_consumer_sandbox_sample_verify_report.json", verifier_report)
    return root


class L3RealLlmRegressionSuiteTests(unittest.TestCase):
    def test_replay_reads_fake_sandbox_and_verifier_reports_and_passes(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project()
        result = suite.run_regression_suite(root, read_only=True)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.mode, "replay")
        self.assertTrue((root / "outputs" / "l3_real_llm_regression_suite_report.json").exists())
        self.assertTrue((root / "outputs" / "l3_real_llm_regression_suite_report.md").exists())
        self.assertTrue((root / "outputs" / "l3_real_llm_regression_suite_manifest.json").exists())

    def test_missing_sandbox_output_fails_clearly(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project()
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").unlink()
        result = suite.run_regression_suite(root, read_only=True)
        self.assertFalse(result.ok)
        self.assertIn("missing sandbox output", "\n".join(result.errors))

    def test_missing_verifier_report_fails_clearly(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project()
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample_verify_report.json").unlink()
        result = suite.run_regression_suite(root, read_only=True)
        self.assertFalse(result.ok)
        self.assertIn("missing verifier report", "\n".join(result.errors))

    def test_verifier_non_full_pass_fails(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project(verifier_overrides={"status": "FAIL", "errors": ["bad generation"]})
        result = suite.run_regression_suite(root, read_only=True)
        self.assertFalse(result.ok)
        self.assertIn("verifier_status must be FULL PASS", "\n".join(result.errors))

    def test_out_of_pack_fact_count_fails(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project(verifier_overrides={"out_of_pack_fact_ref_count": 1})
        result = suite.run_regression_suite(root, read_only=True)
        self.assertFalse(result.ok)
        self.assertGreater(result.out_of_pack_fact_ref_count, 0)

    def test_json_parse_error_count_fails(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project(sandbox_manifest_overrides={"json_parse_error_count": 1})
        result = suite.run_regression_suite(root, read_only=True)
        self.assertFalse(result.ok)
        self.assertGreater(result.json_parse_error_count, 0)

    def test_invalid_response_count_equal_response_count_fails(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project(
            sandbox_manifest_overrides={
                "response_count": 10,
                "invalid_response_count": 10,
                "valid_response_count": 0,
                "empty_raw_response_count": 0,
            }
        )
        result = suite.run_regression_suite(root, read_only=True)
        self.assertFalse(result.ok)
        self.assertIn("invalid_response_count must be 0", "\n".join(result.errors))

    def test_fixture_empty_and_invalid_responses_fail(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project()
        result = suite.run_regression_suite(root, read_only=True)
        cases = {case["name"]: case for case in result.case_results}
        self.assertEqual(cases["fixture_empty_response"]["status"], "FAIL")
        self.assertEqual(cases["fixture_invalid_json"]["status"], "FAIL")
        self.assertEqual(cases["fixture_valid_json"]["status"], "PASS")
        self.assertEqual(cases["fake_markdown_wrapped_json"]["status"], "PASS")
        self.assertTrue(cases["fake_markdown_wrapped_json"]["warnings"])

    def test_api_key_leak_detection_fails_without_echoing_secret(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        secret = "sk-test-secret-value"
        root = build_fake_regression_project(sandbox_payload_overrides={"debug": secret})
        with mock.patch.dict(os.environ, {"NOVELRAG_LLM_API_KEY": secret, "MOONSHOT_API_KEY": "moonshot-secret"}, clear=False):
            result = suite.run_regression_suite(root, read_only=True)
        self.assertFalse(result.ok)
        self.assertTrue(result.api_key_leak_detected)
        report_text = (root / "outputs" / "l3_real_llm_regression_suite_report.json").read_text(encoding="utf-8")
        self.assertNotIn(secret, report_text)

    def test_real_llm_without_allow_env_is_rejected_before_network_or_sandbox_call(self) -> None:
        from scripts import l3_real_llm_regression_suite as suite

        root = build_fake_regression_project()
        with mock.patch.dict(os.environ, {"NOVELRAG_ALLOW_REAL_LLM": "0"}, clear=False):
            with mock.patch("scripts.l3_real_llm_regression_suite.run_real_llm_pipeline") as pipeline:
                result = suite.run_regression_suite(root, real_llm=True, read_only=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(result.reason, "real_llm_not_allowed")
        pipeline.assert_not_called()

    def test_cli_replay_outputs_full_pass_line(self) -> None:
        root = build_fake_regression_project()
        proc = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "l3_real_llm_regression_suite.py"),
                "--project-dir",
                str(root),
                "--sandbox-output-prefix",
                "l3_real_llm_consumer_sandbox_sample",
                "--read-only",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip().splitlines()[0], "L3.14 real llm regression suite FULL PASS")


if __name__ == "__main__":
    unittest.main()
