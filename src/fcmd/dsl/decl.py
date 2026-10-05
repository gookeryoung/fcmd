"""命令声明层：TOML 表 → CommandDecl / ToolDecl（纯函数，无 I/O）。

承载命令定义 DSL 的声明与校验，支持两种形态：

* **单命令**（``[commands.<name>]``）：整个表是一条 exec 命令声明，解析为
  :class:`CommandDecl`（子命令为 ``None``）。
* **多子命令**（``[commands.<tool>.<sub>]``）：工具表的每个子表是一个子命令，
  解析为 :class:`ToolDecl`（含多个 :class:`CommandDecl`），支持 ``needs``
  依赖（引用同工具其他子命令）与 ``strategy`` 执行策略——与 ``@fx.tool``
  的 cmd/聚合任务语义对齐。

非法声明抛 :class:`CommandDeclError`（含命令名与键名上下文，便于用户定位
配置错误）。本模块不读文件、不注册工具——文件定位与解析编排见
:mod:`fcmd.dsl.loader`，声明到 :class:`~fcmd.apis._tool_args.ToolSpec` 的
转换见 :mod:`fcmd.dsl.synth`。
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CommandDecl",
    "CommandDeclError",
    "ParamDecl",
    "ToolDecl",
    "WhenDecl",
    "parse_command_table",
    "parse_tool_table",
]

# 保留名：DSL 命令名不得与其冲突（fcmd 自身 + 内建命令，后者被 FcmdApp 优先
# 路由遮蔽，注册了也永远不可达）。与 fcmd.cli._common._BUILTIN_COMMANDS
# 保持同步，一致性由 tests/test_dsl.py 断言保证。
_RESERVED_NAMES: frozenset[str] = frozenset(
    {"fcmd", "graph", "info", "completion", "yaml", "env", "doctor", "profiler"}
)

# 工具名模式：小写字母开头，允许小写字母/数字/连字符/下划线
_TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")

# 子命令名模式：额外允许下划线开头（`_init` 式内部隐藏子命令约定，
# 供 needs 链式编排引用，hidden 对子命令列表不可见）
_SUB_NAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*$")

# 参数名模式：小写字母/下划线开头，允许小写字母/数字/下划线
_PARAM_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# 保留参数名：与 _add_global_options 的全局选项冲突（argparse 会崩溃）。
# 注：cwd 允许使用（引擎有 CLI 值覆盖装饰器 cwd 的既有语义）。
_RESERVED_PARAM_NAMES: frozenset[str] = frozenset({"dry_run", "quiet", "strategy"})

# 支持的参数类型（list → list[str]，positional 映射 nargs，见 synth/_tool_args）
_PARAM_TYPES: frozenset[str] = frozenset({"str", "int", "float", "bool", "path", "choices", "list"})

# 命令声明顶层合法键
_TOP_KEYS = frozenset(
    {
        "help",
        "description",
        "aliases",
        "hidden",
        "cmd",
        "win",
        "unix",
        "args",
        "cwd",
        "timeout",
        "env",
        "needs",
        "strategy",
        "message",
        "when",
        "allow_upstream_skip",
    }
)

# 执行策略（与 ToolSpec.strategy 的 Literal 取值一致）
_STRATEGIES: frozenset[str] = frozenset({"sequential", "thread", "async", "dependency"})

# 平台子表（win/unix）内合法键
_PLATFORM_KEYS = frozenset({"cmd"})

# 参数声明表内合法键（on 仅 type=bool：truthy 时向 cmd 追加的固定 token）
_PARAM_KEYS = frozenset({"type", "default", "help", "choices", "on"})


class CommandDeclError(ValueError):
    """命令声明非法（配置错误，非运行时错误）。"""


@dataclass(frozen=True)
class WhenDecl:
    """执行守卫探针声明（TOML ``when`` 表的解析结果）。

    任务执行前同步求值一次，不满足则任务 SKIPPED（命令退出码 0、汇总
    记为跳过）。探针自身失败（命令不存在/路径异常）由引擎
    :meth:`fcmd.apis.task.TaskSpec.should_execute` 捕获并视为条件不满足。

    参数
    ----
    cmd:
        命令探针（shell 字符串，按 stdout 是否非空判定）；与 ``path`` 互斥
    path:
        路径探针（``~`` 展开后按存在性判定）；与 ``cmd`` 互斥
    expect:
        期望值：cmd 探针 ``nonempty`` / ``empty``；path 探针 ``exists`` / ``missing``
    """

    cmd: str | None = None
    path: str | None = None
    expect: str = "nonempty"


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
    needs:
        依赖的子命令名（引用同工具其他子命令；单命令形态禁用）
    strategy:
        执行策略：``sequential`` / ``thread`` / ``async`` / ``dependency``
    message:
        执行成功后打印的完成消息（支持 ``{参数名}`` 插值）；空串表示不打印
    when:
        执行守卫探针声明（任务执行前求值，不满足则 SKIPPED）；``None`` 表示未声明
    allow_upstream_skip:
        硬依赖被 SKIPPED 时本任务是否仍执行（聚合链豁免场景）
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
    needs: tuple[str, ...] = ()
    strategy: str | None = None
    message: str = ""
    when: WhenDecl | None = None
    allow_upstream_skip: bool = False


@dataclass(frozen=True)
class ToolDecl:
    """工具级声明（TOML ``[commands.<name>]`` 表的解析结果）。

    单命令形态（``flat=True``）：``commands`` 恰含一个与工具同名的
    :class:`CommandDecl`，注册为子命令 ``None`` 的单命令工具。
    多子命令形态（``flat=False``）：``commands`` 为各 ``[commands.<name>.<sub>]``
    子表声明的元组，注册时每个子命令挂到工具名下。

    参数
    ----
    name:
        工具名（CLI 调用名）
    commands:
        命令声明元组（单命令形态恰一个，多子命令形态至少一个）
    flat:
        ``True`` 表示单命令形态
    description:
        工具描述（fcmd 工具列表页）；多子命令形态且工具未在 Python 中注册时
        传播到各子命令 ToolSpec
    aliases:
        工具级别名（多子命令形态）
    """

    name: str
    commands: tuple[CommandDecl, ...]
    flat: bool = True
    description: str = ""
    aliases: tuple[str, ...] = ()


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
        参数类型：``str`` / ``int`` / ``float`` / ``bool`` / ``path`` / ``choices`` / ``list``
    default:
        默认值（``None`` = 无默认 → positional）
    help:
        参数帮助文本
    choices:
        ``type="choices"`` 时的取值列表（全字符串）
    on:
        ``type="bool"`` 专用：值为真时向 cmd 尾部追加的固定 token 元组
        （如 ``["--fix", "--unsafe-fixes"]``）
    """

    name: str
    type: str = "str"
    default: str | int | float | bool | None = None
    help: str = ""
    choices: tuple[str, ...] = ()
    on: tuple[str, ...] = ()


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


