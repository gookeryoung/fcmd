"""命令定义 DSL：TOML 声明式命令（exec 型）。

让 ``clr`` 这类简单命令以几行 TOML 声明替代完整 Python 模块：

.. code-block:: toml

    [commands.clr]
    help = "清屏（跨平台）"
    win.cmd = "cls"
    unix.cmd = ["clear"]

两级配置来源：包内 ``fcmd/commands/*.toml``（内置，按文件名拆分）与
``${FCMD_HOME:-~/.fcmd}/commands.toml``（用户自定义）。声明在
``ensure_tools_discovered`` 时统一注册进 ``_TOOL_REGISTRY``，graph/info/
completion 等内建命令与 ``run_tool`` 执行链自动兼容。

分层
----
* :mod:`fcmd.dsl.actions` —— 内建动作注册表（``action`` 命令的进程内实现）
* :mod:`fcmd.dsl.decl` —— 声明与校验（纯函数）
* :mod:`fcmd.dsl.synth` —— 声明 → ToolSpec（平台选定 + 合成签名）
* :mod:`fcmd.dsl.loader` —— 文件定位与解析编排
* :mod:`fcmd.dsl.entry` —— console script 通用入口
"""

from __future__ import annotations

from .actions import Action, action, action_names, get_action, has_action
from .decl import CommandDecl, CommandDeclError, ParamDecl, ToolDecl, parse_command_table, parse_tool_table
from .loader import builtin_tool_decls, user_tool_decls
from .synth import build_tool_spec, select_platform_cmd

# entry 子模块不在包级 re-export：runpy.run_module("fcmd.dsl.entry") 等路径
# 会先把目标模块占位进 sys.modules 再导入父包，父包 __init__ 里的
# ``from .entry import ...`` 会触发 Python RuntimeWarning。
# 需直接引用请从 ``fcmd.dsl.entry`` 导入。

__all__ = [
    "Action",
    "CommandDecl",
    "CommandDeclError",
    "ParamDecl",
    "ToolDecl",
    "action",
    "action_names",
    "build_tool_spec",
    "builtin_tool_decls",
    "get_action",
    "has_action",
    "parse_command_table",
    "parse_tool_table",
    "select_platform_cmd",
    "user_tool_decls",
]
