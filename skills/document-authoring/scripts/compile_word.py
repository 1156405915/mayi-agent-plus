"""标准 Word 编译交付脚本（M4 深化版）：支持明标/公文/技术暗标预设、横竖自适应分节编译与成品合规复检。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from packages.doc_engine.docx_auditor import audit_docx_file
from packages.doc_engine.docx_builder import DEFAULT_STYLE_PRESET, compile_markdown_to_docx
from packages.doc_engine.qingtian_auditor import verify_qingtian_release_gate


def _load_preset(preset_name_or_path: str, workspace_dir: Path | None = None) -> dict:
    """按预设名（formal_bidding / standard_report / blind_bidding）或工作区自定义 JSON 路径加载版式参数。"""
    presets_dir = Path(__file__).resolve().parent.parent / "presets"
    candidates = [presets_dir / f"{preset_name_or_path}.json", Path(preset_name_or_path)]
    if workspace_dir is not None:
        candidates.append((workspace_dir / preset_name_or_path).resolve())
    for cand in candidates:
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
    # 招标文件有“≤N 页”要求时必须传入，成品会经真实排版引擎测页，超限直接判定不通过。
    parser.add_argument("--max-pages", type=int, default=None, help="招标规定的页数上限（真实排版测页）")
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
    preset = _load_preset(preset_name, workspace_dir=workspace_dir)
    is_blind = bool(args.blind or preset.get("monochrome", False))

    # 硬性准入锁死：凡投标类预设（或工作区已启用青天雷达）必须持有与当前草稿 SHA-256 一致的 ≥90 分准入证方可编译。
    require_qa = bool(
        preset.get("require_qingtian_gate", False)
        or (workspace_dir / ".workspace" / "qingtian_radar.json").is_file()
        or (workspace_dir / "03-qingtian-review.md").is_file()
    )
    qa_gate: dict | None = None
    if require_qa:
        min_score = float(preset.get("qingtian_min_score", 90.0))
        qa_gate = verify_qingtian_release_gate(
            workspace_dir=workspace_dir,
            draft_files=chapters,
            min_score=min_score,
            blind_mode=is_blind,
        )
        if not qa_gate["passed"]:
            print(json.dumps({"passed": False, "qingtian_gate": qa_gate}, ensure_ascii=False, indent=2))
            return 3

    build_info = compile_markdown_to_docx(
        chapter_files=chapters,
        output_docx=output_docx,
        title=args.title,
        subtitle=args.subtitle,
        preset=preset,
        workspace_dir=workspace_dir,
    )
    audit_info = audit_docx_file(output_docx, blind_mode=is_blind, max_pages=args.max_pages)
    payload = {
        "passed": audit_info["passed"],
        "preset_used": preset.get("name", preset_name),
        "blind_mode": is_blind,
        "deliverable_relative_path": output_docx.relative_to(workspace_dir).as_posix(),
        "build": build_info,
        "audit": audit_info,
        **({"qingtian_gate": qa_gate} if qa_gate is not None else {}),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if audit_info["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
