"""确定性章节草稿硬检脚本（M4 深化版）：检查缺章、字数下限、占位符残留、图片断链、跨章总工期一致性及暗标合规红线。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from packages.doc_engine.docx_auditor import audit_markdown_drafts


def main() -> int:
    """解析命令行参数并对工作区章节草稿目录执行全方位质量审计。"""
    parser = argparse.ArgumentParser(description="Audit Markdown chapter drafts before Word compilation")
    parser.add_argument("--drafts-dir", default="drafts", help="相对于工作区的章节草稿目录路径")
    parser.add_argument("--min-chars", type=int, default=180, help="单章最低有效字符数阈值")
    parser.add_argument("--blind", action="store_true", help="启用技术暗标红线检查（拦截单位署名、手机号、邮箱）")
    parser.add_argument("--expected-duration-days", type=int, default=None, help="可选：强制要求的总工期日历天上限")
    args = parser.parse_args()

    workspace_dir = Path(os.environ.get("MAYI_WORKSPACE_DIR") or Path.cwd()).resolve()
    drafts_dir = (workspace_dir / args.drafts_dir).resolve()
    if not drafts_dir.is_dir():
        print(json.dumps({"passed": False, "error": f"未找到草稿目录: {args.drafts_dir}"}, ensure_ascii=False))
        return 1

    md_files = sorted(p for p in drafts_dir.glob("*.md") if p.is_file())
    report = audit_markdown_drafts(
        md_files,
        workspace_dir=workspace_dir,
        min_chapter_chars=args.min_chars,
        blind_mode=args.blind,
        expected_duration_days=args.expected_duration_days,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
