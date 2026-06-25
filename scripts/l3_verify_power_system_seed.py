from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import ensure_dirs, project_root_from_env


POWER_FILE = "power_realm.seed.json"
CATALOG_FILE = "godway_catalog.seed.json"
PROGRESSION_FILE = "godway_progression.seed.json"
SPECIAL_FILE = "special_power_rules.seed.json"
REPORT_RELATIVE_PATH = Path("outputs") / "l3_power_system_seed_verify_report.md"
PASS_MESSAGE = "L3 POWER SYSTEM SEED VERIFY PASS，可以进入 L3 RAG 反查候选证据阶段。"
FAIL_MESSAGE = "L3 POWER SYSTEM SEED VERIFY FAIL，禁止进入 L3 RAG 补证据。"
GODWAY_STATUS_VALUES = {"active", "deprecating", "deprecated"}
EVIDENCE_STATUS_VALUES = {"user_seed", "pending_l1_evidence", "l1_confirmed"}
SPECIAL_ONLY_TAGS = {"disasterization", "true_god", "rank_10", "chen_ling_unique"}


@dataclass
class Finding:
    severity: str
    code: str
    file: str
    json_path: str
    message: str
    impact: str
    fix_suggestion: str
    example_patch: str
    character: str = ""
    godways: list[str] = field(default_factory=list)
    role_descriptions: list[str] = field(default_factory=list)
    exclusive_conflict: bool = False
    conflict_analysis: str = ""


@dataclass
class L3VerificationResult:
    ok: bool
    project_dir: Path
    config_dir: Path
    report_path: Path
    timestamped_report_path: Path | None = None
    final_message: str = FAIL_MESSAGE
    findings: list[Finding] = field(default_factory=list)
    error_count: int = 0
    warning_count: int = 0
    suspect_count: int = 0
    duplicate_character_warning_count: int = 0
    branch_checksum_error_count: int = 0
    special_rule_error_count: int = 0
    rank_error_count: int = 0
    deprecated_reference_error_count: int = 0


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def normalize_severity(value: object, default: str = "error") -> str:
    text = str(value or default).lower()
    return text if text in {"error", "warning", "suspect"} else default


def json_path(base: str, *parts: object) -> str:
    path = base
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def add_finding(result: L3VerificationResult, finding: Finding) -> None:
    result.findings.append(finding)


def make_finding(
    *,
    severity: str,
    code: str,
    file: str,
    path: str,
    message: str,
    impact: str,
    fix_suggestion: str,
    example_patch: str,
) -> Finding:
    return Finding(
        severity=normalize_severity(severity),
        code=code,
        file=file,
        json_path=path,
        message=message,
        impact=impact,
        fix_suggestion=fix_suggestion,
        example_patch=example_patch,
    )


def load_json_file(config_dir: Path, file_name: str, result: L3VerificationResult) -> Any | None:
    path = config_dir / file_name
    if not path.exists():
        add_finding(
            result,
            make_finding(
                severity="error",
                code="JSON_FILE_MISSING",
                file=file_name,
                path="$",
                message=f"缺少必需 seed 文件：{file_name}",
                impact="L3.2 seed 无法完成跨文件校验。",
                fix_suggestion=f"在 config 目录新增 {file_name}。",
                example_patch=f'{{\n  "version": "l3_seed_v1.1.0",\n  "source": "user_seed"\n}}',
            ),
        )
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        add_finding(
            result,
            make_finding(
                severity="error",
                code="JSON_PARSE_ERROR",
                file=file_name,
                path="$",
                message=f"JSON 解析失败：{exc}",
                impact="该文件无法参与后续结构校验。",
                fix_suggestion="修复 JSON 语法后重新运行 verifier。",
                example_patch="// 检查逗号、引号、括号配对，确保文件是合法 JSON。",
            ),
        )
        return None


def validate_evidence_status(result: L3VerificationResult, file_name: str, path: str, value: object) -> None:
    if value not in EVIDENCE_STATUS_VALUES:
        add_finding(
            result,
            make_finding(
                severity="error",
                code="EVIDENCE_STATUS_INVALID",
                file=file_name,
                path=path,
                message=f"证据状态 {value!r} 不在允许值中。",
                impact="seed 生命周期不可判定，可能污染 candidate/confirmed 流程。",
                fix_suggestion="将证据状态改为 user_seed、pending_l1_evidence 或 l1_confirmed。",
                example_patch='"status": "user_seed"',
            ),
        )


