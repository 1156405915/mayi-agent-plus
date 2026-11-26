"""文档草稿与成品 Word 确定性质量体检器：客观扫描占位符残留、空章节、断链图片与表格异常。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document

PLACEHOLDER_PATTERNS = [
    (re.compile(r"\b(?:TODO|TBD|FIXME)\b", re.IGNORECASE), "残留英文待办占位符 (TODO/TBD/FIXME)"),
    (re.compile(r"(?:待补充|待完善|待填写|此处略|内容略|后续补充|\[请填写.*?\]|【待.*?】)"), "残留中文未完成占位词"),
    (re.compile(r"(?<![A-Za-z0-9])X{3,}(?![A-Za-z0-9])"), "残留连续 XXX 模糊占位符"),
]

IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def audit_markdown_drafts(
    draft_files: list[Path],
    workspace_dir: Path | None = None,
    min_chapter_chars: int = 200,
) -> dict[str, Any]:
    """
    对所有章节 Markdown 草稿执行确定性硬规则扫描，输出客观缺陷清单。
    用于在调用独立 Reviewer 与合成 Word 前快速拦截低级疏漏。
    """
    issues: list[dict[str, Any]] = []
    chapter_stats: list[dict[str, Any]] = []

    if not draft_files:
        return {
            "passed": False,
            "issue_count": 1,
            "issues": [{"file": "<none>", "severity": "error", "message": "未找到任何章节草稿文件"}],
            "chapters": [],
        }

    for path in draft_files:
        rel_name = (
            path.resolve().relative_to(workspace_dir.resolve()).as_posix()
            if workspace_dir and path.resolve().is_relative_to(workspace_dir.resolve())
            else path.name
        )
        text = path.read_text(encoding="utf-8").strip()
        clean_chars = len(re.sub(r"\s+", "", text))
        headings = re.findall(r"^(#{1,4})\s+(.+)$", text, flags=re.MULTILINE)

        chapter_stats.append({
            "file": rel_name,
            "characters": clean_chars,
            "heading_count": len(headings),
        })

        if clean_chars < min_chapter_chars:
            issues.append({
                "file": rel_name,
                "severity": "error",
                "message": f"章节有效正文字数过少（仅 {clean_chars} 字，低于下限 {min_chapter_chars} 字）",
            })

        if not headings or len(headings[0][0]) != 1:
            issues.append({
                "file": rel_name,
                "severity": "warning",
                "message": "章节文件首级标题建议使用一级标题 `# 第X章 ...` 以确保 Word 目录层级一致",
            })

        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern, reason in PLACEHOLDER_PATTERNS:
                if pattern.search(line):
                    issues.append({
                        "file": rel_name,
                        "line": line_no,
                        "severity": "error",
                        "message": f"{reason}：{line.strip()[:80]}",
                    })

            if workspace_dir is not None:
                for _alt, img_src in IMAGE_LINK_RE.findall(line):
                    img_path = (workspace_dir / img_src.strip()).resolve()
                    if not img_path.is_file():
                        issues.append({
                            "file": rel_name,
                            "line": line_no,
                            "severity": "error",
                            "message": f"引用的图片文件不存在：{img_src}",
                        })

    error_count = sum(1 for item in issues if item["severity"] == "error")
    return {
        "passed": error_count == 0,
        "error_count": error_count,
        "warning_count": len(issues) - error_count,
        "issue_count": len(issues),
        "issues": issues,
        "chapters": chapter_stats,
        "total_characters": sum(c["characters"] for c in chapter_stats),
    }


def audit_docx_file(docx_path: Path) -> dict[str, Any]:
    """打开编译生成的 .docx 成品文件，核验原生段落、一级章节标题与原生表格结构是否真实落盘。"""
    if not docx_path.is_file() or docx_path.stat().st_size == 0:
        return {
            "passed": False,
            "issues": ["目标 DOCX 文件不存在或大小为 0 字节"],
        }

    doc = Document(str(docx_path))
    issues: list[str] = []
    h1_titles: list[str] = []
    total_text_chars = 0

    for p in doc.paragraphs:
        txt = p.text.strip()
        total_text_chars += len(txt)
        if p.style and p.style.name == "Heading 1" and txt:
            h1_titles.append(txt)
        for pattern, reason in PLACEHOLDER_PATTERNS:
            if pattern.search(txt):
                issues.append(f"成品段落发现{reason}：{txt[:60]}")

    empty_tables = 0
    for tbl in doc.tables:
        cell_texts = [cell.text.strip() for row in tbl.rows for cell in row.cells]
        total_text_chars += sum(len(t) for t in cell_texts)
        if len(tbl.rows) < 2 or not any(cell_texts):
            empty_tables += 1

    if empty_tables > 0:
        issues.append(f"发现 {empty_tables} 个空表或仅有表头无数据行的残缺表格")

    if not h1_titles:
        issues.append("成品文档未检测到任何 Heading 1 一级章节标题")

    # 按中文 A4 标准版面约 750 字/页 + 表格折算做页数范围估算供交付参考
    estimated_pages = max(1, round((total_text_chars / 750.0) + len(doc.tables) * 0.35))

    return {
        "passed": len(issues) == 0,
        "docx_path": str(docx_path),
        "bytes": docx_path.stat().st_size,
        "heading_1_count": len(h1_titles),
        "heading_1_titles": h1_titles,
        "table_count": len(doc.tables),
        "total_characters": total_text_chars,
        "estimated_pages": estimated_pages,
        "issues": issues,
    }
