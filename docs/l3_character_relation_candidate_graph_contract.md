# L3.4R Character Relation Candidate Graph Contract

## Purpose

L3.4R builds a deterministic character relation candidate graph from L3.4 character appearance evidence.

It does not infer real narrative relationships such as family, enemy, lover, teacher, disciple, faction membership, betrayal, or alliance. It only records mechanical co-occurrence evidence between stable character identities.

The graph supports:

- ego-centered retrieval around Chen Ling / 陈伶
- same-sentence character co-occurrence
- same-paragraph character co-occurrence
- same-scene character co-occurrence
- same-chapter weak co-occurrence
- nearby-window co-occurrence
- later manual or model-assisted relation confirmation

## Hard Constraints

- No LLM calls.
- No embedding.
- No vector database.
- No Chroma writes.
- No L1 or L2 mutation.
- Do not store full chapter, paragraph, sentence, or scene text.
- Do not infer semantic relationship truth automatically.
- All automatic relation rows must use `status = candidate`.
- All automatic relation types must be mechanical evidence types, not narrative truth.

Allowed automatic relation evidence types:

- `same_sentence`
- `same_paragraph`
- `same_scene_block`
- `same_chapter`
- `nearby_window`

Forbidden automatic relation types:

- `father`
- `mother`
- `lover`
- `enemy`
- `ally`
- `teacher`
- `disciple`
- `member_of`
- `leader_of`
- `betrayed`
- `killed`
- `saved`
- `controlled`
- `identity_is`

These may only appear in a later manually reviewed relation fact table.

Default builder scope should stay limited to strong local evidence:

- `same_sentence`
- `same_paragraph`

The broader scopes `same_scene_block`, `same_chapter`, and `nearby_window` are supported but should be explicitly requested because they can create very large weak-evidence tables on long novels.

## Source Tables

L3.4R may only read:

- `l3_character_def`
- `l3_character_alias`
- `l3_character_appearance`
- `v_l2_current_sentences`
- `v_l2_current_paragraphs`
- `v_current_chapters`
- `l3_scene_blocks` if available and verified

L3.4R must not read raw L1 tables directly except through existing verified views or validated backcut logic used by verifiers.

## Tables

### l3_character_relation_evidence

Stores one deterministic co-occurrence evidence unit.

```text
evidence_id
character_id_a
character_id_b
chapter_id
chapter_num
version_id
evidence_scope
scene_block_id
para_id
sentence_id
appearance_ids_a_json
appearance_ids_b_json
mention_count_a
mention_count_b
offset_distance_min
evidence_hash
status
source
created_at
```

`character_id_a` must be lexicographically smaller than `character_id_b`. Self-relations are forbidden. `appearance_ids_a_json` and `appearance_ids_b_json` contain appearance ids only, never prose text.

## Verification

`scripts/l3_verify_character_relation_candidate_graph.py` must fail when:

- required source tables or views are missing
- `l3_character_relation_evidence` is missing
- evidence scope is not one of the allowed mechanical scopes
- forbidden semantic relation types appear
- status is not `candidate`
- self-relations exist
- unordered character pairs exist
- duplicate relation evidence keys exist
- referenced characters do not exist
- referenced appearance ids do not exist
- L3.4R stores full text columns

Pass output:

```text
L3 character relation candidate graph FULL PASS
```

Fail output:

```text
L3 character relation candidate graph VERIFY FAIL
```
