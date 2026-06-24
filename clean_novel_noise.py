from __future__ import annotations

import re
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
PROJECT_ROOT = SCRIPT_PATH.parent
INPUT_DIR = PROJECT_ROOT / "chapters_raw"
OUTPUT_DIR = PROJECT_ROOT / "chapters"

READ_ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk")
ASCII_PUNCTUATION_MAP = str.maketrans(
    {
        ",": "，",
        "?": "？",
        "!": "！",
        ":": "：",
        ";": "；",
    }
)

INVISIBLE_CHARS_RE = re.compile(r"[\u200b\ufeff\xa0\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
INLINE_WHITESPACE_RE = re.compile(r"[ \t]+")
FRONT_MATTER_RE = re.compile(r"^(---\n.*?\n---)(\n*)", re.DOTALL)
CHAR_COUNT_RE = re.compile(r"(?m)^char_count:\s*.*$")


def configure_console_output() -> None:
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            continue


def discover_input_files(input_dir: Path) -> list[Path]:
    files = [
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in {".md", ".txt"}
    ]
    return sorted(files, key=lambda item: item.name)


def read_text_with_fallback(path: Path) -> tuple[str, str]:
    failures: list[str] = []
    for encoding in READ_ENCODINGS:
        try:
            text = path.read_text(encoding=encoding)
            return text.replace("\r\n", "\n").replace("\r", "\n"), encoding
        except UnicodeDecodeError as exc:
            failures.append(f"{encoding}: {exc}")

    detail = "; ".join(failures)
    raise UnicodeDecodeError("unknown", b"", 0, 0, f"无法读取文件 {path.name}: {detail}")


def split_markdown_components(text: str) -> tuple[str, str, str]:
    front_matter = ""
    title_block = ""
    body = text

    front_matter_match = FRONT_MATTER_RE.match(text)
    if front_matter_match:
        front_matter = front_matter_match.group(1)
        body = text[front_matter_match.end() :]

    lines = body.split("\n")
    title_index = None
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        if line.startswith("# "):
            title_index = index
        break

    if title_index is not None:
        title_block = lines[title_index].rstrip()
        body = "\n".join(lines[title_index + 1 :])
    else:
        body = "\n".join(lines)

    return front_matter, title_block, body


def remove_invisible_chars(text: str) -> str:
    return INVISIBLE_CHARS_RE.sub("", text)


def normalize_ascii_punctuation(text: str) -> str:
    return text.translate(ASCII_PUNCTUATION_MAP)


def clean_body_lines(body: str) -> str:
    cleaned_paragraphs: list[str] = []
    for raw_line in body.split("\n"):
        line = raw_line.strip(" \t")
        line = INLINE_WHITESPACE_RE.sub(" ", line)
        if not line:
            continue
        cleaned_paragraphs.append(line)
    return "\n\n".join(cleaned_paragraphs)


def update_front_matter_char_count(front_matter: str, cleaned_body: str) -> str:
    if not front_matter or "char_count:" not in front_matter:
        return front_matter
    return CHAR_COUNT_RE.sub(f"char_count: {len(cleaned_body)}", front_matter, count=1)


def clean_chapter_content(text: str, suffix: str) -> tuple[str, dict[str, int]]:
    suffix = suffix.lower()
    if suffix == ".md":
        front_matter, title_block, body = split_markdown_components(text)
        original_body_length = len(body)
        body = remove_invisible_chars(body)
        body = normalize_ascii_punctuation(body)
        cleaned_body = clean_body_lines(body)
        front_matter = update_front_matter_char_count(front_matter, cleaned_body)

        parts: list[str] = []
        if front_matter:
            parts.append(front_matter)
        if title_block:
            parts.append(title_block)
        if cleaned_body:
            parts.append(cleaned_body)

        cleaned_text = "\n\n".join(parts).rstrip("\n") + "\n"
        return cleaned_text, {
            "before_chars": original_body_length,
            "after_chars": len(cleaned_body),
        }

    original_length = len(text)
    text = remove_invisible_chars(text)
    text = normalize_ascii_punctuation(text)
    cleaned_text = clean_body_lines(text).rstrip("\n") + "\n"
    return cleaned_text, {
        "before_chars": original_length,
        "after_chars": len(cleaned_text.rstrip("\n")),
    }


def process_one_file(src: Path, dst: Path) -> dict[str, object]:
    text, encoding = read_text_with_fallback(src)
    cleaned_text, stats = clean_chapter_content(text, src.suffix)
    dst.write_text(cleaned_text, encoding="utf-8", newline="\n")
    return {
        "name": src.name,
        "encoding": encoding,
        "before_chars": stats["before_chars"],
        "after_chars": stats["after_chars"],
    }


def main() -> int:
    configure_console_output()
    print("🚀 开始清洗小说章节...")

    if not INPUT_DIR.exists() or not INPUT_DIR.is_dir():
        print(f"❌ 输入目录不存在: {INPUT_DIR}")
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    input_files = discover_input_files(INPUT_DIR)

    if not input_files:
        print(f"⚠️ 未在输入目录中发现 .md 或 .txt 文件: {INPUT_DIR}")
        print(f"📊 总文件数: 0 | 成功: 0 | 失败: 0 | 输出目录: {OUTPUT_DIR}")
        return 0

    success_count = 0
    failure_count = 0

    for src in input_files:
        dst = OUTPUT_DIR / src.name
        try:
            process_one_file(src, dst)
            success_count += 1
            print(f"✅ 已成功清洗: {src.name}")
        except Exception as exc:
            failure_count += 1
            print(f"❌ 清洗失败: {src.name} -> {exc}")

    print(
        f"📊 总文件数: {len(input_files)} | 成功: {success_count} | 失败: {failure_count} | "
        f"输出目录: {OUTPUT_DIR}"
    )

    if failure_count == 0:
        print("🎉 所有章节清洗完毕！")
        return 0

    print("⚠️ 部分章节清洗失败，请检查日志。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
