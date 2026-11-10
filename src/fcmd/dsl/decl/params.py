"""参数声明（``[[commands.<name>.args]]``）的解析与校验。"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .errors import CommandDeclError

# 参数名模式：小写字母/下划线开头，允许小写字母/数字/下划线
_PARAM_NAME_RE = re.compile(r"^[a-z_][a-z0-9_]*$")

# 保留参数名：与 _add_global_options 的全局选项冲突（argparse 会崩溃）。
# 注：cwd 允许使用（引擎有 CLI 值覆盖装饰器 cwd 的既有语义）。
_RESERVED_PARAM_NAMES: frozenset[str] = frozenset({"dry_run", "quiet", "strategy"})

# 支持的参数类型（list → list[str]，positional 映射 nargs，见 synth/_tool_args）
_PARAM_TYPES: frozenset[str] = frozenset({"str", "int", "float", "bool", "path", "choices", "list"})

# 参数声明表内合法键（on 仅 type=bool：truthy 时向 cmd 追加的固定 token；
# default_env 仅 type=str：CLI 值等于 default 时的环境变量回退链）
_PARAM_KEYS = frozenset({"type", "default", "help", "choices", "on", "default_env"})


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
    default_env:
        环境变量回退链（仅 ``type="str"`` 且须同时声明 ``default``）：CLI
        解析值等于 ``default`` 时取链中第一个非空环境变量值；用于复刻
        ``参数或动态默认`` 的 Python 语义（如 ``username or getpass.getuser()``）
    """

    name: str
    type: str = "str"
    default: str | int | float | bool | None = None
    help: str = ""
    choices: tuple[str, ...] = ()
    on: tuple[str, ...] = ()
    default_env: tuple[str, ...] = ()


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


def _parse_default_env(name: str, pname: str, ptype: str, default: Any, raw: Any) -> tuple[str, ...]:
    """解析并校验 str 参数的 default_env 环境变量回退链。

    Raises
    ------
    CommandDeclError
        非 str 类型声明 default_env / 未同时声明 default / 值不是非空
        字符串或非空字符串数组
    """
    if ptype != "str":
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 仅 type=str 可声明 default_env")
    if default is None:
        raise CommandDeclError(f"命令 {name!r} 的参数 {pname!r} 声明 default_env 须同时提供 default")
    if isinstance(raw, str) and raw:
        return (raw,)
    if isinstance(raw, list) and raw and all(isinstance(e, str) and e for e in raw):
        return tuple(raw)
    raise CommandDeclError(
        f"命令 {name!r} 的参数 {pname!r} 的 default_env 须是非空字符串或非空字符串数组，实际: {raw!r}"
    )


def parse_param_table(name: str, pname: str, table: Mapping[str, Any]) -> ParamDecl:
    """解析并校验单个 ``[commands.<name>.args.<pname>]`` 表。

    Raises
    ------
    CommandDeclError
        声明非法（参数名 / 未知键 / 类型不支持 / bool 缺 default /
        choices 缺失 / default 类型不匹配 / default 不在 choices 内 /
        default_env 组合非法）
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

    default_env_raw = table.get("default_env")
    default_env = () if default_env_raw is None else _parse_default_env(name, pname, ptype, default, default_env_raw)

    return ParamDecl(
        name=pname,
        type=ptype,
        default=default,
        help=help_text,
        choices=tuple(choices_raw),
        on=on_tokens,
        default_env=default_env,
    )
