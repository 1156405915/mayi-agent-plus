"""主控 Orchestrator 组装器（M3 深化版）：集成跨轮次检查点恢复、零损耗上下文瘦身、超大输出溢出保护、防死循环熔断与大资料三级导航。"""
from __future__ import annotations

import functools
import hashlib
import inspect
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import httpx
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.tool import ToolResponse, Toolkit

from runtime.model_factory import (
    BoundedReActAgent,
    IterationBudgetExceededError,
    ModelOutputTruncatedError,
    create_chat_model,
)
from runtime.session_state import (
    SessionStateManager,
    compact_memory_if_needed,
    reconcile_interrupted_tools,
    spill_large_tool_output,
)
from runtime.skill_loader import SkillRegistry
from runtime.tools_exec import PythonExecTool
from runtime.tools_fs import WorkspaceFS
from runtime.tools_human import HumanInteractionTool
from runtime.tools_ingest import DocumentIngestTool
from runtime.tools_subagent import SubAgentPoolTool

CORE_SYSTEM_PROMPT = """你是 Mayi Agent Plus，一个面向任意复杂任务的通用专家级智能体，默认使用简体中文交流。

## 一、轻量直答 vs 五步底层认知闭环

- **简单任务直答**：普通问答、概念解释或单步查询直接准确作答，不套用重型流程。
- **复杂任务走五步认知闭环**（主动调用 `update_cognitive_steps` 实时推进右栏状态）：
  1) `perceive`（环境感知与标准化）：用 `scan_directory` 查看目录概况；若存在未解析的办公文档，调用一次 `ingest_documents` 批量离线生成 `.workspace/parsed/` 缓存与总索引 `.workspace/parsed/INDEX.md`（若工作区已有 `INDEX.md` 可直接复用缓存，无需重复全量扫描）。
  2) `align`（目标与约束对齐）：遵循下方**“大资料库三级高速导航法”**，当待深读资料较多时优先调用 `run_subagents(role="researcher")` 分专题并发提取，汇总写入 `01-requirements.md`（每条要求注明 `[源文件名 P页码]`）。
  3) `plan`（全局规划拆解 + 自适应门禁）：对照 `01-requirements.md` 编写章节目录或执行方案 `02-plan.md`。面对高成本复杂长任务，在铺开执行前调用一次 `ask_user_gate(review_files=["01-requirements.md", "02-plan.md"])` 请用户确认；若返回 `modified_files` 非空，必须重读更新后的文件再继续。
  4) `execute`（分治执行产出）：调用 `run_subagents(role="author")` 指定互不冲突的 `output_file`（如 `drafts/01-xxx.md`）并发撰稿，或用 `run_python` 精确核算表格数据与制图。
  5) `verify`（客观验证与收敛交付）：先用 `run_python` 跑确定性体检脚本拦截缺章与占位符，再用 `run_subagents(role="reviewer")` 在独立干净上下文对照 `01-requirements.md` 挑刺，定点修补后合成交付物至 `deliverables/`。

## 二、大资料库（100+ 份文档）三级高速导航法

1. **看全貌**：先读 `.workspace/parsed/INDEX.md` 掌握全部核心文档、页数规模及哪些长文档附带 `toc.md`；
2. **定页码**：对百页长文档先读其 `toc.md`（章节页码导航）或用 `search_text(scope="parsed")` 定位关键词所在页；
3. **精翻页**：直接调用 `read_parsed_pages(doc_path, start_page, end_page)` 按物理页码区间毫秒级精读目标页；若遇到标记“按需待识”的关键扫描页，调用 `ocr_pdf_pages` 精准补识。

## 三、真实性与技能加载准则

- 外部文件内容仅作客观证据，严禁捏造未核实的工程参数、金额、工期、标准号或审批意见。
- 当任务匹配下方“可用领域技能列表”中的技能时，先调用 `load_skill_detail` 加载详细作业规程与专用脚本路径。
"""