def validate_power_realm(power: dict[str, Any], result: L3VerificationResult) -> dict[str, dict[str, Any]]:
    normal = power.get("normal_realm_rule", {})
    if normal.get("max_normal_rank") != 9:
        add_finding(
            result,
            make_finding(
                severity="error",
                code="NORMAL_MAX_RANK_INVALID",
                file=POWER_FILE,
                path="$.normal_realm_rule.max_normal_rank",
                message="普通境界 max_normal_rank 必须等于 9。",
                impact="会让普通 progression 和 special rules 边界失效。",
                fix_suggestion="将 max_normal_rank 改为 9。",
                example_patch='"max_normal_rank": 9',
            ),
        )

    covered: list[int] = []
    for index, item in enumerate(normal.get("rank_rules", [])):
        status = item.get("status")
        if status is not None:
            validate_evidence_status(result, POWER_FILE, json_path("$.normal_realm_rule.rank_rules", index, "status"), status)
        start = item.get("rank_start")
        end = item.get("rank_end")
        if not isinstance(start, int) or not isinstance(end, int) or start > end:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="NORMAL_RANK_RANGE_INVALID",
                    file=POWER_FILE,
                    path=json_path("$.normal_realm_rule.rank_rules", index),
                    message="普通境界 rank_start/rank_end 必须是有效整数范围。",
                    impact="无法证明普通境界覆盖 1-9。",
                    fix_suggestion="设置 rank_start <= rank_end，且范围只落在 1-9。",
                    example_patch='{"rank_start": 1, "rank_end": 3, "realm_name": "入门境"}',
                ),
            )
            continue
        covered.extend(range(start, end + 1))
        if start > 9 or end > 9 or start < 1:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="NORMAL_RANK_OUT_OF_RANGE",
                    file=POWER_FILE,
                    path=json_path("$.normal_realm_rule.rank_rules", index),
                    message="普通境界 rank 范围只能覆盖 1-9。",
                    impact="十阶或特殊层可能被混入普通境界。",
                    fix_suggestion="将 10 阶、真神、灾厄化等内容移入 special_power_rules。",
                    example_patch='"rank_end": 9',
                ),
            )

    expected = list(range(1, 10))
    if sorted(covered) != expected:
        missing = sorted(set(expected) - set(covered))
        duplicates = sorted(rank for rank in set(covered) if covered.count(rank) > 1)
        add_finding(
            result,
            make_finding(
                severity="error",
                code="NORMAL_RANK_COVERAGE_INVALID",
                file=POWER_FILE,
                path="$.normal_realm_rule.rank_rules",
                message=f"普通境界 rank 必须连续覆盖 1-9。missing={missing} duplicate={duplicates}",
                impact="普通 progression 的边界校验没有稳定基准。",
                fix_suggestion="调整 rank_rules，使每个 rank 1-9 恰好出现一次。",
                example_patch='"rank_rules": [{"rank_start": 1, "rank_end": 3}, {"rank_start": 4, "rank_end": 4}, {"rank_start": 5, "rank_end": 7}, {"rank_start": 8, "rank_end": 8}, {"rank_start": 9, "rank_end": 9}]',
            ),
        )

    rules: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(power.get("capability_order_rules", [])):
        tag = item.get("capability_tag")
        if not tag:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="CAPABILITY_RULE_TAG_MISSING",
                    file=POWER_FILE,
                    path=json_path("$.capability_order_rules", index),
                    message="capability_order_rules 缺少 capability_tag。",
                    impact="无法对 progression capability_tags 做顺序校验。",
                    fix_suggestion="为该规则补充 capability_tag。",
                    example_patch='"capability_tag": "domain_expansion"',
                ),
            )
            continue
        rules[str(tag)] = item
    return rules


