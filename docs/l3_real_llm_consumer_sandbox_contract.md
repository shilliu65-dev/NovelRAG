# L3.11 Real LLM Consumer Sandbox contract

## Positioning

L3.11 is the first real-LLM consumer sandbox layer.

L3.11 is not a final fact layer. It does not write SQLite source tables, does not write Chroma, and does not create `final_event`, `final_timeline`, `final_relationship_graph`, or `final_state_machine`.

L3.11 consumes L3.9 context packs, sends constrained prompts to either a mock LLM or a real OpenAI-compatible endpoint, and records review artifacts for sandbox inspection.

L3.13b JSON hardening patches the real LLM path so provider output can be recovered when it is wrapped in Markdown or explanatory text, while still failing truly invalid JSON.

## Inputs

```text
outputs/l3_prompt_context_pack_sample.json
outputs/l3_prompt_context_pack_sample_manifest.json
index/novel_story_bible.db
```

## Outputs

```text
outputs/l3_real_llm_consumer_sandbox_sample.json
outputs/l3_real_llm_consumer_sandbox_sample.md
outputs/l3_real_llm_consumer_sandbox_sample_manifest.json
outputs/l3_real_llm_consumer_sandbox_sample_prompt_audit.json
```

## Runtime Modes

- `--mock-llm`
  - used for tests and offline execution
  - does not call external APIs
- real API mode
  - uses `NOVELRAG_LLM_BASE_URL`
  - uses `NOVELRAG_LLM_API_KEY`
  - uses `NOVELRAG_LLM_MODEL`
  - provider flag currently supports `openai_compatible`
- `--read-only`
  - mandatory safety mode
  - forbids SQLite writes
  - forbids Chroma writes
  - forbids final table creation

## Response JSON Structure

Each response row must include at least:

```json
{
  "context_pack_id": "q001",
  "query_id": "q001",
  "response_status": "ready",
  "response_text": "...",
  "claim_units": [],
  "used_fact_ids": [],
  "used_evidence_ids": [],
  "unused_fact_ids": [],
  "unsupported_claims": [],
  "refusal_reason": "",
  "raw_response": "...",
  "extracted_json_text": "...",
  "json_extraction_warnings": [],
  "raw_response_was_wrapped": false,
  "prompt_hash": "...",
  "response_hash": "..."
}
```

Each `claim_units` item must include:

```json
{
  "claim_id": "CLAIM-1",
  "claim_text": "...",
  "used_fact_ids": [],
  "used_evidence_ids": [],
  "claim_status": "supported"
}
```

## Sandbox Rules

- Real LLM output must be JSON.
- The prompt must require exactly one JSON object.
- The prompt must forbid Markdown, code fences, explanatory text, prefixes, suffixes, and trailing commas.
- The prompt must require `{` as the first character and `}` as the final character.
- JSON parse failure must not crash the run.
- JSON parse failure must be recorded as invalid response.
- JSON wrapped in a ```json code block may be extracted before parsing.
- JSON surrounded by explanatory text may be extracted by the first balanced JSON object before parsing.
- If extraction cannot produce a valid JSON object, `json_parse_error_count` must increase.
- Every response must contain `claim_units`.
- Every claim must bind `used_fact_ids` and `used_evidence_ids`.
- `used_fact_ids` must come from the current pack `allowed_facts`.
- `used_evidence_ids` must come from the current pack `evidence_refs`.
- If `unsupported_claims` is non-empty, `response_status` must not be `ready`.
- If `context_pack_status != ready`, then `response_status` must not be `ready`.
- If `allowed_facts` is empty, `response_status` must be `insufficient` or `invalid`.
- The sandbox must not use context outside the current input pack.
- The sandbox must not invent facts beyond the pack.

## Manifest Requirements

```json
{
  "layer": "L3.11",
  "input_prefix": "l3_prompt_context_pack_sample",
  "output_prefix": "l3_real_llm_consumer_sandbox_sample",
  "context_pack_count": 10,
  "llm_call_count": 10,
  "response_count": 10,
  "ready_response_count": 0,
  "partial_response_count": 0,
  "insufficient_response_count": 0,
  "invalid_response_count": 0,
  "unsupported_claim_count": 0,
  "out_of_pack_fact_ref_count": 0,
  "out_of_pack_evidence_ref_count": 0,
  "json_parse_error_count": 0,
  "json_extraction_warning_count": 0,
  "raw_response_wrapped_count": 0,
  "source_table_mutation_count": 0,
  "forbidden_final_table_count": 0,
  "chroma_accessed": false,
  "embedding_called": false,
  "sqlite_written": false,
  "error_count": 0,
  "warning_count": 0
}
```

## Strictly Forbidden Behavior

- Modify any L1, L2, L3, L4, or L5 source table.
- Modify `l3_child_segment`.
- Access or write Chroma.
- Call embedding models.
- Create any `final_*` table.
- Treat sandbox output as final truth.

## OpenAI-Compatible HTTP Behavior

- Requests use `NOVELRAG_LLM_BASE_URL`, `NOVELRAG_LLM_API_KEY`, and `NOVELRAG_LLM_MODEL`.
- Request messages include a JSON-only system message and the hardened user prompt.
- The request may include `response_format: {"type": "json_object"}`.
- If the provider returns HTTP 400 indicating `response_format` is unsupported, the request is retried once without `response_format`.
- API keys must not be printed or written to artifacts.
