# L5.4 Review Decision Intake Contract

## Scope

L5.4 receives human review decisions for L5.3 normalized event candidates.

It may export a review template, import filled review decisions, validate them, write L5.4-only decision tables, build a current decision snapshot, audit conflicts, and export L5.5 readiness rows.

L5.4 does not create confirmed events. It does not create timelines, relationship graphs, merge groups, or final fact tables.

## Inputs

- Source candidates: `l5_normalized_event_candidate`
- Optional source arguments/evidence: `l5_normalized_event_argument`, `l5_normalized_event_evidence`
- Default filled review input: `inputs/l5_review_decision_filled.csv`

If the filled input file is absent, the intake command may run with an in-memory synthetic `uncertain` review set so verification and reporting remain auditable. This fallback does not write to `inputs/`.

## Human Decision Enum

Allowed `human_decision` values:

- `approved_candidate`
- `weak_candidate`
- `duplicate_candidate`
- `needs_context`
- `rejected`
- `uncertain`

## Template Export

Template outputs:

- `outputs/l5_review_decision_template.csv`
- `outputs/l5_review_decision_template.json`
- `outputs/l5_review_decision_template_report.md`
- `outputs/l5_review_decision_template_manifest.json`

Required template columns:

- `normalized_event_id`
- `event_key`
- `chapter_num`
- `scene_block_id`
- `scene_block_source`
- `event_type`
- `event_subtype`
- `subject_text`
- `predicate_canonical`
- `object_text`
- `location_text`
- `time_text`
- `evidence_text_preview`
- `evidence_quality`
- `normalization_confidence`
- `review_recommendation_from_l5_3`
- `human_decision`
- `human_confidence`
- `human_notes`
- `duplicate_of_normalized_event_id`
- `needs_context_reason`
- `reject_reason`
- `reviewer_name`

## L5.4 Tables

L5.4 may create and rebuild only:

- `l5_review_decision_import`
- `l5_review_decision_current`
- `l5_review_decision_conflict_audit`
- `l5_review_decision_run`

`--rebuild` deletes rows only from these L5.4 tables. It must not mutate L5.3, L5.1, L5.0, L1, L2, L3, or L4 source tables.

## Validation Rules

Hard validation errors:

- `normalized_event_id` must exist in L5.3.
- `human_decision` must be in the enum.
- `human_confidence` must be empty, numeric `0.0` through `1.0`, or `low` / `medium` / `high`.
- `duplicate_candidate` requires `duplicate_of_normalized_event_id`.
- `duplicate_of_normalized_event_id` cannot equal the current event.
- `duplicate_of_normalized_event_id` must exist in L5.3.
- `needs_context` requires `needs_context_reason`.
- `rejected` requires `reject_reason` or `human_notes`.
- A single `review_batch_id` cannot give one `normalized_event_id` conflicting decisions.
- `source_file_hash` must be recorded.

Warnings are allowed. PASS requires `error_count = 0`; `warning_count` may be greater than zero.

## Current Snapshot

`l5_review_decision_current` contains at most one row per `normalized_event_id`.

Invalid rows and rows with blocking conflicts do not enter current.

## Conflict Audit

Supported conflict types include:

- `conflicting_decisions`
- `duplicate_cycle`
- `duplicate_target_missing`
- `duplicate_target_rejected`
- `approved_low_confidence`
- `same_event_multiple_current_decisions`

Conflict outputs:

- `outputs/l5_review_decision_conflict_audit.csv`
- `outputs/l5_review_decision_conflict_audit.json`
- `outputs/l5_review_decision_conflict_audit.md`

## L5.5 Readiness Export

Readiness outputs:

- `outputs/l5_confirmed_event_candidate_readiness.csv`
- `outputs/l5_confirmed_event_candidate_readiness.json`
- `outputs/l5_confirmed_event_candidate_readiness_report.md`

Allowed `readiness_status` values:

- `ready_for_l5_5`
- `blocked_by_duplicate`
- `blocked_by_rejected`
- `blocked_by_needs_context`
- `blocked_by_uncertain`
- `blocked_by_weak_candidate`
- `blocked_by_conflict`
- `missing_review_decision`

Only `approved_candidate` current decisions without conflict are `ready_for_l5_5`.

## Verifier

When all hard checks pass, stdout must be exactly:

```text
L5.4 review decision intake FULL PASS
```

On failure, stdout must be:

```text
L5.4 review decision intake FAIL
```
