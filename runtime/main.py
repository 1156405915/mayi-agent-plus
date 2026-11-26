"""Python 薄内核标准进程入口：通过 UTF-8 NDJSON 与 Electron 宿主双向通信，同时支持命令行独立调试。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.orchestrator import run_orchestrator_turn


def main() -> int:
    """初始化标准流编码、读取单轮会话请求并运行主控协程。"""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    parser = argparse.ArgumentParser(description="Mayi Agent Plus Python Runtime")
    parser.add_argument("--ipc", action="store_true", help="启用与 Electron 主进程的 NDJSON 双向流模式")
    parser.add_argument("--auto-approve", action="store_true", help="调试或无人值守测试时自动放行门禁")
    parser.add_argument("--workspace", help="独立 CLI 运行时的工作区目录")
    parser.add_argument("--source", help="独立 CLI 运行时的只读源资料目录")
    parser.add_argument("--prompt", help="独立 CLI 运行时的任务提示词")
    args = parser.parse_args()

    # 将业务 print 重定向到 stderr，确保 stdout 纯净承载 NDJSON 事件帧
    protocol_stdout = sys.stdout
    sys.stdout = sys.stderr

    def emit(event: dict[str, Any]) -> None:
        protocol_stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
        protocol_stdout.flush()

    def wait_gate_response(gate_id: str) -> dict[str, Any]:
        """在 IPC 模式下阻塞读取宿主发回的门禁响应帧。"""
        if not args.ipc:
            return {"approved": True, "feedback": ""}
        while True:
            line = sys.stdin.readline()
            if not line:
                return {"approved": False, "feedback": "连接已关闭"}
            try:
                frame = json.loads(line)
            except ValueError:
                continue
            if frame.get("type") == "gate_response" and frame.get("id") == gate_id:
                return frame

    try:
        if args.workspace and args.prompt:
            import os
            request = {
                "workspace": str(Path(args.workspace).resolve()),
                "source_directory": str(Path(args.source or args.workspace).resolve()),
                "prompt": args.prompt,
                "config": {
                    "api_key": os.environ.get("MAYI_API_KEY") or os.environ.get("OPENAI_API_KEY") or "",
                    "base_url": os.environ.get("MAYI_BASE_URL") or "https://api.deepseek.com",
                    "model": os.environ.get("MAYI_MODEL") or "deepseek-chat",
                },
            }
        else:
            first_line = sys.stdin.readline() if args.ipc else sys.stdin.read()
            if not first_line.strip():
                raise ValueError("未收到有效的 JSON 请求负载")
            request = json.loads(first_line)

        asyncio.run(
            run_orchestrator_turn(
                request=request,
                project_root=PROJECT_ROOT,
                emit=emit,
                wait_gate_response=wait_gate_response,
                auto_approve=args.auto_approve,
            )
        )
        return 0
    except KeyboardInterrupt:
        emit({"type": "error", "message": "任务已被取消"})
        return 130
    except Exception as exc:
        emit({"type": "error", "error_type": type(exc).__name__, "message": str(exc)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
