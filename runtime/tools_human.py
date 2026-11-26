"""原子工具组 5（五步认知进度同步与自适应门禁）：驱动右栏五步看板并在关键规划节点支持人工拍板或在线微调。"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

VALID_PHASE_IDS = ("perceive", "align", "plan", "execute", "verify")


class HumanInteractionTool:
    """管理五步认知进度广播以及与宿主前端之间的阻塞式门禁确认交互。"""

    def __init__(
        self,
        workspace_dir: Path,
        emit_event: Callable[[dict[str, Any]], None],
        wait_gate_response: Callable[[str], dict[str, Any]],
        auto_approve: bool = False,
    ):
        self.workspace_dir = workspace_dir.resolve()
        self.emit_event = emit_event
        self.wait_gate_response = wait_gate_response
        self.auto_approve = auto_approve

    def update_cognitive_steps(self, steps: list[dict[str, str]]) -> dict[str, Any]:
        """
        同步更新工作台右栏的五步认知闭环进度条。
        `steps` 包含各阶段 `{id, title, status, detail}`，其中 `status` 取值为 'pending' | 'in_progress' | 'done' | 'skipped'。
        推荐的五个标准阶段 ID 为：perceive（感知标准化）、align（目标与约束对齐）、plan（全局规划）、execute（分治产出）、verify（客观验证交付）。
        """
        normalized = []
        for item in steps[:8]:
            normalized.append({
                "id": str(item.get("id") or "step"),
                "title": str(item.get("title") or "执行阶段"),
                "status": str(item.get("status") or "pending"),
                "detail": str(item.get("detail") or ""),
            })
        self.emit_event({"type": "steps", "steps": normalized})
        return {"status": "updated", "step_count": len(normalized)}

    def ask_user_gate(
        self,
        title: str,
        summary: str,
        review_files: list[str] | None = None,
        kind: str = "plan_approval",
    ) -> dict[str, Any]:
        """
        触发自适应人机对齐门禁（用于复杂长任务完成 `01-requirements.md` 与 `02-plan.md` 后请用户确认方向，或询问缺失的关键事实）。
        右栏会自动高亮打开 `review_files` 供用户审阅甚至直接编辑保存；返回用户的确认状态、补充反馈以及审阅文件最新内容快照。
        """
        files = [f.strip().replace("\\", "/").lstrip("/") for f in (review_files or []) if f.strip()]
        before_hashes: dict[str, str] = {}
        for rel in files:
            fp = (self.workspace_dir / rel).resolve()
            if fp.is_file() and fp.is_relative_to(self.workspace_dir):
                before_hashes[rel] = fp.read_text(encoding="utf-8", errors="replace")

        if self.auto_approve:
            return {
                "approved": True,
                "auto_approved": True,
                "feedback": "自动放行模式已启用，请按当前规划继续执行后续阶段。",
                "modified_files": [],
            }

        gate_id = uuid.uuid4().hex
        self.emit_event({
            "type": "gate",
            "id": gate_id,
            "kind": kind,
            "title": title,
            "summary": summary,
            "reviewFiles": files,
        })

        resp = self.wait_gate_response(gate_id)
        approved = bool(resp.get("approved", True))
        feedback = str(resp.get("feedback") or "").strip()

        # 检测用户在门禁暂停期间是否在右侧编辑器手工微调了规划或要求文档，若是则主动提示模型重读最新文件！
        modified_files: list[str] = []
        for rel, old_content in before_hashes.items():
            fp = (self.workspace_dir / rel).resolve()
            if fp.is_file():
                new_content = fp.read_text(encoding="utf-8", errors="replace")
                if new_content != old_content:
                    modified_files.append(rel)

        return {
            "approved": approved,
            "feedback": feedback,
            "modified_files": modified_files,
            "note": (
                f"用户在门禁确认期间直接编辑保存了 {modified_files}，请务必调用 read_lines 读取其最新版本后再执行！"
                if modified_files
                else "用户已响应门禁决策。"
            ),
        }
