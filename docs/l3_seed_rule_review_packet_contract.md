# L3 Seed Rule Review Packet Contract

## Purpose

Build a deterministic human reading packet for reviewing whether each seed rule is supported by original-text evidence.

This layer is a read-only review packet generator. It does not:

- write facts back into seed files
- confirm rules automatically
- query databases
- call LLMs
- create embeddings
- write Chroma

## Inputs

- `config/*.seed.json`
- `outputs/l3_evidence_review_queue.json`
- `outputs/l3_seed_evidence_candidates.json`

The canonical review queue input shape is:

- `meta`
- `summary`
- `review_items`

The packet also reads the candidate file to preserve per-seed candidate ordering and to keep zero-evidence seed items in scope.

## Per-Seed Output

Each seed item must include:

- `seed_item_id`
- `seed_category`
- `seed_rule_content`
- `rule_status`
- `rule_note`
- `evidence_candidates`

Allowed `rule_status` values only:

- `null`
- `confirmed`
- `revise`
- `needs_more`
- `unsupported`
- `conflict`

`rule_status` defaults to `null`.
`rule_note` defaults to `""`.

## Evidence Output

Each evidence item must include:

- `candidate_id`
- `chapter_id`
- `chapter_num`
- `evidence_text`
- `l2_coordinates`
- `paragraph_hash`
- `backcut`
- `human_status`
- `human_note`

Allowed `human_status` values only:

- `null`
- `accepted`
- `rejected`
- `needs_more`

## Zero-Evidence Seeds

The packet is a full reading packet, not a fact writer.

It must allow:

- `seed_items_without_evidence > 0`

A seed item with zero evidence is still emitted with:

- its rule content
- empty `evidence_candidates`
- default `rule_status`
- default `rule_note`

This is expected behavior, not failure.

## Top-K

Default packet retention is:

- at most `3` evidence candidates per `seed_item_id`

The packet keeps candidate ordering from `l3_seed_evidence_candidates.json`.

## Count Semantics

`l3_evidence_review_manual_template.json` and `l3_seed_rule_review_packet.json` do not count the same thing.

- The apply template counts only seed items that still have retained evidence after queue/template filtering.
- The packet counts all seed items found in `config/*.seed.json`, including those with zero retained evidence.

Therefore a result such as:

- template seed items = `41`
- packet seed items = `43`

is expected and not contradictory.

## Outputs

- `outputs/l3_seed_rule_review_packet.json`
- `outputs/l3_seed_rule_review_packet.md`
- `outputs/l3_seed_rule_review_summary.json`

All JSON outputs must be deterministic, stable across repeated runs for the same inputs, and suitable for diff-based review.
