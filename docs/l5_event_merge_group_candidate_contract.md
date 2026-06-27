# L5.6 Event Merge Group Candidate Contract

## Scope

L5.6 groups L5.5 confirmed event candidates into deterministic merge group candidates.

It does not create final merged events, timelines, relationship graphs, merge groups without the `candidate` semantics, or any world fact table.

## Inputs

- `l5_confirmed_event_candidate`
- `l5_confirmed_event_argument_candidate`
- `l5_confirmed_event_evidence_span`
- `l5_confirmed_event_blocked_audit`
- `l5_confirmed_event_candidate_run`

SQLite is the source of truth. Output files are reports only.

## Created Tables

L5.6 may create and rebuild only:

- `l5_event_merge_group_candidate`
- `l5_event_merge_group_member`
- `l5_event_merge_group_evidence`
- `l5_event_merge_group_audit`
- `l5_event_merge_group_run`

## Rules

Grouping is deterministic. L5.6 uses available L5.5 fields plus carried-forward arguments:

- `predicate_canonical` is derived from `l5_2_event_type`.
- `object_text`, `location_text`, and `time_text` are derived from matching argument roles when present.
- candidates sharing chapter, scene, subject, predicate, and object form `exact_event_signature` groups.
- otherwise each candidate forms a valid `singleton_group`.

`group_status` must be `merge_group_candidate`.

## Outputs

- `outputs/l5_event_merge_group_candidates.csv`
- `outputs/l5_event_merge_group_candidates.json`
- `outputs/l5_event_merge_group_members.csv`
- `outputs/l5_event_merge_group_members.json`
- `outputs/l5_event_merge_group_evidence.csv`
- `outputs/l5_event_merge_group_evidence.json`
- `outputs/l5_event_merge_group_audit.csv`
- `outputs/l5_event_merge_group_audit.json`
- `outputs/l5_event_merge_group_candidate_report.md`
- `outputs/l5_event_merge_group_candidate_manifest.json`
- `outputs/l5_event_merge_group_candidate_verify_report.json`
- `outputs/l5_event_merge_group_candidate_verify_report.md`

## Verifier

Pass stdout:

```text
L5.6 event merge group candidate FULL PASS
```

Fail stdout:

```text
L5.6 event merge group candidate FAIL
```
