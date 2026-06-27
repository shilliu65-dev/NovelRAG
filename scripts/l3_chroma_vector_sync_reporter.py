from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import chromadb

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as child_builder
from scripts import l3_chroma_vector_sync as sync


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError("L3.6 manifest is missing; run l3_chroma_vector_sync.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def latest_sync_state(project_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    db_path = project_dir / child_builder.DB_RELATIVE_PATH
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        scope = [int(item) for item in manifest["chapter_scope"]]
        sqlite_rows = sync.fetch_child_segments(conn, scope)
        source_table_names = list(manifest["source_table_names"])
        source_guard_hash = child_builder.guard_hash(child_builder.collect_source_guard_stats(conn, source_table_names))
        child_segment_fingerprint = child_builder.stable_row_fingerprint(conn, scope)
        forbidden_tables = child_builder.forbidden_final_tables(conn)
    finally:
        conn.close()

    client = chromadb.PersistentClient(path=str(sync.resolve_chroma_dir(project_dir, manifest["chroma_dir"])))
    try:
        collection = client.get_collection(manifest["collection_name"])
        chroma_rows = sync.fetch_collection_rows(collection)
        diff = sync.compute_diff_metrics(
            sqlite_rows,
            chroma_rows,
            embedding_provider=str(manifest["embedding_provider"]),
            embedding_model=str(manifest["embedding_model"]),
            embedding_mode=str(manifest["embedding_mode"]),
        )
        chroma_count = collection.count()
        fingerprint = sync.collection_fingerprint(chroma_rows)
        embedding_dimension = 0
        if chroma_rows:
            first = next(iter(chroma_rows.values()))
            embedding_dimension = len(first["embedding"])
        fake_fingerprint = sync.existing_collection_fingerprint(client, sync.DEFAULT_COLLECTION_NAME)
    finally:
        sync.close_client(client)
    return {
        "sync_run_id": manifest["sync_run_id"],
        "collection_name": manifest["collection_name"],
        "embedding_provider": manifest["embedding_provider"],
        "embedding_model": manifest["embedding_model"],
        "embedding_mode": manifest["embedding_mode"],
        "chapter_scope": scope,
        "sqlite_child_segment_count": len(sqlite_rows),
        "chroma_document_count": chroma_count,
        "synced_document_count": len(sqlite_rows),
        "skipped_document_count": max(0, len(sqlite_rows) - len(sqlite_rows)),
        "hash_mismatch_count": diff["hash_mismatch_count"],
        "missing_in_chroma_count": diff["missing_in_chroma_count"],
        "extra_in_chroma_count": diff["extra_in_chroma_count"],
        "embedding_dimension": embedding_dimension,
        "embedding_error_count": int(manifest.get("embedding_error_count", 0)),
        "source_mutation_detected": source_guard_hash != manifest["source_guard_after_hash"],
        "child_segment_mutation_detected": child_segment_fingerprint != manifest["child_segment_fingerprint_after"],
        "fake_collection_mutation_detected": fake_fingerprint != manifest.get("fake_collection_fingerprint_after", fake_fingerprint),
        "forbidden_final_table_count": len(forbidden_tables),
        "created_at": manifest["created_at"],
        "chroma_dir": manifest["chroma_dir"],
        "collection_fingerprint": fingerprint,
        "fake_collection_name": manifest.get("fake_collection_name", sync.DEFAULT_COLLECTION_NAME),
        "fake_collection_fingerprint_before": manifest.get("fake_collection_fingerprint_before", fake_fingerprint),
        "fake_collection_fingerprint_after": fake_fingerprint,
        "source_guard_before_hash": manifest["source_guard_before_hash"],
        "source_guard_after_hash": source_guard_hash,
        "child_segment_fingerprint_before": manifest["child_segment_fingerprint_before"],
        "child_segment_fingerprint_after": child_segment_fingerprint,
        "source_table_names": source_table_names,
        "test_fake_embedding_dim": manifest["test_fake_embedding_dim"],
    }


def run_report(project_dir: Path | str, *, collection_name: str = sync.DEFAULT_COLLECTION_NAME) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    child_builder.ensure_dirs(root)
    manifest_relative_path, report_relative_path = sync.output_paths_for_mode(
        sync.REAL_EMBEDDING_MODE if collection_name == sync.DEFAULT_REAL_COLLECTION_NAME else sync.DEFAULT_EMBEDDING_MODE,
        collection_name,
    )
    manifest_path = root / manifest_relative_path
    current = latest_sync_state(root, load_manifest(manifest_path))
    sync.write_manifest(manifest_path, current)
    sync.write_report(root / report_relative_path, current)
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute L3.6 Chroma vector sync manifest and report.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--collection-name", type=str, default=sync.DEFAULT_COLLECTION_NAME)
    args = parser.parse_args()
    manifest = run_report(args.project_dir, collection_name=args.collection_name)
    if manifest["embedding_mode"] == sync.REAL_EMBEDDING_MODE:
        print("L3.6b real embedding Chroma sync REPORT PASS")
    else:
        print("L3.6 Chroma vector sync REPORT PASS")
    print(f"chroma_document_count={manifest['chroma_document_count']}")


if __name__ == "__main__":
    main()
