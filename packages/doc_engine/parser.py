"""生产级离线文档批量标准化引擎：支持 PDF(多进程+图文混排OCR+目录提取)、DOCX/DOC、XLSX/XLS(合并单元格还原)、XML/电子标书容器自省与内容寻址缓存。"""
from __future__ import annotations

import argparse
import base64
import concurrent.futures
import csv
import datetime
import hashlib
import io
import json
import math
import os
import queue
import re
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable

PARSER_VERSION = "mayi-plus-parser-v2"
MAX_FILE_BYTES = 120 * 1024 * 1024
MAX_ZIP_ENTRIES = 6000
MAX_UNPACKED_BYTES = 350 * 1024 * 1024
MAX_PDF_PAGES = 1500
MAX_RENDER_PIXELS = 1_600_000
MAX_RENDER_SIDE = 1400
MAX_SHEET_ROWS_IN_MD = 600
DEFAULT_MAX_AUTO_OCR_PAGES_PER_PDF = 6
DEFAULT_MAX_BATCH_OCR_PAGES = 15

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

PARSEABLE_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
    ".xml", ".hfzf", ".18zhzb",
}

CATALOG_ONLY_EXTENSIONS = {
    ".dwg", ".dxf", ".rvt", ".zip", ".rar", ".7z", ".png", ".jpg", ".jpeg",
}

TEMP_LOCK_SUFFIXES = {".dwl", ".dwl2", ".tmp", ".bak", ".lnk"}
HEADING_LINE_RE = re.compile(
    r"^(?:第[一二三四五六七八九十百零〇\d]+[章节篇部分]\s*.+|"
    r"[一二三四五六七八九十]+[、.]\s*\S+.+|"
    r"\d+\.\d+(?:\.\d+)?\s+\S+.+)$"
)

_SINGLE_OCR: Any = None
_OCR_LOCK = threading.Lock()
_OCR_UNAVAILABLE = False
# PDFium 底层 C 库与 Windows ONNXRuntime OpenMP 线程池均通过独立互斥锁保护，彻底根除多线程并发死锁。
_PDFIUM_LOCK = threading.Lock()


class _BatchOcrBudget:
    """控制单次整库摄入的最大自动 OCR 页数预算，防止上百张扫描图纸阻塞首轮全库建档。"""

    def __init__(self, limit: int):
        self.limit = limit
        self.used = 0
        self._lock = threading.Lock()

    def try_consume(self) -> bool:
        if self.limit < 0:
            return True
        with self._lock:
            if self.used >= self.limit:
                return False
            self.used += 1
            return True


def _sha256_bytes(data: bytes) -> str:
    """计算原始字节流的 SHA-256 摘要，作为内容寻址缓存的唯一版本指纹。"""
    return hashlib.sha256(data).hexdigest()


def _safe_cache_slug(rel_source: str, file_hash: str) -> str:
    """结合相对父路径语义与内容哈希生成唯一且可读的缓存目录名，避免不同目录下同名文件互相挤占。"""
    p = Path(rel_source)
    parts = [*p.parent.parts[-2:], p.stem] if str(p.parent) != "." else [p.stem]
    joined = "_".join(parts)
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", joined).strip("_")
    if len(cleaned) > 56:
        cleaned = cleaned[-56:].lstrip("_")
    return f"{cleaned or 'doc'}-{file_hash[:10]}"


