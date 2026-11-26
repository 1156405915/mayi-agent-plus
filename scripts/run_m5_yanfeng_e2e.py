"""M5 真实大模型端到端验收驱动脚本：挂载延锋地块真实资料目录，完整驱动五步认知闭环产出标准施工组织设计 Word 文档。"""
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
    """读取用户已配置的模型凭据，在独立验收工作区跑通延锋地块五步全链路并打印脱敏轨迹（支持 --resume 断点续跑）。"""
    resume_mode = "--resume" in sys.argv
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8")

    settings_path = PROJECT_ROOT / ".mayi-plus-data" / "settings.json"
    if not settings_path.is_file():
        print("未找到 .mayi-plus-data/settings.json", file=sys.stderr)
        return 1

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    source_dir = Path(r"D:\Desktop\shizu\6.10（山水）延锋地块")
    m2_cache = PROJECT_ROOT / "test-temp" / "yanfeng-m2-audit" / ".workspace" / "parsed"
    m5_ws = PROJECT_ROOT / "test-temp" / "yanfeng-m5-live"

    if not resume_mode:
        if m5_ws.exists():
            shutil.rmtree(m5_ws)
        m5_ws.mkdir(parents=True, exist_ok=True)
        if m2_cache.is_dir():
            target_parsed = m5_ws / ".workspace" / "parsed"
            target_parsed.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(m2_cache, target_parsed)
            print("[M5 Init] 已预热挂载延锋地块 244 份资料内容寻址缓存。", flush=True)
    else:
        print("[M5 Resume] 从已有检查点与工作区成果无缝断点续跑...", flush=True)

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
                print(f"[{elapsed:6.1f}s] [TOOL START] {tname} -> {str(ex.get('input', ''))[:140]}", flush=True)
            else:
                dur = ex.get("durationMs", 0)
                print(f"[{elapsed:6.1f}s] [TOOL {st.upper()}] {tname} ({dur} ms)", flush=True)
        elif etype == "subagent_status":
            print(
                f"[{elapsed:6.1f}s] [SUBAGENT] {evt.get('id')} ({evt.get('title')}) -> {evt.get('status')}",
                flush=True,
            )
        elif etype == "gate":
            print(
                f"[{elapsed:6.1f}s] [GATE TRIGGERED] {evt.get('title')} | reviewFiles={evt.get('reviewFiles')}",
                flush=True,
            )
        elif etype == "log":
            print(f"[{elapsed:6.1f}s] [LOG] {evt.get('event')} {evt.get('metadata')}", flush=True)

    def on_gate_wait(gate_id: str) -> dict[str, Any]:
        req_exists = (m5_ws / "01-requirements.md").is_file()
        plan_exists = (m5_ws / "02-plan.md").is_file()
        print(
            f"[GATE CHECK] gate_id={gate_id[:8]} | 01-requirements.md={req_exists} | 02-plan.md={plan_exists} -> 自动确认放行！",
            flush=True,
        )
        return {
            "approved": True,
            "feedback": "提取依据与章节规划结构清晰，请继续生成高清工程配图、并发撰写全部规划章节、执行硬指标体检与独立挑刺审查，并编译输出标准 Word 成品。",
        }

    # --prompt "<文本>" 允许在续跑时注入人工验收反馈（如真实页数超限），模拟用户在工作台中追加指令。
    custom_prompt = sys.argv[sys.argv.index("--prompt") + 1] if "--prompt" in sys.argv else None
    prompt = custom_prompt or (
        "继续执行：当前工作区 `01-requirements.md`、`02-plan.md` 与 `assets/` 下的配图均已就绪。"
        "请立即使用 `run_subagents(role='author')` 按 `02-plan.md` 并发撰写全部 6 章 Markdown 草稿（`drafts/01-*.md` ~ `drafts/06-*.md`，嵌入已有 `assets/fig01_org.png` ~ `fig07_trench.png` 及结构化表格，总篇幅控制在合理精炼范围以满足 <=50 页约束），"
        "随后运行 `verify_drafts.py` 体检、派 `reviewer` 子代理核查修正，最后调用 `compile_word.py` 输出正式 `.docx` 至 `deliverables/` 目录。"
        if resume_mode
        else (
            "请基于当前挂载的“延锋地块东侧规划支路”全套招标、补疑、工程量清单与施工图设计说明资料，"
            "完整走完五步认知闭环，为我编制一份高质量、图文表齐全的《延锋地块东侧规划支路工程施工组织设计》（规划 6 章核心内容，含项目组织架构图、关键施工工艺流程图、总进度横道图及主要工程量/机械配置表），"
            "核查招标文件及补疑2的最新要求（工期、业绩调整、暗标或明标规定等），最终通过体检与审查后编译交付标准 .docx 文档。"
        )
    )

    request = {
        "workspace": str(m5_ws),
        "source_directory": str(source_dir),
        "prompt": prompt,
        "history": [],
        "config": {
            "api_key": settings["apiKey"],
            "base_url": settings.get("baseUrl") or "https://api.deepseek.com",
            "model": settings.get("model") or "deepseek-flash",
            "auto_approve_gates": False,
        },
    }

    result = asyncio.run(
        run_orchestrator_turn(
            request=request,
            project_root=PROJECT_ROOT,
            emit=on_event,
            wait_gate_response=on_gate_wait,
            auto_approve=False,
        )
    )

    total_sec = round(time.perf_counter() - started, 1)
    print(f"\n=== M5 END-TO-END COMPLETED in {total_sec}s | status={result['status']} ===", flush=True)
    print("Deliverables:", json.dumps(result.get("deliverables", []), ensure_ascii=False, indent=2), flush=True)
    print("\nAssistant Final Summary:\n" + result.get("text", "")[:2500], flush=True)
    return 0 if result["status"] == "completed" and result.get("deliverables") else 2


if __name__ == "__main__":
    raise SystemExit(main())
