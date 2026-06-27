# L5.9 Event Understanding Quality Gate Contract

## Scope

L5.9 is the quality gate and cross-layer audit for the L5.0-L5.8 event understanding pipeline.

It reads prior L5 event-layer outputs, records chain completeness, quality issues, metrics, and next-stage readiness. It does not create final events, final timelines, final relationship graphs, final state machines, embeddings, Chroma rows, or world fact tables.

## Inputs

L5.9 reads SQLite as the source of truth. It uses compatible table discovery for earlier layers:

- L5.3 normalized event candidate tables
- L5.4 review decision tables
- L5.5 confirmed event candidate tables
- L5.6 event merge group candidate tables
- L5.7 timeline anchor candidate tables
- L5.8 relationship/state impact candidate tables

Missing L5.5-L5.8 core tables are hard errors. Earlier optional shape differences are recorded as audit signals where possible.

## Created Tables

L5.9 may create and rebuild only:

- `l5_event_quality_gate_summary`
- `l5_event_quality_gate_chain`
- `l5_event_quality_gate_issue`
- `l5_event_quality_gate_metric`
- `l5_event_quality_gate_run`

## Status Semantics

`quality_gate_status` may be:

- `structure_pass`
- `quality_warning`
- `quality_fail`

Warnings are allowed for sample data, singleton-only groups, fallback anchors, missing relationship impact, and low sample size. Hard errors make the status `quality_fail`.

## Outputs

- `outputs/l5_event_quality_gate_summary.json`
- `outputs/l5_event_quality_gate_summary.md`
- `outputs/l5_event_quality_gate_chain.csv`
- `outputs/l5_event_quality_gate_chain.json`
- `outputs/l5_event_quality_gate_issues.csv`
- `outputs/l5_event_quality_gate_issues.json`
- `outputs/l5_event_quality_gate_metrics.csv`
- `outputs/l5_event_quality_gate_metrics.json`
- `outputs/l5_event_quality_gate_manifest.json`
- `outputs/l5_event_quality_gate_verify_report.json`
- `outputs/l5_event_quality_gate_verify_report.md`

## Verifier

Pass stdout:

```text
L5.9 event understanding quality gate FULL PASS
```

Fail stdout:

```text
L5.9 event understanding quality gate FAIL
```
