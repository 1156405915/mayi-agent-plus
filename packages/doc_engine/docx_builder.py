"""原生 Word (.docx) 编译器：将受控标准 Markdown 章节文件结合版式预设编译为高质量 Word 文档。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from markdown_it import MarkdownIt

DEFAULT_STYLE_PRESET: dict[str, Any] = {
    "name": "standard_report",
    "page_width_cm": 21.0,
    "page_height_cm": 29.7,
    "margin_top_cm": 2.54,
    "margin_bottom_cm": 2.54,
    "margin_left_cm": 2.8,
    "margin_right_cm": 2.6,
    "east_asia_body_font": "宋体",
    "east_asia_heading_font": "黑体",
    "western_font": "Times New Roman",
    "body_size_pt": 12.0,
    "line_spacing_pt": 22.0,
    "first_line_indent_chars": 2,
    "heading_sizes_pt": {"1": 16.0, "2": 14.0, "3": 13.0, "4": 12.0},
    "table_font_size_pt": 10.5,
    "table_header_fill": "EFEFEF",
    "table_border_color": "666666",
    "include_cover": True,
    "include_toc": True,
    "header_text": "",
}

_CAPTION_RE = re.compile(r"^(?:表|图)\s*\d+(?:[-.]\d+)*\s+.+")


def _apply_run_font(
    run,
    east_asia_font: str,
    western_font: str,
    size_pt: float,
    bold: bool = False,
    italic: bool = False,
    color_hex: str = "1F2329",
) -> None:
    """统一设置 Word Run 的中西文字体族、字号与粗斜体属性，防止中文退回默认等线字体。"""
    run.font.name = western_font
    r_pr = run._element.get_or_add_rPr()
    r_pr.get_or_add_rFonts().set(qn("w:eastAsia"), east_asia_font)
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    run.font.color.rgb = RGBColor.from_string(color_hex)


def _add_xml_field(paragraph, instruction: str, placeholder_text: str = "") -> None:
    """向段落写入原生 Word 域指令（如自动页码 PAGE 或自动目录 TOC）。"""
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")

    paragraph.add_run()._r.append(begin)
    paragraph.add_run()._r.append(instr)
    paragraph.add_run()._r.append(separate)
    if placeholder_text:
        paragraph.add_run(placeholder_text)
    paragraph.add_run()._r.append(end)


def _configure_document_styles(doc: Document, preset: dict[str, Any]) -> None:
    """初始化页面纸张边距、页眉页脚页码及正文与各级标题的基础段落样式。"""
    section = doc.sections[0]
    section.page_width = Cm(float(preset["page_width_cm"]))
    section.page_height = Cm(float(preset["page_height_cm"]))
    section.top_margin = Cm(float(preset["margin_top_cm"]))
    section.bottom_margin = Cm(float(preset["margin_bottom_cm"]))
    section.left_margin = Cm(float(preset["margin_left_cm"]))
    section.right_margin = Cm(float(preset["margin_right_cm"]))

    # 配置页眉提示与居中页码页脚
    header_text = str(preset.get("header_text") or "").strip()
    if header_text:
        hp = section.header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        hr = hp.add_run(header_text)
        _apply_run_font(hr, preset["east_asia_body_font"], preset["western_font"], 9.5, color_hex="666666")

    fp = section.footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r_pre = fp.add_run("- ")
    _apply_run_font(r_pre, preset["east_asia_body_font"], preset["western_font"], 9.5, color_hex="555555")
    _add_xml_field(fp, " PAGE ", "1")
    r_post = fp.add_run(" -")
    _apply_run_font(r_post, preset["east_asia_body_font"], preset["western_font"], 9.5, color_hex="555555")

    # 配置 Normal 正文样式
    normal = doc.styles["Normal"]
    normal.font.name = preset["western_font"]
    normal._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), preset["east_asia_body_font"])
    normal.font.size = Pt(float(preset["body_size_pt"]))
    normal.paragraph_format.line_spacing = Pt(float(preset["line_spacing_pt"]))
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(4)

    # 配置 Heading 1~4 样式，确保 Word 导航窗格与自动目录能够准确识别章节大纲
    heading_sizes = preset.get("heading_sizes_pt", DEFAULT_STYLE_PRESET["heading_sizes_pt"])
    for level in range(1, 5):
        style = doc.styles[f"Heading {level}"]
        size_pt = float(heading_sizes.get(str(level), 12.0))
        style.font.name = preset["western_font"]
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), preset["east_asia_heading_font"])
        style.font.size = Pt(size_pt)
        style.font.bold = True
        style.font.color.rgb = RGBColor.from_string("000000")
        pf = style.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.space_before = Pt(12 if level == 1 else 8)
        pf.space_after = Pt(6 if level == 1 else 4)
        pf.line_spacing = Pt(max(float(preset["line_spacing_pt"]), size_pt * 1.4))
        pf.keep_with_next = True


def _render_inline_tokens(
    paragraph,
    children: list[Any],
    preset: dict[str, Any],
    base_font: str,
    size_pt: float,
    force_bold: bool = False,
    workspace_dir: Path | None = None,
    doc: Document | None = None,
) -> bool:
    """
    将 markdown-it 行内 Token 序列渲染为原生 Word Run 对象。
    若该段落实际是独立图片语法 `![图题](path)`，则直接居中插入图片与题注并返回 True。
    """
    if (
        doc is not None
        and workspace_dir is not None
        and len(children) == 1
        and children[0].type == "image"
    ):
        img_token = children[0]
        src = dict(img_token.attrs or {}).get("src", "")
        caption_text = img_token.content or "".join(c.content for c in (img_token.children or []))
        candidate = (workspace_dir / src).resolve()
        if candidate.is_file() and candidate.is_relative_to(workspace_dir.resolve()):
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            paragraph.paragraph_format.first_line_indent = Pt(0)
            usable_width_cm = (
                float(preset["page_width_cm"])
                - float(preset["margin_left_cm"])
                - float(preset["margin_right_cm"])
            )
            paragraph.add_run().add_picture(str(candidate), width=Cm(min(14.5, usable_width_cm * 0.92)))
            if caption_text.strip():
                cap_p = doc.add_paragraph()
                cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                cap_p.paragraph_format.first_line_indent = Pt(0)
                cap_p.paragraph_format.space_after = Pt(6)
                label = caption_text.strip()
                if not label.startswith("图"):
                    label = f"图 {label}"
                cap_run = cap_p.add_run(label)
                _apply_run_font(cap_run, preset["east_asia_heading_font"], preset["western_font"], 10.5, bold=True)
            return True

    bold_depth = 1 if force_bold else 0
    italic_depth = 0

    for token in children:
        t = token.type
        if t == "strong_open":
            bold_depth += 1
        elif t == "strong_close":
            bold_depth = max(0, bold_depth - 1)
        elif t == "em_open":
            italic_depth += 1
        elif t == "em_close":
            italic_depth = max(0, italic_depth - 1)
        elif t == "softbreak" or t == "hardbreak":
            paragraph.add_run("\n")
        elif t == "code_inline":
            run = paragraph.add_run(token.content)
            _apply_run_font(run, preset["east_asia_body_font"], "Consolas", size_pt - 0.5, bold=bold_depth > 0)
        elif t == "text":
            if token.content:
                run = paragraph.add_run(token.content)
                _apply_run_font(
                    run,
                    base_font,
                    preset["western_font"],
                    size_pt,
                    bold=bold_depth > 0,
                    italic=italic_depth > 0,
                )
    return False


def _set_table_borders_and_padding(table, border_color: str) -> None:
    """为原生 Word 表格统一设置单线全边框，消除默认无边框样式导致的排版松散。"""
    tbl_pr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge_name in ("top", "left", "bottom", "right", "insideH", "insideV"):
        edge = OxmlElement(f"w:{edge_name}")
        edge.set(qn("w:val"), "single")
        edge.set(qn("w:sz"), "4")
        edge.set(qn("w:space"), "0")
        edge.set(qn("w:color"), border_color)
        borders.append(edge)
    tbl_pr.append(borders)


def _render_table_tokens(doc: Document, tokens: list[Any], start_idx: int, preset: dict[str, Any]) -> int:
    """解析 markdown-it 的表格 Token 区间，渲染带跨页重复表头与自动列宽权重的原生 Word 表格。"""
    rows_tokens: list[tuple[bool, list[list[Any]]]] = []
    in_thead = False
    current_row: list[list[Any]] = []
    idx = start_idx + 1

    while idx < len(tokens) and tokens[idx].type != "table_close":
        tok = tokens[idx]
        if tok.type == "thead_open":
            in_thead = True
        elif tok.type == "thead_close":
            in_thead = False
        elif tok.type == "tr_open":
            current_row = []
        elif tok.type in ("th_open", "td_open"):
            inline_tok = tokens[idx + 1] if idx + 1 < len(tokens) and tokens[idx + 1].type == "inline" else None
            current_row.append(inline_tok.children if inline_tok and inline_tok.children else [])
        elif tok.type == "tr_close":
            if current_row:
                rows_tokens.append((in_thead, current_row))
        idx += 1

    if not rows_tokens:
        return idx

    col_count = max(len(r[1]) for r in rows_tokens)
    table = doc.add_table(rows=0, cols=col_count)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    _set_table_borders_and_padding(table, preset.get("table_border_color", "666666"))

    # 根据每列最大字符长度估算合理列宽比例，避免序号列过宽而说明列被严重挤压。
    col_weights = [4.0] * col_count
    for _, row_cells in rows_tokens[:30]:
        for c_i, children in enumerate(row_cells):
            text_len = sum(len(getattr(ch, "content", "") or "") for ch in children)
            col_weights[c_i] = max(col_weights[c_i], min(float(text_len + 2), 36.0))
    weight_sum = sum(col_weights) or 1.0

    section = doc.sections[-1]
    usable_width = section.page_width - section.left_margin - section.right_margin
    col_widths = [int(usable_width * (w / weight_sum)) for w in col_weights]
    for col, w_val in zip(table.columns, col_widths):
        col.width = w_val

    table_font_size = float(preset.get("table_font_size_pt", 10.5))
    header_fill = str(preset.get("table_header_fill", "EFEFEF"))

    for row_index, (is_header, row_cells) in enumerate(rows_tokens):
        row = table.add_row()
        if is_header or row_index == 0:
            row._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        padded_cells = row_cells + [[] for _ in range(col_count - len(row_cells))]
        for cell, children, w_val in zip(row.cells, padded_cells, col_widths):
            cell.width = w_val
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_mar = OxmlElement("w:tcMar")
            for side, dxa in (("top", "80"), ("bottom", "80"), ("left", "110"), ("right", "110")):
                m = OxmlElement(f"w:{side}")
                m.set(qn("w:w"), dxa)
                m.set(qn("w:type"), "dxa")
                tc_mar.append(m)
            tc_pr.append(tc_mar)

            p = cell.paragraphs[0]
            p.paragraph_format.first_line_indent = Pt(0)
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = Pt(table_font_size * 1.35)

            if is_header or row_index == 0:
                shd = OxmlElement("w:shd")
                shd.set(qn("w:fill"), header_fill)
                tc_pr.append(shd)
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                _render_inline_tokens(
                    p, children, preset, preset["east_asia_heading_font"], table_font_size, force_bold=True
                )
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                _render_inline_tokens(
                    p, children, preset, preset["east_asia_body_font"], table_font_size, force_bold=False
                )

    # 表格后留出微距，避免紧贴下一段正文显得拥挤
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(2)
    spacer.paragraph_format.line_spacing = Pt(4)
    return idx


def _render_markdown_chapter(
    doc: Document,
    md_text: str,
    preset: dict[str, Any],
    workspace_dir: Path | None = None,
) -> None:
    """将单章 Markdown 语法树逐块编译追加到目标 Word 文档中。"""
    md = MarkdownIt("commonmark").enable("table")
    # 剥离顶部 HTML 注释元数据，避免注释文本进入正式交付正文
    cleaned = re.sub(r"<!--.*?-->", "", md_text, flags=re.DOTALL).strip()
    tokens = md.parse(cleaned)

    body_size = float(preset["body_size_pt"])
    indent_chars = int(preset.get("first_line_indent_chars", 2))
    heading_sizes = preset.get("heading_sizes_pt", DEFAULT_STYLE_PRESET["heading_sizes_pt"])

    list_stack: list[tuple[str, int]] = []
    idx = 0

    while idx < len(tokens):
        tok = tokens[idx]
        t = tok.type

        if t == "heading_open":
            level = min(4, max(1, int(tok.tag[1])))
            inline_tok = tokens[idx + 1] if idx + 1 < len(tokens) else None
            p = doc.add_paragraph(style=f"Heading {level}")
            p.paragraph_format.first_line_indent = Pt(0)
            if level == 1:
                p.alignment = WD_ALIGN_PARAGRAPH.LEFT
            size_pt = float(heading_sizes.get(str(level), body_size))
            if inline_tok and inline_tok.children:
                _render_inline_tokens(
                    p, inline_tok.children, preset, preset["east_asia_heading_font"], size_pt, force_bold=True
                )
            idx += 3
            continue

        if t == "bullet_list_open":
            list_stack.append(("bullet", 0))
            idx += 1
            continue
        if t == "ordered_list_open":
            start_num = int(dict(tok.attrs or {}).get("start", 1))
            list_stack.append(("ordered", start_num - 1))
            idx += 1
            continue
        if t in ("bullet_list_close", "ordered_list_close"):
            if list_stack:
                list_stack.pop()
            idx += 1
            continue

        if t == "list_item_open":
            if list_stack:
                kind, count = list_stack[-1]
                list_stack[-1] = (kind, count + 1)
            idx += 1
            continue

        if t == "paragraph_open":
            inline_tok = tokens[idx + 1] if idx + 1 < len(tokens) else None
            raw_line = (inline_tok.content if inline_tok else "").strip()
            p = doc.add_paragraph(style="Normal")

            if list_stack:
                depth = len(list_stack)
                kind, number = list_stack[-1]
                prefix = f"{number}. " if kind == "ordered" else "• "
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.left_indent = Pt(18 * depth)
                prefix_run = p.add_run(prefix)
                _apply_run_font(prefix_run, preset["east_asia_body_font"], preset["western_font"], body_size, bold=True)
                if inline_tok and inline_tok.children:
                    _render_inline_tokens(p, inline_tok.children, preset, preset["east_asia_body_font"], body_size)
            elif _CAPTION_RE.match(raw_line):
                # 识别“表 X-X ...”题注段落，自动居中加粗并与下方表格保持同页不断行。
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.keep_with_next = raw_line.startswith("表")
                if inline_tok and inline_tok.children:
                    _render_inline_tokens(
                        p, inline_tok.children, preset, preset["east_asia_heading_font"], 10.5, force_bold=True
                    )
            else:
                p.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                p.paragraph_format.first_line_indent = Pt(body_size * indent_chars)
                if inline_tok and inline_tok.children:
                    _render_inline_tokens(
                        p,
                        inline_tok.children,
                        preset,
                        preset["east_asia_body_font"],
                        body_size,
                        workspace_dir=workspace_dir,
                        doc=doc,
                    )
            idx += 3
            continue

        if t == "fence" or t == "code_block":
            code_text = tok.content.rstrip("\n")
            if code_text:
                p = doc.add_paragraph(style="Normal")
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.left_indent = Pt(16)
                p.paragraph_format.line_spacing = Pt(15)
                run = p.add_run(code_text)
                _apply_run_font(run, preset["east_asia_body_font"], "Consolas", 9.5, color_hex="333333")
            idx += 1
            continue

        if t == "blockquote_open":
            # 引用块（如关键提示或结论框）采用缩进斜体或重点呈现
            idx += 1
            continue
        if t == "blockquote_close":
            idx += 1
            continue

        if t == "table_open":
            idx = _render_table_tokens(doc, tokens, idx, preset) + 1
            continue

        idx += 1


def compile_markdown_to_docx(
    chapter_files: list[Path],
    output_docx: Path,
    title: str,
    subtitle: str = "",
    preset: dict[str, Any] | None = None,
    workspace_dir: Path | None = None,
) -> dict[str, Any]:
    """
    按顺序读取多个章节 Markdown 文件（或单个完整 Markdown），合成带封面、自动目录域及规范样式的原生 Word 文档。
    返回输出文件路径、字节大小及章节统计摘要。
    """
    if not chapter_files:
        raise ValueError("至少需要提供一个非空的 Markdown 章节文件")

    merged_preset = {**DEFAULT_STYLE_PRESET, **(preset or {})}
    if not merged_preset.get("header_text"):
        merged_preset["header_text"] = title

    doc = Document()
    _configure_document_styles(doc, merged_preset)

    # 1. 可选生成简洁标准封面页
    if merged_preset.get("include_cover", True):
        for _ in range(4):
            doc.add_paragraph()
        title_p = doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_p.paragraph_format.first_line_indent = Pt(0)
        title_run = title_p.add_run(title)
        _apply_run_font(title_run, merged_preset["east_asia_heading_font"], merged_preset["western_font"], 24.0, bold=True)

        if subtitle.strip():
            sub_p = doc.add_paragraph()
            sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sub_p.paragraph_format.first_line_indent = Pt(0)
            sub_p.paragraph_format.space_before = Pt(18)
            sub_run = sub_p.add_run(subtitle.strip())
            _apply_run_font(sub_run, merged_preset["east_asia_body_font"], merged_preset["western_font"], 14.0)

        doc.add_page_break()

    # 2. 可选生成原生 Word 自动目录域（打开 Word 右键更新域即可刷新精确页码，同时附静态章节导览）
    if merged_preset.get("include_toc", True) and len(chapter_files) > 1:
        toc_title = doc.add_paragraph()
        toc_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        toc_title.paragraph_format.first_line_indent = Pt(0)
        toc_title.paragraph_format.space_after = Pt(12)
        toc_run = toc_title.add_run("目  录")
        _apply_run_font(toc_run, merged_preset["east_asia_heading_font"], merged_preset["western_font"], 16.0, bold=True)

        toc_field_p = doc.add_paragraph()
        toc_field_p.paragraph_format.first_line_indent = Pt(0)
        _add_xml_field(toc_field_p, ' TOC \\o "1-3" \\h \\z \\u ', "（在 Word 中全选按 F9 可自动刷新最新页码目录）")
        doc.add_page_break()

    # 3. 逐章解析 Markdown 并渲染为原生段落与表格，章与章之间自动分页
    total_chars = 0
    for idx, ch_path in enumerate(chapter_files):
        if idx > 0 and len(chapter_files) > 1:
            doc.add_page_break()
        text = ch_path.read_text(encoding="utf-8")
        total_chars += len(text)
        _render_markdown_chapter(doc, text, merged_preset, workspace_dir=workspace_dir)

    output_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_docx))

    return {
        "output_path": str(output_docx),
        "bytes": output_docx.stat().st_size,
        "chapter_count": len(chapter_files),
        "source_characters": total_chars,
        "table_count": len(doc.tables),
        "paragraph_count": len(doc.paragraphs),
    }
