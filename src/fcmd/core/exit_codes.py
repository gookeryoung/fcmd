"""退出码契约：全路径统一（人读与机读一致）。

P1 起，fcmd 的退出码不再「工具各自为政」，而是由本模块唯一定义：

======  ================  ============================================
码值    名称              触发场景
======  ================  ============================================
0       SUCCESS           执行成功（结果已给出）
1       FAILURE           运行期失败（I/O、子进程、内部异常、任务失败）
2       USAGE             调用方用错命令（未知工具/子命令、缺参数、未知选项）
3       INVALID_INPUT     参数值不合法（枚举/格式/范围/互斥冲突）
4       NOT_FOUND         目标不存在（文件/目录/记录）
5       CHECK_FAILED      检查类工具的结论为「不通过」（不是错误）
130     INTERRUPTED       用户中断（Ctrl+C，与 POSIX 约定一致）
======  ================  ============================================

同一码在人读与机读两条路径下含义一致：

* 0：人读=正常结果；机读=stdout ``fcmd-result/1``（``ok: true``）
* 1/2/3/4：人读=``错误: <一句话>`` + ``提示: <改法>``；机读=stderr ``fcmd-error/1``
  （``ok:false``、``kind``、``code``、``exit_code`` 与进程退出码相同）
* 5：人读=现有结论输出 + 一句“检查未通过”；机读=stdout ``fcmd-result/1``
  （``ok:false``，带 ``checks``），不写 stderr 错误体
* 130：人读=``错误: 已中断``；机读=stderr ``fcmd-error/1``（``kind: interrupted``）

``kind`` 与退出码一一对应（见 :data:`ERROR_KINDS`），具体错误码到 ``kind``
的映射集中在 :data:`ERROR_CODE_KINDS`，新增错误码必须在此登记。

本模块只依赖标准库，供 ``core`` / ``infras`` / ``tools`` 复用（ADR 0001）。
"""

from __future__ import annotations

from enum import IntEnum

__all__ = [
    "ERROR_CODE_KINDS",
    "ERROR_KINDS",
    "ExitCode",
    "exit_code_for",
    "kind_for",
]


class ExitCode(IntEnum):
    """fcmd 全路径统一退出码。"""

    SUCCESS = 0
    FAILURE = 1
    USAGE = 2
    INVALID_INPUT = 3
    NOT_FOUND = 4
    CHECK_FAILED = 5
    INTERRUPTED = 130  # 与 POSIX 信号中断一致


#: ``fcmd-error/1`` 的 ``kind`` → 退出码（一一对应，不得复用）
ERROR_KINDS: dict[str, ExitCode] = {
    "runtime": ExitCode.FAILURE,
    "usage": ExitCode.USAGE,
    "input": ExitCode.INVALID_INPUT,
    "not_found": ExitCode.NOT_FOUND,
    "check": ExitCode.CHECK_FAILED,
    "interrupted": ExitCode.INTERRUPTED,
}

#: 具体错误码 → ``kind``；未登记的码回退到 ``input``（见 :func:`kind_for`）
ERROR_CODE_KINDS: dict[str, str] = {
    # 目标不存在
    "FILE_NOT_FOUND": "not_found",
    "DIR_NOT_FOUND": "not_found",
    "NOT_FOUND": "not_found",
    # 参数值不合法
    "INVALID_ALGORITHM": "input",
    "INVALID_ARGUMENT": "input",
    "INVALID_MIN_SIZE": "input",
    "INVALID_REGEX": "input",
    "INVALID_RULES": "input",
    "NOT_PE_FILE": "input",
    "NOT_TEXT": "input",
    "SAME_DIRECTORY": "input",
    # 用法错误
    "UNKNOWN_TOOL": "usage",
    "UNKNOWN_SUBCOMMAND": "usage",
    "USAGE": "usage",
    # 运行期失败
    "RUNTIME_ERROR": "runtime",
    # 结论性失败 / 中断
    "CHECK_FAILED": "check",
    "INTERRUPTED": "interrupted",
}


def kind_for(code: str, *, default: str = "input") -> str:
    """返回具体错误码对应的 ``kind``（未登记时回退 ``default``）。"""
    return ERROR_CODE_KINDS.get(code, default)


def exit_code_for(code: str, *, kind: str | None = None) -> ExitCode:
    """返回具体错误码对应的退出码。

    ``kind`` 显式给出时以其为准（调用方自定分类），否则按错误码查表；
    两者都没有时回退 ``input``（3）。
    """
    resolved = kind if kind is not None else kind_for(code)
    return ERROR_KINDS.get(resolved, ExitCode.INVALID_INPUT)
