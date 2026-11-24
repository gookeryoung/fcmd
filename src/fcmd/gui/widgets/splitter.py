"""分栏容器：创建不可折叠分栏并读写尺寸（尺寸持久化用）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QSplitter, QWidget

from fcmd.gui.layout import parse_sizes, serialize_sizes

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["QuietSplitter", "apply_splitter_sizes", "make_splitter", "splitter_sizes"]

QUIET_HANDLE_WIDTH = 4
"""轻量分栏手柄宽度（像素）；与 llmw 对话 / 工作流页原来的 ChatSplitter 一致。"""


class QuietSplitter(QSplitter):  # pragma: no cover - Qt 控件
    """透明手柄分栏：常态不画线，hover / 拖动时才出现强调色。

    llmw 的 ``ChatSplitter`` 原本单独实现这套口径；抽成公共类后，帮助窗口与
    其它使用 :func:`make_splitter` 的页面共享同一手柄宽度与交互反馈。两侧卡片
    仍保留自己的边框，因此手柄透明不会让分栏边界消失。
    """

    def __init__(
        self,
        orientation: Qt.Orientation = Qt.Orientation.Horizontal,
        parent: QWidget | None = None,
        *,
        handle_width: int = QUIET_HANDLE_WIDTH,
    ) -> None:
        super().__init__(orientation, parent)
        self.setObjectName("QuietSplitter")
        self.setChildrenCollapsible(False)
        self.setHandleWidth(max(1, handle_width))


def make_splitter(
    orientation: Qt.Orientation = Qt.Orientation.Horizontal,
    *,
    sizes: Sequence[int] = (),
    parent: QWidget | None = None,
    handle_width: int = QUIET_HANDLE_WIDTH,
    quiet: bool = True,
) -> QSplitter:  # pragma: no cover - Qt 控件
    """创建不可折叠的分栏容器（可拖动，尺寸可持久化）。

    Parameters
    ----------
    orientation:
        水平 / 垂直分栏。
    sizes:
        初始尺寸；省略时由布局按内容分配。
    parent:
        父控件。
    handle_width:
        手柄宽度（像素），默认 :data:`QUIET_HANDLE_WIDTH`。
    quiet:
        默认 ``True`` 返回 :class:`QuietSplitter`（透明手柄 + hover 强调色）；
        传 ``False`` 回退为普通 6px 分隔线，仅用于需要常驻线的特殊场景。
    """
    if quiet:
        splitter: QSplitter = QuietSplitter(orientation, parent, handle_width=handle_width)
    else:
        splitter = QSplitter(orientation, parent)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(max(1, handle_width))
    if sizes:
        splitter.setSizes(list(sizes))
    return splitter


def splitter_sizes(splitter: QSplitter) -> list[int]:  # pragma: no cover - Qt 控件
    """读取分栏尺寸（写入设置文件用）。"""
    return serialize_sizes(splitter.sizes())


def apply_splitter_sizes(splitter: QSplitter, sizes: object) -> bool:  # pragma: no cover - Qt 控件
    """按记忆尺寸恢复分栏；非法内容保持当前布局并返回 ``False``。"""
    parsed = parse_sizes(sizes)
    if parsed is None:
        return False
    splitter.setSizes(parsed)
    return True
