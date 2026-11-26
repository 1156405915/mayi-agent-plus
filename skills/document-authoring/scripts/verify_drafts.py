"""确定性章节草稿硬检脚本：扫描 drafts/ 下全部 Markdown 章节的字数、标题层级、占位符残留与图片引用完整性。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from packages.doc_engine.docx_auditor import audit_markdown_drafts


def main() -> int:
    """解析命令行参数并对工作区章节草稿目录执行批量合规体检。"""
    parser = argparse.ArgumentParser(description="Audit Markdown chapter drafts before Word compilation")
    parser.add_argument("--drafts-dir", default="drafts", help="相对于工作区的章节草稿目录路径")
    parser.add_argument("--min-chars", type=int, default=180, help="单章最低有效字符数阈值")
    args = parser.parse_args()

    workspace_dir = Path(os.environ.get("MAYI_WORKSPACE_DIR") or Path.cwd()).resolve()
    drafts_dir = (workspace_dir / args.drafts_dir).resolve()
    if not drafts_dir.is_dir():
        print(json.dumps({"passed": False, "error": f"未找到草稿目录: {args.drafts_dir}"}, ensure_ascii=False))
        return 1

    md_files = sorted(p for p in drafts_dir.glob("*.md") if p.is_file())
    report = audit_markdown_drafts(md_files, workspace_dir=workspace_dir, min_chapter_chars=args.min_chars)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
