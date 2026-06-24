# L3.0 章节标题入口索引契约

L3.0 章节标题入口索引是派生检索入口，只回答“某个关键词可能对应哪些章节标题”。事实源仍然是 L1 的 `v_current_chapters` / `chapter_contents` / `chapter_registry` 和 L2 的坐标表。

本索引只处理正文编号章节 `chapter_num BETWEEN 1 AND 1922`。EXTRA、番外、完本感言和其他非正文条目不进入本轮 L3 标题索引。

## 边界

- 只读取 `v_current_chapters` 的 `chapter_id`、`latest_version_id`、`chapter_num`、`chapter_title_current`
- 不读取原始 txt
- 不用 `content_full_text` 生成关键词
- 不修改 L1/L2
- 不重跑 L1/L2
- 不调用大模型
- 不创建 Chroma
- 不实现人物、地区、世界线、scene_blocks、parent_chunks、child_segments、embedding 或分镜逻辑

## 主表和 FTS

`l3_chapter_title_index.title_index_id` 本轮固定等于 `chapter_num`，仅适用于当前正文编号章节 `1..1922`。后续如果支持历史版本并存、EXTRA 或非正文内容，必须重新设计稳定 ID。

`title_hash` 必须基于 `title_raw` 计算。`title_keywords_json` 是 JSON 数组字符串，FTS 的 `title_keywords` 是该数组用空格拼接后的检索文本，不是 JSON。

`l3_chapter_title_fts.rowid` 必须等于 `l3_chapter_title_index.title_index_id`。非 rebuild 模式禁止使用 `INSERT OR REPLACE`，避免破坏 rowid 绑定。

## 运行规则

首次构建必须使用 `--rebuild`。非 rebuild 模式只用于当前 scope 的幂等复跑和一致性检查：

- `--chapter-num N`：检查并处理单章
- `--limit N`：按 `chapter_num ASC` 检查并处理前 N 章
- 无 scope 参数：检查并处理全量 1922 章

小样本或单章 build 是调试状态，不等于全量 PASS。最终验收前必须重新执行全量 `--rebuild` 和全量 verify。