def _format_scalar(value: Any) -> str:
    """统一格式化单元格标量，消除浮点数二进制尾差噪声（如 3.5700000000000003）与空值。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat(sep=" ") if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, float):
        if not math.isfinite(value):
            return ""
        return str(int(value)) if value.is_integer() else format(value, ".12g")
    return str(value).replace("\x00", "").strip()


def _escape_md_cell(value: Any) -> str:
    """转义表格单元格中的竖线和换行符，确保生成的 GFM 管道表格列数严格对齐。"""
    text = _format_scalar(value)
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\r", "<br>").replace("\n", "<br>")


def _rows_to_markdown_table(rows: list[list[Any]]) -> str:
    """将二维矩阵渲染为干净的 Markdown 表格，自动裁剪尾部全空列与全空行。"""
    cleaned = [[_escape_md_cell(c) for c in row] for row in rows]
    cleaned = [r for r in cleaned if any(cell != "" for cell in r)]
    if not cleaned:
        return ""

    max_used_col = 0
    for r in cleaned:
        for idx in range(len(r) - 1, -1, -1):
            if r[idx] != "":
                max_used_col = max(max_used_col, idx + 1)
                break
    if max_used_col == 0:
        return ""

    normalized = [(r[:max_used_col] + [""] * max(0, max_used_col - len(r))) for r in cleaned]
    lines = [
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join(["---"] * max_used_col) + " |",
    ]
    for r in normalized[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _decode_text_bytes(data: bytes) -> str:
    """按 UTF-8 / GB18030 顺序自适应解码中文文本。"""
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _validate_zip_container(zf: zipfile.ZipFile) -> None:
    """校验 ZIP 容器条目上限、路径穿越与加密标记。"""
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(f"压缩条目超出安全上限 ({len(infos)} > {MAX_ZIP_ENTRIES})")
    total = 0
    for info in infos:
        if ".." in Path(info.filename).parts or info.filename.startswith(("/", "\\")):
            raise ValueError("检测到非法路径穿越条目")
        if info.flag_bits & 0x1:
            raise ValueError("文档受密码加密保护，无法离线解析")
        total += info.file_size
        if total > MAX_UNPACKED_BYTES:
            raise ValueError("展开体积超出安全上限")


def is_ignored_temp_file(path: Path) -> bool:
    """识别并跳过 Office/CAD 临时锁文件（如 `~$*.docx`、`.dwl`）及系统隐藏杂项文件。"""
    name = path.name
    if name.startswith("~$") or name.startswith(".~") or name in {".DS_Store", "Thumbs.db", "desktop.ini"}:
        return True
    return path.suffix.lower() in TEMP_LOCK_SUFFIXES


def _ocr_pil_image(pil_img) -> list[str]:
    """在 `_OCR_LOCK` 保护下调用单实例多核 RapidOCR 推理，避免 Windows OpenMP 线程池死锁并按行序重组文本。"""
    global _SINGLE_OCR, _OCR_UNAVAILABLE
    if _OCR_UNAVAILABLE:
        return []
    import numpy as np

    rgb = pil_img.convert("RGB")
    arr = np.asarray(rgb)[:, :, ::-1].copy()

    with _OCR_LOCK:
        if _SINGLE_OCR is None and not _OCR_UNAVAILABLE:
            try:
                import cv2
                from rapidocr_onnxruntime import RapidOCR

                cv2.setNumThreads(2)
                _SINGLE_OCR = RapidOCR(
                    use_cls=False,
                    intra_op_num_threads=max(4, min(8, os.cpu_count() or 4)),
                    inter_op_num_threads=1,
                    rec_batch_num=8,
                    det_use_cuda=False,
                    cls_use_cuda=False,
                    rec_use_cuda=False,
                    print_verbose=False,
                    det_limit_side_len=960,
                )
            except Exception:
                _OCR_UNAVAILABLE = True
                return []
        if _SINGLE_OCR is None:
            return []
        raw_res, _ = _SINGLE_OCR(arr)

    if not raw_res:
        return []

    items = []
    for row in raw_res:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        box, txt = row[0], str(row[1]).strip()
        if not txt:
            continue
        try:
            top = min(float(pt[1]) for pt in box)
            left = min(float(pt[0]) for pt in box)
            height = max(1.0, max(float(pt[1]) for pt in box) - top)
            items.append((top, left, height, txt))
        except Exception:
            items.append((0.0, 0.0, 10.0, txt))

    items.sort(key=lambda x: (x[0], x[1]))
    bands: list[list[Any]] = []
    for top, left, height, txt in items:
        if not bands or abs(top - bands[-1][0]) > 0.45 * min(height, bands[-1][1]):
            bands.append([top, height, [(left, txt)]])
        else:
            bands[-1][2].append((left, txt))

    ordered_lines: list[str] = []
    for _b_top, _b_h, cells in bands:
        cells.sort(key=lambda c: c[0])
        ordered_lines.append("  ".join(c[1] for c in cells))
    return ordered_lines


def _build_toc_markdown(doc_title: str, page_texts: list[tuple[int, str]]) -> list[dict[str, Any]]:
    """扫描各页标题样式行，为长文档自动生成章节页码导航索引，帮助模型秒级定位关键章节所在页。"""
    toc_entries: list[dict[str, Any]] = []
    seen_titles: set[str] = set()

    for page_no, text in page_texts:
        for raw_line in text.splitlines()[:25]:
            line = re.sub(r"\s+", " ", raw_line).strip()
            if len(line) < 4 or len(line) > 72 or ".." in line or "……" in line:
                continue
            if line.startswith("#") or HEADING_LINE_RE.match(line):
                clean_title = line.lstrip("#").strip()
                if clean_title and clean_title not in seen_titles and clean_title != doc_title:
                    seen_titles.add(clean_title)
                    toc_entries.append({"page": page_no, "title": clean_title})
                    if len(toc_entries) >= 120:
                        return toc_entries
    return toc_entries


def _extract_pdf_to_markdown(
    data: bytes,
    doc_name: str,
    doc_cache_dir: Path,
    enable_ocr: bool = True,
    max_ocr_pages: int = DEFAULT_MAX_AUTO_OCR_PAGES_PER_PDF,
    batch_budget: _BatchOcrBudget | None = None,
) -> tuple[str, dict[str, Any]]:
    """
    提取 PDF 全文：
    1. 在 `_PDFIUM_LOCK` 保护下毫秒级提取各页原生矢量文本并检测页面是否含扫描位图；
    2. 结合单文档上限 `max_ocr_pages` 与批次总预算 `batch_budget` 自动 OCR 高优先级扫描页，其余扫描页标注可用 `ocr_pdf_pages` 按需精准补识；
    3. 自动为 >=10 页长文档构建 `toc.md` 章节页码导航。
    """
    import pypdfium2 as pdfium

    pending_pages: list[tuple[int, str, int, Any, bool]] = []
    deferred_ocr_pages: list[int] = []
    scheduled_ocr = 0

    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        try:
            page_count = len(doc)
            if page_count < 1 or page_count > MAX_PDF_PAGES:
                raise ValueError(f"PDF 页数超出范围: {page_count}")

            for idx in range(page_count):
                page_no = idx + 1
                page = doc[idx]
                try:
                    tp = page.get_textpage()
                    try:
                        native_text = (tp.get_text_range() or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                    finally:
                        tp.close()

                    img_pixels = 0
                    try:
                        for obj in page.get_objects(filter=[pdfium.raw.FPDF_PAGEOBJ_IMAGE]):
                            w_px, h_px = obj.get_px_size()
                            if w_px > 0 and h_px > 0:
                                img_pixels += w_px * h_px
                    except Exception:
                        img_pixels = 0

                    compact_len = len(re.sub(r"\s+", "", native_text))
                    is_scan_candidate = enable_ocr and (
                        (img_pixels >= 80_000 and compact_len < 40)
                        or (compact_len == 0 and img_pixels > 0)
                    )

                    pil_img = None
                    was_deferred = False
                    if is_scan_candidate:
                        within_doc = max_ocr_pages < 0 or scheduled_ocr < max_ocr_pages
                        within_batch = within_doc and (batch_budget is None or batch_budget.try_consume())
                        if not within_batch:
                            was_deferred = True
                            deferred_ocr_pages.append(page_no)
                        else:
                            w_pt, h_pt = page.get_size()
                            if w_pt > 0 and h_pt > 0:
                                scale = min(
                                    1.4,
                                    MAX_RENDER_SIDE / max(w_pt, h_pt),
                                    math.sqrt(MAX_RENDER_PIXELS / (w_pt * h_pt)),
                                )
                                bitmap = page.render(scale=max(0.75, scale))
                                try:
                                    cand_img = bitmap.to_pil()
                                    if cand_img.convert("L").getextrema() != (255, 255):
                                        pil_img = cand_img
                                        scheduled_ocr += 1
                                finally:
                                    bitmap.close()

                    pending_pages.append((page_no, native_text, compact_len, pil_img, was_deferred))
                finally:
                    page.close()
        finally:
            doc.close()

    page_results: list[tuple[int, str, bool, bytes | None, bool]] = []
    for page_no, native_text, compact_len, pil_img, was_deferred in pending_pages:
        if pil_img is None:
            page_results.append((page_no, native_text, False, None, was_deferred))
            continue
        ocr_lines = _ocr_pil_image(pil_img)
        ocr_text = "\n".join(ocr_lines).strip()
        if not ocr_text:
            page_results.append((page_no, native_text, False, None, False))
            continue
        buf = io.BytesIO()
        pil_img.save(buf, format="PNG")
        if compact_len < 24 or len(ocr_text) > len(native_text) * 1.2:
            merged_text = ocr_text
        else:
            merged_text = f"{native_text}\n\n[页内附图识别补充]\n{ocr_text}"
        page_results.append((page_no, merged_text, True, buf.getvalue(), False))

    images_dir = doc_cache_dir / "images"
    sections: list[str] = []
    ocr_pages: list[int] = []
    page_texts_for_toc: list[tuple[int, str]] = []

    for page_no, text, used_ocr, png_bytes, was_deferred in page_results:
        if used_ocr:
            ocr_pages.append(page_no)
            if png_bytes:
                images_dir.mkdir(parents=True, exist_ok=True)
                (images_dir / f"page-{page_no:04d}.png").write_bytes(png_bytes)

        header = f"<!-- page: {page_no} -->"
        if used_ocr:
            header += f"\n> [OCR识别页：第 {page_no} 页，快照 `images/page-{page_no:04d}.png`]"
        if was_deferred and not text:
            body = f"[扫描图像页（首轮已自动识别前 {max_ocr_pages} 个扫描页，如需识别本页请调用 ocr_pdf_pages 指定第 {page_no} 页）]"
        else:
            body = text if text else "[空白页或纯图形页]"
        sections.append(f"{header}\n{body}")
        page_texts_for_toc.append((page_no, body))

    warnings: list[str] = []
    if ocr_pages:
        warnings.append(f"已自动 OCR 识别 {len(ocr_pages)} 页")
    if deferred_ocr_pages:
        warnings.append(
            f"另有 {len(deferred_ocr_pages)} 个扫描/图像页（P{deferred_ocr_pages[0]}~P{deferred_ocr_pages[-1]}）按需待识，必要时可调 `ocr_pdf_pages` 补识"
        )

    extra: dict[str, Any] = {
        "unit_kind": "pages",
        "unit_count": page_count,
        "ocr_pages": ocr_pages,
        "deferred_ocr_pages": deferred_ocr_pages,
        "warnings": warnings,
    }

    if page_count >= 10:
        toc_items = _build_toc_markdown(Path(doc_name).stem, page_texts_for_toc)
        if toc_items:
            toc_lines = [f"# 文档章节页码导航：{doc_name}（共 {page_count} 页）\n"]
            for item in toc_items:
                toc_lines.append(f"- [P{item['page']}] {item['title']}")
            toc_rel = f".workspace/parsed/{doc_cache_dir.name}/toc.md"
            (doc_cache_dir / "toc.md").write_text("\n".join(toc_lines) + "\n", encoding="utf-8")
            extra["toc_markdown"] = toc_rel
            extra["toc_entries_count"] = len(toc_items)

    return "\n\n".join(sections), extra


def _parse_docx_table_with_merges(tbl_el: ET.Element) -> str:
    """解析 DOCX 表格并处理横向 `w:gridSpan` 与纵向 `w:vMerge` 合并单元格，防止多级表头列错位。"""
    matrix: list[list[str]] = []
    vmerge_memory: dict[int, str] = {}

    for tr in tbl_el.findall(f"./{W_NS}tr"):
        row_vals: list[str] = []
        col_cursor = 0
        for tc in tr.findall(f"./{W_NS}tc"):
            tc_pr = tc.find(f"./{W_NS}tcPr")
            span = 1
            vmerge_state: str | None = None
            if tc_pr is not None:
                gs = tc_pr.find(f"./{W_NS}gridSpan")
                if gs is not None:
                    try:
                        span = max(1, int(gs.get(f"{W_NS}val") or "1"))
                    except ValueError:
                        span = 1
                vm = tc_pr.find(f"./{W_NS}vMerge")
                if vm is not None:
                    vmerge_state = vm.get(f"{W_NS}val") or "continue"

            paras = []
            for p in tc.findall(f"./{W_NS}p"):
                p_txt = "".join(t.text for t in p.iter(f"{W_NS}t") if t.text).strip()
                if p_txt:
                    paras.append(p_txt)
            cell_text = "\n".join(paras)

            if vmerge_state == "continue" and not cell_text:
                cell_text = vmerge_memory.get(col_cursor, "")
            else:
                for offset in range(span):
                    vmerge_memory[col_cursor + offset] = cell_text

            for _ in range(span):
                row_vals.append(cell_text)
                col_cursor += 1
        matrix.append(row_vals)

    return _rows_to_markdown_table(matrix)


def _extract_docx_to_markdown(data: bytes, enable_ocr: bool = True) -> tuple[str, dict[str, Any]]:
    """解析 DOCX 页眉页脚、正文标题层级、含合并单元格表格，并对尺寸显著的内嵌图片补充 OCR。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _validate_zip_container(zf)
        names = set(zf.namelist())
        if "word/document.xml" not in names:
            raise ValueError("DOCX 缺少 word/document.xml")

        blocks: list[str] = []
        warnings: list[str] = []

        # 1. 提取去重后的页眉/页脚文本（常包含项目编号、版本或编制单位）
        header_notes: list[str] = []
        for hf_name in sorted(n for n in names if re.fullmatch(r"word/(?:header|footer)\d*\.xml", n)):
            try:
                hf_root = ET.fromstring(zf.read(hf_name))
                hf_txt = " ".join(t.text.strip() for t in hf_root.iter(f"{W_NS}t") if t.text and t.text.strip())
                if hf_txt and hf_txt not in header_notes:
                    header_notes.append(hf_txt)
            except Exception:
                continue
        if header_notes:
            blocks.append("> [页眉/页脚信息] " + " | ".join(header_notes[:4]))

        # 2. 按顺序解析正文段落与表格
        root = ET.fromstring(zf.read("word/document.xml"))
        body = root.find(f"{W_NS}body")
        table_count = 0
        if body is not None:
            for child in body:
                if child.tag == f"{W_NS}p":
                    line = "".join(node.text for node in child.iter(f"{W_NS}t") if node.text).strip()
                    if not line:
                        continue
                    style_el = child.find(f"./{W_NS}pPr/{W_NS}pStyle")
                    style_val = (style_el.get(f"{W_NS}val") or "") if style_el is not None else ""
                    m = re.search(r"(?:Heading|标题)\s*([1-4])", style_val, re.IGNORECASE)
                    if m:
                        blocks.append(f"{'#' * int(m.group(1))} {line}")
                    else:
                        blocks.append(line)
                elif child.tag == f"{W_NS}tbl":
                    md_tbl = _parse_docx_table_with_merges(child)
                    if md_tbl:
                        table_count += 1
                        blocks.append(md_tbl)

        # 3. 仅当正文文字极少（如扫描图粘贴型 Word）时，对前 5 张大尺寸内嵌图补充 OCR，避免对地勘钻孔照片空跑耗时
        ocr_media_count = 0
        body_char_len = sum(len(b) for b in blocks)
        if enable_ocr and body_char_len < 600:
            from PIL import Image

            media_files = sorted(n for n in names if n.startswith("word/media/") and not n.endswith("/"))
            for m_name in media_files[:5]:
                try:
                    raw_img = zf.read(m_name)
                    with Image.open(io.BytesIO(raw_img)) as im:
                        if im.width * im.height < 120_000:
                            continue
                        lines = _ocr_pil_image(im)
                        if lines:
                            ocr_media_count += 1
                            blocks.append(f"### [文档内嵌图识别：{Path(m_name).name}]\n" + "\n".join(lines))
                except Exception:
                    continue
        if ocr_media_count:
            warnings.append(f"已对 {ocr_media_count} 张文档内嵌图片补充 OCR 识别")

    return "\n\n".join(blocks), {
        "unit_kind": "sections",
        "unit_count": len(blocks),
        "table_count": table_count,
        "warnings": warnings,
    }


