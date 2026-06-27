# L5.7 Timeline Anchor Candidate Contract

## Scope

L5.7 assigns one timeline anchor candidate to each L5.6 merge group candidate.

It does not create a final timeline, final timeline node, global order solution, or flashback resolution.

## Inputs

- `l5_event_merge_group_candidate`
- `l5_event_merge_group_member`
- `l5_confirmed_event_candidate`
- `l5_confirmed_event_evidence_span`
- optional `l3_chapter_time_mapping`
- optional `l3_era_def`
- optional `l3_timeline_anchor`

When optional L3 timeline tables are missing, L5.7 falls back to chapter order.

## Created Tables

L5.7 may create and rebuild only:

- `l5_timeline_anchor_candidate`
- `l5_timeline_relative_order_candidate`
- `l5_timeline_anchor_audit`
- `l5_timeline_anchor_run`

## Outputs

- `outputs/l5_timeline_anchor_candidates.csv`
- `outputs/l5_timeline_anchor_candidates.json`
- `outputs/l5_timeline_relative_order_candidates.csv`
- `outputs/l5_timeline_relative_order_candidates.json`
- `outputs/l5_timeline_anchor_audit.csv`
- `outputs/l5_timeline_anchor_audit.json`
- `outputs/l5_timeline_anchor_candidate_report.md`
- `outputs/l5_timeline_anchor_candidate_manifest.json`
- `outputs/l5_timeline_anchor_candidate_verify_report.json`
- `outputs/l5_timeline_anchor_candidate_verify_report.md`

## Verifier

Pass stdout:

```text
L5.7 timeline anchor candidate FULL PASS
```

Fail stdout:

```text
L5.7 timeline anchor candidate FAIL
```
