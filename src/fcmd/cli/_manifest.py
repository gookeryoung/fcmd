"""静态工具清单读取（快路径的清单校验与查询）。

``fcmd <tool>`` 的默认路径需要全量发现（扫描 + 导入 ``fcmd.tools`` 下全部
工具模块并加载全部 DSL 声明），单个工具调用为此付出约 150ms 固定成本。
本模块读取生成文件 :mod:`fcmd.cli._manifest_data`（工具名 → 模块路径 /
DSL 文件），配合 :func:`fcmd.cli._discovery.resolve_tool_fast` 只导入目标
工具，把 ``fcmd <tool>`` 的启动成本压到「解释器 + 单个模块」。

清单是生成文件，新增 / 删除 / 改名工具后须重新生成:：

    uv run python -m fcmd.cli._manifest_data

任何异常（清单缺失、``SCHEMA`` 不匹配、目录布局与清单不一致）都返回
``None``，由调用方回退全量发现——清单只影响速度，不影响语义。
:func:`tool_layout` 用「``fcmd/tools`` 下的候选模块文件集合」做新鲜度
指纹：布局一致说明没有新增 / 删除工具模块，清单条目仍指向有效模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from fcmd._lazylog import LazyLogger

__all__ = ["SCHEMA", "ToolEntry", "aliases_for", "ambiguous_names", "clear_cache", "load", "lookup", "tool_layout"]

logger = LazyLogger(__name__)

# fcmd 包目录（``src/fcmd``）：清单新鲜度校验直接扫描真实文件系统，
# 不走 importlib.resources（其导入链约 11ms，快路径不应承担）。
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# 清单格式版本：与 ``_manifest_data.SCHEMA`` 不一致时视为过期。
SCHEMA = 1


class ToolEntry(NamedTuple):
    """清单中的单条工具记录。

    ``kind`` 为 ``"python"`` 时 ``target`` 是模块路径；为 ``"dsl"`` 时
    ``target`` 是内置 ``fcmd/commands`` 下的 TOML 文件名。
    """

    name: str
    kind: str
    target: str


class _Manifest(NamedTuple):
    """校验通过的清单快照。"""

    tools: dict[str, ToolEntry]
    aliases: dict[str, str]
    ambiguous: frozenset[str]


_loaded = False
_cache: _Manifest | None = None


def tool_layout() -> frozenset[str] | None:
    """返回 ``fcmd/tools`` 下候选工具模块的相对路径集合（清单新鲜度指纹）。

    规则与 :func:`fcmd.cli._discovery.ensure_tools_discovered` 的扫描一致：
    顶层 ``*.py``（排除 ``_`` 前缀与 ``main.py``）；领域子包再列一层
    ``*.py``（排除 ``_`` 前缀）。目录读取失败返回 ``None``（按清单不可用处理）。
    """
    try:
        root = _PACKAGE_ROOT / "tools"
        if not root.is_dir():
            return None
        layout: set[str] = set()
        for entry in root.iterdir():
            name = entry.name
            if name.startswith("_"):
                continue
            if name.endswith(".py"):
                if name != "main.py":
                    layout.add(f"tools/{name}")
                continue
            if not entry.is_dir():
                continue
            for sub in entry.iterdir():
                if sub.name.startswith("_") or not sub.name.endswith(".py"):
                    continue
                layout.add(f"tools/{name}/{sub.name}")
    except OSError as exc:
        logger.debug("工具目录布局读取失败，清单不可用: %s", exc)
        return None
    return frozenset(layout)


def load() -> _Manifest | None:
    """加载并校验清单；不可用时返回 ``None``（调用方回退全量发现）。

    结果进程内缓存（含 ``None``，避免反复尝试损坏的清单）。
    """
    global _loaded, _cache  # noqa: PLW0603 - 清单缓存为模块级单例
    if not _loaded:
        _loaded = True
        _cache = _build()
    return _cache


def _build() -> _Manifest | None:
    """导入生成文件并校验版本与新鲜度。"""
    import importlib

    try:
        data = importlib.import_module("fcmd.cli._manifest_data")
    except Exception as exc:  # 清单缺失/损坏只影响速度：一律回退全量发现
        logger.debug("工具清单不可用，回退全量发现: %s", exc)
        return None

    if data.SCHEMA != SCHEMA:
        logger.debug("工具清单版本不匹配（%r != %r），回退全量发现", data.SCHEMA, SCHEMA)
        return None
    layout = tool_layout()
    if layout is None or layout != frozenset(data.LAYOUT):
        logger.debug("工具清单与当前目录布局不一致（已过期），回退全量发现")
        return None
    return _Manifest(dict(data.TOOLS), dict(data.ALIASES), frozenset(data.AMBIGUOUS))


def clear_cache() -> None:
    """清空清单缓存（供测试在替换清单 / 目录布局后重新加载）。"""
    global _loaded, _cache  # noqa: PLW0603 - 清单缓存为模块级单例
    _loaded = False
    _cache = None


def lookup(name: str) -> ToolEntry | None:
    """按规范名或别名查清单条目；清单不可用或未命中返回 ``None``。"""
    manifest = load()
    if manifest is None:
        return None
    return manifest.tools.get(manifest.aliases.get(name, name))


def aliases_for(canonical: str) -> list[str]:
    """返回清单中登记的别名（不含规范名本身）；清单不可用时返回空。"""
    manifest = load()
    if manifest is None:
        return []
    return sorted(alias for alias, target in manifest.aliases.items() if target == canonical)


def ambiguous_names() -> frozenset[str]:
    """返回同时被 Python 模块与内置 DSL 声明的工具名（须走全量发现合并）。"""
    manifest = load()
    if manifest is None:
        return frozenset()
    return manifest.ambiguous


def regenerate() -> None:  # pragma: no cover - 仅开发者显式执行
    """按当前全量发现结果重写 :mod:`fcmd.cli._manifest_data`。

    新增 / 删除 / 改名工具或调整别名后运行::

        uv run python -m fcmd.cli._manifest_data

    生成时隔离用户级 DSL（``FCMD_HOME`` 指向临时目录），保证清单与用户
    配置无关；``tests/cli/test_tools_manifest.py`` 会断言清单与实跑发现一致。
    """
    import os
    import tempfile
    from pathlib import Path

    from fcmd.cli import _discovery
    from fcmd.core.toolkit import _TOOL_REGISTRY
    from fcmd.dsl.loader import builtin_tool_decl, builtin_tool_files

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["FCMD_HOME"] = tmp
        _discovery._TOOLS_DISCOVERED = False
        _discovery._TOOL_ALIASES.clear()
        _discovery._TOOL_MODULES.clear()
        _discovery._DSL_TOOL_SOURCES.clear()
        _discovery.ensure_tools_discovered()

        python_tools = {name: path for name, path in _discovery._TOOL_MODULES.items() if name in _TOOL_REGISTRY}
        dsl_files: dict[str, str] = {}
        for name in _discovery._DSL_TOOL_SOURCES:
            for file_name in builtin_tool_files():
                if builtin_tool_decl(file_name, name) is not None:
                    dsl_files[name] = file_name
                    break
        ambiguous = frozenset(name for name in dsl_files if name in python_tools)
        tools = {name: ToolEntry(name, "python", path) for name, path in python_tools.items()}
        dsl_only = {name: file_name for name, file_name in dsl_files.items() if name not in python_tools}
        tools.update({name: ToolEntry(name, "dsl", file_name) for name, file_name in dsl_only.items()})
        aliases = {
            alias: target for alias, target in _discovery._TOOL_ALIASES.items() if alias != target and target in tools
        }
        layout = sorted(tool_layout() or ())

    lines = [
        '"""静态工具清单（生成文件，勿手改）。',
        "",
        "由 ``python -m fcmd.cli._manifest_data`` 按当前全量发现结果生成：",
        "工具规范名 → ``ToolEntry``（工具类型与目标）、别名表、内置 DSL 与",
        "Python 模块重名的工具名，以及候选工具模块布局指纹（新鲜度校验用）。",
        "``fcmd <tool>`` 快路径据此只加载目标工具；清单缺失 / 过期 / 与布局",
        "不一致时一律回退全量发现（见 :mod:`fcmd.cli._manifest`）。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from fcmd.cli._manifest import ToolEntry",
        "",
        '__all__ = ["ALIASES", "AMBIGUOUS", "LAYOUT", "SCHEMA", "TOOLS"]',
        "",
        "# 清单格式版本（与 fcmd.cli._manifest.SCHEMA 比对，不一致即视为过期）",
        f"SCHEMA = {SCHEMA}",
        "",
        "TOOLS: dict[str, ToolEntry] = {",
    ]
    lines.extend(
        f'    "{name}": ToolEntry("{name}", "{entry.kind}", "{entry.target}"),' for name, entry in sorted(tools.items())
    )
    lines += [
        "}",
        "",
        "ALIASES: dict[str, str] = {",
    ]
    lines.extend(f'    "{alias}": "{target}",' for alias, target in sorted(aliases.items()))
    lines += [
        "}",
        "",
        "# 同时被 Python 模块与内置 DSL 声明（子命令须全量合并，不走快路径）",
    ]
    if ambiguous:
        lines += ["AMBIGUOUS: frozenset[str] = frozenset(", "    {"]
        lines.extend(f'        "{name}",' for name in sorted(ambiguous))
        lines += ["    }", ")"]
    else:
        lines.append("AMBIGUOUS: frozenset[str] = frozenset()")
    lines += [
        "",
        "# 生成时的 fcmd/tools 候选模块布局（相对 fcmd 包，用于检测清单过期）",
        "LAYOUT: tuple[str, ...] = (",
    ]
    lines.extend(f'    "{path}",' for path in layout)
    lines += [
        ")",
        "",
        "",
        'if __name__ == "__main__":  # pragma: no cover',
        "    from fcmd.cli._manifest import regenerate",
        "",
        "    regenerate()",
        "",
    ]

    Path(__file__).with_name("_manifest_data.py").write_text("\n".join(lines), encoding="utf-8")
    clear_cache()
