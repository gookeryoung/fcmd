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
from collections.abc import Mapping
from typing import Any, TextIO

__all__ = [
    "ERROR_SCHEMA",
    "RESULT_SCHEMA",
    "SCHEMA_SCHEMA",
    "emit_error",
    "emit_json",
    "emit_result",
]

# 成功结果信封（stdout）
RESULT_SCHEMA = "fcmd-result/1"
# 错误信封（stderr）
ERROR_SCHEMA = "fcmd-error/1"
# 工具自描述信封（stdout，fcmd info --json）
SCHEMA_SCHEMA = "fcmd-schema/1"


def emit_json(document: Mapping[str, Any], *, stream: TextIO | None = None) -> None:
    """向 ``stream``（默认 stdout）写一个缩进 JSON 对象。

    使用 ``ensure_ascii=False`` 保留中文原文；``indent=2`` 便于人直接阅读，
    也仍是单个合法 JSON 对象，机器可整体解析。
    """
    target = sys.stdout if stream is None else stream
    print(json.dumps(document, ensure_ascii=False, indent=2), file=target)


def emit_result(tool: str, subcommand: str | None, data: Mapping[str, Any]) -> None:
    """输出成功结果信封（stdout）。

    ``data`` 为工具自己的结构化结果；信封只补充稳定的公共字段，便于调用方
    按 ``schema`` / ``tool`` / ``subcommand`` 分派，而不必猜输出格式。
    """
    emit_json(
        {
            "schema": RESULT_SCHEMA,
            "ok": True,
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
    kind: str = "input",
    field: str | None = None,
    expected: str | None = None,
    hint: str | None = None,
) -> None:
    """输出错误信封（stderr）。

    参数
    ----
    code:
        稳定的机器可判定错误码（如 ``DIR_NOT_FOUND``），不随文案变化。
    message:
        一句人类可读的错误说明（与默认模式的错误行同语义）。
    kind:
        错误类别：``usage`` / ``input`` / ``runtime`` / ``check``。
    field:
        出错的参数名或字段名，便于模型定位需要修正的入参。
    expected:
        该参数期望的形态或取值（如“已存在的目录”）。
    hint:
        可执行的修正建议（如“确认路径存在且为目录”）。
    """
    payload: dict[str, Any] = {
        "schema": ERROR_SCHEMA,
        "ok": False,
        "tool": tool,
        "subcommand": subcommand,
        "kind": kind,
        "code": code,
        "message": message,
    }
    if field is not None:
        payload["field"] = field
    if expected is not None:
        payload["expected"] = expected
    if hint is not None:
        payload["hint"] = hint
    emit_json(payload, stream=sys.stderr)
