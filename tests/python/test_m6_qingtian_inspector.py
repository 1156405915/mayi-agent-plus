"""M6 合肥“青天大模型”评标质检雷达与防篡改准入证门禁单元测试。

测试覆盖：
1. 普通大模型通病拦截：大段散文墙、“压实度按规范/温度达标”模糊糊弄词、二级标题跳号、双重重复图题、无产能验算；
2. 防放水与硬锁死：未拿准入证或底层雷达存在硬伤时（即便 AI 虚给高分），`certify` 与 `compile_word.py` 一律阻断；
3. 高标准施组签发与防篡改哈希绑定：达标 ≥90 分签发 `.workspace/qingtian_certificate.json` 后可编译，但若改动草稿 1 个字立即失效阻断，且禁止通过 `WorkspaceFS` 手工伪造准入证。
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from packages.doc_engine.qingtian_auditor import certify_qingtian_quality, run_qingtian_radar
from runtime.skill_loader import SkillRegistry
from runtime.tools_exec import PythonExecTool
from runtime.tools_fs import WorkspaceFS


class M6QingtianInspectorTests(unittest.TestCase):
    """验证合肥青天五维评标雷达、准入证签发与 Word 编译硬门禁闭环。"""

    def test_radar_catches_mediocre_llm_draft_and_blocks_word_compile(self) -> None:
        """普通大模型式套话散文墙草稿必须被青天雷达抓出全部硬伤，且物理阻断 Word 编译。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / "drafts").mkdir(parents=True)
            (ws / "assets").mkdir(parents=True)

            # 写入伪造的配图
            from PIL import Image

            Image.new("RGB", (400, 200), "white").save(ws / "assets" / "fig1.png")

            # 构造 4 章典型低质“普通大模型”草稿：含跳号(1.1->1.3)、双重图题、多个模糊逃避词、460字散文墙、无产能验算
            wall = "项目部高度重视、精心组织、科学管理、统筹安排施工任务，确保工程保质保量完成。" * 13
            (ws / "drafts" / "01-overview.md").write_text(
                "# 第1章 工程概况\n\n## 1.1 编制依据\n\n"
                f"{wall}\n\n"
                "## 1.3 跳号小节\n\n"
                "沥青混合料温度低于规定值不得摊铺，压实度按规范，终压温度达标，按规范频次检测。\n\n"
                "![图 1-1 工艺图](assets/fig1.png)\n\n"
                "图 1-1 工艺图\n",
                encoding="utf-8",
            )
            for idx in (2, 3, 4):
                (ws / "drafts" / f"0{idx}-ch.md").write_text(
                    f"# 第{idx}章 专项方案\n\n## {idx}.1 施工措施\n\n{wall}\n",
                    encoding="utf-8",
                )

            radar = run_qingtian_radar(sorted((ws / "drafts").glob("*.md")), workspace_dir=ws)
            self.assertFalse(radar["passed_radar"])
            block_msgs = "\n".join(b["message"] for b in radar["blocking_issues"])
            self.assertIn("青天结构断链", block_msgs)
            self.assertIn("双重图题冲突", block_msgs)
            self.assertIn("青天合规红线", block_msgs)
            self.assertIn("青天版面红线", block_msgs)
            self.assertIn("青天可落地性红线", block_msgs)

            # 即便模型评审放水写出 94 分，certify 也必须因底层雷达硬伤而拒签！
            (ws / "03-qingtian-review.md").write_text(
                "# 假高分报告\n\n```json\n"
                + json.dumps({
                    "inspector": "hefei-qingtian-v1",
                    "scores": {
                        "completeness": 29,
                        "specificity": 24,
                        "compliance": 19,
                        "feasibility": 14,
                        "normativity": 8,
                    },
                    "total_score": 94.0,
                    "verdict": "PASS",
                    "blocking_defects": [],
                })
                + "\n```\n",
                encoding="utf-8",
            )
            cert = certify_qingtian_quality(ws, sorted((ws / "drafts").glob("*.md")))
            self.assertFalse(cert["passed"])
            self.assertFalse((ws / ".workspace" / "qingtian_certificate.json").exists())

            # 直接调用 compile_word.py（hefei_qingtian_bidding）必须被门禁拦截（返回 failed）
            exec_tool = PythonExecTool(PROJECT_ROOT, ws, ws, PROJECT_ROOT / "skills")
            skill = SkillRegistry(PROJECT_ROOT / "skills").load_skill_detail("document-authoring")
            self.assertIn("qingtian-rubric.md", skill["references"])

            comp = exec_tool.run_python(
                script_path=skill["scripts"]["compile_word.py"],
                args=[
                    "--drafts-dir", "drafts",
                    "--output", "deliverables/out.docx",
                    "--title", "测试施组",
                    "--preset", "hefei_qingtian_bidding",
                ],
            )
            self.assertEqual(comp["status"], "failed")
            self.assertIn("青天质检门禁拦截", comp["stdout"])

    def test_high_standard_draft_certification_and_sha256_tamper_lock(self) -> None:
        """高标准量化施组通过青天认证后解锁编译；改后未重检或试图手工伪造证书均被严密拦截。"""
        with tempfile.TemporaryDirectory() as tmp:
            ws = Path(tmp) / "ws"
            (ws / "drafts").mkdir(parents=True)
            (ws / "00-facts.md").write_text(
                "# 事实基线\n- 项目编号：2026BFJGZ50374\n- 道路：延锋地块支路（云谷路交口）\n- 清表：58128.470 m³\n- 总工期：240 日历天\n",
                encoding="utf-8",
            )

            # 1. 验证 WorkspaceFS 禁止通过普通写文件工具伪造准入证
            fs = WorkspaceFS(ws, ws)
            with self.assertRaises(PermissionError):
                fs.write_workspace_file(".workspace/qingtian_certificate.json", '{"passed": true}')

            # 2. 撰写符合总工级三段式规范的高标准章节（含项目锚点、产能验算、现行国地标与量化控制表）
            ch1 = ws / "drafts" / "01-overview.md"
            ch1.write_text(
                "# 第1章 工程概况与施工部署\n\n"
                "## 1.1 工况痛点与总体目标\n\n"
                "本项目（2026BFJGZ50374）位于延锋地块东侧，南起云谷路交口，计划总工期 240 日历天（D1—D240）。"
                "针对清表土方 58128.470 m³ 外运强度大痛点，执行 CJJ 1-2008 与 DB34/T 5040 标准。\n\n"
                "- **土方产能验算**：清表 58128.470 m³ 计划 15d 完成，日均外运 3875.2 m³/d；投入 4 台 1.6m³ 挖掘机（单机台班产能 1100 m³/d，合计 4400 m³/d ≥ 3875.2 m³/d），产能闭合。\n\n"
                "表 1-1 核心技术与质量验收控制指标表\n\n"
                "| 控制项目 | 现行标准号 | 允许偏差/合格阈值 | 检测频次 |\n"
                "| --- | --- | --- | --- |\n"
                "| 路床压实度 | CJJ 1-2008 | ≥95%（重型击实） | 每1000m²测3点 |\n"
                "| 沟槽监测位移 | GB 50268-2008 / 建设部令第37号 | 累计≤25mm，速率≤2mm/d | 开挖期1次/d |\n",
                encoding="utf-8",
            )
            ch2 = ws / "drafts" / "02-asphalt.md"
            ch2.write_text(
                "# 第2章 沥青面层精细化施工与安全保障\n\n"
                "## 2.1 改性沥青温控与碾压工艺\n\n"
                "针对云谷路交口 SBS 改性沥青 AC-13C 面层衔接平顺性要求，严格执行 JTG F40-2004 与 JGJ 46-2005 强条。\n\n"
                "- **摊铺产能匹配**：面层 8712.42 m² 计划 4d 完成，日均摊铺 2178 m²/d；配置 2 台 ABG8620 摊铺机梯队联铺（单机产能 1500 m²/台班），满足连续不断料要求。\n\n"
                "表 2-1 SBS 改性沥青面层施工温度与压实度控制表\n\n"
                "| 工序环节 | 现行标准号 | 量化控制指标 | 责任岗位 |\n"
                "| --- | --- | --- | --- |\n"
                "| 摊铺温度 | JTG F40-2004 | ≥160℃（低于160℃严禁摊铺） | 试验员 |\n"
                "| 初压/终压温度 | CJJ 1-2008 | 初压≥150℃，终压≥90℃，<50℃开放交通 | 质检员 |\n"
                "| 马歇尔压实度 | CJJ 1-2008 | ≥96%，平整度标准差≤1.2mm | 质量负责人 |\n",
                encoding="utf-8",
            )

            (ws / "03-qingtian-review.md").write_text(
                "# 合肥青天大模型技术标评标质检报告\n\n```json\n"
                + json.dumps({
                    "inspector": "hefei-qingtian-v1",
                    "scores": {
                        "completeness": 28.5,
                        "specificity": 24.0,
                        "compliance": 19.0,
                        "feasibility": 14.5,
                        "normativity": 9.5,
                    },
                    "total_score": 95.5,
                    "verdict": "PASS",
                    "blocking_defects": [],
                    "chapter_rework": [],
                }, ensure_ascii=False)
                + "\n```\n",
                encoding="utf-8",
            )

            exec_tool = PythonExecTool(PROJECT_ROOT, ws, ws, PROJECT_ROOT / "skills")
            scripts = SkillRegistry(PROJECT_ROOT / "skills").load_skill_detail("document-authoring")["scripts"]

            cert_run = exec_tool.run_python(
                script_path=scripts["qingtian_inspect.py"],
                args=["--mode", "certify", "--drafts-dir", "drafts", "--min-score", "90"],
            )
            self.assertEqual(cert_run["status"], "succeeded", cert_run["stdout"])
            cert_data = json.loads(cert_run["stdout"])
            self.assertTrue(cert_data["passed"])
            self.assertGreaterEqual(cert_data["final_score"], 90.0)

            # 3. 持证编译 hefei_qingtian_bidding 预设 Word 成品，验证放行成功
            ok_compile = exec_tool.run_python(
                script_path=scripts["compile_word.py"],
                args=[
                    "--drafts-dir", "drafts",
                    "--output", "deliverables/延锋高标施组.docx",
                    "--title", "延锋地块施工组织设计",
                    "--preset", "hefei_qingtian_bidding",
                ],
            )
            self.assertEqual(ok_compile["status"], "succeeded", ok_compile["stderr"])
            ok_payload = json.loads(ok_compile["stdout"])
            self.assertTrue(ok_payload["passed"])
            self.assertTrue(ok_payload["qingtian_gate"]["passed"])

            # 4. 篡改草稿哪怕 1 个字而不重跑质检，compile_word.py 必须立即通过 SHA-256 锁拦截！
            ch2.write_text(ch2.read_text(encoding="utf-8") + "\n补充一句未经质检的话。\n", encoding="utf-8")
            tampered_compile = exec_tool.run_python(
                script_path=scripts["compile_word.py"],
                args=[
                    "--drafts-dir", "drafts",
                    "--output", "deliverables/延锋高标施组.docx",
                    "--title", "延锋地块施工组织设计",
                    "--preset", "hefei_qingtian_bidding",
                ],
            )
            self.assertEqual(tampered_compile["status"], "failed")
            self.assertIn("SHA-256 摘要不一致", tampered_compile["stdout"])


if __name__ == "__main__":
    unittest.main()
