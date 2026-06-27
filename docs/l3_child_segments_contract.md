# L3.5 child_segments contract

## Purpose

L3.5 derives smaller retrieval child segments from existing `l3_scene_blocks` plus L2 paragraph and sentence coordinates. It prepares deterministic, sentence-boundary-safe units for later L3.6 vector synchronization.

This layer is a derived L3 table set. It must not mutate L1, L2, existing L3 source tables, L4 tables, or L5 tables.

## Scope

Current execution scope is a small sample loop only:

```text
chapter_num IN (1, 2, 1697)
```

Full 1922-chapter execution is out of scope unless explicitly requested.

## Hard Constraints

```text
No LLM calls.
No embedding.
No Chroma writes.
No vector database writes.
No mutation of L1/L2/L3 existing source tables.
No mutation of L4/L5 source tables.
No final_event table.
No final_timeline table.
No final_relationship_graph table.
No final_state_machine table.
Do not cut inside a sentence.
Do not cross chapter.
Do not cross scene_block.
```

## Inputs

Preferred source objects:

```text
l3_scene_blocks
v_l2_current_paragraphs
v_l2_current_sentences
v_current_chapters
```

The implementation inspects `sqlite_master` and `PRAGMA table_info` before use. Expected coordinates are absolute chapter character offsets.

## Split Parameters

Default parameters:

```text
target_min_chars = 400
target_max_chars = 900
hard_max_chars = 1200
overlap_sentences = 1
```

Rules:

1. A child segment belongs to exactly one `l3_scene_blocks` row.
2. A child segment never crosses chapter boundaries.
3. A child segment never crosses scene block boundaries.
4. A child segment starts and ends on L2 sentence boundaries.
5. The builder prefers sentence-boundary groups near the target length range.
6. The builder may include one overlap sentence from the previous child segment in the same scene.
7. Overlap is explicit: `has_overlap`, `overlap_from_child_segment_id`, and `is_overlap_sentence` in the link table.
8. Every generated child segment has at least one L2 sentence link.
9. If a single sentence exceeds `hard_max_chars`, the sentence is not split and an `oversized_sentence` warning is recorded.

## Output Tables

### `l3_child_segment`

Required columns:

```text
child_segment_id
scene_id
chapter_id
version_id
chapter_num
segment_index_in_scene
segment_kind
segment_text
char_len
sentence_count
paragraph_count
start_para_id
end_para_id
start_sentence_id
end_sentence_id
start_char_offset
end_char_offset
has_overlap
overlap_from_child_segment_id
boundary_status
segment_text_hash
source_fingerprint
build_run_id
created_at
```

### `l3_child_segment_sentence_link`

Links each child segment to its L2 sentence rows in segment order.

Important columns:

```text
child_segment_id
sentence_id
para_id
position_in_segment
sentence_start_offset
sentence_end_offset
sentence_text_hash
is_overlap_sentence
```

### `l3_child_segment_build_run`

Records sample scope, parameters, metrics, source guard hashes, source mutation status, forbidden final table status, and stable row fingerprint.

### `l3_child_segment_warning`

Records non-fatal warnings such as oversized single sentences or source boundary anomalies.

## CLI

Builder:

```powershell
python scripts\l3_child_segment_builder.py --project-dir D:\NovelRAG --sample-chapters 1,2,1697 --rebuild
python scripts\l3_child_segment_builder.py --project-dir D:\NovelRAG --chapter-num 1 --rebuild
```

Reporter:

```powershell
python scripts\l3_child_segment_reporter.py --project-dir D:\NovelRAG
```

Verifier:

```powershell
python scripts\l3_verify_child_segments.py --project-dir D:\NovelRAG
```

Verifier success stdout is exactly:

```text
L3.5 child segments FULL PASS
```

## Reporter Outputs

```text
outputs/l3_child_segments_sample.json
outputs/l3_child_segments_sample_report.md
outputs/l3_child_segments_manifest.json
```

Required metrics:

```text
chapter_count
scene_block_count
child_segment_count
avg_segment_chars
min_segment_chars
max_segment_chars
overlap_segment_count
oversized_sentence_warning_count
empty_segment_count
boundary_warning_count
source_mutation_detected
```

## Verifier Outputs

```text
outputs/l3_child_segments_verify_report.json
outputs/l3_child_segments_verify_report.md
```

Verifier checks:

1. `child_segment` does not cross chapter.
2. `child_segment` does not cross scene block.
3. `child_segment` does not cut inside a sentence.
4. Every `child_segment` contains at least one sentence.
5. Every `child_segment` is traceable through L2 sentence links.
6. `segment_text_hash` recomputes consistently.
7. `source_fingerprint` recomputes consistently.
8. Two rebuild rounds are idempotent.
9. Source tables are not modified.
10. Forbidden final tables do not exist.
11. Sample chapters generate child segments.
