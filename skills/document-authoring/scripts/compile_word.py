"""标准 Word 编译交付脚本（M4 深化版）：支持明标/公文/技术暗标预设、横竖自适应分节编译与成品合规复检。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from packages.doc_engine.docx_auditor import audit_docx_file
from packages.doc_engine.docx_builder import DEFAULT_STYLE_PRESET, compile_markdown_to_docx


def _load_preset(preset_name_or_path: str) -> dict:
    """按预设名（formal_bidding / standard_report / blind_bidding）或 JSON 路径加载版式参数。"""
    presets_dir = Path(__file__).resolve().parent.parent / "presets"
    cand = presets_dir / f"{preset_name_or_path}.json"
    if not cand.is_file():
        cand = Path(preset_name_or_path)
    if cand.is_file():
        return {**DEFAULT_STYLE_PRESET, **json.loads(cand.read_text(encoding="utf-8"))}
    return dict(DEFAULT_STYLE_PRESET)


def main() -> int:
    """将工作区章节 Markdown 集合编译为原生 .docx，并执行包含暗标色彩与页眉检查的成品审计。"""
    parser = argparse.ArgumentParser(description="Compile Markdown chapters into a styled native Word (.docx)")
    parser.add_argument("--drafts-dir", default="drafts", help="章节 Markdown 所在目录")
    parser.add_argument("--output", default="deliverables/output.docx", help="输出的相对工作区 .docx 路径")
    parser.add_argument("--title", required=True, help="文档主标题")
    parser.add_argument("--subtitle", default="", help="可选文档副标题（暗标模式下自动忽略）")
    parser.add_argument("--preset", default="formal_bidding", help="版式预设：formal_bidding | standard_report | blind_bidding")
    parser.add_argument("--blind", action="store_true", help="强制切换为 blind_bidding 技术暗标纯黑白合规模式")
    args = parser.parse_args()

    workspace_dir = Path(os.environ.get("MAYI_WORKSPACE_DIR") or Path.cwd()).resolve()
    drafts_dir = (workspace_dir / args.drafts_dir).resolve()
    output_docx = (workspace_dir / args.output).resolve()

    if not output_docx.is_relative_to(workspace_dir):
        print(json.dumps({"passed": False, "error": "输出路径必须位于当前工作区内"}, ensure_ascii=False))
        return 1

    if not drafts_dir.is_dir():
        print(json.dumps({"passed": False, "error": f"未找到章节目录: {args.drafts_dir}"}, ensure_ascii=False))
        return 1

    chapters = sorted(p for p in drafts_dir.glob("*.md") if p.is_file())
    if not chapters:
        print(json.dumps({"passed": False, "error": f"`{args.drafts_dir}` 目录下没有任何 .md 章节文件"}, ensure_ascii=False))
        return 1

    preset_name = "blind_bidding" if args.blind else args.preset
    preset = _load_preset(preset_name)
    is_blind = bool(args.blind or preset.get("monochrome", False))

    build_info = compile_markdown_to_docx(
        chapter_files=chapters,
        output_docx=output_docx,
        title=args.title,
        subtitle=args.subtitle,
        preset=preset,
        workspace_dir=workspace_dir,
    )
    audit_info = audit_docx_file(output_docx, blind_mode=is_blind)
    payload = {
        "passed": audit_info["passed"],
        "preset_used": preset.get("name", preset_name),
        "blind_mode": is_blind,
        "deliverable_relative_path": output_docx.relative_to(workspace_dir).as_posix(),
        "build": build_info,
        "audit": audit_info,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if audit_info["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