def _extract_doc_to_markdown(data: bytes) -> tuple[str, dict[str, Any]]:
    """通过字节流提取旧版 Word 97-2003 (.doc) 文本并清理控制字符。"""
    from legacy_doc import ExtractionOptions, extract_text

    res = extract_text(
        data,
        options=ExtractionOptions(
            max_file_bytes=MAX_FILE_BYTES,
            max_text_bytes=25 * 1024 * 1024,
            max_chain_sectors=250_000,
        ),
    )
    cleaned = (res.text or "").replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n").strip()
    warnings = ["旧版 .doc 格式为纯文本提取，物理页码与复杂表格边框未保留"]
    for w in (res.warnings or [])[:5]:
        warnings.append(f"doc_notice: {str(w)[:200]}")
    return cleaned, {
        "unit_kind": "document",
        "unit_count": 1,
        "warnings": warnings,
    }


def _extract_xlsx_to_markdown(data: bytes, original_path: Path) -> tuple[str, dict[str, Any]]:
    """解析 XLSX 工作簿并展开填充表头及数据区的合并单元格，保证工程量清单多级列头准确对齐。"""
    import openpyxl

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _validate_zip_container(zf)

    # 对于常规大小表格使用普通只读=False以获取 merged_cells 合并区间坐标；超大文件回退流式只读防内存峰值。
    use_merge_aware = len(data) <= 12 * 1024 * 1024
    wb = openpyxl.load_workbook(
        io.BytesIO(data),
        read_only=not use_merge_aware,
        data_only=True,
        keep_links=False,
    )
    sections: list[str] = []
    warnings: list[str] = []

    try:
        for idx, name in enumerate(wb.sheetnames, start=1):
            ws = wb[name]
            merge_lookup: dict[tuple[int, int], Any] = {}
            if use_merge_aware and getattr(ws, "merged_cells", None):
                for m_range in ws.merged_cells.ranges:
                    if (m_range.max_row - m_range.min_row) > 80:
                        continue
                    top_val = ws.cell(m_range.min_row, m_range.min_col).value
                    if top_val is not None and str(top_val).strip() != "":
                        for r in range(m_range.min_row, min(m_range.max_row, MAX_SHEET_ROWS_IN_MD + 20) + 1):
                            for c in range(m_range.min_col, min(m_range.max_col, 60) + 1):
                                merge_lookup[(r, c)] = top_val

            rows: list[list[Any]] = []
            total_non_empty = 0
            for r_idx, row_vals in enumerate(ws.iter_rows(values_only=True), start=1):
                if row_vals is None:
                    continue
                actual_row = list(row_vals[:60])
                if merge_lookup:
                    for c_idx in range(len(actual_row)):
                        if actual_row[c_idx] is None and (r_idx, c_idx + 1) in merge_lookup:
                            actual_row[c_idx] = merge_lookup[(r_idx, c_idx + 1)]
                if not any(_format_scalar(v) != "" for v in actual_row):
                    continue
                total_non_empty += 1
                if len(rows) < MAX_SHEET_ROWS_IN_MD:
                    rows.append(actual_row)

            header = f"<!-- page: {idx} -->\n## 工作表：{name}（非空行数：{total_non_empty}）"
            if not rows:
                sections.append(f"{header}\n[空工作表]")
                continue

            md_tbl = _rows_to_markdown_table(rows)
            if total_non_empty > MAX_SHEET_ROWS_IN_MD:
                msg = (
                    f"工作表 `{name}` 共 {total_non_empty} 行，仅预览前 {MAX_SHEET_ROWS_IN_MD} 行；"
                    f"全表汇总核算请调用 `run_python` 直接读取 `{original_path}`。"
                )
                warnings.append(msg)
                sections.append(f"{header}\n> [注意：{msg}]\n\n{md_tbl}")
            else:
                sections.append(f"{header}\n\n{md_tbl}")
    finally:
        wb.close()

    return "\n\n".join(sections), {
        "unit_kind": "sheets",
        "unit_count": len(sections),
        "warnings": warnings,
    }


