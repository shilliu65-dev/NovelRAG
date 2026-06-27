from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_child_segment_builder as child_builder
from scripts import l3_prompt_context_pack_builder as pack_builder

DEFAULT_INPUT_PREFIX = "l3_prompt_context_pack_sample"
DEFAULT_OUTPUT_PREFIX = "l3_prompt_consumer_dryrun_sample"
FORBIDDEN_SQL_TOKENS = ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "REPLACE")


@dataclass
class DryRunResult:
    ok: bool
    project_dir: Path
    db_path: Path
    input_prefix: str
    output_prefix: str
    query_count: int = 0
    response_count: int = 0
    ready_response_count: int = 0
    partial_response_count: int = 0
    insufficient_response_count: int = 0
    total_used_fact_count: int = 0
    total_used_evidence_count: int = 0
    avg_used_facts_per_response: float = 0.0
    token_budget: int = 0
    over_token_budget_count: int = 0
    source_table_mutation_count: int = 0
    forbidden_final_table_count: int = 0
    error_count: int = 0
    warning_count: int = 0
    responses: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def output_paths(output_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "json": base / f"{output_prefix}.json",
        "md": base / f"{output_prefix}.md",
        "manifest": base / f"{output_prefix}_manifest.json",
    }


def input_paths(input_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "json": base / f"{input_prefix}.json",
        "manifest": base / f"{input_prefix}_manifest.json",
    }


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required input missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def reject_write_sql(sql: str) -> None:
    upper = " ".join(sql.upper().split())
    for token in FORBIDDEN_SQL_TOKENS:
        if upper.startswith(token) or f" {token} " in upper:
            raise RuntimeError(f"read_only SQL guard rejected statement containing {token}")


