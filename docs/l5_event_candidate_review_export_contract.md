# L5.1 Event Candidate Review Export Contract

L5.1 is a read-only human review export layer for the L5.0 event and state-change candidate index.

L5.1 does not confirm events. L5.1 does not normalize events. L5.1 does not merge events. L5.1 does not modify L1/L2/L3/L4/L5 source tables.

## Purpose

L5.1 helps a human reviewer inspect existing L5.0 candidates through deterministic CSV, JSON, Markdown, and manifest artifacts under `outputs/`.

It exports:

- event candidates
- state-change candidates
- argument candidates
- evidence spans and evidence back-cut status
- scene block linkage when available
- duplicate, weak, missing-source, and suspicious-candidate warnings

## L5.0 vs L5.1

L5.0 creates candidate rows in SQLite.

L5.1 reads those rows and emits review files. It is not a fact-confirmation layer and does not write review decisions back to SQLite.

## Inputs

Required L5.0 tables:

```text
l5_event_candidate
l5_event_argument_candidate
l5_event_state_change_candidate
l5_event_evidence_span
l5_event_extraction_run
```

Evidence back-cut may read:

```text
v_l2_current_sentences
v_l2_current_paragraphs
v_current_chapters
```

Optional scene block sources:

```text
l4_scene_blocks
l3_scene_blocks
scene_blocks
```

Optional location sources are reported as warning context when missing.

## Scene Block Compatibility

Scene block source discovery uses `sqlite_master` and `PRAGMA table_info`.

Preferred order:

```text
l4_scene_blocks
l3_scene_blocks
scene_blocks
```

A compatible scene block table must expose `chapter_num`, `start_offset`, `end_offset`, and a stable ID column such as `scene_block_id`, `scene_id`, `scene_key`, or `block_id`.

If no compatible table exists, L5.1 continues and reports:

```text
scene_block_source_status = missing_optional
```

## Outputs

Required output files:

```text
outputs/l5_event_candidate_review.csv
outputs/l5_event_candidate_review.json
outputs/l5_event_candidate_review_report.md
outputs/l5_state_change_candidate_review.csv
outputs/l5_state_change_candidate_review.json
outputs/l5_state_change_candidate_review_report.md
outputs/l5_event_candidate_review_manifest.json
```

CSV files are review workbooks. JSON files contain the same rows. Markdown reports summarize source status, warning counts, back-cut status, review columns, and output files. The manifest records source fingerprints before and after export.

## Review Columns

Event review rows include review status fields, candidate identifiers, chapter coordinates, scene block status, trigger metadata, argument JSON buckets, evidence back-cut text, confidence fields, warning flags, source run metadata, and export timestamp.

State-change review rows include review status fields, state-change identifiers, linked event coordinates, scene block status, before/after state candidates, changed entity JSON, evidence back-cut text, confidence fields, warning flags, source metadata, and export timestamp.

`review_status` defaults to `candidate`. `review_decision` and `review_note` are empty by default.

## Evidence Back-cut Status

Allowed statuses:

```text
ok
missing_coordinate
missing_l2_source
offset_invalid
hash_mismatch
empty_text
not_checked
```

L5.1 may export evidence text into files for review. It never writes evidence text back into SQLite.

## Warning Flags

Warning flags are deterministic JSON arrays. Examples include:

```text
missing_evidence_span
multiple_evidence_spans
missing_trigger_text
missing_argument
missing_subject_candidate
missing_scene_block
invalid_scene_block_ref
evidence_backcut_not_ok
weak_candidate
duplicate_trigger_same_sentence
duplicate_event_same_evidence
state_change_without_event
event_without_state_change
optional_location_source_missing
optional_scene_block_source_missing
```

Warnings do not merge or delete rows.

## Verifier PASS Criteria

The verifier checks required files, required columns, CSV/JSON row parity, JSON cells, warning arrays, evidence status enums, scene source status, scene references when a compatible source exists, source fingerprints, Markdown report sections, and deterministic output across two consecutive exports.

The final verifier stdout line must be exactly:

```text
L5.1 event candidate review export FULL PASS
```

## Non-goals

L5.1 explicitly does not:

- call an LLM
- call embeddings
- write Chroma or vector indexes
- auto-confirm event facts
- create a final confirmed event table
- merge event candidates
- normalize characters, locations, organizations, or powers
- mutate L1/L2/L3/L4/L5 source tables

## Next Layer Suggestions

A later layer may add human review import, reviewer decisions, confirmed event records, or story-bible integration. Those layers must be separate from L5.1 export.
