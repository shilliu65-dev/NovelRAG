from __future__ import annotations

import argparse
import json
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
from scripts import l3_chroma_vector_sync as sync


JSON_REPORT_RELATIVE_PATH = Path("outputs") / "l3_chroma_vector_sync_verify_report.json"
MD_REPORT_RELATIVE_PATH = Path("outputs") / "l3_chroma_vector_sync_verify_report.md"
REAL_JSON_REPORT_RELATIVE_PATH = Path("outputs") / "l3_chroma_real_embedding_sync_verify_report.json"
REAL_MD_REPORT_RELATIVE_PATH = Path("outputs") / "l3_chroma_real_embedding_sync_verify_report.md"


@dataclass
class VerifyResult:
    ok: bool
    project_dir: Path
    db_path: Path
    chroma_dir: Path
    collection_name: str
    chapter_scope: list[int]
    embedding_provider: str
    embedding_model: str
    embedding_mode: str
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
    forbidden_final_tables: list[str] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_manifest(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError("L3.6 manifest is missing; run l3_chroma_vector_sync.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json_report(result: VerifyResult) -> None:
    payload = {
        "verified_at": now_iso(),
        "collection_name": result.collection_name,
        "embedding_provider": result.embedding_provider,
        "embedding_model": result.embedding_model,
        "embedding_mode": result.embedding_mode,
        "chapter_scope": result.chapter_scope,
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
        "forbidden_final_tables": result.forbidden_final_tables,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "final_status": "PASS" if result.ok else "FAIL",
    }
    report_path = REAL_JSON_REPORT_RELATIVE_PATH if result.embedding_mode == sync.REAL_EMBEDDING_MODE else JSON_REPORT_RELATIVE_PATH
    (result.project_dir / report_path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_md_report(result: VerifyResult) -> None:
    lines = [
        "# L3.6 Chroma vector sync verify report",
        "",
        f"- verified_at: {now_iso()}",
        f"- collection_name: {result.collection_name}",
        f"- embedding_provider: {result.embedding_provider}",
        f"- embedding_model: {result.embedding_model}",
        f"- embedding_mode: {result.embedding_mode}",
        f"- chapter_scope: {','.join(str(item) for item in result.chapter_scope)}",
        f"- sqlite_child_segment_count: {result.sqlite_child_segment_count}",
        f"- chroma_document_count: {result.chroma_document_count}",
        f"- synced_document_count: {result.synced_document_count}",
        f"- skipped_document_count: {result.skipped_document_count}",
        f"- hash_mismatch_count: {result.hash_mismatch_count}",
        f"- missing_in_chroma_count: {result.missing_in_chroma_count}",
        f"- extra_in_chroma_count: {result.extra_in_chroma_count}",
        f"- embedding_dimension: {result.embedding_dimension}",
        f"- embedding_error_count: {result.embedding_error_count}",
        f"- source_mutation_detected: {result.source_mutation_detected}",
        f"- child_segment_mutation_detected: {result.child_segment_mutation_detected}",
        f"- fake_collection_mutation_detected: {result.fake_collection_mutation_detected}",
        f"- forbidden_final_tables: {', '.join(result.forbidden_final_tables) if result.forbidden_final_tables else 'none'}",
        f"- final: {'PASS' if result.ok else 'FAIL'}",
        "",
        "## Errors",
        "",
        *(f"- {error}" for error in result.errors),
        *(["- none"] if not result.errors else []),
        "",
        "## Warnings",
        "",
        *(f"- {warning}" for warning in result.warnings),
        *(["- none"] if not result.warnings else []),
        "",
        final_status_line(result) if result.ok else ("L3.6b real embedding Chroma sync FULL FAIL" if result.embedding_mode == sync.REAL_EMBEDDING_MODE else "L3.6 Chroma vector sync FULL FAIL"),
        "",
    ]
    report_path = REAL_MD_REPORT_RELATIVE_PATH if result.embedding_mode == sync.REAL_EMBEDDING_MODE else MD_REPORT_RELATIVE_PATH
    (result.project_dir / report_path).write_text("\n".join(lines), encoding="utf-8")


def run_verification(
    project_dir: Path | str,
    *,
    collection_name: str = sync.DEFAULT_COLLECTION_NAME,
    expect_embedding_mode: str | None = None,
    expect_embedding_model: str | None = None,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    mode_hint = expect_embedding_mode or (
        sync.REAL_EMBEDDING_MODE if collection_name == sync.DEFAULT_REAL_COLLECTION_NAME else sync.DEFAULT_EMBEDDING_MODE
    )
    manifest_relative_path, _ = sync.output_paths_for_mode(mode_hint, collection_name)
    manifest = load_manifest(root / manifest_relative_path)
    scope = [int(item) for item in manifest["chapter_scope"]]
    db_path = root / child_builder.DB_RELATIVE_PATH
    chroma_dir = sync.resolve_chroma_dir(root, manifest["chroma_dir"])
    result = VerifyResult(
        False,
        root,
        db_path,
        chroma_dir,
        str(manifest["collection_name"]),
        scope,
        str(manifest["embedding_provider"]),
        str(manifest["embedding_model"]),
        str(manifest["embedding_mode"]),
    )
    try:
        if collection_name != result.collection_name:
            result.errors.append(f"manifest collection_name mismatch: expected {collection_name}, got {result.collection_name}")
        if expect_embedding_mode is not None and result.embedding_mode != expect_embedding_mode:
            result.errors.append(f"embedding_mode mismatch: expected {expect_embedding_mode}, got {result.embedding_mode}")
        if expect_embedding_model is not None and result.embedding_model != expect_embedding_model:
            result.errors.append(f"embedding_model mismatch: expected {expect_embedding_model}, got {result.embedding_model}")
        result.embedding_error_count = int(manifest.get("embedding_error_count", 0))
        if result.embedding_error_count:
            result.errors.append("manifest recorded embedding errors")
        if not db_path.exists():
            raise RuntimeError("SQLite database does not exist")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            if not child_builder.object_exists(conn, "l3_child_segment", "table"):
                result.errors.append("l3_child_segment does not exist")
            if not child_builder.object_exists(conn, "l3_child_segment_sentence_link", "table"):
                result.errors.append("l3_child_segment_sentence_link does not exist")
            if not child_builder.object_exists(conn, "l3_child_segment_build_run", "table"):
                result.errors.append("l3_child_segment_build_run does not exist")
            sqlite_rows = sync.fetch_child_segments(conn, scope)
            result.sqlite_child_segment_count = len(sqlite_rows)
            if result.sqlite_child_segment_count < 1:
                result.errors.append("sample chapters have no child_segments")
            source_table_names = list(manifest["source_table_names"])
            source_guard_hash = child_builder.guard_hash(child_builder.collect_source_guard_stats(conn, source_table_names))
            result.source_mutation_detected = source_guard_hash != manifest["source_guard_after_hash"]
            if result.source_mutation_detected:
                result.errors.append("source table mutation detected after L3.6 sync")
            child_segment_fingerprint = child_builder.stable_row_fingerprint(conn, scope)
            result.child_segment_mutation_detected = child_segment_fingerprint != manifest["child_segment_fingerprint_after"]
            if result.child_segment_mutation_detected:
                result.errors.append("l3_child_segment source rows mutated after L3.6 sync")
            result.forbidden_final_tables = child_builder.forbidden_final_tables(conn)
            if result.forbidden_final_tables:
                result.errors.append(f"forbidden final tables exist: {', '.join(result.forbidden_final_tables)}")
        finally:
            conn.close()

        client = chromadb.PersistentClient(path=str(chroma_dir))
        try:
            try:
                collection = client.get_collection(result.collection_name)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Chroma collection missing: {result.collection_name}") from exc
            chroma_rows = sync.fetch_collection_rows(collection)
            result.chroma_document_count = collection.count()
            result.synced_document_count = result.sqlite_child_segment_count
            result.skipped_document_count = 0
            if chroma_rows:
                first_row = next(iter(chroma_rows.values()))
                result.embedding_dimension = len(first_row["embedding"])
            if result.embedding_mode == sync.REAL_EMBEDDING_MODE and result.embedding_dimension <= 0:
                result.errors.append("embedding_dimension must be > 0 for real_embedding")
            if result.sqlite_child_segment_count != result.chroma_document_count:
                result.errors.append("SQLite child_segment count != Chroma document count")
            if "collection_fingerprint" in manifest:
                current_fingerprint = sync.collection_fingerprint(chroma_rows)
                if current_fingerprint != manifest["collection_fingerprint"]:
                    result.errors.append("target Chroma collection mutation detected after sync")
            if "fake_collection_fingerprint_after" in manifest:
                fake_fingerprint = sync.existing_collection_fingerprint(client, sync.DEFAULT_COLLECTION_NAME)
                result.fake_collection_mutation_detected = fake_fingerprint != manifest.get("fake_collection_fingerprint_after", fake_fingerprint)
                if result.fake_collection_mutation_detected:
                    result.errors.append("fake Chroma collection mutated after L3.6b sync")
        finally:
            sync.close_client(client)

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            sqlite_rows = sync.fetch_child_segments(conn, scope)
        finally:
            conn.close()
        sqlite_by_id = {str(row["child_segment_id"]): row for row in sqlite_rows}
        if sorted(chroma_rows) != sorted(sqlite_by_id):
            result.missing_in_chroma_count = len(set(sqlite_by_id) - set(chroma_rows))
            result.extra_in_chroma_count = len(set(chroma_rows) - set(sqlite_by_id))
        for doc_id, payload in chroma_rows.items():
            if doc_id != str(payload["metadata"].get("child_segment_id")):
                result.errors.append(f"Chroma id mismatch: {doc_id}")
            row = sqlite_by_id.get(doc_id)
            if row is None:
                continue
            if payload["document"] != row["segment_text"]:
                result.hash_mismatch_count += 1
                result.errors.append(f"document text mismatch for {doc_id}")
                continue
            expected_metadata = sync.collection_metadata_for_segment(
                row,
                embedding_provider=result.embedding_provider,
                embedding_model=result.embedding_model,
                embedding_mode=result.embedding_mode,
            )
            for key in ("segment_text_hash", "chapter_num", "scene_id", "version_id", "embedding_mode", "embedding_model"):
                if payload["metadata"].get(key) != expected_metadata.get(key):
                    result.hash_mismatch_count += 1
                    result.errors.append(f"metadata mismatch for {doc_id}: {key}")
                    break
        if result.missing_in_chroma_count:
            result.errors.append("SQLite has child_segments missing in Chroma")
        if result.extra_in_chroma_count:
            result.errors.append("Chroma has extra documents not present in SQLite")
        if result.hash_mismatch_count:
            result.errors.append("Chroma metadata or document hashes do not align with SQLite")
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))

    result.error_count = len(result.errors)
    result.ok = result.error_count == 0
    write_json_report(result)
    write_md_report(result)
    return result


def final_status_line(result: VerifyResult) -> str:
    if result.embedding_mode == sync.REAL_EMBEDDING_MODE:
        return "L3.6b real embedding Chroma sync FULL PASS" if result.ok else "L3.6b real embedding Chroma sync FULL FAIL"
    return "L3.6 Chroma vector sync FULL PASS" if result.ok else "L3.6 Chroma vector sync FULL FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.6 Chroma vector sync sample scope.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--collection-name", type=str, default=sync.DEFAULT_COLLECTION_NAME)
    parser.add_argument("--expect-embedding-mode", type=str, default=None)
    parser.add_argument("--expect-embedding-model", type=str, default=None)
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        collection_name=args.collection_name,
        expect_embedding_mode=args.expect_embedding_mode,
        expect_embedding_model=args.expect_embedding_model,
    )
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
