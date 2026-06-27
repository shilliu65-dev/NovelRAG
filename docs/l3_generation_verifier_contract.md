# L3.12 Generation Verifier contract

## Positioning

L3.12 is a deterministic verifier for L3.11 generation sandbox output.

L3.12 does not call an LLM. It does not call embeddings. It does not access or write Chroma. It does not write SQLite. It does not create `final_event`, `final_timeline`, `final_relationship_graph`, or `final_state_machine`.

L3.12 does not confirm any LLM statement as fact. It only checks whether L3.11 output stays strictly inside the L3.9 context-pack boundary.

## Inputs

```text
outputs/{context_pack_prefix}.json
outputs/{llm_output_prefix}.json
outputs/{llm_output_prefix}_manifest.json
index/novel_story_bible.db
```

## Outputs

```text
outputs/{llm_output_prefix}_verify_report.json
outputs/{llm_output_prefix}_verify_report.md
```

## Structural Checks

- Context pack JSON must parse.
- L3.11 output JSON must parse.
- L3.11 manifest JSON must parse.
- L3.11 output must contain `responses`.
- `response_status` may only be `ready`, `partial`, `insufficient`, or `invalid`.
- Each `response.context_pack_id` must exist in L3.9.
- Each `response.query_id` must match the linked pack.

## Constraint Checks

- If input `context_pack_status != ready`, response must not be `ready`.
- If `allowed_facts` is empty, response must be `insufficient` or `invalid`.
- Empty `response_text` must not be `ready`.
- Non-empty `unsupported_claims` must not be `ready`.
- If `claim_units` is empty while `response_text` contains factual content, verification must fail.

## ID Binding Checks

- `used_fact_ids` must remain inside current pack `allowed_facts`.
- `used_evidence_ids` must remain inside current pack `evidence_refs`.
- `claim_units[*].used_fact_ids` must remain inside current pack.
- `claim_units[*].used_evidence_ids` must remain inside current pack.
- Supported claims must bind at least one fact id and one evidence id.
- Unsupported claims must either appear in `unsupported_claims` or force non-ready status.

## Forbidden Inference Checks

The verifier must flag these phrases in `response_text` and `claim_text`:

- `确认发生`
- `已证明`
- `最终时间线`
- `最终关系图谱`
- `状态机已确认`
- `关系已确认`
- `事件已确认`
- `可以确定`
- `必然导致`
- `因此证明`
- `真实因果`
- `官方结论`
- `final_event`
- `final_timeline`
- `final_relationship_graph`
- `final_state_machine`

## Manifest Checks

The verifier must fail if L3.11 manifest reports:

- `chroma_accessed = true`
- `embedding_called = true`
- `sqlite_written = true`
- `source_table_mutation_count > 0`
- `forbidden_final_table_count > 0`

## Report JSON Requirements

```json
{
  "layer": "L3.12",
  "verified_layer": "L3.11",
  "context_pack_prefix": "l3_prompt_context_pack_sample",
  "llm_output_prefix": "l3_real_llm_consumer_sandbox_sample",
  "response_count": 0,
  "checked_response_count": 0,
  "ready_response_count": 0,
  "partial_response_count": 0,
  "insufficient_response_count": 0,
  "invalid_response_count": 0,
  "unsupported_claim_count": 0,
  "out_of_pack_fact_ref_count": 0,
  "out_of_pack_evidence_ref_count": 0,
  "missing_claim_binding_count": 0,
  "forbidden_inference_violation_count": 0,
  "token_budget_violation_count": 0,
  "schema_error_count": 0,
  "source_table_mutation_count": 0,
  "forbidden_final_table_count": 0,
  "chroma_accessed": false,
  "embedding_called": false,
  "sqlite_written": false,
  "error_count": 0,
  "warning_count": 0,
  "semantic_verification_level": "structural_id_bound",
  "status": "FULL PASS"
}
```

## Missing Output Behavior

If L3.11 output does not exist, the verifier must fail clearly with:

```text
Missing L3.11 output file: outputs/{llm_output_prefix}.json
```

and instruct the operator to run L3.11 mock or real sandbox first.
