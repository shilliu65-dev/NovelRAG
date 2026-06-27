import json
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_l3_prompt_context_pack import build_prompt_context_project


def build_prompt_consumer_project() -> tuple[Path, dict]:
    from scripts import l3_prompt_consumer_dryrun as dryrun

    root, _payload = build_prompt_context_project()
    result = dryrun.run_dryrun(root, read_only=True)
    assert result.ok, result.errors
    payload = json.loads((root / "outputs" / "l3_prompt_consumer_dryrun_sample.json").read_text(encoding="utf-8"))
    return root, payload


class L3PromptConsumerDryrunTests(unittest.TestCase):
    def test_reads_l39_context_pack_and_generates_one_response_per_pack(self) -> None:
        root, payload = build_prompt_consumer_project()
        pack_payload = json.loads((root / "outputs" / "l3_prompt_context_pack_sample.json").read_text(encoding="utf-8"))
        self.assertIn("responses", payload)
        self.assertEqual(len(payload["responses"]), len(pack_payload["context_packs"]))

    def test_used_fact_ids_and_used_evidence_ids_resolve_inside_pack(self) -> None:
        root, payload = build_prompt_consumer_project()
        pack_payload = json.loads((root / "outputs" / "l3_prompt_context_pack_sample.json").read_text(encoding="utf-8"))
        pack_by_id = {pack["query_id"]: pack for pack in pack_payload["context_packs"]}
        for response in payload["responses"]:
            pack = pack_by_id[response["context_pack_id"]]
            allowed_fact_ids = {fact["fact_id"] for fact in pack["allowed_facts"]}
            evidence_ids = {evidence["evidence_ref_id"] for evidence in pack["evidence_refs"]}
            for fact_id in response["used_fact_ids"]:
                self.assertIn(fact_id, allowed_fact_ids)
            for evidence_id in response["used_evidence_ids"]:
                self.assertIn(evidence_id, evidence_ids)

    def test_non_ready_pack_is_not_ready_and_empty_allowed_facts_is_insufficient(self) -> None:
        root, payload = build_prompt_consumer_project()
        pack_payload = json.loads((root / "outputs" / "l3_prompt_context_pack_sample.json").read_text(encoding="utf-8"))
        pack_by_id = {pack["query_id"]: pack for pack in pack_payload["context_packs"]}
        for response in payload["responses"]:
            pack = pack_by_id[response["context_pack_id"]]
            if pack["context_pack_status"] != "ready":
                self.assertNotEqual(response["response_status"], "ready")
            if not pack["allowed_facts"]:
                self.assertEqual(response["response_status"], "insufficient")

    def test_response_text_has_evidence_citations_and_token_budget(self) -> None:
        root, payload = build_prompt_consumer_project()
        manifest = json.loads((root / "outputs" / "l3_prompt_consumer_dryrun_sample_manifest.json").read_text(encoding="utf-8"))
        for response in payload["responses"]:
            self.assertIn("used_fact_ids", response)
            self.assertIn("used_evidence_ids", response)
            self.assertIn("unused_fact_ids", response)
            self.assertIn("token_budget_estimate", response)
            self.assertLessEqual(response["token_budget_estimate"], manifest["token_budget"])
            if response["response_status"] != "insufficient":
                for line in response["response_text"].splitlines():
                    if line.strip().startswith("- "):
                        self.assertRegex(line.strip(), r"\[EVID-\d+(,EVID-\d+)*\]$")

    def test_manifest_complete_and_forbidden_final_table_check(self) -> None:
        root, payload = build_prompt_consumer_project()
        del payload
        manifest = json.loads((root / "outputs" / "l3_prompt_consumer_dryrun_sample_manifest.json").read_text(encoding="utf-8"))
        for key in (
            "layer",
            "input_prefix",
            "output_prefix",
            "query_count",
            "response_count",
            "ready_response_count",
            "partial_response_count",
            "insufficient_response_count",
            "total_used_fact_count",
            "total_used_evidence_count",
            "avg_used_facts_per_response",
            "token_budget",
            "over_token_budget_count",
            "source_table_mutation_count",
            "forbidden_final_table_count",
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

    def test_verifier_pass_stdout_exact_and_outputs_exist(self) -> None:
        root, payload = build_prompt_consumer_project()
        del payload
        proc = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "l3_verify_prompt_consumer_dryrun.py"),
                "--project-dir",
                str(root),
                "--input-prefix",
                "l3_prompt_context_pack_sample",
                "--expect-output-prefix",
                "l3_prompt_consumer_dryrun_sample",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "L3.10 prompt consumer dryrun FULL PASS")
        self.assertTrue((root / "outputs" / "l3_prompt_consumer_dryrun_sample_verify_report.json").exists())
        self.assertTrue((root / "outputs" / "l3_prompt_consumer_dryrun_sample_verify_report.md").exists())


if __name__ == "__main__":
    unittest.main()
