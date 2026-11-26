"""原子工具组 3（受控代码执行）：允许模型执行技能内置脚本或自编 Python 分析/校验脚本，补齐确定性计算能力。"""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any, Callable


class PythonExecTool:
    """在隔离环境变量与工作区上下文中运行 Python 脚本，捕获标准输出并同步新生成的交付文件。"""

    def __init__(
        self,
        project_root: Path,
        workspace_dir: Path,
        source_dir: Path,
        skills_root: Path,
        on_workspace_changed: Callable[[str], None] | None = None,
    ):
        self.project_root = project_root.resolve()
        self.workspace_dir = workspace_dir.resolve()
        self.source_dir = source_dir.resolve()
        self.skills_root = skills_root.resolve()
        self.on_workspace_changed = on_workspace_changed

    def _clean_env(self) -> dict[str, str]:
        """构造剥离模型 API 密钥的干净运行环境变量，同时注入工作区、源目录与内部引擎模块路径。"""
        env: dict[str, str] = {}
        for k, v in os.environ.items():
            if any(secret in k.upper() for secret in ("API_KEY", "SECRET", "TOKEN", "PASSWORD", "AUTHORIZATION")):
                continue
            env[k] = v

        existing_py = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = (
            f"{self.project_root}{os.pathsep}{existing_py}" if existing_py else str(self.project_root)
        )
        env["PYTHONIOENCODING"] = "utf-8"
        env["MAYI_WORKSPACE_DIR"] = str(self.workspace_dir)
        env["MAYI_SOURCE_DIR"] = str(self.source_dir)
        return env

    def run_python(
        self,
        code: str = "",
        script_path: str = "",
        args: list[str] | None = None,
        timeout_sec: int = 120,
    ) -> dict[str, Any]:
        """
        在受控 Python 环境中执行数据处理、Excel 精确统计、图表生成或技能配套脚本（如 Word 编译与体检）。
        可传入内联 `code` 字符串，或指定 `script_path`（工作区脚本或 `skills/<id>/scripts/*.py`）与命令行参数 `args`。
        """
        if not code.strip() and not script_path.strip():
            raise ValueError("必须提供 `code`（内联代码）或 `script_path`（脚本路径）之一")

        if script_path.strip():
            cand = Path(script_path.strip())
            resolved = cand.resolve() if cand.is_absolute() else (self.workspace_dir / cand).resolve()
            if not resolved.is_file():
                alt = (self.project_root / script_path.strip()).resolve()
                if alt.is_file():
                    resolved = alt
            if not (
                resolved.is_relative_to(self.workspace_dir)
                or resolved.is_relative_to(self.skills_root)
            ):
                raise PermissionError(f"仅允许运行工作区内或 skills/ 目录下的受信任脚本: {script_path}")
            target_script = resolved
        else:
            scripts_dir = self.workspace_dir / ".workspace" / "scripts"
            scripts_dir.mkdir(parents=True, exist_ok=True)
            target_script = scripts_dir / f"adhoc_{uuid.uuid4().hex[:8]}.py"
            target_script.write_text(code, encoding="utf-8")

        cmd = [sys.executable, str(target_script), *(args or [])]
        bounded_timeout = max(5, min(int(timeout_sec), 300))

        proc = subprocess.run(
            cmd,
            cwd=str(self.workspace_dir),
            env=self._clean_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=bounded_timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        if self.on_workspace_changed:
            self.on_workspace_changed(".")

        return {
            "status": "succeeded" if proc.returncode == 0 else "failed",
            "exit_code": proc.returncode,
            "stdout": (proc.stdout or "").strip()[-8000:],
            "stderr": (proc.stderr or "").strip()[-4000:],
        }
