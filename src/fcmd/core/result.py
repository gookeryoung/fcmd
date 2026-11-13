"""结果信封：把同一份内部结果渲染为人类可读或机器可读两种形态。

设计约束（与 ``docs/analysis/2026-11-13-small-model-tooling-gaps.md`` 对齐）：

* 默认输出保持人类可读形态不变；``--json`` 是增量开关，不替换默认路径。
* 两种呈现来自同一份数据：工具先构造 ``data``，再分别渲染，避免两套逻辑漂移。
* 错误既给人一句可读的话（``message``），也给机器可判定、可自纠的字段
  （``code`` / ``kind`` / ``field`` / ``expected`` / ``hint``）。
* 信封版本化：``fcmd-result/1`` / ``fcmd-error/1`` / ``fcmd-schema/1``。

本模块只依赖标准库，供 ``core`` / ``infras`` / ``tools`` 各层复用（ADR 0001）。
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any, TextIO

from fcmd.core.exit_codes import ExitCode, exit_code_for, kind_for

__all__ = [
    "ERROR_SCHEMA",
    "RESULT_SCHEMA",
    "SCHEMA_SCHEMA",
    "emit_error",
    "emit_json",
    "emit_result",
    "has_pending_error",
    "pending_exit_code",
    "record_error",
    "report_error",
    "reset_error_state",
    "take_pending_exit_code",
]

# 成功结果信封（stdout）
RESULT_SCHEMA = "fcmd-result/1"
# 错误信封（stderr）
ERROR_SCHEMA = "fcmd-error/1"
# 工具自描述信封（stdout，fcmd info --json）
SCHEMA_SCHEMA = "fcmd-schema/1"

# 已呈现错误的退出码：工具只负责打印/写错误，退出码由框架统一收取。
# 只保留第一个错误（根因），后续错误不覆盖，避免同一次调用出现语义漂移。
#
# 双份存储的原因：``thread``/``async`` 策略会把工具函数放到线程池执行，而
# ``ContextVar`` 不跨线程传播——若只存 ContextVar，工作线程里 ``print_error``
# 登记的码在主线程取不到，就退化成「退出 0 但终端有错误」。因此额外维护一份
# 进程级（线程安全）登记：同一进程一次 CLI 调用只有一个工具在跑，全局值不会
# 串味；``run_tool`` 入口统一 :func:`reset_error_state`，跨调用不残留。
_PENDING_EXIT_CODE: ContextVar[int | None] = ContextVar("fcmd_pending_exit_code", default=None)
_PENDING_EXIT_CODE_GLOBAL: int | None = None
_PENDING_LOCK = threading.Lock()


def record_error(code: str, *, kind: str | None = None) -> ExitCode:
    """登记一次已呈现的错误，返回其退出码。

    人读路径（``print_error``）与机读路径（:func:`emit_error`）都走这里，
    保证同一个错误码在两条路径下给出同一个退出码。只记录第一个错误。
    """
    global _PENDING_EXIT_CODE_GLOBAL  # noqa: PLW0603 - 进程级单值，见上方注释

    exit_code = exit_code_for(code, kind=kind)
    with _PENDING_LOCK:
        if _PENDING_EXIT_CODE_GLOBAL is None:
            _PENDING_EXIT_CODE_GLOBAL = int(exit_code)
    if _PENDING_EXIT_CODE.get() is None:
        _PENDING_EXIT_CODE.set(int(exit_code))
    return exit_code


def pending_exit_code() -> int | None:
    """返回当前已登记的退出码（未登记为 ``None``），不清空。"""
    local = _PENDING_EXIT_CODE.get()
    if local is not None:
        return local
    with _PENDING_LOCK:
        return _PENDING_EXIT_CODE_GLOBAL


def has_pending_error() -> bool:
    """判断本次调用是否已经呈现过错误。"""
    return pending_exit_code() is not None


def take_pending_exit_code() -> int | None:
    """取出并清空已登记的退出码（框架在工具返回后调用）。"""
    global _PENDING_EXIT_CODE_GLOBAL  # noqa: PLW0603 - 进程级单值，见上方注释

    local = _PENDING_EXIT_CODE.get()
    with _PENDING_LOCK:
        shared = _PENDING_EXIT_CODE_GLOBAL
        _PENDING_EXIT_CODE_GLOBAL = None
    _PENDING_EXIT_CODE.set(None)
    return local if local is not None else shared


def reset_error_state() -> None:
    """清空登记状态；``run_tool`` 每次入口调用一次，避免跨调用污染。"""
    global _PENDING_EXIT_CODE_GLOBAL  # noqa: PLW0603 - 进程级单值，见上方注释

    _PENDING_EXIT_CODE.set(None)
    with _PENDING_LOCK:
        _PENDING_EXIT_CODE_GLOBAL = None


def emit_json(document: Mapping[str, Any], *, stream: TextIO | None = None) -> None:
    """向 ``stream``（默认 stdout）写一个缩进 JSON 对象。

    使用 ``ensure_ascii=False`` 保留中文原文；``indent=2`` 便于人直接阅读，
    也仍是单个合法 JSON 对象，机器可整体解析。
    """
    target = sys.stdout if stream is None else stream
    print(json.dumps(document, ensure_ascii=False, indent=2), file=target)


def emit_result(tool: str, subcommand: str | None, data: Mapping[str, Any], *, ok: bool = True) -> None:
    """输出成功结果信封（stdout）。

    ``data`` 为工具自己的结构化结果；信封只补充稳定的公共字段，便于调用方
    按 ``schema`` / ``tool`` / ``subcommand`` 分派，而不必猜输出格式。

    ``ok=False`` 用于「检查类工具结论为不通过」（退出码 5）：结果仍写在
    stdout（有结构化 ``checks`` 可读），不写 stderr 错误体——它不是错误，
    而是结论。退出码由调用方与 ``ok`` 保持一致返回 5。
    """
    emit_json(
        {
            "schema": RESULT_SCHEMA,
            "ok": ok,
            "tool": tool,
            "subcommand": subcommand,
            "data": dict(data),
        }
    )


def emit_error(  # noqa: PLR0913 - 错误信封字段齐全，可选字段按需传入
    tool: str,
    subcommand: str | None,
    *,
    code: str,
    message: str,
    kind: str | None = None,
    field: str | None = None,
    expected: str | None = None,
    hint: str | None = None,
) -> int:
    """输出错误信封（stderr），并登记退出码；返回该退出码。

    参数
    ----
    code:
        稳定的机器可判定错误码（如 ``DIR_NOT_FOUND``），不随文案变化。
    message:
        一句人类可读的错误说明（与默认模式的错误行同语义）。
    kind:
        错误类别：``usage`` / ``input`` / ``runtime`` / ``check`` / ``not_found`` /
        ``interrupted``。省略时按 :data:`fcmd.core.exit_codes.ERROR_CODE_KINDS`
        从 ``code`` 推导，保证与退出码一一对应。
    field:
        出错的参数名或字段名，便于模型定位需要修正的入参。
    expected:
        该参数期望的形态或取值（如“已存在的目录”）。
    hint:
        可执行的修正建议（如“确认路径存在且为目录”）。

    返回
    ----
    int
        与进程退出码一致；工具可直接 ``return emit_error(...)``。
    """
    resolved_kind = kind if kind is not None else kind_for(code)
    exit_code = record_error(code, kind=resolved_kind)
    payload: dict[str, Any] = {
        "schema": ERROR_SCHEMA,
        "ok": False,
        "tool": tool,
        "subcommand": subcommand,
        "kind": resolved_kind,
        "code": code,
        "message": message,
        "exit_code": int(exit_code),
    }
    if field is not None:
        payload["field"] = field
    if expected is not None:
        payload["expected"] = expected
    if hint is not None:
        payload["hint"] = hint
    emit_json(payload, stream=sys.stderr)
    return int(exit_code)


def report_error(  # noqa: PLR0913 - 与 emit_error 同字段，两种渲染共享入参
    tool: str,
    subcommand: str | None,
    *,
    code: str,
    message: str,
    json_output: bool,
    kind: str | None = None,
    field: str | None = None,
    expected: str | None = None,
    hint: str | None = None,
) -> int:
    """按 ``json_output`` 选择错误渲染，并返回与退出码一致的值。

    - ``json_output=True``：stderr 写 ``fcmd-error/1`` 信封（:func:`emit_error`）
    - ``json_output=False``：终端输出 ``错误: <message>`` + ``提示: <hint>``
      （``fcmd.console.print_error``）

    两条路径都经 :func:`record_error` 登记同一个退出码，因此工具只需
    ``return report_error(...)``，不会出现「机读有错、人读退出 0」的分叉。
    """
    if json_output:
        return emit_error(
            tool,
            subcommand,
            code=code,
            message=message,
            kind=kind,
            field=field,
            expected=expected,
            hint=hint,
        )
    from fcmd.console import print_error  # 下沉导入：保持 core.result 仅依赖标准库

    print_error(message, code=code, hint=hint)
    return int(exit_code_for(code, kind=kind))
