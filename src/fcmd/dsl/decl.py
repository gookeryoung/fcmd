"""命令声明层：TOML 表 → CommandDecl（纯函数，无 I/O）。

承载命令定义 DSL 的声明与校验：每个 ``[commands.<name>]`` 表解析为一个
:class:`CommandDecl`，非法声明抛 :class:`CommandDeclError`（含命令名与
键名上下文，便于用户定位配置错误）。

本模块不读文件、不注册工具——文件定位与解析编排见 :mod:`fcmd.dsl.loader`，
声明到 :class:`~fcmd.apis._tool_args.ToolSpec` 的转换见 :mod:`fcmd.dsl.synth`。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["CommandDecl", "CommandDeclError", "ParamDecl", "parse_command_table"]

# 保留名：DSL 命令名不得与其冲突（fcmd 自身 + 内建命令，后者被 FcmdApp 优先
# 路由遮蔽，注册了也永远不可达）。与 fcmd.cli._common._BUILTIN_COMMANDS
# 保持同步，一致性由 tests/test_dsl.py 断言保证。
_RESERVED_NAMES: frozenset[str] = frozenset(
    {"fcmd", "graph", "info", "completion", "yaml", "env", "doctor", "profiler"}
)

# 工具名模式：小写字母开头，允许小写字母/数字/连字符/下划线
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# 参数名模式：小写字母/下划线开头，允许小写字母/数字/下划线
_PARAM_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# 保留参数名：与 _add_global_options 的全局选项冲突（argparse 会崩溃）。
# 注：cwd 允许使用（引擎有 CLI 值覆盖装饰器 cwd 的既有语义）。
_RESERVED_PARAM_NAMES: frozenset[str] = frozenset({"dry_run", "quiet", "strategy"})

# 支持的参数类型
_PARAM_TYPES: frozenset[str] = frozenset({"str", "int", "float", "bool", "path", "choices"})

# 命令声明顶层合法键
_TOP_KEYS = frozenset(
    {"help", "description", "aliases", "hidden", "cmd", "win", "unix", "args", "cwd", "timeout", "env"}
)

# 平台子表（win/unix）内合法键
_PLATFORM_KEYS = frozenset({"cmd"})

# 参数声明表内合法键
_PARAM_KEYS = frozenset({"type", "default", "help", "choices"})


class CommandDeclError(ValueError):
    """命令声明非法（配置错误，非运行时错误）。"""


@dataclass(frozen=True)
class CommandDecl:
    """单条命令声明（TOML ``[commands.<name>]`` 表的解析结果）。

    参数
    ----
    name:
        工具名（CLI 调用名，如 ``"clr"``）
    help:
        帮助文本（必填非空，用于 --help 与工具列表）
    win_cmd:
        Windows（``sys.platform == "win32"``）命令；``None`` 表示未提供
    unix_cmd:
        非 Windows 平台命令；``None`` 表示未提供
    cmd:
        通用回退命令（平台分支未命中时使用）；``None`` 表示未提供
    description:
        工具描述（fcmd 工具列表页），空串回退 help
    aliases:
        别名元组
    hidden:
        是否对子命令列表隐藏
    args:
        参数声明元组（P1：驱动 CLI 参数与 cmd 模板插值）
    cwd:
        工作目录（支持 ``{参数名}`` 插值）
    timeout:
        执行超时秒数（正数）
    env:
        环境变量映射（值支持 ``{参数名}`` 插值）
    """

    name: str
    help: str
    win_cmd: str | tuple[str, ...] | None = None
    unix_cmd: str | tuple[str, ...] | None = None
    cmd: str | tuple[str, ...] | None = None
    description: str = ""
    aliases: tuple[str, ...] = ()
    hidden: bool = False
    args: tuple[ParamDecl, ...] = ()
    cwd: str | None = None
    timeout: float | None = None
    env: dict[str, str] | None = None


@dataclass(frozen=True)
class ParamDecl:
    """单参数声明（TOML ``[commands.<name>.args.<参数名>]`` 表的解析结果）。

    ``default`` 为 ``None`` 表示未提供默认值（映射为 positional 参数）；
    提供默认值映射为 ``--name`` 选项。``type=bool`` 时 ``default`` 必填
    （``true`` → ``--no-name`` 关闭开关，``false`` → ``--name`` 启用开关）。

    参数
    ----
    name:
        参数名（Python 标识符风格，CLI 选项自动转连字符）
    type:
        参数类型：``str`` / ``int`` / ``float`` / ``bool`` / ``path`` / ``choices``
    default:
        默认值（``None`` = 无默认 → positional）
    help:
        参数帮助文本
    choices:
        ``type="choices"`` 时的取值列表（全字符串）
    """

    name: str
    type: str = "str"
    default: str | int | float | bool | None = None
    help: str = ""
    choices: tuple[str, ...] = ()


def _normalize_cmd(name: str, where: str, value: Any) -> str | tuple[str, ...] | None:
    """归一化 cmd 值：str → str；非空全 str 列表 → tuple；None → None。

    Parameters
    ----------
    name:
        命令名（错误消息上下文）
    where:
        键位置描述（如 ``"win.cmd"``）
    value:
        原始值

    Raises
    ------
    CommandDeclError
        值既非字符串、非全字符串非空列表、也非 None
    """
    if value is None:
        return None
    if isinstance(value, str):
        if not value:
            raise CommandDeclError(f"命令 {name!r} 的 {where} 不能是空字符串")
        return value
    if isinstance(value, list) and value and all(isinstance(item, str) and item for item in value):
        return tuple(value)
    raise CommandDeclError(f"命令 {name!r} 的 {where} 须是字符串或非空字符串数组，实际: {value!r}")


def _parse_platform_cmd(name: str, key: str, value: Any) -> str | tuple[str, ...] | None:
    """解析平台子表（win/unix）：仅允许含 cmd 键的表。

    Raises
    ------
    CommandDeclError
        子表结构非法（非表 / 含未知键 / cmd 值非法）
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CommandDeclError(f'命令 {name!r} 的 {key} 须是表（如 `{key}.cmd = "..."`），实际: {value!r}')
    unknown = set(value) - _PLATFORM_KEYS
    if unknown:
        raise CommandDeclError(f"命令 {name!r} 的 {key} 表含未知键: {sorted(unknown)}")
    return _normalize_cmd(name, f"{key}.cmd", value.get("cmd"))


