# L5.8 Relationship / State Impact Candidate Contract

## Scope

L5.8 builds relationship impact candidates and state impact candidates from L5.5 confirmed events, L5.6 merge groups, and L5.7 timeline anchors.

It does not create a final relationship graph, final state machine, final causal graph, or modify character/location source tables.

## Inputs

- `l5_confirmed_event_candidate`
- `l5_confirmed_event_argument_candidate`
- `l5_confirmed_event_evidence_span`
- `l5_event_merge_group_candidate`
- `l5_event_merge_group_member`
- `l5_timeline_anchor_candidate`
- `l5_timeline_relative_order_candidate`
- optional L3 character/location tables

When optional L3 entity tables are missing, L5.8 uses raw text fallback and records audit rows.

## Created Tables

L5.8 may create and rebuild only:

- `l5_relationship_impact_candidate`
- `l5_state_impact_candidate`
- `l5_impact_candidate_evidence`
- `l5_relationship_state_impact_audit`
- `l5_relationship_state_impact_run`

## Outputs

- `outputs/l5_relationship_impact_candidates.csv`
- `outputs/l5_relationship_impact_candidates.json`
- `outputs/l5_state_impact_candidates.csv`
- `outputs/l5_state_impact_candidates.json`
- `outputs/l5_impact_candidate_evidence.csv`
- `outputs/l5_impact_candidate_evidence.json`
- `outputs/l5_relationship_state_impact_audit.csv`
- `outputs/l5_relationship_state_impact_audit.json`
- `outputs/l5_relationship_state_impact_candidate_report.md`
- `outputs/l5_relationship_state_impact_candidate_manifest.json`
- `outputs/l5_relationship_state_impact_candidate_verify_report.json`
- `outputs/l5_relationship_state_impact_candidate_verify_report.md`

## Verifier

Pass stdout:

```text
L5.8 relationship state impact candidate FULL PASS
```

Fail stdout:

```text
L5.8 relationship state impact candidate FAIL
```
