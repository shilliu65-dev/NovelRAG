# L3.8 Hybrid RAG evidence QA contract

## Purpose

L3.8 builds deterministic grounded answer drafts from L3.7b hybrid retrieval results.

It does not confirm final facts, generate final events, build timelines, call LLMs, call embedding models, rewrite source text, or write Chroma.

## Inputs

```text
outputs/l3_hybrid_rag_real_embedding_retrieval_results.json
outputs/l3_hybrid_rag_real_embedding_retrieval_manifest.json
index/novel_story_bible.db
```

Default scope:

```text
chapter_num IN (1, 2, 1697)
```

## Hard Constraints

```text
No LLM calls.
No answer generation beyond deterministic grounded_draft templates.
No storyboard generation.
No rewriting source text.
No re-chunking.
No embedding writes.
No embedding model calls.
No Chroma writes.
No mutation of L1/L2/L3/L4/L5 source tables.
No mutation of l3_child_segment source rows.
No final_event table.
No final_timeline table.
No final_relationship_graph table.
No final_state_machine table.
```

SQLite is opened with a read-only URI. The layer only reads retrieval outputs and SQLite coordinates.

## CLI

```powershell
python scripts\l3_hybrid_rag_evidence_qa.py --project-dir D:\NovelRAG --input-prefix l3_hybrid_rag_real_embedding_retrieval --output-prefix l3_hybrid_rag_evidence_qa_sample --sample-chapters 1,2,1697 --read-only --max-evidence-per-query 5 --min-evidence-per-answer 1
python scripts\l3_hybrid_rag_evidence_qa_reporter.py --project-dir D:\NovelRAG --output-prefix l3_hybrid_rag_evidence_qa_sample
python scripts\l3_verify_hybrid_rag_evidence_qa.py --project-dir D:\NovelRAG --input-prefix l3_hybrid_rag_real_embedding_retrieval --expect-output-prefix l3_hybrid_rag_evidence_qa_sample --sample-chapters 1,2,1697
```

## Answer Shape

Each answer includes:

```text
query_id
query_text
answer_status
grounded_answer
confidence_level
evidence_count
evidence
warnings
```

Allowed `answer_status` values:

```text
grounded_draft
insufficient_evidence
conflict_evidence
```

Allowed `confidence_level` values:

```text
good
acceptable
weak
```

Each evidence item includes:

```text
rank
child_segment_id
scene_block_id
chapter_id
chapter_num
paragraph_start
paragraph_end
sentence_start
sentence_end
source
score
text_excerpt
evidence_hash
```

`evidence_hash` is recomputed from SQLite child segment id, scene id, chapter id, chapter number, paragraph/sentence bounds, `segment_text_hash`, and `source_fingerprint`.

## Deterministic Answer Rule

For each query:

1. Take the top deduplicated child segments from L3.7b merged hits.
2. Re-read each segment from SQLite.
3. Verify sample scope and segment hash.
4. Build a deterministic draft by joining evidence excerpts.
5. Append `evidence_refs`.

If no evidence remains, `answer_status` must be `insufficient_evidence` and `grounded_answer` must be empty.

## Outputs

```text
outputs/l3_hybrid_rag_evidence_qa_sample_answers.json
outputs/l3_hybrid_rag_evidence_qa_sample_answers.csv
outputs/l3_hybrid_rag_evidence_qa_sample_report.md
outputs/l3_hybrid_rag_evidence_qa_sample_manifest.json
outputs/l3_hybrid_rag_evidence_qa_sample_verify_report.json
outputs/l3_hybrid_rag_evidence_qa_sample_verify_report.md
```

## Verifier

The verifier checks output presence, query/answer counts, evidence back-cut, evidence hashes, sample scope, forbidden final tables, and source table row counts.

Success stdout must be exactly:

```text
L3.8 hybrid RAG evidence QA FULL PASS
```
