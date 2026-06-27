# L5.2 Event Normalization Seed / Rule Contract

## Purpose

L5.2 defines deterministic seed catalogs and rule contracts for future event normalization in NovelRAG. It is an auditable rule definition layer, not a fact-generation layer.

L5.2 does not confirm events.
L5.2 does not normalize candidate rows into final facts.
L5.2 does not merge event candidates.
L5.2 does not modify L1/L2/L3/L4/L5.0 source tables.
L5.2 only defines deterministic rule seeds for future L5.3.

## Position in Pipeline

```text
L5.0 = event / state-change candidate extraction
L5.1 = human review export
L5.2 = normalization seed / rule catalog
L5.3 = future normalized event index
```

## Difference Between L5.0, L5.1, L5.2, and Future L5.3

L5.0 creates candidate rows from deterministic trigger rules. L5.1 exports those candidate rows for human review. L5.2 defines the catalogs and validation rules that future normalization should use. Future L5.3 may create normalized indexes only after its own task explicitly authorizes that execution layer.

## Hard Constraints

- No LLM calls.
- No embedding model calls.
- No Chroma writes.
- No vector index creation or updates.
- No mutation of L1/L2/L3/L4/L5.0 source tables.
- No confirmed event creation.
- No normalized event table creation.
- No automatic candidate confirmation.
- No automatic candidate merge.
- Candidate data is not story truth.

## Input Files

- `config/event_normalization_rules.seed.json`
- Optional read-only coverage inputs:
  - `index/novel_story_bible.db`
  - `outputs/l5_event_candidate_review.csv`
  - `outputs/l5_state_change_candidate_review.csv`
  - `outputs/l5_event_candidate_review.json`
  - `outputs/l5_state_change_candidate_review.json`

## Output Files

- `outputs/l5_event_normalization_rules_verify_report.json`
- `outputs/l5_event_normalization_rules_verify_report.md`
- Optional reporter outputs:
  - `outputs/l5_event_normalization_rules_coverage_report.json`
  - `outputs/l5_event_normalization_rules_coverage_report.md`

## Event Type Catalog

The event type catalog defines canonical future event types, labels, allowed subtypes, default argument roles, allowed state-change links, and major-event candidacy. Required types include movement, dialogue, conflict, death, performance, disaster_event, world_rule_change, knowledge_change, relationship_change, and other canonical L5.2 types.

Allowed subtypes are scoped to their parent event type and must use lowercase snake_case.

## State Change Type Catalog

The state change type catalog defines canonical future state-change types, including location_change, identity_change, injury_change, death_change, power_change, world_rule_change, disaster_state_change, scene_presence_change, and status_change.

Each state-change type lists allowed event types and default before/after field names for future L5.3 use.

## Argument Role Catalog

The argument role catalog defines canonical event argument roles such as subject, object, target, agent, patient, speaker, listener, mover, origin_location, destination_location, power, artifact, evidence, scene, chapter, and unknown.

Argument role entity kinds must be drawn only from the supported entity kind enum:

```text
character
location
organization
power
artifact
disaster
world_rule
time
chapter
scene
unknown
```

## Trigger Category Mapping

Trigger category mappings connect L5.0 trigger categories or event families to safe canonical event types. Unknown trigger categories map explicitly to a generic status_change fallback and may reduce confidence.

## Candidate To Normalized Field Mapping

The `candidate_to_normalized_field_mapping` section declares how L5.1 review fields would map to future L5.3 normalized event fields. This is a contract only. It must not create future tables or transform candidate rows in L5.2.

## Confidence Rules

Confidence rules define deterministic future score adjustments and bounds. Positive boost rules must have positive deltas. Penalty rules must have negative deltas. Review rejection must force a very low confidence ceiling.

## Importance Rules

Importance rules define allowed future importance levels:

```text
minor
normal
major
critical
```

Rules such as death, revival, power upgrade, world rule change, disaster events, and major battles are classified with higher default importance.

## Merge Key Rules

Merge key rules define future grouping contracts only. They must never imply automatic confirmation. The `merge_forbidden_without_review` rule is mandatory and uses strategy `forbidden`.

Allowed merge strategies:

```text
manual_review_required
future_deterministic_candidate_grouping
forbidden
```

## Reject Rules

Reject rules define future unusable-candidate decisions. They never delete candidate rows and never mutate source tables.

Allowed reject levels:

```text
soft
hard
manual_only
```

## Downgrade Rules

Downgrade rules define future candidate quality reductions for missing arguments, weak triggers, missing scene blocks, missing optional sources, and ambiguous evidence.

Allowed downgrade targets:

```text
candidate
weak_candidate
needs_context
uncertain
```

## Warning Flag Catalog

The warning flag catalog defines recognized audit flags, severities, descriptions, and recommended review actions. Flags must be unique and use allowed severities:

```text
info
warning
suspect
error
```

## Guardrails

Required guardrails:

- `candidate_is_not_truth`
- `manual_review_before_confirmed_event`
- `no_auto_entity_resolution`
- `evidence_required`
- `source_table_read_only`

These guardrails are mandatory verifier checks.

## Future Tables Not Created In L5.2

L5.2 may describe future tables but must not create them:

- `l5_normalized_event`
- `l5_normalized_state_change`
- `l5_event_merge_group`

The verifier also checks that `confirmed_event` does not exist.

## Verifier PASS Criteria

The verifier passes only when there are no errors. It validates seed structure, required catalogs, unique IDs, references, enum consistency, checksum behavior, future table absence, and report generation.

Warnings are allowed in default mode. With `--strict`, optional coverage warnings become failures.

The final stdout line must be exactly:

```text
L5.2 event normalization seed/rule FULL PASS
```

## What L5.2 Does Not Do

- It does not call LLMs.
- It does not call embedding models.
- It does not write Chroma.
- It does not build or update vector indexes.
- It does not mutate L1/L2/L3/L4/L5.0 tables.
- It does not mutate review exports except by generating optional reports.
- It does not create `l5_normalized_event`.
- It does not create `confirmed_event`.
- It does not auto-confirm, auto-merge, or infer story facts.

## Next Layer Suggestions

Future L5.3 should consume this seed only after a separate task defines reviewed acceptance criteria, normalized table schemas, source immutability checks, and migration-safe output behavior. L5.3 should keep all source candidate and review data immutable and preserve evidence coordinates and hashes.