def _parse_on_tokens(name: str, pname: str, ptype: str, on_raw: Any) -> tuple[str, ...]:
    """解析并校验 bool 参数的 on 固定 token。

    Raises
    ------
    CommandDeclError
        非 bool 类型声明 on / on 不是非空字符串数组
    """
    if not on_raw:
        return ()
    if ptype != "bool":
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 仅 type=bool 可声明 on")
    if not isinstance(on_raw, list) or not all(isinstance(t, str) and t for t in on_raw):
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 on 须是非空字符串数组")
    return tuple(on_raw)


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
    if ptype == "list" and default is not None:
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 type=list 不支持 default（positional 参数）")
    if default is not None:
        _check_default_type(name, pname, ptype, default)
        if ptype == "choices" and default not in choices_raw:
            raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 的 default 须在 choices 内: {default!r}")

    on_tokens = _parse_on_tokens(name, pname, ptype, table.get("on", []))

    return ParamDecl(
        name=pname,
        type=ptype,
        default=default,
        help=help_text,
        choices=tuple(choices_raw),
        on=on_tokens,
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


def _parse_needs_strategy(name: str, table: Mapping[str, Any]) -> tuple[tuple[str, ...], str | None]:
    """解析并校验 needs 与 strategy 字段。

    Returns
    -------
    tuple
        (needs, strategy)，未声明时为 ((), None)

    Raises
    ------
    CommandDeclError
        needs 不是非空字符串数组 / strategy 不在支持集合内
    """
    needs_raw = table.get("needs", [])
    if not isinstance(needs_raw, list) or not all(isinstance(n, str) and n for n in needs_raw):
        raise CommandDeclError(f"命令 {name!r} 的 needs 须是非空字符串数组")
    strategy = table.get("strategy")
    if strategy is not None and (not isinstance(strategy, str) or strategy not in _STRATEGIES):
        raise CommandDeclError(f"命令 {name!r} 的 strategy 须是 {sorted(_STRATEGIES)} 之一，实际: {strategy!r}")
    return tuple(needs_raw), strategy


# when 表内合法键（cmd/path 二选一 + expect）
_WHEN_KEYS = frozenset({"cmd", "path", "expect"})

# 探针期望值枚举（cmd → stdout 判定；path → 存在性判定）
_WHEN_CMD_EXPECTS: frozenset[str] = frozenset({"nonempty", "empty"})
_WHEN_PATH_EXPECTS: frozenset[str] = frozenset({"exists", "missing"})


def _parse_when(name: str, value: Any) -> WhenDecl | None:
    """解析并校验 when 探针表（cmd/path 互斥，expect 按探针类型枚举）。

    Returns
    -------
    WhenDecl | None
        未声明 when 时返回 ``None``

    Raises
    ------
    CommandDeclError
        声明非法（非表 / 未知键 / cmd 与 path 同时或均未提供 /
        探针目标非非空字符串 / expect 缺失或取值与探针类型不匹配）
    """
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise CommandDeclError(
            f'命令 {name!r} 的 when 须是表（when = {{cmd = "...", expect = "..."}}），实际: {value!r}'
        )
    unknown = set(value) - _WHEN_KEYS
    if unknown:
        raise CommandDeclError(f"命令 {name!r} 的 when 表含未知键: {sorted(unknown)}")
    cmd = value.get("cmd")
    path = value.get("path")
    if (cmd is None) == (path is None):
        raise CommandDeclError(f"命令 {name!r} 的 when 须且仅须提供 cmd 或 path 之一")
    if cmd is not None:
        probe, expects = cmd, _WHEN_CMD_EXPECTS
    else:
        assert path is not None  # cmd/path 恰有其一
        probe, expects = path, _WHEN_PATH_EXPECTS
    if not isinstance(probe, str) or not probe:
        raise CommandDeclError(f"命令 {name!r} 的 when 探针须是非空字符串，实际: {probe!r}")
    expect = value.get("expect")
    if expect not in expects:
        raise CommandDeclError(f"命令 {name!r} 的 when.expect 须是 {sorted(expects)} 之一，实际: {expect!r}")
    return WhenDecl(cmd=cmd, path=path, expect=str(expect))


def _check_list_cmd_placeholders(
    name: str, where: str, cmd: str | tuple[str, ...] | None, args: tuple[ParamDecl, ...]
) -> None:
    """校验 tuple cmd 中 list 参数占位符形态。

    无 shell 的 tuple cmd 里，list 值仅在**独占占位符项**（整项恰为
    ``{name}``）下按元素展开；部分占位（如 ``prefix-{name}``）会被插成
    单个带空格 token，声明期直接报错。str cmd（shell 执行）按空格拼接
    语义合法，不校验。

    Raises
    ------
    CommandDeclError
        list 参数在 tuple cmd 项中以非独占形式出现
    """
    if not isinstance(cmd, tuple):
        return
    list_names = {p.name for p in args if p.type == "list"}
    for item in cmd:
        for pname in list_names:
            token = "{" + pname + "}"
            if token in item and item != token:
                raise CommandDeclError(
                    f"命令 {name!r} 的 {where} 项 {item!r} 对 list 参数 {pname!r} 仅支持独占占位符（整项为 {token}）"
                )


def parse_command_table(name: str, table: Mapping[str, Any], *, subcommand: bool = False) -> CommandDecl:
    """解析并校验单个 ``[commands.<name>]`` 表。

    Parameters
    ----------
    name:
        命令名（TOML 子表名；多子命令形态下为子命令名）
    table:
        子表内容（TOML 解析出的映射）
    subcommand:
        ``True`` 表示多子命令形态的子命令（跳过顶层保留名校验）

    Returns
    -------
    CommandDecl
        校验通过的命令声明

    Raises
    ------
    CommandDeclError
        声明非法（保留名 / 未知键 / 缺 help / 无任何 cmd / 值类型错误 /
        when 探针非法 / 单命令形态带 needs）
    """
    name_re = _SUB_NAME_RE if subcommand else _TOOL_NAME_RE
    if not name_re.match(name):
        raise CommandDeclError(f"命令名 {name!r} 非法：须匹配 {name_re.pattern}")
    if not subcommand and name in _RESERVED_NAMES:
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
    has_cmd = not (win_cmd is None and unix_cmd is None and cmd is None)
    needs, strategy = _parse_needs_strategy(name, table)
    if not has_cmd and not needs:
        raise CommandDeclError(f"命令 {name!r} 须提供 win.cmd / unix.cmd / cmd 至少一个（无 cmd 时须声明 needs 聚合）")

    args_raw = table.get("args", {})
    if not isinstance(args_raw, Mapping):
        raise CommandDeclError(f"命令 {name!r} 的 args 须是表（[commands.{name}.args.<参数名>]）")
    args = tuple(parse_param_table(name, pname, ptable) for pname, ptable in args_raw.items())

    _check_list_cmd_placeholders(name, "cmd", cmd, args)
    _check_list_cmd_placeholders(name, "win.cmd", win_cmd, args)
    _check_list_cmd_placeholders(name, "unix.cmd", unix_cmd, args)

    if needs and not subcommand:
        raise CommandDeclError(f"命令 {name!r} 是单命令形态，不支持 needs（needs 引用同工具其他子命令）")

    cwd, timeout, env = _parse_transparency(name, table)

    message = table.get("message", "")
    if not isinstance(message, str):
        raise CommandDeclError(f"命令 {name!r} 的 message 须是字符串")

    when = _parse_when(name, table.get("when"))
    allow_upstream_skip = table.get("allow_upstream_skip", False)
    if not isinstance(allow_upstream_skip, bool):
        raise CommandDeclError(f"命令 {name!r} 的 allow_upstream_skip 须是布尔值")

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
        needs=needs,
        strategy=strategy,
        message=message,
        when=when,
        allow_upstream_skip=allow_upstream_skip,
    )


