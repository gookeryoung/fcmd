"""声明 → ToolSpec：平台命令选定与合成函数签名。

DSL 声明到 :class:`~fcmd.apis._tool_args.ToolSpec` 的转换层：

* :func:`select_platform_cmd` —— 按平台从声明选定最终 cmd（win32 命中
  ``win_cmd``，其余命中 ``unix_cmd``，均缺回退 ``cmd``）。平台作为显式
  参数传入，双平台分支可直测，无需 monkeypatch。
* :func:`build_tool_spec` —— 构造 ToolSpec：合成占位函数（cmd 任务函数体
  不执行，签名仅驱动 CLI，与 ``@fx.tool(cmd=...)`` 的既有语义一致）。

签名合成机制：stdlib ``inspect.signature`` 优先读取函数对象的
``__signature__`` 属性、``typing.get_type_hints`` 读取 ``__annotations__``
字典，两者均支持运行时注入——参数声明 (:class:`~fcmd.dsl.decl.ParamDecl`)
转换为真实类型注解（``Literal`` 动态构造）与 :class:`inspect.Signature`，
零改动复用 :func:`fcmd.apis._tool_args._build_parser_for_tool` 的签名推导。

合成函数无源码（``inspect.getsource`` 抛 OSError），但 exec 型命令必有
cmd，``_is_aggregate`` 在 ``spec.cmd is not None`` 时短路返回 False，
函数逻辑分析路径完全不会被触及。
"""

from __future__ import annotations

import inspect
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fcmd.apis._tool_args import ToolSpec

from .decl import CommandDecl, CommandDeclError, ParamDecl

__all__ = ["build_tool_spec", "select_platform_cmd"]

# 参数类型名 → 真实类型对象（choices 特判，见 _param_annotation）
_TYPE_MAP: dict[str, type] = {"str": str, "int": int, "float": float, "bool": bool, "path": Path}


def select_platform_cmd(decl: CommandDecl, platform: str = sys.platform) -> str | tuple[str, ...]:
    """按平台选定命令：``win32`` 命中 ``win_cmd``，其余命中 ``unix_cmd``，均缺回退 ``cmd``。

    Parameters
    ----------
    decl:
        命令声明
    platform:
        平台标识（``sys.platform`` 值），默认当前平台

    Returns
    -------
    str | tuple[str, ...]
        选定的命令（str → shell 执行，tuple → 无 shell 执行）

    Raises
    ------
    CommandDeclError
        该平台无可用命令（未提供对应平台分支也未提供通用回退）
    """
    if platform == "win32":
        selected = decl.win_cmd if decl.win_cmd is not None else decl.cmd
    else:
        selected = decl.unix_cmd if decl.unix_cmd is not None else decl.cmd
    if selected is None:
        raise CommandDeclError(f"命令 {decl.name!r} 在平台 {platform!r} 无可用命令（未提供对应平台分支或通用回退）")
    return selected


def _param_annotation(param: ParamDecl) -> Any:
    """ParamDecl 类型声明 → 真实类型注解对象。

    ``choices`` 动态构造 ``Literal[c1, c2, ...]``（argparse 自动映射为 choices）。
    """
    if param.type == "choices":
        return Literal[param.choices]  # type: ignore[valid-type]
    return _TYPE_MAP[param.type]


def _param_default(param: ParamDecl) -> Any:
    """ParamDecl 默认值 → 签名默认值（path 类型包装为 Path）。"""
    if param.type == "path" and param.default is not None:
        return Path(str(param.default))
    return param.default


def _synthesize_func(decl: CommandDecl) -> Callable[..., Any]:
    """生成签名驱动的占位函数（cmd 任务：函数体不执行，签名仅驱动 CLI）。

    注入 ``__signature__`` / ``__annotations__``（均为真实类型对象，
    ``get_type_hints`` 无需 eval）；``__doc__`` 供内省。
    """
    annotations: dict[str, Any] = {}
    parameters: list[inspect.Parameter] = []
    for param in decl.args:
        annotation = _param_annotation(param)
        annotations[param.name] = annotation
        if param.default is None and param.type != "bool":
            parameters.append(
                inspect.Parameter(param.name, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=annotation)
            )
        else:
            parameters.append(
                inspect.Parameter(
                    param.name,
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    annotation=annotation,
                    default=_param_default(param),
                )
            )

    def dsl_command() -> None:
        """cmd 任务占位函数。"""

    dsl_command.__name__ = f"_dsl_{decl.name}"
    dsl_command.__qualname__ = f"fcmd.dsl.synth._dsl_{decl.name}"
    dsl_command.__doc__ = decl.help
    dsl_command.__signature__ = inspect.Signature(parameters)  # type: ignore[attr-defined]
    dsl_command.__annotations__ = annotations
    return dsl_command


def build_tool_spec(decl: CommandDecl, platform: str = sys.platform) -> ToolSpec:
    """CommandDecl → ToolSpec（注册进 ``_TOOL_REGISTRY`` 的形态）。

    Parameters
    ----------
    decl:
        命令声明
    platform:
        平台标识（``sys.platform`` 值），默认当前平台

    Returns
    -------
    ToolSpec
        cmd 任务型工具描述符（subcommand=None，单命令工具）
    """
    return ToolSpec(
        name=decl.name,
        subcommand=None,
        func=_synthesize_func(decl),
        help=decl.help,
        description=decl.description,
        hidden=decl.hidden,
        cmd=select_platform_cmd(decl, platform),
        param_help={p.name: p.help for p in decl.args if p.help},
        cwd=decl.cwd,
        env=dict(decl.env) if decl.env else None,
        timeout=decl.timeout,
    )