def representative_entries(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            entries.append(
                {
                    "name": item,
                    "role_description": "",
                    "exclusive": False,
                    "allow_cross_godway": False,
                    "allowed_cross_godway_ids": [],
                    "source_kind": "user_seed",
                }
            )
        elif isinstance(item, dict):
            entries.append(item)
    return entries


def validate_catalog(catalog: dict[str, Any], result: L3VerificationResult) -> dict[str, dict[str, Any]]:
    godways = catalog.get("godways", [])
    if not isinstance(godways, list):
        add_finding(
            result,
            make_finding(
                severity="error",
                code="CATALOG_GODWAYS_NOT_LIST",
                file=CATALOG_FILE,
                path="$.godways",
                message="godways 必须是数组。",
                impact="无法建立 godway_id 目录索引。",
                fix_suggestion="将 godways 改为数组结构。",
                example_patch='"godways": []',
            ),
        )
        return {}

    by_id: dict[str, dict[str, Any]] = {}
    seen_names: dict[str, str] = {}
    group_counts: dict[str, int] = {"mainstream_14": 0, "ancient_declined_4": 0}
    character_occurrences: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}

    for index, item in enumerate(godways):
        if not isinstance(item, dict):
            continue
        base = json_path("$.godways", index)
        godway_id = item.get("godway_id")
        name = item.get("name")
        group = item.get("group")
        category = item.get("category")
        status = item.get("status")

        if not godway_id:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="GODWAY_ID_MISSING",
                    file=CATALOG_FILE,
                    path=base,
                    message="godway 缺少 godway_id。",
                    impact="跨文件 FK 无法引用该神道。",
                    fix_suggestion="补充稳定 godway_id。",
                    example_patch='"godway_id": "godway_xi"',
                ),
            )
        elif godway_id in by_id:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="GODWAY_ID_DUPLICATE",
                    file=CATALOG_FILE,
                    path=f"{base}.godway_id",
                    message=f"godway_id 重复：{godway_id}",
                    impact="progression/special rule 引用会变得不确定。",
                    fix_suggestion="保留唯一 godway_id，重复项改名或合并。",
                    example_patch='"godway_id": "godway_unique_id"',
                ),
            )
        else:
            by_id[str(godway_id)] = item

        if name:
            if name in seen_names:
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="GODWAY_NAME_DUPLICATE",
                        file=CATALOG_FILE,
                        path=f"{base}.name",
                        message=f"godway name 重复：{name}",
                        impact="按名称检索和报告会产生歧义。",
                        fix_suggestion="保持每条 godway 的 name 唯一。",
                        example_patch='"name": "唯一神道名"',
                    ),
                )
            seen_names[str(name)] = str(godway_id)

        if group in group_counts:
            group_counts[str(group)] += 1
        else:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="GODWAY_GROUP_INVALID",
                    file=CATALOG_FILE,
                    path=f"{base}.group",
                    message=f"godway group 非法：{group}",
                    impact="14+4 目录计数无法验证。",
                    fix_suggestion="group 只能是 mainstream_14 或 ancient_declined_4。",
                    example_patch='"group": "mainstream_14"',
                ),
            )

        if category not in {"mainstream_14", "ancient_declined_4"}:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="GODWAY_CATEGORY_INVALID",
                    file=CATALOG_FILE,
                    path=f"{base}.category",
                    message=f"godway category 非法：{category}",
                    impact="base_godway_constraints 无法正确约束引用。",
                    fix_suggestion="category 只能是 mainstream_14 或 ancient_declined_4。",
                    example_patch='"category": "mainstream_14"',
                ),
            )

        if status not in GODWAY_STATUS_VALUES:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="GODWAY_STATUS_INVALID",
                    file=CATALOG_FILE,
                    path=f"{base}.status",
                    message=f"godway status 非法：{status}",
                    impact="无法判定该神道是否可被 special_power_rules 引用。",
                    fix_suggestion="status 只能是 active、deprecating 或 deprecated。",
                    example_patch='"status": "active"',
                ),
            )
        elif status == "deprecating" and not item.get("replacement_godway_id") and not item.get("deprecation_note"):
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="GODWAY_DEPRECATING_NO_GUIDANCE",
                    file=CATALOG_FILE,
                    path=f"{base}.status",
                    message=f"{godway_id} 标记为 deprecating 但没有 replacement_godway_id 或 deprecation_note。",
                    impact="下游不知道该迁移到哪个神道或为何弃用。",
                    fix_suggestion="补充 replacement_godway_id 或 deprecation_note。",
                    example_patch='"replacement_godway_id": "godway_xi"',
                ),
            )

        seed_status = item.get("seed_status")
        if seed_status is not None:
            validate_evidence_status(result, CATALOG_FILE, f"{base}.seed_status", seed_status)

        if "branches" not in item:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="CATALOG_BRANCHES_MISSING",
                    file=CATALOG_FILE,
                    path=f"{base}.branches",
                    message=f"{godway_id or name} 缺少 branches 字段。",
                    impact="后续格式化检索必须反复处理缺失字段，且 checksum 无法计算。",
                    fix_suggestion='无明确分支时显式写入 "branches": []。',
                    example_patch='"branches": []',
                ),
            )
        elif not isinstance(item.get("branches"), list):
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="CATALOG_BRANCHES_NOT_LIST",
                    file=CATALOG_FILE,
                    path=f"{base}.branches",
                    message=f"{godway_id or name} 的 branches 必须是数组。",
                    impact="分支 checksum 无法稳定计算。",
                    fix_suggestion="将 branches 改为数组；无分支用空数组。",
                    example_patch='"branches": []',
                ),
            )
        elif "branches_checksum" not in item:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="CATALOG_BRANCHES_CHECKSUM_MISSING",
                    file=CATALOG_FILE,
                    path=f"{base}.branches_checksum",
                    message=f"{godway_id or name} 缺少 branches_checksum。",
                    impact="progression 无法锁定 catalog 分支版本。",
                    fix_suggestion="按 canonical_json(branches) 的 SHA256 写入 branches_checksum。",
                    example_patch=f'"branches_checksum": "{sha256_text(canonical_json(item.get("branches", [])))}"',
                ),
            )
        else:
            expected = sha256_text(canonical_json(item.get("branches")))
            if item.get("branches_checksum") != expected:
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="CATALOG_BRANCHES_CHECKSUM_INVALID",
                        file=CATALOG_FILE,
                        path=f"{base}.branches_checksum",
                        message=f"{godway_id or name} 的 branches_checksum 与 branches 不一致。",
                        impact="分支结构变更后 progression 可能仍引用旧版本。",
                        fix_suggestion="重新计算 canonical_json(branches) 的 SHA256。",
                        example_patch=f'"branches_checksum": "{expected}"',
                    ),
                )

        for rep in representative_entries(item.get("representative_characters")):
            char_name = rep.get("name")
            if char_name:
                character_occurrences.setdefault(str(char_name), []).append((item, rep))

    if len(godways) != 18:
        add_finding(
            result,
            make_finding(
                severity="error",
                code="GODWAY_COUNT_INVALID",
                file=CATALOG_FILE,
                path="$.godways",
                message=f"神道目录应为 18 条，实际为 {len(godways)} 条。",
                impact="14 主流 + 4 古神道目录边界不完整。",
                fix_suggestion="补齐或移除神道条目，使目录总数为 18。",
                example_patch='"godways": [/* 14 mainstream_14 + 4 ancient_declined_4 */]',
            ),
        )
    for group, expected_count in {"mainstream_14": 14, "ancient_declined_4": 4}.items():
        if group_counts[group] != expected_count:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="GODWAY_GROUP_COUNT_INVALID",
                    file=CATALOG_FILE,
                    path="$.godways",
                    message=f"{group} 应为 {expected_count} 条，实际为 {group_counts[group]} 条。",
                    impact="神道目录分组规则不满足 seed 契约。",
                    fix_suggestion=f"调整 group={group} 的条目数量为 {expected_count}。",
                    example_patch=f'"group": "{group}"',
                ),
            )

    validate_duplicate_characters(character_occurrences, result)
    return by_id


