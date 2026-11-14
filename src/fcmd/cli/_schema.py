"""工具自描述：把运行时注册表投影成人与机器共读的结构。

``fcmd info --json`` 输出 ``fcmd-schema/1`` 文档；``fcmd info`` 的文本表格
复用同一份 :func:`fcmd.core.toolkit.param_schema` 数据，避免两套描述漂移。
文本给人读（可当 ``--help`` 之外的补充说明），JSON 给模型拼参数。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from fcmd.cli._discovery import aliases_for, tool_description
from fcmd.core.result import SCHEMA_SCHEMA
from fcmd.core.toolkit import ToolSpec, param_schema

__all__ = [
    "GUI_TOOLS",
    "overview_schema",
    "spec_kind",
    "subcommand_schema",
    "tool_document",
    "tool_schema",
    "tool_summary",
]

# 纯 Tk 窗口入口：没有 headless 模式，模型调用会弹窗阻塞或在无显示环境报错。
# 静态清单见 docs/analysis/2026-11-13-small-model-tooling-gaps.md §2.1。
GUI_TOOLS: frozenset[str] = frozenset(
    {
        "alarmclock",
        "checksum",
        "cmderw",
        "docdiffw",
        "dynacalcw",
        "envw",
        "filescanw",
        "filesyncw",
        "img2icow",
        "img2pdfw",
        "locllmw",
        "mirrortoolw",
        "ncmconvertw",
        "paramcadw",
        "parameshw",
        "pdfcryptw",
        "pdfsplitw",
        "pevieww",
        "pptmakew",
        "regexvalidatew",
        "renamerw",
        "reshackw",
        "videoconvw",
    }
)


def spec_kind(spec: ToolSpec) -> str:
    """判断 ToolSpec 类型：cmd / aggregate / fn。"""
    if spec.cmd is not None:
        return "cmd"
    if spec.needs:
        return "aggregate"
    return "fn"


def subcommand_schema(spec: ToolSpec) -> dict[str, Any]:
    """单个子命令的 schema（参数、示例、编排字段）。"""
    return {
        "name": spec.subcommand,
        "help": spec.help,
        "kind": spec_kind(spec),
        "hidden": spec.hidden,
        "needs": list(spec.needs),
        "strategy": spec.strategy,
        "params": param_schema(spec),
        "examples": list(spec.examples),
    }


def tool_schema(tool_name: str, subs: Mapping[str | None, ToolSpec]) -> dict[str, Any]:
    """单个工具的完整 schema（含全部子命令）。"""
    return {
        "name": tool_name,
        "aliases": aliases_for(tool_name),
        "description": tool_description(tool_name),
        "gui": tool_name in GUI_TOOLS,
        "subcommands": [subcommand_schema(spec) for spec in sorted(subs.values(), key=lambda s: s.subcommand or "")],
    }


def tool_summary(tool_name: str, subs: Mapping[str | None, ToolSpec]) -> dict[str, Any]:
    """工具列表项（不含参数细节，控制清单体积）。"""
    visible = sum(1 for spec in subs.values() if not spec.hidden)
    hidden = len(subs) - visible
    return {
        "name": tool_name,
        "aliases": aliases_for(tool_name),
        "description": tool_description(tool_name),
        "gui": tool_name in GUI_TOOLS,
        "subcommands": visible,
        "hidden": hidden,
    }


def overview_schema(builtins: Sequence[str], tools: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``fcmd info --json`` 的顶层文档。"""
    return {
        "schema": SCHEMA_SCHEMA,
        "builtins": list(builtins),
        "tools": [dict(item) for item in tools],
    }


def tool_document(tool_name: str, subs: Mapping[str | None, ToolSpec]) -> dict[str, Any]:
    """``fcmd info <tool> --json`` 的顶层文档。"""
    return {"schema": SCHEMA_SCHEMA, "tool": tool_schema(tool_name, subs)}
