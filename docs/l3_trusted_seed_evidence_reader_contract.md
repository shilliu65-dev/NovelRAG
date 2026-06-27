# L3 Trusted Seed Evidence Reader Contract

## Scope

This step implements a read-only in-memory reader over:

- `outputs/l3_seed_rule_trusted_evidence_packet.json`
- `outputs/l3_trusted_seed_evidence_coverage_lock.json`
- `outputs/l3_trusted_seed_evidence_gap_review.json`

The reader does not:

- modify `index/*.db`
- modify `config/*.seed.json`
- modify L1/L2/L3 core index tables
- generate new evidence
- repair uncovered gaps
- call models
- generate embeddings
- use vector stores

## Files

- Script: `scripts/l3_trusted_seed_evidence_reader.py`
- Tests: `tests/test_l3_trusted_seed_evidence_reader.py`
- Contract: `docs/l3_trusted_seed_evidence_reader_contract.md`

## Outputs

- `outputs/l3_trusted_seed_evidence_reader_sample.json`
- `outputs/l3_trusted_seed_evidence_reader_sample.md`
- `outputs/l3_trusted_seed_evidence_reader_validation_report.json`
- `outputs/l3_trusted_seed_evidence_reader_validation_report.md`

## In-Memory Indexes

The reader builds these indexes in memory only:

1. `by_seed_item_id`
2. `by_seed_file`
3. `by_seed_rule_type`
4. `by_trusted_status`
5. `by_chapter_num`
6. `by_candidate_id`
7. `uncovered_seed_items`

## CLI

```bash
python scripts\l3_trusted_seed_evidence_reader.py --project-dir D:\NovelRAG
```

Supported flags:

- `--trusted-packet`
- `--coverage-lock`
- `--gap-review`
- `--seed-item-id`
- `--seed-file`
- `--seed-rule-type`
- `--trusted-status`
- `--chapter-num`
- `--include-evidence-text`
- `--out-json`
- `--out-md`

## JSON Output Shape

Top-level shape:

```json
{
  "meta": {},
  "summary": {},
  "results": [],
  "uncovered_seed_items": []
}
```

### `summary`

- `seed_items_total`
- `covered_seed_items`
- `uncovered_seed_items`
- `accepted_evidence_count`
- `query_result_count`
- `filters`

### `results[*]`

- `seed_item_id`
- `seed_file`
- `seed_path`
- `seed_rule_type`
- `seed_rule_text`
- `trusted_status`
- `coverage_status`
- `accepted_evidence_count`
- `evidence_refs`

### `evidence_refs[*]`

- `candidate_id`
- `chapter_id`
- `chapter_num`
- `paragraph_hash`
- `evidence_hash`
- `evidence_text` when `--include-evidence-text` is enabled
- `backcut`
- `human_status`

Only accepted evidence may appear in query results.

## Markdown Output

The Markdown sample is intended for manual inspection and includes:

- summary counts
- active filters
- per-result seed metadata
- evidence references
- uncovered seed items from gap review

## Validation Rules

The reader must fail fast when:

- trusted packet and coverage lock disagree on total seeds
- trusted packet and coverage lock disagree on covered seeds
- trusted packet and coverage lock disagree on uncovered seeds
- trusted packet and coverage lock disagree on accepted evidence count
- trusted packet and coverage lock disagree on max accepted evidence per seed
- gap review uncovered count disagrees with coverage lock / trusted packet
- coverage lock integrity does not pass
- trusted packet contains any non-accepted evidence inside `trusted_evidence`

## Tests

Minimum test coverage:

- inputs can be read successfully
- seed item filtering works
- seed file filtering works
- seed rule type filtering works
- trusted status filtering works
- chapter number filtering works
- uncovered seed items are returned correctly
- only accepted evidence is returned
- count mismatches fail fast
- repeated runs are deterministic with fixed timestamps
- static safety checks confirm no DB / model / vector-store usage

## Next Step

If the reader passes, the next step is L3 Story Bible seed projection sampling based on this reader, without direct reads from `config/*.seed.json` and without model calls.
