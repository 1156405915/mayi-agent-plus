"""原子工具组 4（无锁并发子代理池）：支持共享底座快照自动注入、物理文件隔离并发撰稿、独立挑刺审查与完整报告留档。"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
import uuid
from pathlib import Path
from typing import Any, Callable

from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.tool import ToolResponse, Toolkit

from runtime.model_factory import BoundedReActAgent, IterationBudgetExceededError
from runtime.session_state import compact_memory_if_needed, spill_large_tool_output
from runtime.tools_fs import WorkspaceFS

ROLE_PROMPTS = {
    "researcher": (
        "你是专注客观证据提炼的调研子代理。优先通过 `search_text`、`read_parsed_pages`（支持按页码直读）或查阅 `toc.md` 定位原文，"
        "所有提炼出的要求、标准、金额、工期、工程量必须附上准确来源定位（如 `[招标文件正文.pdf P5]`），严禁主观臆测。"
    ),
    "author": (
        "你是高标准专业技术撰稿子代理。请严格对齐已注入的事实基线（`00-facts.md`）、全局约束清单（`01-requirements.md`）与规划大纲（`02-plan.md`），"
        "采用“工况痛点/重难点定界 → 定量工艺参数与机械产能验算 → 现行国行地标强条验收控制表”的高密度专业结构撰写被分配的独占章节。"
        "严禁堆砌超过 300 字不分段的大段散文墙，严禁用“按规范要求/合理安排/高度重视/确保达标”等万能套话糊弄关键技术指标，严禁出现 TODO、XXX、待补充等占位词。"
    ),
    "reviewer": (
        "你是独立挑刺审查子代理（Verifier）。你未参与初稿撰写，请保持严苛客观视角，逐条对照 `00-facts.md`、`01-requirements.md` 与 `02-plan.md`"
        "核查各章草稿是否存在漏响应评分点、前后工期/金额/工程量口径矛盾或空洞套话，输出具体文件、位置与修复建议。"
    ),
    "inspector": (
        "你是独立首席评标质检官（QA Inspector）。你完全独立于撰稿团队，负责以严苛的智能辅助评标算法与资深评审专家双重标准，"
        "结合确定性雷达扫描数据对全套章节草稿进行逐章穿透式审查与五维量化打分。"
        "凡发现漏项跳号、大段散文墙、套话口号、“按规范”模糊逃避词、缺少国地标编号与定量产能验算、或跨章数据打架，必须据实扣分并开具精确到文件与小节的强制返工处方；"
        "未达到高分准入标准前绝不放行。"
    ),
}


def _wrap_sub_tool(workspace_dir: Path, fn: Callable[..., Any]) -> Callable[..., Any]:
    """包装子代理工具调用，内置异常自纠捕获与超长输出自动溢出保护。"""

    @functools.wraps(fn)
    async def _wrapped(*args, **kwargs):
        call_id = uuid.uuid4().hex
        try:
            res = fn(*args, **kwargs)
            if inspect.isawaitable(res):
                res = await res
        except Exception as exc:
            res = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        text_payload, _spilled = spill_large_tool_output(workspace_dir, fn.__name__, call_id, res, max_inline_chars=12000)
        return ToolResponse(content=[{"type": "text", "text": text_payload}])

    return _wrapped


def _build_shared_briefing(workspace_dir: Path, role: str) -> str:
    """
    在子代理启动时自动注入精简的工作区冻结底座摘要，省去每个子代理前 3~4 轮重复探路读取开销，
    同时确保所有并行撰稿子代理对项目名称、工期、造价等核心口径 100% 一致。
    """
    sections: list[str] = []

    if role == "researcher":
        manifest_file = workspace_dir / ".workspace" / "parsed" / "manifest.json"
        if manifest_file.is_file():
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                docs = [d for d in data.get("documents", []) if d.get("status") == "ready"]
                core_docs = [
                    f"- `{d['source_path']}` ({d.get('unit_count')} {d.get('unit_kind')})"
                    + (f" [目录导航: `{d['toc_markdown']}`]" if d.get("toc_markdown") else "")
                    for d in docs[:30]
                ]
                if core_docs:
                    sections.append(
                        f"【已就绪资料库速览（共 {len(docs)} 份，全表见 `.workspace/parsed/INDEX.md`）】\n"
                        + "\n".join(core_docs)
                    )
            except (OSError, ValueError, TypeError):
                pass
    else:
        # 自动注入事实基线、需求约束与全局规划，消除并行撰稿与质检子代理的口径漂移。
        for fname, label in (
            ("00-facts.md", "唯一事实与工程量数据基线"),
            ("01-requirements.md", "全局目标与约束底座"),
            ("02-plan.md", "全局目录与规划底座"),
        ):
            fp = workspace_dir / fname
            if fp.is_file():
                content = fp.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    clipped = content[:8500] + ("\n...[后文可调用 read_lines 继续查阅]" if len(content) > 8500 else "")
                    sections.append(f"【{label} (`{fname}`)】\n{clipped}")

        # 对审查/质检角色自动附带最新的确定性雷达体检快照，辅助精准定位套话、散文墙与规范缺口。
        if role in ("inspector", "reviewer"):
            radar_fp = workspace_dir / ".workspace" / "qingtian_radar.json"
            if radar_fp.is_file():
                radar_raw = radar_fp.read_text(encoding="utf-8", errors="replace").strip()
                if radar_raw:
                    sections.append(
                        "【确定性青天雷达扫描快照 (`.workspace/qingtian_radar.json`)】\n"
                        + radar_raw[:9000]
                    )

    return "\n\n".join(sections)


class SubAgentPoolTool:
    """管理并发子代理的物理文件隔离挂载、共享底座自动注入、完整报告留档与并发限流。"""

    def __init__(
        self,
        workspace_dir: Path,
        source_dir: Path,
        model_builder: Callable[[], Any],
        on_workspace_changed: Callable[[str], None] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.workspace_dir = workspace_dir.resolve()
        self.source_dir = source_dir.resolve()
        self.model_builder = model_builder
        self.on_workspace_changed = on_workspace_changed
        self.on_event = on_event

    async def _run_one(self, spec: dict[str, Any], sem: asyncio.Semaphore) -> dict[str, Any]:
        """在信号量限流下运行单个独立记忆子代理，并将子代理完整原始答复归档至 `.workspace/subagents/`。"""
        task_id = str(spec.get("id") or f"sub_{uuid.uuid4().hex[:6]}").strip()
        title = str(spec.get("title") or task_id).strip()
        prompt = str(spec.get("prompt") or "").strip()
        role = str(spec.get("role") or "researcher").strip().lower()
        custom_role_prompt = str(spec.get("role_prompt") or "").strip()
        output_file = str(spec.get("output_file") or "").strip().replace("\\", "/").lstrip("/") or None
        default_iters = 28 if role in ("inspector", "reviewer") else 22
        max_iters = max(4, min(int(spec.get("max_iters") or default_iters), 40))

        if not prompt:
            return {"id": task_id, "title": title, "status": "failed", "error": "子任务 prompt 不能为空"}

        async with sem:
            if self.on_event:
                self.on_event({"type": "subagent_status", "id": task_id, "title": title, "status": "running"})

            scoped_fs = WorkspaceFS(
                workspace_dir=self.workspace_dir,
                source_dir=self.source_dir,
                on_workspace_changed=self.on_workspace_changed,
                allowed_write_file=output_file,
            )
            tk = Toolkit()
            tk.register_tool_function(_wrap_sub_tool(self.workspace_dir, scoped_fs.scan_directory))
            tk.register_tool_function(_wrap_sub_tool(self.workspace_dir, scoped_fs.search_text))
            tk.register_tool_function(_wrap_sub_tool(self.workspace_dir, scoped_fs.read_lines))
            tk.register_tool_function(_wrap_sub_tool(self.workspace_dir, scoped_fs.read_parsed_pages))
            if role == "researcher":
                from runtime.tools_ingest import DocumentIngestTool

                scoped_ingest = DocumentIngestTool(
                    self.workspace_dir, self.source_dir, on_workspace_changed=self.on_workspace_changed
                )
                tk.register_tool_function(_wrap_sub_tool(self.workspace_dir, scoped_ingest.ocr_pdf_pages))

            if output_file is not None:
                tk.register_tool_function(_wrap_sub_tool(self.workspace_dir, scoped_fs.write_workspace_file))
                tk.register_tool_function(_wrap_sub_tool(self.workspace_dir, scoped_fs.edit_workspace_file))

            sys_prompt = ROLE_PROMPTS.get(role, ROLE_PROMPTS["researcher"])
            if custom_role_prompt:
                sys_prompt += "\n\n【领域专项角色规程】\n" + custom_role_prompt
            briefing = _build_shared_briefing(self.workspace_dir, role)
            if briefing:
                sys_prompt += "\n\n" + briefing
            if output_file:
                sys_prompt += f"\n\n【独占输出契约】你仅被授权写入工作区文件：`{output_file}`，结束前必须调用 `write_workspace_file` 将完整成果落盘。"

            sub_agent = BoundedReActAgent(
                name=f"SubAgent_{task_id}",
                sys_prompt=sys_prompt,
                model=self.model_builder(),
                formatter=OpenAIChatFormatter(),
                memory=InMemoryMemory(),
                toolkit=tk,
                max_iters=max_iters,
                parallel_tool_calls=False,
            )
            sub_agent.set_console_output_enabled(False)

            sub_round = 0

            async def _pre_sub_reasoning(_ag, _kw):
                nonlocal sub_round
                sub_round += 1
                await compact_memory_if_needed(_ag.memory, trigger_chars=42000, keep_recent_messages=8)
                if output_file and sub_round == max_iters - 2:
                    await _ag.memory.add(
                        Msg(
                            "system",
                            f"[宿主收尾提醒] 当前子任务仅剩最后 2 轮推理预算！请立即停止继续搜索，马上调用 `write_workspace_file` 将目前已掌握的完整内容写入 `{output_file}`。",
                            "system",
                        )
                    )

            sub_agent.register_instance_hook("pre_reasoning", "sub_compact", _pre_sub_reasoning)

            try:
                reply = await sub_agent(Msg("user", prompt, "user"))
                full_reply_text = (reply.get_text_content() or "").strip()
                status = "succeeded"
            except IterationBudgetExceededError as exc:
                full_reply_text = str(exc)
                status = "partial"
            except Exception as exc:
                full_reply_text = f"{type(exc).__name__}: {exc}"
                status = "failed"

            # 无损归档子代理完整回复，防止长篇调研结论在回传主控摘要时丢失细节
            reports_dir = self.workspace_dir / ".workspace" / "subagents"
            reports_dir.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in task_id)[:40]
            report_rel = f".workspace/subagents/{safe_id}.md"
            (self.workspace_dir / report_rel).write_text(
                f"# 子任务报告：{title} (`{status}`)\n\n{full_reply_text}\n",
                encoding="utf-8",
            )

            file_info: dict[str, Any] | None = None
            if output_file:
                out_path = (self.workspace_dir / output_file).resolve()
                exists = out_path.is_file()
                chars = len(out_path.read_text(encoding="utf-8", errors="replace")) if exists else 0
                file_info = {"path": output_file, "exists": exists, "characters": chars}
                if status == "succeeded" and (not exists or chars == 0):
                    status = "partial"

            if self.on_event:
                self.on_event({"type": "subagent_status", "id": task_id, "title": title, "status": status})

            return {
                "id": task_id,
                "title": title,
                "role": role,
                "status": status,
                "output_file": file_info,
                "report_file": report_rel,
                "summary_truncated": len(full_reply_text) > 1800,
                "summary": full_reply_text[:1800],
            }

    async def run_subagents(self, tasks: list[dict[str, Any]], max_concurrency: int = 3) -> dict[str, Any]:
        """
        并发启动 1~8 个拥有独立上下文记忆的子代理（用于多份文档并行提炼、多章节并行写稿或独立挑刺审查）。
        每个 `tasks` 元素包含：`id`、`title`、`prompt`、可选 `role` ('researcher'|'author'|'reviewer'|'inspector')、
        可选 `role_prompt`（由具体 Skill 注入的专项评审/撰稿人设规程）、
        可选 `output_file`（如 'drafts/01-overview.md' 或 '03-qingtian-review.md'，不同子任务的 output_file 必须互不相同）。
        """
        if not tasks or len(tasks) > 8:
            raise ValueError("单次 `run_subagents` 请传入 1 至 8 个独立子任务")

        declared_outputs = [
            str(t.get("output_file")).strip().replace("\\", "/").lstrip("/")
            for t in tasks
            if t.get("output_file")
        ]
        if len(declared_outputs) != len(set(declared_outputs)):
            raise ValueError("同一批次并发子任务声明了重复的 `output_file`，请确保一人一文件物理隔离。")

        sem = asyncio.Semaphore(max(1, min(int(max_concurrency), 4)))
        results = await asyncio.gather(*(self._run_one(spec, sem) for spec in tasks))

        return {
            "total": len(results),
            "succeeded": sum(1 for r in results if r["status"] == "succeeded"),
            "partial": sum(1 for r in results if r["status"] == "partial"),
            "failed": sum(1 for r in results if r["status"] == "failed"),
            "results": results,
        }
