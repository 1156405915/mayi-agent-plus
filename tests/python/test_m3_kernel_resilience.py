"""M3 薄内核深度加固测试：覆盖三级页码导航、大结果溢出保护、中断检查点自愈、确定性上下文瘦身、防死循环熔断与子代理底座注入。"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import ChatModelBase, ChatResponse

from runtime.orchestrator import run_orchestrator_turn
from runtime.session_state import (
    SessionStateManager,
    compact_memory_if_needed,
    reconcile_interrupted_tools,
    spill_large_tool_output,
)
from runtime.tools_fs import WorkspaceFS
from runtime.tools_subagent import SubAgentPoolTool, _build_shared_briefing


class InspectingMockModel(ChatModelBase):
    """支持检查传入系统提示词与消息队列的离线替身模型。"""

    def __init__(self, turns: list[list[dict]]):
        super().__init__(model_name="m3-mock", stream=False)
        self._turns = list(turns)
        self.received_batches: list[list[Any]] = []

    async def __call__(self, messages, tools=None, **kwargs):
        self.received_batches.append(messages)
        if not self._turns:
            return ChatResponse(content=[{"type": "text", "text": "执行完毕。"}])
        return ChatResponse(content=self._turns.pop(0))


class M3KernelResilienceTests(unittest.TestCase):
    """验证 M3 阶段针对大规模资料库与长周期任务的核心稳定性机制。"""

    def test_large_tool_output_spillover_and_readback(self) -> None:
        """测试超过 14,000 字符的工具输出自动无损落盘至 .tool_outputs 并可通过 read_lines 完整回读。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            huge_payload = {"rows": [f"第{i}行工程量明细数据_" + ("X" * 80) for i in range(300)]}
            envelope_str, spilled = spill_large_tool_output(ws, "search_text", "abcd1234efgh", huge_payload)
            self.assertTrue(spilled)

            envelope = json.loads(envelope_str)
            self.assertTrue(envelope["_spilled_to_file"])
            saved_rel = envelope["saved_output_file"]
            self.assertTrue((ws / saved_rel).is_file())

            fs = WorkspaceFS(ws, ws)
            tail = fs.read_lines(saved_rel, start_line=280, max_lines=40)
            self.assertIn("第295行工程量明细数据", tail["content"])

    def test_deterministic_memory_compaction_preserves_tool_pairs(self) -> None:
        """测试超过阈值时自动折叠旧轮次大体积 tool_result，同时 100% 保持 tool_use/tool_result 配对完整性。"""
        async def _run():
            mem = InMemoryMemory()
            for i in range(12):
                call_id = f"call_{i}"
                await mem.add(Msg("assistant", [{"type": "tool_use", "id": call_id, "name": "read_lines", "input": {}}], "assistant"))
                await mem.add(
                    Msg(
                        "system",
                        [{"type": "tool_result", "id": call_id, "name": "read_lines", "output": [{"type": "text", "text": "A" * 6000}]}],
                        "system",
                    )
                )

            compacted = await compact_memory_if_needed(mem, trigger_chars=30000, keep_recent_messages=6)
            self.assertTrue(compacted)

            msgs = await mem.get_memory()
            # 早期消息应被折叠缩短
            first_result_txt = json.dumps(msgs[1].content, ensure_ascii=False)
            self.assertIn("已折叠释放上下文", first_result_txt)
            self.assertLess(len(first_result_txt), 1000)

            # 最近 6 条消息必须保持 6000 字符完整未动
            recent_result_txt = json.dumps(msgs[-1].content, ensure_ascii=False)
            self.assertGreater(len(recent_result_txt), 5900)

            # 所有 tool_use ID 仍具有一一对应的 tool_result ID
            use_ids = {b["id"] for m in msgs for b in m.get_content_blocks("tool_use")}
            res_ids = {b["id"] for m in msgs for b in m.get_content_blocks("tool_result")}
            self.assertEqual(use_ids, res_ids)

        asyncio.run(_run())

    def test_checkpoint_interrupted_tool_self_healing_and_skill_persistence(self) -> None:
        """测试上一轮被强制中断遗留悬空 tool_use 时，下一轮自动补齐回执并恢复已激活技能说明。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            state_mgr = SessionStateManager(ws)

            async def _prepare_interrupted():
                mem = InMemoryMemory()
                await mem.add(Msg("user", "分析标书", "user"))
                await mem.add(
                    Msg(
                        "assistant",
                        [{"type": "tool_use", "id": "dangling_99", "name": "ingest_documents", "input": {}}],
                        "assistant",
                    )
                )
                state_mgr.save(
                    source_directory=str(ws.resolve()),
                    memory=mem,
                    loaded_skills=["document-authoring"],
                    cognitive_steps=[{"id": "perceive", "title": "感知", "status": "in_progress", "detail": ""}],
                )

            asyncio.run(_prepare_interrupted())

            mock_model = InspectingMockModel([
                [{"type": "text", "text": "已从上次中断处无缝恢复并保持长文档技能激活！"}]
            ])
            events: list[dict] = []
            res = asyncio.run(
                run_orchestrator_turn(
                    request={"workspace": str(ws), "source_directory": str(ws), "prompt": "继续"},
                    project_root=PROJECT_ROOT,
                    emit=events.append,
                    wait_gate_response=lambda _gid: {"approved": True},
                    auto_approve=True,
                    model_override=mock_model,
                )
            )
            self.assertEqual(res["status"], "completed")
            reconcile_logs = [e for e in events if e.get("type") == "log" and e.get("event") == "state.reconciled_interrupted_tools"]
            self.assertEqual(len(reconcile_logs), 1)
            # 验证已激活技能规程被自动重注入系统提示词
            sent_first_batch = json.dumps(mock_model.received_batches[0], ensure_ascii=False, default=str)
            self.assertIn("已激活技能规程：`document-authoring`", sent_first_batch)

    def test_repeated_failure_circuit_breaker_and_subagent_briefing(self) -> None:
        """测试同参数连续 3 次报错自动触发防死循环熔断，且子代理自动获注全局底座摘要。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp)
            (ws / "01-requirements.md").write_text("# 项目约束\n- 工期：240日历天", encoding="utf-8")
            (ws / "02-plan.md").write_text("# 章节大纲\n- 第1章 工程概况", encoding="utf-8")

            briefing = _build_shared_briefing(ws, "author")
            self.assertIn("240日历天", briefing)
            self.assertIn("第1章 工程概况", briefing)

            # 连续 4 次用完全相同的不存在路径调用 read_lines，第 4 次应直接收到熔断器拦截
            bad_call = {"type": "tool_use", "name": "read_lines", "input": {"path": "not_exist.md"}}
            mock_model = InspectingMockModel([
                [{**bad_call, "id": "c1"}],
                [{**bad_call, "id": "c2"}],
                [{**bad_call, "id": "c3"}],
                [{**bad_call, "id": "c4"}],
                [{"type": "text", "text": "收到熔断提示，停止重试。"}],
            ])
            events: list[dict] = []
            asyncio.run(
                run_orchestrator_turn(
                    request={"workspace": str(ws), "source_directory": str(ws), "prompt": "读取文件"},
                    project_root=PROJECT_ROOT,
                    emit=events.append,
                    wait_gate_response=lambda _gid: {"approved": True},
                    auto_approve=True,
                    model_override=mock_model,
                )
            )
            tool_outputs = [
                e["execution"].get("output", "")
                for e in events
                if e.get("type") == "tool" and e["execution"].get("output")
            ]
            self.assertTrue(any("RepeatedFailureCircuitBreaker" in out for out in tool_outputs))

    def test_read_parsed_pages_on_yanfeng_real_cache_if_present(self) -> None:
        """测试 read_parsed_pages 支持按原文件名直接定位物理页（优先在延锋地块真实缓存上验证，离线环境构造等价缓存）。"""
        yanfeng_ws = PROJECT_ROOT / "test-temp" / "yanfeng-m2-audit"
        yanfeng_src = Path(r"D:\Desktop\shizu\6.10（山水）延锋地块")
        if (yanfeng_ws / ".workspace" / "parsed" / "manifest.json").is_file() and yanfeng_src.is_dir():
            fs = WorkspaceFS(yanfeng_ws, yanfeng_src)
            page_res = fs.read_parsed_pages("招标文件正文.pdf", start_page=5, end_page=6)
            self.assertEqual(page_res["total_pages"], 227)
            self.assertIn("240 日历天", page_res["content"])
            self.assertIn("1771.818", page_res["content"])
            self.assertTrue(page_res.get("toc_markdown", "").endswith("toc.md"))
        else:
            with tempfile.TemporaryDirectory() as tmp:
                ws = Path(tmp)
                pdir = ws / ".workspace" / "parsed" / "demo-123"
                pdir.mkdir(parents=True)
                (pdir / "full.md").write_text("<!-- page: 1 -->\nP1内容\n\n<!-- page: 2 -->\nP2工期240天\n", encoding="utf-8")
                (ws / ".workspace" / "parsed" / "manifest.json").write_text(
                    json.dumps({"documents": [{"source_path": "测试标书.pdf", "parsed_markdown": ".workspace/parsed/demo-123/full.md"}]}),
                    encoding="utf-8",
                )
                fs = WorkspaceFS(ws, ws)
                res = fs.read_parsed_pages("测试标书.pdf", 2, 2)
                self.assertIn("P2工期240天", res["content"])


if __name__ == "__main__":
    unittest.main()
