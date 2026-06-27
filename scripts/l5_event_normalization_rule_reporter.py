from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l5_verify_event_normalization_rules import (
    compute_seed_checksum,
    load_seed,
    resolve_project_paths,
    validate_candidate_event_type_mappings,
    validate_event_types,
    validate_l5_candidate_coverage,
    validate_l5_review_export_coverage,
    validate_state_change_types,
    validate_trigger_mappings,
    validate_warning_flags,
)


COVERAGE_JSON_RELATIVE_PATH = Path("outputs") / "l5_event_normalization_rules_coverage_report.json"
COVERAGE_MD_RELATIVE_PATH = Path("outputs") / "l5_event_normalization_rules_coverage_report.md"


def write_coverage_reports(payload: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# L5.2 Event Normalization Rule Coverage Report",
        "",
        f"- created_at: {payload['created_at']}",
        f"- expected_seed_checksum: {payload['expected_seed_checksum']}",
        f"- error_count: {len(payload['errors'])}",
        f"- warning_count: {len(payload['warnings'])}",
        "",
        "## Coverage",
        "",
        "```json",
        json.dumps(payload["coverage"], ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
        "## Errors",
        "",
    ]
    lines.extend(f"- {item}" for item in payload["errors"]) if payload["errors"] else lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {item}" for item in payload["warnings"]) if payload["warnings"] else lines.append("- none")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def run_l5_event_normalization_rule_reporter(
    project_dir: Path | str | None = None,
    *,
    strict: bool = False,
    check_l5_candidates: bool = True,
    check_l5_review_export: bool = True,
) -> dict[str, Any]:
    paths = resolve_project_paths(project_dir)
    errors: list[str] = []
    warnings: list[str] = []
    seed = load_seed(paths["seed"], errors)
    coverage: dict[str, Any] = {}
    expected_checksum = ""
    if seed:
        expected_checksum = compute_seed_checksum(seed)
        event_types, subtype_by_event = validate_event_types(seed, errors)
        state_types = validate_state_change_types(seed, event_types, errors)
        trigger_categories = validate_trigger_mappings(seed, event_types, subtype_by_event, errors)
        event_candidate_types, state_candidate_types = validate_candidate_event_type_mappings(seed, event_types, state_types, subtype_by_event, errors)
        warning_flags = validate_warning_flags(seed, errors)
        if check_l5_candidates:
            coverage["l5_candidates"] = validate_l5_candidate_coverage(
                paths["db"],
                trigger_categories,
                event_candidate_types,
                state_candidate_types,
                warning_flags,
                errors,
                warnings,
                strict,
            )
        if check_l5_review_export:
            coverage["l5_review_export"] = validate_l5_review_export_coverage(
                paths["root"],
                trigger_categories,
                event_candidate_types,
                state_candidate_types,
                warning_flags,
                errors,
                warnings,
                strict,
            )
    payload = {
        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "project_dir": str(paths["root"]),
        "seed_path": str(paths["seed"]),
        "db_path": str(paths["db"]),
        "strict": strict,
        "expected_seed_checksum": expected_checksum,
        "coverage": coverage,
        "errors": errors,
        "warnings": warnings,
    }
    write_coverage_reports(payload, paths["root"] / COVERAGE_JSON_RELATIVE_PATH, paths["root"] / COVERAGE_MD_RELATIVE_PATH)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Report L5.2 event normalization rule coverage.")
    parser.add_argument("--project-dir", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--skip-l5-candidates", action="store_true")
    parser.add_argument("--skip-l5-review-export", action="store_true")
    args = parser.parse_args()
    payload = run_l5_event_normalization_rule_reporter(
        args.project_dir,
        strict=args.strict,
        check_l5_candidates=not args.skip_l5_candidates,
        check_l5_review_export=not args.skip_l5_review_export,
    )
    print(f"expected_seed_checksum: {payload['expected_seed_checksum']}")
    print("L5.2 event normalization rule coverage report written")
    raise SystemExit(0 if not payload["errors"] else 1)


if __name__ == "__main__":
    main()
