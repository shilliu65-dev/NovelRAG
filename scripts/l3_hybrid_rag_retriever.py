from __future__ import annotations

import argparse
import csv
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


DEFAULT_QUERIES = ["陈伶", "韩蒙", "戏神道", "极光界域", "灾厄"]
REAL_QUALITY_EXTRA_QUERIES = [
    "主角醒来后发生了什么",
    "谁在第一章出现",
    "和灾厄有关的描写",
    "人物第一次遭遇危险",
    "场景里出现的关键人物",
]
DEFAULT_TOP_K = 10
DEFAULT_TITLE_WEIGHT = 0.35
DEFAULT_KEYWORD_WEIGHT = 0.30
DEFAULT_VECTOR_WEIGHT = 0.35
DEFAULT_OUTPUT_PREFIX = "l3_hybrid_rag_retrieval"
REAL_OUTPUT_PREFIX = "l3_hybrid_rag_real_embedding_retrieval"
RESULTS_JSON_RELATIVE_PATH = Path("outputs") / "l3_hybrid_rag_retrieval_results.json"
RESULTS_CSV_RELATIVE_PATH = Path("outputs") / "l3_hybrid_rag_retrieval_results.csv"
REPORT_RELATIVE_PATH = Path("outputs") / "l3_hybrid_rag_retrieval_report.md"
MANIFEST_RELATIVE_PATH = Path("outputs") / "l3_hybrid_rag_retrieval_manifest.json"
OPTIONAL_SQLITE_OBJECTS = [
    "v_current_chapters",
    "v_l2_current_paragraphs",
    "v_l2_current_sentences",
    "l3_scene_blocks",
    "l3_chapter_title_index",
    "l3_chapter_title_fts",
]
REQUIRED_SQLITE_TABLES = [
    "l3_child_segment",
    "l3_child_segment_sentence_link",
]


