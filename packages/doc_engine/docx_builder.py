"""原生 Word (.docx) 编译器（M4 深化版）：支持横竖版自适应分节、8列以上宽表自动横排、表格防跨页劈行、引用框及暗标纯黑白合规模式。"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION_START
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
    "monochrome": False,
    "auto_landscape_wide_tables": True,
    "wide_table_col_threshold": 8,
}

_CAPTION_RE = re.compile(r"^(?:表|图)\s*\d+(?:[-.]\d+)*\s+.+")
_LANDSCAPE_MARKER = "@@MAYI_PAGE_LANDSCAPE@@"
_PORTRAIT_MARKER = "@@MAYI_PAGE_PORTRAIT@@"


def _apply_run_font(
    run,
    east_asia_font: str,
    western_font: str,
    size_pt: float,
    bold: bool = False,
    italic: bool = False,
    color_hex: str = "000000",
    monochrome: bool = False,
) -> None:
    """统一设置 Word Run 的中西文字体、字号与色彩；暗标模式下强制锁定纯黑（#000000）。"""
    run.font.name = western_font
    r_pr = run._element.get_or_add_rPr()
    r_pr.get_or_add_rFonts().set(qn("w:eastAsia"), east_asia_font)
    run.font.size = Pt(size_pt)
    run.bold = bold
    run.italic = italic
    final_color = "000000" if monochrome else color_hex
    run.font.color.rgb = RGBColor.from_string(final_color)


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


def _apply_section_geometry(section, preset: dict[str, Any], landscape: bool = False) -> None:
    """配置单个 Word 节（Section）的纸张宽高方向与页边距，支持竖向与横向自由切换。"""
    base_w = float(preset["page_width_cm"])
    base_h = float(preset["page_height_cm"])
    if landscape:
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width = Cm(max(base_w, base_h))
        section.page_height = Cm(min(base_w, base_h))
        section.top_margin = Cm(float(preset["margin_left_cm"]))
        section.bottom_margin = Cm(float(preset["margin_right_cm"]))
        section.left_margin = Cm(float(preset["margin_top_cm"]))
        section.right_margin = Cm(float(preset["margin_bottom_cm"]))
    else:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Cm(min(base_w, base_h))
        section.page_height = Cm(max(base_w, base_h))
        section.top_margin = Cm(float(preset["margin_top_cm"]))
        section.bottom_margin = Cm(float(preset["margin_bottom_cm"]))
        section.left_margin = Cm(float(preset["margin_left_cm"]))
        section.right_margin = Cm(float(preset["margin_right_cm"]))


def _switch_orientation(doc: Document, preset: dict[str, Any], target_landscape: bool) -> bool:
    """若目标纸张方向与当前末尾节不同，则新建分页 Section 并保持页眉页脚继承连贯。"""
    curr = doc.sections[-1]
    is_curr_landscape = curr.page_width > curr.page_height
    if is_curr_landscape == target_landscape:
        return False
    new_sec = doc.add_section(WD_SECTION_START.NEW_PAGE)
    _apply_section_geometry(new_sec, preset, landscape=target_landscape)
    new_sec.header.is_linked_to_previous = True
    new_sec.footer.is_linked_to_previous = True
    return True


