# L5.1a Event Argument Review Quality Patch Contract

## Purpose

L5.1a improves the human review quality of L5.1 event candidate exports by adding deterministic enhanced argument columns, especially enhanced subject candidates.

L5.1a does not confirm events. L5.1a does not confirm subjects. L5.1a does not normalize candidate rows into final facts. L5.1a does not merge events.

## Position in Pipeline

```text
L5.0  = event / state-change candidate extraction
L5.1  = human review export
L5.1a = argument review quality patch / enhanced review export
L5.2  = normalization seed / rule catalog
L5.3  = future normalized event index
```

L5.1a is still a review/export layer, not a fact layer.

## Difference Between L5.1, L5.1a, L5.2, and Future L5.3

L5.1 exports existing candidates as review files.

L5.1a reads L5.1 files and source evidence, then writes enhanced review files with additive columns.

L5.2 defines deterministic seed catalogs and rules.

Future L5.3 may create normalized event indexes after review. L5.1a does not create L5.3 tables.

## Hard Constraints

L5.1a does not call LLMs, embeddings, Chroma, or vector indexes.

L5.1a does not modify L1/L2/L3/L4/L5.0 source tables.

L5.1a does not modify original L5.1 review exports.

L5.1a does not create `l5_normalized_event`, `l5_normalized_state_change`, `l5_event_merge_group`, or `confirmed_event`.

## Inputs

Required L5.1 files:

```text
outputs/l5_event_candidate_review.csv
outputs/l5_event_candidate_review.json
outputs/l5_event_candidate_review_manifest.json
```

Readable source tables and views:

```text
l5_event_candidate
l5_event_argument_candidate
l5_event_state_change_candidate
l5_event_evidence_span
l5_event_extraction_run
v_l2_current_sentences
v_l2_current_paragraphs
v_current_chapters
```

## Optional Source Discovery

Optional character sources:

```text
l3_character_appearance
l3_character_alias
l3_character_candidate
l3_character_identity
l3_character_def
```

Optional scene block sources use this order:

```text
l4_scene_blocks
l3_scene_blocks
scene_blocks
```

Missing optional sources are warnings.

## Subject Candidate Enhancement Rules

Rules run in deterministic priority order:

1. Preserve existing subject candidates.
2. Use L5 subject-like argument roles.
3. Use same-sentence named character before trigger.
4. Use quoted speech speaker pattern.
5. Use pronoun back-reference to previous sentence.
6. Use paragraph or scene context where available.
7. Mark not found.

Every enhanced candidate keeps `is_confirmed = false`.

## Candidate Safety Rules

Enhanced columns are additive. Original columns remain unchanged.

Enhanced candidates may include source IDs only as candidate provenance. They are not final normalized identities.

No review decision is auto-filled as `accept`.

## Enhanced Review Schema

The enhanced CSV preserves every L5.1 event review column and adds:

```text
enhanced_subject_candidates_json
enhanced_object_candidates_json
enhanced_location_candidates_json
enhanced_time_hint_candidates_json
enhanced_argument_candidates_json
enhanced_subject_source_rule
enhanced_subject_source_kind
enhanced_subject_confidence
enhanced_subject_evidence_text
enhanced_subject_evidence_span_json
enhanced_argument_count
enhanced_subject_candidate_added
enhanced_warning_flags_json
enhanced_quality_score
enhanced_review_recommendation
enhanced_notes
```

## Warning Flags

L5.1a preserves original warnings in `warning_flags_json` and writes revised warnings to `enhanced_warning_flags_json`.

If an enhanced subject is added, `missing_subject_candidate` is removed from the enhanced warning set and `enhanced_subject_added` is added.

## Quality Metrics

The manifest records:

```text
original_missing_subject_candidate_count
enhanced_missing_subject_candidate_count
enhanced_subject_candidate_added_count
ready_for_l5_3_candidate_count
```

## Manifest

The manifest records input files, output files, source fingerprints before and after, original L5.1 file hashes before and after, optional source status, scene source status, character source status, L5.2 seed status, row counts, warning counts, and quality metrics.

## Verifier PASS Criteria

The verifier checks required outputs, preserved original columns, enhanced columns, JSON validity, `is_confirmed = false`, confidence ranges, recommendation enum values, no auto-accept decisions, no future tables, source immutability, original L5.1 file immutability, deterministic enhanced CSV/JSON output, and quality metric improvement when evidence permits.

Final stdout must be exactly:

```text
L5.1a event argument review quality patch FULL PASS
```

## What L5.1a Does Not Do

L5.1a does not confirm events.

L5.1a does not confirm subjects.

L5.1a does not normalize candidate rows into final facts.

L5.1a does not merge events.

L5.1a does not create `l5_normalized_event`.

L5.1a does not modify L1/L2/L3/L4/L5.0 source tables.

L5.1a does not modify original L5.1 review exports.

## Next Layer Suggestions

Future L5.3 can consume enhanced review files after human review or additional deterministic validation. L5.3 should remain separate from this export patch.
