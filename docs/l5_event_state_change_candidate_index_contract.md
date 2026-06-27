# L5.0 Event / State Change Candidate Index Contract

L5.0 is a deterministic, evidence-first candidate layer for event-like actions and explicit state-change wording.

It does not confirm story facts. Every automatic event, argument, and state-change row is written with `status = candidate`.

## Scope

First-pass scope is limited to sample chapters:

```text
1
2
1697
```

L5.0 may read current L1/L2 views and optional L3/L4 source tables, but it must write only L5 tables and L5 output reports.

Required read views:

```text
v_current_chapters
v_l2_current_paragraphs
v_l2_current_sentences
```

Optional read sources:

```text
l3_character_appearance
l3_location_appearance
l3_location_def
l3_location_alias
l3_location_candidate
l4_scene_blocks
```

Missing optional sources are reported in the JSON and Markdown reports.

Scene block compatibility:

```text
l4_scene_blocks is preferred when it exists and has compatible columns.
l3_scene_blocks is accepted as a compatible fallback.
scene_blocks is accepted as a final compatible fallback.
```

Compatible scene block tables must expose a stable ID column such as `scene_block_id`, `scene_id`, `scene_key`, or `block_id`, plus `chapter_num`, `start_offset`, and `end_offset`. Missing scene block source is a warning, not a verifier failure.

## L5 Tables

The extractor creates:

```text
l5_event_candidate
l5_event_argument_candidate
l5_event_state_change_candidate
l5_event_evidence_span
l5_event_extraction_run
```

Rebuild deletes and recreates only L5 table contents. L1, L2, L3, and L4 source tables are not mutated.

## Candidate Rules

Event candidates are created from deterministic trigger rules in:

```text
config/event_trigger_rules.seed.json
```

No LLM calls, embeddings, Chroma, or vector database access are used.

Duplicate event key:

```text
chapter_id
version_id
sentence_id
trigger_start_offset
trigger_end_offset
trigger_text
event_type
```

Duplicate evidence span key:

```text
event_candidate_id
sentence_id
span_start_offset
span_end_offset
span_text
```

Duplicate argument key:

```text
event_candidate_id
argument_role
entity_layer
entity_id
entity_text
distance_scope
```

## Outputs

The extractor and reporter write:

```text
outputs/l5_event_candidates_sample.json
outputs/l5_event_candidates_sample_report.md
outputs/l5_event_candidate_verify_report.json
outputs/l5_event_candidate_verify_report.md
```

The verifier prints exactly one final pass/fail message:

```text
L5 event candidate FULL PASS
```

or

```text
L5 event candidate VERIFY FAIL
```
