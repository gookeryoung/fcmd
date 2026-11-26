"""分层依赖守卫（ADR 0001 / 0003）：把 import 方向写成可执行断言。

目录形状由 :mod:`tests.test_layout` 守卫；本文件守卫「谁能 import 谁」。
新增跨层依赖时先判断是否符合分层，再改这里的规则；不要用懒导入掩盖反向依赖
（``core`` 对 ``fcmd.console`` 的历史懒导入是唯一例外，见 :data:`LAZY_CONSOLE_ALLOWED`）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from fcmd._compat import override

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "fcmd"

LAYERS = ("shared", "core", "infras", "gui", "tools", "apps", "cli", "mcp")

#: 每层禁止 import 的其它层。``None`` 表示只禁 stdlib 之外的 fcmd 层。
FORBIDDEN: dict[str, frozenset[str]] = {
    "shared": frozenset({"core", "infras", "gui", "tools", "apps", "cli", "mcp"}),
    "core": frozenset({"infras", "gui", "tools", "apps", "cli", "mcp"}),
    "infras": frozenset({"core", "gui", "tools", "apps", "cli", "mcp"}),
    "gui": frozenset({"core", "infras", "tools", "apps", "cli", "mcp"}),
    "tools": frozenset({"cli", "mcp", "apps"}),
    "apps": frozenset({"cli", "mcp"}),
    "mcp": frozenset({"tools", "apps"}),
    "cli": frozenset(),
}

#: ``core`` 可以在函数体内懒导入展示层；模块顶层 import 仍然禁止。
LAZY_CONSOLE_ALLOWED = frozenset({"core"})


class _ImportCollector(ast.NodeVisitor):
    """收集 ``fcmd.<layer>...`` import 及其是否位于函数体内。"""

    def __init__(self, relative: Path) -> None:
        self.relative = relative
        self.imports: list[tuple[str, int, bool]] = []
        self._function_depth = 0

    def _add(self, module: str, line: int) -> None:
        if module.startswith("fcmd."):
            self.imports.append((module, line, self._function_depth == 0))

    @override
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._add(alias.name, node.lineno)

    @override
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self._add(node.module, node.lineno)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    visit_FunctionDef = _visit_function
    visit_AsyncFunctionDef = _visit_function


def _iter_modules() -> list[tuple[str, Path, _ImportCollector]]:
    """返回 ``(layer, path, 收集结果)``；跳过 ``__pycache__`` 与 ``shared`` 之外的根模块。"""
    modules: list[tuple[str, Path, _ImportCollector]] = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = path.relative_to(PACKAGE)
        layer = relative.parts[0]
        if layer not in LAYERS:
            continue
        collector = _ImportCollector(relative)
        collector.visit(ast.parse(path.read_text(encoding="utf-8")))
        modules.append((layer, relative, collector))
    return modules


def _target_layer(module: str) -> str | None:
    parts = module.split(".")
    return parts[1] if len(parts) > 1 and parts[1] in LAYERS else None


def _format(violations: list[tuple[Path, int, str, str]]) -> str:
    return "\n".join(f"{path}:{line}: {source} -> {target}" for path, line, source, target in violations)


@pytest.mark.parametrize("layer", LAYERS)
def test_layer_import_direction(layer: str) -> None:
    """各层不得 import 上层 / 无关层（ADR 0001）。"""
    forbidden = FORBIDDEN[layer]
    violations: list[tuple[Path, int, str, str]] = []
    for module_layer, path, collector in _iter_modules():
        if module_layer != layer:
            continue
        for module, line, top_level in collector.imports:
            target = _target_layer(module)
            if target is None or target not in forbidden:
                continue
            if module == "fcmd.console" and layer in LAZY_CONSOLE_ALLOWED and not top_level:
                continue
            violations.append((path, line, layer, module))
    assert not violations, "跨层 import 违规（顶层 import；懒导入例外见 LAZY_CONSOLE_ALLOWED）：\n" + _format(
        violations
    )


def test_core_does_not_import_console_at_module_level() -> None:
    """展示层只能懒导入：``core`` 模块顶层不得 import ``fcmd.console``。"""
    violations = [
        (path, line)
        for layer, path, collector in _iter_modules()
        if layer == "core"
        for module, line, top_level in collector.imports
        if module == "fcmd.console" and top_level
    ]
    assert not violations, f"core 顶层 import fcmd.console: {violations}"


def test_tools_do_not_import_other_domains() -> None:
    """``tools/<域>`` 不互相 import 实现（可共享 infras / gui / core）。"""
    violations: list[tuple[Path, int, str, str]] = []
    for layer, path, collector in _iter_modules():
        if layer != "tools" or len(path.parts) < 3:
            continue
        source_domain = path.parts[1]
        for module, line, _top in collector.imports:
            parts = module.split(".")
            if len(parts) >= 3 and parts[1] == "tools" and not parts[2].startswith("_") and parts[2] != source_domain:
                violations.append((path, line, source_domain, module))
    assert not violations, "工具域之间不得互相 import：\n" + _format(violations)


def test_shared_only_imports_stdlib() -> None:
    """``fcmd.shared`` 是叶子层：只允许标准库 import。"""
    violations: list[tuple[Path, int, str, str]] = []
    for layer, path, collector in _iter_modules():
        if layer != "shared":
            continue
        for module, line, _top in collector.imports:
            if module.split(".")[0] == "fcmd":
                violations.append((path, line, "shared", module))
    assert not violations, "shared 只能依赖标准库：\n" + _format(violations)


#: 历史遗留的大文件白名单：只减不增。新增模块超过预算请拆分，而不是往这里加。
LEGACY_LARGE = {
    "apps/cmder/_cmder_qt.py",
    "apps/cmder/_cmder_qt_panels.py",
    "apps/cmder/_ops.py",
    "apps/cmder/app.py",
    "apps/dynacalc/_model.py",
    "apps/dynacalc/_params.py",
    "apps/filescan/_engine.py",
    "apps/filescan/_filescanw_qt.py",
    "apps/filesync/_core.py",
    "apps/llm/_llmw_qt_chat.py",
    "apps/llm/_llmw_qt_flow.py",
    "apps/llm/_llmw_qt_overview.py",
    "apps/llm/_llmw_qt_paint.py",
    "apps/llm/llmconvert.py",
    "apps/llm/llmdown.py",
    "apps/llm/llmengine.py",
    "apps/llm/llmserve.py",
    "apps/paramcad/_export.py",
    "apps/peview/_pe.py",
    "apps/peview/_report.py",
    "apps/pptmake/_design.py",
    "apps/pptmake/_pptmakew_qt.py",
    "apps/renamer/_core.py",
    "apps/renamer/_renamerw_qt.py",
    "apps/reshack/_reshackw_qt.py",
    "apps/wslman/wslman.py",
    "apps/wslman/wslmanw.py",
    "tools/dev/pyprof.py",
    "tools/pdf/pdfbase.py",
    "tools/system/fonttool.py",
}

LINE_BUDGET = 800


def test_qt_pages_excluded_from_coverage_and_typecheck() -> None:
    """``apps/<app>/qt/`` 的 Qt 页面胶水必须同时从 coverage 与 pyrefly 排除。

    ADR 0033 / 0034 约定 Qt 控件胶水不计覆盖率；ADR 0046 把页面迁入
    ``apps/<app>/qt/`` 后，若只改 pyrefly 而漏改 ``.coveragerc``，会让整目录
    计入覆盖率并拉低门禁（曾导致 3.14 覆盖门禁跌破阈值）。
    """
    root = PACKAGE.parent.parent
    coveragerc = (root / ".coveragerc").read_text(encoding="utf-8")
    omit_block = coveragerc.split("omit =", 1)[1].split("source", 1)[0]
    assert "src/fcmd/apps/*/qt/*" in omit_block, ".coveragerc omit 缺少 apps/*/qt 排除（ADR 0046）"
    pyrefly = (root / "pyrefly.toml").read_text(encoding="utf-8")
    assert "src/fcmd/apps/*/qt/**" in pyrefly, "pyrefly.toml 缺少 apps/*/qt 排除（ADR 0046）"


def test_new_modules_stay_within_line_budget() -> None:
    """新文件不超过 800 行；存量大文件只允许缩小，不允许新增白名单。"""
    oversized = sorted(
        p.relative_to(PACKAGE).as_posix()
        for p in PACKAGE.rglob("*.py")
        if "__pycache__" not in p.parts
        and len(p.read_text(encoding="utf-8").splitlines()) > LINE_BUDGET
        and p.relative_to(PACKAGE).as_posix() not in LEGACY_LARGE
    )
    assert not oversized, f"新模块超过 {LINE_BUDGET} 行，请拆分：{oversized}"
