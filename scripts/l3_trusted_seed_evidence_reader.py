from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import ensure_dirs, project_root_from_env


TOOL_VERSION = "v1"
DEFAULT_TRUSTED_PACKET = Path("outputs") / "l3_seed_rule_trusted_evidence_packet.json"
DEFAULT_COVERAGE_LOCK = Path("outputs") / "l3_trusted_seed_evidence_coverage_lock.json"
DEFAULT_GAP_REVIEW = Path("outputs") / "l3_trusted_seed_evidence_gap_review.json"
DEFAULT_SAMPLE_JSON = Path("outputs") / "l3_trusted_seed_evidence_reader_sample.json"
DEFAULT_SAMPLE_MD = Path("outputs") / "l3_trusted_seed_evidence_reader_sample.md"
DEFAULT_VALIDATION_JSON = Path("outputs") / "l3_trusted_seed_evidence_reader_validation_report.json"
DEFAULT_VALIDATION_MD = Path("outputs") / "l3_trusted_seed_evidence_reader_validation_report.md"


def resolve_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_metadata(generated_at: str, project_dir: Path, script_path: Path, input_paths: dict[str, Path]) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "project_dir": str(project_dir),
        "source_files": {key: str(path) for key, path in sorted(input_paths.items())},
        "source_file_sha256": {key: sha256_file(path) for key, path in sorted(input_paths.items())},
        "script_name": script_path.name,
        "version": TOOL_VERSION,
    }


def build_reader_rows(
    trusted_packet: dict[str, Any],
    coverage_lock: dict[str, Any],
) -> list[dict[str, Any]]:
    coverage_by_seed = {
        str(item["seed_item_id"]): item
        for item in coverage_lock.get("seed_coverage_items", [])
    }
    rows: list[dict[str, Any]] = []
    for seed_rule in trusted_packet.get("seed_rules", []):
        seed_item_id = str(seed_rule.get("seed_item_id"))
        coverage = coverage_by_seed.get(seed_item_id)
        if coverage is None:
            raise ValueError(f"coverage lock missing seed_item_id={seed_item_id}")
        evidence_refs = []
        for evidence in seed_rule.get("trusted_evidence", []):
            if evidence.get("human_status") != "accepted":
                raise ValueError(f"trusted packet contains non-accepted evidence for seed_item_id={seed_item_id}")
            evidence_refs.append(
                {
                    "candidate_id": evidence.get("candidate_id"),
                    "chapter_id": evidence.get("chapter_id"),
                    "chapter_num": evidence.get("chapter_num"),
                    "paragraph_hash": evidence.get("paragraph_hash"),
                    "evidence_hash": evidence.get("evidence_hash"),
                    "evidence_text": evidence.get("evidence_text"),
                    "backcut": evidence.get("backcut"),
                    "human_status": evidence.get("human_status"),
                }
            )
        evidence_refs.sort(key=lambda item: (int(item["chapter_num"]) if item.get("chapter_num") is not None else -1, str(item["candidate_id"])))
        rows.append(
            {
                "seed_item_id": seed_item_id,
                "seed_file": seed_rule.get("seed_file"),
                "seed_path": seed_rule.get("seed_path"),
                "seed_rule_type": seed_rule.get("seed_rule_type"),
                "seed_rule_text": seed_rule.get("seed_rule_text"),
                "trusted_status": seed_rule.get("trusted_status"),
                "coverage_status": coverage.get("coverage_status"),
                "accepted_evidence_count": len(evidence_refs),
                "evidence_refs": evidence_refs,
            }
        )
    rows.sort(key=lambda item: item["seed_item_id"])
    return rows


