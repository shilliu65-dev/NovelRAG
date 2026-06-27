from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import chromadb

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as child_builder


DEFAULT_CHROMA_DIR = Path("index") / "chroma"
DEFAULT_COLLECTION_NAME = "novelrag_l3_child_segments_sample"
DEFAULT_REAL_COLLECTION_NAME = "novelrag_l3_child_segments_sample_bge_m3"
DEFAULT_FULL_COLLECTION_NAME = "novelrag_l3_child_segments_full"
DEFAULT_EMBEDDING_PROVIDER = "test"
DEFAULT_EMBEDDING_MODEL = "deterministic-hash"
DEFAULT_EMBEDDING_MODE = "test_fake_embedding"
REAL_EMBEDDING_PROVIDER = "sentence-transformers"
REAL_EMBEDDING_MODEL = "BAAI/bge-m3"
REAL_EMBEDDING_MODE = "real_embedding"
TEST_FAKE_EMBEDDING_DIM = 16
MANIFEST_RELATIVE_PATH = Path("outputs") / "l3_chroma_vector_sync_manifest.json"
REPORT_RELATIVE_PATH = Path("outputs") / "l3_chroma_vector_sync_report.md"
REAL_MANIFEST_RELATIVE_PATH = Path("outputs") / "l3_chroma_real_embedding_sync_manifest.json"
REAL_REPORT_RELATIVE_PATH = Path("outputs") / "l3_chroma_real_embedding_sync_report.md"
REQUIRED_METADATA_KEYS = [
    "child_segment_id",
    "scene_id",
    "chapter_id",
    "version_id",
    "chapter_num",
    "segment_index_in_scene",
    "segment_kind",
    "char_len",
    "sentence_count",
    "paragraph_count",
    "start_para_id",
    "end_para_id",
    "start_sentence_id",
    "end_sentence_id",
    "start_char_offset",
    "end_char_offset",
    "has_overlap",
    "segment_text_hash",
    "source_fingerprint",
    "build_run_id",
]


@dataclass
class SyncResult:
    ok: bool
    project_dir: Path
    db_path: Path
    chroma_dir: Path
    collection_name: str
    scope: list[int]
    rebuild_collection: bool
    embedding_provider: str
    embedding_model: str
    embedding_mode: str
    sync_run_id: str = ""
    sqlite_child_segment_count: int = 0
    chroma_document_count: int = 0
    synced_document_count: int = 0
    skipped_document_count: int = 0
    hash_mismatch_count: int = 0
    missing_in_chroma_count: int = 0
    extra_in_chroma_count: int = 0
    embedding_dimension: int = 0
    embedding_error_count: int = 0
    source_mutation_detected: bool = False
    child_segment_mutation_detected: bool = False
    fake_collection_mutation_detected: bool = False
    forbidden_final_table_count: int = 0
    collection_fingerprint: str = ""
    fake_collection_fingerprint_before: str = ""
    fake_collection_fingerprint_after: str = ""
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def resolve_chroma_dir(project_dir: Path, chroma_dir: Path | str | None) -> Path:
    if chroma_dir is None:
        return (project_dir / DEFAULT_CHROMA_DIR).resolve()
    path = Path(chroma_dir)
    if path.is_absolute():
        return path.resolve()
    return (project_dir / path).resolve()


def deterministic_fake_embedding(text: str, *, dim: int = TEST_FAKE_EMBEDDING_DIM) -> list[float]:
    values: list[float] = []
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(f"{counter}:{text}".encode("utf-8")).digest()
        for index in range(0, len(digest), 4):
            chunk = digest[index : index + 4]
            if len(chunk) < 4:
                continue
            raw = int.from_bytes(chunk, "big", signed=False)
            values.append(round((raw / 4294967295.0) * 2.0 - 1.0, 6))
            if len(values) == dim:
                break
        counter += 1
    norm = math.sqrt(sum(value * value for value in values)) or 1.0
    return [round(value / norm, 6) for value in values]