@dataclass
class RetrievalResult:
    ok: bool
    project_dir: Path
    db_path: Path
    chroma_dir: Path
    collection_name: str
    chapter_scope: list[int]
    embedding_provider: str
    embedding_model: str
    embedding_mode: str
    retrieval_run_id: str = ""
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
    hash_mismatch_count: int = 0
    missing_sqlite_child_count: int = 0
    missing_chroma_child_count: int = 0
    embedding_dimension: int = 0
    source_mutation_detected: bool = False
    child_segment_mutation_detected: bool = False
    chroma_mutation_detected: bool = False
    fake_collection_mutation_detected: bool = False
    real_collection_mutation_detected: bool = False
    forbidden_final_table_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    title_recall: list[dict[str, Any]] = field(default_factory=list)
    keyword_recall: list[dict[str, Any]] = field(default_factory=list)
    vector_recall: list[dict[str, Any]] = field(default_factory=list)
    merged_hits: list[dict[str, Any]] = field(default_factory=list)
    quality_summary: list[dict[str, Any]] = field(default_factory=list)
    queries: list[dict[str, str]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def connect_sqlite_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def normalize_query(value: str) -> str:
    return " ".join(str(value).strip().split())


def query_specs(query: str | None, *, real_quality_report: bool = False) -> list[dict[str, str]]:
    values = [query] if query is not None else DEFAULT_QUERIES + (REAL_QUALITY_EXTRA_QUERIES if real_quality_report else [])
    return [
        {"query_id": f"q{index:03d}", "query_text": normalize_query(value)}
        for index, value in enumerate(values, start=1)
    ]


def relative_paths_for_prefix(output_prefix: str) -> dict[str, Path]:
    if output_prefix == DEFAULT_OUTPUT_PREFIX:
        return {
            "results_json": RESULTS_JSON_RELATIVE_PATH,
            "results_csv": RESULTS_CSV_RELATIVE_PATH,
            "report": REPORT_RELATIVE_PATH,
            "manifest": MANIFEST_RELATIVE_PATH,
        }
    return {
        "results_json": Path("outputs") / f"{output_prefix}_results.json",
        "results_csv": Path("outputs") / f"{output_prefix}_results.csv",
        "report": Path("outputs") / f"{output_prefix}_report.md",
        "manifest": Path("outputs") / f"{output_prefix}_manifest.json",
    }


def snippet(text: str, *, max_chars: int = 120) -> str:
    compact = " ".join(str(text).split())
    return compact[:max_chars]


def fts_phrase(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def score_from_distance(distance: Any) -> float:
    try:
        value = float(distance)
    except (TypeError, ValueError):
        return 0.0
    if value < 0:
        value = abs(value)
    return round(1.0 / (1.0 + value), 6)


def keyword_score(query_text: str, segment_text: str) -> tuple[int, float]:
    if not query_text:
        return 0, 0.0
    occurrence_count = segment_text.count(query_text)
    if occurrence_count <= 0:
        return 0, 0.0
    return occurrence_count, round(min(1.0, 0.25 + occurrence_count / 3.0), 6)


class QueryEmbeddingEncoder:
    def __init__(self, *, embedding_provider: str, embedding_model: str, embedding_mode: str) -> None:
        self.embedding_provider = embedding_provider
        self.embedding_model = embedding_model
        self.embedding_mode = embedding_mode
        self._model: Any | None = None

    def encode(self, text: str) -> list[float]:
        if self.embedding_mode == sync.DEFAULT_EMBEDDING_MODE:
            return sync.embedding_for_text(text, embedding_mode=self.embedding_mode)
        sync.verify_embedding_configuration(
            embedding_provider=self.embedding_provider,
            embedding_model=self.embedding_model,
            embedding_mode=self.embedding_mode,
        )
        if self._model is None:
            self._model = sync.load_sentence_transformer_model(self.embedding_model)
        encoded = self._model.encode([text], normalize_embeddings=True, show_progress_bar=False)
        vector = encoded[0]
        if hasattr(vector, "tolist"):
            vector = vector.tolist()
        return [float(value) for value in vector]


def sqlite_object_available(conn: sqlite3.Connection, name: str) -> bool:
    return child_builder.object_exists(conn, name)


def validate_sqlite_inputs(conn: sqlite3.Connection, result: RetrievalResult) -> None:
    for table_name in REQUIRED_SQLITE_TABLES:
        if not child_builder.object_exists(conn, table_name, "table"):
            raise RuntimeError(f"required SQLite table missing: {table_name}")
    for object_name in OPTIONAL_SQLITE_OBJECTS:
        if not sqlite_object_available(conn, object_name):
            result.warnings.append(f"optional SQLite object missing: {object_name}")


def sqlite_title_recall(
    conn: sqlite3.Connection,
    *,
    query_id: str,
    query_text: str,
    scope: list[int],
    top_k: int,
) -> list[dict[str, Any]]:
    if not query_text or not sqlite_object_available(conn, "l3_chapter_title_index"):
        return []
    placeholders = child_builder.scoped_placeholders(scope)
    rows: list[sqlite3.Row] = []
    if sqlite_object_available(conn, "l3_chapter_title_fts"):
        try:
            rows = conn.execute(
                f"""
                SELECT
                    ti.chapter_id,
                    ti.chapter_num,
                    ti.title_raw,
                    ti.title_norm,
                    ti.title_keywords_json,
                    bm25(l3_chapter_title_fts) AS raw_score
                FROM l3_chapter_title_fts
                JOIN l3_chapter_title_index ti
                  ON ti.chapter_num = l3_chapter_title_fts.chapter_num
                WHERE l3_chapter_title_fts MATCH ?
                  AND ti.chapter_num IN ({placeholders})
                ORDER BY raw_score ASC, ti.chapter_num ASC
                LIMIT ?
                """,
                (fts_phrase(query_text), *scope, top_k),
            ).fetchall()
        except sqlite3.DatabaseError:
            rows = []
    if rows:
        recalled: list[dict[str, Any]] = []
        for rank, row in enumerate(rows, start=1):
            recalled.append(
                {
                    "query_id": query_id,
                    "query_text": query_text,
                    "recall_type": "sqlite_title",
                    "chapter_id": row["chapter_id"],
                    "chapter_num": int(row["chapter_num"]),
                    "title_score": round(1.0 / (1.0 + abs(float(row["raw_score"] or 0.0))), 6),
                    "rank": rank,
                }
            )
        return recalled

    like_value = f"%{query_text}%"
    rows = conn.execute(
        f"""
        SELECT chapter_id, chapter_num, title_raw, title_norm, title_keywords_json
        FROM l3_chapter_title_index
        WHERE chapter_num IN ({placeholders})
          AND (
              title_raw LIKE ?
              OR title_norm LIKE ?
              OR title_keywords_json LIKE ?
          )
        ORDER BY chapter_num ASC
        LIMIT ?
        """,
        (*scope, like_value, like_value, like_value, top_k),
    ).fetchall()
    recalled = []
    for rank, row in enumerate(rows, start=1):
        title_raw = str(row["title_raw"] or "")
        title_norm = str(row["title_norm"] or "")
        keywords = str(row["title_keywords_json"] or "")
        if query_text == title_norm or query_text == title_raw:
            score = 1.0
        elif query_text in keywords:
            score = 0.9
        else:
            score = 0.8
        recalled.append(
            {
                "query_id": query_id,
                "query_text": query_text,
                "recall_type": "sqlite_title",
                "chapter_id": row["chapter_id"],
                "chapter_num": int(row["chapter_num"]),
                "title_score": round(score, 6),
                "rank": rank,
            }
        )
    return recalled


def sqlite_child_keyword_recall(
    conn: sqlite3.Connection,
    *,
    query_id: str,
    query_text: str,
    scope: list[int],
    top_k: int,
) -> list[dict[str, Any]]:
    if not query_text:
        return []
    placeholders = child_builder.scoped_placeholders(scope)
    rows = conn.execute(
        f"""
        SELECT *
        FROM l3_child_segment
        WHERE chapter_num IN ({placeholders})
          AND segment_text LIKE ?
        ORDER BY chapter_num ASC, scene_id ASC, segment_index_in_scene ASC, child_segment_id ASC
        """,
        (*scope, f"%{query_text}%"),
    ).fetchall()
    scored: list[tuple[int, float, sqlite3.Row]] = []
    for row in rows:
        occurrence_count, score = keyword_score(query_text, str(row["segment_text"]))
        if occurrence_count > 0:
            scored.append((occurrence_count, score, row))
    scored.sort(key=lambda item: (-item[1], -item[0], int(item[2]["char_len"]), str(item[2]["child_segment_id"])))
    recalled: list[dict[str, Any]] = []
    for rank, (_, score, row) in enumerate(scored[:top_k], start=1):
        recalled.append(
            {
                "query_id": query_id,
                "query_text": query_text,
                "recall_type": "sqlite_child_keyword",
                "child_segment_id": row["child_segment_id"],
                "scene_id": str(row["scene_id"]),
                "chapter_id": row["chapter_id"],
                "chapter_num": int(row["chapter_num"]),
                "keyword_score": score,
                "rank": rank,
            }
        )
    return recalled


def chroma_vector_recall(
    collection: Any,
    *,
    query_id: str,
    query_text: str,
    scope: list[int],
    top_k: int,
    query_encoder: QueryEmbeddingEncoder,
) -> list[dict[str, Any]]:
    if not query_text:
        return []
    collection_count = int(collection.count())
    if collection_count <= 0:
        return []
    n_results = min(collection_count, max(top_k * 5, top_k))
    query_embedding = query_encoder.encode(query_text)
    try:
        payload = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            where={"chapter_num": {"$in": scope}},
            include=["metadatas", "distances", "documents"],
        )
    except Exception:  # noqa: BLE001
        payload = collection.query(
            query_embeddings=[query_embedding],
            n_results=n_results,
            include=["metadatas", "distances", "documents"],
        )
    ids = payload.get("ids", [[]])[0]
    metadatas = payload.get("metadatas", [[]])[0]
    distances = payload.get("distances", [[]])[0]
    documents = payload.get("documents", [[]])[0]
    recalled: list[dict[str, Any]] = []
    for doc_id, metadata, distance, document in zip(ids, metadatas, distances, documents, strict=False):
        chapter_num = int(metadata.get("chapter_num", -1))
        if chapter_num not in scope:
            continue
        recalled.append(
            {
                "query_id": query_id,
                "query_text": query_text,
                "recall_type": "chroma_vector",
                "child_segment_id": str(metadata.get("child_segment_id", doc_id)),
                "scene_id": str(metadata.get("scene_id", "")),
                "chapter_id": str(metadata.get("chapter_id", "")),
                "chapter_num": chapter_num,
                "vector_distance": round(float(distance), 6),
                "vector_score": score_from_distance(distance),
                "vector_snippet": snippet(str(document)),
                "rank": len(recalled) + 1,
            }
        )
        if len(recalled) >= top_k:
            break
    return recalled


def rows_by_child_id(conn: sqlite3.Connection, child_segment_ids: list[str]) -> dict[str, sqlite3.Row]:
    if not child_segment_ids:
        return {}
    unique_ids = sorted(set(child_segment_ids))
    placeholders = ", ".join("?" for _ in unique_ids)
    rows = conn.execute(
        f"""
        SELECT *
        FROM l3_child_segment
        WHERE child_segment_id IN ({placeholders})
        """,
        tuple(unique_ids),
    ).fetchall()
    return {str(row["child_segment_id"]): row for row in rows}


def child_segments_for_title_hit(conn: sqlite3.Connection, title_hit: dict[str, Any], *, top_k: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT *
        FROM l3_child_segment
        WHERE chapter_num = ?
          AND chapter_id = ?
        ORDER BY scene_id ASC, segment_index_in_scene ASC, child_segment_id ASC
        LIMIT ?
        """,
        (int(title_hit["chapter_num"]), title_hit["chapter_id"], top_k),
    ).fetchall()
    return rows


def add_or_update_candidate(
    candidates: dict[tuple[str, str], dict[str, Any]],
    *,
    row: sqlite3.Row,
    query_id: str,
    query_text: str,
    source: str,
    title_score: float = 0.0,
    keyword_score_value: float = 0.0,
    vector_score: float = 0.0,
) -> None:
    key = (query_id, str(row["child_segment_id"]))
    candidate = candidates.setdefault(
        key,
        {
            "query_id": query_id,
            "query_text": query_text,
            "child_segment_id": row["child_segment_id"],
            "scene_id": str(row["scene_id"]),
            "chapter_id": row["chapter_id"],
            "chapter_num": int(row["chapter_num"]),
            "title_score": 0.0,
            "keyword_score": 0.0,
            "vector_score": 0.0,
            "recall_sources": [],
            "segment_text": row["segment_text"],
            "start_sentence_id": row["start_sentence_id"],
            "end_sentence_id": row["end_sentence_id"],
            "start_para_id": row["start_para_id"],
            "end_para_id": row["end_para_id"],
            "segment_text_hash": row["segment_text_hash"],
            "source_fingerprint": row["source_fingerprint"],
        },
    )
    candidate["title_score"] = max(float(candidate["title_score"]), float(title_score))
    candidate["keyword_score"] = max(float(candidate["keyword_score"]), float(keyword_score_value))
    candidate["vector_score"] = max(float(candidate["vector_score"]), float(vector_score))
    if source not in candidate["recall_sources"]:
        candidate["recall_sources"].append(source)


def child_segment_sentence_link_ok(conn: sqlite3.Connection, child_segment_id: str, row: sqlite3.Row | None = None) -> bool:
    link_rows = conn.execute(
        """
        SELECT sentence_id, para_id
        FROM l3_child_segment_sentence_link
        WHERE child_segment_id = ?
        ORDER BY position_in_segment ASC
        """,
        (child_segment_id,),
    ).fetchall()
    if not link_rows:
        return False
    if row is not None:
        sentence_ids = {str(item["sentence_id"]) for item in link_rows}
        para_ids = {str(item["para_id"]) for item in link_rows}
        if str(row["start_sentence_id"]) not in sentence_ids or str(row["end_sentence_id"]) not in sentence_ids:
            return False
        if str(row["start_para_id"]) not in para_ids or str(row["end_para_id"]) not in para_ids:
            return False
    if sqlite_object_available(conn, "v_l2_current_sentences"):
        linked_ids = [str(item["sentence_id"]) for item in link_rows]
        placeholders = ", ".join("?" for _ in linked_ids)
        count = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM v_l2_current_sentences
                WHERE sentence_id IN ({placeholders})
                """,
                tuple(linked_ids),
            ).fetchone()[0]
        )
        return count == len(set(linked_ids))
    return True