def build_indexes(rows: list[dict[str, Any]], gap_review: dict[str, Any]) -> dict[str, Any]:
    by_seed_item_id: dict[str, dict[str, Any]] = {}
    by_seed_file: dict[str, list[str]] = {}
    by_seed_rule_type: dict[str, list[str]] = {}
    by_trusted_status: dict[str, list[str]] = {}
    by_chapter_num: dict[int, list[dict[str, str]]] = {}
    by_candidate_id: dict[str, dict[str, Any]] = {}

    for row in rows:
        seed_item_id = row["seed_item_id"]
        by_seed_item_id[seed_item_id] = row
        by_seed_file.setdefault(str(row.get("seed_file")), []).append(seed_item_id)
        by_seed_rule_type.setdefault(str(row.get("seed_rule_type")), []).append(seed_item_id)
        by_trusted_status.setdefault(str(row.get("trusted_status")), []).append(seed_item_id)
        for evidence in row["evidence_refs"]:
            chapter_num = evidence.get("chapter_num")
            if chapter_num is not None:
                by_chapter_num.setdefault(int(chapter_num), []).append(
                    {
                        "seed_item_id": seed_item_id,
                        "candidate_id": str(evidence.get("candidate_id")),
                    }
                )
            by_candidate_id[str(evidence.get("candidate_id"))] = {
                "seed_item_id": seed_item_id,
                **evidence,
            }

    for mapping in (by_seed_file, by_seed_rule_type, by_trusted_status):
        for key in list(mapping.keys()):
            mapping[key] = sorted(mapping[key])
    for key in list(by_chapter_num.keys()):
        by_chapter_num[key].sort(key=lambda item: (item["seed_item_id"], item["candidate_id"]))

    uncovered_seed_items = sorted(gap_review.get("gap_items", []), key=lambda item: str(item.get("seed_item_id")))
    return {
        "by_seed_item_id": by_seed_item_id,
        "by_seed_file": by_seed_file,
        "by_seed_rule_type": by_seed_rule_type,
        "by_trusted_status": by_trusted_status,
        "by_chapter_num": by_chapter_num,
        "by_candidate_id": by_candidate_id,
        "uncovered_seed_items": uncovered_seed_items,
    }