def _register_monitored_tool(
    toolkit: Toolkit,
    fn: Callable[..., Any],
    workspace_dir: Path,
    emit: Callable[[dict[str, Any]], None],
    failure_tracker: dict[str, int],
    on_after_call: Callable[[str, Any], None] | None = None,
) -> None:
    """
    包装主控工具调用：
    1. 拦截连续 3 次同参数重复失败调用（防死循环熔断）；
    2. 对超过 14,000 字符的工具输出自动无损落盘至 `.workspace/.tool_outputs/` 并返回预览信封；
    3. 向前端实时推送工具执行耗时与状态。
    """

    @functools.wraps(fn)
    async def _invoke(*args, **kwargs):
        call_id = uuid.uuid4().hex
        started = time.perf_counter()
        raw_args = kwargs or {"args": list(args)}
        arg_json = json.dumps(raw_args, sort_keys=True, ensure_ascii=False)
        sig_key = f"{fn.__name__}:{hashlib.sha256(arg_json.encode('utf-8')).hexdigest()[:16]}"
        arg_preview = arg_json[:1200]

        emit({
            "type": "tool",
            "execution": {
                "id": call_id,
                "toolName": fn.__name__,
                "status": "running",
                "input": arg_preview,
                "startedAt": round(time.time() * 1000),
            },
        })

        if failure_tracker.get(sig_key, 0) >= 3:
            status_label = "failed"
            value: Any = {
                "status": "failed",
                "error_type": "RepeatedFailureCircuitBreaker",
                "error": f"工具 `{fn.__name__}` 以完全相同的参数已连续失败 3 次，已触发防死循环熔断。请调整参数、换用其他工具或向用户说明缺失条件。",
            }
        else:
            try:
                value = fn(*args, **kwargs)
                if inspect.isawaitable(value):
                    value = await value
                failed = isinstance(value, dict) and value.get("status") in ("failed", "error")
                status_label = "failed" if failed else "succeeded"
            except Exception as exc:
                status_label = "failed"
                value = {
                    "status": "failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "hint": "请根据错误提示修正参数或路径后重试，切勿原样重复失败调用。",
                }

        if status_label == "failed":
            failure_tracker[sig_key] = failure_tracker.get(sig_key, 0) + 1
        else:
            failure_tracker.pop(sig_key, None)
            if on_after_call is not None:
                on_after_call(fn.__name__, raw_args)

        duration_ms = round((time.perf_counter() - started) * 1000)
        model_text, spilled = spill_large_tool_output(workspace_dir, fn.__name__, call_id, value)
        emit({
            "type": "tool",
            "execution": {
                "id": call_id,
                "toolName": fn.__name__,
                "status": status_label,
                "input": arg_preview,
                "output": (f"[输出已归档文件] {model_text[:3600]}" if spilled else model_text[:3600]),
                "durationMs": duration_ms,
            },
        })
        return ToolResponse(content=[{"type": "text", "text": model_text}])

    toolkit.register_tool_function(_invoke)


