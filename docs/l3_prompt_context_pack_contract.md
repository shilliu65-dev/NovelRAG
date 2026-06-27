# L3.9 Prompt Context Pack contract

## Positioning

L3.9 is the Prompt Context Pack Builder.

L3.9 is not a QA system. It does not generate final storyboard output. It does not confirm facts. It does not write `final_event`, `final_timeline`, `final_relationship_graph`, or `final_state_machine`.

L3.9 only converts L3.8 grounded answers and evidence into standardized prompt context packs that can be safely provided to an LLM or Agent.

## Inputs

```text
outputs/l3_hybrid_rag_evidence_qa_sample_answers.json
outputs/l3_hybrid_rag_evidence_qa_sample_manifest.json
index/novel_story_bible.db
```

## Outputs

```text
outputs/l3_prompt_context_pack_sample.json
outputs/l3_prompt_context_pack_sample.md
outputs/l3_prompt_context_pack_sample_manifest.json
outputs/l3_prompt_context_pack_sample_report.md
outputs/l3_prompt_context_pack_sample_verify_report.json
outputs/l3_prompt_context_pack_sample_verify_report.md
```

The L3.9b core builder writes only:

```text
outputs/l3_prompt_context_pack_sample.json
outputs/l3_prompt_context_pack_sample_manifest.json
```

Markdown rendering, reporting, verification reports, and tests are handled by L3.9c / L3.9d / L3.9e.

## Context Pack JSON Structure

Each context pack has this shape:

```json
{
  "query_id": "...",
  "query_text": "...",
  "task_type": "qa | storyboard | character | location | event | mixed",
  "context_pack_status": "ready | partial | insufficient",
  "source_answer_status": "grounded_draft | insufficient_evidence | conflict_evidence",
  "confidence_level": "good | acceptable | weak",
  "allowed_facts": [],
  "evidence_refs": [],
  "forbidden_inference_rules": [],
  "prompt_context_text": "...",
  "token_budget_estimate": 0,
  "warnings": []
}
```

## allowed_facts Structure

```json
{
  "fact_id": "...",
  "fact_text": "...",
  "source_evidence_ref_ids": [],
  "fact_status": "evidence_supported | weakly_supported | insufficient",
  "chapter_scope": "1,2,1697"
}
```

Rules:

- `allowed_facts` must be derived deterministically from `grounded_answer` and evidence `text_excerpt`.
- No allowed fact may be generated without at least one supporting evidence ref.
- Weak or partial evidence must be marked `weakly_supported` or `insufficient`.
- `allowed_facts` are context constraints for downstream generation, not final truth records.

## evidence_refs Structure

```json
{
  "evidence_ref_id": "...",
  "child_segment_id": "...",
  "scene_block_id": "...",
  "chapter_num": 1,
  "paragraph_start": 1,
  "paragraph_end": 3,
  "sentence_start": 1,
  "sentence_end": 5,
  "text_excerpt": "...",
  "evidence_hash": "...",
  "source": "chroma_vector | sqlite_child_keyword | merged"
}
```

Rules:

- `child_segment_id` must exist in SQLite `l3_child_segment`.
- `chapter_num` must stay within the configured sample scope.
- `evidence_hash` must match the L3.8 evidence record and SQLite back-cut.
- Evidence refs are citations, not confirmed final events.

## Default Forbidden Inference Rules

Every context pack must include these rules:

1. Do not use facts outside the provided evidence.
2. Do not invent characters, locations, organizations, events, powers, or relationships.
3. Do not merge evidence from different chapters as a confirmed single event unless evidence explicitly supports it.
4. Do not treat candidate or draft answers as final truth.
5. If evidence is insufficient, say evidence is insufficient.
6. Preserve chapter_num and evidence_ref_id when producing any answer.
7. Do not create final_event, final_timeline, final_relationship_graph, or final_state_machine.

## Quality Standards

### ready

- `source_answer_status = grounded_draft`
- `evidence_count >= 1`
- `allowed_facts >= 1`
- `prompt_context_text` is non-empty

### partial

- `evidence_count >= 1`
- allowed facts are weak, absent, or `confidence_level` is not `good`

### insufficient

- `source_answer_status = insufficient_evidence`
- or `evidence_count = 0`

## Strictly Forbidden Behavior

L3.9 must not:

- Modify L1/L2/L3/L4/L5 source tables.
- Modify `l3_child_segment`.
- Modify any Chroma collection.
- Call an LLM.
- Call an embedding model.
- Write any `final_*` table.
- Treat L3.8 `grounded_answer` as a final fact table.
- Generate an `allowed_fact` without an `evidence_ref`.

## Manifest Requirements

The manifest must include:

```json
{
  "layer": "L3.9",
  "input_prefix": "l3_hybrid_rag_evidence_qa_sample",
  "output_prefix": "l3_prompt_context_pack_sample",
  "chapter_scope": "1,2,1697",
  "query_count": 10,
  "context_pack_count": 10,
  "ready_pack_count": 0,
  "partial_pack_count": 0,
  "insufficient_pack_count": 0,
  "total_allowed_fact_count": 0,
  "total_evidence_ref_count": 0,
  "avg_evidence_refs_per_pack": 0.0,
  "token_budget": 2500,
  "over_token_budget_count": 0,
  "hash_mismatch_count": 0,
  "missing_sqlite_child_count": 0,
  "source_table_mutation_count": 0,
  "forbidden_final_table_count": 0,
  "error_count": 0,
  "warning_count": 0
}
```
