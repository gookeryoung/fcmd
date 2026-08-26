"""``fcmd doctor`` 内建命令：环境健康诊断。"""

from __future__ import annotations

import argparse

from fcmd.cli._discovery import _TOOL_MODULES, ensure_tools_discovered
from fcmd.cli._doctor_helpers import collect_doctor_checks, render_doctor_report

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd doctor``。

    环境健康诊断（只读，输出 OK/FAIL 状态表格）：

    - Python 版本 ≥ 3.8
    - fcmd 核心模块导入正常
    - 工具模块全部可正常导入（统计失败数）
    - 可选依赖（img/pdf/ocr）状态
    - PATH 中的常用外部命令（git/uv/python）可用性

    退出码：全部通过返回 0，有失败项返回 1。
    """
    parser = argparse.ArgumentParser(
        prog="fcmd doctor",
        description="环境健康诊断",
    )
    parser.parse_args(argv) if argv else parser.parse_args([])

    ensure_tools_discovered()
    checks = collect_doctor_checks(_TOOL_MODULES)
    return render_doctor_report(checks)
