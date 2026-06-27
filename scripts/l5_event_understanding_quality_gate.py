from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import object_exists


CREATED_AT = "1970-01-01T00:00:00"
MANIFEST_JSON = "l5_event_quality_gate_manifest.json"

L5_9_TABLES = (
    "l5_event_quality_gate_summary",
    "l5_event_quality_gate_chain",
    "l5_event_quality_gate_issue",
    "l5_event_quality_gate_metric",
    "l5_event_quality_gate_run",
)
FORBIDDEN_FINAL_TABLES = {
    "final_event",
    "final_timeline",
    "final_relationship_graph",
    "final_state_machine",
    "l5_final_event",
    "l5_final_timeline",
    "l5_final_relationship_graph",
    "l5_final_state_machine",
}
CORE_REQUIRED_TABLES = (
    "l5_confirmed_event_candidate",
    "l5_event_merge_group_candidate",
    "l5_event_merge_group_member",
    "l5_timeline_anchor_candidate",
    "l5_relationship_impact_candidate",
    "l5_state_impact_candidate",
)
INPUT_TABLES = (
    "l5_normalized_event_candidate",
    "l5_normalized_event_argument",
    "l5_normalized_event_argument_candidate",
    "l5_normalized_event_evidence",
    "l5_normalized_event_evidence_span",
    "l5_review_decision_import",
    "l5_review_decision_current",
    "l5_review_decision_conflict_audit",
    "l5_review_decision_run",
    "l5_confirmed_event_candidate",
    "l5_confirmed_event_argument_candidate",
    "l5_confirmed_event_evidence_span",
    "l5_confirmed_event_blocked_audit",
    "l5_confirmed_event_candidate_run",
    "l5_event_merge_group_candidate",
    "l5_event_merge_group_member",
    "l5_event_merge_group_evidence",
    "l5_event_merge_group_audit",
    "l5_event_merge_group_run",
    "l5_timeline_anchor_candidate",
    "l5_timeline_relative_order_candidate",
    "l5_timeline_anchor_audit",
    "l5_timeline_anchor_run",
    "l5_relationship_impact_candidate",
    "l5_state_impact_candidate",
    "l5_impact_candidate_evidence",
    "l5_relationship_state_impact_audit",
    "l5_relationship_state_impact_run",
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__type__": "bytes", "hex": value.hex()}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not object_exists(conn, table, "table"):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")}


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not object_exists(conn, table, "table"):
        return []
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
    order_sql = ", ".join(sql_identifier(column) for column in columns)
    return [dict(row) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]


def table_fingerprints(conn: sqlite3.Connection, tables: tuple[str, ...]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for table in tables:
        if not object_exists(conn, table, "table"):
            continue
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        result[table] = {"row_count": len(rows), "columns": columns, "aggregate_hash": sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))}
    return result


