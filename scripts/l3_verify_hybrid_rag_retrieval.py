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
from scripts import l3_hybrid_rag_retriever as retriever


JSON_REPORT_RELATIVE_PATH = Path("outputs") / "l3_hybrid_rag_retrieval_verify_report.json"
MD_REPORT_RELATIVE_PATH = Path("outputs") / "l3_hybrid_rag_retrieval_verify_report.md"


@dataclass
class VerifyResult:
    ok: bool
    project_dir: Path
    db_path: Path
    chroma_dir: Path
    collection_name: str
    chapter_scope: list[int]
    query_count: int = 0
    sqlite_title_hit_count: int = 0
    sqlite_child_keyword_hit_count: int = 0
    chroma_vector_hit_count: int = 0
    merged_hit_count: int = 0
    zero_hit_query_count: int = 0
    good_query_count: int = 0
    acceptable_query_count: int = 0
    weak_query_count: int = 0
    keyword_vector_overlap_total: int = 0
    embedding_dimension: int = 0
    embedding_model: str = ""
    embedding_mode: str = ""
    hash_mismatch_count: int = 0
    missing_sqlite_child_count: int = 0
    missing_chroma_child_count: int = 0
    source_mutation_detected: bool = False
    child_segment_mutation_detected: bool = False
    chroma_mutation_detected: bool = False
    fake_collection_mutation_detected: bool = False
    real_collection_mutation_detected: bool = False
    forbidden_final_tables: list[str] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required retrieval output missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def verify_output_files(root: Path, result: VerifyResult, *, output_prefix: str) -> None:
    paths = retriever.relative_paths_for_prefix(output_prefix)
    for relative_path in paths.values():
        if not (root / relative_path).exists():
            result.errors.append(f"retrieval output file missing: {relative_path.as_posix()}")


def verify_sqlite_state(
    conn: sqlite3.Connection,
    *,
    manifest: dict[str, Any],
    results: dict[str, Any],
    chroma_rows: dict[str, dict[str, Any]],
    result: VerifyResult,
) -> None:
    if not child_builder.object_exists(conn, "l3_child_segment", "table"):
        result.errors.append("l3_child_segment does not exist")
        return
    if not child_builder.object_exists(conn, "l3_child_segment_sentence_link", "table"):
        result.errors.append("l3_child_segment_sentence_link does not exist")
        return

    source_table_names = list(manifest.get("source_table_names", []))
    current_source_table_names = child_builder.discover_source_table_names(conn)
    current_source_guard_hash = child_builder.guard_hash(child_builder.collect_source_guard_stats(conn, source_table_names))
    current_child_fingerprint = child_builder.stable_row_fingerprint(conn, result.chapter_scope)
    result.source_mutation_detected = (
        bool(manifest.get("source_mutation_detected"))
        or current_source_guard_hash != manifest.get("source_guard_after_hash")
        or current_source_table_names != source_table_names
    )
    result.child_segment_mutation_detected = (
        bool(manifest.get("child_segment_mutation_detected"))
        or current_child_fingerprint != manifest.get("child_segment_fingerprint_after")
    )
    if result.source_mutation_detected:
        result.errors.append("source table mutation detected after L3.7 retrieval")
    if result.child_segment_mutation_detected:
        result.errors.append("l3_child_segment source rows mutated after L3.7 retrieval")

    result.forbidden_final_tables = child_builder.forbidden_final_tables(conn)
    if result.forbidden_final_tables:
        result.errors.append(f"forbidden final tables exist: {', '.join(result.forbidden_final_tables)}")

    merged_hits = list(results.get("merged_hits", []))
    child_ids = [str(hit.get("child_segment_id", "")) for hit in merged_hits if hit.get("child_segment_id")]
    sqlite_rows = retriever.rows_by_child_id(conn, child_ids)
    for hit in merged_hits:
        child_segment_id = str(hit.get("child_segment_id", ""))
        row = sqlite_rows.get(child_segment_id)
        if row is None:
            result.missing_sqlite_child_count += 1
            result.errors.append(f"SQLite child_segment missing for retrieval hit: {child_segment_id}")
            continue
        recomputed_hash = child_builder.sha256_text(str(row["segment_text"]))
        if recomputed_hash != str(row["segment_text_hash"]) or str(hit.get("segment_text_hash")) != str(row["segment_text_hash"]):
            result.hash_mismatch_count += 1
            result.errors.append(f"segment_text_hash mismatch for retrieval hit: {child_segment_id}")
        chroma_payload = chroma_rows.get(child_segment_id)
        if chroma_payload is None:
            result.missing_chroma_child_count += 1
            result.errors.append(f"Chroma child_segment missing for retrieval hit: {child_segment_id}")
        else:
            metadata = chroma_payload["metadata"]
            if (
                str(metadata.get("segment_text_hash")) != str(row["segment_text_hash"])
                or str(metadata.get("child_segment_id")) != child_segment_id
                or str(chroma_payload["document"]) != str(row["segment_text"])
            ):
                result.hash_mismatch_count += 1
                result.errors.append(f"Chroma metadata hash mismatch for retrieval hit: {child_segment_id}")
        if not retriever.child_segment_sentence_link_ok(conn, child_segment_id, row):
            result.errors.append(f"sentence link backtrace failed for retrieval hit: {child_segment_id}")

    for hit in results.get("recall", {}).get("chroma_vector", []):
        child_segment_id = str(hit.get("child_segment_id", ""))
        if child_segment_id and child_segment_id not in chroma_rows:
            result.errors.append(f"Chroma vector recall id missing in collection: {child_segment_id}")