def validate_integrity(trusted_packet: dict[str, Any], coverage_lock: dict[str, Any], gap_review: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    packet_summary = trusted_packet.get("summary", {})
    coverage_summary = coverage_lock.get("summary", {})
    comparisons = [
        ("seed_items_total", packet_summary.get("seed_items_total"), coverage_summary.get("seed_items_total")),
        ("covered_seed_items", packet_summary.get("seed_items_with_accepted_evidence"), coverage_summary.get("seed_items_with_accepted_evidence")),
        ("uncovered_seed_items", packet_summary.get("seed_items_without_accepted_evidence"), coverage_summary.get("seed_items_without_accepted_evidence")),
        ("accepted_evidence_count", packet_summary.get("accepted_evidence_count"), coverage_summary.get("accepted_evidence_count")),
        ("max_accepted_evidence_per_seed", packet_summary.get("max_accepted_evidence_per_seed"), coverage_summary.get("max_accepted_evidence_per_seed")),
    ]
    for label, packet_value, coverage_value in comparisons:
        if packet_value != coverage_value:
            raise ValueError(f"trusted packet and coverage lock mismatch for {label}: {packet_value} != {coverage_value}")

    if packet_summary.get("seed_items_without_accepted_evidence") != gap_review.get("summary", {}).get("uncovered_seed_count"):
        raise ValueError("coverage lock uncovered count does not match gap review uncovered count")

    if coverage_lock.get("integrity", {}).get("lock_validation_passed") is not True:
        raise ValueError("coverage lock integrity did not pass")

    covered_count = sum(1 for row in rows if row["coverage_status"] == "covered")
    uncovered_count = sum(1 for row in rows if row["coverage_status"] == "uncovered")
    accepted_count = sum(len(row["evidence_refs"]) for row in rows)
    if covered_count != coverage_summary.get("seed_items_with_accepted_evidence"):
        raise ValueError("covered seed count derived from rows does not match coverage lock summary")
    if uncovered_count != coverage_summary.get("seed_items_without_accepted_evidence"):
        raise ValueError("uncovered seed count derived from rows does not match coverage lock summary")
    if accepted_count != coverage_summary.get("accepted_evidence_count"):
        raise ValueError("accepted evidence count derived from rows does not match coverage lock summary")


def filter_rows(
    rows: list[dict[str, Any]],
    *,
    seed_item_id: str | None = None,
    seed_file: str | None = None,
    seed_rule_type: str | None = None,
    trusted_status: str | None = None,
    chapter_num: int | None = None,
    include_evidence_text: bool = False,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for row in rows:
        if seed_item_id is not None and row["seed_item_id"] != seed_item_id:
            continue
        if seed_file is not None and str(row.get("seed_file")) != seed_file:
            continue
        if seed_rule_type is not None and str(row.get("seed_rule_type")) != seed_rule_type:
            continue
        if trusted_status is not None and str(row.get("trusted_status")) != trusted_status:
            continue

        filtered_evidence = row["evidence_refs"]
        if chapter_num is not None:
            filtered_evidence = [evidence for evidence in filtered_evidence if evidence.get("chapter_num") == chapter_num]
            if not filtered_evidence:
                continue

        result_evidence = []
        for evidence in filtered_evidence:
            result_evidence.append(
                {
                    "candidate_id": evidence.get("candidate_id"),
                    "chapter_id": evidence.get("chapter_id"),
                    "chapter_num": evidence.get("chapter_num"),
                    "paragraph_hash": evidence.get("paragraph_hash"),
                    "evidence_hash": evidence.get("evidence_hash"),
                    **({"evidence_text": evidence.get("evidence_text")} if include_evidence_text else {}),
                    "backcut": evidence.get("backcut"),
                    "human_status": evidence.get("human_status"),
                }
            )

        if not result_evidence:
            continue

        results.append(
            {
                "seed_item_id": row["seed_item_id"],
                "seed_file": row.get("seed_file"),
                "seed_path": row.get("seed_path"),
                "seed_rule_type": row.get("seed_rule_type"),
                "seed_rule_text": row.get("seed_rule_text"),
                "trusted_status": row.get("trusted_status"),
                "coverage_status": row.get("coverage_status"),
                "accepted_evidence_count": len(result_evidence),
                "evidence_refs": result_evidence,
            }
        )
    return results


def sample_markdown(sample: dict[str, Any]) -> str:
    summary = sample["summary"]
    lines = [
        "# L3 Trusted Seed Evidence Reader Sample",
        "",
        "## Summary",
        "",
        f"- seed_items_total: {summary['seed_items_total']}",
        f"- covered_seed_items: {summary['covered_seed_items']}",
        f"- uncovered_seed_items: {summary['uncovered_seed_items']}",
        f"- accepted_evidence_count: {summary['accepted_evidence_count']}",
        f"- query_result_count: {summary['query_result_count']}",
        f"- filters: {json.dumps(summary['filters'], ensure_ascii=False, sort_keys=True)}",
        "",
        "## Results",
        "",
    ]
    if not sample["results"]:
        lines.extend(["- No results", ""])
    for item in sample["results"]:
        lines.extend(
            [
                f"### {item['seed_item_id']}",
                "",
                f"- seed_file: {item.get('seed_file')}",
                f"- seed_rule_type: {item.get('seed_rule_type')}",
                f"- trusted_status: {item.get('trusted_status')}",
                f"- coverage_status: {item.get('coverage_status')}",
                "",
                "Seed rule text:",
                "",
                "```json",
                str(item.get("seed_rule_text")),
                "```",
                "",
                "Evidence refs:",
                "",
            ]
        )
        if not item["evidence_refs"]:
            lines.extend(["- No accepted evidence", ""])
            continue
        for evidence in item["evidence_refs"]:
            lines.extend(
                [
                    f"- candidate_id: {evidence.get('candidate_id')}",
                    f"- chapter_num: {evidence.get('chapter_num')}",
                    f"- paragraph_hash: {evidence.get('paragraph_hash')}",
                    f"- evidence_text: {evidence.get('evidence_text', '(hidden)')}",
                    f"- backcut: {json.dumps(evidence.get('backcut'), ensure_ascii=False)}",
                    "",
                ]
            )
    lines.extend(["## Uncovered Seed Items", ""])
    for item in sample["uncovered_seed_items"]:
        lines.extend(
            [
                f"- seed_item_id: {item.get('seed_item_id')}",
                f"- seed_file: {item.get('seed_source_file')}",
                f"- seed_path: {item.get('seed_path') or item.get('seed_content_summary')}",
                f"- reason: {item.get('gap_reason')}",
                f"- recommended_action: {item.get('recommended_action')}",
                "",
            ]
        )
    return "\n".join(lines)


def validation_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# L3 Trusted Seed Evidence Reader Validation Report",
        "",
        "## Summary",
        "",
    ]
    for key in (
        "seed_items_total",
        "covered_seed_items",
        "uncovered_seed_items",
        "accepted_evidence_count",
        "sample_query_result_count",
        "integrity_check_result",
        "whether_index_db_modified",
        "whether_config_seed_modified",
        "whether_existing_outputs_overwritten",
    ):
        lines.append(f"- {key}: {json.dumps(report[key], ensure_ascii=False)}")
    lines.extend(["", "## Generated Outputs", ""])
    for item in report["generated_outputs"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Indexes Built", ""])
    for item in report["indexes_built"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Commands Run", ""])
    for item in report["commands_run"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Test Results", ""])
    for item in report["test_results"]:
        lines.append(f"- {item['command']}: {item['result']}")
    lines.extend(["", "## Next Step", "", f"- {report['next_recommended_step']}", ""])
    return "\n".join(lines)


def run_l3_trusted_seed_evidence_reader(
    project_dir: Path | str | None = None,
    *,
    trusted_packet: Path | str | None = None,
    coverage_lock: Path | str | None = None,
    gap_review: Path | str | None = None,
    seed_item_id: str | None = None,
    seed_file: str | None = None,
    seed_rule_type: str | None = None,
    trusted_status: str | None = None,
    chapter_num: int | None = None,
    include_evidence_text: bool = False,
    out_json: Path | str | None = None,
    out_md: Path | str | None = None,
    validation_json: Path | str | None = None,
    validation_md: Path | str | None = None,
    fixed_generated_at: str | None = None,
) -> dict[str, Any]:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    script_path = Path(__file__).resolve()

    input_paths = {
        "trusted_packet": resolve_path(root, trusted_packet, DEFAULT_TRUSTED_PACKET).resolve(),
        "coverage_lock": resolve_path(root, coverage_lock, DEFAULT_COVERAGE_LOCK).resolve(),
        "gap_review": resolve_path(root, gap_review, DEFAULT_GAP_REVIEW).resolve(),
    }
    for path in input_paths.values():
        if not path.exists():
            raise FileNotFoundError(f"Required input file not found: {path}")

    generated_at = fixed_generated_at or datetime.now().isoformat(timespec="seconds")
    metadata = build_metadata(generated_at, root, script_path, input_paths)

    trusted_packet_payload = load_json(input_paths["trusted_packet"])
    coverage_lock_payload = load_json(input_paths["coverage_lock"])
    gap_review_payload = load_json(input_paths["gap_review"])

    rows = build_reader_rows(trusted_packet_payload, coverage_lock_payload)
    validate_integrity(trusted_packet_payload, coverage_lock_payload, gap_review_payload, rows)
    indexes = build_indexes(rows, gap_review_payload)

    results = filter_rows(
        rows,
        seed_item_id=seed_item_id,
        seed_file=seed_file,
        seed_rule_type=seed_rule_type,
        trusted_status=trusted_status,
        chapter_num=chapter_num,
        include_evidence_text=include_evidence_text,
    )

    filters = {
        key: value
        for key, value in {
            "seed_item_id": seed_item_id,
            "seed_file": seed_file,
            "seed_rule_type": seed_rule_type,
            "trusted_status": trusted_status,
            "chapter_num": chapter_num,
            "include_evidence_text": include_evidence_text,
        }.items()
        if value not in (None, False)
    }
    sample = {
        "meta": {
            "artifact_type": "l3_trusted_seed_evidence_reader_sample",
            "version": TOOL_VERSION,
            "project": "NovelRAG",
            "source_files": [str(path) for _, path in sorted(input_paths.items())],
            "generated_at": generated_at,
            "notes": [],
        },
        "summary": {
            "seed_items_total": trusted_packet_payload["summary"]["seed_items_total"],
            "covered_seed_items": trusted_packet_payload["summary"]["seed_items_with_accepted_evidence"],
            "uncovered_seed_items": trusted_packet_payload["summary"]["seed_items_without_accepted_evidence"],
            "accepted_evidence_count": trusted_packet_payload["summary"]["accepted_evidence_count"],
            "query_result_count": len(results),
            "filters": filters,
        },
        "results": results,
        "uncovered_seed_items": indexes["uncovered_seed_items"],
    }

    command_parts = [
        f"python {script_path.name}",
        f"--project-dir {root}",
        f"--trusted-packet {input_paths['trusted_packet']}",
        f"--coverage-lock {input_paths['coverage_lock']}",
        f"--gap-review {input_paths['gap_review']}",
    ]
    if seed_item_id is not None:
        command_parts.append(f"--seed-item-id {seed_item_id}")
    if seed_file is not None:
        command_parts.append(f"--seed-file {seed_file}")
    if seed_rule_type is not None:
        command_parts.append(f"--seed-rule-type {seed_rule_type}")
    if trusted_status is not None:
        command_parts.append(f"--trusted-status {trusted_status}")
    if chapter_num is not None:
        command_parts.append(f"--chapter-num {chapter_num}")
    if include_evidence_text:
        command_parts.append("--include-evidence-text")
    command_parts.append(f"--out-json {resolve_path(root, out_json, DEFAULT_SAMPLE_JSON)}")
    command_parts.append(f"--out-md {resolve_path(root, out_md, DEFAULT_SAMPLE_MD)}")

    validation_report = {
        "checked_files": [str(path) for _, path in sorted(input_paths.items())],
        "changed_files": [
            str(resolve_path(root, out_json, DEFAULT_SAMPLE_JSON)),
            str(resolve_path(root, out_md, DEFAULT_SAMPLE_MD)),
            str(resolve_path(root, validation_json, DEFAULT_VALIDATION_JSON)),
            str(resolve_path(root, validation_md, DEFAULT_VALIDATION_MD)),
        ],
        "commands_run": [
            " ".join(command_parts),
        ],
        "test_results": [
            {"command": "python -m unittest tests.test_l3_trusted_seed_evidence_reader -v", "result": "not_run_by_script"},
            {"command": "python -m unittest tests.test_l3_lock_trusted_seed_evidence_coverage -v", "result": "not_run_by_script"},
            {"command": "python -m unittest tests.test_l3_build_trusted_seed_evidence_packet -v", "result": "not_run_by_script"},
            {"command": "python -m unittest discover -s tests -v", "result": "not_run_by_script"},
            {"command": "python -m compileall scripts tests", "result": "not_run_by_script"},
        ],
        "generated_outputs": [
            str(resolve_path(root, out_json, DEFAULT_SAMPLE_JSON)),
            str(resolve_path(root, out_md, DEFAULT_SAMPLE_MD)),
            str(resolve_path(root, validation_json, DEFAULT_VALIDATION_JSON)),
            str(resolve_path(root, validation_md, DEFAULT_VALIDATION_MD)),
        ],
        "seed_items_total": trusted_packet_payload["summary"]["seed_items_total"],
        "covered_seed_items": trusted_packet_payload["summary"]["seed_items_with_accepted_evidence"],
        "uncovered_seed_items": trusted_packet_payload["summary"]["seed_items_without_accepted_evidence"],
        "accepted_evidence_count": trusted_packet_payload["summary"]["accepted_evidence_count"],
        "indexes_built": [
            "by_seed_item_id",
            "by_seed_file",
            "by_seed_rule_type",
            "by_trusted_status",
            "by_chapter_num",
            "by_candidate_id",
            "uncovered_seed_items",
        ],
        "sample_query_result_count": len(results),
        "integrity_check_result": "passed",
        "whether_index_db_modified": False,
        "whether_config_seed_modified": False,
        "whether_existing_outputs_overwritten": False,
        "remaining_risks": [
            "uncovered seed items remain unresolved and must not be treated as trusted",
            "reader is in-memory only and does not persist secondary indexes",
        ],
        "next_recommended_step": "如果 reader 通过，则下一步进入 L3 Story Bible seed projection 小样本设计：从 trusted evidence reader 读取规则，生成只读的 story_bible_seed_projection_sample，不直接读取 config/*.seed.json，不调用模型。",
    }

    out_json_path = resolve_path(root, out_json, DEFAULT_SAMPLE_JSON)
    out_md_path = resolve_path(root, out_md, DEFAULT_SAMPLE_MD)
    validation_json_path = resolve_path(root, validation_json, DEFAULT_VALIDATION_JSON)
    validation_md_path = resolve_path(root, validation_md, DEFAULT_VALIDATION_MD)

    write_json(out_json_path, sample)
    write_markdown(out_md_path, sample_markdown(sample))
    write_json(validation_json_path, validation_report)
    write_markdown(validation_md_path, validation_markdown(validation_report))

    return {
        "sample": sample,
        "validation_report": validation_report,
        "indexes": indexes,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Read trusted L3 seed evidence through an in-memory read-only index layer.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--trusted-packet", type=Path, default=DEFAULT_TRUSTED_PACKET, help="Trusted packet JSON path.")
    parser.add_argument("--coverage-lock", type=Path, default=DEFAULT_COVERAGE_LOCK, help="Coverage lock JSON path.")
    parser.add_argument("--gap-review", type=Path, default=DEFAULT_GAP_REVIEW, help="Gap review JSON path.")
    parser.add_argument("--seed-item-id", type=str, default=None, help="Filter by seed item id.")
    parser.add_argument("--seed-file", type=str, default=None, help="Filter by seed file.")
    parser.add_argument("--seed-rule-type", type=str, default=None, help="Filter by seed rule type.")
    parser.add_argument("--trusted-status", type=str, default=None, help="Filter by trusted status.")
    parser.add_argument("--chapter-num", type=int, default=None, help="Filter evidence by chapter number.")
    parser.add_argument("--include-evidence-text", action="store_true", help="Include full evidence text in sample output.")
    parser.add_argument("--out-json", type=Path, default=DEFAULT_SAMPLE_JSON, help="Sample JSON output path.")
    parser.add_argument("--out-md", type=Path, default=DEFAULT_SAMPLE_MD, help="Sample Markdown output path.")
    parser.add_argument("--validation-json", type=Path, default=DEFAULT_VALIDATION_JSON, help="Validation report JSON output path.")
    parser.add_argument("--validation-md", type=Path, default=DEFAULT_VALIDATION_MD, help="Validation report Markdown output path.")
    parser.add_argument("--fixed-generated-at", type=str, default=None, help="Fixed timestamp for deterministic test output.")
    args = parser.parse_args()

    result = run_l3_trusted_seed_evidence_reader(
        project_dir=args.project_dir,
        trusted_packet=args.trusted_packet,
        coverage_lock=args.coverage_lock,
        gap_review=args.gap_review,
        seed_item_id=args.seed_item_id,
        seed_file=args.seed_file,
        seed_rule_type=args.seed_rule_type,
        trusted_status=args.trusted_status,
        chapter_num=args.chapter_num,
        include_evidence_text=args.include_evidence_text,
        out_json=args.out_json,
        out_md=args.out_md,
        validation_json=args.validation_json,
        validation_md=args.validation_md,
        fixed_generated_at=args.fixed_generated_at,
    )
    print(f"Query result count: {result['sample']['summary']['query_result_count']}")
    print(f"Covered seed items: {result['sample']['summary']['covered_seed_items']}")
    print(f"Uncovered seed items: {result['sample']['summary']['uncovered_seed_items']}")


if __name__ == "__main__":
    main()