def _extract_xls_to_markdown(data: bytes, original_path: Path) -> tuple[str, dict[str, Any]]:
    """解析旧版 XLS 工作簿，支持日期/布尔/浮点数干净格式化与合并单元格填充。"""
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=data, formatting_info=True, on_demand=True)
    except Exception:
        book = xlrd.open_workbook(file_contents=data, on_demand=True, ragged_rows=True)

    sections: list[str] = []
    warnings: list[str] = []
    names = book.sheet_names()

    try:
        for idx, name in enumerate(names, start=1):
            sheet = book.sheet_by_name(name)
            merge_map: dict[tuple[int, int], Any] = {}
            for rlo, rhi, clo, chi in getattr(sheet, "merged_cells", []) or []:
                if (rhi - rlo) > 80:
                    continue
                top_val = sheet.cell_value(rlo, clo)
                if str(top_val).strip() != "":
                    for rr in range(rlo, min(rhi, MAX_SHEET_ROWS_IN_MD + 20)):
                        for cc in range(clo, min(chi, 60)):
                            merge_map[(rr, cc)] = top_val

            rows: list[list[Any]] = []
            total_non_empty = 0
            for r in range(sheet.nrows):
                row_len = min(sheet.row_len(r), 60)
                vals: list[Any] = []
                for c in range(row_len):
                    cell = sheet.cell(r, c)
                    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                        v = merge_map.get((r, c), "")
                    elif cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            v = xlrd.xldate_as_datetime(cell.value, book.datemode)
                        except Exception:
                            v = cell.value
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        v = bool(cell.value)
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        v = "#ERROR"
                    else:
                        v = cell.value
                    vals.append(v)

                if not any(_format_scalar(x) != "" for x in vals):
                    continue
                total_non_empty += 1
                if len(rows) < MAX_SHEET_ROWS_IN_MD:
                    rows.append(vals)

            header = f"<!-- page: {idx} -->\n## 工作表：{name}（非空行数：{total_non_empty}）"
            if not rows:
                sections.append(f"{header}\n[空工作表]")
            else:
                if total_non_empty > MAX_SHEET_ROWS_IN_MD:
                    warnings.append(f"工作表 `{name}` 共 {total_non_empty} 行，超出预览上限，精确求和请用脚本读取 `{original_path}`")
                sections.append(f"{header}\n\n{_rows_to_markdown_table(rows)}")
    finally:
        book.release_resources()

    return "\n\n".join(sections), {
        "unit_kind": "sheets",
        "unit_count": len(names),
        "warnings": warnings,
    }


