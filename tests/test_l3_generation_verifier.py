import json
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

from tests.test_l3_prompt_context_pack import build_prompt_context_project


def build_generation_verifier_project() -> Path:
    from scripts import l3_real_llm_consumer_sandbox as sandbox

    root, _payload = build_prompt_context_project()
    result = sandbox.run_sandbox(root, mock_llm=True, read_only=True)
    assert result.ok, result.errors
    return root


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class L3GenerationVerifierTests(unittest.TestCase):
    def test_normal_mock_output_passes(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        result = verifier.run_verification(root)
        self.assertTrue(result.ok, result.errors)

    def test_out_of_pack_fact_id_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        payload["responses"][0]["used_fact_ids"].append("FACT-999")
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)
        self.assertGreater(result.out_of_pack_fact_ref_count, 0)

    def test_out_of_pack_evidence_id_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        payload["responses"][0]["used_evidence_ids"].append("EVID-999")
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)
        self.assertGreater(result.out_of_pack_evidence_ref_count, 0)

    def test_supported_claim_without_evidence_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        payload["responses"][0]["claim_units"][0]["used_evidence_ids"] = []
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)
        self.assertGreater(result.missing_claim_binding_count, 0)

    def test_supported_claim_without_fact_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        payload["responses"][0]["claim_units"][0]["used_fact_ids"] = []
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)
        self.assertGreater(result.missing_claim_binding_count, 0)

    def test_unsupported_claims_with_ready_status_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        payload["responses"][0]["unsupported_claims"] = ["x"]
        payload["responses"][0]["response_status"] = "ready"
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_non_ready_pack_with_ready_response_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        context_payload = load_json(root / "outputs" / "l3_prompt_context_pack_sample.json")
        target_pack = next(pack for pack in context_payload["context_packs"] if pack["context_pack_status"] != "ready")
        output_payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        target_response = next(item for item in output_payload["responses"] if item["context_pack_id"] == target_pack["query_id"])
        target_response["response_status"] = "ready"
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_empty_allowed_facts_with_ready_response_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        context_payload = load_json(root / "outputs" / "l3_prompt_context_pack_sample.json")
        target_pack = next(pack for pack in context_payload["context_packs"] if not pack["allowed_facts"])
        output_payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        target_response = next(item for item in output_payload["responses"] if item["context_pack_id"] == target_pack["query_id"])
        target_response["response_status"] = "ready"
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(output_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_forbidden_final_table_count_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        manifest = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json")
        manifest["forbidden_final_table_count"] = 1
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_chroma_accessed_true_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        manifest = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json")
        manifest["chroma_accessed"] = True
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_embedding_called_true_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        manifest = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json")
        manifest["embedding_called"] = True
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_sqlite_written_true_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        manifest = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json")
        manifest["sqlite_written"] = True
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_forbidden_inference_phrase_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        payload["responses"][0]["response_text"] = "可以确定 事件已确认"
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)
        self.assertGreater(result.forbidden_inference_violation_count, 0)

    def test_missing_claim_units_fails(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        del payload["responses"][0]["claim_units"]
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)

    def test_missing_claim_unit_fields_fail(self) -> None:
        from scripts import l3_verify_generation_output as verifier

        root = build_generation_verifier_project()
        payload = load_json(root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json")
        claim = payload["responses"][0]["claim_units"][0]
        del claim["claim_id"]
        del claim["claim_text"]
        del claim["used_fact_ids"]
        del claim["used_evidence_ids"]
        del claim["claim_status"]
        (root / "outputs" / "l3_real_llm_consumer_sandbox_sample.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
