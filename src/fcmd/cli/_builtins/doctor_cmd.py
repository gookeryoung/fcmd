"""``fcmd doctor`` 内建命令：环境健康诊断。"""

from __future__ import annotations

import argparse

from fcmd.cli._discovery import _TOOL_MODULES, ensure_tools_discovered
from fcmd.cli._doctor_helpers import collect_doctor_checks, doctor_result, render_doctor_report
from fcmd.core.result import emit_result

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd doctor``。

    环境健康诊断（只读，输出 OK/FAIL 状态表格）：

    - Python 版本 ≥ 3.13
    - fcmd 核心模块导入正常
    - 工具模块全部可正常导入（统计失败数）
    - 可选依赖（img/pdf/ocr）状态
    - PATH 中的常用外部命令（git/uv/python）可用性

    ``--json``：同一份检查项输出为 ``fcmd-result/1``（含 ``checks[{item,ok,detail,fix}]``），
    供模型逐项判定；默认表格输出不变。

    退出码：全部通过返回 0，有失败项返回 1（两种输出形态一致）。
    """
    parser = argparse.ArgumentParser(
        prog="fcmd doctor",
        description="环境健康诊断",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出检查项（机器可读，含修复建议）")
    parsed = parser.parse_args(argv)

    ensure_tools_discovered()
    checks = collect_doctor_checks(_TOOL_MODULES)
    if parsed.json:
        result = doctor_result(checks)
        emit_result("doctor", None, result)
        return 0 if result["ok"] else 1
    return render_doctor_report(checks)