def validate_duplicate_characters(
    occurrences: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]],
    result: L3VerificationResult,
) -> None:
    for character, rows in occurrences.items():
        godway_ids = [str(godway.get("godway_id")) for godway, _ in rows]
        if len(set(godway_ids)) <= 1:
            continue
        role_descriptions = [str(rep.get("role_description", "")) for _, rep in rows]
        exclusive_conflict = any(rep.get("exclusive") is True and rep.get("allow_cross_godway") is False for _, rep in rows)
        allowed_cross = any(rep.get("allow_cross_godway") is True for _, rep in rows)
        conflict_pair = False
        for godway, _ in rows:
            conflicts = set(godway.get("conflict_with_godway_ids", []))
            if conflicts.intersection(godway_ids):
                conflict_pair = True
                break
        if conflict_pair:
            severity = "error"
            code = "DUPLICATE_CHARACTER_CONFLICT_GODWAY"
        elif exclusive_conflict:
            severity = "error"
            code = "DUPLICATE_CHARACTER_EXCLUSIVE"
        elif allowed_cross:
            severity = "warning"
            code = "DUPLICATE_CHARACTER_ALLOWED"
        else:
            severity = "warning"
            code = "DUPLICATE_CHARACTER_WARNING"

        finding = make_finding(
            severity=severity,
            code=code,
            file=CATALOG_FILE,
            path="$.godways[*].representative_characters",
            message=f"代表人物 {character} 出现在多个 godway：{', '.join(godway_ids)}。",
            impact="后续 RAG 反查时可能出现人物归属证据链分叉。",
            fix_suggestion="如为跨神道身份，设置 allow_cross_godway=true 并写明 role_description；如为手误，删除错误归属。",
            example_patch=(
                '{\n'
                f'  "name": "{character}",\n'
                '  "role_description": "说明该人物在此神道中的身份",\n'
                '  "exclusive": false,\n'
                '  "allow_cross_godway": true,\n'
                '  "allowed_cross_godway_ids": ["godway_other"],\n'
                '  "source_kind": "user_seed"\n'
                '}'
            ),
        )
        finding.character = character
        finding.godways = godway_ids
        finding.role_descriptions = role_descriptions
        finding.exclusive_conflict = exclusive_conflict or conflict_pair
        finding.conflict_analysis = "; ".join(
            f"{godway.get('godway_id')}({godway.get('name')}): {rep.get('role_description', '')}" for godway, rep in rows
        )
        add_finding(result, finding)


def collect_forbidden_patterns(special: dict[str, Any]) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    for rule in special.get("special_rules", []):
        if not isinstance(rule, dict):
            continue
        for pattern in rule.get("forbidden_patterns", []):
            if isinstance(pattern, dict):
                patterns.append(pattern)
    return patterns


def text_matches(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, text) is not None
    except re.error:
        return pattern in text


def scan_forbidden_patterns(
    text: str,
    result: L3VerificationResult,
    *,
    file_name: str,
    path: str,
    patterns: list[dict[str, Any]],
) -> None:
    for pattern in patterns:
        term = str(pattern.get("term", ""))
        regex = str(pattern.get("pattern") or term)
        if not term and not regex:
            continue
        if term not in text and not text_matches(regex, text):
            continue

        blocked = [str(item) for item in pattern.get("blocked_context_patterns", []) if text_matches(str(item), text)]
        allowed = [str(item) for item in pattern.get("allowed_context_patterns", []) if text_matches(str(item), text)]
        if blocked:
            severity = "error"
            code = "FORBIDDEN_BLOCKED_CONTEXT"
            context = ", ".join(blocked)
        elif allowed:
            severity = "warning"
            code = "FORBIDDEN_ALLOWED_CONTEXT"
            context = ", ".join(allowed)
        else:
            severity = "suspect"
            code = "FORBIDDEN_UNCLEAR_CONTEXT"
            context = term or regex

        add_finding(
            result,
            make_finding(
                severity=severity,
                code=code,
                file=file_name,
                path=path,
                message=f"普通 progression 文本命中特殊规则词：{term or regex}，context={context}",
                impact="可能把陈伶特殊路线、灾厄化或真神层混入普通 1-9 阶 progression。",
                fix_suggestion="如果这是普通排除说明，补充 allowed_context_patterns；如果是特殊路线内容，移入 special_power_rules。",
                example_patch='"description": "普通进阶描述，不涉及嘲灾。"',
            ),
        )


