import json
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_l3_prompt_context_pack import build_prompt_context_project


def build_real_llm_sandbox_project(*, mock_invalid_query_ids: set[str] | None = None) -> tuple[Path, dict]:
    from scripts import l3_real_llm_consumer_sandbox as sandbox

    root, _payload = build_prompt_context_project()
    result = sandbox.run_sandbox(
        root,
        mock_llm=True,
        read_only=True,
        mock_invalid_query_ids=mock_invalid_query_ids,
    )
    assert result.ok, result.errors
    payload = json.loads((root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").read_text(encoding="utf-8"))
    return root, payload


class L3RealLlmConsumerSandboxTests(unittest.TestCase):
    def test_extract_json_object_accepts_plain_json_object(self) -> None:
        from scripts import l3_real_llm_consumer_sandbox as sandbox

        extracted, warnings = sandbox.extract_json_object('{"response_status": "insufficient"}')
        self.assertEqual(extracted, '{"response_status": "insufficient"}')
        self.assertEqual(warnings, [])

    def test_extract_json_object_accepts_markdown_json_code_block(self) -> None:
        from scripts import l3_real_llm_consumer_sandbox as sandbox

        raw = '```json\n{"response_status": "insufficient"}\n```'
        extracted, warnings = sandbox.extract_json_object(raw)
        self.assertEqual(json.loads(extracted)["response_status"], "insufficient")
        self.assertIn("extracted JSON from markdown code block", warnings)

    def test_extract_json_object_accepts_wrapped_balanced_object(self) -> None:
        from scripts import l3_real_llm_consumer_sandbox as sandbox

        raw = 'Here is the JSON:\n{"response_status": "insufficient", "refusal_reason": "no evidence"}\nDone.'
        extracted, warnings = sandbox.extract_json_object(raw)
        self.assertEqual(json.loads(extracted)["refusal_reason"], "no evidence")
        self.assertIn("extracted balanced JSON object from wrapped text", warnings)

    def test_extract_json_object_unparseable_returns_original_with_warning(self) -> None:
        from scripts import l3_real_llm_consumer_sandbox as sandbox

        raw = "not json at all"
        extracted, warnings = sandbox.extract_json_object(raw)
        self.assertEqual(extracted, raw)
        self.assertTrue(warnings)

    def test_parse_json_response_accepts_markdown_wrapped_json_without_parse_error(self) -> None:
        from scripts import l3_real_llm_consumer_sandbox as sandbox

        raw = '```json\n{"response_status": "insufficient", "claim_units": []}\n```'
        parsed, parse_failed, extracted, warnings = sandbox.parse_json_response(raw)
        self.assertFalse(parse_failed)
        self.assertEqual(parsed["response_status"], "insufficient")
        self.assertEqual(json.loads(extracted)["claim_units"], [])
        self.assertTrue(warnings)

    def test_parse_json_response_unparseable_reports_parse_error(self) -> None:
        from scripts import l3_real_llm_consumer_sandbox as sandbox

        parsed, parse_failed, extracted, warnings = sandbox.parse_json_response("not json")
        self.assertIsNone(parsed)
        self.assertTrue(parse_failed)
        self.assertEqual(extracted, "not json")
        self.assertTrue(warnings)

    def test_mock_mode_reads_context_pack_and_generates_one_response_per_pack(self) -> None:
        root, payload = build_real_llm_sandbox_project()
        pack_payload = json.loads((root / "outputs" / "l3_prompt_context_pack_sample.json").read_text(encoding="utf-8"))
        self.assertIn("responses", payload)
        self.assertEqual(len(payload["responses"]), len(pack_payload["context_packs"]))

    def test_each_response_contains_claim_units_and_in_pack_refs(self) -> None:
        root, payload = build_real_llm_sandbox_project()
        pack_payload = json.loads((root / "outputs" / "l3_prompt_context_pack_sample.json").read_text(encoding="utf-8"))
        pack_by_id = {pack["query_id"]: pack for pack in pack_payload["context_packs"]}
        for response in payload["responses"]:
            pack = pack_by_id[response["context_pack_id"]]
            allowed_fact_ids = {fact["fact_id"] for fact in pack["allowed_facts"]}
            evidence_ids = {evidence["evidence_ref_id"] for evidence in pack["evidence_refs"]}
            self.assertIn("claim_units", response)
            for claim in response["claim_units"]:
                self.assertIn("claim_id", claim)
                self.assertIn("claim_text", claim)
                self.assertIn("used_fact_ids", claim)
                self.assertIn("used_evidence_ids", claim)
                self.assertIn("claim_status", claim)
                for fact_id in claim["used_fact_ids"]:
                    self.assertIn(fact_id, allowed_fact_ids)
                for evidence_id in claim["used_evidence_ids"]:
                    self.assertIn(evidence_id, evidence_ids)

    def test_non_ready_pack_cannot_be_ready_and_empty_allowed_facts_is_not_ready(self) -> None:
        root, payload = build_real_llm_sandbox_project()
        pack_payload = json.loads((root / "outputs" / "l3_prompt_context_pack_sample.json").read_text(encoding="utf-8"))
        pack_by_id = {pack["query_id"]: pack for pack in pack_payload["context_packs"]}
        for response in payload["responses"]:
            pack = pack_by_id[response["context_pack_id"]]
            if pack["context_pack_status"] != "ready":
                self.assertNotEqual(response["response_status"], "ready")
            if not pack["allowed_facts"]:
                self.assertIn(response["response_status"], {"insufficient", "invalid"})

    def test_invalid_json_does_not_crash_and_is_recorded(self) -> None:
        from scripts import l3_real_llm_consumer_sandbox as sandbox

        root, _payload = build_prompt_context_project()
        result = sandbox.run_sandbox(
            root,
            mock_llm=True,
            read_only=True,
            mock_invalid_query_ids={"q001"},
        )
        self.assertTrue(result.ok, result.errors)
        manifest = json.loads((root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json").read_text(encoding="utf-8"))
        responses = json.loads((root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").read_text(encoding="utf-8"))["responses"]
        q001 = next(item for item in responses if item["query_id"] == "q001")
        self.assertEqual(q001["response_status"], "invalid")
        self.assertGreaterEqual(manifest["json_parse_error_count"], 1)

    def test_manifest_complete_and_forbidden_final_table_check(self) -> None:
        root, payload = build_real_llm_sandbox_project()
        del payload
        manifest = json.loads((root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json").read_text(encoding="utf-8"))
        for key in (
            "layer",
            "input_prefix",
            "output_prefix",
            "context_pack_count",
            "llm_call_count",
            "response_count",
            "ready_response_count",
            "partial_response_count",
            "insufficient_response_count",
            "invalid_response_count",
            "unsupported_claim_count",
            "out_of_pack_fact_ref_count",
            "out_of_pack_evidence_ref_count",
            "json_parse_error_count",
            "json_extraction_warning_count",
            "raw_response_wrapped_count",
            "source_table_mutation_count",
            "forbidden_final_table_count",
            "chroma_accessed",
            "embedding_called",
            "sqlite_written",
            "error_count",
            "warning_count",
        ):
            self.assertIn(key, manifest)
        conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
        try:
            forbidden = {
                row[0]
                for row in conn.execute(
                    """
                    SELECT name
                    FROM sqlite_master
                    WHERE type = 'table'
                      AND name IN (
                          'final_event',
                          'final_timeline',
                          'final_relationship_graph',
                          'final_state_machine'
                      )
                    """
                )
            }
        finally:
            conn.close()
        self.assertEqual(forbidden, set())

    def test_cli_mock_stdout_exact_and_outputs_exist(self) -> None:
        root, payload = build_real_llm_sandbox_project()
        del payload
        proc = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "l3_real_llm_consumer_sandbox.py"),
                "--project-dir",
                str(root),
                "--input-prefix",
                "l3_prompt_context_pack_sample",
                "--output-prefix",
                "l3_real_llm_consumer_sandbox_sample",
                "--sample-chapters",
                "1,2,1697",
                "--mock-llm",
                "--read-only",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip().splitlines()[0], "L3.11 real llm consumer sandbox MOCK PASS")
        self.assertTrue((root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").exists())
        self.assertTrue((root / "outputs" / "l3_real_llm_consumer_sandbox_sample.md").exists())
        self.assertTrue((root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json").exists())
        self.assertTrue((root / "outputs" / "l3_real_llm_consumer_sandbox_sample_prompt_audit.json").exists())


if __name__ == "__main__":
    unittest.main()
