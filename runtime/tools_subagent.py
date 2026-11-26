"""原子工具组 4（无锁并发子代理池）：按独立上下文与独立输出文件执行并行资料提取、并行章节撰写和独立审查。"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
from pathlib import Path
from typing import Any, Callable

from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.tool import ToolResponse, Toolkit

from runtime.model_factory import BoundedReActAgent, IterationBudgetExceededError
from runtime.tools_fs import WorkspaceFS

ROLE_PROMPTS = {
    "researcher": (
        "你是专注客观证据提取的调研子代理。只根据已解析的 `.workspace/parsed/` 文档或指定文件回答问题，"
        "所有提取出的要求、标准、数值必须标注准确来源文件名与页码（如 `[招标文件.pdf P14]`），绝不编造。"
    ),
    "author": (
        "你是专业内容撰稿子代理。请严格依据工作区内的 `01-requirements.md`（约束清单）、`02-plan.md`（规划大纲）"
        "以及源文档事实，使用干净规范的 Markdown（`#` 一级章标题、`##/###` 节标题、GFM 管道表格）撰写被分配的目标文件。"
        "严禁留下 TODO、XXX、待补充等任何占位词，严禁捏造未授权的数据。"
    ),
    "reviewer": (
        "你是独立挑刺审查子代理（Verifier）。你没有参与初稿编写，请保持客观严苛视角，"
        "逐条对照 `01-requirements.md` 与 `02-plan.md` 核查目标草稿：指出漏项、前后数值矛盾、空泛套话或无依据宣称，"
        "输出结构化缺陷清单与具体修改位置建议。"
    ),
}


def _wrap_sub_tool(fn: Callable[..., Any]) -> Callable[..., Any]:
    """将普通工作区方法包装为标准 AgentScope 工具响应函数，捕获参数错误转交子模型自纠。"""

    @functools.wraps(fn)
    async def _wrapped(*args, **kwargs):
        try:
            res = fn(*args, **kwargs)
            if inspect.isawaitable(res):
                res = await res
        except Exception as exc:
            res = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
        return ToolResponse(content=[{"type": "text", "text": json.dumps(res, ensure_ascii=False)}])

    return _wrapped


class SubAgentPoolTool:
    """管理并发子代理的物理文件隔离挂载、异步并发限流与精简结果汇总。"""

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
        """在受控信号量下运行单个隔离记忆的子代理，仅允许其写入自身声明的单一目标文件。"""
        task_id = str(spec.get("id") or "subtask").strip()
        title = str(spec.get("title") or task_id).strip()
        prompt = str(spec.get("prompt") or "").strip()
        role = str(spec.get("role") or "researcher").strip().lower()
        output_file = str(spec.get("output_file") or "").strip().replace("\\", "/").lstrip("/") or None
        max_iters = max(4, min(int(spec.get("max_iters") or 20), 36))

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
            tk.register_tool_function(_wrap_sub_tool(scoped_fs.scan_directory))
            tk.register_tool_function(_wrap_sub_tool(scoped_fs.search_text))
            tk.register_tool_function(_wrap_sub_tool(scoped_fs.read_lines))
            if output_file is not None:
                tk.register_tool_function(_wrap_sub_tool(scoped_fs.write_workspace_file))
                tk.register_tool_function(_wrap_sub_tool(scoped_fs.edit_workspace_file))

            sys_prompt = ROLE_PROMPTS.get(role, ROLE_PROMPTS["researcher"])
            if output_file:
                sys_prompt += f"\n\n【独占输出目标】你被授权且仅能写入工作区文件：`{output_file}`。完成任务前请确保已将完整成果写入该文件。"

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

            try:
                reply = await sub_agent(Msg("user", prompt, "user"))
                summary = (reply.get_text_content() or "").strip()
                status = "succeeded"
            except IterationBudgetExceededError as exc:
                summary = str(exc)
                status = "partial"
            except Exception as exc:
                summary = f"{type(exc).__name__}: {exc}"
                status = "failed"

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
                "summary": summary[:1500],
            }

    async def run_subagents(self, tasks: list[dict[str, Any]], max_concurrency: int = 3) -> dict[str, Any]:
        """
        并发启动 1~8 个拥有独立上下文记忆的子代理（用于多份文档并行提炼、多章节并行写稿或独立挑刺审查）。
        每个 `tasks` 元素包含：`id`、`title`、`prompt`、可选 `role` ('researcher'|'author'|'reviewer')、
        可选 `output_file`（如 'drafts/01-overview.md'，不同子任务的 output_file 必须互不相同）。
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