def _configure_document_styles(doc: Document, preset: dict[str, Any]) -> None:
    """初始化首节纸张边距、页眉页脚（暗标模式下自动清空页眉标识）及正文与各级标题样式。"""
    monochrome = bool(preset.get("monochrome", False))
    section = doc.sections[0]
    _apply_section_geometry(section, preset, landscape=False)

    header_text = "" if monochrome else str(preset.get("header_text") or "").strip()
    if header_text:
        hp = section.header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        hr = hp.add_run(header_text)
        _apply_run_font(
            hr, preset["east_asia_body_font"], preset["western_font"], 9.5, color_hex="666666", monochrome=monochrome
        )

    if preset.get("include_page_numbers", True):
        fp = section.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r_pre = fp.add_run("" if monochrome else "- ")
        _apply_run_font(r_pre, preset["east_asia_body_font"], preset["western_font"], 9.5, color_hex="555555", monochrome=monochrome)
        _add_xml_field(fp, " PAGE ", "1")
        if not monochrome:
            r_post = fp.add_run(" -")
            _apply_run_font(r_post, preset["east_asia_body_font"], preset["western_font"], 9.5, color_hex="555555", monochrome=monochrome)

    normal = doc.styles["Normal"]
    normal.font.name = preset["western_font"]
    normal._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), preset["east_asia_body_font"])
    normal.font.size = Pt(float(preset["body_size_pt"]))
    normal.font.color.rgb = RGBColor.from_string("000000")
    normal.paragraph_format.line_spacing = Pt(float(preset["line_spacing_pt"]))
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(0 if monochrome else 4)
    normal.paragraph_format.widow_control = True

    heading_sizes = preset.get("heading_sizes_pt", DEFAULT_STYLE_PRESET["heading_sizes_pt"])
    for level in range(1, 5):
        style = doc.styles[f"Heading {level}"]
        size_pt = float(heading_sizes.get(str(level), 12.0))
        style.font.name = preset["western_font"]
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), preset["east_asia_heading_font"])
        style.font.size = Pt(size_pt)
        style.font.bold = not bool(preset.get("plain_headings", False))
        style.font.color.rgb = RGBColor.from_string("000000")
        pf = style.paragraph_format
        pf.first_line_indent = Pt(0)
        pf.space_before = Pt(8 if level == 1 else 6)
        pf.space_after = Pt(6 if level == 1 else 3)
        pf.line_spacing = Pt(max(float(preset["line_spacing_pt"]), size_pt * 1.35))
        pf.keep_with_next = True


def _usable_section_width_cm(doc: Document) -> float:
    """根据当前末尾 Section 的实际方向（竖版或横版）动态计算版心可用宽度（厘米）。"""
    sec = doc.sections[-1]
    usable_emu = sec.page_width - sec.left_margin - sec.right_margin
    return max(10.0, float(usable_emu) / 360000.0)


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
    """渲染行内富文本或独立图片块；横版页面插入图表时自动放大利用横版全宽。"""
    monochrome = bool(preset.get("monochrome", False))
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
            paragraph.paragraph_format.keep_with_next = bool(caption_text.strip())
            usable_cm = _usable_section_width_cm(doc)
            max_img_cm = min(23.2, usable_cm * 0.94) if usable_cm > 19.0 else min(15.0, usable_cm * 0.94)
            paragraph.add_run().add_picture(str(candidate), width=Cm(max_img_cm))
            if caption_text.strip():
                cap_p = doc.add_paragraph()
                cap_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                cap_p.paragraph_format.first_line_indent = Pt(0)
                cap_p.paragraph_format.space_after = Pt(6)
                label = caption_text.strip()
                if not label.startswith("图"):
                    label = f"图 {label}"
                cap_run = cap_p.add_run(label)
                _apply_run_font(
                    cap_run,
                    preset["east_asia_heading_font"],
                    preset["western_font"],
                    10.5,
                    bold=True,
                    monochrome=monochrome,
                )
            return True

    bold_depth = 1 if force_bold else 0
    italic_depth = 0
    default_color = "000000" if monochrome else "1F2329"

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
        elif t in ("softbreak", "hardbreak"):
            paragraph.add_run("\n")
        elif t == "code_inline":
            run = paragraph.add_run(token.content)
            _apply_run_font(
                run,
                preset["east_asia_body_font"],
                preset["western_font"] if monochrome else "Consolas",
                size_pt - 0.5,
                bold=bold_depth > 0,
                color_hex=default_color,
                monochrome=monochrome,
            )
        elif t == "text" and token.content:
            run = paragraph.add_run(token.content)
            _apply_run_font(
                run,
                base_font,
                preset["western_font"],
                size_pt,
                bold=bold_depth > 0,
                italic=italic_depth > 0,
                color_hex=default_color,
                monochrome=monochrome,
            )
    return False


def _set_table_borders(table, border_color: str) -> None:
    """设置原生 Word 表格单线全边框。"""
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


