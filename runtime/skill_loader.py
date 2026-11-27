"""可插拔领域技能加载器：自动发现 skills/*/SKILL.md 并在主控需要时按需注入完整规程与脚本路径。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SkillInfo:
    """单个技能包的元数据与磁盘路径描述。"""

    skill_id: str
    name: str
    description: str
    skill_dir: Path
    skill_md_path: Path
    scripts: list[str]


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 SKILL.md 顶部的简易 YAML Frontmatter（name / description）并分离正文。"""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        match = re.match(r"^\s*([A-Za-z0-9_-]+)\s*:\s*(.+?)\s*$", line)
        if match:
            key, val = match.group(1), match.group(2).strip().strip("'\"")
            meta[key] = val
    return meta, parts[2].lstrip("\r\n")


class SkillRegistry:
    """管理本地技能目录的扫描、摘要生成与按需详细读取。"""

    def __init__(self, skills_root: Path):
        self.skills_root = skills_root.resolve()
        self._skills: dict[str, SkillInfo] = {}
        self.refresh()

    def refresh(self) -> None:
        """重新扫描技能根目录，收集所有包含 SKILL.md 的有效技能包。"""
        self._skills.clear()
        if not self.skills_root.is_dir():
            return
        for child in sorted(self.skills_root.iterdir(), key=lambda p: p.name):
            md_file = child / "SKILL.md"
            if not child.is_dir() or not md_file.is_file():
                continue
            raw = md_file.read_text(encoding="utf-8")
            meta, _body = _parse_frontmatter(raw)
            scripts_dir = child / "scripts"
            scripts = (
                sorted(p.name for p in scripts_dir.glob("*.py") if p.is_file())
                if scripts_dir.is_dir()
                else []
            )
            self._skills[child.name] = SkillInfo(
                skill_id=child.name,
                name=meta.get("name") or child.name,
                description=meta.get("description") or "领域专用作业技能包",
                skill_dir=child.resolve(),
                skill_md_path=md_file.resolve(),
                scripts=scripts,
            )

    def build_catalog_prompt(self) -> str:
        """生成轻量技能清单提示词注入主控上下文，避免一次性塞入所有技能长正文。"""
        if not self._skills:
            return "当前未挂载额外领域技能包。"
        lines = ["可用领域技能列表（遇到匹配任务时，请先调用 `load_skill` 读取完整作业规范与配套脚本路径）："]
        for info in self._skills.values():
            lines.append(f"- `{info.skill_id}` ({info.name})：{info.description}")
        return "\n".join(lines)

    def load_skill_detail(self, skill_id: str) -> dict[str, Any]:
        """按 ID 读取指定技能包的完整 SKILL.md 正文、预设目录与可执行脚本绝对路径。"""
        info = self._skills.get(skill_id.strip())
        if info is None:
            return {
                "status": "not_found",
                "available_skills": list(self._skills.keys()),
                "message": f"未找到名为 `{skill_id}` 的技能包",
            }
        raw = info.skill_md_path.read_text(encoding="utf-8")
        _meta, body = _parse_frontmatter(raw)
        presets_dir = info.skill_dir / "presets"
        presets = (
            [str(p.resolve()) for p in sorted(presets_dir.glob("*.json"))]
            if presets_dir.is_dir()
            else []
        )
        # 自动附带技能包下的 references/*.md 评审量表与作业标准，免去跨目录读取权限受限问题。
        refs_dir = info.skill_dir / "references"
        references = (
            {p.name: p.read_text(encoding="utf-8", errors="replace") for p in sorted(refs_dir.glob("*.md"))}
            if refs_dir.is_dir()
            else {}
        )
        return {
            "status": "ok",
            "skill_id": info.skill_id,
            "name": info.name,
            "skill_dir": str(info.skill_dir),
            "scripts": {name: str((info.skill_dir / "scripts" / name).resolve()) for name in info.scripts},
            "presets": presets,
            "references": references,
            "instructions": body,
        }
