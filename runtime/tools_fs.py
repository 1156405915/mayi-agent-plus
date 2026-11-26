"""原子工具组 1（文件系统与毫秒级检索）：提供目录元数据扫描、带页码定位的全文搜索及工作区文件读写。"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Callable

IGNORED_DIRS = {".git", "node_modules", "__pycache__", ".venv", ".tools", "dist", "out", "release"}
TEXT_EXTENSIONS = {
    ".md", ".txt", ".json", ".yaml", ".yml", ".csv", ".py", ".ts", ".js", ".vue", ".html", ".css", ".xml",
}


class WorkspaceFS:
    """
    封装工作区（可读写）与外部资料源目录（严格只读）的文件访问边界。
    支持向特定子代理施加 `allowed_write_file` 单文件写限制，实现无锁物理隔离并发。
    """

    def __init__(
        self,
        workspace_dir: Path,
        source_dir: Path | None = None,
        on_workspace_changed: Callable[[str], None] | None = None,
        allowed_write_file: str | None = None,
    ):
        self.workspace_dir = workspace_dir.resolve()
        self.source_dir = source_dir.resolve() if source_dir else self.workspace_dir
        self.on_workspace_changed = on_workspace_changed
        self.allowed_write_file = allowed_write_file.replace("\\", "/").lstrip("/") if allowed_write_file else None
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def _resolve_read_path(self, raw_path: str) -> Path:
        """解析读取路径：支持相对工作区路径、相对源目录路径或受授权根内的绝对路径，阻断越界与符号链接穿越。"""
        cleaned = raw_path.strip()
        cand = Path(cleaned)
        roots = [self.workspace_dir, self.source_dir]

        if cand.is_absolute():
            resolved = cand.resolve()
            if any(resolved.is_relative_to(r) for r in roots):
                return resolved
            raise PermissionError(f"路径不在授权的工作区或资料目录内: {raw_path}")

        ws_target = (self.workspace_dir / cleaned).resolve()
        if ws_target.exists() and ws_target.is_relative_to(self.workspace_dir):
            return ws_target

        src_target = (self.source_dir / cleaned).resolve()
        if src_target.exists() and src_target.is_relative_to(self.source_dir):
            return src_target

        raise FileNotFoundError(f"未找到目标文件或目录: {raw_path}")

    def _resolve_write_path(self, rel_path: str) -> tuple[Path, str]:
        """解析工作区写路径并校验单文件租约（若当前为受限并发子代理），严禁修改外部只读源目录。"""
        norm = rel_path.strip().replace("\\", "/").lstrip("/")
        if not norm or norm.startswith("..") or "/../" in f"/{norm}/":
            raise PermissionError(f"非法的工作区相对路径: {rel_path}")
        if norm.startswith(".workspace/parsed/"):
            raise PermissionError("标准化解析缓存目录由 ingest_documents 维护，请勿手工覆盖")
        if self.allowed_write_file is not None and norm != self.allowed_write_file:
            raise PermissionError(f"当前子任务仅被授权写入 `{self.allowed_write_file}`，拒绝写入 `{norm}`")

        target = (self.workspace_dir / norm).resolve()
        if not target.is_relative_to(self.workspace_dir):
            raise PermissionError(f"禁止向工作区外部写入文件: {rel_path}")
        return target, norm

    def scan_directory(self, target: str = "source", subpath: str = ".", max_depth: int = 3) -> dict[str, Any]:
        """
        毫秒级扫描目录元数据树（文件名、相对路径、扩展名、大小），最多返回前 300 项以保护上下文体积。
        target 可选 'source'（用户提供的源资料文件夹）或 'workspace'（当前任务工作区）。
        """
        base = self.source_dir if target == "source" else self.workspace_dir
        start = (base / subpath).resolve()
        if not start.is_relative_to(base) or not start.is_dir():
            raise ValueError(f"无效的扫描子目录: {target}:{subpath}")

        entries: list[dict[str, Any]] = []
        truncated = False
        max_depth = max(1, min(int(max_depth), 6))

        def _walk(curr: Path, depth: int) -> None:
            nonlocal truncated
            if depth > max_depth or truncated:
                return
            try:
                children = sorted(curr.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
            except OSError:
                return
            for item in children:
                if item.name in IGNORED_DIRS or item.is_symlink():
                    continue
                if len(entries) >= 300:
                    truncated = True
                    return
                rel = item.relative_to(base).as_posix()
                if item.is_dir():
                    entries.append({"path": rel, "kind": "dir"})
                    _walk(item, depth + 1)
                elif item.is_file():
                    entries.append({
                        "path": rel,
                        "kind": "file",
                        "ext": item.suffix.lower(),
                        "bytes": item.stat().st_size,
                    })

        _walk(start, 1)
        return {
            "target": target,
            "root": str(base),
            "count": len(entries),
            "truncated": truncated,
            "entries": entries,
        }

    def search_text(
        self,
        query: str,
        scope: str = "parsed",
        path_filter: str = "",
        max_hits: int = 60,
    ) -> dict[str, Any]:
        """
        在纯文本或已转换的 Markdown 缓存中执行毫秒级多关键词/正则搜索，并自动回溯最近的 `<!-- page: N -->` 页码锚点。
        scope 可选 'parsed'（默认搜索 .workspace/parsed/ 下的标准化文档）、'workspace' 或 'source'。
        多个空格分隔词采用“任一命中并按命中词数优先”或正则匹配，方便一次性检索多个业务术语。
        """
        q = query.strip()
        if not q:
            raise ValueError("搜索关键词 query 不能为空")

        if scope == "parsed":
            search_root = self.workspace_dir / ".workspace" / "parsed"
        elif scope == "workspace":
            search_root = self.workspace_dir
        else:
            search_root = self.source_dir

        if not search_root.exists():
            return {"query": q, "scope": scope, "hit_count": 0, "hits": [], "note": "目标目录尚无文件，若需检索办公文档请先调用 ingest_documents"}

        terms = [t for t in re.split(r"\s+", q) if t]
        try:
            pattern = re.compile("|".join(re.escape(t) for t in terms), re.IGNORECASE) if len(terms) > 1 else re.compile(q, re.IGNORECASE)
        except re.error:
            pattern = re.compile(re.escape(q), re.IGNORECASE)

        page_re = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")
        source_banner_re = re.compile(r"<!--\s*source:\s*([^|]+?)\s*\|")
        hits: list[dict[str, Any]] = []
        pf_lower = path_filter.strip().lower()

        for root_str, dirs, files in os.walk(search_root):
            dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
            for fname in sorted(files):
                fpath = Path(root_str) / fname
                if fpath.suffix.lower() not in TEXT_EXTENSIONS:
                    continue
                rel_to_ws = (
                    fpath.resolve().relative_to(self.workspace_dir).as_posix()
                    if fpath.resolve().is_relative_to(self.workspace_dir)
                    else fpath.resolve().relative_to(self.source_dir).as_posix()
                )
                if pf_lower and pf_lower not in rel_to_ws.lower():
                    continue
                try:
                    lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue

                current_page: int | None = None
                origin_doc: str | None = None
                for idx, line in enumerate(lines, start=1):
                    if idx <= 3:
                        sm = source_banner_re.search(line)
                        if sm:
                            origin_doc = sm.group(1).strip()
                    pm = page_re.search(line)
                    if pm:
                        current_page = int(pm.group(1))
                        continue
                    if pattern.search(line):
                        matched_count = sum(1 for t in terms if t.lower() in line.lower())
                        hits.append({
                            "file": rel_to_ws,
                            "origin_source": origin_doc,
                            "page": current_page,
                            "line": idx,
                            "matched_terms": matched_count,
                            "snippet": line.strip()[:300],
                        })
                        if len(hits) >= max(200, max_hits * 3):
                            break

        hits.sort(key=lambda h: (-h["matched_terms"], h["file"], h["line"]))
        selected = hits[: max(1, min(int(max_hits), 120))]
        return {
            "query": q,
            "scope": scope,
            "hit_count": len(selected),
            "total_candidates": len(hits),
            "hits": selected,
        }

    def read_lines(self, path: str, start_line: int = 1, max_lines: int = 160) -> dict[str, Any]:
        """按行区间读取工作区或源目录中的纯文本/Markdown 文件（单次最多 300 行），返回带行号正文与总行数。"""
        target = self._resolve_read_path(path)
        if target.suffix.lower() in {".pdf", ".docx", ".doc", ".xlsx", ".xls"}:
            raise ValueError(
                f"`{path}` 是二进制办公文档，请先调用 `ingest_documents` 并读取 `.workspace/parsed/` 下对应的 `full.md`。"
            )
        text = target.read_text(encoding="utf-8", errors="replace")
        all_lines = text.splitlines()
        total = len(all_lines)
        s = max(1, int(start_line))
        limit = max(1, min(int(max_lines), 300))
        e = min(total, s + limit - 1)
        window = [f"{ln}: {all_lines[ln - 1]}" for ln in range(s, e + 1)] if s <= total else []

        return {
            "path": path,
            "start_line": s,
            "end_line": e,
            "total_lines": total,
            "has_more": e < total,
            "next_start_line": e + 1 if e < total else None,
            "content": "\n".join(window),
        }

    def write_workspace_file(self, path: str, content: str) -> dict[str, Any]:
        """在工作区内创建或完整覆写指定文本/Markdown 文件，并通知前端工作台实时刷新预览。"""
        target, norm = self._resolve_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        if self.on_workspace_changed:
            self.on_workspace_changed(norm)
        return {
            "status": "written",
            "path": norm,
            "characters": len(content),
            "lines": len(content.splitlines()),
        }

    def edit_workspace_file(self, path: str, old_text: str, new_text: str) -> dict[str, Any]:
        """对工作区已有文件执行局部精确替换修订，避免修改几行内容时重写整篇长文。"""
        target, norm = self._resolve_write_path(path)
        if not target.is_file():
            raise FileNotFoundError(f"待编辑的工作区文件不存在: {norm}")
        raw = target.read_text(encoding="utf-8")
        if not old_text or old_text not in raw:
            raise ValueError("未在目标文件中匹配到 `old_text` 原文片段，请先用 `read_lines` 确认准确文本后再替换。")
        updated = raw.replace(old_text, new_text, 1)
        target.write_text(updated, encoding="utf-8")
        if self.on_workspace_changed:
            self.on_workspace_changed(norm)
        return {
            "status": "edited",
            "path": norm,
            "characters": len(updated),
        }
