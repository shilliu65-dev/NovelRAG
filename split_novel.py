from __future__ import annotations

import csv
import hashlib
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
SOURCE_PATH = BASE_DIR / "source" / "271579.txt"
CHAPTERS_DIR = BASE_DIR / "chapters"
INDEX_DIR = BASE_DIR / "index"
LOGS_DIR = BASE_DIR / "logs"
INDEX_PATH = INDEX_DIR / "chapters_index.csv"
REPORT_PATH = LOGS_DIR / "split_report.txt"

ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "gbk")

CHINESE_NUMBER = r"[零〇一二两三四五六七八九十百千万]+"
ARABIC_NUMBER = r"[0-9０-９]+"
CHAPTER_NUMBER = rf"(?:{ARABIC_NUMBER}|{CHINESE_NUMBER})"

TITLE_PATTERNS = (
    re.compile(rf"^\s*第\s*{CHAPTER_NUMBER}\s*章(?:\s+\S.{{0,80}}|\s*)$"),
    re.compile(r"^\s*(?:序章|楔子|引子|后记|尾声)(?:\s+\S.{0,80}|\s*)$"),
    re.compile(rf"^\s*番外(?:\s*{CHAPTER_NUMBER})?(?:\s+\S.{{0,80}}|\s*)$"),
)

WINDOWS_INVALID_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class Chapter:
    order: int
    chapter_id: str
    title: str
    filename: str
    body: str
    char_count: int
    md5: str = ""


def read_source(path: Path) -> tuple[str, str]:
    if not path.exists():
        raise FileNotFoundError(f"找不到源文件: {path}")
    if not path.is_file():
        raise OSError(f"源路径不是文件: {path}")

    failures: list[str] = []
    for encoding in ENCODINGS:
        try:
            return path.read_text(encoding=encoding), encoding
        except UnicodeDecodeError as exc:
            failures.append(f"{encoding}: {exc}")

    detail = "\n".join(failures)
    raise UnicodeDecodeError(
        "unknown",
        b"",
        0,
        0,
        f"编码读取失败，已尝试: {', '.join(ENCODINGS)}\n{detail}",
    )


def is_chapter_title(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if len(stripped) > 90:
        return False
    return any(pattern.fullmatch(line) for pattern in TITLE_PATTERNS)


def find_chapter_starts(lines: list[str]) -> list[tuple[int, str]]:
    starts: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if is_chapter_title(line):
            starts.append((index, line.strip()))
    return starts


def sanitize_filename_part(value: str, max_length: int = 80) -> str:
    sanitized = WINDOWS_INVALID_FILENAME_CHARS.sub("_", value.strip())
    sanitized = re.sub(r"\s+", "_", sanitized)
    sanitized = sanitized.strip(" ._")
    if not sanitized:
        sanitized = "untitled"
    return sanitized[:max_length].rstrip(" ._")


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def build_chapters(text: str) -> list[Chapter]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    starts = find_chapter_starts(lines)
    if not starts:
        raise ValueError("没有识别到章节标题，请检查源文件或标题格式。")

    chapters: list[Chapter] = []
    for order, (start_index, title) in enumerate(starts, start=1):
        end_index = starts[order][0] if order < len(starts) else len(lines)
        body_lines = lines[start_index + 1 : end_index]
        body = "\n".join(body_lines).strip("\n")
        chapter_id = f"chapter_{order:03d}"
        filename_title = sanitize_filename_part(title)
        filename = f"{chapter_id}_{filename_title}.md"
        chapters.append(
            Chapter(
                order=order,
                chapter_id=chapter_id,
                title=title,
                filename=filename,
                body=body,
                char_count=len(body),
            )
        )
    return chapters


def render_markdown(chapter: Chapter) -> str:
    source = SOURCE_PATH.name
    metadata = [
        "---",
        f"chapter_id: {yaml_quote(chapter.chapter_id)}",
        f"order: {chapter.order}",
        f"title: {yaml_quote(chapter.title)}",
        f"source: {yaml_quote(source)}",
        f"char_count: {chapter.char_count}",
        "---",
        "",
        f"# {chapter.title}",
        "",
    ]
    if chapter.body:
        return "\n".join(metadata) + chapter.body + "\n"
    return "\n".join(metadata)


def write_outputs(chapters: list[Chapter]) -> tuple[list[Chapter], list[str]]:
    warnings: list[str] = []
    CHAPTERS_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    old_chapter_files = list(CHAPTERS_DIR.glob("chapter_*.md"))
    if old_chapter_files and len(old_chapter_files) > len(chapters):
        warnings.append(
            f"chapters 目录中已有 {len(old_chapter_files)} 个 chapter_*.md 文件，"
            f"本次输出 {len(chapters)} 个；脚本不会清理多余旧文件。"
        )

    written: list[Chapter] = []
    for chapter in chapters:
        markdown = render_markdown(chapter)
        digest = hashlib.md5(markdown.encode("utf-8")).hexdigest()
        target = CHAPTERS_DIR / chapter.filename
        target.write_text(markdown, encoding="utf-8", newline="\n")
        written.append(
            Chapter(
                order=chapter.order,
                chapter_id=chapter.chapter_id,
                title=chapter.title,
                filename=chapter.filename,
                body=chapter.body,
                char_count=chapter.char_count,
                md5=digest,
            )
        )

    with INDEX_PATH.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=("order", "chapter_id", "title", "filename", "char_count", "md5"),
        )
        writer.writeheader()
        for chapter in written:
            writer.writerow(
                {
                    "order": chapter.order,
                    "chapter_id": chapter.chapter_id,
                    "title": chapter.title,
                    "filename": chapter.filename,
                    "char_count": chapter.char_count,
                    "md5": chapter.md5,
                }
            )

    return written, warnings


def write_report(
    *,
    source_path: Path,
    encoding: str,
    chapters_detected: int,
    files_written: int,
    warnings: list[str],
    error: str | None = None,
) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "小说章节切分报告",
        f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"源文件: {source_path}",
        f"编码: {encoding or 'N/A'}",
        f"识别章节数: {chapters_detected}",
        f"输出 Markdown 文件数: {files_written}",
        f"章节目录表: {INDEX_PATH}",
        f"切分报告: {REPORT_PATH}",
        "",
        "警告:",
    ]
    if warnings:
        lines.extend(f"- {item}" for item in warnings)
    else:
        lines.append("- 无")

    lines.append("")
    lines.append("异常:")
    lines.append(f"- {error}" if error else "- 无")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    encoding = ""
    chapters_detected = 0
    files_written = 0
    warnings: list[str] = []

    try:
        text, encoding = read_source(SOURCE_PATH)
        chapters = build_chapters(text)
        chapters_detected = len(chapters)
        written, warnings = write_outputs(chapters)
        files_written = len(written)
        write_report(
            source_path=SOURCE_PATH,
            encoding=encoding,
            chapters_detected=chapters_detected,
            files_written=files_written,
            warnings=warnings,
        )
    except Exception as exc:
        error = str(exc)
        print(f"错误: {error}", file=sys.stderr)
        write_report(
            source_path=SOURCE_PATH,
            encoding=encoding,
            chapters_detected=chapters_detected,
            files_written=files_written,
            warnings=warnings,
            error=error,
        )
        return 1

    print(f"识别章节数: {chapters_detected}")
    print(f"输出 Markdown 文件数: {files_written}")
    print(f"章节目录表: {INDEX_PATH}")
    print(f"切分报告: {REPORT_PATH}")
    if warnings:
        print("警告:")
        for warning in warnings:
            print(f"- {warning}")
    else:
        print("异常: 无")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