def _check_default_type(name: str, pname: str, ptype: str, default: Any) -> None:
    """校验 default 值与 type 声明一致（float 宽容接受 int）。

    Raises
    ------
    CommandDeclError
        default 值类型与 type 声明不匹配
    """
    expected: tuple[type, ...]
    if ptype == "str":
        expected = (str,)
    elif ptype == "int":
        expected = (int,)
    elif ptype == "float":
        expected = (int, float)
    elif ptype == "bool":
        expected = (bool,)
    else:  # path / choices：TOML 字符串形式
        expected = (str,)
    if not isinstance(default, expected):
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} default 类型与 type={ptype!r} 不匹配: {default!r}")


def parse_param_table(name: str, pname: str, table: Mapping[str, Any]) -> ParamDecl:
    """解析并校验单个 ``[commands.<name>.args.<pname>]`` 表。

    Raises
    ------
    CommandDeclError
        声明非法（参数名 / 未知键 / 类型不支持 / bool 缺 default /
        choices 缺失 / default 类型不匹配 / default 不在 choices 内）
    """
    if not _PARAM_NAME_RE.match(pname):
        raise CommandDeclError(f"命令 {name!r} 的参数名 {pname!r} 非法：须匹配 {_PARAM_NAME_RE.pattern}")
    if pname in _RESERVED_PARAM_NAMES:
        raise CommandDeclError(f"命令 {name!r} 的参数名 {pname!r} 是保留名（全局选项）")

    unknown = set(table) - _PARAM_KEYS
    if unknown:
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 含未知键: {sorted(unknown)}")

    ptype = table.get("type", "str")
    if ptype not in _PARAM_TYPES:
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 type 须是 {sorted(_PARAM_TYPES)}，实际: {ptype!r}")

    help_text = table.get("help", "")
    if not isinstance(help_text, str):
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 help 须是字符串")

    choices_raw = table.get("choices", [])
    if ptype == "choices":
        if not isinstance(choices_raw, list) or not choices_raw or not all(isinstance(c, str) for c in choices_raw):
            raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 type=choices 须提供非空字符串列表 choices")
    elif choices_raw:
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 仅 type=choices 可声明 choices")

    default = table.get("default", None)
    if ptype == "bool" and default is None:
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 type=bool 须提供 default（true/false）")
    if default is not None:
        _check_default_type(name, pname, ptype, default)
        if ptype == "choices" and default not in choices_raw:
            raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 default 须在 choices 内: {default!r}")

    return ParamDecl(
        name=pname,
        type=ptype,
        default=default,
        help=help_text,
        choices=tuple(choices_raw),
    )


