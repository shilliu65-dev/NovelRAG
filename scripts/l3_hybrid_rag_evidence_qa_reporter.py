from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_hybrid_rag_evidence_qa as qa


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required L3.8 output missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def run_report(project_dir: Path | str, *, output_prefix: str = qa.DEFAULT_OUTPUT_PREFIX) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    paths = qa.output_paths(output_prefix)
    answers_payload = load_json(root / paths["answers_json"])
    manifest = load_json(root / paths["manifest"])
    qa.write_report(root, output_prefix, manifest, list(answers_payload.get("answers", [])))
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild L3.8 hybrid RAG evidence QA report.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output-prefix", type=str, default=qa.DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    manifest = run_report(args.project_dir, output_prefix=args.output_prefix)
    print("L3.8 hybrid RAG evidence QA REPORT PASS")
    print(f"answer_count={manifest['answer_count']}")


if __name__ == "__main__":
    main()