def embedding_for_text(text: str, *, embedding_mode: str) -> list[float]:
    if embedding_mode != DEFAULT_EMBEDDING_MODE:
        raise ValueError(f"unsupported embedding_mode: {embedding_mode}")
    return deterministic_fake_embedding(text)


def output_paths_for_mode(embedding_mode: str, collection_name: str) -> tuple[Path, Path]:
    if embedding_mode == REAL_EMBEDDING_MODE or collection_name == DEFAULT_REAL_COLLECTION_NAME:
        return REAL_MANIFEST_RELATIVE_PATH, REAL_REPORT_RELATIVE_PATH
    return MANIFEST_RELATIVE_PATH, REPORT_RELATIVE_PATH


def verify_embedding_configuration(*, embedding_provider: str, embedding_model: str, embedding_mode: str) -> None:
    if embedding_mode == DEFAULT_EMBEDDING_MODE:
        if embedding_provider != DEFAULT_EMBEDDING_PROVIDER:
            raise ValueError("test_fake_embedding requires --embedding-provider test")
        if embedding_model != DEFAULT_EMBEDDING_MODEL:
            raise ValueError("test_fake_embedding requires --embedding-model deterministic-hash")
        return
    if embedding_mode == REAL_EMBEDDING_MODE:
        if embedding_provider != REAL_EMBEDDING_PROVIDER:
            raise ValueError("real_embedding requires --embedding-provider sentence-transformers")
        if not embedding_model:
            raise ValueError("real_embedding requires --embedding-model")
        return
    raise ValueError(f"unsupported embedding_mode: {embedding_mode}")


def load_sentence_transformer_model(embedding_model: str) -> Any:
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:
        raise RuntimeError("sentence_transformers not installed") from exc
    try:
        return SentenceTransformer(embedding_model)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"model {embedding_model} not available: {exc}") from exc


def embeddings_for_documents(
    documents: list[str],
    *,
    embedding_provider: str,
    embedding_model: str,
    embedding_mode: str,
) -> tuple[list[list[float]], int]:
    verify_embedding_configuration(
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_mode=embedding_mode,
    )
    if embedding_mode == DEFAULT_EMBEDDING_MODE:
        embeddings = [embedding_for_text(document, embedding_mode=embedding_mode) for document in documents]
        return embeddings, TEST_FAKE_EMBEDDING_DIM
    model = load_sentence_transformer_model(embedding_model)
    try:
        encoded = model.encode(documents, normalize_embeddings=True, show_progress_bar=False)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"embedding generation failed for model {embedding_model}: {exc}") from exc
    embeddings: list[list[float]] = []
    for vector in encoded:
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        embeddings.append([float(value) for value in vector])
    dimension = len(embeddings[0]) if embeddings else 0
    if dimension <= 0:
        raise RuntimeError(f"embedding generation failed for model {embedding_model}: empty embedding")
    return embeddings, dimension


def fetch_child_segments(conn: sqlite3.Connection, scope: list[int]) -> list[sqlite3.Row]:
    placeholders = child_builder.scoped_placeholders(scope)
    return conn.execute(
        f"""
        SELECT *
        FROM l3_child_segment
        WHERE chapter_num IN ({placeholders})
        ORDER BY chapter_num, scene_id, segment_index_in_scene, child_segment_id
        """,
        tuple(scope),
    ).fetchall()


def collection_metadata_for_segment(row: sqlite3.Row, *, embedding_provider: str, embedding_model: str, embedding_mode: str) -> dict[str, Any]:
    metadata = {key: row[key] for key in REQUIRED_METADATA_KEYS}
    metadata["has_overlap"] = int(metadata["has_overlap"])
    metadata["chapter_num"] = int(metadata["chapter_num"])
    metadata["segment_index_in_scene"] = int(metadata["segment_index_in_scene"])
    metadata["char_len"] = int(metadata["char_len"])
    metadata["sentence_count"] = int(metadata["sentence_count"])
    metadata["paragraph_count"] = int(metadata["paragraph_count"])
    metadata["start_char_offset"] = int(metadata["start_char_offset"])
    metadata["end_char_offset"] = int(metadata["end_char_offset"])
    metadata["embedding_provider"] = embedding_provider
    metadata["embedding_model"] = embedding_model
    metadata["embedding_mode"] = embedding_mode
    return metadata


