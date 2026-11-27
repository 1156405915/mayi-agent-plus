"""M6 合肥“青天大模型”评标质检与施组高标准重修实战驱动脚本。

工作流程：
1. 自动备份 M5 普通初版章节草稿与初版 `.docx`，便于直观对比升级前后差异；
2. 清理工作区旧检查点与临时预览废图（`assets/_*.png`）；
3. 驱动真实大模型（`deepseek-flash`）加载升级版 `document-authoring` 技能（含 `qingtian-rubric.md`）：
   - 修正 `scripts/make_figs.py` 去除图内底部重复图号文字并重绘 7 张高清配图；
   - 对旧版草稿执行青天五维雷达扫描，按“工况痛点→定量参数与机械产能验算→现行国地标强条验收控制表”总工级三段式重修六章草稿；
   - 派发独立 `inspector`（合肥青天评标质检员）出具 `03-qingtian-review.md` 五维量化评分报告；
   - 通过 `qingtian_inspect.py --mode certify --min-score 90` 签发防篡改准入证；
   - 调用 `compile_word.py --preset hefei_qingtian_bidding --max-pages 50` 编译输出高标准正式 `.docx` 并通过本机真实排版页数核验。
"""
from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.orchestrator import run_orchestrator_turn


def main() -> int:
    """启动或断点续跑延锋地块施组“青天评标质检 + 总工级高标准重修”全流程。"""
    resume_mode = "--resume" in sys.argv
    custom_prompt = sys.argv[sys.argv.index("--prompt") + 1] if "--prompt" in sys.argv else None

    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    settings_path = PROJECT_ROOT / ".mayi-plus-data" / "settings.json"
    if not settings_path.is_file():
        print("未找到 .mayi-plus-data/settings.json", file=sys.stderr)
        return 1

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    source_dir = Path(r"D:\Desktop\shizu\6.10（山水）延锋地块")
    ws = PROJECT_ROOT / "test-temp" / "yanfeng-m5-live"

    if not resume_mode:
        # 1. 备份 M5 初版草稿与 Word 成品（仅在首次备份不存在时执行，防止覆盖原始对照样本）
        m5_drafts_bak = ws / ".workspace" / "m5_drafts_backup"
        if not m5_drafts_bak.exists() and (ws / "drafts").is_dir():
            shutil.copytree(ws / "drafts", m5_drafts_bak)
            print("[M6 Init] 已备份 M5 普通初版草稿至 .workspace/m5_drafts_backup/", flush=True)

        orig_docx = ws / "deliverables" / "延锋地块东侧规划支路工程施工组织设计.docx"
        bak_docx = ws / "deliverables" / "延锋地块东侧规划支路工程施工组织设计_M5初版备份.docx"
        if orig_docx.is_file() and not bak_docx.exists():
            shutil.copy2(orig_docx, bak_docx)
            print("[M6 Init] 已备份 M5 初版 Word 至 deliverables/..._M5初版备份.docx", flush=True)

        # 2. 清理临时测试预览图与旧会话检查点，确保开启全新高标准质检重修会话
        if (ws / "assets").is_dir():
            for scratch_png in (ws / "assets").glob("_*.png"):
                scratch_png.unlink()
        state_dir = ws / ".workspace" / ".state"
        if state_dir.is_dir():
            shutil.rmtree(state_dir)
        cert_file = ws / ".workspace" / "qingtian_certificate.json"
        if cert_file.is_file():
            cert_file.unlink()
        print("[M6 Init] 已清理旧检查点与旧证书，启动青天高标准质检重修闭环...", flush=True)
    else:
        print("[M6 Resume] 从最新检查点无缝续跑青天质检重修闭环...", flush=True)

    started = time.perf_counter()

    def on_event(evt: dict[str, Any]) -> None:
        elapsed = round(time.perf_counter() - started, 1)
        etype = evt.get("type")
        if etype == "steps":
            summary = " | ".join(f"{s['id']}:{s['status']}" for s in evt.get("steps", []))
            print(f"[{elapsed:6.1f}s] [STEPS] {summary}", flush=True)
        elif etype == "tool":
            ex = evt.get("execution", {})
            st = ex.get("status")
            tname = ex.get("toolName")
            if st == "running":
                print(f"[{elapsed:6.1f}s] [TOOL START] {tname} -> {str(ex.get('input', ''))[:150]}", flush=True)
            else:
                dur = ex.get("durationMs", 0)
                print(f"[{elapsed:6.1f}s] [TOOL {st.upper()}] {tname} ({dur} ms)", flush=True)
        elif etype == "subagent_status":
            print(
                f"[{elapsed:6.1f}s] [SUBAGENT] {evt.get('id')} ({evt.get('title')}) -> {evt.get('status')}",
                flush=True,
            )
        elif etype == "log":
            print(f"[{elapsed:6.1f}s] [LOG] {evt.get('event')} {evt.get('metadata')}", flush=True)

    def on_gate_wait(gate_id: str) -> dict[str, Any]:
        print(f"[GATE AUTO-APPROVE] gate_id={gate_id[:8]}", flush=True)
        return {"approved": True, "feedback": "请继续执行高标准重修与青天评标质检认证。"}

    default_prompt = (
        "当前工作区已有《延锋地块东侧规划支路工程施工组织设计》的事实基线（`00-facts.md`、`01-requirements.md`、`02-plan.md`）以及初版 `drafts/01~06` 草稿，"
        "但初版存在明显普通大模型通病（大段散文墙、“压实度按规范/温度达标”等模糊逃避词、缺少多工序定量产能验算、图内底部写死了错误图号且正文双重图题）。\n\n"
        "请你立即执行以下**合肥“青天大模型”高标准重修与严苛质检交付闭环**：\n"
        "1. 先调用 `load_skill` 加载 `document-authoring` 最新规程与 `qingtian-rubric.md` 标准，并运行一次 `qingtian_inspect.py --mode radar` 查看当前旧稿的具体硬伤基线；\n"
        "2. 修改 `scripts/make_figs.py` 并重新运行生成 `assets/fig01_org.png`~`fig07_trench.png`：**去掉图片内部底部带“图 X-Y”编号的 title 绘制**（因为 Word 编译器会根据 Markdown `![图 X-Y ...]` 自动生成唯一规范居中图题，图内再画图号会导致跨章错号与重复！同时修正 `fig02_pipe.png` 注释中 3.5m 沟槽误写必做专家论证的表述）；\n"
        "3. 使用 `run_subagents(role='author')` 对 `drafts/01-*.md` ~ `drafts/06-*.md` 全部 6 章进行**总工级高标准重修**（严格基于 `00-facts.md` 真实清单数据，严禁编造）：\n"
        "   - 每节采用“**工况锚定与痛点**（≤150字）→ **定量工艺步骤与机械台班产能匹配验算**（`- **工序**：...`，道路清表土方、雨水管道、水稳基层、沥青面层等核心工序必须列出 `清单量 ÷ 计划天数 = 日均强度 ≤ 拟投入机械台数 × 台班产能` 闭合算式）→ **现行国行地标强条与量化实测验收控制表**（GFM 表格，列明标准号如 `CJJ 1-2008`、`GB 50268-2008`、`JTG F40-2004`、`JGJ 46-2005`、`JGJ 59-2011`、`DB34/T 5040`、建设部令第37号、建质〔2018〕162号及精确数值阈值）”；\n"
        "   - **单段连续纯叙述文字严禁超过 280 字**（彻底消灭散文墙！），**零“按规范/温度达标/低于规定值”模糊词**，全篇统一规范术语“混凝土”；\n"
        "   - 插图统一采用**单行 `![图 X-Y 图题名](assets/figXX.png)`**，严禁在下方再手写第二行 `图 X-Y ...`；横道图保留 `<!-- page: landscape -->` 与 `<!-- page: portrait -->`；\n"
        "   - **严格控制全书总篇幅**：6 章合计总字符数控制在 24,000~28,000 字左右（高密度表格化精炼表达），确保真实 Word 排版页数稳定在 **43~48 页**（严禁超过招标 `≤50 页` 红线！）；\n"
        "4. 重修完成后，先跑 `qingtian_inspect.py --mode radar` 确保 `passed_radar: true`（若有残余散文墙/模糊词/双重图题立即定点修掉），随后调用 `run_subagents(role='inspector', output_file='03-qingtian-review.md')` 充当**合肥公共资源交易“青天大模型”评标质检专家**对 6 章逐章严苛审核并输出五维评分报告与末尾 JSON 评分卡；\n"
        "5. 运行 `qingtian_inspect.py --mode certify --min-score 90`：若未达 90 分或存在阻断缺陷，必须按质检处方打回重修并复审，直至签发 `.workspace/qingtian_certificate.json`（`passed: true`）；\n"
        "6. 最后调用 `compile_word.py`（`--drafts-dir drafts --output deliverables/延锋地块东侧规划支路工程施工组织设计.docx --title 延锋地块东侧规划支路工程施工组织设计 --preset hefei_qingtian_bidding --max-pages 50`）输出正式高标准 Word 成品（若真实页数超 50 页，精简后须重跑 `certify` 更新 SHA-256 证书再编译）。"
    )

    prompt = custom_prompt or (
        "请继续推进未完成的青天质检重修、认证（`--mode certify` ≥90分）与正式 Word 编译（`--preset hefei_qingtian_bidding --max-pages 50`），确保最终 `passed: true`。"
        if resume_mode
        else default_prompt
    )

    request = {
        "workspace": str(ws),
        "source_directory": str(source_dir),
        "prompt": prompt,
        "history": [],
        "config": {
            "api_key": settings["apiKey"],
            "base_url": settings.get("base_url") or settings.get("baseUrl") or "https://api.deepseek.com",
            "model": settings.get("model") or "deepseek-flash",
            "auto_approve_gates": True,
        },
    }

    # CLI 批处理模式下若触发单轮 80 步推理预算暂停（status == "paused"），自动无缝续跑最多 3 轮直至彻底完成交付。
    result: dict[str, Any] = {}
    for turn_idx in range(1, 4):
        if turn_idx > 1:
            print(f"\n[M6 Auto-Continue] 触发单次轮次预算暂停，自动进入第 {turn_idx} 轮无缝续跑...", flush=True)
            request["prompt"] = (
                "继续执行剩余收尾步骤：确保 `qingtian_inspect.py --mode radar` 零硬伤、完成 `inspector` 子代理产出 `03-qingtian-review.md`、"
                "通过 `qingtian_inspect.py --mode certify --min-score 90` 签发准入证，并调用 `compile_word.py`（`--preset hefei_qingtian_bidding --max-pages 50`）输出最终达标 `.docx`。"
            )
        result = asyncio.run(
            run_orchestrator_turn(
                request=request,
                project_root=PROJECT_ROOT,
                emit=on_event,
                wait_gate_response=on_gate_wait,
                auto_approve=True,
            )
        )
        if result.get("status") != "paused":
            break

    total_sec = round(time.perf_counter() - started, 1)
    print(f"\n=== M6 QINGTIAN UPGRADE FINISHED in {total_sec}s | status={result['status']} ===", flush=True)
    print("Deliverables:", json.dumps(result.get("deliverables", []), ensure_ascii=False, indent=2), flush=True)
    print("\nAssistant Final Summary:\n" + result.get("text", "")[:3000], flush=True)
    return 0 if result["status"] == "completed" and result.get("deliverables") else 2


if __name__ == "__main__":
    raise SystemExit(main())
