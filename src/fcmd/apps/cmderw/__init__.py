"""cmderw —— 双面板文件管理器（Tk GUI，Total Commander 风格）。

应用入口在 :mod:`fcmd.apps.cmderw.app`；本包门面只负责导入入口模块以触发
``@fcmd.tool`` 注册，并重新导出 ``main`` 供全局脚本入口使用。
"""

from __future__ import annotations

from . import app as app
from .app import main as main

__all__ = ["app", "main"]
