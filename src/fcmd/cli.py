"""fcmd CLI 入口."""

from __future__ import annotations

import argparse

from fcmd import __version__

__all__ = ["main"]


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器."""
    parser = argparse.ArgumentParser(prog="fcmd", description="极速 python 工具集。")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main() -> None:
    """主入口，解析参数并执行."""
    _build_parser().parse_args()


if __name__ == "__main__":
    main()
