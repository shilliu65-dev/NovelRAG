from __future__ import annotations

import argparse
import csv
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
from scripts.l5_review_decision_template_exporter import HUMAN_DECISIONS, build_template_rows


CREATED_AT = "1970-01-01T00:00:00"
DEFAULT_INPUT_FILE = Path("inputs") / "l5_review_decision_filled.csv"
DEFAULT_BATCH_ID = "default_review_batch"

VALIDATE_JSON = "l5_review_decision_validate_report.json"
VALIDATE_MD = "l5_review_decision_validate_report.md"
MANIFEST_JSON = "l5_review_decision_manifest.json"

L5_4_TABLES = (
    "l5_review_decision_import",
    "l5_review_decision_current",
    "l5_review_decision_conflict_audit",
    "l5_review_decision_run",
)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def stable_id(prefix: str, *parts: object, length: int = 16) -> str:
    return f"{prefix}_{sha256_text('|'.join(str(part) for part in parts))[:length]}"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def json_safe(value: Any) -> Any:
    if isinstance(value, bytes):
        return {"__type__": "bytes", "hex": value.hex()}
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    return value


def sql_identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def non_l5_4_fingerprints(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall()
    for table_row in rows:
        table = str(table_row[0])
        if table in L5_4_TABLES:
            continue
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({sql_identifier(table)})")]
        if not columns:
            continue
        order_sql = ", ".join(sql_identifier(column) for column in columns)
        table_rows = [json_safe(dict(row)) for row in conn.execute(f"SELECT * FROM {sql_identifier(table)} ORDER BY {order_sql}")]
        result[table] = {
            "row_count": len(table_rows),
            "columns": columns,
            "aggregate_hash": sha256_text(json.dumps(table_rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))),
        }
    return result


