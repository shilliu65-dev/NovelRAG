from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.l1_chapter_importer import DB_RELATIVE_PATH, ensure_dirs, project_root_from_env


TOOL_VERSION = "v1"
DEFAULT_JSON_RELATIVE_PATH = Path("outputs") / "l3_character_candidates.json"
DEFAULT_MD_RELATIVE_PATH = Path("outputs") / "l3_character_candidates_report.md"
READ_VIEWS = ["v_l2_current_sentences", "v_l2_current_paragraphs", "v_current_chapters"]

COMMON_SURNAMES = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏苏潘范彭鲁马方任袁柳唐薛贺倪汤罗毕郝安常乐于傅齐康余顾孟黄穆萧尹姚邵湛汪祁毛狄米明计成戴宋庞熊纪舒屈项祝董梁杜阮蓝闵季贾路娄江童颜郭梅盛林钟徐邱骆高夏蔡田胡凌霍虞万卢莫房裘解应宗丁宣邓杭洪包诸左石崔吉龚程邢裴陆荣翁荀羊惠甄曲家封靳松段富巫焦巴弓牧山谷车侯全班秋仲伊宫宁仇栾甘厉祖武符刘景詹龙叶司韶黎薄印宿白蒲从索赖卓蔺屠蒙池乔闻党翟谭贡劳逄姬申扶堵冉宰桑桂濮牛寿边燕冀浦尚农温庄晏柴瞿阎充慕连茹习艾鱼容向古易慎戈廖终衡耿满弘匡国文寇广禄东欧沃利蔚越师聂晁勾敖融冷辛阚那简饶空曾沙养须丰巢关相查荆红游竺权盖益桓公姬嬴"

NON_PERSON_TERMS = {
    "灰界",
    "灾厄",
    "能力",
    "规则",
    "组织",
    "先生",
    "夫人",
    "姑娘",
    "少年",
    "少女",
    "老人",
    "男人",
    "女人",
    "陛下",
    "殿下",
    "众人",
    "人类",
    "所有人",
}
NON_PERSON_SUFFIXES = (
    "神道",
    "界域",
    "灾厄",
    "能力",
    "规则",
    "组织",
    "军团",
    "小队",
    "协会",
    "城市",
    "国度",
    "王朝",
    "城",
    "殿",
    "宫",
    "山",
    "海",
    "河",
    "伞",
    "剑",
    "刀",
    "书",
)

NAME_BEFORE_VERB = re.compile(
    r"(?P<name>[\u4e00-\u9fff]{2,4})(?:说道|问道|开口|低声道|笑道|冷笑道|沉声道|"
    r"点头|摇头|看向|望向|皱眉|走进|走到|站起|喃喃道|叹道|问)"
)
NAME_AFTER_VERB = re.compile(r"(?:看向|望向|问|叫住|对|朝着|名叫|名为|唤作)(?P<name>[\u4e00-\u9fff]{2,4})")
COMMON_SURNAME_NAME = re.compile(rf"(?P<name>[{COMMON_SURNAMES}][\u4e00-\u9fff]{{1,2}})")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def resolve_output_path(project_dir: Path, value: str | Path | None, default: Path) -> Path:
    path = Path(value) if value is not None else default
    return path if path.is_absolute() else project_dir / path


def character_id_for(name: str) -> str:
    return f"char_{sha256_text(name)[:12]}"


def normalize_name(value: str) -> str | None:
    name = re.sub(r"[^\u4e00-\u9fff]", "", value).strip()
    if len(name) < 2 or len(name) > 4:
        return None
    if name in NON_PERSON_TERMS:
        return None
    if any(name.endswith(suffix) for suffix in NON_PERSON_SUFFIXES):
        return None
    if any(term in name for term in ("神道", "界域", "灰界", "灾厄", "能力", "规则", "组织")):
        return None
    return name


def safe_flatten(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (int, float, bool)):
        return [str(value)]
    if isinstance(value, list):
        out: list[str] = []
        for item in value:
            out.extend(safe_flatten(item))
        return out
    if isinstance(value, dict):
        out: list[str] = []
        for item in value.values():
            out.extend(safe_flatten(item))
        return out
    return [str(value)]


def collect_seed_names(value: Any) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        for rep in value.get("representative_characters", []) or []:
            if isinstance(rep, dict):
                name = normalize_name(str(rep.get("name", "")))
                if name:
                    names.add(name)
        for key in ("related_character", "lord_name"):
            name = normalize_name(str(value.get(key, "")))
            if name and name != "unknown":
                names.add(name)
        if "lord_id" in value:
            for alias in safe_flatten(value.get("alias")) + safe_flatten(value.get("aliases")):
                name = normalize_name(alias)
                if name:
                    names.add(name)
        for item in value.values():
            names.update(collect_seed_names(item))
    elif isinstance(value, list):
        for item in value:
            names.update(collect_seed_names(item))
    return names


