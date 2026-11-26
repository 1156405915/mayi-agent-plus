"""原子工具组 2（批量文档离线标准化）：支持大规模多层级工程资料库无截断批量解析、建档与实时进度推送。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from packages.doc_engine.parser import ingest_files, is_ignored_temp_file, ocr_pdf_page_range
from runtime.tools_fs import IGNORED_DIRS

MAX_INGEST_BATCH_FILES = 400


class DocumentIngestTool:
    """封装生产级离线文档解析引擎，自动发现深层子目录文档并向工作台实时推送解析进度。"""

    def __init__(
        self,
        workspace_dir: Path,
        source_dir: Path,
        on_workspace_changed: Callable[[str], None] | None = None,
        on_progress_event: Callable[[str], None] | None = None,
    ):
        self.workspace_dir = workspace_dir.resolve()
        self.source_dir = source_dir.resolve()
        self.on_workspace_changed = on_workspace_changed
        self.on_progress_event = on_progress_event

    def ingest_documents(
        self,
        paths: list[str] | None = None,
        enable_ocr: bool = True,
    ) -> dict[str, Any]:
        """
        将源目录中的办公文档（PDF/DOCX/DOC/XLSX/XLS/XML/HFZF/18ZHZB/CSV/TXT）批量离线标准化至 `.workspace/parsed/`，
        同时对 `.dwg` 等二进制图纸登记建档，并生成总索引表 `.workspace/parsed/INDEX.md`。
        省略 `paths` 时自动递归扫描整个 `source_dir`（最多 400 个文件，自动过滤 `~$` 锁文件）。
        """
        candidates: list[Path] = []
        if paths:
            for raw in paths:
                cand = Path(raw.strip())
                resolved = cand.resolve() if cand.is_absolute() else (self.source_dir / cand).resolve()
                if not (resolved.is_relative_to(self.source_dir) or resolved.is_relative_to(self.workspace_dir)):
                    raise PermissionError(f"禁止解析未授权目录外的文件: {raw}")
                if resolved.is_file():
                    if not is_ignored_temp_file(resolved):
                        candidates.append(resolved)
                elif resolved.is_dir():
                    for root_s, dirs, files in os.walk(resolved):
                        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".workspace")]
                        for fn in sorted(files):
                            fp = Path(root_s) / fn
                            if not is_ignored_temp_file(fp):
                                candidates.append(fp)
        else:
            for root_s, dirs, files in os.walk(self.source_dir):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".workspace")]
                for fn in sorted(files):
                    fp = Path(root_s) / fn
                    if not is_ignored_temp_file(fp):
                        candidates.append(fp)
                        if len(candidates) >= MAX_INGEST_BATCH_FILES:
                            break
                if len(candidates) >= MAX_INGEST_BATCH_FILES:
                    break

        if not candidates:
            return {
                "status": "empty",
                "message": "源目录下未发现任何有效资料文件。",
            }

        def _report_progress(done: int, total: int, rel_path: str, _status: str) -> None:
            if self.on_progress_event and (done == 1 or done == total or done % 5 == 0):
                self.on_progress_event(f"正在离线标准化资料 ({done}/{total})：{rel_path}")

        summary = ingest_files(
            file_paths=candidates[:MAX_INGEST_BATCH_FILES],
            workspace_dir=self.workspace_dir,
            source_base=self.source_dir,
            enable_ocr=enable_ocr,
            on_progress=_report_progress,
        )
        if self.on_workspace_changed:
            self.on_workspace_changed(".workspace/parsed/INDEX.md")
        return summary

    def ocr_pdf_pages(self, source_path: str, start_page: int, end_page: int) -> dict[str, Any]:
        """
        当超长扫描版 PDF 在首轮标准化中提示仍有按需待识页面（`deferred_ocr_pages`）时，
        按页码区间 `[start_page, end_page]`（单次最多 20 页）补做本地 OCR 并原地更新 `.workspace/parsed/*/full.md`。
        """
        cand = Path(source_path.strip())
        resolved = cand.resolve() if cand.is_absolute() else (self.source_dir / cand).resolve()
        if not (resolved.is_relative_to(self.source_dir) or resolved.is_relative_to(self.workspace_dir)):
            raise PermissionError(f"未授权访问的 PDF 路径: {source_path}")
        if not resolved.is_file() or resolved.suffix.lower() != ".pdf":
            raise ValueError(f"目标文件不存在或不是 PDF: {source_path}")

        res = ocr_pdf_page_range(
            source_file=resolved,
            workspace_dir=self.workspace_dir,
            start_page=start_page,
            end_page=end_page,
            source_base=self.source_dir,
        )
        if self.on_workspace_changed:
            self.on_workspace_changed(res["parsed_markdown"])
        return res
