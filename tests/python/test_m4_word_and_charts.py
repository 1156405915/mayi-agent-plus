"""M4 标杆技能与深度 Word 排版测试：覆盖三大工程图表绘制、横竖自适应分节宽表、跨章大纲/工期体检及技术暗标纯黑白合规编译。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.skill_loader import SkillRegistry
from runtime.tools_exec import PythonExecTool


class M4WordAndChartsTests(unittest.TestCase):
    """验证 M4 阶段高清图表渲染、宽表自动横向分节、暗标敏感红线拦截与纯黑白 Word 编译。"""

    def test_chart_rendering_landscape_wide_table_and_blind_bidding_compliance(self) -> None:
        """测试一键生成甘特图/架构图/工艺图，拦截暗标违规与工期超限，修复后编译出含横向分节的合规 Word。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / "drafts").mkdir(parents=True)

            exec_tool = PythonExecTool(
                project_root=PROJECT_ROOT,
                workspace_dir=ws,
                source_dir=ws,
                skills_root=PROJECT_ROOT / "skills",
            )
            skill = SkillRegistry(PROJECT_ROOT / "skills").load_skill_detail("document-authoring")
            scripts = skill["scripts"]

            # 1. 测试批量绘制黑白暗标模式的甘特横道图、组织架构图与施工工艺流程图
            spec = {
                "charts": [
                    {
                        "type": "org_tree",
                        "output": "assets/org.png",
                        "title": "项目现场管理组织架构图",
                        "root": "项目经理",
                        "deputies": ["技术负责人", "安全总监"],
                        "departments": [
                            {"name": "道路排水部", "items": ["路基班", "管网班"]},
                            {"name": "质量安全部", "items": ["质检组", "安全组"]},
                        ],
                    },
                    {
                        "type": "flow",
                        "output": "assets/flow.png",
                        "title": "雨污水管道安装施工工艺流程图",
                        "steps": [
                            {"title": "测量放线", "detail": "全站仪中线复核", "checkpoint": "轴线复测"},
                            {"title": "沟槽开挖支护", "detail": "分层放坡降水", "checkpoint": "基底验槽"},
                            {"title": "管道安装接口", "detail": "橡胶圈柔性承插", "checkpoint": "闭水试验"},
                            {"title": "管腔分层回填", "detail": "两侧对称夯实", "checkpoint": "压实度>=95%"},
                        ],
                    },
                    {
                        "type": "gantt",
                        "output": "assets/gantt.png",
                        "title": "延锋地块东侧规划支路240日历天总进度横道图",
                        "total_days": 240,
                        "tasks": [
                            {"name": "施工准备与交通导改", "start": 1, "end": 20, "critical": True},
                            {"name": "排水及综合管线工程", "start": 21, "end": 105, "critical": True},
                            {"name": "道路路基与水稳基层", "start": 90, "end": 175, "critical": True},
                            {"name": "沥青面层与附属交通绿化", "start": 176, "end": 235, "critical": False},
                        ],
                    },
                ]
            }
            (ws / "charts.json").write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")

            chart_res = exec_tool.run_python(
                script_path=scripts["render_charts.py"],
                args=["--spec-file", "charts.json", "--blind"],
            )
            self.assertEqual(chart_res["status"], "succeeded", chart_res["stderr"])
            for name in ("org.png", "flow.png", "gantt.png"):
                self.assertTrue((ws / "assets" / name).is_file())

            # 2. 写入 01-requirements.md (240日历天) 与 02-plan.md (声明两章)
            (ws / "01-requirements.md").write_text("# 核心要求\n- 计划工期：240 日历天\n- 评审方式：技术暗标\n", encoding="utf-8")
            (ws / "02-plan.md").write_text(
                "# 规划目录\n- `drafts/01-overview.md` 第1章 总体施工部署\n- `drafts/02-schedule.md` 第2章 施工进度计划与保障\n",
                encoding="utf-8",
            )

            # 故意只写第1章且包含“工期300日历天”超限与“我司名称：某某路桥”暗标违规，验证体检器必须精准拦截！
            (ws / "drafts" / "01-overview.md").write_text(
                "# 第1章 总体施工部署与组织机构\n\n"
                "本项目总工期拟定为 300 日历天。我司名称：安徽某某建设集团，联系电话 13812345678。"
                "现场建立完善的施工管理架构与雨污水施工工艺控制体系，确保工程一次验收合格，安全文明施工全面达标。\n\n"
                "![1-1 项目现场管理组织架构图](assets/org.png)\n",
                encoding="utf-8",
            )

            bad_verify = exec_tool.run_python(
                script_path=scripts["verify_drafts.py"],
                args=["--drafts-dir", "drafts", "--min-chars", "60", "--blind"],
            )
            self.assertEqual(bad_verify["status"], "failed")
            bad_report = json.loads(bad_verify["stdout"])
            self.assertFalse(bad_report["passed"])
            all_err_msgs = "\n".join(i["message"] for i in bad_report["issues"])
            self.assertIn("drafts/02-schedule.md", all_err_msgs)
            self.assertIn("工期超限矛盾", all_err_msgs)
            self.assertIn("暗标红线拦截", all_err_msgs)

            # 3. 修复违规项并补齐第2章（包含显式横向分节甘特图 + 9列宽表格自动横排验证，同时满足青天规范）
            (ws / "drafts" / "01-overview.md").write_text(
                "# 第1章 总体施工部署与工艺体系\n\n"
                "## 1.1 工程目标与规范控制\n\n"
                "本项目严格响应招标要求，计划总工期控制在 235 日历天（优于招标要求的 240 日历天上限），"
                "工程质量满足 CJJ 1-2008 与 GB 50268-2008 验收合格标准（路床压实度≥95%，水稳压实度≥98%）。\n\n"
                "![图 1-1 项目现场管理组织架构图](assets/org.png)\n\n"
                "![图 1-2 雨污水管道安装施工工艺流程图](assets/flow.png)\n",
                encoding="utf-8",
            )
            (ws / "drafts" / "02-schedule.md").write_text(
                "# 第2章 施工总进度计划与资源配置\n\n"
                "## 2.1 阶段划分与产能匹配验算\n\n"
                "按 JGJ 46-2005 与 DB34/T 5040 规范组织四个施工阶段，沥青摊铺日均产能 2600 m²/d（单机台班产能 1400 m²/台班 × 2 台），"
                "保障总工期 235 日历天（D1—D235）刚性兑现。\n\n"
                "表 2-1 主要施工机械设备分阶段进场配置宽表\n\n"
                "| 序号 | 设备名称 | 规格型号 | 额定功率 | 数量 | 道路阶段 | 排水阶段 | 照明阶段 | 检修状态 |\n"
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n"
                "| 1 | 沥青摊铺机 | ABG8620 | 190kW | 2台 | 主力投入 | 备用 | 退场 | 完好已检 |\n"
                "| 2 | 双钢轮压路机 | HD138 | 118kW | 3台 | 主力投入 | 沟槽回填 | 备用 | 完好已检 |\n\n"
                "<!-- page: landscape -->\n\n"
                "![图 2-1 延锋地块东侧规划支路240日历天总进度横道图](assets/gantt.png)\n\n"
                "<!-- page: portrait -->\n\n"
                "横道图后自动切回标准竖向 A4 页面编写工期纠偏保障措施，确保雨季施工和节假日材料储备充足。\n",
                encoding="utf-8",
            )

            good_verify = exec_tool.run_python(
                script_path=scripts["verify_drafts.py"],
                args=["--drafts-dir", "drafts", "--min-chars", "60", "--blind"],
            )
            self.assertEqual(good_verify["status"], "succeeded", good_verify["stdout"])

            # 3.5 模拟青天质检官出具 ≥90 分评审报告并签发准入证后，方可解锁暗标 Word 编译
            (ws / "03-qingtian-review.md").write_text(
                "# 青天评标质检报告\n\n```json\n"
                + json.dumps({
                    "inspector": "hefei-qingtian-v1",
                    "scores": {
                        "completeness": 29.0,
                        "specificity": 23.5,
                        "compliance": 19.0,
                        "feasibility": 14.0,
                        "normativity": 9.5,
                    },
                    "total_score": 95.0,
                    "verdict": "PASS",
                    "blocking_defects": [],
                    "chapter_rework": [],
                }, ensure_ascii=False)
                + "\n```\n",
                encoding="utf-8",
            )
            cert_res = exec_tool.run_python(
                script_path=scripts["qingtian_inspect.py"],
                args=["--mode", "certify", "--drafts-dir", "drafts", "--blind"],
            )
            self.assertEqual(cert_res["status"], "succeeded", cert_res["stdout"])

            # 4. 编译输出严格暗标版 .docx 并验证存在横向分节、纯黑无页眉且通过成品审计
            compile_res = exec_tool.run_python(
                script_path=scripts["compile_word.py"],
                args=[
                    "--drafts-dir", "drafts",
                    "--output", "deliverables/延锋地块施工组织设计_暗标版.docx",
                    "--title", "施工组织设计",
                    "--blind",
                ],
            )
            self.assertEqual(compile_res["status"], "succeeded", compile_res["stderr"])
            out_json = json.loads(compile_res["stdout"])
            self.assertTrue(out_json["passed"])
            self.assertTrue(out_json["blind_mode"])
            self.assertGreaterEqual(out_json["build"]["landscape_section_count"], 1)


if __name__ == "__main__":
    unittest.main()
