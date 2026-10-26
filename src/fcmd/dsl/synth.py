"""声明 → ToolSpec：平台命令选定与合成函数签名。

DSL 声明到 :class:`~fcmd.core.toolkit.args.ToolSpec` 的转换层：

* :func:`select_platform_cmd` —— 按平台从声明选定最终 cmd（win32 命中
  ``win_cmd``，其余命中 ``unix_cmd``，均缺回退 ``cmd``）。平台作为显式
  参数传入，双平台分支可直测，无需 monkeypatch。
* :func:`build_tool_spec` —— 构造 ToolSpec：合成占位函数（cmd 任务函数体
  不执行，签名仅驱动 CLI，与 ``@fx.tool(cmd=...)`` 的既有语义一致）。

签名合成机制：stdlib ``inspect.signature`` 优先读取函数对象的
``__signature__`` 属性、``typing.get_type_hints`` 读取 ``__annotations__``
字典，两者均支持运行时注入——参数声明 (:class:`~fcmd.dsl.decl.ParamDecl`)
转换为真实类型注解（``Literal`` 动态构造）与 :class:`inspect.Signature`，
零改动复用 :func:`fcmd.core.toolkit.args._build_parser_for_tool` 的签名推导。

合成函数无源码（``inspect.getsource`` 抛 OSError），标记
``__dsl_empty_body__`` 供 :func:`fcmd.core.toolkit.exec._has_function_logic`
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

import functools
import inspect
import os
import subprocess
import sys
import typing
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from fcmd.core.toolkit.args import ToolSpec

from .actions import Action, get_action
from .decl import CommandDecl, CommandDeclError, ParamDecl

__all__ = [
    "build_tool_spec",
    "resolve_windows_openssh",
    "resolve_windows_plink",
    "resolve_windows_ssh_client",
    "select_platform_cmd",
]

# 参数类型名 → 真实类型注解对象（choices 特判，见 _param_annotation；
# list 必须是 list[str] 泛型——引擎 argparse 层按 list[X] 检测映射 nargs，
# 裸 list 不被识别，见 _tool_args）
_TYPE_MAP: dict[str, Any] = {"str": str, "int": int, "float": float, "bool": bool, "path": Path, "list": list[str]}


@functools.lru_cache(maxsize=1)
def resolve_windows_openssh() -> str | None:
    """定位 Windows 上 OpenSSH ssh.exe 的完整路径。

    问题背景：Windows 用户 PATH 中 ``plink.exe``（PuTTY 的命令行工具）
    经常排在 OpenSSH ``ssh.exe`` 前面，导致 shell 命令 ``ssh -p ...``
    实际被 plink 执行。plink 不支持 ``-p``（plink 用 ``-P`` 大写）、
    ``-o StrictHostKeyChecking=no`` 等 OpenSSH 参数。

    检测策略（带 lru_cache 缓存，进程内只做一次）：

    1. 优先检查 Windows 10/11 自带 OpenSSH 的标准安装路径
       ``C:\\Windows\\System32\\OpenSSH\\ssh.exe``
    2. 用 ``where ssh`` 枚举 PATH 中的 ``ssh.exe``，逐个执行 ``ssh -V``
       从 stderr 版本字符串判断是否含 "OpenSSH"，取首个 OpenSSH 路径
    3. 全部找不到返回 ``None``（调用方回退到裸 ``ssh``，可能仍被 plink
       劫持，属用户环境问题）

    Returns
    -------
    str | None
        OpenSSH ssh.exe 的绝对路径（带 ``.exe`` 扩展名），或 ``None``
        表示未找到 OpenSSH 客户端
    """
    if sys.platform != "win32":
        return None

    # 1) 标准安装路径（Windows 10 1809+ 自带 OpenSSH）
    candidates: list[Path] = []
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    for sub in ("System32", "SysWOW64"):
        p = Path(system_root) / sub / "OpenSSH" / "ssh.exe"
        if p.is_file():
            candidates.append(p)

    # 2) where ssh（cmd.exe）枚举 PATH 中的 ssh.exe
    try:
        proc = subprocess.run(
            ["where", "ssh"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            for raw_line in proc.stdout.strip().splitlines():
                candidate_line = raw_line.strip()
                if candidate_line and candidate_line.lower().endswith(".exe"):
                    candidates.append(Path(candidate_line))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # 3) 逐个执行 ssh -V，从 stderr 判断是否为 OpenSSH
    #    OpenSSH 版本输出（stderr）示例：
    #    OpenSSH_for_Windows_8.1p1 LibreSSL, 24 Jan 2022
    #    PuTTY/plink 版本输出示例：
    #    plink: PuTTY-Open Source SSH client, version 0.78
    for ssh_path in candidates:
        if not ssh_path.is_file():
            continue
        try:
            result = subprocess.run(
                [str(ssh_path), "-V"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            # ssh -V 输出到 stderr
            version = (result.stderr or result.stdout or "").lower()
            if "openssh" in version and "putty" not in version:
                return str(ssh_path)
        except (subprocess.TimeoutExpired, OSError):
            continue

    return None


@functools.lru_cache(maxsize=1)
def resolve_windows_plink() -> str | None:
    """定位 Windows 上 plink.exe（PuTTY 命令行工具）的完整路径。

    plink 能从命令行参数 ``-pw`` 自动传递密码（OpenSSH for Windows
    不支持自动传密码，只能交互式输入）。本检测为 Windows 分支提供
    密码自动注入能力——优先使用 plink 让 password 参数生效。

    检测策略（与 :func:`resolve_windows_openssh` 同构，独立缓存）：

    1. 用 ``where plink`` 枚举 PATH 中的 ``plink.exe``
    2. 逐个执行 ``plink -V``，从 stderr 版本串判断是否含 "PuTTY"
    3. 全部找不到返回 ``None``

    Returns
    -------
    str | None
        plink.exe 的绝对路径（带 ``.exe`` 扩展名），或 ``None`` 表示未找到
    """
    if sys.platform != "win32":
        return None

    candidates: list[Path] = []

    try:
        proc = subprocess.run(
            ["where", "plink"],
            capture_output=True,
            check=False,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode == 0:
            for raw_line in proc.stdout.strip().splitlines():
                candidate_line = raw_line.strip()
                if candidate_line and candidate_line.lower().endswith(".exe"):
                    candidates.append(Path(candidate_line))
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    for plink_path in candidates:
        if not plink_path.is_file():
            continue
        try:
            result = subprocess.run(
                [str(plink_path), "-V"],
                capture_output=True,
                check=False,
                text=True,
                timeout=3,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            version = (result.stderr or result.stdout or "").lower()
            if "putty" in version:
                return str(plink_path)
        except (subprocess.TimeoutExpired, OSError):
            continue

    return None


@functools.lru_cache(maxsize=1)
def resolve_windows_ssh_client() -> tuple[str, str] | None:
    """协调 plink 与 OpenSSH 的检测，返回 Windows 上最优 SSH 客户端。

    优先级：**plink 优先**（支持 ``-pw`` 自动传密码，让 DSL 声明的
    ``password`` 参数生效）→ 其次 OpenSSH（只能交互式输入密码，``tty``
    让密码提示透传终端）→ 都找不到返回 ``None``。

    Returns
    -------
    tuple[str, str] | None
        ``("plink", path)`` 或 ``("openssh", path)``；``None`` 表示未找到
        任何 SSH 客户端（调用方回退裸 ``ssh``，但裸 ``ssh`` 在 PATH 中被
        plink 劫持会导致 OpenSSH 参数不被识别，属用户环境问题）
    """
    plink = resolve_windows_plink()
    if plink is not None:
        return ("plink", plink)
    openssh = resolve_windows_openssh()
    if openssh is not None:
        return ("openssh", openssh)
    return None


def _rewrite_openssh_cmd_as_plink(openssh_cmd: str, plink_path: str) -> str:
    """把 OpenSSH 命令模板字符串改写成 plink 格式。

    通过结构化字符串替换（而非正则解析远端命令），保证模板占位符
    ``{password}``/``{port}``/``{username}``/``{hostname}``/``{keypath}``
    在后续 execution 层 ``_expand_cmd_placeholders`` 中被正确替换：

    - ``ssh `` → ``plink_path -ssh -pw {password} -batch ``
    - 去掉 OpenSSH 专属的 ``-o StrictHostKeyChecking=no`` /
      ``-o UserKnownHostsFile=/dev/null`` / ``-o ConnectTimeout={timeout}``
      （plink 用 ``-batch`` 禁止交互提示，不支持 ``-o``）
    - ``-p {port}`` → ``-P {port}``（plink 端口大写）

    不改动远端命令引号和 stdin 重定向（``< "{keypath}"``）——plink
    同样支持这些语法。
    """
    import re as _re

    cmd = openssh_cmd

    # 1. ssh 开头 → plink -ssh -pw {password} -batch
    if cmd.startswith("ssh "):
        cmd = plink_path + " -ssh -pw {password} -batch" + cmd[3:]
    elif cmd.startswith("sshpass "):
        # Linux 分支不应该走到这里，但做个防御性处理
        return cmd
    else:
        # 已被路径替换过（理论上 _maybe_replace_ssh_path 保证不会走到这里）
        # 尝试提取 ssh 后的剩余部分
        first_space = cmd.find(" ")
        if first_space != -1:
            rest = cmd[first_space + 1 :]
            cmd = plink_path + " -ssh -pw {password} -batch " + rest
        else:
            cmd = plink_path + " -ssh -pw {password} -batch"

    # 2. 去掉所有 -o <option> 选项（OpenSSH 专属，plink 不支持）
    cmd = _re.sub(r"\s+-o\s+\S+", "", cmd)

    # 3. -p {port} → -P {port}（plink 端口大写 P）
    cmd = cmd.replace(" -p {port}", " -P {port}")

    return cmd


def select_platform_cmd(decl: CommandDecl, platform: str = sys.platform) -> str | tuple[str, ...] | None:
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
    引擎 :func:`fcmd.core.toolkit.exec._has_function_logic` 判有逻辑 → fn
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

    raw_cmd = (
        None if act is not None or (decl.needs and not _has_any_cmd(decl)) else select_platform_cmd(decl, platform)
    )

    # Windows 路径上的 ssh 命令：用 OpenSSH 完整路径替换裸 "ssh"，
    # 避免 PATH 中 plink.exe 抢先导致 -p / -o 等 OpenSSH 参数不被识别。
    resolved_cmd = _maybe_replace_ssh_path(raw_cmd, platform)

    return ToolSpec(
        name=tool_name or decl.name,
        subcommand=subcommand,
        func=func,
        help=decl.help,
        description=decl.description,
        hidden=decl.hidden,
        cmd=resolved_cmd,
        param_help=param_help,
        cwd=decl.cwd,
        env=resolved_env,
        timeout=decl.timeout,
        needs=decl.needs,
        strategy=cast("Literal['sequential', 'thread', 'async', 'dependency'] | None", decl.strategy),
        allow_upstream_skip=decl.allow_upstream_skip,
        passthrough=resolved_tty,
    )


def _maybe_replace_ssh_path(cmd: str | tuple[str, ...] | None, platform: str) -> str | tuple[str, ...] | None:
    """在 win32 平台上重写 shell 命令以匹配检测到的 SSH 客户端。

    Windows SSH 客户端有两种常见选择，本函数按 **plink 优先 → OpenSSH 次之**
    的策略重写命令模板：

    - **plink**（PuTTY 命令行工具）：支持 ``-pw`` 参数从命令行自动传递密码
      （OpenSSH for Windows 不支持自动传密码，只能交互式输入）。检测到 plink
      时把 OpenSSH 命令模板**重写为 plink 格式**——替换客户端为 plink 路径、
      去掉 OpenSSH 专属的 ``-o`` 选项、``-p`` → ``-P``、追加
      ``-pw {password} -batch``，让 DSL 声明的 ``password`` 参数生效。
    - **OpenSSH**（Windows 10/11 自带）：只替换裸 ``ssh`` 为完整路径
      （避免 PATH 中 plink 抢先导致 ``-p`` / ``-o`` 等参数被拒绝），
      不做其他改动。
    - **都没找到**：返回原命令不变（裸 ``ssh`` 可能仍被 plink 劫持，
      属用户环境问题）。

    仅处理 str 形态（shell=True 执行）的命令；tuple 形态（无 shell，
    list 参数）不处理。
    """
    if platform != "win32" or not isinstance(cmd, str):
        return cmd

    client = resolve_windows_ssh_client()
    if client is None:
        return cmd

    client_type, client_path = client

    if client_type == "plink":
        # plink 支持 -pw 自动传密码 → 重写整个命令模板为 plink 格式
        return _rewrite_openssh_cmd_as_plink(cmd, client_path)

    # OpenSSH：只替换开头的裸 "ssh" 为完整路径
    if cmd.startswith("ssh "):
        return client_path + " " + cmd[4:]
    return cmd


def _has_any_cmd(decl: CommandDecl) -> bool:
    """声明是否提供了任一 cmd（含平台分支）。"""
    return not (decl.cmd is None and decl.win_cmd is None and decl.unix_cmd is None)
