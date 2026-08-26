"""fcmd.cli 工具发现与注册表查询。

``fcmd.cli`` 包下每个非 ``main`` / 非 ``_`` 前缀的模块即一个工具，
模块名即工具名。模块内可选定义 ``__tool_aliases__: list[str]`` 声明别名。

首次调用 :func:`ensure_tools_discovered` 时用 ``pkgutil.iter_modules``
扫描并导入所有工具模块，``import fcmd`` 冷启动不受影响（本模块顶层
不执行扫描）。

本模块是工具发现状态的唯一定义处；``main.py`` 与 ``_builtins/`` 下
各内建命令均从此处查询，测试应 patch 本命名空间。
"""

from __future__ import annotations

import contextlib
import importlib
import pkgutil
from typing import TYPE_CHECKING

from fcmd.console import get_console

if TYPE_CHECKING:
    from fcmd.apis.toolkit import ToolSpec

__all__ = [
    "aliases_for",
    "ensure_tools_discovered",
    "import_all_tool_modules",
    "load_tool_subs",
    "resolve_tool",
    "tool_description",
    "tool_names",
]

# 工具别名 → 规范名（由 ensure_tools_discovered 懒填充）
_TOOL_ALIASES: dict[str, str] = {}

# 规范工具名 → 模块路径（由 ensure_tools_discovered 懒填充）
_TOOL_MODULES: dict[str, str] = {}

# 发现标志：True 表示已扫描过 fcmd.cli 包
_TOOLS_DISCOVERED = False


def ensure_tools_discovered() -> None:
    """首次调用时扫描 ``fcmd.cli`` 包，发现工具模块并填充注册表。

    幂等：后续调用直接返回。用 ``setdefault`` 填充，不覆盖测试通过
    ``monkeypatch.setitem`` 注入的键。扫描时导入模块以读取
    ``__tool_aliases__`` 并触发 ``@fx.tool`` 注册。
    """
    global _TOOLS_DISCOVERED  # noqa: PLW0603
    if _TOOLS_DISCOVERED:
        return
    _TOOLS_DISCOVERED = True

    # 懒导入 fcmd.cli 以访问 __path__，避免 import fcmd 时触发
    import fcmd.cli as cli_pkg

    for _finder, name, _ispkg in pkgutil.iter_modules(cli_pkg.__path__):
        # 排除入口模块、私有模块、包自身
        if name.startswith("_") or name == "main":
            continue
        module_path = f"fcmd.cli.{name}"
        tool_name = name
        _TOOL_MODULES.setdefault(tool_name, module_path)
        _TOOL_ALIASES.setdefault(tool_name, tool_name)
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        # 读取模块声明的别名
        aliases = getattr(mod, "__tool_aliases__", ())
        for alias in aliases:
            _TOOL_ALIASES.setdefault(alias, tool_name)


def resolve_tool(name: str) -> str | None:
    """解析工具名，返回规范名或 None。"""
    return _TOOL_ALIASES.get(name)


def tool_names() -> list[str]:
    """返回全部规范工具名（排序去重）。"""
    return sorted(set(_TOOL_ALIASES.values()))


def aliases_for(canonical: str) -> list[str]:
    """获取工具的别名（不含规范名本身）。"""
    return sorted(a for a, t in _TOOL_ALIASES.items() if t == canonical and a != canonical)


def import_all_tool_modules() -> None:
    """触发全部工具模块导入（用于统计 / 补全数据收集）。

    单个模块导入失败（可选依赖缺失）时静默跳过，不影响其余模块。
    """
    for _tool_name, module_path in list(_TOOL_MODULES.items()):
        with contextlib.suppress(ImportError):
            importlib.import_module(module_path)


def tool_description(tool_name: str) -> str:
    """获取工具描述（从 _TOOL_REGISTRY 中已注册 ToolSpec 的 description/help）。"""
    from fcmd.apis.toolkit import _TOOL_REGISTRY

    # 触发模块导入以注册工具
    if tool_name in _TOOL_MODULES:
        try:
            importlib.import_module(_TOOL_MODULES[tool_name])
        except ImportError:
            return ""

    if tool_name not in _TOOL_REGISTRY:
        return ""

    subs = _TOOL_REGISTRY[tool_name]
    for spec in subs.values():
        if spec.description:
            return spec.description
    for spec in subs.values():
        if not spec.hidden and spec.help:
            return spec.help
    return ""


def load_tool_subs(tool_name: str) -> dict[str | None, ToolSpec] | None:
    """加载工具模块并返回子命令字典，失败时打印错误并返回 None。"""
    from fcmd.apis.toolkit import _TOOL_REGISTRY

    if tool_name in _TOOL_MODULES:
        try:
            importlib.import_module(_TOOL_MODULES[tool_name])
        except ImportError as e:
            get_console().print(f"[red]错误:[/red] 加载工具 {tool_name!r} 失败: {e}")
            return None

    if tool_name not in _TOOL_REGISTRY:
        get_console().print(f"[red]错误:[/red] 工具 {tool_name!r} 未注册")
        return None
    return _TOOL_REGISTRY[tool_name]
