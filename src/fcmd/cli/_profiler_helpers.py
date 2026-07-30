"""``fcmd profiler`` 内建命令的辅助函数。

包含 hook 注入、目标脚本执行、报告输出三块独立逻辑，从 ``FcmdApp`` 中提取
以缩小 main.py 体积。本模块下划线开头，``_ensure_tools_discovered`` 会跳过它
（非工具模块）。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

from fcmd.console import get_console

__all__ = ["inject_run_hook", "output_profile", "run_target_script"]


def inject_run_hook() -> dict[str, Any]:
    """注入 hook 捕获 ``fcmd.run()`` 调用。

    同时 patch 三处引用：

    * ``fcmd.executors.run`` —— 实际实现
    * ``fcmd.run`` —— 顶层包导出的引用（用户脚本 ``fx.run()`` 常用入口）
    * ``RunReport.__init__`` —— 捕获 ``run()`` 内部创建的 report 实例，
      用于 ``run()`` 抛 ``TaskFailedError`` 时仍能拿到已填充的 report。

    另外修复懒加载属性被 import 系统遮蔽的问题：当 ``from fcmd.task import X``
    执行时，import 系统会把 ``fcmd.__dict__["task"]`` 设为 *module*（而非
    ``__getattr__`` 应返回的 ``task`` 函数），导致脚本中 ``@fx.task`` 报
    ``'module' object is not callable``。此处遍历 ``_LAZY_ATTRS``，将
    ``__dict__`` 中为 module 的属性覆盖为正确的函数/类。

    返回字典含 ``graph`` / ``report``（执行后填充）与 ``_restore`` 还原函数。
    """
    import types

    import fcmd as fcmd_mod
    from fcmd import executors as executors_mod
    from fcmd.apis.report import RunReport

    # 修复懒加载属性被 import 系统遮蔽：将 __dict__ 中为 module 的属性
    # 覆盖为 _LAZY_ATTRS 指定的函数/类。此修复不需要还原（修复的是
    # Python import 系统的副作用，还原反而会重新引入 bug）。
    lazy_attrs = getattr(fcmd_mod, "_LAZY_ATTRS", {})
    for attr_name, (module_path, symbol_name) in lazy_attrs.items():
        current = fcmd_mod.__dict__.get(attr_name)
        if isinstance(current, types.ModuleType):
            module = importlib.import_module(module_path)
            fcmd_mod.__dict__[attr_name] = getattr(module, symbol_name)

    captured: dict[str, Any] = {}
    original_exec_run = executors_mod.run
    has_top_run = "run" in fcmd_mod.__dict__
    original_top_run = fcmd_mod.__dict__.get("run")
    original_report_init = RunReport.__init__
    capture_enabled = [False]

    def patched_report_init(self_obj: RunReport, *args: Any, **kwargs: Any) -> None:
        original_report_init(self_obj, *args, **kwargs)
        if capture_enabled[0]:
            captured["report"] = self_obj

    RunReport.__init__ = patched_report_init  # type: ignore[assignment]

    def patched_run(graph: Any, *args: Any, **kwargs: Any) -> Any:
        captured["graph"] = graph
        capture_enabled[0] = True
        try:
            report = original_exec_run(graph, *args, **kwargs)
            captured["report"] = report
            return report
        finally:
            capture_enabled[0] = False

    executors_mod.run = patched_run  # type: ignore[assignment]
    fcmd_mod.run = patched_run  # pyrefly: ignore [missing-attribute]

    def _restore() -> None:
        executors_mod.run = original_exec_run  # type: ignore[assignment]
        if has_top_run:
            fcmd_mod.run = original_top_run  # type: ignore[assignment]
        else:
            del fcmd_mod.__dict__["run"]
        RunReport.__init__ = original_report_init  # type: ignore[assignment]

    captured["_restore"] = _restore
    return captured


def run_target_script(script: Path, script_args: list[str]) -> None:
    """以 ``__main__`` 身份执行目标脚本。"""
    import runpy

    sys.argv = [str(script), *script_args]
    script_dir = str(script.parent.resolve())
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    runpy.run_path(str(script), run_name="__main__")


def output_profile(
    profile: Any,
    export: str,
    output: str | None,
    script_stem: str,
    no_browser: bool,
) -> None:
    """输出性能报告到 stdout 或文件。"""
    if export == "text":
        sys.stdout.write(profile.describe())
        sys.stdout.write("\n")
        return

    # HTML 格式
    html = profile.to_html()
    if output:
        out_path = Path(output)
    else:
        out_path = Path.cwd() / f"{script_stem}_profile.html"
    out_path.write_text(html, encoding="utf-8")
    get_console().print(f"[green]HTML 报告已生成:[/green] {out_path}")

    if not no_browser:
        import webbrowser

        try:
            webbrowser.open(f"file:///{out_path.resolve().as_posix()}")
        except Exception as e:
            get_console().print(f"[yellow]警告:[/yellow] 无法打开浏览器: {e}")