def merge_recall_results(
    conn: sqlite3.Connection,
    *,
    title_recall: list[dict[str, Any]],
    keyword_recall: list[dict[str, Any]],
    vector_recall: list[dict[str, Any]],
    top_k: int,
    title_weight: float,
    keyword_weight: float,
    vector_weight: float,
) -> list[dict[str, Any]]:
    candidates: dict[tuple[str, str], dict[str, Any]] = {}
    for hit in title_recall:
        for row in child_segments_for_title_hit(conn, hit, top_k=top_k):
            add_or_update_candidate(
                candidates,
                row=row,
                query_id=hit["query_id"],
                query_text=hit["query_text"],
                source="sqlite_title",
                title_score=float(hit["title_score"]),
            )
    direct_ids = [str(hit["child_segment_id"]) for hit in keyword_recall + vector_recall]
    rows = rows_by_child_id(conn, direct_ids)
    for hit in keyword_recall:
        row = rows.get(str(hit["child_segment_id"]))
        if row is None:
            continue
        add_or_update_candidate(
            candidates,
            row=row,
            query_id=hit["query_id"],
            query_text=hit["query_text"],
            source="sqlite_child_keyword",
            keyword_score_value=float(hit["keyword_score"]),
        )
    for hit in vector_recall:
        row = rows.get(str(hit["child_segment_id"]))
        if row is None:
            continue
        add_or_update_candidate(
            candidates,
            row=row,
            query_id=hit["query_id"],
            query_text=hit["query_text"],
            source="chroma_vector",
            vector_score=float(hit["vector_score"]),
        )

    by_query: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates.values():
        candidate["recall_sources"] = sorted(candidate["recall_sources"])
        candidate["hybrid_score"] = round(
            title_weight * float(candidate["title_score"])
            + keyword_weight * float(candidate["keyword_score"])
            + vector_weight * float(candidate["vector_score"]),
            6,
        )
        by_query.setdefault(candidate["query_id"], []).append(candidate)

    merged_hits: list[dict[str, Any]] = []
    for query_id in sorted(by_query):
        query_hits = sorted(
            by_query[query_id],
            key=lambda item: (
                -float(item["hybrid_score"]),
                int(item["chapter_num"]),
                str(item["scene_id"]),
                str(item["child_segment_id"]),
            ),
        )
        for rank, hit in enumerate(query_hits[:top_k], start=1):
            hit["rank"] = rank
            merged_hits.append(hit)
    return merged_hits