def _parse_transparency(name: str, table: Mapping[str, Any]) -> tuple[str | None, float | None, dict[str, str] | None]:
    """解析并校验透传字段（cwd / timeout / env）。

    Returns
    -------
    tuple
        (cwd, timeout, env)，均未声明时为 (None, None, None)

    Raises
    ------
    CommandDeclError
        字段值类型非法
    """
    cwd = table.get("cwd", None)
    if cwd is not None and not isinstance(cwd, str):
        raise CommandDeclError(f"命令 {name!r} 的 cwd 须是字符串")

    timeout = table.get("timeout", None)
    if timeout is not None and (isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0):
        raise CommandDeclError(f"命令 {name!r} 的 timeout 须是正数，实际: {timeout!r}")

    env_raw = table.get("env", None)
    if env_raw is None:
        return cwd, float(timeout) if timeout is not None else None, None
    if not isinstance(env_raw, Mapping) or not all(isinstance(v, str) for v in env_raw.values()):
        raise CommandDeclError(f'命令 {name!r} 的 env 须是字符串值映射（KEY = "..."）')
    return cwd, float(timeout) if timeout is not None else None, dict(env_raw)


def parse_command_table(name: str, table: Mapping[str, Any]) -> CommandDecl:
    """解析并校验单个 ``[commands.<name>]`` 表。

    Parameters
    ----------
    name:
        命令名（TOML 子表名）
    table:
        子表内容（TOML 解析出的映射）

    Returns
    -------
    CommandDecl
        校验通过的命令声明

    Raises
    ------
    CommandDeclError
        声明非法（保留名 / 未知键 / 缺 help / 无任何 cmd / 值类型错误）
    """
    if not _TOOL_NAME_RE.match(name):
        raise CommandDeclError(f"命令名 {name!r} 非法：须匹配 {_TOOL_NAME_RE.pattern}")
    if name in _RESERVED_NAMES:
        raise CommandDeclError(f"命令名 {name!r} 是保留名（fcmd 或内建命令）")

    unknown = set(table) - _TOP_KEYS
    if unknown:
        raise CommandDeclError(f"命令 {name!r} 含未知键: {sorted(unknown)}")

    help_text = table.get("help")
    if not isinstance(help_text, str) or not help_text.strip():
        raise CommandDeclError(f"命令 {name!r} 缺少必填的非空 help")

    description = table.get("description", "")
    if not isinstance(description, str):
        raise CommandDeclError(f"命令 {name!r} 的 description 须是字符串")

    aliases_raw = table.get("aliases", [])
    if not isinstance(aliases_raw, list) or not all(isinstance(a, str) and a for a in aliases_raw):
        raise CommandDeclError(f"命令 {name!r} 的 aliases 须是非空字符串数组")

    hidden = table.get("hidden", False)
    if not isinstance(hidden, bool):
        raise CommandDeclError(f"命令 {name!r} 的 hidden 须是布尔值")

    win_cmd = _parse_platform_cmd(name, "win", table.get("win"))
    unix_cmd = _parse_platform_cmd(name, "unix", table.get("unix"))
    cmd = _normalize_cmd(name, "cmd", table.get("cmd"))
    if win_cmd is None and unix_cmd is None and cmd is None:
        raise CommandDeclError(f"命令 {name!r} 须提供 win.cmd / unix.cmd / cmd 至少一个")

    args_raw = table.get("args", {})
    if not isinstance(args_raw, Mapping):
        raise CommandDeclError(f"命令 {name!r} 的 args 须是表（[commands.{name}.args.<参数名>]）")
    args = tuple(parse_param_table(name, pname, ptable) for pname, ptable in args_raw.items())

    cwd, timeout, env = _parse_transparency(name, table)

    return CommandDecl(
        name=name,
        help=help_text,
        win_cmd=win_cmd,
        unix_cmd=unix_cmd,
        cmd=cmd,
        description=description,
        aliases=tuple(aliases_raw),
        hidden=hidden,
        args=args,
        cwd=cwd,
        timeout=timeout,
        env=env,
    )
