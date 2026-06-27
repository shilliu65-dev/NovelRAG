# L3 Evidence Review Apply Contract

## Purpose

Apply deterministic human review decisions to the L3 evidence review queue.

This layer does not retrieve new evidence. It only:

- emits a manual review template;
- validates `human_status`;
- splits reviewed evidence into stable accepted, rejected, and needs-more outputs;
- keeps unreviewed candidates in summary counts only.

## Hard Constraints

- No LLM calls.
- No embedding.
- No vector database.
- No Chroma writes.
- No L1 or L2 mutation.
- No seed file mutation.
- Candidate status must remain `candidate`.
- Script output must be idempotent for the same input and arguments.

## Inputs

- `--project-dir`: project root.
- `--input`: one JSON file from:
  - `outputs/l3_evidence_review_queue.json`
  - any existing `outputs/l3_evidence_review_*.json`
  - a previously emitted `outputs/l3_evidence_review_manual_template.json`
- `--emit-review-template`: emit a manual review template.
- `--top-k-per-seed`: max candidates kept per `seed_item_id`, default `3`.

Supported input item shapes:

1. Canonical review queue shape
   - `meta`
   - `summary`
   - `review_items[]`
2. Manual template shape
   - `items[].seed_item_id`
   - `items[].review_candidates[]`
3. Legacy candidate list shape still tolerated for compatibility
   - `items[].seed_source`
   - `items[].candidates[]`

The canonical verifier output is `meta + summary + review_items`.
`l3_apply_evidence_review.py` must consume that canonical queue shape without requiring any database lookup.

If `candidate_id` is missing, the script must synthesize a deterministic ID from seed and paragraph identity.

## Human Review Rules

Allowed `human_status` values only:

- `null`
- `accepted`
- `rejected`
- `needs_more`

Any other value must raise an error.

When `--emit-review-template` is used:

- every emitted `review_candidates[].human_status` must be `null`;
- every emitted `review_candidates[].review_note` must be `null`;
- original candidate evidence fields must be preserved.

## Accepted Evidence Guarantee

Each accepted evidence item must retain:

- `seed_item_id`
- `candidate_id`
- `chapter_id`
- `chapter_num`
- `version_id`
- `paragraph_id`
- `paragraph_index`
- `char_start`
- `char_end`
- `paragraph_hash`
- `l2_coordinates`
- `evidence_text`
- `backcut`

Accepted evidence must preserve enough backcut and paragraph identity information to remain verifiable by downstream L3 evidence review verification logic.

## Outputs

- `outputs/l3_evidence_review_manual_template.json`
- `outputs/l3_seed_evidence_accepted.json`
- `outputs/l3_seed_evidence_rejected.json`
- `outputs/l3_seed_evidence_needs_more.json`
- `outputs/l3_seed_evidence_review_summary.json`

Summary must include:

- total candidate count after `top-k-per-seed`
- candidate count by `seed_item_id`
- status counts for:
  - `accepted`
  - `rejected`
  - `needs_more`
  - `unreviewed`

## Count Semantics

`l3_evidence_review_manual_template.json` is built from retained evidence candidates only.
It therefore counts only seed items that still have reviewable evidence after the queue/top-k filter.

This differs from `l3_seed_rule_review_packet.json`, which is a full manual reading packet over all seed rules from `config/*.seed.json`.
That packet may include seed items with zero retained evidence and must allow `seed_items_without_evidence > 0`.

So a template count such as `41` and a packet count such as `43` are not contradictory:

- `41` means seed items with retained evidence in the apply/template path
- `43` means total seed items in the full packet path