def get_client(chroma_dir: Path) -> chromadb.ClientAPI:
    chroma_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(chroma_dir))


def get_collection(client: chromadb.ClientAPI, collection_name: str, *, rebuild_collection: bool, embedding_mode: str = DEFAULT_EMBEDDING_MODE):
    if rebuild_collection:
        try:
            client.delete_collection(collection_name)
        except Exception:  # noqa: BLE001
            pass
    return client.get_or_create_collection(
        name=collection_name,
        metadata={"sync_layer": "l3.6b" if embedding_mode == REAL_EMBEDDING_MODE else "l3.6", "collection_scope": "sample"},
    )


def fetch_collection_rows(collection: Any) -> dict[str, dict[str, Any]]:
    payload = collection.get(include=["documents", "metadatas", "embeddings"])
    result: dict[str, dict[str, Any]] = {}
    for index, doc_id in enumerate(payload.get("ids", [])):
        embedding = payload["embeddings"][index]
        if hasattr(embedding, "tolist"):
            embedding = embedding.tolist()
        result[str(doc_id)] = {
            "document": payload["documents"][index],
            "metadata": payload["metadatas"][index],
            "embedding": embedding,
        }
    return result


def close_client(client: chromadb.ClientAPI) -> None:
    try:
        client.close()
    except Exception:  # noqa: BLE001
        pass


def collection_fingerprint(rows: dict[str, dict[str, Any]]) -> str:
    ordered = []
    for doc_id in sorted(rows):
        entry = rows[doc_id]
        ordered.append(
            {
                "id": doc_id,
                "document": entry["document"],
                "metadata": entry["metadata"],
                "embedding": entry["embedding"],
            }
        )
    return sha256_json(ordered)


def collection_names(client: chromadb.ClientAPI) -> list[str]:
    names = []
    for collection in client.list_collections():
        names.append(collection.name if hasattr(collection, "name") else str(collection))
    return names


def existing_collection_fingerprint(client: chromadb.ClientAPI, collection_name: str) -> str:
    if collection_name not in collection_names(client):
        return ""
    collection = client.get_collection(collection_name)
    return collection_fingerprint(fetch_collection_rows(collection))


def compute_diff_metrics(
    sqlite_rows: list[sqlite3.Row],
    chroma_rows: dict[str, dict[str, Any]],
    *,
    embedding_provider: str,
    embedding_model: str,
    embedding_mode: str,
) -> dict[str, int]:
    sqlite_by_id = {str(row["child_segment_id"]): row for row in sqlite_rows}
    hash_mismatch_count = 0
    missing_in_chroma_count = 0
    for doc_id, row in sqlite_by_id.items():
        stored = chroma_rows.get(doc_id)
        if stored is None:
            missing_in_chroma_count += 1
            continue
        expected_metadata = collection_metadata_for_segment(
            row,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_mode=embedding_mode,
        )
        if stored["document"] != row["segment_text"] or stored["metadata"] != expected_metadata:
            hash_mismatch_count += 1
    extra_in_chroma_count = len(set(chroma_rows) - set(sqlite_by_id))
    return {
        "hash_mismatch_count": hash_mismatch_count,
        "missing_in_chroma_count": missing_in_chroma_count,
        "extra_in_chroma_count": extra_in_chroma_count,
    }


