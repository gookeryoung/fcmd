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

合成函数无源码（``inspect.getsource`` 抛 OSError），标记
``__dsl_empty_body__`` 供 :func:`fcmd.apis._tool_exec._has_function_logic`
识别空函数体（DSL 声明是纯 exec/聚合编排，占位函数体永不承载逻辑）：
cmd 任务在 ``spec.cmd is not None`` 时短路，聚合任务（needs 且无 cmd）据此
正确判定为聚合。bool 参数的 ``on`` 固定 token 经 ``__dsl_param_on__`` 属性
传递，由 ``_tool_exec._expand_cmd_placeholders`` 在值为真时追加到 cmd 尾部。
命令级 ``message``（post-run 完成消息）经 ``__dsl_message__`` 属性传递，由
``_tool_exec._execute_tool_tasks`` 在任务执行成功后打印；``fail_message``
（post-run 失败消息）经 ``__dsl_fail_message__`` 属性传递，失败时打印。
``when`` 探针守卫经
``__dsl_when__`` 属性传递（仅声明 when 的命令注入），由
``_tool_exec._build_conditions`` 构造为引擎 ``TaskSpec.conditions`` 闭包。
str 参数的 ``default_env`` 环境变量回退链经 ``__dsl_param_env__`` 属性传递
（仅声明 default_env 的命令注入），由 ``_tool_exec._apply_env_defaults`` 在
CLI 解析值等于声明 default 时取链中第一个非空环境变量值。
命令级 ``compute``（计算型动作原语）经 ``__dsl_compute__`` 属性传递
（仅声明 compute 的命令注入），由 ``_tool_exec._apply_compute`` 在构建任务
前进程内执行计算动作并将返回值注入共享变量，供本命令模板插值消费。
``allow_upstream_skip`` / ``tty`` 不经函数属性，直接映射 :class:`ToolSpec`
既有/新增声明字段（``tty`` → ``TaskSpec.passthrough``，引擎侧已消费，
属于声明字段而非函数属性契约）。