def validate_special_rules(
    special: dict[str, Any],
    catalog_by_id: dict[str, dict[str, Any]],
    result: L3VerificationResult,
) -> list[dict[str, Any]]:
    constraints = special.get("base_godway_constraints", {})
    allowed_categories = set(constraints.get("allowed_godway_categories", []))
    disallowed_status = set(constraints.get("disallowed_status", []))

    disaster = special.get("disasterization_rules", {})
    if disaster.get("normal_progression_allowed") is not False:
        add_finding(
            result,
            make_finding(
                severity="error",
                code="DISASTERIZATION_NORMAL_ALLOWED_INVALID",
                file=SPECIAL_FILE,
                path="$.disasterization_rules.normal_progression_allowed",
                message="disasterization_rules.normal_progression_allowed 必须为 false。",
                impact="灾厄化可能被误并入普通 progression。",
                fix_suggestion="将 normal_progression_allowed 设为 false。",
                example_patch='"normal_progression_allowed": false',
            ),
        )
    if disaster.get("belongs_to_rank_10") is not False:
        add_finding(
            result,
            make_finding(
                severity="error",
                code="DISASTERIZATION_RANK10_INVALID",
                file=SPECIAL_FILE,
                path="$.disasterization_rules.belongs_to_rank_10",
                message="disasterization_rules.belongs_to_rank_10 必须为 false。",
                impact="灾厄化会被误建模为十阶真神。",
                fix_suggestion="将 belongs_to_rank_10 设为 false。",
                example_patch='"belongs_to_rank_10": false',
            ),
        )
    if disaster.get("trigger_type") != "special_event":
        add_finding(
            result,
            make_finding(
                severity="error",
                code="DISASTERIZATION_TRIGGER_INVALID",
                file=SPECIAL_FILE,
                path="$.disasterization_rules.trigger_type",
                message='disasterization_rules.trigger_type 必须为 "special_event"。',
                impact="灾厄化触发边界不清晰。",
                fix_suggestion='将 trigger_type 设为 "special_event"。',
                example_patch='"trigger_type": "special_event"',
            ),
        )
    if not isinstance(disaster.get("trigger_conditions"), list) or not disaster.get("trigger_conditions"):
        add_finding(
            result,
            make_finding(
                severity="error",
                code="DISASTERIZATION_TRIGGER_CONDITIONS_INVALID",
                file=SPECIAL_FILE,
                path="$.disasterization_rules.trigger_conditions",
                message="disasterization_rules.trigger_conditions 必须是非空数组。",
                impact="灾厄化触发条件缺失，后续证据流程无目标。",
                fix_suggestion="补充人工确认的触发条件 seed。",
                example_patch='"trigger_conditions": ["特殊事件触发"]',
            ),
        )

    found_chenling = False
    for index, rule in enumerate(special.get("special_rules", [])):
        if not isinstance(rule, dict):
            continue
        base = json_path("$.special_rules", index)
        status = rule.get("status")
        if status is not None:
            validate_evidence_status(result, SPECIAL_FILE, f"{base}.status", status)
        if rule.get("rule_id") == "chenling_twisted_xi_god_route":
            found_chenling = True
        if rule.get("related_character") == "陈伶" and rule.get("base_godway_id") == "godway_xi":
            found_chenling = True

        if "forbidden_patterns" in rule:
            for pattern_index, pattern in enumerate(rule.get("forbidden_patterns", [])):
                pattern_path = json_path(base, "forbidden_patterns", pattern_index)
                for key in ("term", "pattern", "severity", "allowed_context_patterns", "blocked_context_patterns", "description"):
                    if key not in pattern:
                        add_finding(
                            result,
                            make_finding(
                                severity="error",
                                code="FORBIDDEN_PATTERN_FIELD_MISSING",
                                file=SPECIAL_FILE,
                                path=f"{pattern_path}.{key}",
                                message=f"forbidden_pattern 缺少 {key}。",
                                impact="普通 progression 特殊词分级无法稳定执行。",
                                fix_suggestion=f"补充 {key} 字段。",
                                example_patch=f'"{key}": []',
                            ),
                        )

        base_godway_id = rule.get("base_godway_id")
        if not base_godway_id:
            continue
        catalog_item = catalog_by_id.get(str(base_godway_id))
        if catalog_item is None:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="BASE_GODWAY_NOT_FOUND",
                    file=SPECIAL_FILE,
                    path=f"{base}.base_godway_id",
                    message=f"base_godway_id 不存在于 catalog：{base_godway_id}",
                    impact="special rule 引用了孤立神道。",
                    fix_suggestion="修正 base_godway_id 或在 godway_catalog 中补充对应神道。",
                    example_patch='"base_godway_id": "godway_xi"',
                ),
            )
            continue
        category = catalog_item.get("category")
        if allowed_categories and category not in allowed_categories:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="BASE_GODWAY_CATEGORY_INVALID",
                    file=SPECIAL_FILE,
                    path=f"{base}.base_godway_id",
                    message=f"base_godway_id={base_godway_id} 的 category={category} 不在允许范围。",
                    impact="special rule 绑定到了不允许的神道类别。",
                    fix_suggestion="改用允许类别的 base_godway_id，或调整 base_godway_constraints。",
                    example_patch='"allowed_godway_categories": ["mainstream_14"]',
                ),
            )
        status_value = catalog_item.get("status")
        if status_value == "deprecated" or status_value in disallowed_status and status_value == "deprecated":
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="BASE_GODWAY_DEPRECATED",
                    file=SPECIAL_FILE,
                    path=f"{base}.base_godway_id",
                    message=f"base_godway_id={base_godway_id} 引用了 deprecated 神道。",
                    impact="special rule 会绑定到已废弃目录项。",
                    fix_suggestion="改用 replacement_godway_id 或恢复该 godway 为 active。",
                    example_patch='"base_godway_id": "replacement_godway_id"',
                ),
            )
        elif status_value == "deprecating":
            if not catalog_item.get("replacement_godway_id"):
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="BASE_GODWAY_DEPRECATING_NO_REPLACEMENT",
                        file=SPECIAL_FILE,
                        path=f"{base}.base_godway_id",
                        message=f"base_godway_id={base_godway_id} 正在 deprecating 但没有 replacement_godway_id。",
                        impact="special rule 无法迁移到稳定神道。",
                        fix_suggestion="在 catalog 对应 godway 上补充 replacement_godway_id，或不要引用 deprecating 神道。",
                        example_patch='"replacement_godway_id": "godway_xi_new"',
                    ),
                )
            else:
                add_finding(
                    result,
                    make_finding(
                        severity="warning",
                        code="BASE_GODWAY_DEPRECATING_WITH_REPLACEMENT",
                        file=SPECIAL_FILE,
                        path=f"{base}.base_godway_id",
                        message=f"base_godway_id={base_godway_id} 正在 deprecating，存在 replacement_godway_id。",
                        impact="当前引用可解析，但后续应迁移到 replacement。",
                        fix_suggestion="确认后将 special rule 的 base_godway_id 改到 replacement_godway_id。",
                        example_patch=f'"base_godway_id": "{catalog_item.get("replacement_godway_id")}"',
                    ),
                )

    if not found_chenling:
        add_finding(
            result,
            make_finding(
                severity="error",
                code="CHENLING_SPECIAL_RULE_MISSING",
                file=SPECIAL_FILE,
                path="$.special_rules",
                message="陈伶特殊路线必须存在于 special_power_rules。",
                impact="陈伶路线可能被误并入普通戏神道 progression。",
                fix_suggestion="新增 rule_id=chenling_twisted_xi_god_route 的 special rule。",
                example_patch='"rule_id": "chenling_twisted_xi_god_route"',
            ),
        )
    return collect_forbidden_patterns(special)


