# L3 scene_blocks Contract

## Status

This contract covers only the L3 scene_blocks sample build.

Allowed scope:

- `chapter_num = 1`
- `chapter_num = 2`
- `chapter_num = 1697`

`--chapter-num` is also limited to these three chapters. Any chapter outside this sample scope must fail.

This pass does not mean:

- L3 scene_blocks full build is complete.
- L3 is complete.
- L3.1 worldline, character, region, timeline, Chroma, embedding, or storyboard work can start automatically.

## Inputs

The builder and verifier may read only these SQLite views as content authority:

- `v_current_chapters`
- `v_l2_current_paragraphs`
- `v_l2_current_sentences`

The verifier must reload these L1/L2 views and validate L3 rows against them. L3 tables are never allowed to self-prove correctness.

Forbidden in this round:

- Raw txt reads
- L1/L2 modification
- LLM extraction
- Full 1922-chapter build
- Worldline index
- Character appearance index
- Region/domain index
- Timeline hint
- child_segments
- Chroma
- embedding
- storyboard logic

## Tables

`l3_scene_blocks` stores paragraph-aligned parent evidence blocks:

- `scene_id INTEGER PRIMARY KEY AUTOINCREMENT`
- `scene_key TEXT NOT NULL UNIQUE`
- `chapter_id TEXT NOT NULL`
- `version_id TEXT NOT NULL`
- `chapter_num INTEGER NOT NULL`
- `scene_index_in_chapter INTEGER NOT NULL`
- `start_para_id TEXT NOT NULL`
- `end_para_id TEXT NOT NULL`
- `start_para_index INTEGER NOT NULL`
- `end_para_index INTEGER NOT NULL`
- `start_sentence_id TEXT`
- `end_sentence_id TEXT`
- `start_offset INTEGER NOT NULL`
- `end_offset INTEGER NOT NULL`
- `length INTEGER NOT NULL`
- `scene_kind TEXT NOT NULL`
- `split_reason TEXT NOT NULL`
- `summary_short TEXT`
- `source_hash TEXT NOT NULL`
- `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`
- `UNIQUE(chapter_id, version_id, scene_index_in_chapter)`

`l3_scene_block_status` stores one status row per current chapter/version:

```sql
CREATE TABLE IF NOT EXISTS l3_scene_block_status (
    chapter_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    chapter_num INTEGER NOT NULL,
    status TEXT NOT NULL,
    scene_count INTEGER NOT NULL,
    paragraph_count INTEGER NOT NULL,
    sentence_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(chapter_id, version_id)
);
```

`status` is fixed to `indexed`.

## Stable Keys And Enums

`scene_key` format:

```text
{chapter_id}:{version_id}:scene:{scene_index_in_chapter}
```

If `chapter_id` or `version_id` contains `:`, build and verify must fail.

`scene_kind` values:

- `normal`
- `separator`
- `author_note`
- `promo`
- `noise`

Normal paragraph aggregation uses `normal`. Special paragraphs are isolated into one block and use their matching `para_kind`.

`split_reason` values:

- `target_max_reached`
- `chapter_end`
- `single_long_paragraph`
- `strong_separator_trigger`
- `strong_separator_block`

## Hash And Coordinates

Blocks are paragraph-boundary units. The builder must not split sentences or alter L2 coordinates.

For each block:

```text
start_offset = first paragraph start_offset
end_offset = last paragraph end_offset
length = end_offset - start_offset
source_hash = sha256(content_full_text[start_offset:end_offset].encode("utf-8"))
```

The hash must not be calculated from paragraph concatenation, trimmed text, normalized text, summaries, or generated text.

## Commands

Required acceptance sequence:

```powershell
python -m unittest tests.test_l3_scene_blocks
python scripts\l3_scene_block_builder.py --project-dir D:\NovelRAG --sample-chapters 1,2,1697 --rebuild
python scripts\l3_verify_scene_blocks.py --project-dir D:\NovelRAG --sample-chapters 1,2,1697
python -m compileall scripts tests
```

PASS requires `outputs/l3_scene_blocks_verify_report.md` to contain:

```text
L3 SCENE BLOCKS VERIFY PASS
```