def _apply_blockquote_border(paragraph, color_hex: str = "2563EB") -> None:
    """为非暗标模式下的重点提示块（blockquote）添加美观的左侧竖线装饰框。"""
    p_pr = paragraph._p.get_or_add_pPr()
    p_bdr = OxmlElement("w:pBdr")
    left = OxmlElement("w:left")
    left.set(qn("w:val"), "single")
    left.set(qn("w:sz"), "18")
    left.set(qn("w:space"), "8")
    left.set(qn("w:color"), color_hex)
    p_bdr.append(left)
    p_pr.append(p_bdr)


def _render_table_tokens(doc: Document, tokens: list[Any], start_idx: int, preset: dict[str, Any]) -> int:
    """
    渲染原生表格：
    1. 若列数达到宽表阈值且当前为竖版，自动临时切换横向 A4 分节并在表后切回竖向；
    2. 注入 `w:tblHeader`（跨页重复表头）与 `w:cantSplit`（禁止单行跨页撕裂）；
    3. 暗标模式下自动禁用彩色底纹，保持纯黑白合规。
    """
    monochrome = bool(preset.get("monochrome", False))
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
    wide_threshold = int(preset.get("wide_table_col_threshold", 8))
    auto_switched = False
    if preset.get("auto_landscape_wide_tables", True) and col_count >= wide_threshold:
        auto_switched = _switch_orientation(doc, preset, target_landscape=True)

    table = doc.add_table(rows=0, cols=col_count)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    border_col = "000000" if monochrome else str(preset.get("table_border_color", "666666"))
    _set_table_borders(table, border_col)

    col_weights = [3.5] * col_count
    for _, row_cells in rows_tokens[:35]:
        for c_i, children in enumerate(row_cells):
            text_len = sum(len(getattr(ch, "content", "") or "") for ch in children)
            col_weights[c_i] = max(col_weights[c_i], min(float(text_len + 2), 34.0))
    weight_sum = sum(col_weights) or 1.0

    section = doc.sections[-1]
    usable_width = section.page_width - section.left_margin - section.right_margin
    col_widths = [int(usable_width * (w / weight_sum)) for w in col_weights]
    for col, w_val in zip(table.columns, col_widths):
        col.width = w_val

    base_tbl_font = float(preset.get("table_font_size_pt", 10.5))
    table_font_size = min(base_tbl_font, 9.5) if col_count >= 9 else base_tbl_font
    header_fill = "" if monochrome else str(preset.get("table_header_fill", "EFEFEF"))

    for row_index, (is_header, row_cells) in enumerate(rows_tokens):
        row = table.add_row()
        tr_pr = row._tr.get_or_add_trPr()
        # 禁止单行在分页处被上下劈成两半
        tr_pr.append(OxmlElement("w:cantSplit"))
        if is_header or row_index == 0:
            tr_pr.append(OxmlElement("w:tblHeader"))

        padded_cells = row_cells + [[] for _ in range(col_count - len(row_cells))]
        for cell, children, w_val in zip(row.cells, padded_cells, col_widths):
            cell.width = w_val
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_mar = OxmlElement("w:tcMar")
            pad_h = "70" if col_count >= 8 else "105"
            for side, dxa in (("top", "70"), ("bottom", "70"), ("left", pad_h), ("right", pad_h)):
                m = OxmlElement(f"w:{side}")
                m.set(qn("w:w"), dxa)
                m.set(qn("w:type"), "dxa")
                tc_mar.append(m)
            tc_pr.append(tc_mar)

            p = cell.paragraphs[0]
            p.paragraph_format.first_line_indent = Pt(0)
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.line_spacing = Pt(table_font_size * 1.3)

            if is_header or row_index == 0:
                if header_fill:
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

    if auto_switched:
        _switch_orientation(doc, preset, target_landscape=False)
    else:
        spacer = doc.add_paragraph()
        spacer.paragraph_format.space_before = Pt(0)
        spacer.paragraph_format.space_after = Pt(2)
        spacer.paragraph_format.line_spacing = Pt(4)
    return idx


