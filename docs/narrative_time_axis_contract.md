# L3.0 代际纪元 Seed 契约

`config/narrative_time_axis.seed.json` 是人工维护的 L3.0 代际纪元定义 seed，用来给后续流程解释第一代到第六代世界的基础概念。

它不是事实源，不代表章节归属判断，也不替代 L1/L2 原文证据。第一版只保存 `era_01` 到 `era_06` 的基础定义、别名、说明和注意事项。

本轮禁止：

- 不创建 `l3_chapter_time_mapping`
- 不创建 `l3_timeline_anchor`
- 不判断每章属于哪一代
- 不给 1922 章打时间标签
- 不做章节时间映射
- 不做时间锚点抽取
- 不做 scene_blocks
- 不调用大模型
- 不修改 L1/L2

seed 文件本身是 JSON，因此 `era_aliases` 和 `evidence_refs` 使用 JSON 数组，不使用嵌套 JSON 字符串。`created_at` 固定为 `2026-06-24T00:00:00+08:00`，重复运行脚本不得自动更新 seed 内容。

后续如果需要把代际信息入库，可以新增独立任务设计 `l3_era_def`，再将数组字段转换为 SQLite JSON 字符串。章节级或场景级时间提示必须另行设计，不能由本 seed 自动推断。
