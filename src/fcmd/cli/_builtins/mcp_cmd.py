"""``fcmd mcp serve`` 内建命令：启动 MCP stdio server。"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from fcmd.core.exit_codes import ExitCode

__all__ = ["run"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fcmd mcp", description="MCP 集成（可选依赖 fcmd[agent]）")
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("serve", help="启动 MCP stdio server")
    return parser


def run(argv: list[str]) -> int:
    """分发 ``fcmd mcp``；``serve`` 会阻塞直到客户端断开。"""
    parser = _parser()
    args = parser.parse_args(argv)
    action: Any = args.action
    if action is None:
        parser.print_help()
        return int(ExitCode.USAGE)
    if action != "serve":
        parser.error(f"未知 MCP 动作: {action}")
    from fcmd.mcp.server import run_mcp_stdio

    try:
        run_mcp_stdio()
    except SystemExit as exc:  # 缺 mcp 依赖时 run_mcp_stdio 以 sys.exit 报告安装提示
        code = exc.code
        return int(code) if isinstance(code, int) else int(ExitCode.FAILURE)
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return int(ExitCode.INTERRUPTED)
    return int(ExitCode.SUCCESS)
