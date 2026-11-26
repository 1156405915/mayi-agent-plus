"""M5 真实页数门禁测试：验证页数红线以真实排版引擎为准，超限阻断、达标放行、引擎缺失时退回估算。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from docx import Document

from packages.doc_engine import docx_auditor


def _make_docx(path: Path) -> None:
    """生成一份最小合规成品：含一级标题与一张带数据行的表格。"""
    doc = Document()
    doc.add_heading("第1章 工程概况", level=1)
    doc.add_paragraph("本工程为城市支路新建工程。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(1, 0).text = "工期 240 日历天"
    doc.save(str(path))


class M5PageGateTests(unittest.TestCase):
    """覆盖 audit_docx_file(max_pages=...) 三种分支，测页引擎通过 mock 隔离，保证 CI 无需安装 Word。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.docx = Path(self._tmp.name) / "out.docx"
        _make_docx(self.docx)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_real_pages_over_limit_blocks(self) -> None:
        """真实排版 51 页 > 50 页上限时必须判定不通过（复现 M5 自估 45.5 页漏判场景）。"""
        with mock.patch.object(
            docx_auditor, "measure_real_page_count",
            return_value={"pages": 51, "engine": "word_com", "error": None},
        ):
            r = docx_auditor.audit_docx_file(str(self.docx), max_pages=50)
        self.assertFalse(r["passed"])
        self.assertEqual(r["real_pages"], 51)
        self.assertTrue(any("超过招标上限" in i for i in r["issues"]))

    def test_real_pages_within_limit_passes(self) -> None:
        """真实页数达标时放行，并在结果中回传 real_pages 与引擎名供智能体汇报。"""
        with mock.patch.object(
            docx_auditor, "measure_real_page_count",
            return_value={"pages": 48, "engine": "word_com", "error": None},
        ):
            r = docx_auditor.audit_docx_file(self.docx, max_pages=50)
        self.assertTrue(r["passed"], r["issues"])
        self.assertEqual((r["real_pages"], r["page_engine"]), (48, "word_com"))

    def test_no_engine_falls_back_to_estimate_and_no_limit_skips_measure(self) -> None:
        """无排版引擎时退回估算判定；未设上限时完全不触发测页，避免拖慢普通编译。"""
        with mock.patch.object(
            docx_auditor, "measure_real_page_count",
            return_value={"pages": None, "engine": "unavailable", "error": "none"},
        ):
            r = docx_auditor.audit_docx_file(self.docx, max_pages=50)
        self.assertTrue(r["passed"])
        self.assertIsNone(r["real_pages"])

        with mock.patch.object(docx_auditor, "measure_real_page_count") as m:
            docx_auditor.audit_docx_file(self.docx)
            m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