def rank_text(rank: dict[str, Any]) -> str:
    values: list[str] = []
    for key in ("ability_summary", "description", "domain"):
        value = rank.get(key)
        if isinstance(value, str):
            values.append(value)
    for key in ("capability_tags", "prerequisites", "forbidden_tags"):
        value = rank.get(key)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
    return "\n".join(values)


def validate_progression(
    progression: dict[str, Any],
    catalog_by_id: dict[str, dict[str, Any]],
    capability_rules: dict[str, dict[str, Any]],
    forbidden_patterns: list[dict[str, Any]],
    result: L3VerificationResult,
) -> None:
    progressions = progression.get("progressions", [])
    if not isinstance(progressions, list):
        add_finding(
            result,
            make_finding(
                severity="error",
                code="PROGRESSION_LIST_INVALID",
                file=PROGRESSION_FILE,
                path="$.progressions",
                message="progressions 必须是数组。",
                impact="无法校验普通进阶线。",
                fix_suggestion="将 progressions 改为数组。",
                example_patch='"progressions": []',
            ),
        )
        return

    for index, item in enumerate(progressions):
        if not isinstance(item, dict):
            continue
        base = json_path("$.progressions", index)
        godway_id = item.get("godway_id")
        catalog_item = catalog_by_id.get(str(godway_id))
        if catalog_item is None:
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="PROGRESSION_GODWAY_NOT_FOUND",
                    file=PROGRESSION_FILE,
                    path=f"{base}.godway_id",
                    message=f"progression godway_id 不存在于 catalog：{godway_id}",
                    impact="出现孤立进阶线，无法关联神道目录。",
                    fix_suggestion="修正 godway_id 或补充 catalog 对应条目。",
                    example_patch='"godway_id": "godway_xi"',
                ),
            )
        else:
            if item.get("name") != catalog_item.get("name"):
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="PROGRESSION_NAME_MISMATCH",
                        file=PROGRESSION_FILE,
                        path=f"{base}.name",
                        message=f"progression name={item.get('name')} 与 catalog name={catalog_item.get('name')} 不一致。",
                        impact="同一 godway_id 的名称不稳定，会影响检索和报告。",
                        fix_suggestion="将 progression.name 改为 catalog 中同一 godway_id 的 name。",
                        example_patch=f'"name": "{catalog_item.get("name")}"',
                    ),
                )
            if "catalog_branches_checksum" not in item:
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="PROGRESSION_BRANCHES_CHECKSUM_MISSING",
                        file=PROGRESSION_FILE,
                        path=f"{base}.catalog_branches_checksum",
                        message="progression 缺少 catalog_branches_checksum。",
                        impact="进阶线无法锁定目录分支版本。",
                        fix_suggestion="写入对应 catalog.branches_checksum。",
                        example_patch=f'"catalog_branches_checksum": "{catalog_item.get("branches_checksum", "")}"',
                    ),
                )
            elif item.get("catalog_branches_checksum") != catalog_item.get("branches_checksum"):
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="PROGRESSION_BRANCHES_CHECKSUM_MISMATCH",
                        file=PROGRESSION_FILE,
                        path=f"{base}.catalog_branches_checksum",
                        message="progression.catalog_branches_checksum 与 catalog.branches_checksum 不一致。",
                        impact="progression 可能针对旧分支结构编写。",
                        fix_suggestion="同步 catalog_branches_checksum 为 catalog 当前 branches_checksum。",
                        example_patch=f'"catalog_branches_checksum": "{catalog_item.get("branches_checksum", "")}"',
                    ),
                )

        ranks = item.get("ranks", [])
        if not isinstance(ranks, list):
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="PROGRESSION_RANKS_NOT_LIST",
                    file=PROGRESSION_FILE,
                    path=f"{base}.ranks",
                    message="progression.ranks 必须是数组。",
                    impact="无法校验 rank 1-9 连续性。",
                    fix_suggestion="将 ranks 改为数组。",
                    example_patch='"ranks": []',
                ),
            )
            continue
        rank_values = [rank.get("rank") for rank in ranks if isinstance(rank, dict)]
        if sorted(rank_values) != list(range(1, 10)):
            add_finding(
                result,
                make_finding(
                    severity="error",
                    code="PROGRESSION_RANK_CONTINUITY_INVALID",
                    file=PROGRESSION_FILE,
                    path=f"{base}.ranks",
                    message=f"普通 progression rank 必须连续为 1-9，实际为 {rank_values}。",
                    impact="普通进阶线不完整或混入特殊阶位。",
                    fix_suggestion="保留且只保留 rank 1-9。",
                    example_patch='"ranks": [{"rank": 1}, {"rank": 2}, {"rank": 3}, {"rank": 4}, {"rank": 5}, {"rank": 6}, {"rank": 7}, {"rank": 8}, {"rank": 9}]',
                ),
            )

        for rank_index, rank in enumerate(ranks):
            if not isinstance(rank, dict):
                continue
            rank_path = json_path(base, "ranks", rank_index)
            rank_value = rank.get("rank")
            if not isinstance(rank_value, int) or rank_value < 1 or rank_value > 9:
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="PROGRESSION_RANK_OUT_OF_RANGE",
                        file=PROGRESSION_FILE,
                        path=f"{rank_path}.rank",
                        message=f"普通 progression rank 只能是 1-9，实际为 {rank_value}。",
                        impact="十阶或特殊层被混入普通 progression。",
                        fix_suggestion="删除该 rank，或将特殊层移入 special_power_rules。",
                        example_patch='"rank": 9',
                    ),
                )
            status = rank.get("status")
            if status is not None:
                validate_evidence_status(result, PROGRESSION_FILE, f"{rank_path}.status", status)
            for field_name in ("capability_tags", "prerequisites", "forbidden_tags"):
                if field_name not in rank:
                    add_finding(
                        result,
                        make_finding(
                            severity="error",
                            code="PROGRESSION_RANK_FIELD_MISSING",
                            file=PROGRESSION_FILE,
                            path=f"{rank_path}.{field_name}",
                            message=f"rank 缺少 {field_name} 字段。",
                            impact="普通 progression 无法完成 v1.1 结构化校验。",
                            fix_suggestion=f"补充 {field_name}；无内容时使用空数组。",
                            example_patch=f'"{field_name}": []',
                        ),
                    )
                elif not isinstance(rank.get(field_name), list):
                    add_finding(
                        result,
                        make_finding(
                            severity="error",
                            code="PROGRESSION_RANK_FIELD_NOT_LIST",
                            file=PROGRESSION_FILE,
                            path=f"{rank_path}.{field_name}",
                            message=f"{field_name} 必须是数组。",
                            impact="标签和前置条件无法稳定解析。",
                            fix_suggestion=f"将 {field_name} 改为数组。",
                            example_patch=f'"{field_name}": []',
                        ),
                    )
            if rank.get("disasterized") is True:
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="NORMAL_PROGRESSION_DISASTERIZED",
                        file=PROGRESSION_FILE,
                        path=f"{rank_path}.disasterized",
                        message="普通 progression 不允许出现 disasterized=true。",
                        impact="灾厄化被混入普通 1-9 阶。",
                        fix_suggestion="删除该字段，并将灾厄化内容移入 special_power_rules。",
                        example_patch='"disasterized": false',
                    ),
                )
            if "special_rule_id" in rank:
                add_finding(
                    result,
                    make_finding(
                        severity="error",
                        code="NORMAL_PROGRESSION_SPECIAL_RULE_REF",
                        file=PROGRESSION_FILE,
                        path=f"{rank_path}.special_rule_id",
                        message="普通 progression 不允许引用 special_rule_id。",
                        impact="普通进阶线与特殊规则产生硬耦合。",
                        fix_suggestion="删除 special_rule_id；特殊内容放入 special_power_rules。",
                        example_patch='// remove "special_rule_id" from this rank',
                    ),
                )

            tags = [str(tag) for tag in rank.get("capability_tags", []) if isinstance(rank.get("capability_tags"), list)]
            forbidden_tags = [str(tag) for tag in rank.get("forbidden_tags", []) if isinstance(rank.get("forbidden_tags"), list)]
            for tag in tags + forbidden_tags:
                rule = capability_rules.get(tag)
                if tag in SPECIAL_ONLY_TAGS or (rule and rule.get("allowed_in_normal_progression") is False):
                    add_finding(
                        result,
                        make_finding(
                            severity="error",
                            code="SPECIAL_TAG_IN_NORMAL_PROGRESSION",
                            file=PROGRESSION_FILE,
                            path=f"{rank_path}.capability_tags",
                            message=f"特殊标签 {tag} 不允许出现在普通 progression。",
                            impact="特殊层或陈伶路线可能被混入普通 1-9 阶。",
                            fix_suggestion="删除该标签，并把相关内容移入 special_power_rules。",
                            example_patch=f'"capability_tags": [/* remove "{tag}" */]',
                        ),
                    )
                    continue
                if rule and isinstance(rank_value, int):
                    first_allowed = rule.get("first_allowed_rank")
                    if isinstance(first_allowed, int) and rank_value < first_allowed:
                        add_finding(
                            result,
                            make_finding(
                                severity=normalize_severity(rule.get("severity"), "warning"),
                                code="CAPABILITY_TAG_TOO_EARLY",
                                file=PROGRESSION_FILE,
                                path=f"{rank_path}.capability_tags",
                                message=f"capability_tag={tag} 最早允许 rank={first_allowed}，当前 rank={rank_value}。",
                                impact="能力出现顺序可能早于境界体系规则。",
                                fix_suggestion="将该 capability_tag 移到允许 rank，或调整 power_realm.seed.json 中的 first_allowed_rank。",
                                example_patch=f'"capability_tags": [/* move "{tag}" to rank {first_allowed}+ */]',
                            ),
                        )
            scan_forbidden_patterns(
                rank_text(rank),
                result,
                file_name=PROGRESSION_FILE,
                path=rank_path,
                patterns=forbidden_patterns,
            )


