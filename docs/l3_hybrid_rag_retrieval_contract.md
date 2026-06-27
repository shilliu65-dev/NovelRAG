# L3.7 / L3.7b Hybrid RAG retrieval contract

## Purpose

L3.7 runs a sample hybrid retrieval loop over L3 child segments. It does not generate final answers, call LLMs, create storyboard data, re-chunk text, or write embeddings.

L3.7b reuses the same loop against the real embedding Chroma collection and adds rule-based retrieval quality reporting.

The loop is:

```text
query
-> SQLite title recall
-> SQLite child_segment keyword recall
-> Chroma child_segment vector recall
-> SQLite evidence back-cut
-> retrieval results, report, manifest, verification report
```

## Scope

Current scope is sample only:

```text
chapter_num IN (1, 2, 1697)
```

Default Chroma collection:

```text
novelrag_l3_child_segments_sample
```

L3.7b real embedding collection:

```text
novelrag_l3_child_segments_sample_bge_m3
embedding_provider = sentence-transformers
embedding_model = BAAI/bge-m3
embedding_mode = real_embedding
```

The CLI defaults to the sample scope. It must not silently run all chapters.

## Hard Constraints

```text
No LLM calls.
No answer generation.
No storyboard generation.
No rewriting source text.
No re-chunking.
No embedding writes.
No Chroma writes in retrieval mode.
No mutation of L1/L2/L3/L4/L5 source tables.
No mutation of l3_child_segment source rows.
No mutation of fake Chroma collection.
No mutation of real Chroma collection.
No final_event table.
No final_timeline table.
No final_relationship_graph table.
No final_state_machine table.
```

SQLite is opened with a read-only URI. Chroma is opened only for collection lookup, document reads, and vector queries.

## Required Inputs

SQLite tables:

```text
l3_child_segment
l3_child_segment_sentence_link
```

Chroma collection:

```text
novelrag_l3_child_segments_sample
```

For L3.7b:

```text
novelrag_l3_child_segments_sample_bge_m3
```

Optional SQLite inputs are used when present and reported as warnings when absent:

```text
v_current_chapters
v_l2_current_paragraphs
v_l2_current_sentences
l3_scene_blocks
l3_chapter_title_index
l3_chapter_title_fts
```

## Recall Routes

### SQLite title recall

Uses `l3_chapter_title_fts` when available and falls back to `l3_chapter_title_index` `LIKE` matching.

Output fields:

```text
query_id
query_text
recall_type = sqlite_title
chapter_id
chapter_num
title_score
rank
```

### SQLite child keyword recall

Uses `l3_child_segment.segment_text LIKE` and occurrence scoring.

Output fields:

```text
query_id
query_text
recall_type = sqlite_child_keyword
child_segment_id
scene_id
chapter_id
chapter_num
keyword_score
rank
```

### Chroma vector recall

Uses deterministic `test_fake_embedding` query embeddings to exercise retrieval flow. This mode does not evaluate semantic quality.

Output fields:

```text
query_id
query_text
recall_type = chroma_vector
child_segment_id
scene_id
chapter_id
chapter_num
vector_distance
vector_score
rank
```

## Hybrid Merge

Merged child_segment candidates use:

```text
hybrid_score =
  0.35 * title_score
+ 0.30 * keyword_score
+ 0.35 * vector_score
```

Missing route scores are `0`. The same `child_segment_id` is emitted once per query and records all matched routes in `recall_sources`.

L3.7b recommended real embedding weights:

```text
--title-weight 0.25
--keyword-weight 0.30
--vector-weight 0.45
```

## L3.7b Quality Fields

L3.7b uses the default five queries plus five semantic queries:

```text
陈伶
韩蒙
戏神道
极光界域
灾厄
主角醒来后发生了什么
谁在第一章出现
和灾厄有关的描写
人物第一次遭遇危险
场景里出现的关键人物
```

Each query quality row includes:

