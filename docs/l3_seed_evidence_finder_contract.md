# L3 Seed Evidence Finder Contract

## Purpose

Link seed definitions to L2 paragraph evidence candidates and L1 backcut checks.

This layer is retrieval only:

- Seed is a hypothesis.
- Evidence is a deterministic retrieval result.
- Verification belongs to the next layer or to human review.

## Hard Constraints

- No LLM calls.
- No embedding.
- No vector database.
- No Chroma writes.
- No L1 or L2 mutation.
- Only read these SQLite views:
  - `v_l2_current_paragraphs`
  - `v_current_chapters`

## Inputs

- `--project-dir`: project root.
- `--seed`: one seed JSON file.
- `--top-k`: max candidates per seed item.
- `--context-paragraphs`: same-chapter neighboring paragraphs to include.
- `--output-json`: optional JSON output path.
- `--output-md`: optional Markdown report path.

Seed items are extracted recursively from dictionaries containing seed marker fields such as `id`, `name`, `keywords`, `alias`, `aliases`, `godway_id`, `rule_id`, `domain_id`, `lord_id`, or `rank`.

Positive retrieval terms come from:

- `name`
- `alias`
- `aliases`
- `keywords`
- `representative_characters`
- `branches`

Rule retrieval terms come from:

- `forbidden_patterns`

Nested strings, arrays, and objects are flattened safely. Empty terms and duplicate terms are ignored. Terms are preserved as written; the finder does not segment Chinese text.

## Retrieval And Evidence Rules

- Paragraph retrieval uses parameterized `LIKE ? ESCAPE '\'`.
- `%`, `_`, and `\` are escaped before retrieval.
- Retrieval reads `v_l2_current_paragraphs` only.
- L1 backcut reads `v_current_chapters` only.
- Candidate dedupe key is `paragraph_id`.
- Stable sort order is `score DESC`, `chapter_num ASC`, `paragraph_index ASC`, `paragraph_id ASC`.
- Candidate status is always `candidate`.
- Allowed evidence types:
  - `positive_candidate`
  - `rule_violation_candidate`
- Backcut check must compare `content_full_text[char_start:char_end]` with `paragraph_hash`.
- Backcut failures are excluded from candidates and reported in output metadata.

## Output Guarantee

Each candidate evidence record contains:

- `chapter_id`
- `version_id`
- `chapter_num`
- `chapter_title`
- `paragraph_id`
- `paragraph_index`
- `char_start`
- `char_end`
- `paragraph_hash`
- `paragraph_text`
- `context`
- `l1_backcut_check`

Outputs:

- `outputs/l3_seed_evidence_candidates.json`
- `outputs/l3_seed_evidence_report.md`

## Status Policy

Only `candidate` is allowed in this layer.

The finder must not produce:

- `confirmed`
- `rejected`
- `uncertain`

Confirmed or rejected judgments are the responsibility of a later verifier or human review.
