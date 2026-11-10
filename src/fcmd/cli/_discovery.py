"""fcmd.tools 工具发现与注册表查询。

``fcmd.tools`` 包下每个非 ``main`` / 非 ``_`` 前缀的模块即一个工具，
模块名即工具名。模块内可选定义 ``__tool_aliases__: list[str]`` 声明别名。

首次调用 :func:`ensure_tools_discovered` 时用 ``pkgutil.iter_modules``
扫描并导入所有工具模块，随后加载 DSL 声明式命令（内置
``fcmd/commands/*.toml`` + 用户级 ``${FCMD_HOME:-~/.fcmd}/commands.toml``）
统一注册进 ``_TOOL_REGISTRY``。``import fcmd`` 冷启动不受影响（本模块
顶层不执行扫描）。

优先级：Python 模块工具 > 用户 DSL > 内置 DSL（代码不可被配置遮蔽，
用户配置可覆盖出厂配置）。

本模块是工具发现状态的唯一定义处；``main.py`` 与 ``_builtins/`` 下
各内建命令均从此处查询，测试应 patch 本命名空间。

``fcmd <tool>`` 的快路径 :func:`resolve_tool_fast` 也在本模块：按生成清单
（:mod:`fcmd.cli._manifest`）只加载目标工具；清单未命中 / 过期或需要与
DSL / 用户命令合并时返回 ``None``，由调用方回退 :func:`ensure_tools_discovered`。
"""

from __future__ import annotations

import contextlib
import importlib
import importlib.util
import os
import pkgutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd._lazyload import timed_import
from fcmd._lazylog import LazyLogger

if TYPE_CHECKING:
    from fcmd.cli._manifest import ToolEntry
    from fcmd.core.toolkit import ToolSpec
    from fcmd.dsl.decl import ToolDecl

logger = LazyLogger(__name__)

__all__ = [
    "aliases_for",
    "ensure_tools_discovered",
    "import_all_tool_modules",
    "import_tool_module",
    "load_tool_subs",
    "resolve_tool",
    "resolve_tool_fast",
    "tool_description",
    "tool_names",
]

# 工具别名 → 规范名（由 ensure_tools_discovered 懒填充）
_TOOL_ALIASES: dict[str, str] = {}

# 规范工具名 → 模块路径（由 ensure_tools_discovered 懒填充）
_TOOL_MODULES: dict[str, str] = {}

# DSL 命令注册状态：{工具名: 来源}（"builtin" / "user"，由 _register_dsl_commands 懒填充）
_DSL_TOOL_SOURCES: dict[str, str] = {}

# 发现标志：True 表示已扫描过 fcmd.tools 包
_TOOLS_DISCOVERED = False


def ensure_tools_discovered() -> None:
    """首次调用时扫描 ``fcmd.tools`` 包，发现工具模块并填充注册表。

    扫描两层结构：顶层模块与领域子包（``fcmd.tools.<域>.<工具>``，
    如 ``fcmd.tools.media.screenshot``）。工具名取模块名（最后一段），调用方式
    ``fcmd <工具名>`` 不受分组影响。领域子包内部不再嵌套子包。

    幂等：后续调用直接返回。用 ``setdefault`` 填充，不覆盖测试通过
    ``monkeypatch.setitem`` 注入的键。扫描时导入模块以读取
    ``__tool_aliases__`` 并触发 ``@fx.tool`` 注册。
    """
    global _TOOLS_DISCOVERED  # noqa: PLW0603
    if _TOOLS_DISCOVERED:
        return
    _TOOLS_DISCOVERED = True

    # 懒导入 fcmd.tools 以访问 __path__，避免 import fcmd 时触发
    import fcmd.tools as tools_pkg

    for _finder, name, ispkg in pkgutil.iter_modules(tools_pkg.__path__):
        # 排除入口模块、私有模块/包（_builtins 等）
        if name.startswith("_") or name == "main":
            continue
        if ispkg:
            # 领域子包：工具名取子包内模块名，模块路径带域前缀
            _discover_domain(name)
        else:
            _register_tool(f"fcmd.tools.{name}", name)

    # 模块扫描后注册 DSL 声明式命令（Python 工具优先级高于 DSL，
    # 冲突时 DSL 侧跳过，见 _register_dsl_tool）
    _register_dsl_commands()


