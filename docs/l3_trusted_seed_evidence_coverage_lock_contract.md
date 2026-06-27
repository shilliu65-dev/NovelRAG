# L3 Trusted Seed Evidence Coverage Lock Contract

## Scope

Stage D only performs trusted seed evidence coverage lock and uncovered-seed gap review.

This stage must not:

- modify `index/*.db`
- modify `config/*.seed.json`
- modify existing L1/L2/L3 index tables
- overwrite previous `outputs/l3_*` stage artifacts
- generate guessed evidence
- write accepted evidence back into seed files

## Required Inputs

The script reads only these JSON inputs:

- `outputs/l3_seed_rule_trusted_evidence_packet.json`
- `outputs/l3_seed_rule_trusted_evidence_summary.json`
- `outputs/l3_evidence_review_manual_template_top3_reviewed_all_accepted.json`
- `outputs/l3_seed_evidence_candidates.json`

It may read `config/*.seed.json` only for seed-item comparison if needed, but does not modify them.

## Script

- Path: `scripts/l3_lock_trusted_seed_evidence_coverage.py`
- CLI:

```bash
python scripts\l3_lock_trusted_seed_evidence_coverage.py ^
  --project-dir D:\NovelRAG ^
  --trusted-packet outputs\l3_seed_rule_trusted_evidence_packet.json ^
  --trusted-summary outputs\l3_seed_rule_trusted_evidence_summary.json ^
  --review-template outputs\l3_evidence_review_manual_template_top3_reviewed_all_accepted.json ^
  --candidates outputs\l3_seed_evidence_candidates.json
```

Optional deterministic test flag:

```bash
--fixed-generated-at 2026-06-25T13:00:00
```

## Outputs

The script writes new stage D artifacts only:

- `outputs/l3_trusted_seed_evidence_coverage_lock.json`
- `outputs/l3_trusted_seed_evidence_coverage_lock.md`
- `outputs/l3_trusted_seed_evidence_gap_review.json`
- `outputs/l3_trusted_seed_evidence_gap_review.md`
- `outputs/l3_trusted_seed_evidence_coverage_lock_validation_report.json`
- `outputs/l3_trusted_seed_evidence_coverage_lock_validation_report.md`

All JSON outputs must use stable key ordering from Python dict insertion order, stable item sorting, 2-space indentation, UTF-8, and trailing newline.

## Coverage Lock JSON

### `metadata`

- `generated_at`
- `project_dir`
- `input_files`
- `input_file_sha256`
- `script_name`
- `contract_version`

### `summary`

- `seed_items_total`
- `seed_items_with_accepted_evidence`
- `seed_items_without_accepted_evidence`
- `accepted_evidence_count`
- `pending_evidence_count`
- `rejected_evidence_count`
- `needs_more_evidence_count`
- `max_accepted_evidence_per_seed`
- `coverage_rate`

### `seed_coverage_items`

Exactly one entry per seed item. Stage D expects exactly 43 entries.

Each item contains:

- `seed_item_id`
- `seed_source_file`
- `seed_category`
- `seed_label`
- `coverage_status`
  - `covered`
  - `uncovered`
- `accepted_evidence_count`
- `candidate_ids`
- `chapter_nums`
- `evidence_refs`
- `gap_reason`
- `next_action`

`coverage_status` is `covered` only if the trusted packet contains accepted evidence for that seed.

`gap_reason` is emitted only for uncovered seeds and must be one of:

- `no_candidate_evidence`
- `no_review_evidence`
- `no_accepted_evidence`

### `integrity`

- `duplicate_seed_item_id_detected`
- `duplicate_seed_item_ids`
- `accepted_evidence_missing_candidate_detected`
- `accepted_evidence_missing_candidate_refs`
- `illegal_human_status_detected`
- `illegal_human_status_refs`
- `pending_evidence_detected`
- `accepted_evidence_exceeds_top3_limit_detected`
- `lock_validation_passed`

## Gap Review JSON / Markdown

Gap review lists uncovered seeds only.

Each gap item contains:

- `seed_item_id`
- `seed_source_file`
- `seed_category`
- `seed_content_summary`
- `current_status`
- `gap_reason`
- `risk_level`
  - `low`
  - `medium`
  - `high`
- `recommended_action`
  - `keep_as_uncovered_for_now`
  - `schedule_followup_evidence_search`
  - `revise_seed_later_if_no_evidence`

Current implementation uses:

- `high` for rule-like seed paths
- `medium` for godway/domain/lord/item paths
- `low` otherwise

## Validation Rules

The script must enforce:

1. `seed_items_total == 43`
2. `seed_coverage_items` length is exactly 43
3. `accepted_evidence_count` matches trusted packet and trusted summary
4. `pending_evidence_count == 0`
5. `max_accepted_evidence_per_seed <= 3`
6. `covered + uncovered == 43`
7. uncovered count equals gap review item count
8. every accepted evidence record must resolve to a candidate id in candidate inputs
9. JSON outputs are deterministic except for `generated_at`
10. reruns with `--fixed-generated-at` are byte-stable

The script fails fast on missing required inputs, illegal review status values, missing candidate back-references, pending review evidence, or top-3 overflow.

## Validation Report

The validation report records:

- input files read
- `index/*.db` modified: no
- `config/*.seed.json` modified: no
- whether pending evidence is cleared
- 43-seed coverage summary
- accepted evidence count across covered seeds
- uncovered seed list
- coverage lock pass/fail
- gap review generated or not
- recommended test commands
- stage E recommendation

The script lists test commands, but it does not execute them by itself.
