# L3.4 Character Appearance Index Contract

## Purpose

L3.4 builds a deterministic character appearance index from L2 coordinates. It supports characters with multiple visible names, titles, aliases, disguises, or time-limited identities, and it allows the same visible `alias_text` to refer to different `character_id` values in different chapter ranges.

The model separates:

- stable identity: `character_id`
- visible text form: `alias_text`
- temporal validity: `valid_from_chapter_num` / `valid_to_chapter_num`
- occurrence evidence: exact L1/L2 coordinates and hashes

## Hard Constraints

- No LLM calls.
- No embedding.
- No vector database.
- No Chroma writes.
- No L1 or L2 mutation.
- Do not store full chapter, paragraph, or sentence text in L3.4 tables.
- Read source text only through:
  - `v_l2_current_sentences`
  - `v_l2_current_paragraphs`
  - `v_current_chapters`

## Candidate Extractor

`scripts/l3_character_candidate_extractor.py` reads `v_l2_current_sentences` and emits deterministic character name candidates.

Outputs:

- `outputs/l3_character_candidates.json`
- `outputs/l3_character_candidates_report.md`

All automatic results must use:

```text
status = candidate
```

Automatic candidates are not confirmed truth. They are review input.

The extractor must filter obvious non-person terms, including godways, domains, gray world terms, disasters, powers, rules, organizations, pure titles, places, and item names.

## Temporal Alias Model

`l3_character_def` stores stable identities:

```text
character_id
canonical_name
status
source
```

`l3_character_alias` stores visible text aliases:

```text
alias_id
character_id
alias_text
alias_type
valid_from_chapter_num
valid_to_chapter_num
status
evidence_ref
```

The same `alias_text` may be used by multiple `character_id` values. If their chapter ranges overlap, verifier must produce a warning by default, not fail. A later strict mode may promote this warning to failure.

## Appearance Index

`l3_character_appearance` stores exact occurrences:

```text
character_id
alias_id
matched_text
chapter_id
chapter_num
version_id
para_id
sentence_id
sentence_start_offset
sentence_end_offset
match_start_offset
match_end_offset
sentence_hash
paragraph_hash
l1_backcut_matched
```

The indexer must scan only active aliases from `l3_character_alias`. Matching is limited by `valid_from_chapter_num` and `valid_to_chapter_num`. If the same alias appears multiple times in one sentence, each occurrence must be a separate appearance row.

`matched_text` must equal the text that appears in the original prose. Downstream code must not assume `canonical_name` appeared in the prose.

## Verification

`scripts/l3_verify_character_appearance_index.py` must fail when:

- L3.4 tables or required views are missing.
- An appearance references a non-current L1 version.
- Offsets are out of bounds.
- L1 backcut sentence hash does not equal `sentence_hash`.
- L1 backcut matched text does not equal `matched_text`.
- Character names, alias text, status, or temporal ranges are invalid.
- Duplicate appearance keys exist.

Pass output:

```text
L3 character appearance FULL PASS
```

Fail output:

```text
L3 character appearance VERIFY FAIL
```