def _extract_xml_or_ebid_container(data: bytes, file_name: str) -> tuple[str, dict[str, Any]]:
    """
    通用 XML 与电子招投标容器（如 `.xml`、`.18zhzb`、`.hfzf`）自省提取器：
    1. 提取结构化 XML 节点属性摘要；
    2. 若检测到节点内嵌 Base64 ZIP 归档（`UEsDBBQ...`），自动在内存中安全探查内部归档清单。
    """
    text = _decode_text_bytes(data).lstrip("\ufeff").strip()
    root = ET.fromstring(text)
    lines: list[str] = [f"## 结构化容器概览（根节点：`<{root.tag}>`）"]
    if root.attrib:
        attrs_str = " | ".join(f"**{k}**: `{v}`" for k, v in list(root.attrib.items())[:20])
        lines.append(attrs_str)

    embedded_zips = 0
    elem_rows: list[list[str]] = [["节点标签", "关键属性 / 文本摘要"]]

    for elem in root.iter():
        if elem is root:
            continue
        body_txt = (elem.text or "").strip()
        # 识别内嵌 Base64 编码的 ZIP 包（PK\x03\x04 对应 Base64 前缀 UEsDBBQ）
        if len(body_txt) > 200 and body_txt.startswith("UEsDBBQ"):
            try:
                raw_zip = base64.b64decode(body_txt)
                with zipfile.ZipFile(io.BytesIO(raw_zip)) as inner_zf:
                    inner_names = inner_zf.namelist()[:60]
                    embedded_zips += 1
                    lines.append(
                        f"\n### 内嵌压缩包 `<{elem.tag}>`（共 {len(inner_zf.namelist())} 个内部条目）\n"
                        + "\n".join(f"- `{n}`" for n in inner_names)
                    )
            except Exception:
                lines.append(f"\n- `<{elem.tag}>`：包含二进制编码块（长度 {len(body_txt)} 字符）")
            continue

        if len(elem_rows) < 350:
            summary_parts = []
            if elem.attrib:
                summary_parts.append(", ".join(f"{k}={v}" for k, v in list(elem.attrib.items())[:10]))
            if body_txt and len(body_txt) <= 200:
                summary_parts.append(body_txt)
            if summary_parts:
                elem_rows.append([elem.tag, " ; ".join(summary_parts)])

    if len(elem_rows) > 1:
        lines.append("\n### 结构化条目明细预览\n\n" + _rows_to_markdown_table(elem_rows))

    return "\n\n".join(lines), {
        "unit_kind": "xml_nodes",
        "unit_count": len(elem_rows) - 1,
        "embedded_archives": embedded_zips,
        "warnings": [],
    }