def parse_tool_table(name: str, table: Mapping[str, Any]) -> ToolDecl:
    """解析并校验 ``[commands.<name>]`` 表（自动识别单命令 / 多子命令形态）。

    形态识别：表含 ``cmd`` / ``win`` / ``unix`` / ``args`` 任一键即为单命令
    形态；否则每个表值键视为子命令表。两种形态混用报错。多子命令形态的
    工具级仅支持 ``description`` / ``aliases`` 元数据。

    Raises
    ------
    CommandDeclError
        声明非法（形态混用 / 工具级未知键 / 无子命令 / needs 引用不存在或
        自引用 / 子命令声明 aliases）
    """
    if not _TOOL_NAME_RE.match(name):
        raise CommandDeclError(f"命令名 {name!r} 非法：须匹配 {_TOOL_NAME_RE.pattern}")
    if name in _RESERVED_NAMES:
        raise CommandDeclError(f"命令名 {name!r} 是保留名（fcmd 或内建命令）")

    has_flat_key = bool({"cmd", "win", "unix", "args"} & set(table))
    sub_keys = [
        k
        for k, v in table.items()
        if isinstance(v, Mapping) and k not in _TOP_KEYS and k not in {"win", "unix", "args"}
    ]
    if has_flat_key and sub_keys:
        raise CommandDeclError(f"命令 {name!r} 混用单命令与子命令形态（单命令表内不能嵌套子命令表: {sub_keys}）")
    if has_flat_key:
        decl = parse_command_table(name, table)
        return ToolDecl(name=name, commands=(decl,), flat=True, description=decl.description, aliases=decl.aliases)
    return _parse_tool_subs(name, table)


