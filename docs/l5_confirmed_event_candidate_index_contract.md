# L5.5 Confirmed Event Candidate Index Contract

## Scope

L5.5 builds a deterministic confirmed-event-candidate layer from L5.4 current review decisions and L5.3 normalized event candidates.

L5.5 only promotes `approved_candidate` rows that are current and conflict-free. It does not create final events, timelines, relationship graphs, merge groups, or any L6-style fact tables.

## Inputs

- Required L5.3 tables:
  - `l5_normalized_event_candidate`
  - `l5_normalized_event_argument`
  - `l5_normalized_event_evidence`
- Required L5.4 tables:
  - `l5_review_decision_current`
  - `l5_review_decision_conflict_audit`
- Required database: `index/novel_story_bible.db`

The effective confirmation input is the subset of `l5_review_decision_current` where:

- `human_decision = approved_candidate`
- the row has no blocking conflict in `l5_review_decision_conflict_audit`
- the referenced `normalized_event_id` still exists in L5.3

L5.5 treats `l5_review_decision_current.review_import_id` as the auditable `current_decision_id`.

## Created Tables

L5.5 may create and rebuild only:

- `l5_confirmed_event_candidate`
- `l5_confirmed_event_argument_candidate`
- `l5_confirmed_event_evidence_span`
- `l5_confirmed_event_blocked_audit`
- `l5_confirmed_event_candidate_run`

`--rebuild` deletes rows only from these L5.5 tables.

## Candidate Semantics

- One `normalized_event_id` may create at most one `confirmed_event_candidate_id`.
- `confirmation_status` must be `confirmed_candidate`.
- A confirmed row must keep:
  - `normalized_event_id`
  - `current_decision_id`
  - `review_batch_id`
  - `review_source_file_hash`
  - `confirmed_candidate_hash`

## Carry Forward

L5.5 carries forward L5.3 rows instead of re-extracting them:

- `l5_normalized_event_argument` -> `l5_confirmed_event_argument_candidate`
- `l5_normalized_event_evidence` -> `l5_confirmed_event_evidence_span`

## Blocked Audit

Every normalized event candidate that does not enter the confirmed table must appear in `l5_confirmed_event_blocked_audit`.

Supported `block_reason` values:

- `blocked_by_missing_review`
- `blocked_by_duplicate`
- `blocked_by_rejected`
- `blocked_by_needs_context`
- `blocked_by_uncertain`
- `blocked_by_weak_candidate`
- `blocked_by_conflict`
- `blocked_by_source_missing`

## Outputs

Required outputs:

- `outputs/l5_confirmed_event_candidates.csv`
- `outputs/l5_confirmed_event_candidates.json`
- `outputs/l5_confirmed_event_arguments.csv`
- `outputs/l5_confirmed_event_arguments.json`
- `outputs/l5_confirmed_event_evidence_spans.csv`
- `outputs/l5_confirmed_event_evidence_spans.json`
- `outputs/l5_confirmed_event_blocked_audit.csv`
- `outputs/l5_confirmed_event_blocked_audit.json`
- `outputs/l5_confirmed_event_candidate_report.md`
- `outputs/l5_confirmed_event_candidate_manifest.json`
- `outputs/l5_confirmed_event_candidate_verify_report.json`
- `outputs/l5_confirmed_event_candidate_verify_report.md`

## Verifier

When hard checks pass, stdout must be exactly:

```text
L5.5 confirmed event candidate index FULL PASS
```

On failure, stdout must be:

```text
L5.5 confirmed event candidate index FAIL
```
