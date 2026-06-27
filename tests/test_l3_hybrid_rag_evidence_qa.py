import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_l3_child_segments import seed_child_segment_project


def make_temp_project() -> Path:
    return Path(tempfile.mkdtemp(prefix="novelrag_l38_"))


def prepare_project() -> Path:
    from scripts import l3_child_segment_builder as child_builder

    root = make_temp_project()
    seed_child_segment_project(root)
    result = child_builder.run_build(root, sample_chapters="1,2,1697", rebuild=True)
    assert result.ok, result.errors
    return root


def write_retrieval_outputs(root: Path, *, empty_query: bool = False) -> list[str]:
    conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT child_segment_id, scene_id, chapter_id, chapter_num, segment_text, segment_text_hash, source_fingerprint,
                   start_para_id, end_para_id, start_sentence_id, end_sentence_id
            FROM l3_child_segment
            WHERE chapter_num IN (1,2,1697)
            ORDER BY chapter_num, scene_id, segment_index_in_scene, child_segment_id
            LIMIT 4
            """
        ).fetchall()
    finally:
        conn.close()
    ids = [str(row["child_segment_id"]) for row in rows]
    queries = [{"query_id": "q001", "query_text": "Chapter 1"}, {"query_id": "q002", "query_text": "missing"}]
    merged_hits = [
        {
            "query_id": "q001",
            "query_text": "Chapter 1",
            "rank": 1,
            "child_segment_id": ids[0],
            "scene_id": str(rows[0]["scene_id"]),
            "chapter_id": rows[0]["chapter_id"],
            "chapter_num": int(rows[0]["chapter_num"]),
            "hybrid_score": 0.9,
            "recall_sources": ["sqlite_child_keyword", "chroma_vector"],
            "segment_text_hash": rows[0]["segment_text_hash"],
            "source_fingerprint": rows[0]["source_fingerprint"],
        },
        {
            "query_id": "q001",
            "query_text": "Chapter 1",
            "rank": 2,
            "child_segment_id": ids[0],
            "scene_id": str(rows[0]["scene_id"]),
            "chapter_id": rows[0]["chapter_id"],
            "chapter_num": int(rows[0]["chapter_num"]),
            "hybrid_score": 0.8,
            "recall_sources": ["chroma_vector"],
            "segment_text_hash": rows[0]["segment_text_hash"],
            "source_fingerprint": rows[0]["source_fingerprint"],
        },
        {
            "query_id": "q001",
            "query_text": "Chapter 1",
            "rank": 3,
            "child_segment_id": ids[1],
            "scene_id": str(rows[1]["scene_id"]),
            "chapter_id": rows[1]["chapter_id"],
            "chapter_num": int(rows[1]["chapter_num"]),
            "hybrid_score": 0.7,
            "recall_sources": ["chroma_vector"],
            "segment_text_hash": rows[1]["segment_text_hash"],
            "source_fingerprint": rows[1]["source_fingerprint"],
        },
    ]
    if empty_query:
        merged_hits = [hit for hit in merged_hits if hit["query_id"] != "q002"]
    payload = {
        "retrieval_run_id": "test_retrieval",
        "collection_name": "novelrag_l3_child_segments_sample_bge_m3",
        "embedding_mode": "real_embedding",
        "queries": queries,
        "recall": {"sqlite_title": [], "sqlite_child_keyword": [], "chroma_vector": []},
        "merged_hits": merged_hits,
        "quality_summary": [],
        "errors": [],
        "warnings": [],
    }
    manifest = {
        "retrieval_run_id": "test_retrieval",
        "collection_name": "novelrag_l3_child_segments_sample_bge_m3",
        "embedding_mode": "real_embedding",
        "chapter_scope": [1, 2, 1697],
        "query_count": len(queries),
        "error_count": 0,
        "warning_count": 0,
    }
    outputs = root / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    (outputs / "l3_hybrid_rag_real_embedding_retrieval_results.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (outputs / "l3_hybrid_rag_real_embedding_retrieval_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return ids


class L3HybridRagEvidenceQaTests(unittest.TestCase):
    def test_reads_retrieval_results_generates_answer_rows_and_dedups_evidence(self) -> None:
        from scripts import l3_hybrid_rag_evidence_qa as qa

        root = prepare_project()
        write_retrieval_outputs(root)
        result = qa.run_evidence_qa(root, read_only=True, max_evidence_per_query=5)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.query_count, 2)
        self.assertEqual(result.answer_count, 2)
        self.assertEqual(result.answers[0]["evidence_count"], 2)
        self.assertEqual(len({item["child_segment_id"] for item in result.answers[0]["evidence"]}), 2)
        self.assertEqual(result.answers[0]["answer_status"], "grounded_draft")
        self.assertIn("evidence_refs:", result.answers[0]["grounded_answer"])
        self.assertEqual(result.answers[1]["answer_status"], "insufficient_evidence")

    def test_zero_evidence_query_is_insufficient_evidence_and_manifest_complete(self) -> None:
        from scripts import l3_hybrid_rag_evidence_qa as qa

        root = prepare_project()
        write_retrieval_outputs(root, empty_query=True)
        result = qa.run_evidence_qa(root, read_only=True, min_evidence_per_answer=1)

        self.assertTrue(result.ok, result.errors)
        zero_answer = next(item for item in result.answers if item["query_id"] == "q002")
        self.assertEqual(zero_answer["evidence_count"], 0)
        self.assertEqual(zero_answer["answer_status"], "insufficient_evidence")
        manifest = json.loads((root / "outputs" / "l3_hybrid_rag_evidence_qa_sample_manifest.json").read_text(encoding="utf-8"))
        for key in (
            "layer",
            "input_prefix",
            "output_prefix",
            "query_count",
            "answer_count",
            "total_evidence_count",
            "hash_mismatch_count",
            "missing_sqlite_child_count",
            "source_table_mutation_count",
            "forbidden_final_table_count",
        ):
            self.assertIn(key, manifest)

    def test_read_only_sql_guard_rejects_writes_and_scope_is_checked(self) -> None:
        from scripts import l3_hybrid_rag_evidence_qa as qa

        with self.assertRaisesRegex(RuntimeError, "read_only SQL guard"):
            qa.reject_write_sql("INSERT INTO x VALUES (1)")
        root = prepare_project()
        ids = write_retrieval_outputs(root)
        result = qa.run_evidence_qa(root, read_only=True)
        self.assertTrue(result.ok, result.errors)
        for answer in result.answers:
            for evidence in answer["evidence"]:
                self.assertIn(evidence["chapter_num"], {1, 2, 1697})
                self.assertIn(evidence["child_segment_id"], ids)

    def test_reporter_outputs_and_verifier_pass_stdout(self) -> None:
        from scripts import l3_hybrid_rag_evidence_qa as qa
        from scripts import l3_hybrid_rag_evidence_qa_reporter as reporter

        root = prepare_project()
        write_retrieval_outputs(root)
        result = qa.run_evidence_qa(root, read_only=True)
        self.assertTrue(result.ok, result.errors)
        manifest = reporter.run_report(root, output_prefix="l3_hybrid_rag_evidence_qa_sample")
        self.assertEqual(manifest["answer_count"], 2)

        proc = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "l3_verify_hybrid_rag_evidence_qa.py"),
                "--project-dir",
                str(root),
                "--input-prefix",
                "l3_hybrid_rag_real_embedding_retrieval",
                "--expect-output-prefix",
                "l3_hybrid_rag_evidence_qa_sample",
                "--sample-chapters",
                "1,2,1697",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.strip(), "L3.8 hybrid RAG evidence QA FULL PASS")

    def test_verifier_detects_forbidden_final_table(self) -> None:
        from scripts import l3_hybrid_rag_evidence_qa as qa
        from scripts import l3_verify_hybrid_rag_evidence_qa as verifier

        root = prepare_project()
        write_retrieval_outputs(root)
        self.assertTrue(qa.run_evidence_qa(root, read_only=True).ok)
        conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
        try:
            conn.execute("CREATE TABLE final_event (id TEXT)")
            conn.commit()
        finally:
            conn.close()
        result = verifier.run_verification(root)
        self.assertFalse(result.ok)
        self.assertGreater(result.forbidden_final_table_count, 0)


if __name__ == "__main__":
    unittest.main()