def validate_merged_hits(
    conn: sqlite3.Connection,
    *,
    merged_hits: list[dict[str, Any]],
    chroma_rows: dict[str, dict[str, Any]],
) -> tuple[int, int, int]:
    child_ids = [str(hit["child_segment_id"]) for hit in merged_hits]
    sqlite_rows = rows_by_child_id(conn, child_ids)
    missing_sqlite_child_count = 0
    missing_chroma_child_count = 0
    hash_mismatch_count = 0
    for hit in merged_hits:
        child_segment_id = str(hit["child_segment_id"])
        row = sqlite_rows.get(child_segment_id)
        if row is None:
            missing_sqlite_child_count += 1
            hit["sqlite_child_exists"] = False
            hit["hash_check_ok"] = False
            hit["sentence_link_backtrace_ok"] = False
            continue
        hit["sqlite_child_exists"] = True
        sqlite_hash_ok = child_builder.sha256_text(str(row["segment_text"])) == str(row["segment_text_hash"])
        chroma_payload = chroma_rows.get(child_segment_id)
        chroma_hash_ok = False
        if chroma_payload is None:
            missing_chroma_child_count += 1
        else:
            metadata = chroma_payload["metadata"]
            chroma_hash_ok = (
                str(metadata.get("segment_text_hash")) == str(row["segment_text_hash"])
                and str(metadata.get("child_segment_id")) == child_segment_id
                and str(chroma_payload["document"]) == str(row["segment_text"])
            )
        sentence_link_ok = child_segment_sentence_link_ok(conn, child_segment_id, row)
        hit["hash_check_ok"] = bool(sqlite_hash_ok and chroma_hash_ok)
        hit["chroma_child_exists"] = chroma_payload is not None
        hit["chroma_hash_check_ok"] = bool(chroma_hash_ok)
        hit["sentence_link_backtrace_ok"] = bool(sentence_link_ok)
        if not sqlite_hash_ok or not chroma_hash_ok:
            hash_mismatch_count += 1
    return hash_mismatch_count, missing_sqlite_child_count, missing_chroma_child_count


def direct_query_overlap(query_text: str, text: str) -> bool:
    query = normalize_query(query_text)
    if not query:
        return False
    if query in text:
        return True
    tokens = [token for token in query.replace("，", " ").replace("。", " ").split() if len(token) >= 2]
    return any(token in text for token in tokens)


def query_quality_summary(
    *,
    query: dict[str, str],
    merged_hits: list[dict[str, Any]],
    keyword_recall: list[dict[str, Any]],
    vector_recall: list[dict[str, Any]],
) -> dict[str, Any]:
    query_id = query["query_id"]
    query_text = query["query_text"]
    query_hits = [hit for hit in merged_hits if hit["query_id"] == query_id]
    keyword_hits = [hit for hit in keyword_recall if hit["query_id"] == query_id]
    vector_hits = [hit for hit in vector_recall if hit["query_id"] == query_id]
    keyword_ids = {str(hit["child_segment_id"]) for hit in keyword_hits}
    vector_ids = {str(hit["child_segment_id"]) for hit in vector_hits}
    overlap_count = len(keyword_ids & vector_ids)
    top1 = query_hits[0] if query_hits else None
    vector_top1 = vector_hits[0] if vector_hits else None
    top3_hits = query_hits[:3]
    top5_hits = query_hits[:5]
    if top1 is None:
        quality_label = "zero_hit"
        quality_notes = "no merged retrieval hit"
    elif "sqlite_child_keyword" in top1.get("recall_sources", []) and "chroma_vector" in top1.get("recall_sources", []):
        quality_label = "good"
        quality_notes = "top1 matched both keyword and vector routes"
    elif direct_query_overlap(query_text, str(top1.get("segment_text", ""))):
        quality_label = "good"
        quality_notes = "query text directly overlaps top1 snippet"
    elif any("sqlite_child_keyword" in hit.get("recall_sources", []) for hit in top3_hits):
        quality_label = "good"
        quality_notes = "top3 contains keyword-backed hit"
    elif vector_hits and bool(top1.get("hash_check_ok")) and bool(top1.get("sentence_link_backtrace_ok")):
        quality_label = "acceptable"
        quality_notes = "vector-backed hit has valid SQLite/L2/hash evidence"
    else:
        quality_label = "weak"
        quality_notes = "only weak vector evidence by rule"
    return {
        "query_id": query_id,
        "query_text": query_text,
        "top1_child_segment_id": top1.get("child_segment_id") if top1 else "",
        "top1_chapter_num": top1.get("chapter_num") if top1 else "",
        "top1_hybrid_score": top1.get("hybrid_score") if top1 else 0.0,
        "top1_recall_sources": top1.get("recall_sources", []) if top1 else [],
        "top1_snippet": snippet(str(top1.get("segment_text", ""))) if top1 else "",
        "top3_hit_count": len(top3_hits),
        "top5_hit_count": len(top5_hits),
        "keyword_hit_count": len(keyword_hits),
        "vector_hit_count": len(vector_hits),
        "vector_top1_child_segment_id": vector_top1.get("child_segment_id") if vector_top1 else "",
        "vector_top1_score": vector_top1.get("vector_score") if vector_top1 else 0.0,
        "vector_top1_chapter_num": vector_top1.get("chapter_num") if vector_top1 else "",
        "vector_top1_snippet": vector_top1.get("vector_snippet", "") if vector_top1 else "",
        "vector_top3_child_segment_ids": [str(hit["child_segment_id"]) for hit in vector_hits[:3]],
        "vector_top5_child_segment_ids": [str(hit["child_segment_id"]) for hit in vector_hits[:5]],
        "keyword_vector_overlap_count": overlap_count,
        "hybrid_top1_recall_sources": top1.get("recall_sources", []) if top1 else [],
        "zero_hit": top1 is None,
        "quality_label": quality_label,
        "quality_notes": quality_notes,
    }


