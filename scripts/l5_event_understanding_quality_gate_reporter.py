from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env
from scripts.l5_event_candidate_review_exporter import object_exists
from scripts.l5_event_understanding_quality_gate import CREATED_AT, MANIFEST_JSON


SUMMARY_JSON = "l5_event_quality_gate_summary.json"
SUMMARY_MD = "l5_event_quality_gate_summary.md"
CHAIN_CSV = "l5_event_quality_gate_chain.csv"
CHAIN_JSON = "l5_event_quality_gate_chain.json"
ISSUES_CSV = "l5_event_quality_gate_issues.csv"
ISSUES_JSON = "l5_event_quality_gate_issues.json"
METRICS_CSV = "l5_event_quality_gate_metrics.csv"
METRICS_JSON = "l5_event_quality_gate_metrics.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})


def fetch_rows(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not object_exists(conn, table, "table"):
        return []
    columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table})")]
    order_sql = ", ".join(columns)
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_sql}")]


def write_markdown(path: Path, summary: dict[str, Any], chains: list[dict[str, Any]], issues: list[dict[str, Any]], metrics: list[dict[str, Any]], forbidden_count: int) -> None:
    readiness_counts = Counter(str(row.get("readiness_status") or "") for row in chains)
    severity_counts = Counter(str(row.get("issue_severity") or "") for row in issues)
    decision_counts = Counter(str(row.get("review_decision") or "") for row in chains)
    lines = [
        "# L5.9 Event Understanding Quality Gate",
        "",
        "## Pipeline overview",
        "",
        f"- quality_gate_status: {summary.get('quality_gate_status', '')}",
        f"- confirmed_event_candidate_count: {summary.get('confirmed_event_candidate_count', 0)}",
        f"- full_chain_complete_count: {summary.get('full_chain_complete_count', 0)}",
        f"- partial_chain_count: {summary.get('partial_chain_count', 0)}",
        f"- blocked_chain_count: {summary.get('blocked_chain_count', 0)}",
        "",
        "## Count summary",
        "",
    ]
    for key in (
        "normalized_event_count",
        "review_current_count",
        "confirmed_event_candidate_count",
        "merge_group_candidate_count",
        "timeline_anchor_candidate_count",
        "relationship_impact_candidate_count",
        "state_impact_candidate_count",
        "singleton_group_count",
        "multi_member_group_count",
        "fallback_anchor_count",
    ):
        lines.append(f"- {key}: {summary.get(key, 0)}")
    lines.extend(["", "## Chain completeness", ""])
    lines.extend(f"- {key}: {value}" for key, value in Counter(str(row.get("chain_status") or "") for row in chains).items()) if chains else lines.append("- none")
    lines.extend(["", "## Review decision distribution", ""])
    lines.extend(f"- {key or 'none'}: {value}" for key, value in decision_counts.items()) if decision_counts else lines.append("- none")
    lines.extend(["", "## Merge group quality", ""])
    lines.append(f"- singleton_group_count: {summary.get('singleton_group_count', 0)}")
    lines.append(f"- multi_member_group_count: {summary.get('multi_member_group_count', 0)}")
    lines.extend(["", "## Timeline anchor quality", ""])
    lines.append(f"- fallback_anchor_count: {summary.get('fallback_anchor_count', 0)}")
    lines.extend(["", "## Relationship impact quality", ""])
    lines.append(f"- relationship_impact_candidate_count: {summary.get('relationship_impact_candidate_count', 0)}")
    lines.append(f"- relationship_missing_count: {summary.get('relationship_missing_count', 0)}")
    lines.extend(["", "## State impact quality", ""])
    lines.append(f"- state_impact_candidate_count: {summary.get('state_impact_candidate_count', 0)}")
    lines.append(f"- state_missing_count: {summary.get('state_missing_count', 0)}")
    lines.extend(["", "## Readiness distribution", ""])
    lines.extend(f"- {key}: {value}" for key, value in readiness_counts.items()) if readiness_counts else lines.append("- none")
    lines.extend(["", "## Issues by severity", ""])
    lines.extend(f"- {key}: {value}" for key, value in severity_counts.items()) if severity_counts else lines.append("- none")
    lines.extend(["", "## Forbidden final table check", "", f"- forbidden_final_table_count: {forbidden_count}"])
    lines.extend(["", "## Mutation check", ""])
    lines.append(f"- source_mutation_detected: {bool(summary.get('source_mutation_detected', 0))}")
    lines.append(f"- input_mutation_detected: {bool(summary.get('input_mutation_detected', 0))}")
    lines.extend(
        [
            "",
            "## Recommended next action",
            "",
            "- pipeline structure passed",
            "- expand sample review rows or run full-sample review",
            "- improve relationship impact rules after more subject-object evidence",
            "- add stronger L3 timeline mapping before final timeline",
            "- do not create final graph yet",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_event_understanding_quality_gate_reporter(
    project_dir: Path | str | None = None,
    *,
    output_dir: Path | str = "outputs",
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = root / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        summaries = fetch_rows(conn, "l5_event_quality_gate_summary")
        chains = fetch_rows(conn, "l5_event_quality_gate_chain")
        issues = fetch_rows(conn, "l5_event_quality_gate_issue")
        metrics = fetch_rows(conn, "l5_event_quality_gate_metric")
        runs = fetch_rows(conn, "l5_event_quality_gate_run")
    finally:
        conn.close()

    summary = summaries[-1] if summaries else {}
    chain_columns = list(chains[0].keys()) if chains else ["chain_id", "normalized_event_id", "confirmed_event_candidate_id", "chain_status", "readiness_status"]
    issue_columns = list(issues[0].keys()) if issues else ["issue_id", "issue_scope", "issue_type", "issue_severity", "description"]
    metric_columns = list(metrics[0].keys()) if metrics else ["metric_id", "metric_name", "metric_value", "metric_group"]
    forbidden_metric = next((row for row in metrics if row.get("metric_name") == "forbidden_final_table_count"), {})
    forbidden_count = int(forbidden_metric.get("metric_value") or 0)

    write_json(out_dir / SUMMARY_JSON, {"export_name": "l5_event_quality_gate_summary", "created_at": CREATED_AT, "row_count": len(summaries), "summary": summary})
    write_csv(out_dir / CHAIN_CSV, chains, chain_columns)
    write_json(out_dir / CHAIN_JSON, {"export_name": "l5_event_quality_gate_chain", "created_at": CREATED_AT, "row_count": len(chains), "columns": chain_columns, "rows": chains})
    write_csv(out_dir / ISSUES_CSV, issues, issue_columns)
    write_json(out_dir / ISSUES_JSON, {"export_name": "l5_event_quality_gate_issue", "created_at": CREATED_AT, "row_count": len(issues), "columns": issue_columns, "rows": issues})
    write_csv(out_dir / METRICS_CSV, metrics, metric_columns)
    write_json(out_dir / METRICS_JSON, {"export_name": "l5_event_quality_gate_metric", "created_at": CREATED_AT, "row_count": len(metrics), "columns": metric_columns, "rows": metrics})
    write_markdown(out_dir / SUMMARY_MD, summary, chains, issues, metrics, forbidden_count)

    manifest = {
        "export_layer": "L5.9 Event Understanding Quality Gate",
        "project_dir": str(root),
        "database_path": str(root / DB_RELATIVE_PATH),
        "created_at": CREATED_AT,
        "row_counts": {
            "summary_count": len(summaries),
            "chain_count": len(chains),
            "issue_count": len(issues),
            "metric_count": len(metrics),
            "run_count": len(runs),
        },
        "issue_severity_counts": dict(sorted(Counter(str(row.get("issue_severity") or "") for row in issues).items())),
        "issue_type_counts": dict(sorted(Counter(str(row.get("issue_type") or "") for row in issues).items())),
        "output_files": [
            str(out_dir / SUMMARY_JSON),
            str(out_dir / SUMMARY_MD),
            str(out_dir / CHAIN_CSV),
            str(out_dir / CHAIN_JSON),
            str(out_dir / ISSUES_CSV),
            str(out_dir / ISSUES_JSON),
            str(out_dir / METRICS_CSV),
            str(out_dir / METRICS_JSON),
            str(out_dir / MANIFEST_JSON),
        ],
    }
    write_json(out_dir / MANIFEST_JSON, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export L5.9 event understanding quality gate reports.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    manifest = run_l5_event_understanding_quality_gate_reporter(args.project_dir, output_dir=args.output_dir)
    print(f"L5.9 quality gate chain rows: {manifest['row_counts']['chain_count']}")


if __name__ == "__main__":
    main()
