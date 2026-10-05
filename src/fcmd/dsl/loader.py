"""DSL 文件定位与解析编排：内置命令目录 + 用户级 commands.toml。

两级配置来源：

* 内置：包内 ``fcmd/commands/`` 目录下的 ``*.toml``（按文件名排序逐文件
  加载，避免单文件臃肿；经 :mod:`importlib.resources` 读取，兼容
  wheel/Nuitka 等安装形态）。
* 用户级：``${FCMD_HOME:-~/.fcmd}/commands.toml``（用户自定义命令；
  ``FCMD_HOME`` 显式覆盖便于测试跨平台确定性——Windows 下 ``Path.home()``
  走 USERPROFILE 而非 HOME）。

容错策略：文件缺失静默返回空；文件级损坏（I/O / 编码 / TOML 语法）warning
后跳过整个文件；条目级非法（校验不过）仅跳过该条，其余命令继续——与
``_discovery`` 中"单模块导入失败不影响其他工具"的既有容错哲学一致。
任何配置问题都不阻断主 CLI 启动。
"""

from __future__ import annotations

import logging
import os
import tomllib
from collections.abc import Mapping
from importlib import resources
from pathlib import Path
from typing import Any

from .decl import CommandDeclError, ToolDecl, parse_tool_table

__all__ = ["builtin_tool_decls", "user_tool_decls"]

logger = logging.getLogger(__name__)


def _parse_decls(data: Mapping[str, Any], source: str) -> list[ToolDecl]:
    """解析 TOML 顶层结构（``[commands]`` 表），逐条校验。

    坏条目 warning 跳过，不影响其余命令。
    """
    commands = data.get("commands", {})
    if not isinstance(commands, Mapping):
        logger.warning("DSL 文件 %s 的 [commands] 不是表，已跳过整个文件", source)
        return []
    decls: list[ToolDecl] = []
    for name, table in commands.items():
        if not isinstance(table, Mapping):
            logger.warning("DSL 命令 %s（%s）的定义不是表，已跳过", name, source)
            continue
        try:
            decls.append(parse_tool_table(name, table))
        except CommandDeclError as exc:
            logger.warning("DSL 命令定义非法（%s），已跳过: %s", source, exc)
    return decls


def builtin_tool_decls() -> list[ToolDecl]:
    """读取包内内置命令目录 ``fcmd/commands/*.toml``（按文件名排序逐文件加载）。

    出厂命令按工具拆分为多个 TOML 文件；文件名排序保证合并顺序确定，
    文件间同名工具先注册者优先。目录下非 ``*.toml`` 条目忽略。
    内置文件属项目源码，正常不可能损坏；防御性降级（warning + 跳过）
    仅为不阻断启动，配套 CI 门禁测试断言其逐条合法。
    """
    root = resources.files("fcmd").joinpath("commands")
    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        logger.warning("内置命令目录读取失败，已跳过: %s", exc)
        return []
    decls: list[ToolDecl] = []
    for entry in entries:
        if entry.name.startswith("_") or not entry.name.endswith(".toml"):
            continue
        try:
            data = tomllib.loads(entry.read_bytes().decode("utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            logger.warning("内置命令文件 %s 读取失败，已跳过: %s", entry.name, exc)
            continue
        decls.extend(_parse_decls(data, f"<内置 {entry.name}>"))
    return decls


def user_tool_decls() -> list[ToolDecl]:
    """读取用户级命令定义：``${FCMD_HOME:-~/.fcmd}/commands.toml``。

    文件缺失返回空（未配置用户命令是常态，非警告事件）。
    """
    home = Path(os.environ.get("FCMD_HOME", str(Path.home() / ".fcmd")))
    path = home / "commands.toml"
    if not path.is_file():
        return []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.warning("用户命令文件 %s 解析失败，已跳过: %s", path, exc)
        return []
    return _parse_decls(data, str(path))
