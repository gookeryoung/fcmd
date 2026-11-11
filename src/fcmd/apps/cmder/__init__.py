"""cmder —— 双面板文件管理器（Tk GUI，Total Commander 风格）。

应用包目录名为 ``cmder``，对外命令仍为 ``cmderw``（GUI 应用）；由
``__tool_name__`` 显式声明，避免工具名随目录名变化。应用入口在
:mod:`fcmd.apps.cmder.app`；本包门面只负责导入入口模块以触发
``@fcmd.tool`` 注册，并重新导出 ``main`` 供全局脚本入口使用。
"""

from __future__ import annotations

from . import app as app
from .app import main as main

# 目录名与用户命令解耦：发现时以该名字注册（见 cli/_discovery._scan_root）
__tool_name__ = "cmderw"
__all__ = ["app", "main"]
