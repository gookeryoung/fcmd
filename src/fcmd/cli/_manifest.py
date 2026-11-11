"""静态工具清单读取（快路径的清单校验与查询）。

``fcmd <tool>`` 的默认路径需要全量发现（扫描 + 导入 ``fcmd.tools`` 下全部
工具模块并加载全部 DSL 声明），单个工具调用为此付出约 150ms 固定成本。
本模块读取生成文件 :mod:`fcmd.cli._manifest_data`（工具名 → 模块路径 /
DSL 文件），配合 :func:`fcmd.cli._discovery.resolve_tool_fast` 只导入目标
工具，把 ``fcmd <tool>`` 的启动成本压到「解释器 + 单个模块」。

清单是生成文件，新增 / 删除 / 改名工具后须重新生成:：

    uv run python -m fcmd.cli._manifest_data

任何异常（清单缺失、``SCHEMA`` 不匹配、目录布局与清单不一致、内容指纹
不符）都返回 ``None``，由调用方回退全量发现——清单只影响速度，不影响语义。
:func:`tool_layout` 用「``fcmd/tools`` 与 ``fcmd/apps`` 下的候选模块文件集合」
做布局指纹：布局一致说明没有新增 / 删除工具模块，清单条目仍指向有效模块。

列表路径额外要求 :func:`tool_digest`（工具源码 + 内置 ``commands/*.toml``
的内容指纹）与生成期一致：描述与别名都写在这些文件里，只校验布局会列出
过期文案。
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

from fcmd._lazylog import LazyLogger

__all__ = [
    "SCHEMA",
    "ToolEntry",
    "aliases_for",
    "ambiguous_names",
    "clear_cache",
    "list_rows",
    "load",
    "load_strict",
    "lookup",
    "tool_digest",
    "tool_layout",
]

logger = LazyLogger(__name__)

# fcmd 包目录（``src/fcmd``）：清单新鲜度校验直接扫描真实文件系统，
# 不走 importlib.resources（其导入链约 11ms，快路径不应承担）。
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent

# 清单格式版本：与 ``_manifest_data.SCHEMA`` 不一致时视为过期。
# 2：新增 ``DESCRIPTIONS`` / ``DIGEST``（列表快路径）。
SCHEMA = 2


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
    descriptions: dict[str, str]
    digest: str


_loaded = False
_cache: _Manifest | None = None

_strict_loaded = False
_strict_cache: _Manifest | None = None


def _collect_candidates(root_name: str, scan_root: Path) -> list[tuple[str, Path]]:
    """收集单个工具根包下的候选模块（顶层 + 一层子包，跳过私有与 ``main``）。"""
    files: list[tuple[str, Path]] = []
    for entry in sorted(scan_root.iterdir(), key=lambda item: item.name):
        name = entry.name
        if name.startswith("_"):
            continue
        if name.endswith(".py"):
            if name != "main.py":
                files.append((f"{root_name}/{name}", entry))
            continue
        if not entry.is_dir():
            continue
        for sub in sorted(entry.iterdir(), key=lambda item: item.name):
            if sub.name.startswith("_") or not sub.name.endswith(".py"):
                continue
            files.append((f"{root_name}/{name}/{sub.name}", sub))
    return files


def _source_files() -> list[tuple[str, Path]] | None:
    """返回影响工具元数据的文件（相对 fcmd 包的路径 + 绝对路径）。

    覆盖范围与 :func:`tool_layout` 的扫描规则一致（``tools`` / ``apps`` 两个
    工具根下的候选模块），并加上内置 ``commands/*.toml``（DSL 命令的名字 /
    别名 / 描述都在这里）。``tools`` 目录读取失败返回 ``None``（按清单不可用
    处理）；``apps`` 目录缺失时跳过。
    """
    root = _PACKAGE_ROOT
    tools_root = root / "tools"
    files: list[tuple[str, Path]] = []
    try:
        if not tools_root.is_dir():
            return None
        for root_name in ("tools", "apps"):
            scan_root = root / root_name
            if scan_root.is_dir():
                files.extend(_collect_candidates(root_name, scan_root))
        commands_root = root / "commands"
        if commands_root.is_dir():
            for sub in sorted(commands_root.iterdir(), key=lambda item: item.name):
                if sub.name.startswith("_") or not sub.name.endswith(".toml"):
                    continue
                files.append((f"commands/{sub.name}", sub))
    except OSError as exc:
        logger.debug("工具源码目录读取失败，清单不可用: %s", exc)
        return None
    return files


def tool_layout() -> frozenset[str] | None:
    """返回工具根包下候选工具模块的相对路径集合（清单布局指纹）。

    规则与 :func:`fcmd.cli._discovery.ensure_tools_discovered` 的扫描一致：
    ``tools`` / ``apps`` 顶层 ``*.py``（排除 ``_`` 前缀与 ``main.py``）；
    子包再列一层 ``*.py``（排除 ``_`` 前缀）。目录读取失败返回 ``None``
    （按清单不可用处理）。
    """
    files = _source_files()
    if files is None:
        return None
    return frozenset(rel for rel, _ in files if rel.startswith(("tools/", "apps/")))


def tool_digest() -> str | None:
    """返回工具源码与内置声明的内容指纹（列表快路径的新鲜度校验）。

    逐文件读入 ``blake2b``，任一工具模块或 ``commands/*.toml`` 的内容变化
    都会改变指纹。读取失败返回 ``None``（按清单不可用处理）。
    """
    files = _source_files()
    if files is None:
        return None
    digest = hashlib.blake2b(digest_size=16)
    try:
        for rel, path in files:
            data = path.read_bytes()
            digest.update(rel.encode("utf-8"))
            digest.update(b"\0")
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
    except OSError as exc:
        logger.debug("工具源码读取失败，清单严格校验不可用: %s", exc)
        return None
    return digest.hexdigest()


def load() -> _Manifest | None:
    """加载并校验清单；不可用时返回 ``None``（调用方回退全量发现）。

    只校验版本与目录布局（``fcmd <tool>`` 快路径只需模块路径，代价最低）。
    结果进程内缓存（含 ``None``，避免反复尝试损坏的清单）。
    """
    global _loaded, _cache  # noqa: PLW0603 - 清单缓存为模块级单例
    if not _loaded:
        _loaded = True
        _cache = _build()
    return _cache


def load_strict() -> _Manifest | None:
    """在 :func:`load` 基础上追加内容指纹校验（列表快路径用）。

    工具描述来自模块源码 / TOML 声明，布局不变而内容变化（改文案、改别名）
    时清单会过期，必须回退全量发现。结果同样进程内缓存。
    """
    global _strict_loaded, _strict_cache  # noqa: PLW0603 - 严格缓存为模块级单例
    if not _strict_loaded:
        _strict_loaded = True
        manifest = load()
        if manifest is not None:
            digest = tool_digest()
            if digest is None or digest != manifest.digest:
                logger.debug("工具清单内容指纹不一致（已过期），回退全量发现")
            else:
                _strict_cache = manifest
    return _strict_cache


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
    return _Manifest(
        dict(data.TOOLS),
        dict(data.ALIASES),
        frozenset(data.AMBIGUOUS),
        dict(data.DESCRIPTIONS),
        data.DIGEST,
    )


def clear_cache() -> None:
    """清空清单缓存（供测试在替换清单 / 目录布局后重新加载）。"""
    global _loaded, _cache, _strict_loaded, _strict_cache  # noqa: PLW0603 - 模块级单例
    _loaded = False
    _cache = None
    _strict_loaded = False
    _strict_cache = None


def list_rows() -> list[tuple[str, list[str], str]] | None:
    """列表快路径数据：``(规范名, 别名列表, 描述)``，按规范名排序。

    严格校验不通过（清单缺失 / 版本不符 / 布局或内容指纹不一致）时返回
    ``None``，调用方回退 :func:`fcmd.cli._discovery.ensure_tools_discovered`。
    """
    manifest = load_strict()
    if manifest is None:
        return None
    return [
        (
            name,
            sorted(alias for alias, target in manifest.aliases.items() if target == name),
            manifest.descriptions.get(name, ""),
        )
        for name in sorted(manifest.tools)
    ]


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


def _literal(value: str) -> str:
    """生成双引号 Python 字符串字面量（与 ``ruff format`` 风格一致）。"""
    import json

    return json.dumps(value, ensure_ascii=False)


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

        registered = set(_TOOL_REGISTRY)
        python_tools = {name: path for name, path in _discovery._TOOL_MODULES.items() if name in registered}
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
        # 导入失败的工具仍出现在列表里（描述为空），清单必须同样收录，
        # 否则列表快路径会少一行；kind="unknown" 让单工具快路径继续回退。
        for name in sorted(set(_discovery._TOOL_ALIASES.values())):
            tools.setdefault(name, ToolEntry(name, "unknown", ""))
        aliases = {
            alias: target for alias, target in _discovery._TOOL_ALIASES.items() if alias != target and target in tools
        }
        layout = sorted(tool_layout() or ())
        # 描述口径与运行期完全一致：直接复用 tool_description
        descriptions = {name: _discovery.tool_description(name) for name in sorted(tools)}

    digest = tool_digest()
    if digest is None:  # pragma: no cover - 仅源码目录不可读时触发
        raise RuntimeError("无法计算工具源码指纹，清单未生成")

    lines = [
        '"""静态工具清单（生成文件，勿手改）。',
        "",
        "由 ``python -m fcmd.cli._manifest_data`` 按当前全量发现结果生成：",
        "工具规范名 → ``ToolEntry``（工具类型与目标）、别名表、工具描述、",
        "内置 DSL 与 Python 模块重名的工具名，以及候选工具模块布局与源码",
        "内容指纹（新鲜度校验用）。``fcmd <tool>`` 快路径据此只加载目标工具，",
        "``fcmd`` 列表快路径据此直接取名字 / 别名 / 描述、不导入任何工具模块；",
        "清单缺失 / 过期 / 与布局或内容指纹不一致时一律回退全量发现",
        "（见 :mod:`fcmd.cli._manifest`）。",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from fcmd.cli._manifest import ToolEntry",
        "",
        '__all__ = ["ALIASES", "AMBIGUOUS", "DESCRIPTIONS", "DIGEST", "LAYOUT", "SCHEMA", "TOOLS"]',
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
        "# 列表页描述（与 _discovery.tool_description 同口径，供列表快路径直接使用）",
        "DESCRIPTIONS: dict[str, str] = {",
    ]
    lines.extend(f"    {_literal(name)}: {_literal(text)}," for name, text in sorted(descriptions.items()))
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
        "# 工具源码 + 内置 commands/*.toml 的内容指纹（列表快路径的严格校验）",
        f"DIGEST = {_literal(digest)}",
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
