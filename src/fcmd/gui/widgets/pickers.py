"""文件 / 目录选择器：把 QFileDialog.get* 收敛成「返回值不是元组」的函数。

Qt 的 getOpenFileName / getSaveFileName 返回 (path, filter)，各窗口都写
chosen, _ = QFileDialog.getOpenFileName(...)。这里统一封装：取消返回空串（或空
列表），调用方只关心路径本身；过滤器固定补一条「所有文件」兜底。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import QFileDialog

if TYPE_CHECKING:
    from collections.abc import Sequence

    from PySide6.QtWidgets import QWidget

__all__ = ["pick_dir", "pick_file", "pick_files", "save_file"]


def _filters(pattern: str) -> str:
    """补上「所有文件」兜底项；已带兜底或空 pattern 时按原样返回。"""
    if not pattern:
        return "所有文件 (*.*)"
    return pattern if "所有文件" in pattern else f"{pattern};;所有文件 (*.*)"


def pick_dir(parent: QWidget, title: str = "选择目录", start: str = "") -> str:  # pragma: no cover - Qt 控件
    """选择已存在的目录；取消返回空串。"""
    return QFileDialog.getExistingDirectory(parent, title, start)


def pick_file(
    parent: QWidget,
    title: str = "选择文件",
    start: str = "",
    pattern: str = "",
) -> str:  # pragma: no cover - Qt 控件
    """选择单个文件；取消返回空串。"""
    chosen, _ = QFileDialog.getOpenFileName(parent, title, start, _filters(pattern))
    return chosen


def pick_files(
    parent: QWidget,
    title: str = "选择文件",
    start: str = "",
    pattern: str = "",
) -> Sequence[str]:  # pragma: no cover - Qt 控件
    """选择多个文件；取消返回空列表。"""
    chosen, _ = QFileDialog.getOpenFileNames(parent, title, start, _filters(pattern))
    return chosen


def save_file(
    parent: QWidget,
    title: str = "保存为",
    start: str = "",
    pattern: str = "",
) -> str:  # pragma: no cover - Qt 控件
    """选择保存路径；取消返回空串。"""
    chosen, _ = QFileDialog.getSaveFileName(parent, title, start, _filters(pattern))
    return chosen
