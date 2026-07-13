"""fcmd CLI 主入口（P0 最小版）。

P1 阶段将扩展为完整 FcmdApp（路由表 + importlib 懒加载工具模块）。
当前仅支持 --version 与 --help，用于验证 entry point 可用。
"""

from __future__ import annotations

import argparse

from fcmd import __version__

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="fcmd",
        description="极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> None:
    """主入口：解析参数并执行。"""
    _build_parser().parse_args()


if __name__ == "__main__":
    main()