命令级 ``action``（内建动作原语）合成**有逻辑**的函数体（转发
:mod:`fcmd.dsl.actions` 注册表实现，进程内执行），不标记
``__dsl_empty_body__``——引擎判有函数逻辑 → fn 任务，kwargs 按签名从
CLI 解析变量注入；参数 schema 拷贝自动作实现函数签名（TOML ``args``
禁用，单一真理源），标记 ``__dsl_action__`` 供内省与测试。
"""

from __future__ import annotations

import inspect
import sys
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from fcmd.apis._tool_args import ToolSpec

from .actions import Action, get_action
from .decl import CommandDecl, CommandDeclError, ParamDecl

__all__ = ["build_tool_spec", "select_platform_cmd"]

# 参数类型名 → 真实类型注解对象（choices 特判，见 _param_annotation；
# list 必须是 list[str] 泛型——引擎 argparse 层按 list[X] 检测映射 nargs，
# 裸 list 不被识别，见 _tool_args）
_TYPE_MAP: dict[str, Any] = {"str": str, "int": int, "float": float, "bool": bool, "path": Path, "list": list[str]}


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


def _inject_contracts(func: Callable[..., Any], decl: CommandDecl) -> None:
    """注入 post-run 消息与 when 守卫函数属性契约（cmd/action 合成函数共用）。

    bool on-token（``__dsl_param_on__``）与环境回退链（``__dsl_param_env__``）
    仅 TOML args 声明存在；action 命令禁用 args，不涉及。
    """
    # post-run 完成消息契约（_tool_exec 消费）：执行成功后打印的消息模板
    if decl.message:
        func.__dsl_message__ = decl.message  # type: ignore[attr-defined]
    # post-run 失败消息契约（_tool_exec 消费）：执行失败后打印的消息模板
    if decl.fail_message:
        func.__dsl_fail_message__ = decl.fail_message  # type: ignore[attr-defined]
    # when 探针守卫契约（_tool_exec._build_conditions 消费）：任务执行前求值，
    # 不满足则 SKIPPED；仅声明 when 的命令注入
    if decl.when is not None:
        func.__dsl_when__ = decl.when  # type: ignore[attr-defined]


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
    dsl_command.__dsl_empty_body__ = True  # type: ignore[attr-defined]
    # bool on-token 契约（与 __dsl_empty_body__ 同为函数属性约定，_tool_exec
    # 消费）：{参数名: truthy 时向 cmd 尾部追加的固定 token}
    dsl_command.__dsl_param_on__ = {p.name: p.on for p in decl.args if p.on}  # type: ignore[attr-defined]
    # 环境变量回退链契约（_tool_exec._apply_env_defaults 消费）：
    # {参数名: (环境变量链, 声明默认值)}，仅声明 default_env 的命令注入
    env_defaults = {p.name: (p.default_env, p.default) for p in decl.args if p.default_env}
    if env_defaults:
        dsl_command.__dsl_param_env__ = env_defaults  # type: ignore[attr-defined]
    # 计算型动作契约（_tool_exec._apply_compute / _expand_value 消费）：
    # (计算动作名, 注入变量名)，仅声明 compute 的命令注入
    if decl.compute is not None:
        dsl_command.__dsl_compute__ = (decl.compute.action, decl.compute.as_name)  # type: ignore[attr-defined]
    _inject_contracts(dsl_command, decl)
    return dsl_command


def _synthesize_action_func(decl: CommandDecl, act: Action) -> Callable[..., Any]:
    """生成内建动作的合成函数（fn 任务形态：函数体有逻辑，引擎按 fn 任务执行）。

    与 cmd 任务占位函数（``__dsl_empty_body__``，函数体不执行）不同：动作
    合成函数的函数体转发注册表实现（进程内执行），**不标记**空函数体——
    引擎 :func:`fcmd.apis._tool_exec._has_function_logic` 判有逻辑 → fn
    任务，kwargs 按签名从 CLI 解析变量注入。签名/注解拷贝自动作实现函数
    （声明期已校验参数名与保留名冲突），零改动复用 ``_build_parser_for_tool``。

    Parameters
    ----------
    decl:
        命令声明（``action`` 非空，声明期校验保证）
    act:
        已注册的动作描述符

    Returns
    -------
    Callable
        合成函数（标记 ``__dsl_action__`` 供内省与测试）
    """
    impl = act.func
    sig = inspect.signature(impl)
    # 注解解析加固：字符串注解在实现函数自身命名空间求值（actions 模块开启
    # ``from __future__ import annotations`` 后 inspect.signature 返回原始
    # 字符串），不依赖合成函数模块命名空间的隐式可见性；求值失败回退原始
    # 注解（与既有行为一致，由 _resolve_hints 二次处理）
    try:
        hints: dict[str, Any] = typing.get_type_hints(impl)
    except Exception:
        hints = {}

    def dsl_action(**kwargs: Any) -> Any:
        """内建动作任务：转发注册表实现（进程内执行）。"""
        return impl(**kwargs)

    dsl_action.__name__ = f"_dsl_{decl.name}"
    dsl_action.__qualname__ = f"fcmd.dsl.synth._dsl_{decl.name}"
    dsl_action.__doc__ = decl.help
    dsl_action.__signature__ = sig  # type: ignore[attr-defined]
    dsl_action.__annotations__ = {pname: hints.get(pname, p.annotation) for pname, p in sig.parameters.items()}  # type: ignore[attr-defined]
    dsl_action.__dsl_action__ = act.name  # type: ignore[attr-defined]
    _inject_contracts(dsl_action, decl)
    return dsl_action


def build_tool_spec(
    decl: CommandDecl,
    platform: str = sys.platform,
    *,
    tool_name: str | None = None,
    subcommand: str | None = None,
) -> ToolSpec:
    """CommandDecl → ToolSpec（注册进 ``_TOOL_REGISTRY`` 的形态）。

    Parameters
    ----------
    decl:
        命令声明
    platform:
        平台标识（``sys.platform`` 值），默认当前平台
    tool_name:
        工具名；多子命令形态下与 ``decl.name``（子命令名）不同，缺省用
        ``decl.name``
    subcommand:
        子命令名；``None`` 表示单命令工具

    Returns
    -------
    ToolSpec
        cmd/聚合任务型工具描述符

    Raises
    ------
    CommandDeclError
        该平台无可用命令
    """
    act = get_action(decl.action) if decl.action else None
    if act is not None:
        func: Callable[..., Any] = _synthesize_action_func(decl, act)
        param_help: dict[str, str] = dict(act.param_help)
    else:
        func = _synthesize_func(decl)
        param_help = {p.name: p.help for p in decl.args if p.help}

    # 平台级 tty/env 覆盖：win32 优先 win_tty/win_env，其余优先 unix_tty/unix_env；
    # 平台级未声明（None）时继承顶层；env 做字典合并（平台级覆盖同名键）。
    if platform == "win32":
        plat_tty = decl.win_tty
        plat_env = decl.win_env
    else:
        plat_tty = decl.unix_tty
        plat_env = decl.unix_env
    resolved_tty = plat_tty if plat_tty is not None else decl.tty
    resolved_env: dict[str, str] | None
    if plat_env is None:
        resolved_env = dict(decl.env) if decl.env else None
    elif decl.env is None:
        resolved_env = dict(plat_env)
    else:
        merged = dict(decl.env)
        merged.update(plat_env)  # 平台级覆盖同名键
        resolved_env = merged

    return ToolSpec(
        name=tool_name or decl.name,
        subcommand=subcommand,
        func=func,
        help=decl.help,
        description=decl.description,
        hidden=decl.hidden,
        # action 命令无子进程命令（fn 任务形态）；聚合命令（needs 且无 cmd）
        # 同样置 None
        cmd=None if act is not None or (decl.needs and not _has_any_cmd(decl)) else select_platform_cmd(decl, platform),
        param_help=param_help,
        cwd=decl.cwd,
        env=resolved_env,
        timeout=decl.timeout,
        needs=decl.needs,
        strategy=cast("Literal['sequential', 'thread', 'async', 'dependency'] | None", decl.strategy),
        allow_upstream_skip=decl.allow_upstream_skip,
        passthrough=resolved_tty,
    )


def _has_any_cmd(decl: CommandDecl) -> bool:
    """声明是否提供了任一 cmd（含平台分支）。"""
    return not (decl.cmd is None and decl.win_cmd is None and decl.unix_cmd is None)