def build_sync_run_id(
    *,
    scope: list[int],
    collection_name: str,
    embedding_provider: str,
    embedding_model: str,
    embedding_mode: str,
    child_segment_fingerprint: str,
) -> str:
    return "l3cvs_" + sha256_json(
        {
            "scope": scope,
            "collection_name": collection_name,
            "embedding_provider": embedding_provider,
            "embedding_model": embedding_model,
            "embedding_mode": embedding_mode,
            "child_segment_fingerprint": child_segment_fingerprint,
        }
    )[:24]


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# L3.6b real embedding Chroma sync report" if payload["embedding_mode"] == REAL_EMBEDDING_MODE else "# L3.6 Chroma vector sync report",
        "",
        f"- created_at: {payload['created_at']}",
        f"- collection_name: {payload['collection_name']}",
        f"- embedding_provider: {payload['embedding_provider']}",
        f"- embedding_model: {payload['embedding_model']}",
        f"- embedding_mode: {payload['embedding_mode']}",
        f"- embedding_dimension: {payload.get('embedding_dimension', 0)}",
        f"- embedding_error_count: {payload.get('embedding_error_count', 0)}",
        f"- chapter_scope: {','.join(str(item) for item in payload['chapter_scope'])}",
        f"- sqlite_child_segment_count: {payload['sqlite_child_segment_count']}",
        f"- chroma_document_count: {payload['chroma_document_count']}",
        f"- synced_document_count: {payload['synced_document_count']}",
        f"- skipped_document_count: {payload['skipped_document_count']}",
        f"- hash_mismatch_count: {payload['hash_mismatch_count']}",
        f"- missing_in_chroma_count: {payload['missing_in_chroma_count']}",
        f"- extra_in_chroma_count: {payload['extra_in_chroma_count']}",
        f"- source_mutation_detected: {payload['source_mutation_detected']}",
        f"- child_segment_mutation_detected: {payload['child_segment_mutation_detected']}",
        f"- fake_collection_mutation_detected: {payload.get('fake_collection_mutation_detected', False)}",
        f"- forbidden_final_table_count: {payload['forbidden_final_table_count']}",
        f"- no_llm_generation: YES",
        f"- re_chunking: NO",
        "",
        "L3.6b real embedding Chroma sync report generated." if payload["embedding_mode"] == REAL_EMBEDDING_MODE else "L3.6 Chroma vector sync report generated.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_sync(
    project_dir: Path | str,
    *,
    sample_chapters: str | None = None,
    chapter_num: int | None = None,
    rebuild_collection: bool = False,
    collection_name: str = DEFAULT_COLLECTION_NAME,
    embedding_provider: str = DEFAULT_EMBEDDING_PROVIDER,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    embedding_mode: str = DEFAULT_EMBEDDING_MODE,
    chroma_dir: Path | str | None = None,
) -> SyncResult:
    root = Path(project_dir).resolve()
    child_builder.ensure_dirs(root)
    db_path = root / child_builder.DB_RELATIVE_PATH
    resolved_chroma_dir = resolve_chroma_dir(root, chroma_dir)
    scope: list[int] = []
    result = SyncResult(
        False,
        root,
        db_path,
        resolved_chroma_dir,
        collection_name,
        scope,
        rebuild_collection,
        embedding_provider,
        embedding_model,
        embedding_mode,
    )
    try:
        scope = child_builder.parse_scope(sample_chapters, chapter_num)
        result.scope = scope
        verify_embedding_configuration(
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_mode=embedding_mode,
        )
        if embedding_mode == REAL_EMBEDDING_MODE and collection_name == DEFAULT_COLLECTION_NAME:
            raise ValueError("real_embedding must not write to fake collection novelrag_l3_child_segments_sample")
        if not db_path.exists():
            raise RuntimeError("SQLite database does not exist")
        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            child_builder.validate_source_objects(conn)
            if not child_builder.object_exists(conn, "l3_child_segment", "table"):
                raise RuntimeError("l3_child_segment table is missing; run L3.5 first")
            source_table_names = child_builder.discover_source_table_names(conn)
            source_guard_before = child_builder.collect_source_guard_stats(conn, source_table_names)
            source_guard_before_hash = child_builder.guard_hash(source_guard_before)
            child_segment_fingerprint_before = child_builder.stable_row_fingerprint(conn, scope)
            sqlite_rows = fetch_child_segments(conn, scope)
            if not sqlite_rows:
                raise RuntimeError("sample chapters have no l3_child_segment rows")
            result.sqlite_child_segment_count = len(sqlite_rows)
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
            if result.forbidden_final_table_count:
                result.errors.append("forbidden final tables exist before sync")
        finally:
            conn.close()

        result.sync_run_id = build_sync_run_id(
            scope=scope,
            collection_name=collection_name,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_mode=embedding_mode,
            child_segment_fingerprint=child_segment_fingerprint_before,
        )

        client = get_client(resolved_chroma_dir)
        try:
            fake_collection_fingerprint_before = existing_collection_fingerprint(client, DEFAULT_COLLECTION_NAME)
            result.fake_collection_fingerprint_before = fake_collection_fingerprint_before
            collection = get_collection(
                client,
                collection_name,
                rebuild_collection=rebuild_collection,
                embedding_mode=embedding_mode,
            )
            ids = [str(row["child_segment_id"]) for row in sqlite_rows]
            documents = [str(row["segment_text"]) for row in sqlite_rows]
            metadatas = [
                collection_metadata_for_segment(
                    row,
                    embedding_provider=embedding_provider,
                    embedding_model=embedding_model,
                    embedding_mode=embedding_mode,
                )
                for row in sqlite_rows
            ]
            try:
                embeddings, embedding_dimension = embeddings_for_documents(
                    documents,
                    embedding_provider=embedding_provider,
                    embedding_model=embedding_model,
                    embedding_mode=embedding_mode,
                )
                result.embedding_dimension = embedding_dimension
            except Exception:
                result.embedding_error_count += 1
                raise
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

            chroma_rows = fetch_collection_rows(collection)
            diff_metrics = compute_diff_metrics(
                sqlite_rows,
                chroma_rows,
                embedding_provider=embedding_provider,
                embedding_model=embedding_model,
                embedding_mode=embedding_mode,
            )
            result.hash_mismatch_count = diff_metrics["hash_mismatch_count"]
            result.missing_in_chroma_count = diff_metrics["missing_in_chroma_count"]
            result.extra_in_chroma_count = diff_metrics["extra_in_chroma_count"]
            result.chroma_document_count = collection.count()
            result.synced_document_count = len(ids)
            result.skipped_document_count = max(0, result.sqlite_child_segment_count - result.synced_document_count)
            result.collection_fingerprint = collection_fingerprint(chroma_rows)
            fake_collection_fingerprint_after = existing_collection_fingerprint(client, DEFAULT_COLLECTION_NAME)
            result.fake_collection_fingerprint_after = fake_collection_fingerprint_after
            result.fake_collection_mutation_detected = (
                embedding_mode == REAL_EMBEDDING_MODE
                and collection_name != DEFAULT_COLLECTION_NAME
                and fake_collection_fingerprint_before != fake_collection_fingerprint_after
            )
        finally:
            close_client(client)

        conn = sqlite3.connect(db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        try:
            source_guard_after = child_builder.collect_source_guard_stats(conn, source_table_names)
            source_guard_after_hash = child_builder.guard_hash(source_guard_after)
            child_segment_fingerprint_after = child_builder.stable_row_fingerprint(conn, scope)
            result.source_mutation_detected = source_guard_before_hash != source_guard_after_hash
            result.child_segment_mutation_detected = child_segment_fingerprint_before != child_segment_fingerprint_after
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
        finally:
            conn.close()

        if result.source_mutation_detected:
            result.errors.append("source table mutation detected during L3.6 sync")
        if result.child_segment_mutation_detected:
            result.errors.append("l3_child_segment source rows mutated during L3.6 sync")
        if result.fake_collection_mutation_detected:
            result.errors.append("fake Chroma collection mutated during L3.6b sync")
        if result.forbidden_final_table_count:
            result.errors.append("forbidden final tables exist after sync")
        if result.hash_mismatch_count or result.missing_in_chroma_count or result.extra_in_chroma_count:
            result.errors.append("SQLite and Chroma collection are not aligned after sync")

        manifest = {
            "sync_run_id": result.sync_run_id,
            "collection_name": result.collection_name,
            "embedding_provider": result.embedding_provider,
            "embedding_model": result.embedding_model,
            "embedding_mode": result.embedding_mode,
            "chapter_scope": scope,
            "sqlite_child_segment_count": result.sqlite_child_segment_count,
            "chroma_document_count": result.chroma_document_count,
            "synced_document_count": result.synced_document_count,
            "skipped_document_count": result.skipped_document_count,
            "hash_mismatch_count": result.hash_mismatch_count,
            "missing_in_chroma_count": result.missing_in_chroma_count,
            "extra_in_chroma_count": result.extra_in_chroma_count,
            "embedding_dimension": result.embedding_dimension,
            "embedding_error_count": result.embedding_error_count,
            "source_mutation_detected": result.source_mutation_detected,
            "child_segment_mutation_detected": result.child_segment_mutation_detected,
            "fake_collection_mutation_detected": result.fake_collection_mutation_detected,
            "forbidden_final_table_count": result.forbidden_final_table_count,
            "created_at": now_iso(),
            "chroma_dir": str(resolved_chroma_dir),
            "collection_fingerprint": result.collection_fingerprint,
            "fake_collection_name": DEFAULT_COLLECTION_NAME,
            "fake_collection_fingerprint_before": result.fake_collection_fingerprint_before,
            "fake_collection_fingerprint_after": result.fake_collection_fingerprint_after,
            "source_guard_before_hash": source_guard_before_hash,
            "source_guard_after_hash": source_guard_after_hash,
            "child_segment_fingerprint_before": child_segment_fingerprint_before,
            "child_segment_fingerprint_after": child_segment_fingerprint_after,
            "source_table_names": source_table_names,
            "test_fake_embedding_dim": TEST_FAKE_EMBEDDING_DIM,
        }
        manifest_relative_path, report_relative_path = output_paths_for_mode(embedding_mode, collection_name)
        write_manifest(root / manifest_relative_path, manifest)
        write_report(root / report_relative_path, manifest)
        result.error_count = len(result.errors)
        result.warning_count = len(result.warnings)
        result.ok = result.error_count == 0
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        result.error_count = len(result.errors)
        result.ok = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync L3.5 child_segments into Chroma for L3.6 sample scope.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--sample-chapters", type=str, default=None)
    parser.add_argument("--chapter-num", type=int, default=None)
    parser.add_argument("--rebuild-collection", action="store_true")
    parser.add_argument("--collection-name", type=str, default=DEFAULT_COLLECTION_NAME)
    parser.add_argument("--embedding-provider", type=str, default=DEFAULT_EMBEDDING_PROVIDER)
    parser.add_argument("--embedding-model", type=str, default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-mode", type=str, default=DEFAULT_EMBEDDING_MODE)
    parser.add_argument("--chroma-dir", type=Path, default=None)
    args = parser.parse_args()
    result = run_sync(
        args.project_dir,
        sample_chapters=args.sample_chapters,
        chapter_num=args.chapter_num,
        rebuild_collection=args.rebuild_collection,
        collection_name=args.collection_name,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        embedding_mode=args.embedding_mode,
        chroma_dir=args.chroma_dir,
    )
    if result.embedding_mode == REAL_EMBEDDING_MODE:
        print("L3.6b real embedding Chroma sync PASS" if result.ok else "L3.6b real embedding Chroma sync FAIL")
    else:
        print("L3.6 Chroma vector sync PASS" if result.ok else "L3.6 Chroma vector sync FAIL")
    if result.ok:
        print(f"collection_name={result.collection_name}")
        print(f"chroma_document_count={result.chroma_document_count}")
        print(f"embedding_dimension={result.embedding_dimension}")
    else:
        for error in result.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
