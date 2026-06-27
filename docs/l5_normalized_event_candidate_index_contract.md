# L5.3 Normalized Event Candidate Index Contract

## Scope

L5.3 is the **Normalized Event Candidate Index**. It normalizes safe L5.1a review rows into deterministic candidate tables for audit and later human review.

L5.3 does not create confirmed story facts. It does not create timelines, relationship graphs, merge groups, or confirmed event tables.

## Inputs

- Default input CSV: `outputs/l5_event_candidate_review_enhanced.csv`
- Default sample chapters: `1,2,1697`
- Required seed: `config/event_normalization_rules.seed.json`
- Required database: `index/novel_story_bible.db`

Rows are eligible only when:

- `enhanced_review_recommendation = ready_for_l5_3_candidate`
- `enhanced_subject_candidates_json` contains at least one subject candidate
- `evidence_backcut_status = ok`
- `event_type_candidate` maps to the L5.2 event type catalog

Rows with `likely_duplicate`, `needs_context`, or `weak_candidate` are skipped and counted. Ready rows missing an enhanced subject are skipped as `missing_subject`.

## Created Tables

L5.3 may create and rebuild only these tables:

- `l5_normalized_event_candidate`
- `l5_normalized_event_argument`
- `l5_normalized_state_change_candidate`
- `l5_normalized_event_evidence`
- `l5_normalized_event_source_map`
- `l5_normalized_event_index_run`
- `l5_normalized_event_warning`

The following tables must not be created by L5.3:

- `confirmed_event`
- `l5_confirmed_event`
- `l5_event_timeline`
- `l5_relationship_graph`
- `l5_event_merge_group`
- `l5_normalized_event`
- `l5_normalized_state_change`

## Candidate Semantics

Every inserted event row must keep candidate semantics:

- `source_layer = L5.1a`
- `normalization_status = normalized_candidate`
- `subject_is_confirmed = 0`
- `l5_2_seed_checksum` is nonempty and matches the active seed file hash
- `stable_hash` and IDs are deterministic and exclude runtime timestamps

Every inserted argument and state-change row must use `is_confirmed = 0`.

## Scene Block Source Discovery

L5.3 reuses the compatible scene block discovery contract:

1. Prefer `l4_scene_blocks` when present with valid columns.
2. Else use compatible `l3_scene_blocks`.
3. Else use compatible `scene_blocks`.
4. Else report `missing_optional` as a warning, not an error.

Reporter output must include:

- `detected_scene_block_source`
- `scene_block_source_status`
- `scene_block_table_name`
- `scene_block_linked_event_count`
- `event_without_scene_block_count`

When a compatible source exists, verifier checks that nonempty L5.3 `scene_block_id` values exist in that source.

## Outputs

Required outputs:

- `outputs/l5_normalized_event_candidates_sample.json`
- `outputs/l5_normalized_event_candidates_sample.csv`
- `outputs/l5_normalized_event_candidates_sample_report.md`
- `outputs/l5_normalized_event_candidate_verify_report.json`
- `outputs/l5_normalized_event_candidate_verify_report.md`

Additional audit outputs:

- `outputs/l5_normalized_state_change_candidates_sample.json`
- `outputs/l5_normalized_state_change_candidates_sample.csv`
- `outputs/l5_normalized_event_candidate_manifest.json`

## Verifier

The verifier rebuilds deterministically, validates source immutability, validates L5.2 seed catalog usage, validates evidence rows, checks scene block references when available, and blocks forbidden tables.

When hard checks pass, stdout must be exactly:

```text
L5.3 normalized event candidate index FULL PASS
```