def _preprocess_layout_directives(md_text: str) -> str:
    """先提取保留 `<!-- page: landscape -->` / `<!-- page: portrait -->` 版面指令，再清理普通 HTML 注释。"""
    step1 = re.sub(
        r"<!--\s*page:\s*landscape\s*-->",
        f"\n\n{_LANDSCAPE_MARKER}\n\n",
        md_text,
        flags=re.IGNORECASE,
    )
    step2 = re.sub(
        r"<!--\s*page:\s*portrait\s*-->",
        f"\n\n{_PORTRAIT_MARKER}\n\n",
        step1,
        flags=re.IGNORECASE,
    )
    return re.sub(r"<!--.*?-->", "", step2, flags=re.DOTALL).strip()


def _render_markdown_chapter(
    doc: Document,
    md_text: str,
    preset: dict[str, Any],
    workspace_dir: Path | None = None,
) -> None:
    """将单章受控 Markdown（含横竖分节指令、标题、列表、引用框、插图与宽表）编译写入目标 Word 文档。"""
    monochrome = bool(preset.get("monochrome", False))
    md = MarkdownIt("commonmark").enable("table")
    cleaned = _preprocess_layout_directives(md_text)
    tokens = md.parse(cleaned)

    body_size = float(preset["body_size_pt"])
    indent_chars = int(preset.get("first_line_indent_chars", 2))
    heading_sizes = preset.get("heading_sizes_pt", DEFAULT_STYLE_PRESET["heading_sizes_pt"])

    list_stack: list[tuple[str, int]] = []
    in_blockquote = False
    idx = 0

    while idx < len(tokens):
        tok = tokens[idx]
        t = tok.type

        if t == "hr":
            doc.add_page_break()
            idx += 1
            continue

        if t == "heading_open":
            level = min(4, max(1, int(tok.tag[1])))
            inline_tok = tokens[idx + 1] if idx + 1 < len(tokens) else None
            p = doc.add_paragraph(style=f"Heading {level}")
            p.paragraph_format.first_line_indent = Pt(0)
            p.alignment = (
                WD_ALIGN_PARAGRAPH.CENTER
                if (level == 1 and preset.get("center_chapter_title", False))
                else WD_ALIGN_PARAGRAPH.LEFT
            )
            size_pt = float(heading_sizes.get(str(level), body_size))
            if inline_tok and inline_tok.children:
                _render_inline_tokens(
                    p,
                    inline_tok.children,
                    preset,
                    preset["east_asia_heading_font"],
                    size_pt,
                    force_bold=not bool(preset.get("plain_headings", False)),
                )
            idx += 3
            continue

        if t == "blockquote_open":
            in_blockquote = True
            idx += 1
            continue
        if t == "blockquote_close":
            in_blockquote = False
            idx += 1
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

            if raw_line == _LANDSCAPE_MARKER:
                _switch_orientation(doc, preset, target_landscape=True)
                idx += 3
                continue
            if raw_line == _PORTRAIT_MARKER:
                _switch_orientation(doc, preset, target_landscape=False)
                idx += 3
                continue

            p = doc.add_paragraph(style="Normal")

            if list_stack:
                depth = len(list_stack)
                kind, number = list_stack[-1]
                prefix = f"{number}. " if kind == "ordered" else "• "
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.left_indent = Pt(18 * depth)
                prefix_run = p.add_run(prefix)
                _apply_run_font(
                    prefix_run, preset["east_asia_body_font"], preset["western_font"], body_size, bold=True, monochrome=monochrome
                )
                if inline_tok and inline_tok.children:
                    _render_inline_tokens(p, inline_tok.children, preset, preset["east_asia_body_font"], body_size)
            elif in_blockquote:
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.left_indent = Pt(16)
                p.paragraph_format.right_indent = Pt(12)
                if not monochrome:
                    _apply_blockquote_border(p)
                if inline_tok and inline_tok.children:
                    _render_inline_tokens(p, inline_tok.children, preset, preset["east_asia_body_font"], body_size - 0.5)
            elif _CAPTION_RE.match(raw_line):
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

        if t in ("fence", "code_block"):
            code_text = tok.content.rstrip("\n")
            if code_text:
                p = doc.add_paragraph(style="Normal")
                p.paragraph_format.first_line_indent = Pt(0)
                p.paragraph_format.left_indent = Pt(16)
                p.paragraph_format.line_spacing = Pt(15)
                run = p.add_run(code_text)
                _apply_run_font(
                    run,
                    preset["east_asia_body_font"],
                    preset["western_font"] if monochrome else "Consolas",
                    9.5,
                    color_hex="333333",
                    monochrome=monochrome,
                )
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
    按顺序读取多个章节 Markdown 文件，合成带可选封面、自动目录域、横竖自适应分节及规范样式的原生 Word 文档。
    若启用暗标预设（`monochrome: true`），自动关闭页眉标识与彩色装饰，确保合规安全。
    """
    if not chapter_files:
        raise ValueError("至少需要提供一个非空的 Markdown 章节文件")

    merged_preset = {**DEFAULT_STYLE_PRESET, **(preset or {})}
    monochrome = bool(merged_preset.get("monochrome", False))
    if not monochrome and not merged_preset.get("header_text"):
        merged_preset["header_text"] = title

    doc = Document()
    _configure_document_styles(doc, merged_preset)

    if merged_preset.get("include_cover", True):
        for _ in range(4):
            doc.add_paragraph()
        title_p = doc.add_paragraph()
        title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        title_p.paragraph_format.first_line_indent = Pt(0)
        title_run = title_p.add_run(title)
        _apply_run_font(
            title_run,
            merged_preset["east_asia_heading_font"],
            merged_preset["western_font"],
            22.0 if monochrome else 24.0,
            bold=True,
            monochrome=monochrome,
        )

        if subtitle.strip() and not monochrome:
            sub_p = doc.add_paragraph()
            sub_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            sub_p.paragraph_format.first_line_indent = Pt(0)
            sub_p.paragraph_format.space_before = Pt(18)
            sub_run = sub_p.add_run(subtitle.strip())
            _apply_run_font(sub_run, merged_preset["east_asia_body_font"], merged_preset["western_font"], 14.0)

        doc.add_page_break()

    if merged_preset.get("include_toc", True) and len(chapter_files) > 1:
        toc_title = doc.add_paragraph()
        toc_title.alignment = WD_ALIGN_PARAGRAPH.CENTER
        toc_title.paragraph_format.first_line_indent = Pt(0)
        toc_title.paragraph_format.space_after = Pt(12)
        toc_run = toc_title.add_run("目  录")
        _apply_run_font(
            toc_run, merged_preset["east_asia_heading_font"], merged_preset["western_font"], 16.0, bold=True, monochrome=monochrome
        )

        toc_field_p = doc.add_paragraph()
        toc_field_p.paragraph_format.first_line_indent = Pt(0)
        _add_xml_field(toc_field_p, ' TOC \\o "1-3" \\h \\z \\u ', "（打开 Word 后全选按 F9 即可自动生成精确页码目录）")
        doc.add_page_break()

    total_chars = 0
    for idx, ch_path in enumerate(chapter_files):
        if idx > 0 and len(chapter_files) > 1:
            # 确保新章节起始默认回到标准竖向页面（除非该章自身显式声明 landscape）
            if not _switch_orientation(doc, merged_preset, target_landscape=False):
                doc.add_page_break()
        text = ch_path.read_text(encoding="utf-8")
        total_chars += len(text)
        _render_markdown_chapter(doc, text, merged_preset, workspace_dir=workspace_dir)

    output_docx.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_docx))

    landscape_sections = sum(1 for s in doc.sections if s.page_width > s.page_height)
    return {
        "output_path": str(output_docx),
        "bytes": output_docx.stat().st_size,
        "chapter_count": len(chapter_files),
        "source_characters": total_chars,
        "section_count": len(doc.sections),
        "landscape_section_count": landscape_sections,
        "table_count": len(doc.tables),
        "paragraph_count": len(doc.paragraphs),
    }
