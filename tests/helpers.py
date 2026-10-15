"""测试共用辅助：DSL 动作输出捕获。"""

from __future__ import annotations

import pytest

from fcmd.apis.toolkit import run_tool


def run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    """运行工具并返回 (退出码, 过滤掉控制台帧后的输出)。"""
    code = run_tool(name, argv)
    raw = capsys.readouterr().out
    # 保留动作 print 输出，去掉 > / OK / FAILED 开头的结果帧行
    lines = [
        ln
        for ln in raw.splitlines()
        if not ln.startswith("> ") and not ln.startswith("OK ") and not ln.startswith("FAILED ")
    ]
    return code, "\n".join(lines)
