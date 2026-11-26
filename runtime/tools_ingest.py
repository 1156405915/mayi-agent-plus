"""原子工具组 2（批量文档离线标准化）：将源目录或附件中的复杂办公文档一次性转为结构化 Markdown 缓存。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from packages.doc_engine.parser import SUPPORTED_EXTENSIONS, ingest_files
from runtime.tools_fs import IGNORED_DIRS


class DocumentIngestTool:
    """封装离线文档解析引擎，支持指定路径列表或自动发现源目录下的全部办公资料。"""

    def __init__(
        self,
        workspace_dir: Path,
        source_dir: Path,
        on_workspace_changed: Callable[[str], None] | None = None,
    ):
        self.workspace_dir = workspace_dir.resolve()
        self.source_dir = source_dir.resolve()
        self.on_workspace_changed = on_workspace_changed

    def ingest_documents(
        self,
        paths: list[str] | None = None,
        enable_ocr: bool = True,
    ) -> dict[str, Any]:
        """
        将指定的办公文档（PDF/DOCX/DOC/XLSX/XLS/CSV/TXT）批量离线解析为 `.workspace/parsed/` 下的带页码锚点 Markdown。
        若省略 `paths`（或传空列表），将自动扫描 `source_dir` 下最多 50 份支持格式的资料文件一次性完成转换与缓存。
        """
        candidates: list[Path] = []
        if paths:
            for raw in paths:
                cand = Path(raw.strip())
                resolved = cand.resolve() if cand.is_absolute() else (self.source_dir / cand).resolve()
                if not (resolved.is_relative_to(self.source_dir) or resolved.is_relative_to(self.workspace_dir)):
                    raise PermissionError(f"禁止解析未授权目录外的文件: {raw}")
                if resolved.is_file():
                    candidates.append(resolved)
                elif resolved.is_dir():
                    for root_s, dirs, files in os.walk(resolved):
                        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".workspace")]
                        for fn in sorted(files):
                            fp = Path(root_s) / fn
                            if fp.suffix.lower() in SUPPORTED_EXTENSIONS:
                                candidates.append(fp)
        else:
            for root_s, dirs, files in os.walk(self.source_dir):
                dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".workspace")]
                for fn in sorted(files):
                    fp = Path(root_s) / fn
                    if fp.suffix.lower() in SUPPORTED_EXTENSIONS:
                        candidates.append(fp)
                        if len(candidates) >= 50:
                            break
                if len(candidates) >= 50:
                    break

        if not candidates:
            return {
                "status": "empty",
                "message": "未找到可转换的办公或文本资料文件，请确认源目录路径是否正确。",
            }

        summary = ingest_files(
            file_paths=candidates[:50],
            workspace_dir=self.workspace_dir,
            source_base=self.source_dir,
            enable_ocr=enable_ocr,
        )
        if self.on_workspace_changed:
            self.on_workspace_changed(".workspace/parsed/manifest.json")
        return summary