def compatible_rows(conn: sqlite3.Connection, *table_names: str) -> list[dict[str, Any]]:
    for table in table_names:
        if object_exists(conn, table, "table"):
            return fetch_rows(conn, table)
    return []


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l5_event_quality_gate_summary (
            quality_gate_run_id TEXT PRIMARY KEY,
            normalized_event_count INTEGER NOT NULL,
            review_current_count INTEGER NOT NULL,
            confirmed_event_candidate_count INTEGER NOT NULL,
            merge_group_candidate_count INTEGER NOT NULL,
            timeline_anchor_candidate_count INTEGER NOT NULL,
            relationship_impact_candidate_count INTEGER NOT NULL,
            state_impact_candidate_count INTEGER NOT NULL,
            singleton_group_count INTEGER NOT NULL,
            multi_member_group_count INTEGER NOT NULL,
            fallback_anchor_count INTEGER NOT NULL,
            relationship_missing_count INTEGER NOT NULL,
            state_missing_count INTEGER NOT NULL,
            full_chain_complete_count INTEGER NOT NULL,
            partial_chain_count INTEGER NOT NULL,
            blocked_chain_count INTEGER NOT NULL,
            source_mutation_detected INTEGER NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            quality_gate_status TEXT NOT NULL,
            run_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_quality_gate_chain (
            chain_id TEXT PRIMARY KEY,
            normalized_event_id TEXT,
            confirmed_event_candidate_id TEXT,
            merge_group_candidate_id TEXT,
            timeline_anchor_candidate_id TEXT,
            has_review_decision INTEGER NOT NULL,
            review_decision TEXT,
            has_confirmed_candidate INTEGER NOT NULL,
            has_merge_group INTEGER NOT NULL,
            has_timeline_anchor INTEGER NOT NULL,
            has_relationship_impact INTEGER NOT NULL,
            has_state_impact INTEGER NOT NULL,
            is_singleton_group INTEGER NOT NULL,
            is_fallback_anchor INTEGER NOT NULL,
            chain_status TEXT NOT NULL,
            readiness_status TEXT NOT NULL,
            quality_score REAL,
            chain_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_quality_gate_issue (
            issue_id TEXT PRIMARY KEY,
            issue_scope TEXT NOT NULL,
            normalized_event_id TEXT,
            confirmed_event_candidate_id TEXT,
            merge_group_candidate_id TEXT,
            timeline_anchor_candidate_id TEXT,
            issue_type TEXT NOT NULL,
            issue_severity TEXT NOT NULL,
            description TEXT NOT NULL,
            recommended_action TEXT,
            issue_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_quality_gate_metric (
            metric_id TEXT PRIMARY KEY,
            metric_name TEXT NOT NULL,
            metric_value TEXT NOT NULL,
            metric_group TEXT NOT NULL,
            metric_description TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_event_quality_gate_run (
            run_id TEXT PRIMARY KEY,
            l5_3_fingerprint TEXT,
            l5_4_fingerprint TEXT,
            l5_5_fingerprint TEXT,
            l5_6_fingerprint TEXT,
            l5_7_fingerprint TEXT,
            l5_8_fingerprint TEXT,
            source_mutation_detected INTEGER NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            forbidden_final_table_count INTEGER NOT NULL,
            quality_gate_status TEXT NOT NULL,
            error_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL,
            info_count INTEGER NOT NULL,
            run_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )


def clear_l5_9_tables(conn: sqlite3.Connection) -> None:
    for table in reversed(L5_9_TABLES):
        conn.execute(f"DELETE FROM {table}")


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(
        f"INSERT INTO {sql_identifier(table)} ({', '.join(sql_identifier(column) for column in columns)}) VALUES ({placeholders})",
        [tuple(row.get(column) for column in columns) for row in rows],
    )


def row_value(row: dict[str, Any] | None, *names: str, default: Any = "") -> Any:
    if not row:
        return default
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return default


def add_issue(
    issues: list[dict[str, Any]],
    *,
    issue_scope: str,
    issue_type: str,
    issue_severity: str,
    description: str,
    recommended_action: str = "",
    normalized_event_id: str = "",
    confirmed_event_candidate_id: str = "",
    merge_group_candidate_id: str = "",
    timeline_anchor_candidate_id: str = "",
) -> None:
    issue_hash = sha256_text(json.dumps([issue_scope, normalized_event_id, confirmed_event_candidate_id, merge_group_candidate_id, timeline_anchor_candidate_id, issue_type, issue_severity, description], ensure_ascii=False, separators=(",", ":")))
    issues.append(
        {
            "issue_id": stable_id("l5q_issue", issue_hash),
            "issue_scope": issue_scope,
            "normalized_event_id": normalized_event_id,
            "confirmed_event_candidate_id": confirmed_event_candidate_id,
            "merge_group_candidate_id": merge_group_candidate_id,
            "timeline_anchor_candidate_id": timeline_anchor_candidate_id,
            "issue_type": issue_type,
            "issue_severity": issue_severity,
            "description": description,
            "recommended_action": recommended_action,
            "issue_hash": issue_hash,
            "created_at": CREATED_AT,
        }
    )


def upstream_mutation_flag(rows: list[dict[str, Any]]) -> bool:
    return any(int(row.get("source_mutation_detected") or 0) != 0 or int(row.get("input_mutation_detected") or 0) != 0 for row in rows)


def build_quality_rows(conn: sqlite3.Connection, source_mutation: bool, input_mutation: bool) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    normalized_events = compatible_rows(conn, "l5_normalized_event_candidate")
    review_current = fetch_rows(conn, "l5_review_decision_current")
    review_runs = fetch_rows(conn, "l5_review_decision_run")
    confirmed = fetch_rows(conn, "l5_confirmed_event_candidate")
    confirmed_evidence = fetch_rows(conn, "l5_confirmed_event_evidence_span")
    confirmed_runs = fetch_rows(conn, "l5_confirmed_event_candidate_run")
    groups = fetch_rows(conn, "l5_event_merge_group_candidate")
    members = fetch_rows(conn, "l5_event_merge_group_member")
    merge_runs = fetch_rows(conn, "l5_event_merge_group_run")
    anchors = fetch_rows(conn, "l5_timeline_anchor_candidate")
    relative_orders = fetch_rows(conn, "l5_timeline_relative_order_candidate")
    anchor_runs = fetch_rows(conn, "l5_timeline_anchor_run")
    relationships = fetch_rows(conn, "l5_relationship_impact_candidate")
    states = fetch_rows(conn, "l5_state_impact_candidate")
    impact_runs = fetch_rows(conn, "l5_relationship_state_impact_run")

    issues: list[dict[str, Any]] = []
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    forbidden_tables = sorted(FORBIDDEN_FINAL_TABLES.intersection(tables))
    for table in CORE_REQUIRED_TABLES:
        if not object_exists(conn, table, "table"):
            add_issue(issues, issue_scope="source_integrity", issue_type="weak_chain_completeness", issue_severity="error", description=f"missing required table {table}", recommended_action="rebuild prerequisite L5 layer")
    if forbidden_tables:
        add_issue(issues, issue_scope="source_integrity", issue_type="forbidden_final_table_detected", issue_severity="error", description=f"forbidden final tables exist: {', '.join(forbidden_tables)}", recommended_action="remove final fact tables before L5.9")

    source_mutation = source_mutation or upstream_mutation_flag(review_runs + confirmed_runs + merge_runs + anchor_runs + impact_runs)
    input_mutation = input_mutation or upstream_mutation_flag(review_runs + confirmed_runs + merge_runs + anchor_runs + impact_runs)
    if source_mutation:
        add_issue(issues, issue_scope="source_integrity", issue_type="source_mutation_detected", issue_severity="error", description="source mutation flag detected", recommended_action="rerun source layer and inspect mutation")
    if input_mutation:
        add_issue(issues, issue_scope="source_integrity", issue_type="input_mutation_detected", issue_severity="error", description="input mutation flag detected", recommended_action="rerun input layer and inspect mutation")

    normalized_id_key = "normalized_event_candidate_id"
    normalized_ids = {str(row.get(normalized_id_key) or row.get("normalized_event_id") or "") for row in normalized_events}
    normalized_ids.discard("")
    review_by_norm = {str(row.get("normalized_event_id") or ""): row for row in review_current if row.get("normalized_event_id")}
    confirmed_by_norm = {str(row.get("normalized_event_id") or ""): row for row in confirmed if row.get("normalized_event_id")}
    member_by_confirmed = {str(row.get("confirmed_event_candidate_id") or ""): row for row in members if row.get("confirmed_event_candidate_id")}
    group_by_id = {str(row.get("merge_group_candidate_id") or ""): row for row in groups if row.get("merge_group_candidate_id")}
    anchor_by_group = {str(row.get("merge_group_candidate_id") or ""): row for row in anchors if row.get("merge_group_candidate_id")}
    rel_by_confirmed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    state_by_confirmed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    evidence_by_confirmed: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in relationships:
        rel_by_confirmed[str(row.get("source_confirmed_event_candidate_id") or "")].append(row)
    for row in states:
        state_by_confirmed[str(row.get("source_confirmed_event_candidate_id") or "")].append(row)
    for row in confirmed_evidence:
        evidence_by_confirmed[str(row.get("confirmed_event_candidate_id") or "")].append(row)
    all_norm_ids = set(normalized_ids).union(review_by_norm).union(confirmed_by_norm)

    chain_rows: list[dict[str, Any]] = []
    for normalized_id in sorted(all_norm_ids):
        review = review_by_norm.get(normalized_id)
        confirmed_row = confirmed_by_norm.get(normalized_id)
        confirmed_id = str(row_value(confirmed_row, "confirmed_event_candidate_id"))
        member = member_by_confirmed.get(confirmed_id)
        merge_group_id = str(row_value(member, "merge_group_candidate_id"))
        group = group_by_id.get(merge_group_id)
        anchor = anchor_by_group.get(merge_group_id)
        anchor_id = str(row_value(anchor, "timeline_anchor_candidate_id"))
        review_decision = str(row_value(review, "human_decision"))
        has_review = bool(review)
        has_confirmed = bool(confirmed_row)
        has_merge = bool(merge_group_id)
        has_anchor = bool(anchor_id)
        has_relationship = bool(rel_by_confirmed.get(confirmed_id))
        has_state = bool(state_by_confirmed.get(confirmed_id))
        is_singleton = int(row_value(group, "member_count", default=0) or 0) == 1
        is_fallback = str(row_value(anchor, "anchor_type")) in {"chapter_order_anchor", "fallback_anchor"}

        if has_confirmed and has_merge and has_anchor and has_relationship:
            chain_status = "full_chain"
            readiness_status = "ready_for_full_sample_expansion" if not any(row["issue_severity"] == "error" for row in issues) else "blocked_by_quality_issue"
        elif has_confirmed and has_merge and has_anchor and has_state:
            chain_status = "partial_chain"
            readiness_status = "needs_relationship_signal"
        elif has_confirmed and not has_merge:
            chain_status = "missing_merge_group"
            readiness_status = "blocked_by_quality_issue"
        elif has_confirmed and has_merge and not has_anchor:
            chain_status = "missing_timeline_anchor"
            readiness_status = "needs_timeline_mapping"
        elif has_review and not has_confirmed:
            chain_status = "blocked_chain"
            readiness_status = "blocked_by_review_decision" if review_decision in {"rejected", "weak_candidate", "duplicate_candidate", "uncertain", "needs_context"} else "blocked_by_quality_issue"
        elif not has_review:
            chain_status = "missing_review"
            readiness_status = "needs_manual_review"
        else:
            chain_status = "missing_impact"
            readiness_status = "blocked_by_quality_issue"

        quality_score = sum([has_review, has_confirmed, has_merge, has_anchor, has_relationship or has_state]) / 5.0
        chain_hash = sha256_text(json.dumps([normalized_id, confirmed_id, merge_group_id, anchor_id, review_decision, chain_status, readiness_status], ensure_ascii=False, separators=(",", ":")))
        chain_rows.append(
            {
                "chain_id": stable_id("l5q_chain", normalized_id or confirmed_id),
                "normalized_event_id": normalized_id,
                "confirmed_event_candidate_id": confirmed_id,
                "merge_group_candidate_id": merge_group_id,
                "timeline_anchor_candidate_id": anchor_id,
                "has_review_decision": int(has_review),
                "review_decision": review_decision,
                "has_confirmed_candidate": int(has_confirmed),
                "has_merge_group": int(has_merge),
                "has_timeline_anchor": int(has_anchor),
                "has_relationship_impact": int(has_relationship),
                "has_state_impact": int(has_state),
                "is_singleton_group": int(is_singleton),
                "is_fallback_anchor": int(is_fallback),
                "chain_status": chain_status,
                "readiness_status": readiness_status,
                "quality_score": quality_score,
                "chain_hash": chain_hash,
                "created_at": CREATED_AT,
            }
        )
        if has_confirmed and not has_relationship:
            add_issue(issues, issue_scope="relationship_impact", issue_type="missing_relationship_impact", issue_severity="warning", description="confirmed event has no relationship impact candidate", recommended_action="improve subject-object evidence or relationship rules", normalized_event_id=normalized_id, confirmed_event_candidate_id=confirmed_id, merge_group_candidate_id=merge_group_id, timeline_anchor_candidate_id=anchor_id)
        if has_confirmed and not has_state:
            add_issue(issues, issue_scope="state_impact", issue_type="missing_state_impact", issue_severity="warning", description="confirmed event has no state impact candidate", recommended_action="inspect event type/state impact rules", normalized_event_id=normalized_id, confirmed_event_candidate_id=confirmed_id, merge_group_candidate_id=merge_group_id, timeline_anchor_candidate_id=anchor_id)
        if has_confirmed and not evidence_by_confirmed.get(confirmed_id):
            add_issue(issues, issue_scope="evidence", issue_type="missing_evidence", issue_severity="warning", description="confirmed event has no carried-forward evidence span", recommended_action="backfill evidence spans", normalized_event_id=normalized_id, confirmed_event_candidate_id=confirmed_id)
        if has_confirmed and not str(row_value(confirmed_row, "subject_text")):
            add_issue(issues, issue_scope="confirmed_candidate", issue_type="missing_subject", issue_severity="warning", description="confirmed event has no subject text", recommended_action="review argument extraction", normalized_event_id=normalized_id, confirmed_event_candidate_id=confirmed_id)

    confirmed_count = len(confirmed)
    merge_group_count = len(groups)
    timeline_count = len(anchors)
    relationship_count = len(relationships)
    state_count = len(states)
    singleton_count = sum(1 for row in groups if int(row.get("member_count") or 0) == 1)
    multi_count = sum(1 for row in groups if int(row.get("member_count") or 0) > 1)
    fallback_count = sum(1 for row in anchors if str(row.get("anchor_type") or "") in {"chapter_order_anchor", "fallback_anchor"})
    relationship_missing_count = sum(1 for row in chain_rows if row["has_confirmed_candidate"] and not row["has_relationship_impact"])
    state_missing_count = sum(1 for row in chain_rows if row["has_confirmed_candidate"] and not row["has_state_impact"])
    full_count = sum(1 for row in chain_rows if row["chain_status"] == "full_chain")
    partial_count = sum(1 for row in chain_rows if row["chain_status"] == "partial_chain")
    blocked_count = sum(1 for row in chain_rows if row["chain_status"] == "blocked_chain")

    if merge_group_count and multi_count == 0:
        add_issue(issues, issue_scope="merge_group", issue_type="no_multi_member_group", issue_severity="warning", description="no multi-member merge groups were produced", recommended_action="expand review sample before expecting merge evidence")
    if merge_group_count and singleton_count / merge_group_count > 0.8:
        add_issue(issues, issue_scope="merge_group", issue_type="singleton_only", issue_severity="warning", description="singleton merge groups dominate the sample", recommended_action="expand sample review rows")
    if fallback_count > 0:
        add_issue(issues, issue_scope="timeline_anchor", issue_type="fallback_timeline_anchor", issue_severity="warning", description="timeline anchors used chapter-order fallback", recommended_action="add stronger L3 timeline mapping")
    if relationship_count == 0 and confirmed_count > 0:
        add_issue(issues, issue_scope="relationship_impact", issue_type="missing_relationship_impact", issue_severity="warning", description="no relationship impact candidates were produced", recommended_action="improve relationship impact rules after more subject-object evidence")
    if confirmed_count and relationship_missing_count / confirmed_count > 0.8:
        add_issue(issues, issue_scope="relationship_impact", issue_type="missing_relationship_impact", issue_severity="warning", description="most confirmed events lack relationship impact", recommended_action="improve subject-object evidence")
    if confirmed_count < 20:
        add_issue(issues, issue_scope="pipeline", issue_type="weak_chain_completeness", issue_severity="warning", description="confirmed candidate sample is smaller than 20", recommended_action="expand sample review rows")
    if any(str(row.get("reviewer_name") or "") == "sample_reviewer" for row in review_current):
        add_issue(issues, issue_scope="review", issue_type="sample_data_only", issue_severity="warning", description="review decisions come from sample reviewer", recommended_action="replace with real review before final claims")
    if state_count > 0:
        add_issue(issues, issue_scope="state_impact", issue_type="sample_data_only", issue_severity="info", description="state impact candidate generation is present in the sample", recommended_action="inspect state impact distribution")
    if relative_orders:
        add_issue(issues, issue_scope="timeline_anchor", issue_type="sample_data_only", issue_severity="info", description="relative order candidates are present", recommended_action="keep as candidate order only")
    add_issue(issues, issue_scope="pipeline", issue_type="sample_data_only", issue_severity="info", description="pipeline structure is closed but remains candidate/sample data", recommended_action="do not create final graph yet")

    severity_counts = Counter(row["issue_severity"] for row in issues)
    error_count = severity_counts.get("error", 0)
    warning_count = severity_counts.get("warning", 0)
    quality_gate_status = "quality_fail" if error_count else ("quality_warning" if warning_count else "structure_pass")
    run_hash = sha256_text(json.dumps([chain_rows, issues, quality_gate_status], ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    summary = {
        "quality_gate_run_id": stable_id("l5q_run", run_hash),
        "normalized_event_count": len(normalized_events),
        "review_current_count": len(review_current),
        "confirmed_event_candidate_count": confirmed_count,
        "merge_group_candidate_count": merge_group_count,
        "timeline_anchor_candidate_count": timeline_count,
        "relationship_impact_candidate_count": relationship_count,
        "state_impact_candidate_count": state_count,
        "singleton_group_count": singleton_count,
        "multi_member_group_count": multi_count,
        "fallback_anchor_count": fallback_count,
        "relationship_missing_count": relationship_missing_count,
        "state_missing_count": state_missing_count,
        "full_chain_complete_count": full_count,
        "partial_chain_count": partial_count,
        "blocked_chain_count": blocked_count,
        "source_mutation_detected": int(source_mutation),
        "input_mutation_detected": int(input_mutation),
        "quality_gate_status": quality_gate_status,
        "run_hash": run_hash,
        "created_at": CREATED_AT,
    }
    metrics = build_metrics(summary, chain_rows, issues, len(forbidden_tables))
    meta = {
        "forbidden_final_table_count": len(forbidden_tables),
        "error_count": error_count,
        "warning_count": warning_count,
        "info_count": severity_counts.get("info", 0),
        "quality_gate_status": quality_gate_status,
        "run_hash": run_hash,
    }
    return summary, chain_rows, issues, metrics, meta


def build_metrics(summary: dict[str, Any], chains: list[dict[str, Any]], issues: list[dict[str, Any]], forbidden_count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add_metric(name: str, value: Any, group: str, description: str = "") -> None:
        rows.append(
            {
                "metric_id": stable_id("l5q_metric", name, value, group),
                "metric_name": name,
                "metric_value": str(value),
                "metric_group": group,
                "metric_description": description,
                "created_at": CREATED_AT,
            }
        )

    for name in (
        "normalized_event_count",
        "confirmed_event_candidate_count",
        "merge_group_candidate_count",
        "timeline_anchor_candidate_count",
        "relationship_impact_candidate_count",
        "state_impact_candidate_count",
    ):
        add_metric(name, summary[name], "count")
    confirmed_count = int(summary["confirmed_event_candidate_count"])
    add_metric("relationship_missing_ratio", (float(summary["relationship_missing_count"]) / confirmed_count) if confirmed_count else 0.0, "ratio")
    add_metric("fallback_anchor_ratio", (float(summary["fallback_anchor_count"]) / int(summary["timeline_anchor_candidate_count"])) if int(summary["timeline_anchor_candidate_count"]) else 0.0, "ratio")
    readiness_counts = Counter(str(row["readiness_status"]) for row in chains)
    for readiness, count in sorted(readiness_counts.items()):
        add_metric(f"readiness_{readiness}", count, "readiness")
    chain_counts = Counter(str(row["chain_status"]) for row in chains)
    for status, count in sorted(chain_counts.items()):
        add_metric(f"chain_{status}", count, "chain")
    add_metric("quality_gate_status", summary["quality_gate_status"], "quality")
    add_metric("source_mutation_detected", bool(summary["source_mutation_detected"]), "mutation")
    add_metric("input_mutation_detected", bool(summary["input_mutation_detected"]), "mutation")
    add_metric("forbidden_final_table_count", forbidden_count, "forbidden_table_check")
    issue_counts = Counter(str(row["issue_severity"]) for row in issues)
    for severity, count in sorted(issue_counts.items()):
        add_metric(f"issue_severity_{severity}", count, "quality")
    return rows


def stable_output_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for table in ("l5_event_quality_gate_summary", "l5_event_quality_gate_chain", "l5_event_quality_gate_issue", "l5_event_quality_gate_metric"):
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        hashes[table] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return hashes


def layer_fingerprint(fingerprints: dict[str, dict[str, Any]], prefix: str) -> str:
    subset = {key: value for key, value in fingerprints.items() if key.startswith(prefix)}
    return sha256_text(json.dumps(subset, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def run_l5_event_understanding_quality_gate(
    project_dir: Path | str | None = None,
    *,
    output_dir: Path | str = "outputs",
    rebuild: bool = False,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    db_path = root / DB_RELATIVE_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        initialize_schema(conn)
        source_before = table_fingerprints(conn, INPUT_TABLES)
        input_before = table_fingerprints(conn, INPUT_TABLES)
        if rebuild:
            clear_l5_9_tables(conn)
        source_after_clear = table_fingerprints(conn, INPUT_TABLES)
        source_mutation = source_before != source_after_clear
        input_mutation = input_before != source_after_clear
        summary, chains, issues, metrics, meta = build_quality_rows(conn, source_mutation, input_mutation)
        insert_rows(conn, "l5_event_quality_gate_summary", [summary])
        insert_rows(conn, "l5_event_quality_gate_chain", chains)
        insert_rows(conn, "l5_event_quality_gate_issue", issues)
        insert_rows(conn, "l5_event_quality_gate_metric", metrics)
        stable_hashes = stable_output_hashes(conn)
        conn.execute(
            "INSERT INTO l5_event_quality_gate_run VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                summary["quality_gate_run_id"],
                layer_fingerprint(source_before, "l5_normalized"),
                layer_fingerprint(source_before, "l5_review"),
                layer_fingerprint(source_before, "l5_confirmed"),
                layer_fingerprint(source_before, "l5_event_merge"),
                layer_fingerprint(source_before, "l5_timeline"),
                layer_fingerprint(source_before, "l5_relationship") + ":" + layer_fingerprint(source_before, "l5_state") + ":" + layer_fingerprint(source_before, "l5_impact"),
                int(summary["source_mutation_detected"]),
                int(summary["input_mutation_detected"]),
                meta["forbidden_final_table_count"],
                meta["quality_gate_status"],
                meta["error_count"],
                meta["warning_count"],
                meta["info_count"],
                meta["run_hash"],
                CREATED_AT,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    from scripts.l5_event_understanding_quality_gate_reporter import run_l5_event_understanding_quality_gate_reporter

    report_manifest = run_l5_event_understanding_quality_gate_reporter(root, output_dir=out_dir)
    issue_type_counts = report_manifest["issue_type_counts"]
    issue_severity_counts = report_manifest["issue_severity_counts"]
    manifest = {
        "export_layer": "L5.9 Event Understanding Quality Gate",
        "project_dir": str(root),
        "database_path": str(db_path),
        "created_at": CREATED_AT,
        "row_counts": {
            **{key: summary[key] for key in summary if key.endswith("_count")},
            "chain_count": len(chains),
            "issue_count": len(issues),
            "metric_count": len(metrics),
        },
        "quality_gate_status": summary["quality_gate_status"],
        "source_mutation_detected": bool(summary["source_mutation_detected"]),
        "input_mutation_detected": bool(summary["input_mutation_detected"]),
        "forbidden_final_table_count": meta["forbidden_final_table_count"],
        "error_count": meta["error_count"],
        "warning_count": meta["warning_count"],
        "info_count": meta["info_count"],
        "issue_type_counts": issue_type_counts,
        "issue_severity_counts": issue_severity_counts,
        "stable_output_hashes": stable_hashes,
        "reporter_manifest": report_manifest,
    }
    write_json(out_dir / MANIFEST_JSON, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build L5.9 event understanding quality gate.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    manifest = run_l5_event_understanding_quality_gate(args.project_dir, output_dir=args.output_dir, rebuild=args.rebuild)
    print(f"L5.9 quality gate status: {manifest['quality_gate_status']}")


if __name__ == "__main__":
    main()
