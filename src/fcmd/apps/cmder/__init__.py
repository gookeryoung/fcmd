"""cmder —— 双面板文件管理器（Qt GUI，Total Commander 风格）。

应用包目录名为 ``cmder``，对外命令仍为 ``cmderw``（GUI 应用）；由
``__tool_name__`` 显式声明，避免工具名随目录名变化。应用入口在
:mod:`fcmd.apps.cmder.app`。

包门面**不在导入时**拉起 ``app``：fspack 打包的 ``cmderw`` 入口以
``runpy.run_module("fcmd.apps.cmder.app", run_name="__main__")`` 执行，父包
``__init__`` 若先导入一次 ``app``，注册表已有 ``cmderw``，runpy 再执行同一模块
会抛「工具已注册」而整包启动失败。发现逻辑在 ``_register_app`` 里显式导入
``<包>.app`` 触发 ``@fcmd.tool`` 注册；``from fcmd.apps.cmder import app/main``
仍可按需懒加载。
"""

from __future__ import annotations

from typing import Any

# 目录名与用户命令解耦：发现时以该名字注册（见 cli/_discovery._scan_root）
__tool_name__ = "cmderw"
__all__ = ["app", "main"]


def __getattr__(name: str) -> Any:
    """按需暴露 ``app`` / ``main``，避免包导入阶段触发工具注册。"""
    if name not in {"app", "main"}:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    _app = importlib.import_module(f"{__name__}.app")
    return _app if name == "app" else _app.main
