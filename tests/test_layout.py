"""包布局守卫：把目录不变量固化成可执行断言。

``fcmd`` 的三套机制隐式依赖目录形状——``fcmd.tools`` 的工具发现只扫顶层与
一层领域子包（``cli/_discovery.py``）；``cli/_manifest.tool_layout`` 用同一
形状做静态清单的新鲜度指纹；``dsl/actions/_index`` 按模块路径懒加载动作实现。
目录被无意加深、留空或塞入非工具目录时，这些机制不会报错，只会静默回退
（清单失效）或漏发现（工具消失），因此用独立的结构断言把问题变成红灯。
"""

from __future__ import annotations

from pathlib import Path

PACKAGE = Path(__file__).resolve().parent.parent / "src" / "fcmd"

MAX_DIR_LEVELS = 2
"""模块相对 ``src/fcmd`` 允许的最大目录层数（``<包>/[<子包>/]<模块>.py``）。"""

TOOLS = PACKAGE / "tools"


def _iter_dirs() -> list[Path]:
    """``src/fcmd`` 下的全部目录（跳过字节码缓存）。"""
    return [p for p in PACKAGE.rglob("*") if p.is_dir() and p.name != "__pycache__"]


def _iter_tool_modules() -> list[Path]:
    """``tools`` 命名空间下的候选工具模块（与发现机制同口径）。"""
    modules: list[Path] = []
    for entry in TOOLS.iterdir():
        if entry.is_dir():
            modules += [p for p in entry.glob("*.py") if not p.name.startswith("_")]
        elif entry.suffix == ".py" and not entry.name.startswith("_") and entry.stem != "main":
            modules.append(entry)
    return modules


def test_module_depth_within_two_levels() -> None:
    """模块不得深于 ``fcmd/<包>/<子包>/<模块>.py``。"""
    deepest = sorted(
        str(p.relative_to(PACKAGE))
        for p in PACKAGE.rglob("*.py")
        if len(p.relative_to(PACKAGE).parts) > MAX_DIR_LEVELS + 1
    )
    assert not deepest, f"模块目录超过 {MAX_DIR_LEVELS} 层: {deepest}"


def test_directories_are_not_empty() -> None:
    """不留空壳目录：空目录会让发现机制与读者都误判层级。"""
    empty = sorted(
        str(p.relative_to(PACKAGE)) for p in _iter_dirs() if not any(c.name != "__pycache__" for c in p.iterdir())
    )
    assert not empty, f"存在空目录: {empty}"


def test_python_directories_are_packages() -> None:
    """含 ``.py`` 的目录必须是常规包（有 ``__init__.py``），不依赖隐式命名空间包。"""
    missing = sorted(
        str(p.relative_to(PACKAGE)) for p in _iter_dirs() if list(p.glob("*.py")) and not (p / "__init__.py").is_file()
    )
    assert not missing, f"缺少 __init__.py: {missing}"


def test_tool_names_are_unique() -> None:
    """工具名取模块名末段；同名会让后注册者覆盖前者。"""
    names = [p.stem for p in _iter_tool_modules()]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert names, "tools 下没有发现任何工具模块"
    assert not duplicates, f"工具名重复: {duplicates}"


def test_domains_are_flat() -> None:
    """领域子包内不再嵌套子包（发现机制只下探一层）。"""
    nested = sorted(
        str(p.relative_to(TOOLS))
        for p in TOOLS.iterdir()
        if p.is_dir() and any(c.is_dir() and c.name != "__pycache__" for c in p.iterdir())
    )
    assert not nested, f"领域子包内嵌套了子目录: {nested}"
