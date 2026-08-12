"""``fcmd doctor`` 纯函数辅助：检查项收集与报告渲染。

提取自 :mod:`fcmd.cli.main` 的 ``FcmdApp._builtin_doctor``，将检查逻辑
（Python 版本 / 核心导入 / 工具模块扫描 / 可选依赖 / PATH 命令）与表格
渲染分离为纯函数，便于独立测试与复用。

本模块以下划线开头，``_ensure_tools_discovered`` 会跳过它（非工具模块）。
"""

from __future__ import annotations

import importlib
import shutil
import sys
from typing import Any

from fcmd.console import get_console

from ._env_helpers import collect_optional_deps_status

__all__ = ["collect_doctor_checks", "render_doctor_report"]


def collect_doctor_checks(tool_modules: dict[str, str]) -> list[dict[str, Any]]:
    """收集全部诊断检查项，返回 ``checks`` 列表。

    每项结构::

        {"item": str, "ok": bool, "detail": str, "fix": str}

    Parameters
    ----------
    tool_modules:
        规范工具名 → 模块路径映射（``_TOOL_MODULES``），由调用方传入
        避免本模块反向依赖 main.py 的模块状态
    """
    checks: list[dict[str, Any]] = []

    # 1. Python 版本 ≥ 3.8
    py_ok = sys.version_info >= (3, 8)
    checks.append(
        {
            "item": "Python 版本 ≥ 3.8",
            "ok": py_ok,
            "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "fix": "升级 Python 至 3.8+" if not py_ok else "",
        }
    )

    # 2. fcmd 核心模块导入
    try:
        import fcmd  # noqa: F401

        core_ok = True
        core_detail = ""
    except ImportError as e:  # pragma: no cover - fcmd 已导入才能执行到此处，分支不可达
        core_ok = False
        core_detail = str(e)
    checks.append(
        {
            "item": "fcmd 核心导入",
            "ok": core_ok,
            "detail": core_detail,
            "fix": "重装 fcmd: pip install --force-reinstall fcmd" if not core_ok else "",
        }
    )

    # 3. 工具模块全部可导入
    failed_tools: list[str] = []
    for tool_name, module_path in list(tool_modules.items()):
        try:
            importlib.import_module(module_path)
        except ImportError:
            failed_tools.append(tool_name)
    tool_total = len(tool_modules)
    tool_ok = not failed_tools
    checks.append(
        {
            "item": "工具模块扫描",
            "ok": tool_ok,
            "detail": f"{tool_total} 个工具" + (f"，失败: {', '.join(failed_tools)}" if failed_tools else ""),
            "fix": "检查失败工具模块的依赖与语法" if failed_tools else "",
        }
    )

    # 4. 可选依赖检查
    optional_deps = collect_optional_deps_status()
    for dep in optional_deps:
        checks.append(
            {
                "item": f"可选依赖 {dep['extra']} ({dep['package']})",
                "ok": dep["installed"],
                "detail": dep.get("version", "") or "未安装",
                "fix": f"pip install fcmd[{dep['extra']}]" if not dep["installed"] else "",
            }
        )

    # 5. PATH 中的常用外部命令
    for cmd in ("git", "uv", "python", "pip"):
        cmd_path = shutil.which(cmd)
        checks.append(
            {
                "item": f"PATH: {cmd}",
                "ok": cmd_path is not None,
                "detail": cmd_path or "未找到",
                "fix": f"安装 {cmd} 并加入 PATH" if cmd_path is None else "",
            }
        )

    return checks


def render_doctor_report(checks: list[dict[str, Any]]) -> int:
    """渲染诊断检查表格，返回退出码（0=全通过 / 1=有失败）。

    Parameters
    ----------
    checks:
        :func:`collect_doctor_checks` 返回的检查项列表
    """
    from fcmd.console import Table

    console = get_console()
    console.print("[bold cyan]fcmd 环境诊断[/bold cyan]\n")
    table = Table(show_header=True, header_style="bold", show_lines=False)
    table.add_column("检查项", style="cyan", no_wrap=True)
    table.add_column("状态", justify="center", no_wrap=True)
    table.add_column("详情")
    for c in checks:
        status = "[green]OK[/green]" if c["ok"] else "[red]FAIL[/red]"
        detail = c["detail"]
        if not c["ok"] and c["fix"]:
            detail = f"{detail}\n[dim]修复: {c['fix']}[/dim]"
        table.add_row(c["item"], status, detail)
    console.print(table)

    passed = sum(1 for c in checks if c["ok"])
    total = len(checks)
    if passed == total:
        console.print(f"\n[green]诊断结果: {passed}/{total} 全部通过[/green]")
        return 0
    console.print(f"\n[red]诊断结果: {passed}/{total} 通过，{total - passed} 项失败[/red]")
    return 1
