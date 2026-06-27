from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import l3_hybrid_rag_retriever as retriever


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise RuntimeError(f"required retrieval output missing: {path.name}")
    return json.loads(path.read_text(encoding="utf-8"))


def run_report(project_dir: Path | str, *, output_prefix: str = retriever.DEFAULT_OUTPUT_PREFIX) -> dict[str, Any]:
    root = Path(project_dir).resolve()
    paths = retriever.relative_paths_for_prefix(output_prefix)
    manifest = load_json(root / paths["manifest"])
    results = load_json(root / paths["results_json"])
    retriever.write_report(root, manifest, results, output_prefix=output_prefix)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild L3.7 hybrid RAG retrieval markdown report.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--output-prefix", type=str, default=retriever.DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    manifest = run_report(args.project_dir, output_prefix=args.output_prefix)
    if manifest.get("embedding_mode") == "real_embedding":
        print("L3.7b real embedding retrieval quality REPORT PASS")
    else:
        print("L3.7 Hybrid RAG retrieval REPORT PASS")
    print(f"merged_hit_count={manifest['merged_hit_count']}")


if __name__ == "__main__":
    main()