def _extract_csv_to_markdown(data: bytes) -> tuple[str, dict[str, Any]]:
    """将 CSV 转换为标准 Markdown 表格。"""
    text = _decode_text_bytes(data)
    reader = csv.reader(io.StringIO(text))
    all_rows = [row for row in reader if any(cell.strip() for cell in row)]
    preview = all_rows[:MAX_SHEET_ROWS_IN_MD]
    warnings = []
    if len(all_rows) > MAX_SHEET_ROWS_IN_MD:
        warnings.append(f"CSV 共 {len(all_rows)} 行，仅预览前 {MAX_SHEET_ROWS_IN_MD} 行")
    return _rows_to_markdown_table(preview), {
        "unit_kind": "rows",
        "unit_count": len(all_rows),
        "warnings": warnings,
    }


def ocr_pdf_page_range(
    source_file: Path,
    workspace_dir: Path,
    start_page: int,
    end_page: int,
    source_base: Path | None = None,
) -> dict[str, Any]:
    """
    针对超长纯扫描 PDF 的按需增量补识工具：对 `[start_page, end_page]`（单次最多 20 页）执行高精度 OCR，
    并原地写回对应的缓存 `full.md` 与 `meta.json`，后续检索立即生效。
    """
    import pypdfium2 as pdfium

    resolved = source_file.resolve()
    data = resolved.read_bytes()
    file_hash = _sha256_bytes(data)
    rel_source = (
        resolved.relative_to(source_base.resolve()).as_posix()
        if source_base and resolved.is_relative_to(source_base.resolve())
        else resolved.name
    )
    parsed_root = workspace_dir / ".workspace" / "parsed"
    cache_dir_name = _safe_cache_slug(rel_source, file_hash)
    doc_cache_dir = parsed_root / cache_dir_name
    md_path = doc_cache_dir / "full.md"
    if not md_path.is_file():
        parse_single_file(resolved, parsed_root, source_base=source_base, enable_ocr=True)

    with _PDFIUM_LOCK:
        doc = pdfium.PdfDocument(data)
        try:
            total_pages = len(doc)
            s = max(1, int(start_page))
            e = min(total_pages, max(s, int(end_page)), s + 19)
            rendered_list: list[tuple[int, Any]] = []
            for pno in range(s, e + 1):
                page = doc[pno - 1]
                try:
                    w_pt, h_pt = page.get_size()
                    scale = min(1.5, MAX_RENDER_SIDE / max(w_pt, h_pt), math.sqrt(MAX_RENDER_PIXELS / (w_pt * h_pt)))
                    bm = page.render(scale=max(0.75, scale))
                    try:
                        rendered_list.append((pno, bm.to_pil()))
                    finally:
                        bm.close()
                finally:
                    page.close()
        finally:
            doc.close()

    md_text = md_path.read_text(encoding="utf-8")
    images_dir = doc_cache_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    recognized_pages: dict[int, str] = {}

    for pno, pil_img in rendered_list:
        lines = _ocr_pil_image(pil_img)
        txt = "\n".join(lines).strip() or "[空白页]"
        pil_img.save(images_dir / f"page-{pno:04d}.png", format="PNG")
        recognized_pages[pno] = txt
        pattern = re.compile(
            rf"(<!-- page: {pno} -->\n).*?(?=\n\n<!-- page: \d+ -->|\Z)",
            flags=re.DOTALL,
        )
        replacement = f"<!-- page: {pno} -->\n> [OCR识别页：第 {pno} 页，快照 `images/page-{pno:04d}.png`]\n{txt}"
        md_text = pattern.sub(lambda _m, rep=replacement: rep, md_text, count=1)

    md_path.write_text(md_text, encoding="utf-8")
    return {
        "status": "ready",
        "source_path": rel_source,
        "parsed_markdown": f".workspace/parsed/{cache_dir_name}/full.md",
        "ocr_completed_pages": list(recognized_pages.keys()),
        "preview": {str(k): v[:300] for k, v in recognized_pages.items()},
    }


