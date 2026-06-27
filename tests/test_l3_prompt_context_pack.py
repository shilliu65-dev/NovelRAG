import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_l3_hybrid_rag_evidence_qa import prepare_project, write_retrieval_outputs


def build_prompt_context_project() -> tuple[Path, dict]:
    from scripts import l3_hybrid_rag_evidence_qa as qa
    from scripts import l3_prompt_context_pack_builder as builder
    from scripts import l3_prompt_context_pack_reporter as reporter

    root = prepare_project()
    write_retrieval_outputs(root, empty_query=True)
    qa_result = qa.run_evidence_qa(root, read_only=True)
    assert qa_result.ok, qa_result.errors
    build_result = builder.run_builder(root, read_only=True)
    assert build_result.ok, build_result.errors
    reporter.run_report(root)
    payload = json.loads((root / "outputs" / "l3_prompt_context_pack_sample.json").read_text(encoding="utf-8"))
    return root, payload


class L3PromptContextPackTests(unittest.TestCase):
    def test_reads_l38_answers_and_l39_context_pack_and_count_matches(self) -> None:
        root, payload = build_prompt_context_project()
        answers = json.loads((root / "outputs" / "l3_hybrid_rag_evidence_qa_sample_answers.json").read_text(encoding="utf-8"))
        self.assertIn("answers", answers)
        self.assertIn("context_packs", payload)
        self.assertEqual(len(payload["context_packs"]), len(answers["answers"]))

    def test_context_pack_status_enum_ready_and_insufficient_rules_hold(self) -> None:
        root, payload = build_prompt_context_project()
        del root
        packs = payload["context_packs"]
        statuses = {pack["context_pack_status"] for pack in packs}
        self.assertTrue(statuses.issubset({"ready", "partial", "insufficient"}))
        for ready_pack in (pack for pack in packs if pack["context_pack_status"] == "ready"):
            self.assertGreaterEqual(len(ready_pack["allowed_facts"]), 1)
            self.assertGreaterEqual(len(ready_pack["evidence_refs"]), 1)
        insufficient_pack = next(pack for pack in packs if pack["context_pack_status"] == "insufficient")
        if not insufficient_pack["evidence_refs"]:
            self.assertEqual(insufficient_pack["allowed_facts"], [])

    def test_allowed_fact_refs_prompt_fields_and_token_budget_exist(self) -> None:
        root, payload = build_prompt_context_project()
        del root
        for pack in payload["context_packs"]:
            evidence_ids = {ref["evidence_ref_id"] for ref in pack["evidence_refs"]}
            for fact in pack["allowed_facts"]:
                self.assertIn("source_evidence_ref_ids", fact)
                for ref_id in fact["source_evidence_ref_ids"]:
                    self.assertIn(ref_id, evidence_ids)
            self.assertIn("Forbidden inference rules", pack["prompt_context_text"])
            self.assertIn("Evidence", pack["prompt_context_text"])
            self.assertIn("Allowed facts", pack["prompt_context_text"])
            self.assertIn("token_budget_estimate", pack)

    def test_manifest_complete_and_forbidden_final_table_check(self) -> None:
        root, payload = build_prompt_context_project()
        del payload
        manifest = json.loads((root / "outputs" / "l3_prompt_context_pack_sample_manifest.json").read_text(encoding="utf-8"))
        for key in (
            "layer",
            "input_prefix",
            "output_prefix",
            "chapter_scope",
            "query_count",
            "context_pack_count",
            "ready_pack_count",
            "partial_pack_count",
            "insufficient_pack_count",
            "total_allowed_fact_count",
            "total_evidence_ref_count",
            "avg_evidence_refs_per_pack",
            "token_budget",
            "over_token_budget_count",
            "hash_mismatch_count",
            "missing_sqlite_child_count",
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

    def test_reporter_outputs_and_verifier_pass_stdout_exact(self) -> None:
        root, payload = build_prompt_context_project()
        del payload
        for name in (
            "l3_prompt_context_pack_sample.md",
            "l3_prompt_context_pack_sample_report.md",
            "l3_prompt_context_pack_sample_verify_report.json",
            "l3_prompt_context_pack_sample_verify_report.md",
        ):
            if name.endswith("_verify_report.json") or name.endswith("_verify_report.md"):
                continue
            self.assertTrue((root / "outputs" / name).exists(), name)
        proc = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "l3_verify_prompt_context_pack.py"),
                "--project-dir",
                str(root),
                "--input-prefix",
                "l3_hybrid_rag_evidence_qa_sample",
                "--expect-output-prefix",
                "l3_prompt_context_pack_sample",
                "--sample-chapters",
                "1,2,1697",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "L3.9 prompt context pack FULL PASS")
        self.assertTrue((root / "outputs" / "l3_prompt_context_pack_sample_verify_report.json").exists())
        self.assertTrue((root / "outputs" / "l3_prompt_context_pack_sample_verify_report.md").exists())

    def test_verifier_detects_forbidden_final_table(self) -> None:
        from scripts import l3_verify_prompt_context_pack as verifier

        root, payload = build_prompt_context_project()
        del payload
        conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
        try:
            conn.execute("CREATE TABLE final_event (id TEXT)")
            conn.commit()
        finally:
            conn.close()
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)
        self.assertGreater(result.forbidden_final_table_count, 0)


if __name__ == "__main__":
    unittest.main()