```text
query_text
top1_child_segment_id
top1_chapter_num
top1_hybrid_score
top1_recall_sources
top1_snippet
top3_hit_count
top5_hit_count
keyword_hit_count
vector_hit_count
vector_top1_child_segment_id
vector_top1_score
vector_top1_chapter_num
vector_top1_snippet
vector_top3_child_segment_ids
vector_top5_child_segment_ids
keyword_vector_overlap_count
hybrid_top1_recall_sources
zero_hit
quality_label
quality_notes
```

`quality_label` is rule-generated only:

```text
good
acceptable
weak
zero_hit
```

## Evidence Back-Cut

Every final retrieval hit must include:

```text
child_segment_id
scene_id
chapter_id
chapter_num
start_sentence_id
end_sentence_id
start_para_id
end_para_id
segment_text_hash
source_fingerprint
```

Verification recomputes `segment_text_hash`, checks Chroma metadata/document consistency, and verifies `l3_child_segment_sentence_link` backtrace to L2 sentence ids when `v_l2_current_sentences` exists.

## CLI

Required:

```powershell
python scripts\l3_hybrid_rag_retriever.py --project-dir D:\NovelRAG --sample-chapters 1,2,1697 --collection-name novelrag_l3_child_segments_sample
python scripts\l3_hybrid_rag_retriever.py --project-dir D:\NovelRAG --query "陈伶" --sample-chapters 1,2,1697 --collection-name novelrag_l3_child_segments_sample
python scripts\l3_hybrid_rag_retrieval_reporter.py --project-dir D:\NovelRAG
python scripts\l3_verify_hybrid_rag_retrieval.py --project-dir D:\NovelRAG
python scripts\l3_hybrid_rag_retriever.py --project-dir D:\NovelRAG --sample-chapters 1,2,1697 --collection-name novelrag_l3_child_segments_sample_bge_m3 --expect-embedding-mode real_embedding --expect-embedding-model BAAI/bge-m3 --read-only --real-quality-report --output-prefix l3_hybrid_rag_real_embedding_retrieval
python scripts\l3_hybrid_rag_retrieval_reporter.py --project-dir D:\NovelRAG --output-prefix l3_hybrid_rag_real_embedding_retrieval
python scripts\l3_verify_hybrid_rag_retrieval.py --project-dir D:\NovelRAG --collection-name novelrag_l3_child_segments_sample_bge_m3 --expect-embedding-mode real_embedding --expect-embedding-model BAAI/bge-m3 --expect-output-prefix l3_hybrid_rag_real_embedding_retrieval
```

Supported:

```text
--top-k 10
--title-weight 0.35
--keyword-weight 0.30
--vector-weight 0.35
--read-only
```

## Outputs

```text
outputs/l3_hybrid_rag_retrieval_results.json
outputs/l3_hybrid_rag_retrieval_results.csv
outputs/l3_hybrid_rag_retrieval_report.md
outputs/l3_hybrid_rag_retrieval_manifest.json
outputs/l3_hybrid_rag_retrieval_verify_report.json
outputs/l3_hybrid_rag_retrieval_verify_report.md
outputs/l3_hybrid_rag_real_embedding_retrieval_results.json
outputs/l3_hybrid_rag_real_embedding_retrieval_results.csv
outputs/l3_hybrid_rag_real_embedding_retrieval_report.md
outputs/l3_hybrid_rag_real_embedding_retrieval_manifest.json
outputs/l3_hybrid_rag_real_embedding_retrieval_verify_report.json
outputs/l3_hybrid_rag_real_embedding_retrieval_verify_report.md
```

Manifest includes retrieval counts, zero-hit query count, quality label counts, keyword/vector overlap totals, hash/missing-child counts, source and Chroma mutation flags, forbidden final table count, source guard hashes, child_segment fingerprints, and Chroma collection fingerprints.

Verifier success stdout must be exactly:

```text
L3.7 Hybrid RAG retrieval FULL PASS
```

L3.7b verifier success stdout must be exactly:

```text
L3.7b real embedding retrieval quality FULL PASS
```