def _register_dsl_commands() -> None:
    """加载两级 DSL 命令声明并注册（内置 → 用户，用户可覆盖内置）。"""
    # 懒导入避免 cli ↔ dsl 包级循环（dsl.entry 懒导入本模块）
    from fcmd.dsl import builtin_tool_decls, user_tool_decls

    for tool in builtin_tool_decls():
        _register_dsl_tool(tool, "builtin")
    for tool in user_tool_decls():
        _register_dsl_tool(tool, "user")


def _register_dsl_tool(tool: ToolDecl, source: str) -> None:
    """注册单条 DSL 工具声明（含冲突/覆盖/合并/幂等规则）。

    规则：

    - 同源重入（幂等保护被重置后）：跳过注册，仅补别名
    - 用户声明覆盖内置**单命令**声明：移除旧注册后整体替换
    - 与已注册工具（Python 模块）重名：单命令形态 warning 跳过（Python 优先）；
      多子命令形态**逐子命令合并**（与 ``pymake``/``gittool`` 等混合工具的
      既有 Python 子命令共存，同名子命令先注册者优先并 warning）
    - 全新工具：单命令形态注册为子命令 ``None``；多子命令形态逐个注册，
      工具级 description 传播到各子命令（用于工具列表页）
    """
    # 懒导入避免 cli ↔ dsl 包级循环
    from fcmd.core.toolkit import _TOOL_REGISTRY, _register_tool
    from fcmd.dsl import build_tool_spec

    existing_source = _DSL_TOOL_SOURCES.get(tool.name)
    if existing_source == source:
        # 同源幂等重入：registry 已在册，仅补别名（别名表可能被重置）
        _register_dsl_aliases(tool)
        return
    if existing_source is not None and tool.flat:
        # 用户声明覆盖内置单命令声明：移除旧注册后替换
        _TOOL_REGISTRY.pop(tool.name, None)
    elif tool.name in _TOOL_REGISTRY:
        if tool.flat:
            logger.warning("DSL 命令 %r 与已注册的 Python 工具重名，已跳过", tool.name)
            return
        # 多子命令形态与既有工具合并：逐子命令注册，同名先注册者优先
        for command in tool.commands:
            if command.name in _TOOL_REGISTRY[tool.name]:
                logger.warning("DSL 子命令 %r.%r 与已注册子命令重名，已跳过", tool.name, command.name)
                continue
            _register_tool(build_tool_spec(command, tool_name=tool.name, subcommand=command.name))
        _DSL_TOOL_SOURCES[tool.name] = source
        _register_dsl_aliases(tool)
        return
    specs = [
        build_tool_spec(
            command,
            tool_name=tool.name,
            subcommand=None if tool.flat else command.name,
        )
        for command in tool.commands
    ]
    if tool.description and not tool.flat:
        from dataclasses import replace

        specs = [replace(spec, description=tool.description) for spec in specs]
    for spec in specs:
        _register_tool(spec)
    _DSL_TOOL_SOURCES[tool.name] = source
    _register_dsl_aliases(tool)


def _register_dsl_aliases(tool: ToolDecl) -> None:
    """注册 DSL 工具别名（先到先得，冲突时 warning 忽略该别名）。

    单命令形态取命令声明中的 aliases；多子命令形态取工具级别名。
    """
    aliases = tool.commands[0].aliases if tool.flat else tool.aliases
    _TOOL_ALIASES.setdefault(tool.name, tool.name)
    for alias in aliases:
        previous = _TOOL_ALIASES.setdefault(alias, tool.name)
        if previous != tool.name:
            logger.warning("DSL 命令 %r 的别名 %r 已被工具 %r 占用，忽略该别名", tool.name, alias, previous)


def _discover_domain(domain: str) -> None:
    """扫描单个领域子包，注册其中全部工具模块。

    子包导入失败（可选依赖缺失等）时静默跳过整个域，不影响其他工具。
    """
    try:
        domain_pkg = importlib.import_module(f"fcmd.tools.{domain}")
    except ImportError:
        return
    for _finder, name, ispkg in pkgutil.iter_modules(domain_pkg.__path__):
        # 域内不允许嵌套子包，私有模块不算工具
        if ispkg or name.startswith("_"):
            continue
        _register_tool(f"fcmd.tools.{domain}.{name}", name)


