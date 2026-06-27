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
        return [[0.1, 0.2, 0.3, 0.4, 0.5] for _ in documents]


class L3ChromaVectorSyncTests(unittest.TestCase):
    def _prepare_project(self, root: Path) -> None:
        from scripts import l3_child_segment_builder as child_builder

        seed_child_segment_project(root)
        result = child_builder.run_build(root, sample_chapters="1,2,1697", rebuild=True)
        self.assertTrue(result.ok, result.errors)

    def test_sync_creates_collection_with_child_segment_ids_and_hash_metadata(self) -> None:
        from scripts import l3_chroma_vector_sync as sync

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._prepare_project(root)

            result = sync.run_sync(
                root,
                sample_chapters="1,2,1697",
                rebuild_collection=True,
                embedding_mode="test_fake_embedding",
            )

            self.assertTrue(result.ok, result.errors)
            self.assertEqual(result.collection_name, "novelrag_l3_child_segments_sample")
            self.assertEqual(result.embedding_mode, "test_fake_embedding")
            self.assertGreater(result.sqlite_child_segment_count, 0)
            self.assertEqual(result.sqlite_child_segment_count, result.chroma_document_count)

            client = chromadb.PersistentClient(path=str(root / "index" / "chroma"))
            try:
                collection = client.get_collection("novelrag_l3_child_segments_sample")
                payload = collection.get(include=["metadatas", "documents", "embeddings"])
                self.assertEqual(sorted(payload["ids"]), payload["ids"])
                self.assertEqual(len(payload["ids"]), result.sqlite_child_segment_count)
                self.assertEqual(payload["ids"][0], payload["metadatas"][0]["child_segment_id"])
                self.assertEqual(payload["metadatas"][0]["embedding_mode"], "test_fake_embedding")
                self.assertEqual(len(payload["embeddings"][0]), sync.TEST_FAKE_EMBEDDING_DIM)
            finally:
                sync.close_client(client)

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            conn.row_factory = sqlite3.Row
            try:
                row = conn.execute(
                    """
                    SELECT child_segment_id, segment_text, segment_text_hash, chapter_num, scene_id, version_id
                    FROM l3_child_segment
                    ORDER BY chapter_num, scene_id, segment_index_in_scene
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()
            self.assertEqual(payload["ids"][0], row["child_segment_id"])
            self.assertEqual(payload["documents"][0], row["segment_text"])
            self.assertEqual(payload["metadatas"][0]["segment_text_hash"], row["segment_text_hash"])
            self.assertEqual(payload["metadatas"][0]["chapter_num"], row["chapter_num"])
            self.assertEqual(payload["metadatas"][0]["scene_id"], row["scene_id"])
            self.assertEqual(payload["metadatas"][0]["version_id"], row["version_id"])

    def test_rebuild_collection_is_idempotent_for_sample_scope(self) -> None:
        from scripts import l3_chroma_vector_sync as sync

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._prepare_project(root)

            first = sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True, embedding_mode="test_fake_embedding")
            second = sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True, embedding_mode="test_fake_embedding")

            self.assertTrue(first.ok, first.errors)
            self.assertTrue(second.ok, second.errors)
            self.assertEqual(first.collection_fingerprint, second.collection_fingerprint)
            self.assertEqual(first.sqlite_child_segment_count, second.chroma_document_count)

    def test_reporter_outputs_exist_and_counts_match(self) -> None:
        from scripts import l3_chroma_vector_sync as sync
        from scripts import l3_chroma_vector_sync_reporter as reporter

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._prepare_project(root)
            self.assertTrue(
                sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True, embedding_mode="test_fake_embedding").ok
            )

            manifest = reporter.run_report(root)

            for name in (
                "l3_chroma_vector_sync_manifest.json",
                "l3_chroma_vector_sync_report.md",
            ):
                self.assertTrue((root / "outputs" / name).exists(), name)
            self.assertEqual(manifest["sqlite_child_segment_count"], manifest["chroma_document_count"])
            self.assertEqual(manifest["hash_mismatch_count"], 0)
            self.assertEqual(manifest["missing_in_chroma_count"], 0)
            self.assertEqual(manifest["extra_in_chroma_count"], 0)

    def test_verifier_pass_stdout_and_detects_source_mutation(self) -> None:
        from scripts import l3_chroma_vector_sync as sync
        from scripts import l3_verify_chroma_vector_sync as verifier

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._prepare_project(root)
            self.assertTrue(
                sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True, embedding_mode="test_fake_embedding").ok
            )

            result = subprocess.run(
                [
                    sys.executable,
                    str(PROJECT_ROOT / "scripts" / "l3_verify_chroma_vector_sync.py"),
                    "--project-dir",
                    str(root),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "L3.6 Chroma vector sync FULL PASS")

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute("UPDATE chapter_registry SET chapter_title_current = 'mutated' WHERE chapter_num = 1")
                conn.commit()
            finally:
                conn.close()
            mutated = verifier.run_verification(root)
            self.assertFalse(mutated.ok)
            self.assertTrue(any("source table mutation" in error for error in mutated.errors))

    def test_verifier_detects_child_segment_mutation_and_forbidden_final_table(self) -> None:
        from scripts import l3_chroma_vector_sync as sync
        from scripts import l3_verify_chroma_vector_sync as verifier

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._prepare_project(root)
            self.assertTrue(
                sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True, embedding_mode="test_fake_embedding").ok
            )

            conn = sqlite3.connect(root / "index" / "novel_story_bible.db")
            try:
                conn.execute("CREATE TABLE final_event (id TEXT)")
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

            result = verifier.run_verification(root)
            self.assertFalse(result.ok)
            self.assertIn("final_event", ",".join(result.forbidden_final_tables))
            self.assertTrue(any("l3_child_segment source rows mutated" in error for error in result.errors))

    def test_real_embedding_sync_uses_new_collection_and_preserves_fake_collection(self) -> None:
        from scripts import l3_chroma_vector_sync as sync
        from scripts import l3_chroma_vector_sync_reporter as reporter
        from scripts import l3_verify_chroma_vector_sync as verifier

        original_loader = sync.load_sentence_transformer_model
        sync.load_sentence_transformer_model = lambda embedding_model: FakeSentenceTransformer()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self._prepare_project(root)
                fake = sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True)
                self.assertTrue(fake.ok, fake.errors)
                fake_manifest = json.loads((root / "outputs" / "l3_chroma_vector_sync_manifest.json").read_text(encoding="utf-8"))
                fake_fingerprint = fake.collection_fingerprint

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
                self.assertEqual(real.collection_name, "novelrag_l3_child_segments_sample_bge_m3")
                self.assertEqual(real.embedding_dimension, 5)
                self.assertEqual(real.embedding_error_count, 0)
                self.assertFalse(real.fake_collection_mutation_detected)
                self.assertTrue((root / "outputs" / "l3_chroma_real_embedding_sync_manifest.json").exists())
                still_fake_manifest = json.loads((root / "outputs" / "l3_chroma_vector_sync_manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(still_fake_manifest["collection_name"], fake_manifest["collection_name"])
                self.assertEqual(still_fake_manifest["collection_fingerprint"], fake_fingerprint)

                client = chromadb.PersistentClient(path=str(root / "index" / "chroma"))
                try:
                    fake_collection = client.get_collection("novelrag_l3_child_segments_sample")
                    self.assertEqual(sync.collection_fingerprint(sync.fetch_collection_rows(fake_collection)), fake_fingerprint)
                    real_collection = client.get_collection("novelrag_l3_child_segments_sample_bge_m3")
                    payload = real_collection.get(include=["metadatas", "embeddings", "documents"])
                    self.assertEqual(len(payload["ids"]), real.sqlite_child_segment_count)
                    self.assertEqual(payload["metadatas"][0]["embedding_mode"], "real_embedding")
                    self.assertEqual(payload["metadatas"][0]["embedding_model"], "BAAI/bge-m3")
                    self.assertEqual(len(payload["embeddings"][0]), 5)
                finally:
                    sync.close_client(client)

                real_report = reporter.run_report(root, collection_name="novelrag_l3_child_segments_sample_bge_m3")
                self.assertEqual(real_report["embedding_dimension"], 5)
                verified = verifier.run_verification(
                    root,
                    collection_name="novelrag_l3_child_segments_sample_bge_m3",
                    expect_embedding_mode="real_embedding",
                    expect_embedding_model="BAAI/bge-m3",
                )
                self.assertTrue(verified.ok, verified.errors)
                self.assertEqual(verified.embedding_dimension, 5)
                self.assertTrue((root / "outputs" / "l3_chroma_real_embedding_sync_verify_report.json").exists())
        finally:
            sync.load_sentence_transformer_model = original_loader

    def test_real_embedding_dependency_error_is_clear(self) -> None:
        from scripts import l3_chroma_vector_sync as sync

        original_loader = sync.load_sentence_transformer_model
        sync.load_sentence_transformer_model = lambda embedding_model: (_ for _ in ()).throw(
            RuntimeError(f"model {embedding_model} not available")
        )
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self._prepare_project(root)

                result = sync.run_sync(
                    root,
                    sample_chapters="1,2,1697",
                    rebuild_collection=True,
                    collection_name="novelrag_l3_child_segments_sample_bge_m3",
                    embedding_provider="sentence-transformers",
                    embedding_model="BAAI/bge-m3",
                    embedding_mode="real_embedding",
                )

                self.assertFalse(result.ok)
                self.assertEqual(result.embedding_error_count, 1)
                self.assertTrue(any("model BAAI/bge-m3 not available" in error for error in result.errors))
        finally:
            sync.load_sentence_transformer_model = original_loader

    def test_real_verifier_detects_fake_collection_mutation(self) -> None:
        from scripts import l3_chroma_vector_sync as sync
        from scripts import l3_verify_chroma_vector_sync as verifier

        original_loader = sync.load_sentence_transformer_model
        sync.load_sentence_transformer_model = lambda embedding_model: FakeSentenceTransformer()
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                root = Path(temp_dir)
                self._prepare_project(root)
                self.assertTrue(sync.run_sync(root, sample_chapters="1,2,1697", rebuild_collection=True).ok)
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

                client = chromadb.PersistentClient(path=str(root / "index" / "chroma"))
                try:
                    collection = client.get_collection("novelrag_l3_child_segments_sample")
                    payload = collection.get(limit=1, include=["metadatas"])
                    metadata = dict(payload["metadatas"][0])
                    metadata["segment_text_hash"] = "bad"
                    collection.update(ids=[payload["ids"][0]], metadatas=[metadata])
                finally:
                    sync.close_client(client)

                result = verifier.run_verification(
                    root,
                    collection_name="novelrag_l3_child_segments_sample_bge_m3",
                    expect_embedding_mode="real_embedding",
                    expect_embedding_model="BAAI/bge-m3",
                )
                self.assertFalse(result.ok)
                self.assertTrue(result.fake_collection_mutation_detected)
        finally:
            sync.load_sentence_transformer_model = original_loader


if __name__ == "__main__":
    unittest.main()
