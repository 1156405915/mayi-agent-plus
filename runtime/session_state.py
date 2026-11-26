"""会话跨轮次检查点持久化、中断工具调用自愈、零损耗上下文压缩与超大工具输出溢出落盘管理。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agentscope.memory import InMemoryMemory
from agentscope.message import Msg

MAX_INLINE_TOOL_CHARS = 14_000
INLINE_PREVIEW_CHARS = 10_000
MEMORY_COMPACT_TRIGGER_CHARS = 56_000


def spill_large_tool_output(
    workspace_dir: Path,
    tool_name: str,
    call_id: str,
    value: Any,
    max_inline_chars: int = MAX_INLINE_TOOL_CHARS,
) -> tuple[str, bool]:
    """
    当工具返回 JSON 字符串超过内联阈值时，自动将完整原文写入 `.workspace/.tool_outputs/`，
    并向模型返回合法精简的 JSON 信封（附带预览与文件路径），避免硬截断造成非法 JSON 或撑爆上下文窗口。
    """
    serialized = json.dumps(value, ensure_ascii=False)
    if len(serialized) <= max_inline_chars:
        return serialized, False

    out_dir = workspace_dir / ".workspace" / ".tool_outputs"
    out_dir.mkdir(parents=True, exist_ok=True)
    rel_file = f".workspace/.tool_outputs/{tool_name}_{call_id[:8]}.json"
    full_path = workspace_dir / rel_file
    full_path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    envelope = {
        "_spilled_to_file": True,
        "tool": tool_name,
        "total_characters": len(serialized),
        "saved_output_file": rel_file,
        "hint": (
            f"工具返回内容较长（共 {len(serialized)} 字符），完整结果已无损保存至 `{rel_file}`。"
            "下方为前段预览；如需查阅后续条目，请对该文件调用 `read_lines` 或 `search_text(scope='workspace')`。"
        ),
        "preview": serialized[:INLINE_PREVIEW_CHARS],
    }
    return json.dumps(envelope, ensure_ascii=False), True


async def reconcile_interrupted_tools(memory: InMemoryMemory) -> int:
    """
    扫描恢复出的会话记忆：若上一轮因用户点击“停止任务”或进程中断遗留了未闭合的 `tool_use` 块，
    自动补齐对应的 `tool_result` 系统回执，防止 OpenAI/DeepSeek 接口因 tool_call 配对缺失报 400 错误。
    """
    messages = await memory.get_memory()
    completed_ids = {
        block.get("id")
        for msg in messages
        for block in msg.get_content_blocks("tool_result")
        if isinstance(block, dict) and block.get("id")
    }
    pending_uses = [
        block
        for msg in messages
        for block in msg.get_content_blocks("tool_use")
        if isinstance(block, dict) and block.get("id") and block.get("id") not in completed_ids
    ]
    if not pending_uses:
        return 0

    recovery_blocks = [
        {
            "type": "tool_result",
            "id": block["id"],
            "name": block.get("name", "unknown_tool"),
            "output": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "status": "interrupted",
                            "note": "上一轮任务在此工具执行期间被中断，请先检查工作区已有文件状态再继续，避免重复执行已完成的工作。",
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        }
        for block in pending_uses
    ]
    await memory.add(Msg("system", recovery_blocks, "system"))
    return len(pending_uses)


async def compact_memory_if_needed(
    memory: InMemoryMemory,
    trigger_chars: int = MEMORY_COMPACT_TRIGGER_CHARS,
    keep_recent_messages: int = 10,
) -> bool:
    """
    确定性零损耗上下文窗口瘦身：当累计记忆字符数超过阈值时，保留最近 `keep_recent_messages` 条消息不动，
    仅将更早轮次中体积庞大的 `tool_result` 正文折叠为精简摘要，严格保持 `tool_use` 与 `tool_result` ID 配对合法。
    无需额外调用大模型生成摘要，杜绝结构化摘要校验失败风险。
    """
    messages = await memory.get_memory()
    if len(messages) <= keep_recent_messages:
        return False

    def _msg_chars(m: Msg) -> int:
        if isinstance(m.content, str):
            return len(m.content)
        return len(json.dumps(m.content, ensure_ascii=False))

    total_chars = sum(_msg_chars(m) for m in messages)
    if total_chars <= trigger_chars:
        return False

    cutoff = len(messages) - keep_recent_messages
    compacted_any = False

    for idx in range(cutoff):
        msg = messages[idx]
        if not isinstance(msg.content, list):
            continue
        new_blocks = []
        changed_this_msg = False
        for block in msg.content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                # 永不折叠已加载的领域技能规程（含后续阶段必需的编译/绘图/体检脚本路径）
                if block.get("name") == "load_skill_detail":
                    new_blocks.append(block)
                    continue
                raw_out = block.get("output")
                out_str = json.dumps(raw_out, ensure_ascii=False) if not isinstance(raw_out, str) else raw_out
                if len(out_str) > 900:
                    short_note = (
                        f"[历史工具 `{block.get('name', '')}` 输出已折叠释放上下文（原长 {len(out_str)} 字符）；"
                        f"工作区文件仍在磁盘可随时重读] 摘要预览: {out_str[:320]}..."
                    )
                    new_blocks.append({
                        **block,
                        "output": [{"type": "text", "text": short_note}],
                    })
                    changed_this_msg = True
                    compacted_any = True
                    continue
            new_blocks.append(block)
        if changed_this_msg:
            msg.content = new_blocks

    return compacted_any


class SessionStateManager:
    """负责 `.workspace/.state/checkpoint.json` 的原子化保存、损坏回退恢复与已加载技能追踪。"""

    def __init__(self, workspace_dir: Path):
        self.state_dir = workspace_dir.resolve() / ".workspace" / ".state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path = self.state_dir / "checkpoint.json"
        self.backup_path = self.state_dir / "checkpoint.backup.json"

    def load(self, current_source_dir: str) -> dict[str, Any] | None:
        """读取最新检查点；若切换了源资料目录则自动开启新上下文，避免旧目录文件路径混淆。"""
        for candidate in (self.checkpoint_path, self.backup_path):
            if not candidate.is_file():
                continue
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("memory"), dict):
                    continue
                if data.get("source_directory") != current_source_dir:
                    return None
                return data
            except (OSError, ValueError, TypeError):
                continue
        return None

    def save(
        self,
        source_directory: str,
        memory: InMemoryMemory,
        loaded_skills: list[str],
        cognitive_steps: list[dict[str, str]],
    ) -> None:
        """先备份上一版有效检查点，再通过临时文件原子替换写入最新会话记忆与五步进度。"""
        payload = {
            "version": 1,
            "source_directory": source_directory,
            "loaded_skills": sorted(set(loaded_skills)),
            "cognitive_steps": cognitive_steps,
            "memory": memory.state_dict(),
        }
        encoded = json.dumps(payload, ensure_ascii=False)
        if self.checkpoint_path.is_file():
            try:
                self.backup_path.write_bytes(self.checkpoint_path.read_bytes())
            except OSError:
                pass
        tmp_path = self.state_dir / "checkpoint.tmp"
        tmp_path.write_text(encoded, encoding="utf-8")
        tmp_path.replace(self.checkpoint_path)
