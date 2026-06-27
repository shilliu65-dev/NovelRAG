# L3.10 Prompt Consumer Dry Run contract

## Positioning

L3.10 is a read-only prompt consumer dry-run layer.

L3.10 is not a fact writer. It does not confirm truth. It does not write `final_event`, `final_timeline`, `final_relationship_graph`, or `final_state_machine`.

L3.10 only consumes L3.9 prompt context packs and produces deterministic mock review artifacts that show whether downstream constrained generation can stay inside the provided evidence boundary.

## Inputs

```text
outputs/l3_prompt_context_pack_sample.json
outputs/l3_prompt_context_pack_sample_manifest.json
index/novel_story_bible.db
```

## Outputs

```text
outputs/l3_prompt_consumer_dryrun_sample.json
outputs/l3_prompt_consumer_dryrun_sample.md
outputs/l3_prompt_consumer_dryrun_sample_manifest.json
outputs/l3_prompt_consumer_dryrun_verify_report.json
outputs/l3_prompt_consumer_dryrun_verify_report.md
```

## Dry Run JSON Structure

```json
{
  "layer": "L3.10",
  "input_prefix": "l3_prompt_context_pack_sample",
  "output_prefix": "l3_prompt_consumer_dryrun_sample",
  "query_count": 10,
  "responses": [
    {
      "context_pack_id": "q001",
      "query_id": "q001",
      "query_text": "...",
      "source_context_pack_status": "ready",
      "response_status": "ready",
      "response_text": "...",
      "used_fact_ids": [],
      "used_evidence_ids": [],
      "unused_fact_ids": [],
      "evidence_refs": [],
      "token_budget": 2500,
      "token_budget_estimate": 0,
      "warnings": []
    }
  ]
}
```

## Deterministic Mock Rules

- Default mode does not call a real LLM.
- The consumer may only use `context_pack.allowed_facts`.
- Each fact sentence in `response_text` must carry at least one `evidence_ref_id`.
- `used_fact_ids`, `used_evidence_ids`, and `unused_fact_ids` must be recorded.
- If `context_pack_status != ready`, then `response_status` must not be `ready`.
- If `allowed_facts` is empty, `response_status` must be `insufficient`.
- The consumer must not use information outside the input context pack.
- The consumer must not invent new facts.

## Response Status Rules

- `ready`
  - source pack status is `ready`
  - `allowed_facts >= 1`
  - `used_fact_ids >= 1`
  - `used_evidence_ids >= 1`
  - `response_text` is non-empty
- `partial`
  - source pack status is not `ready`
  - but `allowed_facts >= 1`
- `insufficient`
  - `allowed_facts = 0`
  - or no evidence-backed response can be constructed

## Manifest Requirements

The manifest must include at least:

```json
{
  "layer": "L3.10",
  "input_prefix": "l3_prompt_context_pack_sample",
  "output_prefix": "l3_prompt_consumer_dryrun_sample",
  "query_count": 10,
  "response_count": 10,
  "ready_response_count": 0,
  "partial_response_count": 0,
  "insufficient_response_count": 0,
  "total_used_fact_count": 0,
  "total_used_evidence_count": 0,
  "avg_used_facts_per_response": 0.0,
  "token_budget": 2500,
  "over_token_budget_count": 0,
  "source_table_mutation_count": 0,
  "forbidden_final_table_count": 0,
  "error_count": 0,
  "warning_count": 0
}
```

## Strictly Forbidden Behavior

- Modify any L1, L2, L3, L4, or L5 source table.
- Modify `l3_child_segment` source data.
- Access or write Chroma.
- Call embedding models.
- Call an LLM.
- Create `final_event`, `final_timeline`, `final_relationship_graph`, or `final_state_machine`.
- Treat L3.10 dry-run output as final truth.