def finalize_counts(result: L3VerificationResult) -> None:
    result.error_count = sum(1 for item in result.findings if item.severity == "error")
    result.warning_count = sum(1 for item in result.findings if item.severity == "warning")
    result.suspect_count = sum(1 for item in result.findings if item.severity == "suspect")
    result.duplicate_character_warning_count = sum(
        1 for item in result.findings if item.severity == "warning" and item.code.startswith("DUPLICATE_CHARACTER")
    )
    result.branch_checksum_error_count = sum(
        1 for item in result.findings if item.severity == "error" and "BRANCHES_CHECKSUM" in item.code
    )
    result.special_rule_error_count = sum(
        1
        for item in result.findings
        if item.severity == "error" and (item.file == SPECIAL_FILE or item.code.startswith("FORBIDDEN_"))
    )
    result.rank_error_count = sum(
        1 for item in result.findings if item.severity == "error" and ("RANK" in item.code or "CAPABILITY" in item.code)
    )
    result.deprecated_reference_error_count = sum(
        1 for item in result.findings if item.severity == "error" and item.code in {"BASE_GODWAY_DEPRECATED", "BASE_GODWAY_DEPRECATING_NO_REPLACEMENT"}
    )
    result.ok = result.error_count == 0
    result.final_message = PASS_MESSAGE if result.ok else FAIL_MESSAGE