def build_quality_summary(
    *,
    queries: list[dict[str, str]],
    merged_hits: list[dict[str, Any]],
    keyword_recall: list[dict[str, Any]],
    vector_recall: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [
        query_quality_summary(
            query=query,
            merged_hits=merged_hits,
            keyword_recall=keyword_recall,
            vector_recall=vector_recall,
        )
        for query in queries
    ]


def build_retrieval_run_id(
    *,
    scope: list[int],
    queries: list[dict[str, str]],
    collection_name: str,
    source_guard_hash: str,
    child_segment_fingerprint: str,
    collection_fingerprint: str,
) -> str:
    return "l3hr_" + child_builder.sha256_json(
        {
            "scope": scope,
            "queries": queries,
            "collection_name": collection_name,
            "source_guard_hash": source_guard_hash,
            "child_segment_fingerprint": child_segment_fingerprint,
            "collection_fingerprint": collection_fingerprint,
        }
    )[:24]


def results_payload(result: RetrievalResult) -> dict[str, Any]:
    return {
        "retrieval_run_id": result.retrieval_run_id,
        "collection_name": result.collection_name,
        "embedding_provider": result.embedding_provider,
        "embedding_model": result.embedding_model,
        "embedding_mode": result.embedding_mode,
        "chapter_scope": result.chapter_scope,
        "queries": result.queries,
        "recall": {
            "sqlite_title": result.title_recall,
            "sqlite_child_keyword": result.keyword_recall,
            "chroma_vector": result.vector_recall,
        },
        "merged_hits": result.merged_hits,
        "quality_summary": result.quality_summary,
        "errors": result.errors,
        "warnings": result.warnings,
    }


def write_results_json(project_dir: Path, result: RetrievalResult, *, output_prefix: str = DEFAULT_OUTPUT_PREFIX) -> None:
    path = project_dir / relative_paths_for_prefix(output_prefix)["results_json"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results_payload(result), ensure_ascii=False, indent=2), encoding="utf-8")