def initialize_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS l5_review_decision_import (
            review_import_id TEXT PRIMARY KEY,
            review_batch_id TEXT NOT NULL,
            normalized_event_id TEXT NOT NULL,
            event_key TEXT,
            human_decision TEXT NOT NULL,
            human_confidence TEXT,
            human_confidence_normalized REAL,
            human_notes TEXT,
            duplicate_of_normalized_event_id TEXT,
            needs_context_reason TEXT,
            reject_reason TEXT,
            reviewer_name TEXT,
            source_file TEXT NOT NULL,
            source_file_hash TEXT NOT NULL,
            source_row_number INTEGER NOT NULL,
            is_valid INTEGER NOT NULL,
            validation_errors_json TEXT NOT NULL,
            warning_flags_json TEXT NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_review_decision_current (
            normalized_event_id TEXT PRIMARY KEY,
            review_import_id TEXT NOT NULL,
            review_batch_id TEXT NOT NULL,
            human_decision TEXT NOT NULL,
            human_confidence TEXT,
            human_confidence_normalized REAL,
            human_notes TEXT,
            duplicate_of_normalized_event_id TEXT,
            needs_context_reason TEXT,
            reject_reason TEXT,
            reviewer_name TEXT,
            source_file_hash TEXT NOT NULL,
            current_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_review_decision_conflict_audit (
            conflict_id TEXT PRIMARY KEY,
            conflict_type TEXT NOT NULL,
            normalized_event_id TEXT NOT NULL,
            related_normalized_event_id TEXT,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            review_batch_id TEXT,
            source_file_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS l5_review_decision_run (
            run_id TEXT PRIMARY KEY,
            input_file TEXT NOT NULL,
            input_file_status TEXT NOT NULL,
            source_file_hash TEXT NOT NULL,
            input_row_count INTEGER NOT NULL,
            valid_decision_count INTEGER NOT NULL,
            current_decision_count INTEGER NOT NULL,
            conflict_count INTEGER NOT NULL,
            error_count INTEGER NOT NULL,
            warning_count INTEGER NOT NULL,
            source_fingerprints_before_json TEXT NOT NULL,
            source_fingerprints_after_json TEXT NOT NULL,
            source_mutation_detected INTEGER NOT NULL,
            input_hashes_before_json TEXT NOT NULL,
            input_hashes_after_json TEXT NOT NULL,
            input_mutation_detected INTEGER NOT NULL,
            stable_output_hashes_json TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT NOT NULL,
            status TEXT NOT NULL,
            note TEXT
        );
        """
    )


def clear_l5_4_tables(conn: sqlite3.Connection) -> None:
    for table in reversed(L5_4_TABLES):
        conn.execute(f"DELETE FROM {table}")


def l5_3_event_ids(conn: sqlite3.Connection) -> set[str]:
    if not object_exists(conn, "l5_normalized_event_candidate", "table"):
        raise RuntimeError("Missing L5.3 table l5_normalized_event_candidate")
    return {str(row[0]) for row in conn.execute("SELECT normalized_event_candidate_id FROM l5_normalized_event_candidate")}


def normalize_confidence(value: str) -> tuple[float | None, bool]:
    text = (value or "").strip().lower()
    if not text:
        return None, True
    enum = {"low": 0.25, "medium": 0.6, "high": 0.9}
    if text in enum:
        return enum[text], True
    try:
        number = float(text)
    except ValueError:
        return None, False
    if 0.0 <= number <= 1.0:
        return number, True
    return None, False


def row_signature(row: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(row.get("human_decision") or ""),
        str(row.get("duplicate_of_normalized_event_id") or ""),
        str(row.get("needs_context_reason") or ""),
        str(row.get("reject_reason") or ""),
    )


def synthetic_rows_from_l5_3(conn: sqlite3.Connection) -> tuple[list[dict[str, str]], str]:
    template_rows = build_template_rows(conn)
    rows: list[dict[str, str]] = []
    for row in template_rows:
        rows.append(
            {
                "review_batch_id": DEFAULT_BATCH_ID,
                "normalized_event_id": str(row["normalized_event_id"]),
                "event_key": str(row["event_key"]),
                "human_decision": "uncertain",
                "human_confidence": "low",
                "human_notes": "synthetic fallback because review input file is missing",
                "duplicate_of_normalized_event_id": "",
                "needs_context_reason": "",
                "reject_reason": "",
                "reviewer_name": "system_synthetic",
            }
        )
    raw = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return rows, sha256_text(raw)


def load_input_rows(conn: sqlite3.Connection, input_path: Path) -> tuple[list[dict[str, str]], str, str, dict[str, str], dict[str, str]]:
    if input_path.exists():
        before = {input_path.name: file_hash(input_path)}
        rows = read_csv_rows(input_path)
        after = {input_path.name: file_hash(input_path)}
        return rows, before[input_path.name], "present", before, after
    rows, synthetic_hash = synthetic_rows_from_l5_3(conn)
    return rows, synthetic_hash, "missing_synthetic_uncertain", {}, {}


def validate_import_rows(rows: list[dict[str, str]], valid_ids: set[str], source_file_hash: str, source_file: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    imports: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    conflict_counts: Counter[str] = Counter()
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)

    for index, row in enumerate(rows, start=1):
        batch_id = (row.get("review_batch_id") or DEFAULT_BATCH_ID).strip() or DEFAULT_BATCH_ID
        event_id = (row.get("normalized_event_id") or row.get("normalized_event_candidate_id") or "").strip()
        grouped[(batch_id, event_id)].append(index - 1)
        decision = (row.get("human_decision") or "").strip()
        confidence_text = (row.get("human_confidence") or "").strip()
        confidence, confidence_ok = normalize_confidence(confidence_text)
        duplicate_target = (row.get("duplicate_of_normalized_event_id") or "").strip()
        needs_reason = (row.get("needs_context_reason") or "").strip()
        reject_reason = (row.get("reject_reason") or "").strip()
        notes = (row.get("human_notes") or "").strip()
        errors: list[str] = []
        warnings: list[str] = []

        if event_id not in valid_ids:
            errors.append("normalized_event_id_not_found")
        if decision not in HUMAN_DECISIONS:
            errors.append("invalid_human_decision")
        if not confidence_ok:
            errors.append("invalid_human_confidence")
        if decision == "duplicate_candidate":
            if not duplicate_target:
                errors.append("duplicate_target_missing")
                conflicts.append(make_conflict("duplicate_target_missing", event_id, "", "error", "duplicate_candidate requires duplicate_of_normalized_event_id", batch_id, source_file_hash))
                conflict_counts["duplicate_target_missing"] += 1
            elif duplicate_target == event_id:
                errors.append("duplicate_target_self")
            elif duplicate_target not in valid_ids:
                errors.append("duplicate_target_not_found")
                conflicts.append(make_conflict("duplicate_target_missing", event_id, duplicate_target, "error", "duplicate target is not present in L5.3", batch_id, source_file_hash))
                conflict_counts["duplicate_target_missing"] += 1
        if decision == "needs_context" and not needs_reason:
            errors.append("needs_context_reason_missing")
        if decision == "rejected" and not reject_reason and not notes:
            errors.append("reject_reason_or_notes_missing")
        if decision == "approved_candidate" and confidence is not None and confidence < 0.5:
            warnings.append("approved_low_confidence")
            conflicts.append(make_conflict("approved_low_confidence", event_id, "", "warning", "approved_candidate has low confidence", batch_id, source_file_hash))
            conflict_counts["approved_low_confidence"] += 1

        import_id = stable_id("l5r_imp", source_file_hash, index, batch_id, event_id, decision, duplicate_target)
        imports.append(
            {
                "review_import_id": import_id,
                "review_batch_id": batch_id,
                "normalized_event_id": event_id,
                "event_key": row.get("event_key", ""),
                "human_decision": decision,
                "human_confidence": confidence_text,
                "human_confidence_normalized": confidence,
                "human_notes": notes,
                "duplicate_of_normalized_event_id": duplicate_target,
                "needs_context_reason": needs_reason,
                "reject_reason": reject_reason,
                "reviewer_name": row.get("reviewer_name", ""),
                "source_file": source_file,
                "source_file_hash": source_file_hash,
                "source_row_number": index,
                "is_valid": 0 if errors else 1,
                "validation_errors_json": json.dumps(errors, ensure_ascii=False, separators=(",", ":")),
                "warning_flags_json": json.dumps(warnings, ensure_ascii=False, separators=(",", ":")),
                "imported_at": CREATED_AT,
            }
        )

    for (batch_id, event_id), indexes in grouped.items():
        signatures = {row_signature(imports[index]) for index in indexes}
        if event_id and len(signatures) > 1:
            conflict_counts["conflicting_decisions"] += 1
            conflicts.append(make_conflict("conflicting_decisions", event_id, "", "error", "same review batch has conflicting decisions for one event", batch_id, source_file_hash))
            for index in indexes:
                errors = json.loads(imports[index]["validation_errors_json"])
                if "conflicting_decisions" not in errors:
                    errors.append("conflicting_decisions")
                imports[index]["is_valid"] = 0
                imports[index]["validation_errors_json"] = json.dumps(errors, ensure_ascii=False, separators=(",", ":"))
    return imports, conflicts, conflict_counts


def make_conflict(
    conflict_type: str,
    normalized_event_id: str,
    related_normalized_event_id: str,
    severity: str,
    message: str,
    review_batch_id: str,
    source_file_hash: str,
) -> dict[str, Any]:
    return {
        "conflict_id": stable_id("l5r_con", conflict_type, normalized_event_id, related_normalized_event_id, review_batch_id, message),
        "conflict_type": conflict_type,
        "normalized_event_id": normalized_event_id,
        "related_normalized_event_id": related_normalized_event_id,
        "severity": severity,
        "message": message,
        "review_batch_id": review_batch_id,
        "source_file_hash": source_file_hash,
        "created_at": CREATED_AT,
    }


def build_current_rows(import_rows: list[dict[str, Any]], conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocked = {row["normalized_event_id"] for row in conflicts if row["severity"] == "error"}
    by_event: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in import_rows:
        if row["is_valid"] and row["normalized_event_id"] not in blocked:
            by_event[row["normalized_event_id"]].append(row)
    current: list[dict[str, Any]] = []
    for event_id, rows in sorted(by_event.items()):
        row = sorted(rows, key=lambda item: int(item["source_row_number"]))[-1]
        current_hash = sha256_text(
            json.dumps(
                {
                    "normalized_event_id": event_id,
                    "review_import_id": row["review_import_id"],
                    "human_decision": row["human_decision"],
                    "duplicate_of_normalized_event_id": row["duplicate_of_normalized_event_id"],
                    "source_file_hash": row["source_file_hash"],
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        current.append(
            {
                "normalized_event_id": event_id,
                "review_import_id": row["review_import_id"],
                "review_batch_id": row["review_batch_id"],
                "human_decision": row["human_decision"],
                "human_confidence": row["human_confidence"],
                "human_confidence_normalized": row["human_confidence_normalized"],
                "human_notes": row["human_notes"],
                "duplicate_of_normalized_event_id": row["duplicate_of_normalized_event_id"],
                "needs_context_reason": row["needs_context_reason"],
                "reject_reason": row["reject_reason"],
                "reviewer_name": row["reviewer_name"],
                "source_file_hash": row["source_file_hash"],
                "current_hash": current_hash,
                "created_at": CREATED_AT,
            }
        )
    return current


def add_post_current_conflicts(current_rows: list[dict[str, Any]], source_hash: str) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    current_by_id = {row["normalized_event_id"]: row for row in current_rows}
    for row in current_rows:
        if row["human_decision"] != "duplicate_candidate":
            continue
        event_id = row["normalized_event_id"]
        target = row["duplicate_of_normalized_event_id"]
        target_row = current_by_id.get(target)
        if target_row and target_row["human_decision"] == "duplicate_candidate" and target_row["duplicate_of_normalized_event_id"] == event_id:
            conflicts.append(make_conflict("duplicate_cycle", event_id, target, "error", "duplicate candidates point to each other", row["review_batch_id"], source_hash))
        if target_row and target_row["human_decision"] == "rejected":
            conflicts.append(make_conflict("duplicate_target_rejected", event_id, target, "error", "duplicate target is rejected", row["review_batch_id"], source_hash))
    return conflicts


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in columns)
    conn.executemany(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", [[row.get(column) for column in columns] for row in rows])


def stable_l5_4_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    result: dict[str, str] = {}
    for table in L5_4_TABLES:
        if table == "l5_review_decision_run" or not object_exists(conn, table, "table"):
            continue
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
        order_sql = ", ".join(columns)
        rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_sql}")]
        result[table] = sha256_text(json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return result


def write_validate_reports(out_dir: Path, result: dict[str, Any]) -> None:
    validate = result["validation"]
    write_json(out_dir / VALIDATE_JSON, validate)
    lines = [
        "# L5.4 Review Decision Validation",
        "",
        f"- error_count: {validate['error_count']}",
        f"- warning_count: {validate['warning_count']}",
        f"- pass: {validate['error_count'] == 0}",
        "",
        "## Error Counts",
        "",
    ]
    error_counts = validate["error_counts"]
    lines.extend(f"- {key}: {value}" for key, value in error_counts.items()) if error_counts else lines.append("- none")
    lines.extend(["", "## Warning Counts", ""])
    warning_counts = validate["warning_counts"]
    lines.extend(f"- {key}: {value}" for key, value in warning_counts.items()) if warning_counts else lines.append("- none")
    lines.append("")
    (out_dir / VALIDATE_MD).write_text("\n".join(lines), encoding="utf-8")


def run_l5_review_decision_intake(
    project_dir: Path | str | None = None,
    *,
    input_file: Path | str = DEFAULT_INPUT_FILE,
    output_dir: Path | str = "outputs",
    rebuild: bool = False,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    input_path = Path(input_file)
    if not input_path.is_absolute():
        input_path = root / input_path
    db_path = root / DB_RELATIVE_PATH
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        source_before = non_l5_4_fingerprints(conn)
        initialize_schema(conn)
        if rebuild:
            clear_l5_4_tables(conn)
        valid_ids = l5_3_event_ids(conn)
        rows, source_file_hash, input_status, input_hashes_before, input_hashes_after = load_input_rows(conn, input_path)
        imports, conflicts, conflict_counts = validate_import_rows(rows, valid_ids, source_file_hash, str(input_path))
        current_rows = build_current_rows(imports, conflicts)
        post_conflicts = add_post_current_conflicts(current_rows, source_file_hash)
        conflicts.extend(post_conflicts)
        for item in post_conflicts:
            conflict_counts[item["conflict_type"]] += 1
        if post_conflicts:
            current_rows = build_current_rows(imports, conflicts)

        insert_rows(conn, "l5_review_decision_import", imports)
        insert_rows(conn, "l5_review_decision_current", current_rows)
        insert_rows(conn, "l5_review_decision_conflict_audit", conflicts)
        source_after = non_l5_4_fingerprints(conn)
        stable_hashes = stable_l5_4_hashes(conn)

        error_counter: Counter[str] = Counter()
        warning_counter: Counter[str] = Counter()
        for row in imports:
            error_counter.update(json.loads(row["validation_errors_json"]))
            warning_counter.update(json.loads(row["warning_flags_json"]))
        error_counter.update({item["conflict_type"]: 1 for item in conflicts if item["severity"] == "error" and item["conflict_type"] == "conflicting_decisions"})
        run_id = stable_id("l5r_run", source_file_hash, len(rows), json.dumps(stable_hashes, sort_keys=True))
        result = {
            "export_layer": "L5.4 Review Decision Intake",
            "project_dir": str(root),
            "database_path": str(db_path),
            "created_at": CREATED_AT,
            "input_file": str(input_path),
            "input_file_status": input_status,
            "source_file_hash": source_file_hash,
            "row_counts": {
                "input_row_count": len(rows),
                "imported_decision_count": len(imports),
                "valid_decision_count": sum(1 for row in imports if row["is_valid"]),
                "current_decision_count": len(current_rows),
                "conflict_count": len(conflicts),
            },
            "validation": {
                "error_count": sum(error_counter.values()),
                "warning_count": sum(warning_counter.values()),
                "error_counts": dict(sorted(error_counter.items())),
                "warning_counts": dict(sorted(warning_counter.items())),
                "source_file_hash": source_file_hash,
            },
            "conflict_type_counts": dict(sorted(conflict_counts.items())),
            "source_mutation_detected": source_before != source_after,
            "input_mutation_detected": input_hashes_before != input_hashes_after,
            "source_fingerprints_before": source_before,
            "source_fingerprints_after": source_after,
            "input_hashes_before": input_hashes_before,
            "input_hashes_after": input_hashes_after,
            "stable_output_hashes": stable_hashes,
        }
        conn.execute(
            """
            INSERT INTO l5_review_decision_run VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                run_id,
                str(input_path),
                input_status,
                source_file_hash,
                len(rows),
                result["row_counts"]["valid_decision_count"],
                len(current_rows),
                len(conflicts),
                result["validation"]["error_count"],
                result["validation"]["warning_count"],
                json.dumps(source_before, ensure_ascii=False, sort_keys=True),
                json.dumps(source_after, ensure_ascii=False, sort_keys=True),
                1 if source_before != source_after else 0,
                json.dumps(input_hashes_before, ensure_ascii=False, sort_keys=True),
                json.dumps(input_hashes_after, ensure_ascii=False, sort_keys=True),
                1 if input_hashes_before != input_hashes_after else 0,
                json.dumps(stable_hashes, ensure_ascii=False, sort_keys=True),
                CREATED_AT,
                CREATED_AT,
                "passed" if result["validation"]["error_count"] == 0 else "failed_validation",
                "L5.4 writes review decision tables only",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    write_validate_reports(out_dir, result)
    from scripts.l5_review_decision_reporter import run_l5_review_decision_reporter

    report_manifest = run_l5_review_decision_reporter(root, output_dir=out_dir)
    result["reporter_manifest"] = report_manifest
    write_json(out_dir / MANIFEST_JSON, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Import and validate L5.4 human review decisions.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--input-file", type=Path, default=DEFAULT_INPUT_FILE)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    parser.add_argument("--rebuild", action="store_true")
    args = parser.parse_args()
    result = run_l5_review_decision_intake(args.project_dir, input_file=args.input_file, output_dir=args.output_dir, rebuild=args.rebuild)
    print(f"L5.4 review decision imported rows: {result['row_counts']['imported_decision_count']}")
    print(f"L5.4 review decision validation errors: {result['validation']['error_count']}")


if __name__ == "__main__":
    main()
