"""文档草稿与成品 Word 确定性质量体检器（M4 深化版）：覆盖大纲缺章对齐、关键工期口径交叉核对、暗标红线扫描与成品版式检验。"""
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

BLIND_BID_FORBIDDEN_PATTERNS = [
    (re.compile(r"(?:我司名称|投标人名称|编制单位)\s*[:：]\s*\S+"), "暗标疑似泄露投标单位署名信息"),
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "暗标正文疑似出现真实手机号码"),
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "暗标正文疑似出现电子邮箱地址"),
]

IMAGE_LINK_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
PLANNED_DRAFT_RE = re.compile(r"drafts/[A-Za-z0-9_\-\u4e00-\u9fff]+\.md")
DURATION_DAYS_RE = re.compile(r"(?:总工期|计划工期|施工工期)[^\n。；;]{0,18}?(\d{2,4})\s*日历天")


def audit_markdown_drafts(
    draft_files: list[Path],
    workspace_dir: Path | None = None,
    min_chapter_chars: int = 200,
    blind_mode: bool = False,
    expected_duration_days: int | None = None,
) -> dict[str, Any]:
    """
    对所有章节 Markdown 草稿执行多维确定性硬检：
    1. 占位符、最低字数、一级标题规范与图片断链检查；
    2. 对照 `02-plan.md`（若存在）核查规划的 `drafts/*.md` 是否全部落盘无缺漏；
    3. 跨章节总工期（`XX 日历天`）口径前后一致性核查；
    4. 暗标模式（`blind_mode=True`）下的署名/手机号/邮箱红线扫描。
    """
    issues: list[dict[str, Any]] = []
    chapter_stats: list[dict[str, Any]] = []

    if not draft_files:
        return {
            "passed": False,
            "error_count": 1,
            "warning_count": 0,
            "issue_count": 1,
            "issues": [{"file": "<none>", "severity": "error", "message": "未找到任何章节草稿文件"}],
            "chapters": [],
        }

    # 若工作区存在 02-plan.md，自动比对规划声明的章节文件是否齐全
    if workspace_dir is not None:
        plan_file = workspace_dir / "02-plan.md"
        if plan_file.is_file():
            plan_txt = plan_file.read_text(encoding="utf-8", errors="replace")
            planned_rels = sorted(set(PLANNED_DRAFT_RE.findall(plan_txt)))
            existing_rels = {
                p.resolve().relative_to(workspace_dir.resolve()).as_posix()
                for p in draft_files
                if p.resolve().is_relative_to(workspace_dir.resolve())
            }
            for req_rel in planned_rels:
                if req_rel not in existing_rels:
                    issues.append({
                        "file": req_rel,
                        "severity": "error",
                        "message": f"`02-plan.md` 中规划的章节文件尚未生成落盘：{req_rel}",
                    })

        # 若未显式传入 expected_duration_days，尝试从 01-requirements.md 自动识别唯一标准总工期
        if expected_duration_days is None:
            req_file = workspace_dir / "01-requirements.md"
            if req_file.is_file():
                req_txt = req_file.read_text(encoding="utf-8", errors="replace")
                found_days = {int(m) for m in DURATION_DAYS_RE.findall(req_txt)}
                if len(found_days) == 1:
                    expected_duration_days = next(iter(found_days))

    observed_durations: dict[int, list[str]] = {}

    for path in draft_files:
        rel_name = (
            path.resolve().relative_to(workspace_dir.resolve()).as_posix()
            if workspace_dir and path.resolve().is_relative_to(workspace_dir.resolve())
            else path.name
        )
        text = path.read_text(encoding="utf-8").strip()
        clean_chars = len(re.sub(r"\s+", "", text))
        headings = re.findall(r"^(#{1,4})\s+(.+)$", text, flags=re.MULTILINE)
        tables_found = len(re.findall(r"^\|.+\|\s*$", text, flags=re.MULTILINE))
        images_found = len(IMAGE_LINK_RE.findall(text))

        chapter_stats.append({
            "file": rel_name,
            "characters": clean_chars,
            "heading_count": len(headings),
            "table_lines": tables_found,
            "image_count": images_found,
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
                "message": "章节文件首级标题建议使用一级标题 `# 第X章 ...` 以确保 Word 目录层级统一",
            })

        for day_str in DURATION_DAYS_RE.findall(text):
            d_val = int(day_str)
            observed_durations.setdefault(d_val, []).append(rel_name)
            if expected_duration_days is not None and d_val > expected_duration_days:
                issues.append({
                    "file": rel_name,
                    "severity": "error",
                    "message": f"工期超限矛盾：文中出现 `{d_val} 日历天`，超出招标要求上限 `{expected_duration_days} 日历天`",
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

            if blind_mode:
                for b_pat, b_reason in BLIND_BID_FORBIDDEN_PATTERNS:
                    if b_pat.search(line):
                        issues.append({
                            "file": rel_name,
                            "line": line_no,
                            "severity": "error",
                            "message": f"[暗标红线拦截] {b_reason}：{line.strip()[:80]}",
                        })

            if workspace_dir is not None:
                for _alt, img_src in IMAGE_LINK_RE.findall(line):
                    img_path = (workspace_dir / img_src.strip()).resolve()
                    if not img_path.is_file():
                        issues.append({
                            "file": rel_name,
                            "line": line_no,
                            "severity": "error",
                            "message": f"引用的配图文件不存在：{img_src}",
                        })

    if expected_duration_days is None and len(observed_durations) > 1:
        issues.append({
            "file": "<cross_chapter>",
            "severity": "warning",
            "message": f"跨章节检测到多个不同的总工期表述 { {k: v for k, v in observed_durations.items()} }，请核对前后口径是否统一",
        })

    error_count = sum(1 for item in issues if item["severity"] == "error")
    return {
        "passed": error_count == 0,
        "error_count": error_count,
        "warning_count": len(issues) - error_count,
        "issue_count": len(issues),
        "expected_duration_days": expected_duration_days,
        "issues": issues,
        "chapters": chapter_stats,
        "total_characters": sum(c["characters"] for c in chapter_stats),
    }


def audit_docx_file(docx_path: Path, blind_mode: bool = False) -> dict[str, Any]:
    """打开编译后的 .docx 成品文件，核验原生章节、表格、分节版式以及暗标纯黑白合规性。"""
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
        if blind_mode:
            for run in p.runs:
                rgb = run.font.color.rgb if run.font and run.font.color else None
                if rgb is not None and str(rgb) != "000000":
                    issues.append(f"暗标模式检测到非纯黑字体颜色 (#{rgb})：{txt[:40]}")
                    break

    if blind_mode:
        for sec in doc.sections:
            hdr_txt = "".join(hp.text.strip() for hp in sec.header.paragraphs)
            if hdr_txt:
                issues.append(f"暗标模式页眉不允许出现文字标识：{hdr_txt[:40]}")
                break

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

    landscape_sections = sum(1 for s in doc.sections if s.page_width > s.page_height)
    estimated_pages = max(1, round((total_text_chars / 750.0) + len(doc.tables) * 0.4))

    return {
        "passed": len(issues) == 0,
        "docx_path": str(docx_path),
        "bytes": docx_path.stat().st_size,
        "section_count": len(doc.sections),
        "landscape_section_count": landscape_sections,
        "heading_1_count": len(h1_titles),
        "heading_1_titles": h1_titles,
        "table_count": len(doc.tables),
        "total_characters": total_text_chars,
        "estimated_pages": estimated_pages,
        "issues": issues,
    }