def _register_tool(module_path: str, tool_name: str) -> None:
    """注册单个工具：填充模块映射并导入模块读取别名。

    导入失败时**保留**条目：``ImportError`` 表示可选 Python 包缺失，
    ``OSError`` 表示原生动态库缺失（如扩展模块加载失败），两类失败均不影响
    其余工具的发现，且保留条目以维持 ``tool_description`` /
    ``load_tool_subs`` 的友好报错。

    导入成功但未注册任何子命令的模块（如 ``envdev_core`` 等内部辅助模块）
    不算工具，从模块映射与别名表中移除，避免泄漏到工具列表。
    """
    _TOOL_MODULES.setdefault(tool_name, module_path)
    _TOOL_ALIASES.setdefault(tool_name, tool_name)
    _seed_main_module_alias(module_path)
    try:
        mod = importlib.import_module(module_path)
    except (ImportError, OSError) as exc:
        logger.warning("工具模块 %s 导入失败，已跳过: %s", module_path, exc)
        return
    # 懒导入避免 cli ↔ core 包级循环
    from fcmd.core.toolkit import _TOOL_REGISTRY

    if tool_name not in _TOOL_REGISTRY:
        # 模块已导入但无任何子命令：内部辅助模块，不算工具
        _TOOL_MODULES.pop(tool_name, None)
        if _TOOL_ALIASES.get(tool_name) == tool_name:
            _TOOL_ALIASES.pop(tool_name)
        return
    # 读取模块声明的别名
    aliases = getattr(mod, "__tool_aliases__", ())
    for alias in aliases:
        _TOOL_ALIASES.setdefault(alias, tool_name)


def _seed_main_module_alias(module_path: str) -> None:
    """独立入口场景下把 ``__main__`` 别名到真实模块名，防止二次导入。

    独立入口（fspack 包装器 ``runpy.run_module`` / ``python -m`` / 直接执行
    模块文件）以 ``__main__`` 身份执行工具模块：``@fx.tool`` 装饰器已在模块
    顶层执行并注册过子命令，但真实模块名不在 ``sys.modules`` 中。若发现阶段
    直接 ``import_module``，模块会被重新执行，装饰器再次注册同一子命令并抛出
    ``ValueError``。此处按 ``__file__`` 匹配后预置 ``sys.modules``，使后续
    导入命中缓存，注册表保持单一实例。
    """
    if module_path in sys.modules:
        return
    main_mod = sys.modules.get("__main__")
    if main_mod is None:
        return
    main_file = getattr(main_mod, "__file__", None)
    if not main_file:
        return
    try:
        spec = importlib.util.find_spec(module_path)
    except (ImportError, AttributeError, ValueError):
        return
    if spec is None or not spec.origin:
        return
    try:
        same = Path(main_file).samefile(spec.origin)
    except OSError:
        same = os.path.normcase(str(Path(main_file).resolve())) == os.path.normcase(str(Path(spec.origin).resolve()))
    if same:
        sys.modules[module_path] = main_mod


def resolve_tool(name: str) -> str | None:
    """解析工具名，返回规范名或 None。

    只做别名表精确匹配（``_TOOL_ALIASES`` 直接 lookup)：规范名与别名
    都在发现阶段显式登记，不做 ``tool`` / ``cmd`` 后缀推断，避免隐式
    补全把用户拼错的命令静默路由到别处。
    """
    return _TOOL_ALIASES.get(name)


def resolve_tool_fast(name: str) -> str | None:
    """按静态清单快速解析单个工具，命中时只加载该工具（返回规范名）。

    与 :func:`resolve_tool` 的区别：不触发 :func:`ensure_tools_discovered`
    的全量扫描与导入，而是读生成清单
    （:mod:`fcmd.cli._manifest`）后仅：
    Python 工具只导入目标模块；DSL 工具只解析声明它的那一个内置 TOML。

    以下情形返回 ``None``，由调用方回退 ``ensure_tools_discovered`` +
    :func:`resolve_tool`：

    - 清单缺失 / 版本或布局校验失败 / 名字未登记（含新工具未重新生成清单）
    - 名字同时被 Python 模块与内置 DSL 声明（子命令需全量合并）
    - 用户级 ``commands.toml`` 存在（用户声明可覆盖内置，须全量语义）
    - 目标模块导入失败或导入后未注册任何子命令
    """
    from fcmd.cli import _manifest

    entry = _manifest.lookup(name)
    if entry is None or entry.name in _manifest.ambiguous_names():
        return None
    if entry.kind == "python":
        resolved = _load_python_tool(entry)
    elif entry.kind == "dsl":
        resolved = _load_dsl_tool(entry)
    else:
        return None
    if resolved is None:
        return None
    for alias in _manifest.aliases_for(entry.name):
        _TOOL_ALIASES.setdefault(alias, entry.name)
    return resolved


