"""端到端核心链路测试：覆盖离线多格式解析缓存、毫秒级检索、草稿体检、原生 Word 编译及 AgentScope 五步编排。"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import openpyxl
from agentscope.model import ChatModelBase, ChatResponse
from docx import Document
from pypdf import PdfWriter

from packages.doc_engine import audit_docx_file, audit_markdown_drafts, compile_markdown_to_docx
from runtime.orchestrator import run_orchestrator_turn
from runtime.skill_loader import SkillRegistry
from runtime.tools_exec import PythonExecTool
from runtime.tools_fs import WorkspaceFS
from runtime.tools_ingest import DocumentIngestTool


class ScriptedMockModel(ChatModelBase):
    """用于离线确定性验证 AgentScope 主控与并发子代理工具编排的脚本化替身模型。"""

    def __init__(self, scripted_turns: list[list[dict]]):
        super().__init__(model_name="mock-model", stream=False)
        self._turns = list(scripted_turns)

    async def __call__(self, messages, tools=None, **kwargs):
        if not self._turns:
            return ChatResponse(content=[{"type": "text", "text": "已完成子任务。"}])
        content = self._turns.pop(0)
        return ChatResponse(content=content)


class MayiAgentPlusPipelineTests(unittest.TestCase):
    """验证从源文件夹感知标准化到最终 .docx 合成交付的全链路确定性与可靠性。"""

    def test_offline_ingest_cache_and_fast_search(self) -> None:
        """测试混合 DOCX + XLSX + PDF 文件夹离线标准化为带锚点 Markdown，并验证二次命中缓存与页码检索。"""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            src_dir = base / "sources"
            ws_dir = base / "workspace"
            src_dir.mkdir()
            ws_dir.mkdir()

            # 1. 构造含标题与评分要求表的真实 DOCX 招标文件
            tender_doc = Document()
            tender_doc.add_heading("第一章 招标项目概况与工期要求", level=1)
            tender_doc.add_paragraph("本项目名称为高新区智慧道路改造工程，总工期要求为 180 日历天，质量标准为合格。")
            tbl = tender_doc.add_table(rows=2, cols=3)
            tbl.rows[0].cells[0].text = "评分项"
            tbl.rows[0].cells[1].text = "分值"
            tbl.rows[0].cells[2].text = "评审标准"
            tbl.rows[1].cells[0].text = "总体施工部署"
            tbl.rows[1].cells[1].text = "25分"
            tbl.rows[1].cells[2].text = "工期响应180日历天且节点清晰"
            tender_doc.save(str(src_dir / "招标文件.docx"))

            # 2. 构造含工程量数据的真实 XLSX 清单
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "道路工程量清单"
            ws.append(["项目编码", "项目名称", "单位", "工程量"])
            ws.append(["040201001", "沥青混凝土路面铺设", "m2", 12500])
            wb.save(str(src_dir / "工程量清单.xlsx"))

            # 3. 构造空白测试 PDF 验证不崩溃容错
            pdf_writer = PdfWriter()
            pdf_writer.add_blank_page(width=595, height=842)
            with open(src_dir / "附图封面.pdf", "wb") as f:
                pdf_writer.write(f)

            ingest = DocumentIngestTool(ws_dir, src_dir)
            first_run = ingest.ingest_documents(enable_ocr=False)
            self.assertEqual(first_run["ready_count"], 3)
            self.assertEqual(first_run["cache_hits"], 0)

            # 二次调用必须 100% 命中内容寻址缓存
            second_run = ingest.ingest_documents(enable_ocr=False)
            self.assertEqual(second_run["ready_count"], 3)
            self.assertEqual(second_run["cache_hits"], 3)

            # 验证毫秒级全文检索可同时搜到 DOCX 条款与 XLSX 工程量并带页码/工作表锚点
            fs = WorkspaceFS(ws_dir, src_dir)
            hits = fs.search_text("180 沥青混凝土", scope="parsed")
            self.assertGreaterEqual(hits["hit_count"], 2)
            combined_snippets = "\n".join(h["snippet"] for h in hits["hits"])
            self.assertIn("180", combined_snippets)
            self.assertIn("沥青混凝土", combined_snippets)

    def test_skill_draft_verification_and_native_docx_compilation(self) -> None:
        """测试标杆技能脚本：拦截占位符 -> 通过草稿审查 -> 编译生成带目录与原生表格的正式 Word 文档。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = Path(tmp) / "ws"
            drafts_dir = ws_dir / "drafts"
            drafts_dir.mkdir(parents=True)

            ch1 = drafts_dir / "01-overview.md"
            ch1.write_text(
                "# 第1章 工程概况与编制依据\n\n"
                "高新区智慧道路改造工程总工期严格控制在 **180 日历天** 以内，工程质量确保一次性验收合格。"
                "道路主线沥青混凝土路面铺设总面积为 12500 平方米，施工期间采取分段导改保障周边通行安全，"
                "全面响应招标文件各项实质性要求与技术标准规范。TODO 待补充参数\n",
                encoding="utf-8",
            )

            # 第一次审计必须准确抓出残留的 TODO/待补充占位符
            bad_audit = audit_markdown_drafts([ch1], workspace_dir=ws_dir, min_chapter_chars=80)
            self.assertFalse(bad_audit["passed"])
            self.assertGreaterEqual(bad_audit["error_count"], 1)

            # 修复占位符并补充第二章带规范表格的草稿
            ch1.write_text(
                "# 第1章 工程概况与总体目标\n\n"
                "高新区智慧道路改造工程总工期严格控制在 **180 日历天** 以内，工程质量标准为国家验收合格标准。"
                "本项目核心工程内容涵盖道路基层处理与沥青混凝土面层摊铺，总铺装面积达 12500 平方米。"
                "项目部建立健全质量、安全、进度三位一体管控体系，确保各里程碑节点按期兑现。\n\n"
                "表 1-1 核心工程控制指标汇总表\n\n"
                "| 指标维度 | 招标要求目标 | 本方案承诺标准 |\n"
                "| --- | --- | --- |\n"
                "| 总工期 | 180 日历天 | 175 日历天（提前5天） |\n"
                "| 路面铺装量 | 12500 m2 | 12500 m2 全覆盖 |\n",
                encoding="utf-8",
            )
            ch2 = drafts_dir / "02-construction-method.md"
            ch2.write_text(
                "# 第2章 主要施工工艺与质量保证措施\n\n"
                "## 2.1 沥青混凝土摊铺施工方案\n\n"
                "采用双机联铺与智能温控碾压工艺，初压、复压、终压全过程监控混合料到场温度与压实度，"
                "针对检查井周边及路缘石衔接部位采用小型振动夯实设备精细收边，杜绝早期车辙与渗水病害，"
                "所有原材料进场均严格执行见证取样送检制度，保障路面平整度与耐久性全面达标。\n",
                encoding="utf-8",
            )

            exec_tool = PythonExecTool(
                project_root=PROJECT_ROOT,
                workspace_dir=ws_dir,
                source_dir=ws_dir,
                skills_root=PROJECT_ROOT / "skills",
            )
            skill_reg = SkillRegistry(PROJECT_ROOT / "skills")
            detail = skill_reg.load_skill_detail("document-authoring")
            self.assertEqual(detail["status"], "ok")

            verify_res = exec_tool.run_python(
                script_path=detail["scripts"]["verify_drafts.py"],
                args=["--drafts-dir", "drafts", "--min-chars", "80"],
            )
            self.assertEqual(verify_res["status"], "succeeded", verify_res["stderr"])

            compile_res = exec_tool.run_python(
                script_path=detail["scripts"]["compile_word.py"],
                args=[
                    "--drafts-dir", "drafts",
                    "--output", "deliverables/智慧道路改造施工组织设计.docx",
                    "--title", "高新区智慧道路改造工程施工组织设计",
                    "--subtitle", "技术方案报告",
                    "--preset", "standard_report",
                ],
            )
            self.assertEqual(compile_res["status"], "succeeded", compile_res["stderr"])
            parsed_out = json.loads(compile_res["stdout"])
            self.assertTrue(parsed_out["passed"])

            docx_file = ws_dir / "deliverables" / "智慧道路改造施工组织设计.docx"
            self.assertTrue(docx_file.is_file())
            docx_check = audit_docx_file(docx_file)
            self.assertTrue(docx_check["passed"])
            self.assertEqual(docx_check["heading_1_count"], 2)
            self.assertEqual(docx_check["table_count"], 1)

    def test_orchestrator_gate_edit_awareness_and_event_stream(self) -> None:
        """测试主控五步推进与门禁暂停期间用户在线微调 02-plan.md 后的自动感知闭环。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws_dir = Path(tmp) / "ws"
            ws_dir.mkdir()
            emitted: list[dict] = []

            scripted = ScriptedMockModel([
                [
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "write_workspace_file",
                        "input": {"path": "02-plan.md", "content": "# 初始目录\n- 第1章 概况"},
                    }
                ],
                [
                    {
                        "type": "tool_use",
                        "id": "call_2",
                        "name": "ask_user_gate",
                        "input": {
                            "title": "确认目录大纲",
                            "summary": "请确认两章目录结构",
                            "review_files": ["02-plan.md"],
                        },
                    }
                ],
                [
                    {"type": "text", "text": "已识别到您手工新增了第2章，规划已对齐完成！"}
                ],
            ])

            def fake_gate_wait(_gate_id: str) -> dict:
                # 模拟用户在右侧面板直接编辑保存了 02-plan.md 后点击“确认放行”
                (ws_dir / "02-plan.md").write_text("# 微调后目录\n- 第1章 概况\n- 第2章 施工重点", encoding="utf-8")
                return {"approved": True, "feedback": "我加了第2章"}

            res = asyncio.run(
                run_orchestrator_turn(
                    request={"workspace": str(ws_dir), "source_directory": str(ws_dir), "prompt": "帮我列目录"},
                    project_root=PROJECT_ROOT,
                    emit=emitted.append,
                    wait_gate_response=fake_gate_wait,
                    auto_approve=False,
                    model_override=scripted,
                )
            )
            self.assertEqual(res["status"], "completed")
            self.assertIn("第2章", res["text"])
            gate_events = [e for e in emitted if e.get("type") == "gate"]
            self.assertEqual(len(gate_events), 1)
            tool_outputs = [
                e["execution"].get("output", "")
                for e in emitted
                if e.get("type") == "tool" and e["execution"].get("toolName") == "ask_user_gate" and e["execution"].get("output")
            ]
            self.assertTrue(any("02-plan.md" in out for out in tool_outputs))


if __name__ == "__main__":
    unittest.main()