def parse_single_file(
    source_file: Path,
    parsed_root: Path,
    source_base: Path | None = None,
    enable_ocr: bool = True,
    batch_budget: _BatchOcrBudget | None = None,
) -> dict[str, Any]:
    """
    将单个源文件解析或建档登记到 `.workspace/parsed/`。
    - 可解析办公/XML/文本文件：转为 `full.md`（长 PDF 额外生成 `toc.md`），按内容 SHA-256 毫秒级命中缓存；
    - 专业 CAD/压缩包/图片资产（如 `.dwg`）：登记为 `cataloged_binary`，保证全目录资产清单完整可查。
    """
    resolved = source_file.resolve()
    rel_source = (
        resolved.relative_to(source_base.resolve()).as_posix()
        if source_base and resolved.is_relative_to(source_base.resolve())
        else resolved.name
    )

    if is_ignored_temp_file(resolved):
        return {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "status": "ignored_temp",
        }

    ext = resolved.suffix.lower()
    try:
        size = resolved.stat().st_size
    except OSError as exc:
        return {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "status": "failed",
            "warnings": [f"无法读取文件元数据: {exc}"],
        }

    if ext in CATALOG_ONLY_EXTENSIONS or ext not in PARSEABLE_EXTENSIONS:
        return {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "ext": ext,
            "bytes": size,
            "status": "cataloged_binary",
            "note": "二进制图纸或媒体资产（已建档，可优先查阅同目录配套 PDF 签章图或设计说明）",
        }

    if size == 0:
        return {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "status": "failed",
            "warnings": ["文件为空 (0 字节)"],
        }
    if size > MAX_FILE_BYTES:
        return {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "status": "failed",
            "warnings": [f"文件体积超出离线解析阈值 ({size} > {MAX_FILE_BYTES} 字节)"],
        }

    try:
        data = resolved.read_bytes()
        file_hash = _sha256_bytes(data)
        cache_dir_name = _safe_cache_slug(rel_source, file_hash)
        doc_cache_dir = parsed_root / cache_dir_name
        md_path = doc_cache_dir / "full.md"
        meta_path = doc_cache_dir / "meta.json"

        if md_path.is_file() and meta_path.is_file():
            try:
                cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if (
                    cached_meta.get("sha256") == file_hash
                    and cached_meta.get("parser_version") == PARSER_VERSION
                    and cached_meta.get("source_path") == rel_source
                ):
                    return {**cached_meta, "cache_hit": True}
            except (OSError, ValueError, TypeError):
                pass

        doc_cache_dir.mkdir(parents=True, exist_ok=True)

        if ext == ".pdf":
            markdown_body, extra = _extract_pdf_to_markdown(
                data,
                resolved.name,
                doc_cache_dir,
                enable_ocr=enable_ocr,
                batch_budget=batch_budget,
            )
        elif ext == ".docx":
            markdown_body, extra = _extract_docx_to_markdown(data, enable_ocr=enable_ocr)
        elif ext == ".doc":
            markdown_body, extra = _extract_doc_to_markdown(data)
        elif ext == ".xlsx":
            markdown_body, extra = _extract_xlsx_to_markdown(data, resolved)
        elif ext == ".xls":
            markdown_body, extra = _extract_xls_to_markdown(data, resolved)
        elif ext == ".csv":
            markdown_body, extra = _extract_csv_to_markdown(data)
        elif ext in {".xml", ".hfzf", ".18zhzb"} or data.lstrip()[:6].startswith((b"<?xml", b"\xef\xbb\xbf<?x")):
            markdown_body, extra = _extract_xml_or_ebid_container(data, resolved.name)
        else:
            markdown_body = _decode_text_bytes(data).replace("\r\n", "\n").strip()
            extra = {"unit_kind": "lines", "unit_count": len(markdown_body.splitlines()), "warnings": []}

        header_banner = (
            f"<!-- source: {rel_source} | sha256: {file_hash[:16]} -->\n"
            f"# 源文档：{rel_source}\n\n"
        )
        full_markdown = header_banner + markdown_body + "\n"
        md_path.write_text(full_markdown, encoding="utf-8")

        meta = {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "parsed_markdown": f".workspace/parsed/{cache_dir_name}/full.md",
            "sha256": file_hash,
            "parser_version": PARSER_VERSION,
            "status": "ready",
            "bytes": size,
            "characters": len(full_markdown),
            "lines": len(full_markdown.splitlines()),
            **extra,
        }
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**meta, "cache_hit": False}

    except Exception as exc:
        return {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "status": "failed",
            "error": f"{type(exc).__name__}: {exc}",
            "warnings": [f"解析失败 ({type(exc).__name__}: {exc})"],
        }


def priority_sort_key(path: Path, source_base: Path | None = None) -> tuple[int, str]:
    """
    为大资料库设定解析优先级：招标正文、补疑、清单控价、地勘/设计说明等核心文字表格排在最前，
    单幅 `@A3.pdf` 等大批量图框文件排在第二梯队，确保核心约束第一时间落盘就绪。
    """
    name = path.name.lower()
    ext = path.suffix.lower()
    if ext in {".docx", ".doc", ".xlsx", ".xls", ".csv", ".txt", ".md", ".hfzf", ".18zhzb"}:
        tier = 0
    elif ext == ".pdf" and ("@" not in name and "图纸" not in str(path.parent)):
        tier = 1
    elif ext == ".pdf":
        tier = 2
    else:
        tier = 3
    rel = path.relative_to(source_base).as_posix() if source_base and path.is_relative_to(source_base) else name
    return (tier, rel)