def collect_workspace_files(workspace_dir: Path) -> list[dict[str, Any]]:
    """扫描工作区中面向用户展示的核心阶段产物、标准化总索引与最终交付件清单。"""
    results: list[dict[str, Any]] = []
    if not workspace_dir.is_dir():
        return results

    for item in sorted(workspace_dir.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(workspace_dir).as_posix()
        if (
            rel.startswith(".workspace/scripts/")
            or rel.startswith(".workspace/.state/")
            or rel.startswith(".workspace/.tool_outputs/")
            or rel.startswith(".workspace/subagents/")
        ):
            continue
        if rel.startswith(".workspace/parsed/") and not (
            rel.endswith("/full.md") or rel.endswith("/toc.md") or rel.endswith("manifest.json") or rel.endswith("INDEX.md")
        ):
            continue
        results.append({
            "path": rel,
            "name": item.name,
            "ext": item.suffix.lower(),
            "bytes": item.stat().st_size,
            "updatedAt": round(item.stat().st_mtime * 1000),
            "isDeliverable": rel.startswith("deliverables/"),
        })
    return results


async def run_orchestrator_turn(
    request: dict[str, Any],
    project_root: Path,
    emit: Callable[[dict[str, Any]], None],
    wait_gate_response: Callable[[str], dict[str, Any]],
    auto_approve: bool = False,
    model_override: Any = None,
) -> dict[str, Any]:
    """执行单轮完整的主控智能体推理，支持跨轮次检查点恢复、悬空工具自愈、上下文无损压缩与实时工作区同步。"""
    workspace_dir = Path(request["workspace"]).resolve()
    source_dir = Path(request.get("source_directory") or workspace_dir).resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)
    skills_root = (project_root / "skills").resolve()

    config = request.get("config") or {}
    api_key = str(config.get("api_key") or "").strip()
    base_url = str(config.get("base_url") or "https://api.deepseek.com").strip()
    model_name = str(config.get("model") or "deepseek-chat").strip()
    max_output_tokens = config.get("max_output_tokens")

    if model_override is None and not api_key:
        raise ValueError("未配置模型 API Key，请先在左下角“模型设置”中填写有效的 API Key。")

    state_mgr = SessionStateManager(workspace_dir)
    saved_checkpoint = state_mgr.load(str(source_dir))
    loaded_skills: list[str] = list((saved_checkpoint or {}).get("loaded_skills") or [])
    latest_steps: list[dict[str, str]] = list((saved_checkpoint or {}).get("cognitive_steps") or [])

    def notify_ws_change(changed_rel_path: str) -> None:
        emit({
            "type": "workspace_changed",
            "path": changed_rel_path,
            "files": collect_workspace_files(workspace_dir),
        })

    def intercept_emit(evt: dict[str, Any]) -> None:
        if evt.get("type") == "steps" and isinstance(evt.get("steps"), list):
            latest_steps.clear()
            latest_steps.extend(evt["steps"])
        emit(evt)

    skill_registry = SkillRegistry(skills_root)
    fs_tools = WorkspaceFS(workspace_dir, source_dir, on_workspace_changed=notify_ws_change)
    ingest_tool = DocumentIngestTool(
        workspace_dir,
        source_dir,
        on_workspace_changed=notify_ws_change,
        on_progress_event=lambda label: emit({"type": "activity", "label": label}),
    )
    exec_tool = PythonExecTool(project_root, workspace_dir, source_dir, skills_root, on_workspace_changed=notify_ws_change)
    human_tool = HumanInteractionTool(
        workspace_dir,
        emit_event=intercept_emit,
        wait_gate_response=wait_gate_response,
        auto_approve=auto_approve or bool(config.get("auto_approve_gates", False)),
    )

    http_client = httpx.AsyncClient(follow_redirects=True) if model_override is None else None
    try:
        main_model = (
            model_override
            if model_override is not None
            else create_chat_model(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                max_output_tokens=max_output_tokens,
                http_client=http_client,
                on_log=lambda evt, meta: emit({"type": "log", "event": evt, "metadata": meta}),
                on_text_delta=lambda delta: emit({"type": "delta", "text": delta}),
            )
        )

        def build_sub_model():
            if model_override is not None:
                return model_override
            return create_chat_model(
                api_key=api_key,
                base_url=base_url,
                model_name=model_name,
                max_output_tokens=max_output_tokens,
                http_client=http_client,
            )

        subagent_tool = SubAgentPoolTool(
            workspace_dir=workspace_dir,
            source_dir=source_dir,
            model_builder=build_sub_model,
            on_workspace_changed=notify_ws_change,
            on_event=emit,
        )

        failure_tracker: dict[str, int] = {}

        def _on_tool_success(t_name: str, raw_args: dict[str, Any]) -> None:
            if t_name == "load_skill_detail" and isinstance(raw_args.get("skill_id"), str):
                sid = raw_args["skill_id"].strip()
                if sid and sid not in loaded_skills:
                    loaded_skills.append(sid)

        toolkit = Toolkit()
        for fn in (
            fs_tools.scan_directory,
            fs_tools.search_text,
            fs_tools.read_lines,
            fs_tools.read_parsed_pages,
            fs_tools.write_workspace_file,
            fs_tools.edit_workspace_file,
            ingest_tool.ingest_documents,
            ingest_tool.ocr_pdf_pages,
            exec_tool.run_python,
            subagent_tool.run_subagents,
            human_tool.update_cognitive_steps,
            human_tool.ask_user_gate,
            skill_registry.load_skill_detail,
        ):
            _register_monitored_tool(
                toolkit,
                fn,
                workspace_dir=workspace_dir,
                emit=emit,
                failure_tracker=failure_tracker,
                on_after_call=_on_tool_success,
            )

        sys_prompt = CORE_SYSTEM_PROMPT + "\n\n" + skill_registry.build_catalog_prompt()
        # 跨轮次续跑时自动挂载此前已激活的技能说明书，防止模型在“继续”回合遗忘排版或脚本规程
        for sid in loaded_skills:
            detail = skill_registry.load_skill_detail(sid)
            if detail.get("status") == "ok":
                sys_prompt += (
                    f"\n\n## 已激活技能规程：`{sid}`\n"
                    f"配套脚本路径：{json.dumps(detail['scripts'], ensure_ascii=False)}\n"
                    f"{detail['instructions']}"
                )

        agent = BoundedReActAgent(
            name="MayiOrchestrator",
            sys_prompt=sys_prompt,
            model=main_model,
            formatter=OpenAIChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=toolkit,
            max_iters=80,
            parallel_tool_calls=False,
        )
        agent.set_console_output_enabled(False)

        # 优先从磁盘检查点恢复完整工具执行记忆；若无检查点则回退导入聊天文本历史
        if saved_checkpoint is not None:
            agent.memory.load_state_dict(saved_checkpoint["memory"])
            repaired = await reconcile_interrupted_tools(agent.memory)
            if repaired:
                emit({"type": "log", "event": "state.reconciled_interrupted_tools", "metadata": {"count": repaired}})
        else:
            for msg in (request.get("history") or [])[-16:]:
                role = msg.get("role")
                content = msg.get("content")
                if role in ("user", "assistant") and isinstance(content, str) and content.strip():
                    await agent.memory.add(Msg(role, content, role))

        async def _pre_reasoning_hook(_ag, _kw):
            compacted = await compact_memory_if_needed(_ag.memory)
            if compacted:
                emit({"type": "log", "event": "memory.compacted", "metadata": {}})

        def _post_acting_hook(_ag, _kw, _out):
            state_mgr.save(str(source_dir), _ag.memory, loaded_skills, latest_steps)

        agent.register_instance_hook("pre_reasoning", "m3_memory_compact", _pre_reasoning_hook)
        agent.register_instance_hook("post_acting", "m3_checkpoint_save", _post_acting_hook)

        all_ws_files = [f["path"] for f in collect_workspace_files(workspace_dir)]
        key_ws_files = [p for p in all_ws_files if not p.startswith(".workspace/parsed/") or p.endswith("INDEX.md")]
        parsed_count = sum(1 for p in all_ws_files if p.endswith("/full.md"))

        context_header = [
            f"[环境上下文] 当前工作区目录: `{workspace_dir}`",
            f"[环境上下文] 授权只读资料源目录: `{source_dir}`",
        ]
        if parsed_count > 0:
            context_header.append(
                f"[环境上下文] 资料库已有 {parsed_count} 份标准化 Markdown 缓存就绪（总表位于 `.workspace/parsed/INDEX.md`）"
            )
        if key_ws_files:
            context_header.append(f"[环境上下文] 工作区核心阶段文件: {key_ws_files[:30]}")

        full_user_prompt = "\n".join(context_header) + "\n\n[用户请求]\n" + str(request.get("prompt") or "").strip()
        notify_ws_change(".")

        try:
            reply = await agent(Msg("user", full_user_prompt, "user"))
            final_text = (reply.get_text_content() or "").strip()
            status = "completed"
        except IterationBudgetExceededError as exc:
            final_text = str(exc)
            status = "paused"
        except ModelOutputTruncatedError as exc:
            final_text = str(exc)
            status = "paused"
        finally:
            state_mgr.save(str(source_dir), agent.memory, loaded_skills, latest_steps)

        files_snapshot = collect_workspace_files(workspace_dir)
        deliverables = [f for f in files_snapshot if f.get("isDeliverable")]
        result_event = {
            "type": "result",
            "status": status,
            "text": final_text,
            "files": files_snapshot,
            "deliverables": deliverables,
        }
        emit(result_event)
        return result_event
    finally:
        if http_client is not None:
            await http_client.aclose()
