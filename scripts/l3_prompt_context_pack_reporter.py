from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_prompt_context_pack_builder as builder


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required L3.9 output missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def markdown_paths(output_prefix: str) -> dict[str, Path]:
    base = Path("outputs")
    return {
        "pack_md": base / f"{output_prefix}.md",
        "report_md": base / f"{output_prefix}_report.md",
    }


def render_pack_markdown(context_packs: list[dict[str, Any]]) -> str:
    lines = ["# L3.9 Prompt Context Pack Sample", ""]
    for pack in context_packs:
        lines.extend(
            [
                f"## Query: {pack.get('query_text', '')}",
                "",
                f"Status: {pack.get('context_pack_status', '')}  ",
                f"Confidence: {pack.get('confidence_level', '')}  ",
                f"Token estimate: {pack.get('token_budget_estimate', 0)}",
                "",
                "### Allowed Facts",
                "",
            ]
        )
        allowed_facts = list(pack.get("allowed_facts", []))
        if allowed_facts:
            lines.extend(f"- [{fact.get('fact_id', '')}] {fact.get('fact_text', '')}" for fact in allowed_facts)
        else:
            lines.append("- none")
        lines.extend(["", "### Evidence Refs", ""])
        evidence_refs = list(pack.get("evidence_refs", []))
        if evidence_refs:
            for evidence in evidence_refs:
                lines.append(
                    f"- [{evidence.get('evidence_ref_id', '')}] chapter={evidence.get('chapter_num', '')} "
                    f"child_segment_id={evidence.get('child_segment_id', '')}"
                )
                lines.append(f"  excerpt: {evidence.get('text_excerpt', '')}")
        else:
            lines.append("- none")
        lines.extend(["", "### Forbidden Inference Rules", ""])
        rules = list(pack.get("forbidden_inference_rules", []))
        if rules:
            lines.extend(f"{index}. {rule}" for index, rule in enumerate(rules, start=1))
        else:
            lines.append("1. none")
        lines.extend(
            [
                "",
                "### Prompt Context",
                "",
                "```text",
                str(pack.get("prompt_context_text", "")),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def render_report_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# L3.9 Prompt Context Pack Report",
        "",
        f"- layer: {manifest.get('layer', '')}",
        f"- input_prefix: {manifest.get('input_prefix', '')}",
        f"- output_prefix: {manifest.get('output_prefix', '')}",
        f"- chapter_scope: {manifest.get('chapter_scope', '')}",
        f"- query_count: {manifest.get('query_count', 0)}",
        f"- context_pack_count: {manifest.get('context_pack_count', 0)}",
        f"- ready_pack_count: {manifest.get('ready_pack_count', 0)}",
        f"- partial_pack_count: {manifest.get('partial_pack_count', 0)}",
        f"- insufficient_pack_count: {manifest.get('insufficient_pack_count', 0)}",
        f"- total_allowed_fact_count: {manifest.get('total_allowed_fact_count', 0)}",
        f"- total_evidence_ref_count: {manifest.get('total_evidence_ref_count', 0)}",
        f"- avg_evidence_refs_per_pack: {manifest.get('avg_evidence_refs_per_pack', 0.0)}",
        f"- token_budget: {manifest.get('token_budget', 0)}",
        f"- over_token_budget_count: {manifest.get('over_token_budget_count', 0)}",
        f"- hash_mismatch_count: {manifest.get('hash_mismatch_count', 0)}",
        f"- missing_sqlite_child_count: {manifest.get('missing_sqlite_child_count', 0)}",
        f"- source_table_mutation_count: {manifest.get('source_table_mutation_count', 0)}",
        f"- forbidden_final_table_count: {manifest.get('forbidden_final_table_count', 0)}",
        f"- error_count: {manifest.get('error_count', 0)}",
        f"- warning_count: {manifest.get('warning_count', 0)}",
        "",
    ]
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def run_report(project_dir: Path | str, *, output_prefix: str = builder.DEFAULT_OUTPUT_PREFIX) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    pack_payload = load_json(root / builder.output_paths(output_prefix)["packs"])
    manifest = load_json(root / builder.output_paths(output_prefix)["manifest"])
    write_text(
        root / markdown_paths(output_prefix)["pack_md"],
        render_pack_markdown(list(pack_payload.get("context_packs", []))),
    )
    write_text(
        root / markdown_paths(output_prefix)["report_md"],
        render_report_markdown(manifest),
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Render L3.9 prompt context pack markdown outputs.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output-prefix", type=str, default=builder.DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    manifest = run_report(args.project_dir, output_prefix=args.output_prefix)
    print("L3.9 prompt context pack REPORT PASS")
    print(f"context_pack_count={manifest['context_pack_count']}")


if __name__ == "__main__":
    main()
