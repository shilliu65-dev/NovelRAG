# L3 Trusted Seed Evidence Packet Contract

## Purpose

Build a derived trusted-evidence packet from manually reviewed top3 seed evidence.

This artifact is:

- a derived review-layer packet
- read-only with respect to seed source files
- not a seed fact writer

It must not:

- modify `index/*.db`
- modify `config/*.seed.json`
- overwrite existing unrelated `outputs/l3_*`
- call LLMs
- create embeddings
- write Chroma
- rewrite L1 or L2

## Primary Input

Default reviewed input:

- `outputs/l3_evidence_review_manual_template_top3.json`

The packet consumes only manual review results and seed config structure.
For full seed coverage, it also reads the canonical seed universe from the seed-evidence candidates file rather than recursively treating every nested config object as a seed rule.

## Human Status Rules

Allowed `human_status` values only:

- `null`
- `accepted`
- `rejected`
- `needs_more`

Evidence handling:

- `accepted` enters `trusted_evidence`
- `rejected` does not enter `trusted_evidence`
- `needs_more` does not enter `trusted_evidence`, but summary must count it
- `null` does not enter `trusted_evidence`, but summary must count it as pending

## Coverage Rules

- `seed_rules` must cover all seed items from `config/*.seed.json`
- a seed with zero accepted evidence is valid and must still be emitted
- `trusted_evidence` must contain at most `3` accepted evidence items per seed by default

## Output Shape

Top-level JSON structure:

- `meta`
- `summary`
- `seed_rules`

Required `meta` fields:

- `artifact_type`
- `version`
- `project`
- `source_files`
- `generated_at`
- `notes`

Required `summary` fields:

- `seed_items_total`
- `seed_items_with_accepted_evidence`
- `seed_items_without_accepted_evidence`
- `accepted_evidence_count`
- `rejected_evidence_count`
- `needs_more_evidence_count`
- `pending_evidence_count`
- `max_accepted_evidence_per_seed`
- `all_seed_items_lte_3`

Each `seed_rules[]` item must contain:

- `seed_item_id`
- `seed_file`
- `seed_path`
- `seed_rule_type`
- `seed_rule_text`
- `trusted_status`
- `trusted_evidence`

Allowed `trusted_status` values:

- `trusted`
- `no_accepted_evidence`
- `needs_more`
- `pending`

Each `trusted_evidence[]` item must contain:

- `candidate_id`
- `chapter_id`
- `chapter_num`
- `paragraph_hash`
- `evidence_text`
- `backcut`
- `human_status`
- `review_note`
- `evidence_hash`

## Stability

Outputs must be stable and diff-friendly:

- `seed_rules` sorted by `seed_file`, then `seed_item_id`
- `trusted_evidence` kept in stable retained order from reviewed input
- `evidence_hash` built deterministically from `seed_item_id + candidate_id + paragraph_hash + evidence_text`
- repeated runs with the same inputs must be idempotent
