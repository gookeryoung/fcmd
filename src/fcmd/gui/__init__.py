"""Tk 展示基座：窗口骨架、主题与 UI 设置持久化。

本包只依赖标准库 tkinter，不属于任何业务域，也不在 :mod:`fcmd.tools` 的工具发现
命名空间内。各工具的窗口仍与业务同域（``tools/<域>/<工具>w.py``），复用这里的
:mod:`fcmd.gui.window` / :mod:`fcmd.gui.theme` / :mod:`fcmd.gui.config` /
:mod:`fcmd.gui.sidebar`。
"""

from __future__ import annotations

__all__: list[str] = []