def connect_sqlite_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def row_counts(conn: sqlite3.Connection, table_names: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name in table_names:
        if not child_builder.object_exists(conn, name, "table"):
            counts[name] = -1
            continue
        reject_write_sql(f"SELECT COUNT(*) FROM {name}")
        counts[name] = int(conn.execute(f"SELECT COUNT(*) FROM {child_builder.quote_ident(name)}").fetchone()[0])
    return counts


def estimate_tokens(text: str) -> int:
    return pack_builder.estimate_tokens(text)


def build_response_text(used_facts: list[dict[str, Any]]) -> str:
    if not used_facts:
        return "Insufficient evidence. evidence_refs: none"
    lines = ["Dry-run response from allowed facts only:", ""]
    for fact in used_facts:
        refs = ",".join(str(item) for item in fact.get("source_evidence_ref_ids", []))
        lines.append(f"- {fact.get('fact_text', '')} [{refs}]")
    return "\n".join(lines)


def build_response(pack: dict[str, Any], *, token_budget: int) -> tuple[dict[str, Any], bool]:
    context_pack_id = str(pack.get("query_id", ""))
    allowed_facts = list(pack.get("allowed_facts", []))
    evidence_refs = list(pack.get("evidence_refs", []))
    source_status = str(pack.get("context_pack_status", "insufficient"))
    warnings = list(pack.get("warnings", []))

    if not allowed_facts:
        response_text = "Insufficient evidence. evidence_refs: none"
        return (
            {
                "context_pack_id": context_pack_id,
                "query_id": str(pack.get("query_id", "")),
                "query_text": str(pack.get("query_text", "")),
                "source_context_pack_status": source_status,
                "response_status": "insufficient",
                "response_text": response_text,
                "used_fact_ids": [],
                "used_evidence_ids": [],
                "unused_fact_ids": [],
                "evidence_refs": [],
                "token_budget": token_budget,
                "token_budget_estimate": estimate_tokens(response_text),
                "warnings": warnings,
            },
            False,
        )

    used_facts = allowed_facts[:]
    response_text = build_response_text(used_facts)
    token_estimate = estimate_tokens(response_text)
    was_over_budget = token_estimate > token_budget
    while token_estimate > token_budget and len(used_facts) > 1:
        used_facts = used_facts[:-1]
        response_text = build_response_text(used_facts)
        token_estimate = estimate_tokens(response_text)
    if was_over_budget and token_estimate > token_budget:
        warnings.append("response_token_budget_exceeded_after_minimum_fact")
    elif was_over_budget:
        warnings.append("used_facts_truncated_to_fit_token_budget")

    used_fact_ids = [str(fact.get("fact_id", "")) for fact in used_facts]
    used_evidence_ids: list[str] = []
    for fact in used_facts:
        for evidence_id in fact.get("source_evidence_ref_ids", []):
            if str(evidence_id) not in used_evidence_ids:
                used_evidence_ids.append(str(evidence_id))
    evidence_by_id = {str(ref.get("evidence_ref_id", "")): ref for ref in evidence_refs}
    used_evidence_refs = [evidence_by_id[evidence_id] for evidence_id in used_evidence_ids if evidence_id in evidence_by_id]
    unused_fact_ids = [str(fact.get("fact_id", "")) for fact in allowed_facts if str(fact.get("fact_id", "")) not in used_fact_ids]

    response_status = "ready" if source_status == "ready" else "partial"
    if not used_fact_ids or not used_evidence_ids:
        response_status = "insufficient"

    return (
        {
            "context_pack_id": context_pack_id,
            "query_id": str(pack.get("query_id", "")),
            "query_text": str(pack.get("query_text", "")),
            "source_context_pack_status": source_status,
            "response_status": response_status,
            "response_text": response_text,
            "used_fact_ids": used_fact_ids,
            "used_evidence_ids": used_evidence_ids,
            "unused_fact_ids": unused_fact_ids,
            "evidence_refs": used_evidence_refs,
            "token_budget": token_budget,
            "token_budget_estimate": token_estimate,
            "warnings": warnings,
        },
        was_over_budget,
    )


def render_markdown(responses: list[dict[str, Any]]) -> str:
    lines = ["# L3.10 Prompt Consumer Dry Run", ""]
    for response in responses:
        lines.extend(
            [
                f"## Query: {response.get('query_text', '')}",
                "",
                f"Context pack id: {response.get('context_pack_id', '')}  ",
                f"Status: {response.get('response_status', '')}  ",
                f"Token estimate: {response.get('token_budget_estimate', 0)}",
                "",
                f"Used fact ids: {', '.join(response.get('used_fact_ids', [])) or 'none'}  ",
                f"Used evidence ids: {', '.join(response.get('used_evidence_ids', [])) or 'none'}  ",
                f"Unused fact ids: {', '.join(response.get('unused_fact_ids', [])) or 'none'}",
                "",
                "```text",
                str(response.get("response_text", "")),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def compute_counts(result: DryRunResult) -> None:
    result.response_count = len(result.responses)
    result.ready_response_count = sum(1 for item in result.responses if item["response_status"] == "ready")
    result.partial_response_count = sum(1 for item in result.responses if item["response_status"] == "partial")
    result.insufficient_response_count = sum(1 for item in result.responses if item["response_status"] == "insufficient")
    result.total_used_fact_count = sum(len(item["used_fact_ids"]) for item in result.responses)
    result.total_used_evidence_count = sum(len(item["used_evidence_ids"]) for item in result.responses)
    result.avg_used_facts_per_response = round(result.total_used_fact_count / result.response_count, 2) if result.response_count else 0.0
    result.warning_count = len(result.warnings) + sum(len(item.get("warnings", [])) for item in result.responses)


def build_manifest(
    result: DryRunResult,
    *,
    created_at: str,
    source_table_names: list[str],
    source_row_counts_before: dict[str, int],
    source_row_counts_after: dict[str, int],
) -> dict[str, Any]:
    return {
        "layer": "L3.10",
        "input_prefix": result.input_prefix,
        "output_prefix": result.output_prefix,
        "query_count": result.query_count,
        "response_count": result.response_count,
        "ready_response_count": result.ready_response_count,
        "partial_response_count": result.partial_response_count,
        "insufficient_response_count": result.insufficient_response_count,
        "total_used_fact_count": result.total_used_fact_count,
        "total_used_evidence_count": result.total_used_evidence_count,
        "avg_used_facts_per_response": result.avg_used_facts_per_response,
        "token_budget": result.token_budget,
        "over_token_budget_count": result.over_token_budget_count,
        "source_table_mutation_count": result.source_table_mutation_count,
        "forbidden_final_table_count": result.forbidden_final_table_count,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "errors": result.errors,
        "warnings": result.warnings,
        "created_at": created_at,
        "source_table_names": source_table_names,
        "source_row_counts_before": source_row_counts_before,
        "source_row_counts_after": source_row_counts_after,
        "no_llm_calls": True,
        "embedding_model_calls": False,
        "chroma_access": False,
        "read_only": True,
    }


def run_dryrun(
    project_dir: Path | str,
    *,
    input_prefix: str = DEFAULT_INPUT_PREFIX,
    output_prefix: str = DEFAULT_OUTPUT_PREFIX,
    read_only: bool = True,
) -> DryRunResult:
    root = Path(project_dir).resolve()
    child_builder.ensure_dirs(root)
    db_path = root / child_builder.DB_RELATIVE_PATH
    result = DryRunResult(False, root, db_path, input_prefix, output_prefix)
    created_at = now_iso()
    try:
        if not read_only:
            raise RuntimeError("L3.10 dryrun must run with --read-only")
        pack_payload = load_json(root / input_paths(input_prefix)["json"])
        pack_manifest = load_json(root / input_paths(input_prefix)["manifest"])
        context_packs = list(pack_payload.get("context_packs", []))
        result.query_count = int(pack_payload.get("query_count", len(context_packs)))
        result.token_budget = int(pack_manifest.get("token_budget", 0))
        if result.token_budget <= 0:
            raise RuntimeError("invalid or missing token_budget in L3.9 manifest")

        conn = connect_sqlite_readonly(db_path)
        try:
            source_table_names = child_builder.discover_source_table_names(conn)
            source_counts_before = row_counts(conn, source_table_names)
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
            if result.forbidden_final_table_count:
                result.errors.append("forbidden final tables exist before L3.10 dryrun")
            for pack in context_packs:
                response, over_budget = build_response(pack, token_budget=result.token_budget)
                result.responses.append(response)
                if over_budget:
                    result.over_token_budget_count += 1
            source_counts_after = row_counts(conn, source_table_names)
            result.source_table_mutation_count = sum(
                1
                for name in sorted(set(source_counts_before) | set(source_counts_after))
                if source_counts_before.get(name) != source_counts_after.get(name)
            )
            result.forbidden_final_table_count = len(child_builder.forbidden_final_tables(conn))
        finally:
            conn.close()

        compute_counts(result)
        if result.query_count != result.response_count:
            result.errors.append("query_count does not match response_count")
        if result.source_table_mutation_count:
            result.errors.append("source table row count mutation detected")
        if result.forbidden_final_table_count:
            result.errors.append("forbidden final tables exist after L3.10 dryrun")
        result.error_count = len(result.errors)
        result.ok = result.error_count == 0

        write_json(
            root / output_paths(output_prefix)["json"],
            {
                "layer": "L3.10",
                "input_prefix": input_prefix,
                "output_prefix": output_prefix,
                "query_count": result.query_count,
                "responses": result.responses,
            },
        )
        write_text(root / output_paths(output_prefix)["md"], render_markdown(result.responses))
        write_json(
            root / output_paths(output_prefix)["manifest"],
            build_manifest(
                result,
                created_at=created_at,
                source_table_names=source_table_names,
                source_row_counts_before=source_counts_before,
                source_row_counts_after=source_counts_after,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        if not result.errors or str(exc) not in result.errors:
            result.errors.append(str(exc))
        result.error_count = len(result.errors)
        result.warning_count = len(result.warnings)
        result.ok = False
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run L3.10 prompt consumer dryrun from L3.9 context packs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--input-prefix", type=str, default=DEFAULT_INPUT_PREFIX)
    parser.add_argument("--output-prefix", type=str, default=DEFAULT_OUTPUT_PREFIX)
    parser.add_argument("--read-only", action="store_true")
    args = parser.parse_args()
    result = run_dryrun(
        args.project_dir,
        input_prefix=args.input_prefix,
        output_prefix=args.output_prefix,
        read_only=args.read_only,
    )
    print("L3.10 prompt consumer dryrun PASS" if result.ok else "L3.10 prompt consumer dryrun FAIL")
    if result.ok:
        print(f"response_count={result.response_count}")
    else:
        for error in result.errors:
            print(f"ERROR: {error}")
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
