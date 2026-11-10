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

import os
import tomllib
from collections.abc import Mapping
from functools import cache, lru_cache
from pathlib import Path
from typing import Any

from fcmd._lazylog import LazyLogger

from .decl import CommandDeclError, ToolDecl, parse_tool_table

__all__ = ["builtin_tool_decl", "builtin_tool_decls", "builtin_tool_files", "clear_decl_cache", "user_tool_decls"]

logger = LazyLogger(__name__)


class _LazyResources:
    """``importlib.resources`` 的延迟代理：首次 ``files()`` 才导入（约 15ms）。"""

    def files(self, package: str) -> Any:
        from importlib import resources

        return resources.files(package)


resources: Any = _LazyResources()


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


def builtin_tool_files() -> tuple[str, ...]:
    """返回内置命令文件名（``*.toml``，按文件名排序，结果进程内缓存）。"""
    return _builtin_files_cached()


@lru_cache(maxsize=1)
def _builtin_files_cached() -> tuple[str, ...]:
    root = resources.files("fcmd").joinpath("commands")
    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name)
    except OSError as exc:
        logger.warning("内置命令目录读取失败，已跳过: %s", exc)
        return ()
    return tuple(e.name for e in entries if not e.name.startswith("_") and e.name.endswith(".toml"))


def builtin_tool_decls() -> list[ToolDecl]:
    """读取包内内置命令目录 ``fcmd/commands/*.toml``（按文件名排序逐文件加载）。

    出厂命令按工具拆分为多个 TOML 文件；文件名排序保证合并顺序确定，
    文件间同名工具先注册者优先。目录下非 ``*.toml`` 条目忽略。
    内置文件属项目源码，正常不可能损坏；防御性降级（warning + 跳过）
    仅为不阻断启动，配套 CI 门禁测试断言其逐条合法。

    结果按进程缓存：同一进程内重复调用（发现、测试、工具切换）只解析一次。
    """
    decls: list[ToolDecl] = []
    for file_name in _builtin_files_cached():
        decls.extend(_decls_from_file_cached(file_name))
    return decls


def builtin_tool_decl(file_name: str, tool_name: str) -> ToolDecl | None:
    """读取单个内置文件的声明，返回其中名为 ``tool_name`` 的工具。

    供静态清单快路径使用：只解析提供目标工具的单个文件，不加载其余声明。
    文件缺失 / 损坏 / 无该工具时返回 None（调用方回退全量发现）。
    """
    for decl in _decls_from_file_cached(file_name):
        if decl.name == tool_name:
            return decl
    return None


@cache
def _decls_from_file_cached(file_name: str) -> tuple[ToolDecl, ...]:
    """解析单个内置 TOML 文件（进程内缓存），坏文件 warning 后返回空。"""
    root = resources.files("fcmd").joinpath("commands")
    try:
        entry = next((item for item in root.iterdir() if item.name == file_name), None)
    except OSError as exc:
        logger.warning("内置命令文件 %s 读取失败，已跳过: %s", file_name, exc)
        return ()
    if entry is None:
        return ()
    try:
        data = tomllib.loads(entry.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        logger.warning("内置命令文件 %s 读取失败，已跳过: %s", file_name, exc)
        return ()
    return tuple(_parse_decls(data, f"<内置 {file_name}>"))


def clear_decl_cache() -> None:
    """清空内置声明缓存（供测试在替换资源目录后重新加载）。"""
    _builtin_files_cached.cache_clear()
    _decls_from_file_cached.cache_clear()


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