def build_report(result: L3VerificationResult) -> str:
    lines = [
        "# L3.2 Power System Seed v1.1 验证报告",
        "",
        f"- 验证时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- config_dir：{result.config_dir}",
        f"- 最终结论：{'PASS' if result.ok else 'FAIL'}",
        f"- error_count：{result.error_count}",
        f"- warning_count：{result.warning_count}",
        f"- suspect_count：{result.suspect_count}",
        f"- duplicate_character_warning_count：{result.duplicate_character_warning_count}",
        f"- branch_checksum_error_count：{result.branch_checksum_error_count}",
        f"- special_rule_error_count：{result.special_rule_error_count}",
        f"- rank_error_count：{result.rank_error_count}",
        f"- deprecated_reference_error_count：{result.deprecated_reference_error_count}",
        "",
        "## Findings",
        "",
    ]
    if not result.findings:
        lines.append("- 无")
    for index, item in enumerate(result.findings, start=1):
        lines.extend(
            [
                f"### {index}. {item.severity.upper()} {item.code}",
                "",
                f"- severity：{item.severity}",
                f"- code：{item.code}",
                f"- file：{item.file}",
                f"- json_path：{item.json_path}",
                f"- message：{item.message}",
                f"- impact：{item.impact}",
                f"- fix_suggestion：{item.fix_suggestion}",
                "- example_patch：",
                "",
                "```json",
                item.example_patch,
                "```",
            ]
        )
        if item.character:
            lines.extend(
                [
                    f"- character：{item.character}",
                    f"- godways：{', '.join(item.godways)}",
                    f"- role_descriptions：{'; '.join(item.role_descriptions)}",
                    f"- exclusive_conflict：{item.exclusive_conflict}",
                    f"- conflict_analysis：{item.conflict_analysis}",
                ]
            )
        lines.append("")
    lines.extend([result.final_message, ""])
    return "\n".join(lines)


def resolve_output(project_dir: Path, output: Path | None) -> Path:
    if output is None:
        return project_dir / REPORT_RELATIVE_PATH
    if output.is_absolute():
        return output
    return project_dir / output


def run_l3_verification(
    project_dir: Path | str | None = None,
    *,
    config_dir: Path | str | None = None,
    output: Path | str | None = None,
    timestamped_report: bool = False,
) -> L3VerificationResult:
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    resolved_config_dir = Path(config_dir).resolve() if config_dir is not None else root / "config"
    report_path = resolve_output(root, Path(output) if output is not None else None)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    result = L3VerificationResult(
        ok=False,
        project_dir=root,
        config_dir=resolved_config_dir,
        report_path=report_path,
    )

    power = load_json_file(resolved_config_dir, POWER_FILE, result)
    catalog = load_json_file(resolved_config_dir, CATALOG_FILE, result)
    progression = load_json_file(resolved_config_dir, PROGRESSION_FILE, result)
    special = load_json_file(resolved_config_dir, SPECIAL_FILE, result)

    capability_rules = validate_power_realm(power, result) if isinstance(power, dict) else {}
    catalog_by_id = validate_catalog(catalog, result) if isinstance(catalog, dict) else {}
    forbidden_patterns = validate_special_rules(special, catalog_by_id, result) if isinstance(special, dict) else []
    if isinstance(progression, dict):
        validate_progression(
            progression,
            catalog_by_id,
            capability_rules,
            forbidden_patterns,
            result,
        )

    finalize_counts(result)
    report = build_report(result)
    report_path.write_text(report, encoding="utf-8")
    if timestamped_report:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        timestamped = report_path.with_name(f"{report_path.stem}_{timestamp}{report_path.suffix}")
        timestamped.write_text(report, encoding="utf-8")
        result.timestamped_report_path = timestamped
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify L3.2 power system seed JSON files.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--config-dir", type=Path, default=None, help="Directory containing L3.2 seed JSON files. Defaults to <project-dir>/config.")
    parser.add_argument("--output", type=Path, default=None, help="Markdown report path. Defaults to outputs/l3_power_system_seed_verify_report.md.")
    parser.add_argument("--timestamped-report", action="store_true", help="Also write a timestamped report next to the main report.")
    args = parser.parse_args()
    result = run_l3_verification(
        args.project_dir,
        config_dir=args.config_dir,
        output=args.output,
        timestamped_report=args.timestamped_report,
    )
    print(f"L3.2 verify report: {result.report_path}")
    if result.timestamped_report_path is not None:
        print(f"L3.2 timestamped verify report: {result.timestamped_report_path}")
    print(result.final_message)
    raise SystemExit(0 if result.ok else 1)


if __name__ == "__main__":
    main()