def write_results_csv(project_dir: Path, result: RetrievalResult, *, output_prefix: str = DEFAULT_OUTPUT_PREFIX) -> None:
    path = project_dir / relative_paths_for_prefix(output_prefix)["results_csv"]
    path.parent.mkdir(parents=True, exist_ok=True)
    if result.quality_summary:
        fieldnames = [
            "query_id",
            "query_text",
            "top1_child_segment_id",
            "top1_chapter_num",
            "top1_hybrid_score",
            "top1_recall_sources",
            "top1_snippet",
            "top3_hit_count",
            "top5_hit_count",
            "keyword_hit_count",
            "vector_hit_count",
            "vector_top1_child_segment_id",
            "vector_top1_score",
            "vector_top1_chapter_num",
            "vector_top1_snippet",
            "vector_top3_child_segment_ids",
            "vector_top5_child_segment_ids",
            "keyword_vector_overlap_count",
            "hybrid_top1_recall_sources",
            "zero_hit",
            "quality_label",
            "quality_notes",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            for summary in result.quality_summary:
                row = {key: summary.get(key, "") for key in fieldnames}
                for key in ("top1_recall_sources", "vector_top3_child_segment_ids", "vector_top5_child_segment_ids", "hybrid_top1_recall_sources"):
                    row[key] = json.dumps(summary.get(key, []), ensure_ascii=False)
                writer.writerow(row)
        return
    fieldnames = [
        "query_id",
        "query_text",
        "rank",
        "child_segment_id",
        "scene_id",
        "chapter_id",
        "chapter_num",
        "hybrid_score",
        "title_score",
        "keyword_score",
        "vector_score",
        "recall_sources",
        "start_sentence_id",
        "end_sentence_id",
        "start_para_id",
        "end_para_id",
        "segment_text_hash",
        "source_fingerprint",
        "hash_check_ok",
        "sentence_link_backtrace_ok",
        "segment_text",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for hit in result.merged_hits:
            row = {key: hit.get(key, "") for key in fieldnames}
            row["recall_sources"] = json.dumps(hit.get("recall_sources", []), ensure_ascii=False)
            writer.writerow(row)


def manifest_payload(
    result: RetrievalResult,
    *,
    created_at: str,
    source_guard_before_hash: str,
    source_guard_after_hash: str,
    child_segment_fingerprint_before: str,
    child_segment_fingerprint_after: str,
    collection_fingerprint_before: str,
    collection_fingerprint_after: str,
    source_table_names: list[str],
    top_k: int,
    title_weight: float,
    keyword_weight: float,
    vector_weight: float,
) -> dict[str, Any]:
    return {
        "retrieval_run_id": result.retrieval_run_id,
        "collection_name": result.collection_name,
        "embedding_provider": result.embedding_provider,
        "embedding_model": result.embedding_model,
        "embedding_mode": result.embedding_mode,
        "embedding_dimension": result.embedding_dimension,
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
        "hash_mismatch_count": result.hash_mismatch_count,
        "missing_sqlite_child_count": result.missing_sqlite_child_count,
        "missing_chroma_child_count": result.missing_chroma_child_count,
        "source_mutation_detected": result.source_mutation_detected,
        "child_segment_mutation_detected": result.child_segment_mutation_detected,
        "chroma_mutation_detected": result.chroma_mutation_detected,
        "fake_collection_mutation_detected": result.fake_collection_mutation_detected,
        "real_collection_mutation_detected": result.real_collection_mutation_detected,
        "forbidden_final_table_count": result.forbidden_final_table_count,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "created_at": created_at,
        "chroma_dir": str(result.chroma_dir),
        "source_guard_before_hash": source_guard_before_hash,
        "source_guard_after_hash": source_guard_after_hash,
        "child_segment_fingerprint_before": child_segment_fingerprint_before,
        "child_segment_fingerprint_after": child_segment_fingerprint_after,
        "collection_fingerprint_before": collection_fingerprint_before,
        "collection_fingerprint_after": collection_fingerprint_after,
        "source_table_names": source_table_names,
        "top_k": top_k,
        "weights": {
            "title": title_weight,
            "keyword": keyword_weight,
            "vector": vector_weight,
        },
        "no_llm_calls": True,
        "answer_generation": False,
        "storyboard_generation": False,
        "sqlite_read_only": True,
        "chroma_write_mode": False,
    }


def write_manifest(project_dir: Path, payload: dict[str, Any], *, output_prefix: str = DEFAULT_OUTPUT_PREFIX) -> None:
    path = project_dir / relative_paths_for_prefix(output_prefix)["manifest"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(project_dir: Path, manifest: dict[str, Any], results: dict[str, Any], *, output_prefix: str = DEFAULT_OUTPUT_PREFIX) -> None:
    lines = [
        "# L3.7b real embedding retrieval quality report" if manifest.get("embedding_mode") == sync.REAL_EMBEDDING_MODE else "# L3.7 Hybrid RAG retrieval report",
        "",
        f"- created_at: {manifest['created_at']}",
        f"- retrieval_run_id: {manifest['retrieval_run_id']}",
        f"- collection_name: {manifest['collection_name']}",
        f"- embedding_model: {manifest.get('embedding_model', '')}",
        f"- embedding_mode: {manifest['embedding_mode']}",
        f"- embedding_dimension: {manifest.get('embedding_dimension', 0)}",
        f"- chapter_scope: {','.join(str(item) for item in manifest['chapter_scope'])}",
        f"- query_count: {manifest['query_count']}",
        f"- sqlite_title_hit_count: {manifest['sqlite_title_hit_count']}",
        f"- sqlite_child_keyword_hit_count: {manifest['sqlite_child_keyword_hit_count']}",
        f"- chroma_vector_hit_count: {manifest['chroma_vector_hit_count']}",
        f"- merged_hit_count: {manifest['merged_hit_count']}",
        f"- zero_hit_query_count: {manifest['zero_hit_query_count']}",
        f"- good_query_count: {manifest.get('good_query_count', 0)}",
        f"- acceptable_query_count: {manifest.get('acceptable_query_count', 0)}",
        f"- weak_query_count: {manifest.get('weak_query_count', 0)}",
        f"- keyword_vector_overlap_total: {manifest.get('keyword_vector_overlap_total', 0)}",
        f"- hash_mismatch_count: {manifest['hash_mismatch_count']}",
        f"- missing_sqlite_child_count: {manifest['missing_sqlite_child_count']}",
        f"- missing_chroma_child_count: {manifest['missing_chroma_child_count']}",
        f"- source_mutation_detected: {manifest['source_mutation_detected']}",
        f"- child_segment_mutation_detected: {manifest['child_segment_mutation_detected']}",
        f"- chroma_mutation_detected: {manifest['chroma_mutation_detected']}",
        f"- fake_collection_mutation_detected: {manifest.get('fake_collection_mutation_detected', False)}",
        f"- real_collection_mutation_detected: {manifest.get('real_collection_mutation_detected', False)}",
        f"- forbidden_final_table_count: {manifest['forbidden_final_table_count']}",
        f"- error_count: {manifest['error_count']}",
        f"- warning_count: {manifest['warning_count']}",
        "",
        "## Query Summary",
        "",
    ]
    merged_hits = results.get("merged_hits", [])
    for query in results.get("queries", []):
        query_hits = [hit for hit in merged_hits if hit["query_id"] == query["query_id"]]
        top_hit = query_hits[0]["child_segment_id"] if query_hits else "none"
        lines.append(f"- {query['query_id']} `{query['query_text']}`: hits={len(query_hits)}, top_child_segment_id={top_hit}")
    if results.get("quality_summary"):
        lines.extend(["", "## Quality Summary", ""])
        for summary in results["quality_summary"]:
            lines.append(
                "- {query_id} `{query_text}`: label={quality_label}, top1={top1_child_segment_id}, "
                "vector_top1={vector_top1_child_segment_id}, overlap={keyword_vector_overlap_count}, notes={quality_notes}".format(
                    **summary
                )
            )
    lines.extend(
        [
            "",
            "## Warnings",
            "",
            *(f"- {warning}" for warning in manifest.get("warnings", [])),
            *(["- none"] if not manifest.get("warnings") else []),
            "",
            "## Errors",
            "",
            *(f"- {error}" for error in manifest.get("errors", [])),
            *(["- none"] if not manifest.get("errors") else []),
            "",
            "No LLM calls. No answer generation. No storyboard generation. No Chroma writes.",
            "",
        ]
    )
    path = project_dir / relative_paths_for_prefix(output_prefix)["report"]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def run_retrieval(
    project_dir: Path | str,
    *,
    query: str | None = None,
    sample_chapters: str | None = child_builder.DEFAULT_SAMPLE_CHAPTERS,
    chapter_num: int | None = None,
    collection_name: str = sync.DEFAULT_COLLECTION_NAME,
    embedding_provider: str = sync.DEFAULT_EMBEDDING_PROVIDER,
    embedding_model: str = sync.DEFAULT_EMBEDDING_MODEL,
    embedding_mode: str = sync.DEFAULT_EMBEDDING_MODE,
    chroma_dir: Path | str | None = None,
    top_k: int = DEFAULT_TOP_K,
    title_weight: float = DEFAULT_TITLE_WEIGHT,
    keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    read_only: bool = False,
    expect_embedding_mode: str | None = None,
    expect_embedding_model: str | None = None,
    real_quality_report: bool = False,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
) -> RetrievalResult:
    root = Path(project_dir).resolve()
    child_builder.ensure_dirs(root)
    db_path = root / child_builder.DB_RELATIVE_PATH
    resolved_chroma_dir = sync.resolve_chroma_dir(root, chroma_dir)
    scope: list[int] = []
    result = RetrievalResult(
        False,
        root,
        db_path,
        resolved_chroma_dir,
        collection_name,
        scope,
        embedding_provider,
        embedding_model,
        embedding_mode,
    )
    created_at = now_iso()
    source_guard_before_hash = ""
    source_guard_after_hash = ""
    child_segment_fingerprint_before = ""
    child_segment_fingerprint_after = ""
    collection_fingerprint_before = ""
    collection_fingerprint_after = ""
    fake_collection_fingerprint_before = ""
    fake_collection_fingerprint_after = ""
    real_collection_fingerprint_before = ""
    real_collection_fingerprint_after = ""
    source_table_names: list[str] = []
    try:
        if top_k <= 0:
            raise ValueError("--top-k must be positive")
        scope = child_builder.parse_scope(sample_chapters, chapter_num)
        result.chapter_scope = scope
        if expect_embedding_mode is not None:
            embedding_mode = expect_embedding_mode
            result.embedding_mode = embedding_mode
        if expect_embedding_model is not None:
            embedding_model = expect_embedding_model
            result.embedding_model = embedding_model
        if embedding_mode == sync.REAL_EMBEDDING_MODE and embedding_provider == sync.DEFAULT_EMBEDDING_PROVIDER:
            embedding_provider = sync.REAL_EMBEDDING_PROVIDER
            result.embedding_provider = embedding_provider
        sync.verify_embedding_configuration(
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            embedding_mode=embedding_mode,
        )
        if not db_path.exists():
            raise RuntimeError("SQLite database does not exist")

        queries = query_specs(query, real_quality_report=real_quality_report)
        result.queries = queries
        result.query_count = len(queries)

        conn = connect_sqlite_readonly(db_path)
        try:
            validate_sqlite_inputs(conn, result)
            source_table_names = child_builder.discover_source_table_names(conn)
            source_guard_before_hash = child_builder.guard_hash(child_builder.collect_source_guard_stats(conn, source_table_names))
            child_segment_fingerprint_before = child_builder.stable_row_fingerprint(conn, scope)
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
            if result.forbidden_final_table_count:
                result.errors.append("forbidden final tables exist before retrieval")
        finally:
            conn.close()

        client = chromadb.PersistentClient(path=str(resolved_chroma_dir))
        try:
            try:
                collection = client.get_collection(collection_name)
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"Chroma collection missing: {collection_name}") from exc
            fake_collection_fingerprint_before = sync.existing_collection_fingerprint(client, sync.DEFAULT_COLLECTION_NAME)
            real_collection_fingerprint_before = sync.existing_collection_fingerprint(client, sync.DEFAULT_REAL_COLLECTION_NAME)
            chroma_rows_before = sync.fetch_collection_rows(collection)
            collection_fingerprint_before = sync.collection_fingerprint(chroma_rows_before)
            if chroma_rows_before:
                first_payload = next(iter(chroma_rows_before.values()))
                result.embedding_dimension = len(first_payload["embedding"])
                metadata = first_payload["metadata"]
                stored_mode = str(metadata.get("embedding_mode", ""))
                stored_model = str(metadata.get("embedding_model", ""))
                stored_provider = str(metadata.get("embedding_provider", ""))
                if stored_mode != embedding_mode:
                    result.errors.append(f"embedding_mode mismatch: expected {embedding_mode}, got {stored_mode}")
                if expect_embedding_model is not None and stored_model != expect_embedding_model:
                    result.errors.append(f"embedding_model mismatch: expected {expect_embedding_model}, got {stored_model}")
                if stored_provider:
                    result.embedding_provider = stored_provider
            query_encoder = QueryEmbeddingEncoder(
                embedding_provider=result.embedding_provider,
                embedding_model=result.embedding_model,
                embedding_mode=result.embedding_mode,
            )

            conn = connect_sqlite_readonly(db_path)
            try:
                for spec in queries:
                    title_hits = sqlite_title_recall(
                        conn,
                        query_id=spec["query_id"],
                        query_text=spec["query_text"],
                        scope=scope,
                        top_k=top_k,
                    )
                    keyword_hits = sqlite_child_keyword_recall(
                        conn,
                        query_id=spec["query_id"],
                        query_text=spec["query_text"],
                        scope=scope,
                        top_k=top_k,
                    )
                    vector_hits = chroma_vector_recall(
                        collection,
                        query_id=spec["query_id"],
                        query_text=spec["query_text"],
                        scope=scope,
                        top_k=top_k,
                        query_encoder=query_encoder,
                    )
                    result.title_recall.extend(title_hits)
                    result.keyword_recall.extend(keyword_hits)
                    result.vector_recall.extend(vector_hits)
                result.merged_hits = merge_recall_results(
                    conn,
                    title_recall=result.title_recall,
                    keyword_recall=result.keyword_recall,
                    vector_recall=result.vector_recall,
                    top_k=top_k,
                    title_weight=title_weight,
                    keyword_weight=keyword_weight,
                    vector_weight=vector_weight,
                )
                chroma_rows_after_query = sync.fetch_collection_rows(collection)
                (
                    result.hash_mismatch_count,
                    result.missing_sqlite_child_count,
                    result.missing_chroma_child_count,
                ) = validate_merged_hits(conn, merged_hits=result.merged_hits, chroma_rows=chroma_rows_after_query)
                hit_query_ids = {hit["query_id"] for hit in result.merged_hits}
                for spec in queries:
                    if spec["query_id"] not in hit_query_ids:
                        result.zero_hit_query_count += 1
                        result.warnings.append(f"zero_hit query: {spec['query_id']} {spec['query_text']}")
            finally:
                conn.close()

            chroma_rows_after = sync.fetch_collection_rows(collection)
            collection_fingerprint_after = sync.collection_fingerprint(chroma_rows_after)
            fake_collection_fingerprint_after = sync.existing_collection_fingerprint(client, sync.DEFAULT_COLLECTION_NAME)
            real_collection_fingerprint_after = sync.existing_collection_fingerprint(client, sync.DEFAULT_REAL_COLLECTION_NAME)
        finally:
            sync.close_client(client)

        conn = connect_sqlite_readonly(db_path)
        try:
            source_guard_after_hash = child_builder.guard_hash(child_builder.collect_source_guard_stats(conn, source_table_names))
            child_segment_fingerprint_after = child_builder.stable_row_fingerprint(conn, scope)
            result.source_mutation_detected = source_guard_before_hash != source_guard_after_hash
            result.child_segment_mutation_detected = child_segment_fingerprint_before != child_segment_fingerprint_after
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
        finally:
            conn.close()
        result.chroma_mutation_detected = collection_fingerprint_before != collection_fingerprint_after
        result.fake_collection_mutation_detected = fake_collection_fingerprint_before != fake_collection_fingerprint_after
        result.real_collection_mutation_detected = real_collection_fingerprint_before != real_collection_fingerprint_after
        result.sqlite_title_hit_count = len(result.title_recall)
        result.sqlite_child_keyword_hit_count = len(result.keyword_recall)
        result.chroma_vector_hit_count = len(result.vector_recall)
        result.merged_hit_count = len(result.merged_hits)
        if real_quality_report:
            result.quality_summary = build_quality_summary(
                queries=result.queries,
                merged_hits=result.merged_hits,
                keyword_recall=result.keyword_recall,
                vector_recall=result.vector_recall,
            )
            result.good_query_count = sum(1 for item in result.quality_summary if item["quality_label"] == "good")
            result.acceptable_query_count = sum(1 for item in result.quality_summary if item["quality_label"] == "acceptable")
            result.weak_query_count = sum(1 for item in result.quality_summary if item["quality_label"] == "weak")
            result.keyword_vector_overlap_total = sum(int(item["keyword_vector_overlap_count"]) for item in result.quality_summary)
        result.retrieval_run_id = build_retrieval_run_id(
            scope=scope,
            queries=queries,
            collection_name=collection_name,
            source_guard_hash=source_guard_before_hash,
            child_segment_fingerprint=child_segment_fingerprint_before,
            collection_fingerprint=collection_fingerprint_before,
        )

        if result.hash_mismatch_count:
            result.errors.append("hash mismatch detected in retrieval evidence")
        if result.missing_sqlite_child_count:
            result.errors.append("retrieval hit references missing SQLite child_segment")
        if result.missing_chroma_child_count:
            result.errors.append("retrieval hit references missing Chroma child_segment")
        if result.source_mutation_detected:
            result.errors.append("source table mutation detected during L3.7 retrieval")
        if result.child_segment_mutation_detected:
            result.errors.append("l3_child_segment source rows mutated during L3.7 retrieval")
        if result.chroma_mutation_detected:
            result.errors.append("Chroma collection mutation detected during L3.7 retrieval")
        if result.fake_collection_mutation_detected:
            result.errors.append("fake Chroma collection mutation detected during L3.7 retrieval")
        if result.real_collection_mutation_detected:
            result.errors.append("real Chroma collection mutation detected during L3.7 retrieval")
        if result.forbidden_final_table_count:
            result.errors.append("forbidden final tables exist after retrieval")
        result.error_count = len(result.errors)
        result.warning_count = len(result.warnings)
        result.ok = result.error_count == 0

        manifest = manifest_payload(
            result,
            created_at=created_at,
            source_guard_before_hash=source_guard_before_hash,
            source_guard_after_hash=source_guard_after_hash,
            child_segment_fingerprint_before=child_segment_fingerprint_before,
            child_segment_fingerprint_after=child_segment_fingerprint_after,
            collection_fingerprint_before=collection_fingerprint_before,
            collection_fingerprint_after=collection_fingerprint_after,
            source_table_names=source_table_names,
            top_k=top_k,
            title_weight=title_weight,
            keyword_weight=keyword_weight,
            vector_weight=vector_weight,
        )
        manifest["fake_collection_name"] = sync.DEFAULT_COLLECTION_NAME
        manifest["real_collection_name"] = sync.DEFAULT_REAL_COLLECTION_NAME
        manifest["fake_collection_fingerprint_before"] = fake_collection_fingerprint_before
        manifest["fake_collection_fingerprint_after"] = fake_collection_fingerprint_after
        manifest["real_collection_fingerprint_before"] = real_collection_fingerprint_before
        manifest["real_collection_fingerprint_after"] = real_collection_fingerprint_after
        write_results_json(root, result, output_prefix=output_prefix)
        write_results_csv(root, result, output_prefix=output_prefix)
        write_manifest(root, manifest, output_prefix=output_prefix)
        write_report(root, manifest, results_payload(result), output_prefix=output_prefix)
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        result.error_count = len(result.errors)
        result.warning_count = len(result.warnings)
        result.ok = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run L3.7 hybrid RAG retrieval over sample child_segments.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--query", type=str, default=None)
    parser.add_argument("--sample-chapters", type=str, default=child_builder.DEFAULT_SAMPLE_CHAPTERS)
    parser.add_argument("--chapter-num", type=int, default=None)
    parser.add_argument("--collection-name", type=str, default=sync.DEFAULT_COLLECTION_NAME)
    parser.add_argument("--embedding-provider", type=str, default=sync.DEFAULT_EMBEDDING_PROVIDER)
    parser.add_argument("--embedding-model", type=str, default=sync.DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-mode", type=str, default=sync.DEFAULT_EMBEDDING_MODE)
    parser.add_argument("--chroma-dir", type=Path, default=None)
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--title-weight", type=float, default=DEFAULT_TITLE_WEIGHT)
    parser.add_argument("--keyword-weight", type=float, default=DEFAULT_KEYWORD_WEIGHT)
    parser.add_argument("--vector-weight", type=float, default=DEFAULT_VECTOR_WEIGHT)
    parser.add_argument("--read-only", action="store_true")
    parser.add_argument("--expect-embedding-mode", type=str, default=None)
    parser.add_argument("--expect-embedding-model", type=str, default=None)
    parser.add_argument("--real-quality-report", action="store_true")
    parser.add_argument("--output-prefix", type=str, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    result = run_retrieval(
        args.project_dir,
        query=args.query,
        sample_chapters=args.sample_chapters,
        chapter_num=args.chapter_num,
        collection_name=args.collection_name,
        embedding_provider=args.embedding_provider,
        embedding_model=args.embedding_model,
        embedding_mode=args.embedding_mode,
        chroma_dir=args.chroma_dir,
        top_k=args.top_k,
        title_weight=args.title_weight,
        keyword_weight=args.keyword_weight,
        vector_weight=args.vector_weight,
        read_only=args.read_only,
        expect_embedding_mode=args.expect_embedding_mode,
        expect_embedding_model=args.expect_embedding_model,
        real_quality_report=args.real_quality_report,
        output_prefix=args.output_prefix,
    )
    if result.embedding_mode == sync.REAL_EMBEDDING_MODE and args.real_quality_report:
        print("L3.7b real embedding retrieval quality PASS" if result.ok else "L3.7b real embedding retrieval quality FAIL")
    else:
        print("L3.7 Hybrid RAG retrieval PASS" if result.ok else "L3.7 Hybrid RAG retrieval FAIL")
    if result.ok:
        print(f"collection_name={result.collection_name}")
        print(f"merged_hit_count={result.merged_hit_count}")
        print(f"embedding_dimension={result.embedding_dimension}")
    else:
        for error in result.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
