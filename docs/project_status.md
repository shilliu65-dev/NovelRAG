# NovelRAG Project Status

Last updated: 2026-06-24

This document records the current acceptance state only. It is not a new module plan and does not implement any new feature.

## Current Acceptance Status

| Layer / Component | Status | Evidence |
| --- | --- | --- |
| L1 原文事实层 | PASS | `chapter_registry = 1922`, `chapter_contents = 1922` |
| L2 原文坐标层 | PASS | `l2_index_status = 1922`, `l2_paragraph_units = 122870`, `l2_sentence_units = 122918` |
| L3.0 代际纪元 seed | PASS | `config/narrative_time_axis.seed.json` 可解析，包含 `era_01` 到 `era_06` |
| L3-entry 章节标题入口索引 | PASS | `l3_chapter_title_index = 1922`, `l3_chapter_title_fts = 1922` |

## L3.0 代际纪元 Seed

L3.0 代际纪元 seed 已完成，但它只是人工 seed 文件，不是完整时间线系统。

已完成文件：

- `docs/narrative_time_axis_contract.md`
- `config/narrative_time_axis.seed.json`

当前作用：

- 定义第一代到第六代世界的人工口径。
- 帮助后续流程区分五代世界、六代世界、回忆碎片和新纪元。
- 作为人工维护词典使用。

明确限制：

- 不代表已经完成具体时间线系统。
- 不代表章节归属判断。
- 不允许直接当作原文事实结论。

## Time System Status

| Capability | Status |
| --- | --- |
| 具体时间线系统 | NOT STARTED |
| 章节时间映射 | NOT STARTED |
| `l3_era_def` | NOT STARTED / not created |
| `l3_chapter_time_mapping` | NOT STARTED / not created |
| `l3_timeline_anchor` | NOT STARTED / not created |
| 1922 章时间标签 | NOT STARTED |
| 每章属于哪一代世界的判断 | NOT STARTED |
| scene-level `time_axis_hint` | NOT STARTED |
| 绝对剧情时序 `absolute_time_seq` | NOT STARTED |

只读数据库复核结果：

- `l3_era_def`: absent
- `l3_chapter_time_mapping`: absent
- `l3_timeline_anchor`: absent
- `scene_blocks`: absent
- `parent_chunks`: absent
- `child_segments`: absent

## L3-entry Chapter Title Index

L3-entry 章节标题入口索引已完成并验证通过。

数据库状态：

- `l3_chapter_title_index`: 1922 rows
- `l3_chapter_title_fts`: 1922 rows
- indexed chapter range: `1..1922`
- distinct indexed chapters: 1922

验收状态：

- 小样本 build/verify: PASS
- 单章 1697 build/verify: PASS
- 全量 build/verify: PASS
- 幂等复跑: PASS, `inserted=0`, `skipped=1922`
- unittest: 15 tests OK
- compileall: exit 0
- L1/L2 guard: unchanged

报告证据：

- `outputs/l3_chapter_title_index_report.md`
  - `读取章节数`: 1922
  - `处理章节数`: 1922
  - `新增标题索引数`: 0
  - `跳过标题索引数`: 1922
  - `主表最终行数`: 1922
  - `FTS 最终行数`: 1922
  - `L1/L2 guard unchanged`: True
  - final: `L3 CHAPTER TITLE INDEX BUILD PASS`
- `outputs/l3_chapter_title_index_verify_report.md`
  - mode: full
  - `主表行数`: 1922
  - `FTS 行数`: 1922
  - `覆盖章节数`: 1922
  - `expected-main-chapters`: 1922
  - `FTS 查询命中`: 1
  - final: `L3 CHAPTER TITLE INDEX VERIFY PASS`

## Explicitly Not Implemented

The following capabilities have not started and must remain out of scope until a future task explicitly requests them:

- 人物索引
- 地区索引
- 世界线索引
- `scene_blocks`
- `parent_chunks`
- `child_segments`
- Chroma
- embedding
- 分镜逻辑
- chapter-level time mapping
- timeline anchor extraction

## L1/L2 Safety

This status update performed only read-only verification and documentation update.

- No L1 table was modified.
- No L2 table was modified.
- L1 was not rerun.
- L2 was not rerun.
- No new database table was created by this status update.
