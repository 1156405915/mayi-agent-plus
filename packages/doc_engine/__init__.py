"""纯确定性本地文档标准化解析、原生 Word 编译与质量硬检引擎。"""

from .parser import ingest_files, parse_single_file
from .docx_builder import DEFAULT_STYLE_PRESET, compile_markdown_to_docx
from .docx_auditor import audit_docx_file, audit_markdown_drafts
from .qingtian_auditor import (
    certify_qingtian_quality,
    compute_drafts_sha256,
    parse_inspector_scorecard,
    run_qingtian_radar,
    verify_qingtian_release_gate,
)

__all__ = [
    "DEFAULT_STYLE_PRESET",
    "audit_docx_file",
    "audit_markdown_drafts",
    "certify_qingtian_quality",
    "compile_markdown_to_docx",
    "compute_drafts_sha256",
    "ingest_files",
    "parse_single_file",
    "parse_inspector_scorecard",
    "run_qingtian_radar",
    "verify_qingtian_release_gate",
]
