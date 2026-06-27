# L3.14 Real LLM Regression Suite contract

## Positioning

L3.14 is a repeatable acceptance wrapper around the L3.11 real LLM consumer sandbox and the L3.12 generation verifier.

By default it runs in replay mode. Replay mode reads existing sandbox output, sandbox manifest, and verifier reports. It does not call an LLM, does not access the network, does not write SQLite, does not write Chroma, does not call embeddings, and does not modify L1/L2/L3 source tables.

Real LLM mode is opt-in only. It is allowed only when both `--real-llm` is passed and `NOVELRAG_ALLOW_REAL_LLM=1` is set.

## Inputs

```text
outputs/{sandbox_output_prefix}.json
outputs/{sandbox_output_prefix}_manifest.json
outputs/{sandbox_output_prefix}_verify_report.json
outputs/{sandbox_output_prefix}_verify_report.md
```

The verifier JSON report is preferred. The markdown report may exist as a companion artifact.

## Outputs

```text
outputs/l3_real_llm_regression_suite_report.json
outputs/l3_real_llm_regression_suite_report.md
outputs/l3_real_llm_regression_suite_manifest.json
```

## CLI

```text
python scripts/l3_real_llm_regression_suite.py \
  --project-dir D:\NovelRAG \
  --sandbox-output-prefix l3_real_llm_consumer_sandbox_sample \
  --input-prefix l3_prompt_context_pack_sample \
  --sample-chapters 1 \
  --provider openai_compatible \
  --temperature 1 \
  --max-output-tokens 500 \
  --read-only
```

Add `--real-llm` only for explicitly authorized live Kimi/OpenAI-compatible execution.

## Real LLM Gate

Real LLM mode must refuse execution unless all of these are true:

- `--real-llm` is present.
- `NOVELRAG_ALLOW_REAL_LLM=1`.
- `NOVELRAG_LLM_BASE_URL` is set.
- `NOVELRAG_LLM_API_KEY` is set.
- `NOVELRAG_LLM_MODEL` is set.

The API key must never be printed or written to reports.

## PASS Conditions

The suite passes only when all of these are true:

- `sandbox_status = PASS`
- `verifier_status = FULL PASS`
- `response_count > 0`
- `json_parse_error_count = 0`
- `out_of_pack_fact_ref_count = 0`
- `out_of_pack_evidence_ref_count = 0`
- `source_table_mutation_count = 0`
- `forbidden_final_table_count = 0`
- `api_key_leak_detected = false`
- `read_only = true`
- `sqlite_write = false`
- `chroma_write = false`
- `embedding_call = false`
- `source_mutation = false`

Warnings do not directly fail the suite.

## Warning Conditions

- `response_count` does not match the configured sample scope.
- Response hashes differ from historical artifacts.
- Token budgets vary.
- Temperature is not fixed at `1`.

## API Key Leak Detection

The suite scans checked and generated artifacts for:

- `sk-`
- the full current value of `NOVELRAG_LLM_API_KEY`
- the full current value of `MOONSHOT_API_KEY`

Reports may record only the boolean `api_key_leak_detected`; they must not echo secret values.

## Report JSON Shape

```json
{
  "layer": "L3.14",
  "suite_name": "real_llm_regression_suite",
  "status": "PASS",
  "mode": "replay",
  "sandbox_output_prefix": "l3_real_llm_consumer_sandbox_sample",
  "input_prefix": "l3_prompt_context_pack_sample",
  "provider": "openai_compatible",
  "model": "",
  "base_url_host": "",
  "temperature": 1,
  "max_output_tokens": 500,
  "sandbox_status": "PASS",
  "verifier_status": "FULL PASS",
  "response_count": 1,
  "json_parse_error_count": 0,
  "unsupported_claim_count": 0,
  "out_of_pack_fact_ref_count": 0,
  "out_of_pack_evidence_ref_count": 0,
  "source_table_mutation_count": 0,
  "forbidden_final_table_count": 0,
  "api_key_leak_detected": false,
  "errors": [],
  "warnings": [],
  "checked_files": [],
  "generated_files": []
}
```

## Final Output Line

On success the CLI prints:

```text
L3.14 real llm regression suite FULL PASS
```
