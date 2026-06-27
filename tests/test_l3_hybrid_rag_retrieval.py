import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import chromadb

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from tests.test_l3_child_segments import seed_child_segment_project


class FakeSentenceTransformer:
    def encode(self, documents, normalize_embeddings=True, show_progress_bar=False):
        vectors = []
        for index, _document in enumerate(documents, start=1):
            vectors.append([0.001 * ((position + index) % 17) for position in range(1024)])
        return vectors


def make_temp_project() -> Path:
    return Path(tempfile.mkdtemp(prefix="novelrag_l37_"))


def seed_title_index(root: Path) -> None:
    conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
    try:
        conn.execute(
            """
            CREATE TABLE l3_chapter_title_index (
                title_index_id INTEGER PRIMARY KEY,
                chapter_id TEXT NOT NULL,
                version_id TEXT NOT NULL,
                chapter_num INTEGER NOT NULL,
                title_raw TEXT NOT NULL,
                title_norm TEXT NOT NULL,
                title_keywords_json TEXT NOT NULL,
                title_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE l3_chapter_title_fts
                USING fts5(title_norm, title_keywords, chapter_num UNINDEXED)
                """
            )
        except sqlite3.DatabaseError:
            conn.execute(
                """
                CREATE TABLE l3_chapter_title_fts (
                    title_norm TEXT,
                    title_keywords TEXT,
                    chapter_num INTEGER
                )
                """
            )
        for index, (chapter_num, title, keywords) in enumerate(
            (
                (1, "Chapter 1 陈伶", ["Chapter 1", "陈伶"]),
                (2, "Chapter 2 韩蒙", ["Chapter 2", "韩蒙"]),
                (1697, "Chapter 1697 灾厄", ["Chapter 1697", "灾厄"]),
            ),
            start=1,
        ):
            chapter_id = f"ch_{chapter_num:04d}"
            version_id = f"ver_{chapter_num:04d}"
            conn.execute(
                """
                INSERT INTO l3_chapter_title_index (
                    title_index_id, chapter_id, version_id, chapter_num,
                    title_raw, title_norm, title_keywords_json, title_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    index,
                    chapter_id,
                    version_id,
                    chapter_num,
                    title,
                    title,
                    json.dumps(keywords, ensure_ascii=False),
                    str(index),
                ),
            )
            conn.execute(
                """
                INSERT INTO l3_chapter_title_fts (title_norm, title_keywords, chapter_num)
                VALUES (?, ?, ?)
                """,
                (title, " ".join(keywords), chapter_num),
            )
        conn.commit()
    finally:
        conn.close()


class L3HybridRagRetrievalTests(unittest.TestCase):
    def _prepare_project(self) -> Path:
        from scripts import l3_child_segment_builder as child_builder
        from scripts import l3_chroma_vector_sync as sync

        root = make_temp_project()
        seed_child_segment_project(root)
        seed_title_index(root)
        build_result = child_builder.run_build(root, sample_chapters="1,2,1697", rebuild=True)
        self.assertTrue(build_result.ok, build_result.errors)
        sync_result = sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True)
        self.assertTrue(sync_result.ok, sync_result.errors)
        return root

    def _prepare_real_project(self) -> Path:
        from scripts import l3_chroma_vector_sync as sync

        root = self._prepare_project()
        real = sync.run_sync(
            root,
            sample_chapters="1,2,1697",
            rebuild_collection=True,
            collection_name="novelrag_l3_child_segments_sample_bge_m3",
            embedding_provider="sentence-transformers",
            embedding_model="BAAI/bge-m3",
            embedding_mode="real_embedding",
        )
        self.assertTrue(real.ok, real.errors)
        return root

    def test_recall_routes_merge_evidence_and_reporter_outputs(self) -> None:
        from scripts import l3_hybrid_rag_retrieval_reporter as reporter
        from scripts import l3_hybrid_rag_retriever as retriever

        root = self._prepare_project()
        result = retriever.run_retrieval(root, query="Chapter 1", top_k=5, read_only=True)

        self.assertTrue(result.ok, result.errors)
        self.assertGreater(result.sqlite_title_hit_count, 0)
        self.assertGreater(result.sqlite_child_keyword_hit_count, 0)
        self.assertGreater(result.chroma_vector_hit_count, 0)
        self.assertGreater(result.merged_hit_count, 0)
        self.assertEqual(result.hash_mismatch_count, 0)
        self.assertEqual(result.missing_sqlite_child_count, 0)
        self.assertEqual(result.missing_chroma_child_count, 0)
        self.assertFalse(result.source_mutation_detected)
        self.assertFalse(result.child_segment_mutation_detected)
        self.assertFalse(result.chroma_mutation_detected)

        ids = [hit["child_segment_id"] for hit in result.merged_hits if hit["query_id"] == "q001"]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(any(len(hit["recall_sources"]) >= 2 for hit in result.merged_hits))
        top_hit = result.merged_hits[0]
        expected_score = round(
            0.35 * top_hit["title_score"] + 0.30 * top_hit["keyword_score"] + 0.35 * top_hit["vector_score"],
            6,
        )
        self.assertEqual(top_hit["hybrid_score"], expected_score)
        self.assertTrue(top_hit["hash_check_ok"])
        self.assertTrue(top_hit["sentence_link_backtrace_ok"])

        manifest = reporter.run_report(root)
        self.assertEqual(manifest["merged_hit_count"], result.merged_hit_count)
        for name in (
            "l3_hybrid_rag_retrieval_results.json",
            "l3_hybrid_rag_retrieval_results.csv",
            "l3_hybrid_rag_retrieval_report.md",
            "l3_hybrid_rag_retrieval_manifest.json",
        ):
            self.assertTrue((root / "outputs" / name).exists(), name)

    def test_zero_hit_query_handling(self) -> None:
        from scripts import l3_hybrid_rag_retriever as retriever

        root = self._prepare_project()
        result = retriever.run_retrieval(root, query=" ", top_k=5, read_only=True)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.query_count, 1)
        self.assertEqual(result.merged_hit_count, 0)
        self.assertEqual(result.zero_hit_query_count, 1)
        self.assertTrue(any("zero_hit query" in warning for warning in result.warnings))

    def test_verifier_pass_stdout_and_reports(self) -> None:
        from scripts import l3_hybrid_rag_retrieval_reporter as reporter
        from scripts import l3_hybrid_rag_retriever as retriever

        root = self._prepare_project()
        retrieval = retriever.run_retrieval(root, query="Chapter 1", top_k=5, read_only=True)
        self.assertTrue(retrieval.ok, retrieval.errors)
        reporter.run_report(root)

        result = subprocess.run(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts" / "l3_verify_hybrid_rag_retrieval.py"),
                "--project-dir",
                str(root),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "L3.7 Hybrid RAG retrieval FULL PASS")
        self.assertTrue((root / "outputs" / "l3_hybrid_rag_retrieval_verify_report.json").exists())
        self.assertTrue((root / "outputs" / "l3_hybrid_rag_retrieval_verify_report.md").exists())

    def test_verifier_detects_mutations_and_forbidden_final_table(self) -> None:
        from scripts import l3_hybrid_rag_retriever as retriever
        from scripts import l3_verify_hybrid_rag_retrieval as verifier

        source_root = self._prepare_project()
        self.assertTrue(retriever.run_retrieval(source_root, query="Chapter 1", top_k=5, read_only=True).ok)
        conn = sqlite3.connect(source_root / "index" / "novel_story_bible.db")
        try:
            conn.execute("UPDATE chapter_registry SET chapter_title_current = 'mutated' WHERE chapter_num = 1")
            conn.commit()
        finally:
            conn.close()
        source_check = verifier.run_verification(source_root)
        self.assertFalse(source_check.ok)
        self.assertTrue(source_check.source_mutation_detected)

        child_root = self._prepare_project()
        self.assertTrue(retriever.run_retrieval(child_root, query="Chapter 1", top_k=5, read_only=True).ok)
        conn = sqlite3.connect(child_root / "index" / "novel_story_bible.db")
        try:
            conn.execute(
                """
                UPDATE l3_child_segment
                SET segment_text_hash = 'bad'
                WHERE child_segment_id = (
                    SELECT child_segment_id
                    FROM l3_child_segment
                    ORDER BY child_segment_id
                    LIMIT 1
                )
                """
            )
            conn.commit()
        finally:
            conn.close()
        child_check = verifier.run_verification(child_root)
        self.assertFalse(child_check.ok)
        self.assertTrue(child_check.child_segment_mutation_detected)

        chroma_root = self._prepare_project()
        self.assertTrue(retriever.run_retrieval(chroma_root, query="Chapter 1", top_k=5, read_only=True).ok)
        client = chromadb.PersistentClient(path=str(chroma_root / "index" / "chroma"))
        try:
            collection = client.get_collection("novelrag_l3_child_segments_sample")
            payload = collection.get(limit=1, include=["metadatas"])
            metadata = dict(payload["metadatas"][0])
            metadata["segment_text_hash"] = "bad"
            collection.update(ids=[payload["ids"][0]], metadatas=[metadata])
        finally:
            try:
                client.close()
            except Exception:
                pass
        chroma_check = verifier.run_verification(chroma_root)
        self.assertFalse(chroma_check.ok)
        self.assertTrue(chroma_check.chroma_mutation_detected)

        forbidden_root = self._prepare_project()
        self.assertTrue(retriever.run_retrieval(forbidden_root, query="Chapter 1", top_k=5, read_only=True).ok)
        conn = sqlite3.connect(forbidden_root / "index" / "novel_story_bible.db")
        try:
            conn.execute("CREATE TABLE final_event (id TEXT)")
            conn.commit()
        finally:
            conn.close()
        forbidden_check = verifier.run_verification(forbidden_root)
        self.assertFalse(forbidden_check.ok)
        self.assertIn("final_event", forbidden_check.forbidden_final_tables)

    def test_real_collection_retrieval_quality_outputs_and_verifier_pass(self) -> None:
        from scripts import l3_chroma_vector_sync as sync
        from scripts import l3_hybrid_rag_retrieval_reporter as reporter
        from scripts import l3_hybrid_rag_retriever as retriever
        from scripts import l3_verify_hybrid_rag_retrieval as verifier

        original_loader = sync.load_sentence_transformer_model
        sync.load_sentence_transformer_model = lambda embedding_model: FakeSentenceTransformer()
        try:
            root = self._prepare_real_project()
            result = retriever.run_retrieval(
                root,
                sample_chapters="1,2,1697",
                collection_name="novelrag_l3_child_segments_sample_bge_m3",
                expect_embedding_mode="real_embedding",
                expect_embedding_model="BAAI/bge-m3",
                read_only=True,
                real_quality_report=True,
                output_prefix="l3_hybrid_rag_real_embedding_retrieval",
            )

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(result.query_count, 10)
            self.assertEqual(result.embedding_mode, "real_embedding")
            self.assertEqual(result.embedding_model, "BAAI/bge-m3")
            self.assertEqual(result.embedding_dimension, 1024)
            self.assertEqual(len(result.quality_summary), 10)
            self.assertFalse(result.fake_collection_mutation_detected)
            self.assertFalse(result.real_collection_mutation_detected)
            self.assertTrue(all("quality_label" in item for item in result.quality_summary))
            self.assertTrue(all("vector_top5_child_segment_ids" in item for item in result.quality_summary))
            self.assertGreaterEqual(result.keyword_vector_overlap_total, 0)

            manifest = reporter.run_report(root, output_prefix="l3_hybrid_rag_real_embedding_retrieval")
            self.assertEqual(manifest["query_count"], 10)
            for name in (
                "l3_hybrid_rag_real_embedding_retrieval_results.json",
                "l3_hybrid_rag_real_embedding_retrieval_results.csv",
                "l3_hybrid_rag_real_embedding_retrieval_report.md",
                "l3_hybrid_rag_real_embedding_retrieval_manifest.json",
            ):
                self.assertTrue((root / "outputs" / name).exists(), name)

            verified = verifier.run_verification(
                root,
                collection_name="novelrag_l3_child_segments_sample_bge_m3",
                expect_embedding_mode="real_embedding",
                expect_embedding_model="BAAI/bge-m3",
                expect_output_prefix="l3_hybrid_rag_real_embedding_retrieval",
            )
            self.assertTrue(verified.ok, verified.errors)
            self.assertEqual(verified.embedding_dimension, 1024)
            self.assertTrue((root / "outputs" / "l3_hybrid_rag_real_embedding_retrieval_verify_report.json").exists())
        finally:
            sync.load_sentence_transformer_model = original_loader

    def test_real_verifier_detects_source_fake_real_and_forbidden_mutations(self) -> None:
        from scripts import l3_chroma_vector_sync as sync
        from scripts import l3_hybrid_rag_retriever as retriever
        from scripts import l3_verify_hybrid_rag_retrieval as verifier

        original_loader = sync.load_sentence_transformer_model
        sync.load_sentence_transformer_model = lambda embedding_model: FakeSentenceTransformer()
        try:
            root = self._prepare_real_project()
            result = retriever.run_retrieval(
                root,
                collection_name="novelrag_l3_child_segments_sample_bge_m3",
                expect_embedding_mode="real_embedding",
                expect_embedding_model="BAAI/bge-m3",
                read_only=True,
                real_quality_report=True,
                output_prefix="l3_hybrid_rag_real_embedding_retrieval",
            )
            self.assertTrue(result.ok, result.errors)
            manifest_path = root / "outputs" / "l3_hybrid_rag_real_embedding_retrieval_manifest.json"
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
            data["embedding_dimension"] = 1024
            manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute("UPDATE chapter_registry SET chapter_title_current = 'mutated' WHERE chapter_num = 1")
                conn.execute("CREATE TABLE final_event (id TEXT)")
                conn.commit()
            finally:
                conn.close()

            client = chromadb.PersistentClient(path=str(root / "index" / "chroma"))
            try:
                fake = client.get_collection("novelrag_l3_child_segments_sample")
                fake_payload = fake.get(limit=1, include=["metadatas"])
                fake_meta = dict(fake_payload["metadatas"][0])
                fake_meta["segment_text_hash"] = "bad"
                fake.update(ids=[fake_payload["ids"][0]], metadatas=[fake_meta])
                real = client.get_collection("novelrag_l3_child_segments_sample_bge_m3")
                real_payload = real.get(limit=1, include=["metadatas"])
                real_meta = dict(real_payload["metadatas"][0])
                real_meta["segment_text_hash"] = "bad"
                real.update(ids=[real_payload["ids"][0]], metadatas=[real_meta])
            finally:
                sync.close_client(client)

            verified = verifier.run_verification(
                root,
                collection_name="novelrag_l3_child_segments_sample_bge_m3",
                expect_embedding_mode="real_embedding",
                expect_embedding_model="BAAI/bge-m3",
                expect_output_prefix="l3_hybrid_rag_real_embedding_retrieval",
            )
            self.assertFalse(verified.ok)
            self.assertTrue(verified.source_mutation_detected)
            self.assertTrue(verified.fake_collection_mutation_detected)
            self.assertTrue(verified.real_collection_mutation_detected)
            self.assertIn("final_event", verified.forbidden_final_tables)
        finally:
            sync.load_sentence_transformer_model = original_loader


if __name__ == "__main__":
    unittest.main()
