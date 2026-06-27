# L3.6 / L3.6b Chroma vector sync contract

## Purpose

L3.6 synchronizes `l3_child_segment` rows from SQLite into a Chroma collection for retrieval.

L3.6 uses deterministic test embeddings. L3.6b writes real embeddings into a separate collection.

This layer does not generate or rewrite text. It only reads `l3_child_segment*`, creates embeddings, writes Chroma documents, and emits sync manifests and verify reports.

## Scope

Current scope is sample only:

```text
chapter_num IN (1, 2, 1697)
```

Default sample collection name:

```text
novelrag_l3_child_segments_sample
```

L3.6b real embedding sample collection name:

```text
novelrag_l3_child_segments_sample_bge_m3
```

Reserved future full collection name:

```text
novelrag_l3_child_segments_full
```

## Hard Constraints

```text
No LLM generation.
No rewriting segment_text.
No re-chunking.
No mutation of L1/L2/L3/L4/L5 source tables.
No mutation of l3_child_segment source rows.
No mutation of fake Chroma collection `novelrag_l3_child_segments_sample` during L3.6b real embedding sync.
No final_event table.
No final_timeline table.
No final_relationship_graph table.
No final_state_machine table.
```

## Required Inputs

```text
l3_child_segment
l3_child_segment_sentence_link
l3_child_segment_build_run
```

Each Chroma document must use:

```text
id = child_segment_id
document = segment_text
```

Required metadata keys:

```text
child_segment_id
scene_id
chapter_id
version_id
chapter_num
segment_index_in_scene
segment_kind
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
segment_text_hash
source_fingerprint
build_run_id
```

## Embedding Mode

L3.6 test mode supports:

```text
--embedding-mode test_fake_embedding
```

Rules:

1. Deterministic.
2. Same input text always produces the same vector.
3. Fixed dimension.
4. Manifest must record `embedding_mode = test_fake_embedding`.
5. It must remain explicitly marked as test-only embedding, not a real model.

L3.6b real mode supports:

```text
--embedding-provider sentence-transformers
--embedding-model BAAI/bge-m3
--embedding-mode real_embedding
```

Rules:

1. The real collection must not be the fake collection.
2. Missing `sentence_transformers` must fail with `sentence_transformers not installed`.
3. Missing/unavailable model must fail with `model BAAI/bge-m3 not available`.
4. The script must not silently fall back to fake embeddings.
5. Manifest must include `embedding_dimension > 0` and `embedding_error_count = 0` for a passing real run.
6. Real outputs must not overwrite fake L3.6 outputs.

## CLI

Required:

```powershell
python scripts\l3_chroma_vector_sync.py --project-dir D:\NovelRAG --sample-chapters 1,2,1697 --rebuild-collection --embedding-mode test_fake_embedding
python scripts\l3_chroma_vector_sync.py --project-dir D:\NovelRAG --chapter-num 1 --rebuild-collection --embedding-mode test_fake_embedding
python scripts\l3_chroma_vector_sync.py --project-dir D:\NovelRAG --sample-chapters 1,2,1697 --collection-name novelrag_l3_child_segments_sample_bge_m3 --rebuild-collection --embedding-provider sentence-transformers --embedding-model BAAI/bge-m3 --embedding-mode real_embedding
python scripts\l3_chroma_vector_sync_reporter.py --project-dir D:\NovelRAG --collection-name novelrag_l3_child_segments_sample_bge_m3
python scripts\l3_verify_chroma_vector_sync.py --project-dir D:\NovelRAG --collection-name novelrag_l3_child_segments_sample_bge_m3 --expect-embedding-mode real_embedding --expect-embedding-model BAAI/bge-m3
```

Supported options:

```text
--collection-name novelrag_l3_child_segments_sample
--embedding-provider test
--embedding-model deterministic-hash
--chroma-dir D:\NovelRAG\index\chroma
```

## Outputs

```text
outputs/l3_chroma_vector_sync_manifest.json
outputs/l3_chroma_vector_sync_report.md
outputs/l3_chroma_vector_sync_verify_report.json
outputs/l3_chroma_vector_sync_verify_report.md
outputs/l3_chroma_real_embedding_sync_manifest.json
outputs/l3_chroma_real_embedding_sync_report.md
outputs/l3_chroma_real_embedding_sync_verify_report.json
outputs/l3_chroma_real_embedding_sync_verify_report.md
```

Manifest must include:

```text
sync_run_id
collection_name
embedding_provider
embedding_model
embedding_mode
chapter_scope
sqlite_child_segment_count
chroma_document_count
synced_document_count
skipped_document_count
hash_mismatch_count
missing_in_chroma_count
extra_in_chroma_count
embedding_dimension
embedding_error_count
source_mutation_detected
child_segment_mutation_detected
fake_collection_mutation_detected
forbidden_final_table_count
created_at
```

## Verifier Requirements

The verifier must check:

1. `l3_child_segment` exists.
2. Sample chapters have child segments.
3. The Chroma collection exists.
4. SQLite child segment count equals Chroma document count.
5. Every Chroma id equals `child_segment_id`.
6. Every Chroma `segment_text_hash` matches SQLite.
7. Every Chroma `chapter_num`, `scene_id`, and `version_id` matches SQLite.
8. No SQLite child segment is missing in Chroma.
9. No extra Chroma document exists outside SQLite.
10. L1/L2/L3/L4/L5 source tables are unchanged.
11. `l3_child_segment` source rows are unchanged.
12. `embedding_mode` and `embedding_model` match expected CLI flags when provided.
13. Real embedding runs have `embedding_dimension > 0`.
14. L3.6b confirms the fake collection fingerprint is unchanged.
15. Forbidden final tables do not exist.

Verifier success stdout must be exactly:

```text
L3.6 Chroma vector sync FULL PASS
```

L3.6b verifier success stdout must be exactly:

```text
L3.6b real embedding Chroma sync FULL PASS
```
