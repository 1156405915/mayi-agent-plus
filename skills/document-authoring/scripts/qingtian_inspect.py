"""合肥“青天大模型”技术标质检雷达与准入证签发 CLI 脚本。

支持两种运行模式：
1. `--mode radar`：对 `drafts/*.md` 执行确定性五维雷达扫描（结构断链、散文墙、套话率、“按规范”模糊词、国标引用、定量产能验算、双重图题），
   并将结果持久化写入 `.workspace/qingtian_radar.json`（供 `inspector` 子代理自动读取）；
2. `--mode certify`：结合确定性雷达与 `03-qingtian-review.md` 中的 AI 类人评标五维评分卡，
   检验是否满足“0 硬伤 + 综合得分 >= 90 分”，达标后签发 SHA-256 绑定的 `.workspace/qingtian_certificate.json` 放行准入证。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.doc_engine.qingtian_auditor import certify_qingtian_quality, run_qingtian_radar


def main() -> int:
    """解析命令行参数并执行青天雷达扫描或准入证终审签发。"""
    parser = argparse.ArgumentParser(description="Hefei Qingtian LLM Bid-Evaluation QA Radar & Certification Gate")
    parser.add_argument("--mode", choices=("radar", "certify"), default="radar", help="运行模式：radar 预检扫描 | certify 终审签发准入证")
    parser.add_argument("--drafts-dir", default="drafts", help="相对于工作区的章节草稿目录")
    parser.add_argument("--review-file", default="03-qingtian-review.md", help="inspector 质检子代理输出的评标报告路径")
    parser.add_argument("--min-score", type=float, default=90.0, help="准入最低综合得分门槛（默认 90.0 分）")
    parser.add_argument("--blind", action="store_true", help="启用技术暗标匿名合规红线")
    args = parser.parse_args()

    workspace_dir = Path(os.environ.get("MAYI_WORKSPACE_DIR") or Path.cwd()).resolve()
    drafts_dir = (workspace_dir / args.drafts_dir).resolve()
    if not drafts_dir.is_dir():
        print(json.dumps({"passed": False, "error": f"未找到草稿目录: {args.drafts_dir}"}, ensure_ascii=False))
        return 1

    draft_files = sorted(p for p in drafts_dir.glob("*.md") if p.is_file())
    if args.mode == "radar":
        report = run_qingtian_radar(draft_files, workspace_dir=workspace_dir, blind_mode=args.blind)
        meta_dir = workspace_dir / ".workspace"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / "qingtian_radar.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["passed_radar"] else 2

    cert_result = certify_qingtian_quality(
        workspace_dir=workspace_dir,
        draft_files=draft_files,
        review_rel_path=args.review_file,
        min_score=args.min_score,
        blind_mode=args.blind,
    )
    print(json.dumps(cert_result, ensure_ascii=False, indent=2))
    return 0 if cert_result["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
