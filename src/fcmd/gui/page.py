"""Qt 应用页面骨架：页面配置、状态通道与页面容器。

各应用的页面（llmw 概览 / 模型 / 运行 / 对话 / 设置、wslmanw 的发行版 / 镜像 /
设置）此前都从 ``QWidget`` 直接派生，各自重复「建根布局 + 保存 state + 拼状态栏
委托」这几行；页面 key 也分散在窗口的元组与页面里的字符串字面量中。这里收成两层：

- :class:`PageBody`：透明页面容器 + 统一根布局（``body``），只负责布局骨架；
- :class:`AppPage`：在 :class:`PageBody` 上补 ``state`` / :attr:`AppPage.PAGE_KEY` /
  :meth:`AppPage.status` / :meth:`AppPage.header_widgets`，页面只写业务渲染。

:class:`PageSpec` 把窗口登记页面所需的元信息（key / 标题 / 提示 / 图标）收成一个
配置对象，窗口循环登记时不再手写四元组解包。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QVBoxLayout, QWidget

if TYPE_CHECKING:
    from collections.abc import Iterator

    from fcmd.gui.widgets import Card

__all__ = ["AppPage", "PageBody", "PageSpec"]


@dataclass(frozen=True, slots=True)
class PageSpec:
    """窗口页面登记项：侧边栏 key / 标题 / 提示 / 图标。"""

    key: str
    label: str
    hint: str = ""
    icon: str | None = None

    def __iter__(self) -> Iterator[object]:
        """兼容旧的 ``for key, label, hint, icon in PAGES`` 写法。"""
        yield self.key
        yield self.label
        yield self.hint
        yield self.icon


class PageBody(QWidget):
    """页面容器：统一根布局 ``body``，页面只往里加控件。

    ``margins`` / ``spacing`` 可按页覆盖（对话页用 0 边距贴边、概览页底部留 8px），
    避免每个页面再抄一遍 ``QVBoxLayout(self)`` + ``setContentsMargins``。
    """

    PAGE_KEY = ""
    """页面在窗口里的 key；由 :class:`AppPage` 子类覆盖。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
        spacing: int = 8,
        object_name: str = "PageBody",
    ) -> None:
        super().__init__(parent)
        if object_name:
            self.setObjectName(object_name)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)

    @property
    def page_key(self) -> str:
        """当前页面的 key（子类覆盖 :attr:`PAGE_KEY`）。"""
        return self.PAGE_KEY

    def header_widgets(self) -> tuple[QWidget, ...]:
        """页面提供的页标题行动作（默认无）；窗口在构建时收集。"""
        return ()


class AppPage(PageBody):
    """应用页面基类：页面 key + 共享状态 + 状态栏 / 卡片辅助。

    ``state`` 是应用自己的跨页状态对象（llmw 的 :class:`LlmState`、wslmanw 的窗口
    状态等）。页面只依赖它的 ``status`` / ``page_status`` 两个方法，不做 isinstance
    判断：状态对象换成别的实现（测试桩、其它应用）仍可复用。
    """

    PAGE_KEY = ""

    def __init__(
        self,
        state: Any = None,
        parent: QWidget | None = None,
        *,
        margins: tuple[int, int, int, int] = (0, 0, 0, 0),
        spacing: int = 8,
        object_name: str = "PageBody",
    ) -> None:
        super().__init__(parent, margins=margins, spacing=spacing, object_name=object_name)
        self.state = state

    # ------------------------------------------------------------------ #
    # 状态栏
    # ------------------------------------------------------------------ #
    def status(self, text: str) -> None:
        """写「本页」的状态栏文字；页面不在前台时先记下，切回时再显示。"""
        if self.state is None:
            return
        page_status = getattr(self.state, "page_status", None)
        if callable(page_status) and self.page_key:
            page_status(self.page_key, text)
            return
        status = getattr(self.state, "status", None)
        if callable(status):
            status(text)

    def current_status(self) -> str:
        """本页已记录的状态栏文字（测试与页面联动用；无记录时为空串）。"""
        if self.state is None:
            return ""
        statuses = getattr(self.state, "page_statuses", None)
        if isinstance(statuses, dict):
            return str(statuses.get(self.page_key, ""))
        return ""

    # ------------------------------------------------------------------ #
    # 常用容器
    # ------------------------------------------------------------------ #
    def card(self, title: str = "") -> Card:
        """新建一张卡片并追加到页面根布局，返回卡片供继续填内容。"""
        from fcmd.gui.widgets import Card

        widget = Card(title, self)
        self.body.addWidget(widget)
        return widget
