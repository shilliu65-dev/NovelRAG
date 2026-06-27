# L3 Evidence Review Verifier Contract

## Purpose

Build a deterministic human review queue from `outputs/l3_seed_evidence_candidates.json`.

This layer verifies candidate structure, repeats L1 backcut checks, detects duplicate paragraph evidence, adds review risk flags, and prepares files for manual review.

## Hard Constraints

- No LLM calls.
- No embedding.
- No vector database.
- No Chroma writes.
- No L1 or L2 mutation.
- Do not automatically write confirmed knowledge.
- Candidate input status must be `candidate`.
- Final human decision fields are reserved for manual review.

Allowed SQLite reads:

- `v_current_chapters`
- `v_l2_current_paragraphs` optionally
- `v_l2_current_sentences` optionally

The verifier currently needs only `v_current_chapters` for L1 backcut verification.

## Input

Default input:

```text
outputs/l3_seed_evidence_candidates.json
```

Each candidate must contain:

```text
status
evidence_type
score
score_breakdown
matched_keywords
chapter_id
version_id
chapter_num
chapter_title
paragraph_id
paragraph_index
char_start
char_end
paragraph_hash
paragraph_text
l1_backcut_check
```

If any candidate status is not `candidate`, the verifier fails.

## Backcut Verification

For every candidate, the verifier reads `v_current_chapters.content_full_text` by `chapter_id`, then computes:

```python
sha256(content_full_text[char_start:char_end]) == paragraph_hash
```

The result is written as `verifier_backcut_check`.

If the check fails:

- `invalid_backcut` is added to `review_flags`.
- `eligible_for_human_confirm` is `false`.
- `suggested_status` is `invalid`.

## IDs And Dedupe

`evidence_id` is deterministic:

```python
sha256(seed_file + item_path + paragraph_id + char_start + char_end + paragraph_hash)
```

`duplicate_group_id` is deterministic:

```python
sha256(paragraph_id + char_start + char_end + paragraph_hash)
```

Dedupe rules:

- Within the same seed item, duplicate `paragraph_id + char_start + char_end` candidates are retained once.
- Different seed items may share the same paragraph evidence.
- Shared paragraph evidence is flagged with `duplicate_paragraph`.

## Suggested Status

Allowed values:

- `needs_review`
- `likely_relevant`
- `likely_irrelevant`
- `invalid`

The verifier never writes final statuses:

- `confirmed`
- `rejected`
- `uncertain`

Manual fields are always initialized to:

```json
{
  "human_status": null,
  "human_reviewer": null,
  "human_note": null,
  "reviewed_at": null
}
```

## Outputs

- `outputs/l3_evidence_review_queue.json`
- `outputs/l3_evidence_review_queue.md`
- `outputs/l3_evidence_review_queue.csv`
- `outputs/l3_evidence_review_verifier_report.md`

The JSON output contains:

- `meta`
- `summary`
- `review_items`

Default retention is:

- at most `3` review items per seed item after per-seed dedupe

Output order is stable across repeated runs for the same input and database state.
