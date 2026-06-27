# L3.6 Gray Realm / Disaster Entity / Habitat / Invasion Event Index

L3.6 is a deterministic, no-LLM, no-embedding, no-Chroma evidence index for gray realm disaster ecology. It is not a normal location index.

## Scope

L3.6 records four reviewed evidence surfaces:

- `zone_appearance_candidate`: gray realm, gray tide, polluted space, disaster ecology, and other abnormal disaster spaces.
- `disaster_appearance_candidate`: the seven disaster entities as high-tier disaster/Boss entities.
- `habitat_appearance_candidate`: explicitly evidenced lairs, territories, nests, habitats, domains, or placeholder habitats for the seven disasters.
- `disaster_invasion_candidate`: erosion, invasion, pollution, coverage, expansion, boundary movement, occupation, containment, purification, or collapse events caused by gray realm, gray tide, or the seven disasters.

## L3.5 Boundary

L3.5 owns ordinary locations, regions, cities, buildings, strongholds, and the nine domains. L3.6 must not write those as `l3_disaster_zone_def` or habitat records.

The nine domains may appear in L3.6 only as affected targets in `l3_disaster_invasion_event`, when there is evidence that gray realm, gray tide, or a disaster entity is invading, eroding, polluting, covering, or otherwise affecting them.

## Gray Realm Rule

The gray realm is a special abnormal parent ecology, not an ordinary location. It may invade, pollute, cover, expand into, or erode other spaces. Matching must never use the single character `灰` as a positive pattern.

## Seven Disasters

The seven disasters are:

- 嘲灾
- 忌灾
- 息灾
- 浊灾
- 妄灾
- 思灾
- 寂灾

`disaster_ji` is 忌灾. `disaster_ji_mie` is 寂灾. They must not be confused.

`disaster_chao` is a first-version special case. This version only seeds it, extracts candidate appearances, and writes accepted evidence. It does not infer special mechanisms.

## Habitat Rule

The seven disasters may have habitats, territories, lairs, nests, ecology zones, domains, or sealed areas. If the text does not explicitly name a concrete habitat, L3.6 creates only deterministic placeholder habitats:

- `habitat_chao_unknown`
- `habitat_ji_unknown`
- `habitat_xi_unknown`
- `habitat_zhuo_unknown`
- `habitat_wang_unknown`
- `habitat_si_unknown`
- `habitat_ji_mie_unknown`

No script may invent concrete territory names or infer which disaster occupies a location without textual evidence.

## Review And Apply

Candidate extraction is read-only and writes only JSON/Markdown outputs. It reads only:

- `v_current_chapters`
- `v_l2_current_paragraphs`
- `v_l2_current_sentences`

SQLite writes happen only in `scripts/l3_apply_disaster_ecology_review.py`, and only for candidates whose `human_status` is `accepted`. `rejected`, `needs_more`, and `null` are skipped. Invalid statuses stop the apply run.

Every accepted evidence row must be re-cut from current L2 coordinates before insert. The script recomputes `evidence_hash` and does not trust candidate-provided hashes.

## Tables

L3.6 owns exactly these tables:

- `l3_disaster_zone_def`
- `l3_disaster_zone_alias`
- `l3_disaster_zone_appearance`
- `l3_disaster_entity_def`
- `l3_disaster_entity_alias`
- `l3_disaster_entity_appearance`
- `l3_disaster_habitat_def`
- `l3_disaster_habitat_appearance`
- `l3_disaster_invasion_event`

Dynamic spread, erosion, pollution, coverage, expansion, retreat, collapse, purification, and containment events are all stored in `l3_disaster_invasion_event`. There is no separate spread-event table.

## Prohibitions

- Do not call LLMs.
- Do not call embedding models.
- Do not call Chroma.
- Do not modify L1.
- Do not modify L2.
- Do not modify `chapter_registry` or `chapter_contents`.
- Do not modify existing L3.4/L3.5 indexes.
- Do not write candidate-stage records to SQLite.
- Do not write ordinary locations into L3.6 zones or habitats.
- Do not write the nine domains as L3.6 zones.
- Do not use random UUIDs.

## Acceptance

Acceptance requires:

- `python -m unittest tests.test_l3_disaster_ecology_index -v` passes.
- `python -m compileall scripts tests` passes.
- Sample candidate JSON is generated for chapters `1,2,1697`.
- Review template JSON is generated.
- Accepted review rows can be written idempotently to the nine L3.6 tables.
- Verifier report passes with zero backcut errors, zero invalid type counts, zero ordinary-location leaks, and zero single-gray aliases.