def ingest_files(
    file_paths: list[Path],
    workspace_dir: Path,
    source_base: Path | None = None,
    enable_ocr: bool = True,
    max_workers: int = 4,
    max_batch_ocr_pages: int = DEFAULT_MAX_BATCH_OCR_PAGES,
    on_progress: Callable[[int, int, str, str], None] | None = None,
) -> dict[str, Any]:
    """
    并发批量标准化文件集合并维护统一的 `.workspace/parsed/manifest.json` 知识库总表。
    支持通过 `on_progress(done, total, rel_path, status)` 实时向宿主推送转换进度。
    """
    started = time.perf_counter()
    parsed_root = workspace_dir / ".workspace" / "parsed"
    parsed_root.mkdir(parents=True, exist_ok=True)

    filtered = [p for p in file_paths if not is_ignored_temp_file(p)]
    ordered = sorted(filtered, key=lambda p: priority_sort_key(p, source_base))
    total = len(ordered)
    workers = max(1, min(max_workers, total or 1))
    budget = _BatchOcrBudget(max_batch_ocr_pages)

    results_by_index: list[dict[str, Any] | None] = [None] * total
    completed_count = 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        future_to_idx = {
            pool.submit(parse_single_file, path, parsed_root, source_base, enable_ocr, budget): i
            for i, path in enumerate(ordered)
        }
        for future in concurrent.futures.as_completed(future_to_idx):
            idx = future_to_idx[future]
            res = future.result()
            results_by_index[idx] = res
            completed_count += 1
            if on_progress is not None:
                on_progress(completed_count, total, res.get("source_path", ""), res.get("status", ""))

    results = [r for r in results_by_index if r is not None]

    manifest_path = parsed_root / "manifest.json"
    existing_by_source: dict[str, dict[str, Any]] = {}
    if manifest_path.is_file():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            for item in old.get("documents", []):
                if isinstance(item, dict) and "source_path" in item:
                    existing_by_source[item["source_path"]] = item
        except (OSError, ValueError, TypeError):
            pass

    for item in results:
        if item.get("status") != "ignored_temp":
            existing_by_source[item["source_path"]] = item

    merged_docs = sorted(existing_by_source.values(), key=lambda d: d["source_path"])
    elapsed_sec = round(time.perf_counter() - started, 2)

    ready_docs = [r for r in results if r.get("status") == "ready"]
    cataloged_docs = [r for r in results if r.get("status") == "cataloged_binary"]
    failed_docs = [r for r in results if r.get("status") == "failed"]

    # 生成一份人类与大模型都能一眼通览的 Markdown 总索引表 `.workspace/parsed/INDEX.md`
    index_lines = [
        f"# 资料库标准化总索引（共 {len(merged_docs)} 项，已就绪正文 {sum(1 for d in merged_docs if d.get('status') == 'ready')} 份）\n",
        "| 源文件相对路径 | 状态 | 规模 | 标准化 Markdown 路径 | 章节导航 (TOC) | 备注/告警 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for d in merged_docs:
        st = d.get("status", "")
        unit_desc = (
            f"{d.get('unit_count', '-')} {d.get('unit_kind', '')}"
            if st == "ready"
            else f"{round(d.get('bytes', 0) / 1024, 1)} KB"
        )
        md_link = f"`{d['parsed_markdown']}`" if d.get("parsed_markdown") else "-"
        toc_link = f"`{d['toc_markdown']}`" if d.get("toc_markdown") else "-"
        warn_text = "；".join(d.get("warnings") or []) or d.get("note", "")
        index_lines.append(
            f"| `{d['source_path']}` | {st} | {unit_desc} | {md_link} | {toc_link} | {_escape_md_cell(warn_text)[:120]} |"
        )
    (parsed_root / "INDEX.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")

    manifest_payload = {
        "parser_version": PARSER_VERSION,
        "updated_at_epoch": round(time.time()),
        "total_indexed": len(merged_docs),
        "ready_count": sum(1 for d in merged_docs if d.get("status") == "ready"),
        "cataloged_binary_count": sum(1 for d in merged_docs if d.get("status") == "cataloged_binary"),
        "failed_count": sum(1 for d in merged_docs if d.get("status") == "failed"),
        "index_markdown": ".workspace/parsed/INDEX.md",
        "documents": merged_docs,
    }
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # 返回给主控模型的摘要控制精简尺寸：当文档超过 40 份时，引导查看 `.workspace/parsed/INDEX.md` 全表，重点突出核心文档与异常项。
    preview_ready = [
        {
            "source_path": r["source_path"],
            "parsed_markdown": r["parsed_markdown"],
            **({"toc_markdown": r["toc_markdown"]} if r.get("toc_markdown") else {}),
            "unit": f"{r.get('unit_count')} {r.get('unit_kind')}",
            "characters": r.get("characters"),
            **({"warnings": r["warnings"]} if r.get("warnings") else {}),
        }
        for r in ready_docs[:45]
    ]

    return {
        "parser_version": PARSER_VERSION,
        "elapsed_seconds": elapsed_sec,
        "total_processed": len(results),
        "ready_count": len(ready_docs),
        "cataloged_binary_count": len(cataloged_docs),
        "failed_count": len(failed_docs),
        "cache_hits": sum(1 for r in results if r.get("cache_hit") is True),
        "index_markdown": ".workspace/parsed/INDEX.md",
        "ready_documents_preview": preview_ready,
        "ready_preview_truncated": len(ready_docs) > 45,
        "failed_documents": failed_docs,
    }


def _cli_main() -> int:
    """提供独立命令行压测入口，支持直接对任意本地复杂文件夹执行批量标准化验收。"""
    ap = argparse.ArgumentParser(description="Mayi Agent Plus 离线文档标准化 CLI")
    ap.add_argument("--source", required=True, help="源资料目录或单文件路径")
    ap.add_argument("--workspace", required=True, help="输出工作区目录路径")
    ap.add_argument("--no-ocr", action="store_true", help="禁用图像 OCR（仅提取原生文本层）")
    ap.add_argument("--workers", type=int, default=4, help="并发文件工作线程数")
    args = ap.parse_args()

    src = Path(args.source).resolve()
    ws = Path(args.workspace).resolve()
    ws.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    if src.is_file():
        files.append(src)
        base = src.parent
    else:
        base = src
        for root_s, dirs, fnames in os.walk(src):
            dirs[:] = [d for d in dirs if d not in {".git", "node_modules", "__pycache__", ".venv", ".workspace"}]
            for fn in fnames:
                files.append(Path(root_s) / fn)

    def _print_progress(done: int, total: int, rel: str, status: str) -> None:
        if done == 1 or done == total or done % 10 == 0 or status == "failed":
            print(f"[{done}/{total}] ({status}) {rel}", flush=True)

    summary = ingest_files(
        file_paths=files,
        workspace_dir=ws,
        source_base=base,
        enable_ocr=not args.no_ocr,
        max_workers=args.workers,
        on_progress=_print_progress,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["failed_count"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