def verify_report_paths(output_prefix: str) -> tuple[Path, Path]:
    if output_prefix == retriever.DEFAULT_OUTPUT_PREFIX:
        return JSON_REPORT_RELATIVE_PATH, MD_REPORT_RELATIVE_PATH
    return (
        Path("outputs") / f"{output_prefix}_verify_report.json",
        Path("outputs") / f"{output_prefix}_verify_report.md",
    )


def write_json_report(result: VerifyResult, *, output_prefix: str = retriever.DEFAULT_OUTPUT_PREFIX) -> None:
    payload = {
        "verified_at": now_iso(),
        "collection_name": result.collection_name,
        "chapter_scope": result.chapter_scope,
        "query_count": result.query_count,
        "sqlite_title_hit_count": result.sqlite_title_hit_count,
        "sqlite_child_keyword_hit_count": result.sqlite_child_keyword_hit_count,
        "chroma_vector_hit_count": result.chroma_vector_hit_count,
        "merged_hit_count": result.merged_hit_count,
        "zero_hit_query_count": result.zero_hit_query_count,
        "good_query_count": result.good_query_count,
        "acceptable_query_count": result.acceptable_query_count,
        "weak_query_count": result.weak_query_count,
        "keyword_vector_overlap_total": result.keyword_vector_overlap_total,
        "embedding_dimension": result.embedding_dimension,
        "embedding_model": result.embedding_model,
        "embedding_mode": result.embedding_mode,
        "hash_mismatch_count": result.hash_mismatch_count,
        "missing_sqlite_child_count": result.missing_sqlite_child_count,
        "missing_chroma_child_count": result.missing_chroma_child_count,
        "source_mutation_detected": result.source_mutation_detected,
        "child_segment_mutation_detected": result.child_segment_mutation_detected,
        "chroma_mutation_detected": result.chroma_mutation_detected,
        "fake_collection_mutation_detected": result.fake_collection_mutation_detected,
        "real_collection_mutation_detected": result.real_collection_mutation_detected,
        "forbidden_final_tables": result.forbidden_final_tables,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "final_status": "PASS" if result.ok else "FAIL",
    }
    path = result.project_dir / verify_report_paths(output_prefix)[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_md_report(result: VerifyResult, *, output_prefix: str = retriever.DEFAULT_OUTPUT_PREFIX) -> None:
    lines = [
        "# L3.7 Hybrid RAG retrieval verify report",
        "",
        f"- verified_at: {now_iso()}",
        f"- collection_name: {result.collection_name}",
        f"- chapter_scope: {','.join(str(item) for item in result.chapter_scope)}",
        f"- query_count: {result.query_count}",
        f"- sqlite_title_hit_count: {result.sqlite_title_hit_count}",
        f"- sqlite_child_keyword_hit_count: {result.sqlite_child_keyword_hit_count}",
        f"- chroma_vector_hit_count: {result.chroma_vector_hit_count}",
        f"- merged_hit_count: {result.merged_hit_count}",
        f"- zero_hit_query_count: {result.zero_hit_query_count}",
        f"- good_query_count: {result.good_query_count}",
        f"- acceptable_query_count: {result.acceptable_query_count}",
        f"- weak_query_count: {result.weak_query_count}",
        f"- keyword_vector_overlap_total: {result.keyword_vector_overlap_total}",
        f"- embedding_mode: {result.embedding_mode}",
        f"- embedding_model: {result.embedding_model}",
        f"- embedding_dimension: {result.embedding_dimension}",
        f"- hash_mismatch_count: {result.hash_mismatch_count}",
        f"- missing_sqlite_child_count: {result.missing_sqlite_child_count}",
        f"- missing_chroma_child_count: {result.missing_chroma_child_count}",
        f"- source_mutation_detected: {result.source_mutation_detected}",
        f"- child_segment_mutation_detected: {result.child_segment_mutation_detected}",
        f"- chroma_mutation_detected: {result.chroma_mutation_detected}",
        f"- fake_collection_mutation_detected: {result.fake_collection_mutation_detected}",
        f"- real_collection_mutation_detected: {result.real_collection_mutation_detected}",
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
        final_status_line(result) if result.ok else ("L3.7b real embedding retrieval quality FULL FAIL" if result.embedding_mode == sync.REAL_EMBEDDING_MODE else "L3.7 Hybrid RAG retrieval FULL FAIL"),
        "",
    ]
    path = result.project_dir / verify_report_paths(output_prefix)[1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_verification(
    project_dir: Path | str,
    *,
    collection_name: str | None = None,
    expect_embedding_mode: str | None = None,
    expect_embedding_model: str | None = None,
    expect_output_prefix: str = retriever.DEFAULT_OUTPUT_PREFIX,
) -> VerifyResult:
    root = Path(project_dir).resolve()
    db_path = root / child_builder.DB_RELATIVE_PATH
    manifest: dict[str, Any] = {}
    results: dict[str, Any] = {}
    expected_collection_name = collection_name
    manifest_collection_name = ""
    scope: list[int] = []
    chroma_dir = root / sync.DEFAULT_CHROMA_DIR
    result = VerifyResult(False, root, db_path, chroma_dir, "", scope)
    try:
        verify_output_files(root, result, output_prefix=expect_output_prefix)
        paths = retriever.relative_paths_for_prefix(expect_output_prefix)
        manifest = load_json(root / paths["manifest"])
        results = load_json(root / paths["results_json"])
        manifest_collection_name = str(manifest.get("collection_name", sync.DEFAULT_COLLECTION_NAME))
        target_collection_name = expected_collection_name or manifest_collection_name
        scope = [int(item) for item in manifest.get("chapter_scope", [])]
        chroma_dir = sync.resolve_chroma_dir(root, manifest.get("chroma_dir"))
        result.collection_name = manifest_collection_name
        result.chapter_scope = scope
        result.chroma_dir = chroma_dir
        result.query_count = int(manifest.get("query_count", 0))
        result.sqlite_title_hit_count = int(manifest.get("sqlite_title_hit_count", 0))
        result.sqlite_child_keyword_hit_count = int(manifest.get("sqlite_child_keyword_hit_count", 0))
        result.chroma_vector_hit_count = int(manifest.get("chroma_vector_hit_count", 0))
        result.merged_hit_count = int(manifest.get("merged_hit_count", 0))
        result.zero_hit_query_count = int(manifest.get("zero_hit_query_count", 0))
        result.good_query_count = int(manifest.get("good_query_count", 0))
        result.acceptable_query_count = int(manifest.get("acceptable_query_count", 0))
        result.weak_query_count = int(manifest.get("weak_query_count", 0))
        result.keyword_vector_overlap_total = int(manifest.get("keyword_vector_overlap_total", 0))
        result.embedding_dimension = int(manifest.get("embedding_dimension", 0))
        result.embedding_model = str(manifest.get("embedding_model", ""))
        result.embedding_mode = str(manifest.get("embedding_mode", ""))
        result.warning_count = int(manifest.get("warning_count", 0))
        result.warnings.extend(str(item) for item in manifest.get("warnings", []))

        if expected_collection_name is not None and result.collection_name != expected_collection_name:
            result.errors.append(f"collection_name mismatch: expected {expected_collection_name}, got {result.collection_name}")
        if expect_embedding_mode is not None and result.embedding_mode != expect_embedding_mode:
            result.errors.append(f"embedding_mode mismatch: expected {expect_embedding_mode}, got {result.embedding_mode}")
        if expect_embedding_model is not None and result.embedding_model != expect_embedding_model:
            result.errors.append(f"embedding_model mismatch: expected {expect_embedding_model}, got {result.embedding_model}")
        if result.query_count <= 0:
            result.errors.append("query_count must be > 0")
        if expect_embedding_mode == sync.REAL_EMBEDDING_MODE and result.query_count != 10:
            result.errors.append(f"query_count must be 10 for L3.7b real quality; got {result.query_count}")
        if expect_embedding_mode == sync.REAL_EMBEDDING_MODE and result.embedding_dimension != 1024:
            result.errors.append(f"embedding_dimension must be 1024 for BAAI/bge-m3; got {result.embedding_dimension}")
        if not db_path.exists():
            result.errors.append("SQLite database does not exist")

        client = chromadb.PersistentClient(path=str(chroma_dir))
        try:
            try:
                collection = client.get_collection(target_collection_name)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Chroma collection missing: {collection_name}") from exc
            chroma_rows = sync.fetch_collection_rows(collection)
            current_fingerprint = sync.collection_fingerprint(chroma_rows)
            result.chroma_mutation_detected = (
                bool(manifest.get("chroma_mutation_detected"))
                or current_fingerprint != manifest.get("collection_fingerprint_after")
            )
            if chroma_rows:
                first = next(iter(chroma_rows.values()))
                result.embedding_dimension = len(first["embedding"])
                metadata = first["metadata"]
                if expect_embedding_mode is not None and metadata.get("embedding_mode") != expect_embedding_mode:
                    result.errors.append(f"Chroma metadata embedding_mode mismatch: {metadata.get('embedding_mode')}")
                if expect_embedding_model is not None and metadata.get("embedding_model") != expect_embedding_model:
                    result.errors.append(f"Chroma metadata embedding_model mismatch: {metadata.get('embedding_model')}")
            fake_fingerprint = sync.existing_collection_fingerprint(client, sync.DEFAULT_COLLECTION_NAME)
            real_fingerprint = sync.existing_collection_fingerprint(client, sync.DEFAULT_REAL_COLLECTION_NAME)
            result.fake_collection_mutation_detected = fake_fingerprint != manifest.get("fake_collection_fingerprint_after", fake_fingerprint)
            result.real_collection_mutation_detected = real_fingerprint != manifest.get("real_collection_fingerprint_after", real_fingerprint)
            if result.chroma_mutation_detected:
                result.errors.append("Chroma collection mutation detected after L3.7 retrieval")
            if result.fake_collection_mutation_detected:
                result.errors.append("fake Chroma collection mutation detected after L3.7 retrieval")
            if result.real_collection_mutation_detected:
                result.errors.append("real Chroma collection mutation detected after L3.7 retrieval")
        finally:
            sync.close_client(client)

        if db_path.exists():
            conn = retriever.connect_sqlite_readonly(db_path)
            try:
                verify_sqlite_state(conn, manifest=manifest, results=results, chroma_rows=chroma_rows, result=result)
            finally:
                conn.close()

        if int(manifest.get("hash_mismatch_count", 0)) != 0:
            result.errors.append("retrieval manifest recorded hash mismatches")
        if int(manifest.get("missing_sqlite_child_count", 0)) != 0:
            result.errors.append("retrieval manifest recorded missing SQLite child rows")
        if int(manifest.get("missing_chroma_child_count", 0)) != 0:
            result.errors.append("retrieval manifest recorded missing Chroma child rows")
        if int(manifest.get("forbidden_final_table_count", 0)) != 0:
            result.errors.append("retrieval manifest recorded forbidden final tables")
        if expect_embedding_mode == sync.REAL_EMBEDDING_MODE:
            quality = list(results.get("quality_summary", []))
            if len(quality) != 10:
                result.errors.append(f"quality_summary must contain 10 rows; got {len(quality)}")
            for item in quality:
                for key in (
                    "vector_top1_child_segment_id",
                    "vector_top1_score",
                    "vector_top1_chapter_num",
                    "vector_top1_snippet",
                    "vector_top3_child_segment_ids",
                    "vector_top5_child_segment_ids",
                    "keyword_vector_overlap_count",
                    "hybrid_top1_recall_sources",
                    "quality_label",
                ):
                    if key not in item:
                        result.errors.append(f"quality_summary missing field: {key}")
                        break
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))

    result.error_count = len(result.errors)
    result.ok = result.error_count == 0
    write_json_report(result, output_prefix=expect_output_prefix)
    write_md_report(result, output_prefix=expect_output_prefix)
    return result


def final_status_line(result: VerifyResult) -> str:
    if result.embedding_mode == sync.REAL_EMBEDDING_MODE:
        return "L3.7b real embedding retrieval quality FULL PASS" if result.ok else "L3.7b real embedding retrieval quality FULL FAIL"
    return "L3.7 Hybrid RAG retrieval FULL PASS" if result.ok else "L3.7 Hybrid RAG retrieval FULL FAIL"


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.7 hybrid RAG retrieval sample outputs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--collection-name", type=str, default=None)
    parser.add_argument("--expect-embedding-mode", type=str, default=None)
    parser.add_argument("--expect-embedding-model", type=str, default=None)
    parser.add_argument("--expect-output-prefix", type=str, default=retriever.DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    result = run_verification(
        args.project_dir,
        collection_name=args.collection_name,
        expect_embedding_mode=args.expect_embedding_mode,
        expect_embedding_model=args.expect_embedding_model,
        expect_output_prefix=args.expect_output_prefix,
    )
    print(final_status_line(result))
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
