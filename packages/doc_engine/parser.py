"""离线文档批量解析器：将 PDF/DOCX/DOC/XLSX/XLS/文本统一转换为带页码与表格的 Markdown 缓存。"""
from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any

PARSER_VERSION = "mayi-plus-parser-v1"
MAX_FILE_BYTES = 80 * 1024 * 1024
MAX_ZIP_ENTRIES = 4096
MAX_UNPACKED_BYTES = 250 * 1024 * 1024
MAX_PDF_PAGES = 1200
MAX_RENDER_PIXELS = 3_600_000
MAX_SHEET_ROWS_IN_MD = 500

W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".xlsx", ".xls",
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml",
}

_OCR_ENGINE = None
_OCR_UNAVAILABLE = False


def _get_ocr_engine():
    """延迟加载单例 RapidOCR 引擎，避免纯文本转换任务启动时承担模型加载开销。"""
    global _OCR_ENGINE, _OCR_UNAVAILABLE
    if _OCR_UNAVAILABLE:
        return None
    if _OCR_ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
            _OCR_ENGINE = RapidOCR()
        except Exception:
            _OCR_UNAVAILABLE = True
            return None
    return _OCR_ENGINE


def _sha256_bytes(data: bytes) -> str:
    """计算文件内容的 SHA-256 十六进制摘要，用于内容寻址缓存防重算。"""
    return hashlib.sha256(data).hexdigest()


def _safe_slug(name: str) -> str:
    """将源文件名归一化为安全可读的缓存目录前缀，避免特殊字符引发跨平台路径问题。"""
    stem = Path(name).stem
    cleaned = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", stem).strip("_")
    return (cleaned[:48] or "doc")


def _escape_md_cell(value: Any) -> str:
    """转义单元格中的竖线与换行，防止破坏 Markdown 管道表格语法结构。"""
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    return text.replace("|", "\\|").replace("\r\n", "<br>").replace("\n", "<br>")


def _rows_to_markdown_table(rows: list[list[Any]]) -> str:
    """将二维行数据渲染为对齐的 GFM 管道表格，自动丢弃全空尾行并统一列宽。"""
    cleaned_rows = [[_escape_md_cell(cell) for cell in row] for row in rows]
    cleaned_rows = [row for row in cleaned_rows if any(cell != "" for cell in row)]
    if not cleaned_rows:
        return ""
    col_count = max(len(row) for row in cleaned_rows)
    normalized = [row + [""] * (col_count - len(row)) for row in cleaned_rows]
    header = normalized[0]
    separator = ["---"] * col_count
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