def _load_python_tool(entry: ToolEntry) -> str | None:
    """清单快路径：只导入单个 Python 工具模块并校验注册结果。"""
    from fcmd.core.toolkit import _TOOL_REGISTRY

    _seed_main_module_alias(entry.target)
    try:
        importlib.import_module(entry.target)
    except (ImportError, OSError) as exc:
        logger.debug("清单快路径导入 %s 失败，回退全量发现: %s", entry.target, exc)
        _drop_module_specs(entry.target)
        return None
    if entry.name not in _TOOL_REGISTRY:
        # 模块已导入但未注册任何子命令（内部辅助模块），清单条目失效
        return None
    _TOOL_MODULES.setdefault(entry.name, entry.target)
    _TOOL_ALIASES.setdefault(entry.name, entry.name)
    return entry.name


def _load_dsl_tool(entry: ToolEntry) -> str | None:
    """清单快路径：只解析声明该工具的内置 TOML 并注册。"""
    home = Path(os.environ.get("FCMD_HOME", str(Path.home() / ".fcmd")))
    if (home / "commands.toml").is_file():
        # 用户声明可覆盖内置，保守起见回退全量发现
        return None

    from fcmd.core.toolkit import _TOOL_REGISTRY
    from fcmd.dsl import builtin_tool_decl

    decl = builtin_tool_decl(entry.target, entry.name)
    if decl is None:
        return None
    _register_dsl_tool(decl, "builtin")
    if entry.name not in _TOOL_REGISTRY:
        return None
    return entry.name


def _drop_module_specs(module_path: str) -> None:
    """移除某模块已注册的子命令（清单快路径导入半途失败时清理状态）。

    模块导入在注册若干子命令后抛错时不会进入 ``sys.modules``，回退全量
    发现会重新执行模块并再次注册同一子命令；先清理可避免 ``ValueError``。
    """
    from fcmd.core.toolkit import _TOOL_REGISTRY

    for tool_name, subs in list(_TOOL_REGISTRY.items()):
        for subcommand, spec in list(subs.items()):
            if getattr(spec.func, "__module__", "") == module_path:
                del subs[subcommand]
        if not subs:
            _TOOL_REGISTRY.pop(tool_name, None)


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
        with contextlib.suppress(ImportError, OSError):
            importlib.import_module(module_path)


def import_tool_module(tool_name: str) -> Exception | None:
    """导入工具对应的模块，返回导入异常（未登记或成功时返回 None）。

    统一 ``tool_description`` / ``load_tool_subs`` / ``main._run_tool`` 的
    惰性导入逻辑：模块未登记时返回 None（无需导入），``ImportError``
    表示可选 Python 包缺失，``OSError`` 表示原生库加载失败。
    """
    module_path = _TOOL_MODULES.get(tool_name)
    if module_path is None:
        return None
    try:
        timed_import(module_path, f"tool:{tool_name}")
    except (ImportError, OSError) as exc:
        return exc
    return None


def tool_description(tool_name: str) -> str:
    """获取工具描述（从 _TOOL_REGISTRY 中已注册 ToolSpec 的 description/help）。"""
    from fcmd.core.toolkit import _TOOL_REGISTRY

    # 触发模块导入以注册工具
    if import_tool_module(tool_name) is not None:
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
    from fcmd.console import print_error
    from fcmd.core.toolkit import _TOOL_REGISTRY

    error = import_tool_module(tool_name)
    if error is not None:
        print_error(f"加载工具 {tool_name!r} 失败: {error}")
        return None

    if tool_name not in _TOOL_REGISTRY:
        print_error(f"工具 {tool_name!r} 未注册")
        return None
    return _TOOL_REGISTRY[tool_name]