def _parse_tool_subs(name: str, table: Mapping[str, Any]) -> ToolDecl:
    """解析多子命令形态：工具级元数据 + 各子命令表 + needs 引用校验。"""
    description = table.get("description", "")
    if not isinstance(description, str):
        raise CommandDeclError(f"命令 {name!r} 的 description 须是字符串")
    aliases_raw = table.get("aliases", [])
    if not isinstance(aliases_raw, list) or not all(isinstance(a, str) and a for a in aliases_raw):
        raise CommandDeclError(f"命令 {name!r} 的 aliases 须是非空字符串数组")

    commands: list[CommandDecl] = []
    for key in table:
        if key in {"description", "aliases"}:
            continue
        sub_table = table[key]
        if not isinstance(sub_table, Mapping):
            raise CommandDeclError(f"命令 {name!r} 的工具级仅支持 description/aliases 元数据，未知键: {key!r}")
        if "aliases" in sub_table:
            raise CommandDeclError(f"命令 {name!r} 的子命令 {key!r} 不支持 aliases（别名在工具级声明）")
        commands.append(parse_command_table(key, sub_table, subcommand=True))
    if not commands:
        raise CommandDeclError(f"命令 {name!r} 须至少声明一个子命令表（[commands.{name}.<子命令>]）")

    sub_names = {c.name for c in commands}
    for command in commands:
        for ref in command.needs:
            if ref == command.name:
                raise CommandDeclError(f"命令 {name!r} 的子命令 {command.name!r} 不能依赖自身")
            if ref not in sub_names:
                raise CommandDeclError(f"命令 {name!r} 的子命令 {command.name!r} 依赖不存在的子命令 {ref!r}")

    return ToolDecl(
        name=name, commands=tuple(commands), flat=False, description=description, aliases=tuple(aliases_raw)
    )
