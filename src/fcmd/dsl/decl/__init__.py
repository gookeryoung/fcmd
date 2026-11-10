"""命令声明层：TOML 表 → CommandDecl / ToolDecl（纯函数，无 I/O）。

承载命令定义 DSL 的声明与校验，支持两种形态：

* **单命令**（``[commands.<name>]``）：整个表是一条 exec 命令声明，解析为
  :class:`CommandDecl`（子命令为 ``None``）。
* **多子命令**（``[commands.<tool>.<sub>]``）：工具表的每个子表是一个子命令，
  解析为 :class:`ToolDecl`（含多个 :class:`CommandDecl`），支持 ``needs``
  依赖（引用同工具其他子命令）与 ``strategy`` 执行策略——与 ``@fx.tool``
  的 cmd/聚合任务语义对齐。

非法声明抛 :class:`CommandDeclError`（含命令名与键名上下文，便于用户定位
配置错误）。本层不读文件、不注册工具——文件定位与解析编排见
:mod:`fcmd.dsl.loader`，声明到 :class:`~fcmd.core.toolkit.args.ToolSpec` 的
转换见 :mod:`fcmd.dsl.synth`。

实现按职责拆为 :mod:`fcmd.dsl.decl.errors` / :mod:`fcmd.dsl.decl.params` /
:mod:`fcmd.dsl.decl.commands` 三个子模块，本包 re-export 原有全部名字，
``from fcmd.dsl.decl import X`` 的既有导入路径不变。
"""

from __future__ import annotations

from .commands import (  # noqa: F401  门面 re-export：保留拆分前的模块级名字
    _CONTENT_PARAM_TYPES,
    _CONTENT_TOKEN_RE,
    _PLATFORM_KEYS,
    _RESERVED_NAMES,
    _STRATEGIES,
    _SUB_NAME_RE,
    _TOOL_NAME_RE,
    _TOP_KEYS,
    _WHEN_CMD_EXPECTS,
    _WHEN_KEYS,
    _WHEN_PATH_EXPECTS,
    CommandDecl,
    ComputeDecl,
    ToolDecl,
    WhenDecl,
    parse_command_table,
    parse_tool_table,
)
from .errors import CommandDeclError
from .params import (  # noqa: F401  门面 re-export：保留拆分前的模块级名字
    _PARAM_KEYS,
    _PARAM_NAME_RE,
    _PARAM_TYPES,
    _RESERVED_PARAM_NAMES,
    ParamDecl,
    parse_param_table,
)

__all__ = [
    "CommandDecl",
    "CommandDeclError",
    "ComputeDecl",
    "ParamDecl",
    "ToolDecl",
    "WhenDecl",
    "parse_command_table",
    "parse_tool_table",
]