def load_seed_names(project_dir: Path) -> set[str]:
    config_dir = project_dir / "config"
    names: set[str] = set()
    if not config_dir.exists():
        return names
    for path in sorted(config_dir.glob("*.json")):
        try:
            names.update(collect_seed_names(json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return names


def iter_rule_candidates(text: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    for pattern, source_type in (
        (NAME_BEFORE_VERB, "speech_or_action_pattern"),
        (NAME_AFTER_VERB, "address_pattern"),
        (COMMON_SURNAME_NAME, "surname_pattern"),
    ):
        for match in pattern.finditer(text):
            name = normalize_name(match.group("name"))
            if name:
                candidates.append((name, source_type))
    return candidates


def sentence_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                sentence_id,
                para_id,
                chapter_id,
                chapter_num,
                version_id,
                para_index,
                sentence_index,
                global_sentence_index,
                start_offset,
                end_offset,
                sentence_hash,
                sentence_text,
                chapter_title_current
            FROM v_l2_current_sentences
            ORDER BY chapter_num, global_sentence_index, sentence_id
            """
        )
    )


def record_occurrence(occurrences: dict[str, list[dict[str, Any]]], name: str, source_type: str, row: sqlite3.Row) -> None:
    occurrences[name].append(
        {
            "source_type": source_type,
            "chapter_id": row["chapter_id"],
            "version_id": row["version_id"],
            "chapter_num": row["chapter_num"],
            "chapter_title": row["chapter_title_current"],
            "sentence_id": row["sentence_id"],
            "paragraph_id": row["para_id"],
            "sentence_start": row["start_offset"],
            "sentence_end": row["end_offset"],
            "sentence_hash": row["sentence_hash"],
        }
    )


def build_output(project_dir: Path, min_count: int, occurrences: dict[str, list[dict[str, Any]]], seed_names: set[str]) -> dict[str, Any]:
    characters: list[dict[str, Any]] = []
    for name, evidence in occurrences.items():
        if len(evidence) < min_count and name not in seed_names:
            continue
        first = min(evidence, key=lambda item: (int(item["chapter_num"]), str(item["sentence_id"])))
        characters.append(
            {
                "character_id": character_id_for(name),
                "name": name,
                "aliases": [],
                "status": "candidate",
                "source_types": sorted({item["source_type"] for item in evidence}),
                "evidence_count": len(evidence),
                "first_seen": {
                    "chapter_id": first["chapter_id"],
                    "version_id": first["version_id"],
                    "chapter_num": first["chapter_num"],
                    "chapter_title": first["chapter_title"],
                    "sentence_id": first["sentence_id"],
                    "paragraph_id": first["paragraph_id"],
                },
                "evidence": evidence[:10],
            }
        )
    characters.sort(key=lambda item: (-int(item["evidence_count"]), int(item["first_seen"]["chapter_num"]), item["name"]))
    return {
        "meta": {
            "tool": "l3_character_candidate_extractor",
            "version": TOOL_VERSION,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "project_dir": str(project_dir),
            "min_count": min_count,
            "status_policy": "candidate only",
            "read_views": READ_VIEWS,
            "writes_l1_l2": False,
            "character_count": len(characters),
        },
        "characters": characters,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def markdown_report(output: dict[str, Any]) -> str:
    lines = [
        "# L3 Character Candidates Report",
        "",
        f"- Generated at: {output['meta']['generated_at']}",
        f"- Character candidates: {output['meta']['character_count']}",
        "- Status policy: candidate only",
        "- L1/L2 mutation: none",
        "",
        "## Candidates",
        "",
    ]
    for item in output["characters"][:200]:
        first = item["first_seen"]
        lines.extend(
            [
                f"### {item['name']}",
                "",
                f"- character_id: {item['character_id']}",
                f"- evidence_count: {item['evidence_count']}",
                f"- source_types: {', '.join(item['source_types'])}",
                f"- first_seen: chapter {first['chapter_num']} {first['chapter_title']}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def run_l3_character_candidate_extractor(
    project_dir: Path | str | None = None,
    *,
    min_count: int = 2,
    output_json: Path | str | None = None,
    output_md: Path | str | None = None,
) -> dict[str, Any]:
    if min_count <= 0:
        raise ValueError("min_count must be > 0")
    root = Path(project_dir).resolve() if project_dir is not None else project_root_from_env()
    ensure_dirs(root)
    seed_names = load_seed_names(root)
    occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)

    conn = sqlite3.connect(root / DB_RELATIVE_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        for row in sentence_rows(conn):
            text = row["sentence_text"]
            seen: set[tuple[str, str]] = set()
            for seed_name in seed_names:
                if seed_name in text:
                    key = (seed_name, "seed_name")
                    if key not in seen:
                        record_occurrence(occurrences, seed_name, "seed_name", row)
                        seen.add(key)
            for name, source_type in iter_rule_candidates(text):
                key = (name, source_type)
                if key not in seen:
                    record_occurrence(occurrences, name, source_type, row)
                    seen.add(key)
    finally:
        conn.close()

    output = build_output(root, min_count, occurrences, seed_names)
    json_path = resolve_output_path(root, output_json, DEFAULT_JSON_RELATIVE_PATH)
    md_path = resolve_output_path(root, output_md, DEFAULT_MD_RELATIVE_PATH)
    write_json(json_path, output)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(markdown_report(output), encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract deterministic L3 character candidates from L2 current sentences.")
    parser.add_argument("--project-dir", type=Path, default=None, help="Project root. Defaults to NOVEL_RAG_PROJECT_DIR or repo root.")
    parser.add_argument("--min-count", type=int, default=2, help="Minimum evidence count unless a name comes from seed data.")
    parser.add_argument("--output-json", type=Path, default=None, help="Candidate JSON output path.")
    parser.add_argument("--output-md", type=Path, default=None, help="Markdown report output path.")
    args = parser.parse_args()
    output = run_l3_character_candidate_extractor(
        args.project_dir,
        min_count=args.min_count,
        output_json=args.output_json,
        output_md=args.output_md,
    )
    print(f"L3 character candidates: {output['meta']['character_count']}")


if __name__ == "__main__":
    main()