def _decode_text_bytes(data: bytes) -> str:
    """优先使用 UTF-8 解码文本，遇到常见中文 Windows 旧编码文件时自动回退至 GB18030。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _validate_zip_container(zf: zipfile.ZipFile) -> None:
    """检查 Office ZIP 容器条目数、压缩炸弹与路径穿越，阻断恶意构造文档。"""
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ValueError(f"压缩包条目数超出上限 ({len(infos)} > {MAX_ZIP_ENTRIES})")
    total_size = 0
    for info in infos:
        if ".." in Path(info.filename).parts or info.filename.startswith(("/", "\\")):
            raise ValueError("压缩包内存在非法路径穿越条目")
        if info.flag_bits & 0x1:
            raise ValueError("文档已加密，无法自动离线提取正文")
        total_size += info.file_size
        if total_size > MAX_UNPACKED_BYTES:
            raise ValueError("文档展开体积超出安全阈值")


def _extract_pdf_to_markdown(data: bytes, images_dir: Path, enable_ocr: bool = True) -> tuple[str, dict[str, Any]]:
    """逐页提取 PDF 文本并在扫描页上自动启用本地 OCR，输出含页码锚点的完整 Markdown。"""
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument(data)
    page_count = len(doc)
    if page_count < 1 or page_count > MAX_PDF_PAGES:
        doc.close()
        raise ValueError(f"PDF 页数超出支持范围: {page_count}")

    sections: list[str] = []
    warnings: list[str] = []
    ocr_pages: list[int] = []

    try:
        for index in range(page_count):
            page_no = index + 1
            page = doc[index]
            try:
                textpage = page.get_textpage()
                try:
                    raw_text = (textpage.get_text_range() or "").replace("\r\n", "\n").replace("\r", "\n").strip()
                finally:
                    textpage.close()

                # 当原生文本层为空或极度稀疏时，判定可能是扫描件或图片页，按需渲染位图执行 OCR。
                used_ocr = False
                if enable_ocr and len(re.sub(r"\s+", "", raw_text)) < 18:
                    ocr = _get_ocr_engine()
                    if ocr is not None:
                        width, height = page.get_size()
                        scale = min(2.0, (MAX_RENDER_PIXELS / max(width * height, 1.0)) ** 0.5)
                        bitmap = page.render(scale=max(scale, 0.75))
                        pil_img = bitmap.to_pil()
                        images_dir.mkdir(parents=True, exist_ok=True)
                        preview_path = images_dir / f"page-{page_no:04d}.png"
                        pil_img.save(preview_path, format="PNG")
                        import numpy as np
                        ocr_result, _ = ocr(np.array(pil_img))
                        if ocr_result:
                            ocr_lines = [item[1].strip() for item in ocr_result if len(item) >= 2 and str(item[1]).strip()]
                            ocr_text = "\n".join(ocr_lines).strip()
                            if len(ocr_text) > len(raw_text):
                                raw_text = ocr_text
                                used_ocr = True
                                ocr_pages.append(page_no)
                    elif not raw_text:
                        warnings.append(f"第 {page_no} 页疑似扫描页且本地 OCR 未启用")

                header = f"<!-- page: {page_no} -->"
                if used_ocr:
                    header += f"\n> [提示：第 {page_no} 页由 OCR 识别，必要时可核对快照 images/page-{page_no:04d}.png]"
                body = raw_text if raw_text else "[空白页或纯图形页]"
                sections.append(f"{header}\n{body}")
            finally:
                page.close()
    finally:
        doc.close()

    if ocr_pages:
        warnings.append(f"共 {len(ocr_pages)} 页通过 OCR 识别（页码示例：{ocr_pages[:10]}）")

    return "\n\n".join(sections), {
        "unit_kind": "pages",
        "unit_count": page_count,
        "ocr_pages": ocr_pages,
        "warnings": warnings,
    }


def _extract_docx_to_markdown(data: bytes) -> tuple[str, dict[str, Any]]:
    """按正文原始排版顺序解析 DOCX 段落层级与原生表格，生成结构化 Markdown。"""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        _validate_zip_container(zf)
        if "word/document.xml" not in zf.namelist():
            raise ValueError("无效的 DOCX 文件：缺少 word/document.xml")
        xml_bytes = zf.read("word/document.xml")

    root = ET.fromstring(xml_bytes)
    body = root.find(f"{W_NS}body")
    if body is None:
        return "", {"unit_kind": "blocks", "unit_count": 0, "warnings": ["文档正文为空"]}

    blocks: list[str] = []
    table_count = 0

    for child in body:
        if child.tag == f"{W_NS}p":
            texts = [node.text for node in child.iter(f"{W_NS}t") if node.text]
            line = "".join(texts).strip()
            if not line:
                continue
            style_el = child.find(f"./{W_NS}pPr/{W_NS}pStyle")
            style_val = (style_el.get(f"{W_NS}val") or "") if style_el is not None else ""
            # 识别常见中英文标题样式，保留原文档大纲层级结构以便后续快速提炼目录与条款。
            level_match = re.search(r"(?:Heading|标题)\s*([1-4])", style_val, re.IGNORECASE)
            if level_match:
                hashes = "#" * int(level_match.group(1))
                blocks.append(f"{hashes} {line}")
            else:
                blocks.append(line)
        elif child.tag == f"{W_NS}tbl":
            rows: list[list[str]] = []
            for tr in child.findall(f"./{W_NS}tr"):
                row_cells: list[str] = []
                for tc in tr.findall(f"./{W_NS}tc"):
                    paras = []
                    for p in tc.findall(f"./{W_NS}p"):
                        p_text = "".join(node.text for node in p.iter(f"{W_NS}t") if node.text).strip()
                        if p_text:
                            paras.append(p_text)
                    row_cells.append("\n".join(paras))
                rows.append(row_cells)
            md_table = _rows_to_markdown_table(rows)
            if md_table:
                table_count += 1
                blocks.append(md_table)

    return "\n\n".join(blocks), {
        "unit_kind": "sections",
        "unit_count": len(blocks),
        "table_count": table_count,
        "warnings": [],
    }


def _extract_doc_to_markdown(file_path: Path) -> tuple[str, dict[str, Any]]:
    """提取旧版二进制 Word (.doc) 文本并附带版式复核提示。"""
    import legacy_doc

    text = legacy_doc.extract_text(str(file_path)).strip()
    return text, {
        "unit_kind": "document",
        "unit_count": 1,
        "warnings": ["旧版 .doc 格式仅提取纯文本流，复杂表格建议结合原文件复核"],
    }


def _extract_xlsx_to_markdown(data: bytes, original_path: Path) -> tuple[str, dict[str, Any]]:
    """按工作表提取 XLSX 数据为 Markdown 表格；超大表自动截断展示并提示用 Python 脚本精确统计。"""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True, keep_links=False)
    sections: list[str] = []
    warnings: list[str] = []
    sheet_names = wb.sheetnames

    try:
        for sheet_idx, name in enumerate(sheet_names, start=1):
            ws = wb[name]
            rows: list[list[Any]] = []
            total_non_empty = 0
            for row in ws.iter_rows(values_only=True):
                if row is None or not any(cell is not None and str(cell).strip() != "" for cell in row):
                    continue
                total_non_empty += 1
                if len(rows) < MAX_SHEET_ROWS_IN_MD:
                    rows.append(list(row))

            section_header = f"<!-- page: {sheet_idx} -->\n## 工作表：{name}（非空行数：{total_non_empty}）"
            if not rows:
                sections.append(f"{section_header}\n[空工作表]")
                continue

            table_md = _rows_to_markdown_table(rows)
            if total_non_empty > MAX_SHEET_ROWS_IN_MD:
                msg = (
                    f"工作表 `{name}` 共 {total_non_empty} 行，Markdown 缓存仅展示前 {MAX_SHEET_ROWS_IN_MD} 行；"
                    f"如需全表精确求和或筛选，请调用 run_python 读取原文件 `{original_path}`。"
                )
                warnings.append(msg)
                sections.append(f"{section_header}\n> [注意：{msg}]\n\n{table_md}")
            else:
                sections.append(f"{section_header}\n\n{table_md}")
    finally:
        wb.close()

    return "\n\n".join(sections), {
        "unit_kind": "sheets",
        "unit_count": len(sheet_names),
        "warnings": warnings,
    }


def _extract_xls_to_markdown(data: bytes, original_path: Path) -> tuple[str, dict[str, Any]]:
    """按工作表提取旧版 XLS 工作簿内容为 Markdown 表格。"""
    import xlrd

    book = xlrd.open_workbook(file_contents=data, on_demand=True)
    sections: list[str] = []
    warnings: list[str] = []
    names = book.sheet_names()

    try:
        for idx, name in enumerate(names, start=1):
            sheet = book.sheet_by_name(name)
            rows: list[list[Any]] = []
            total_non_empty = 0
            for r in range(sheet.nrows):
                vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
                if not any(str(v).strip() != "" for v in vals):
                    continue
                total_non_empty += 1
                if len(rows) < MAX_SHEET_ROWS_IN_MD:
                    rows.append(vals)
            header = f"<!-- page: {idx} -->\n## 工作表：{name}（非空行数：{total_non_empty}）"
            if not rows:
                sections.append(f"{header}\n[空工作表]")
            else:
                if total_non_empty > MAX_SHEET_ROWS_IN_MD:
                    warnings.append(f"工作表 `{name}` 超出 {MAX_SHEET_ROWS_IN_MD} 行预览上限，汇总核算请直接脚本读取 `{original_path}`")
                sections.append(f"{header}\n\n{_rows_to_markdown_table(rows)}")
    finally:
        book.release_resources()

    return "\n\n".join(sections), {
        "unit_kind": "sheets",
        "unit_count": len(names),
        "warnings": warnings,
    }


def _extract_csv_to_markdown(data: bytes) -> tuple[str, dict[str, Any]]:
    """将 CSV 文本转换为标准 Markdown 表格，便于模型直接阅读字段结构。"""
    text = _decode_text_bytes(data)
    reader = csv.reader(io.StringIO(text))
    all_rows = [row for row in reader if any(cell.strip() for cell in row)]
    preview = all_rows[:MAX_SHEET_ROWS_IN_MD]
    warnings = []
    if len(all_rows) > MAX_SHEET_ROWS_IN_MD:
        warnings.append(f"CSV 共 {len(all_rows)} 行，预览截取前 {MAX_SHEET_ROWS_IN_MD} 行")
    return _rows_to_markdown_table(preview), {
        "unit_kind": "rows",
        "unit_count": len(all_rows),
        "warnings": warnings,
    }


def parse_single_file(
    source_file: Path,
    parsed_root: Path,
    source_base: Path | None = None,
    enable_ocr: bool = True,
) -> dict[str, Any]:
    """
    将单个源文件解析并缓存为结构化 Markdown。
    若目标缓存已存在且文件 SHA-256 与解析器版本一致，直接毫秒级命中返回，绝不重复解析。
    异常或加密文件会被安全捕获并记录 failed 状态，不中断整批文件夹标准化流程。
    """
    resolved = source_file.resolve()
    rel_source = (
        resolved.relative_to(source_base.resolve()).as_posix()
        if source_base and resolved.is_relative_to(source_base.resolve())
        else resolved.name
    )
    ext = resolved.suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        return {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "status": "skipped",
            "reason": f"暂不支持自动转文本的扩展名: {ext}",
        }

    try:
        size = resolved.stat().st_size
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
                "warnings": [f"文件体积超出离线解析上限 ({size} > {MAX_FILE_BYTES} 字节)"],
            }

        data = resolved.read_bytes()
        file_hash = _sha256_bytes(data)
        cache_dir_name = f"{_safe_slug(resolved.name)}-{file_hash[:10]}"
        doc_cache_dir = parsed_root / cache_dir_name
        md_path = doc_cache_dir / "full.md"
        meta_path = doc_cache_dir / "meta.json"

        # 命中内容寻址缓存时直接复用磁盘结果，确保多轮对话或重启后零解析延迟。
        if md_path.is_file() and meta_path.is_file():
            try:
                cached_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if (
                    cached_meta.get("sha256") == file_hash
                    and cached_meta.get("parser_version") == PARSER_VERSION
                ):
                    return {**cached_meta, "cache_hit": True}
            except (OSError, ValueError, TypeError):
                pass

        doc_cache_dir.mkdir(parents=True, exist_ok=True)
        images_dir = doc_cache_dir / "images"

        if ext == ".pdf":
            markdown_body, extra = _extract_pdf_to_markdown(data, images_dir, enable_ocr=enable_ocr)
        elif ext == ".docx":
            markdown_body, extra = _extract_docx_to_markdown(data)
        elif ext == ".doc":
            markdown_body, extra = _extract_doc_to_markdown(resolved)
        elif ext == ".xlsx":
            markdown_body, extra = _extract_xlsx_to_markdown(data, resolved)
        elif ext == ".xls":
            markdown_body, extra = _extract_xls_to_markdown(data, resolved)
        elif ext == ".csv":
            markdown_body, extra = _extract_csv_to_markdown(data)
        else:
            markdown_body = _decode_text_bytes(data).replace("\r\n", "\n").strip()
            extra = {"unit_kind": "lines", "unit_count": len(markdown_body.splitlines()), "warnings": []}

        header_banner = (
            f"<!-- source: {rel_source} | sha256: {file_hash[:16]} -->\n"
            f"# 源文档：{resolved.name}\n\n"
        )
        full_markdown = header_banner + markdown_body + "\n"
        md_path.write_text(full_markdown, encoding="utf-8")

        line_count = len(full_markdown.splitlines())
        meta = {
            "source_path": rel_source,
            "original_abspath": str(resolved),
            "parsed_markdown": f".workspace/parsed/{cache_dir_name}/full.md",
            "sha256": file_hash,
            "parser_version": PARSER_VERSION,
            "status": "ready",
            "characters": len(full_markdown),
            "lines": line_count,
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
            "warnings": [f"文件解析失败 ({type(exc).__name__}: {exc})"],
        }


def ingest_files(
    file_paths: list[Path],
    workspace_dir: Path,
    source_base: Path | None = None,
    enable_ocr: bool = True,
    max_workers: int = 4,
) -> dict[str, Any]:
    """
    并发批量将多份源文档转换为工作区 `.workspace/parsed/` 下的标准 Markdown 缓存，
    并更新统一的 `manifest.json` 索引清单供主控与子代理快速检索。
    """
    parsed_root = workspace_dir / ".workspace" / "parsed"
    parsed_root.mkdir(parents=True, exist_ok=True)

    workers = max(1, min(max_workers, len(file_paths) or 1))
    results: list[dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(parse_single_file, path, parsed_root, source_base, enable_ocr)
            for path in file_paths
        ]
        for future in futures:
            results.append(future.result())

    # 合并历史已解析清单，支持增量补充新文件而不覆盖既有缓存记录。
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
        existing_by_source[item["source_path"]] = item

    merged_docs = sorted(existing_by_source.values(), key=lambda d: d["source_path"])
    summary = {
        "parser_version": PARSER_VERSION,
        "total_processed_this_batch": len(results),
        "ready_count": sum(1 for r in results if r.get("status") == "ready"),
        "failed_count": sum(1 for r in results if r.get("status") == "failed"),
        "skipped_count": sum(1 for r in results if r.get("status") == "skipped"),
        "cache_hits": sum(1 for r in results if r.get("cache_hit") is True),
        "batch_results": results,
        "total_indexed_documents": len(merged_docs),
    }
    manifest_payload = {
        "parser_version": PARSER_VERSION,
        "documents": merged_docs,
    }
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
