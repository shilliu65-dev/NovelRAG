# L3.2 神道境界体系 Seed 契约 v1.1

L3.2 `power_system_seed` 只校验人工 seed JSON 的结构化规则。本层不写 SQLite，不读取原文，不调用模型，不做 embedding/Chroma，不修改 L1/L2/L3/scene_blocks。

## 状态生命周期

神道目录中的 `godway.status` 只允许：

```text
active
deprecating
deprecated
```

seed 证据状态只允许：

```text
user_seed
pending_l1_evidence
l1_confirmed
```

`deprecating` 必须包含 `replacement_godway_id` 或 `deprecation_note`。`deprecated` 不允许被 `special_power_rules.base_godway_id` 引用。

## 普通进阶边界

普通神道 progression 只允许 rank 1-9，且必须连续。`rank=10`、`disasterized=true`、`special_rule_id`、`disasterization`、`true_god`、`rank_10`、`chen_ling_unique` 等特殊标签不得进入普通 progression。

灾厄化不属于 rank 10，也不属于普通 1-9 阶 progression。灾厄化、真神、陈伶特殊路线统一由 `special_power_rules.seed.json` 表达。

## Forbidden Pattern 分级

verifier 不做语义理解，只按结构化 pattern 分级：

1. 命中 `blocked_context_patterns` 输出 `error`。
2. 命中 `allowed_context_patterns` 输出 `warning`。
3. 只命中 `term` 且上下文不明确输出 `suspect`。

`warning` 和 `suspect` 必须进入报告，但不得阻断 PASS。

## Branch Checksum

每条 godway 必须包含 `branches` 和 `branches_checksum`。`branches=[]` 合法，但也必须计算 checksum。

checksum 使用：

```python
json.dumps(branches, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
```

然后对该 canonical JSON 的 UTF-8 bytes 做 SHA256。progression 必须用 `catalog_branches_checksum` 锁定对应 catalog 分支版本。

## 报告契约

PASS 条件只有一个：

```text
error_count=0
```

每条 finding 必须包含：

```text
severity
code
file
json_path
message
impact
fix_suggestion
example_patch
```

重复人物 finding 还必须包含：

```text
character
godways
role_descriptions
exclusive_conflict
conflict_analysis
```
