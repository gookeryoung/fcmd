"""对话框骨架：统一边距、表单行、按钮行与收尾尺寸（ADR 0038 D2 边界之内）。

只做**布局与按钮接线**，不接管校验、不接管任务执行：业务校验仍写在对话框自己的
``_start`` / ``_apply`` 里，失败时调 :func:`fcmd.gui.widgets.messages.error` 后
``return``；成功仍各自 ``accept()``。这样 ADR 0038 D2「不做通用 TaskDialog」的
边界不被越过——这里没有任何任务生命周期概念。

典型用法::

    class _CopyDialog(FormDialog):
        def __init__(self, owner):
            super().__init__(owner, title="复制实例", min_width=620)
            self.add_hint("源实例：…")
            self.name = self.add_row("新实例名称", QLineEdit(...))
            self.finish(ok_text="开始复制", on_ok=self._start)
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget

from fcmd.gui.widgets.buttons import Button, button
from fcmd.gui.widgets.cards import fit_dialog_to_content
from fcmd.gui.widgets.forms import FormGrid
from fcmd.gui.widgets.text import ElidedLabel, hint_label

if TYPE_CHECKING:
    pass

__all__ = ["DialogShell", "FormDialog", "action_row", "dialog_footer"]

_MARGINS = (12, 12, 12, 12)


def action_row(*widgets: QWidget, spacing: int = 6) -> QHBoxLayout:  # pragma: no cover - Qt 控件
    """动作行：零边距横排，按给定顺序放控件（调用方自行 addStretch）。"""
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for widget in widgets:
        row.addWidget(widget)
    return row


def dialog_footer(  # noqa: PLR0913
    *,
    cancel_text: str = "取消",
    ok_text: str = "",
    ok_kind: str = "primary",
    on_cancel: Callable[[], object] | None = None,
    on_ok: Callable[[], object] | None = None,
    extra: Iterable[QWidget] = (),
) -> tuple[QHBoxLayout, Button, Button | None]:
    """取消靠左、确认靠右的按钮行（对话框最常见的收尾形态）。

    ``extra`` 里的控件排在「取消」之后、stretch 之前（次要操作）。
    ``ok_text`` 为空时不建确认按钮，返回的第三个值为 ``None``。
    """
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    cancel = button(cancel_text)
    cancel.clicked.connect(on_cancel if on_cancel is not None else _noop)
    row.addWidget(cancel)
    for widget in extra:
        row.addWidget(widget)
    row.addStretch(1)
    if not ok_text:
        return row, cancel, None
    ok = button(ok_text, kind=ok_kind)
    ok.clicked.connect(on_ok if on_ok is not None else _noop)
    row.addWidget(ok)
    return row, cancel, ok


def _noop() -> None:
    """占位槽：调用方未给回调时按钮不做事（而不是让 clicked 连到 None）。"""


class DialogShell(QDialog):  # pragma: no cover - Qt 控件
    """对话框外壳：统一边距 / 间距 / 内容容器与按钮行。

    子类在 ``__init__`` 里调 ``super().__init__(parent, title=..., min_width=...)``，
    然后往 :attr:`body`（或其上的 ``add`` / ``add_layout`` / ``add_hint``）追加内容，
    最后 ``finish(...)`` 收尾。
    """

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = "",
        min_width: int | None = None,
        margins: tuple[int, int, int, int] = _MARGINS,
        spacing: int = 8,
    ) -> None:
        super().__init__(parent)
        if title:
            self.setWindowTitle(title)
        if min_width:
            self.setMinimumWidth(min_width)
        self._min_width = min_width
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(*margins)
        self.body.setSpacing(spacing)
        self._footer_added = False

    # ------------------------------------------------------------------ #
    # 内容
    # ------------------------------------------------------------------ #
    def add(self, widget: QWidget) -> QWidget:
        """把控件追加到内容区，并原样返回以便链式赋值。"""
        self.body.addWidget(widget)
        return widget

    def add_layout(self, layout: object) -> object:
        """把布局（QGridLayout / QHBoxLayout …）追加到内容区。"""
        self.body.addLayout(layout)
        return layout

    def add_hint(self, text: str) -> ElidedLabel:
        """追加一段次要色说明文字（自动省略过长的一行）。"""
        return self.add(hint_label(text))

    def add_stretch(self, weight: int = 1) -> None:
        """内容区加伸缩项（把后续内容压到底部）。"""
        self.body.addStretch(weight)

    # ------------------------------------------------------------------ #
    # 收尾
    # ------------------------------------------------------------------ #
    def finish(  # noqa: PLR0913
        self,
        *,
        cancel_text: str = "取消",
        ok_text: str = "",
        ok_kind: str = "primary",
        on_cancel: Callable[[], object] | None = None,
        on_ok: Callable[[], object] | None = None,
        extra: Iterable[QWidget] = (),
        fit: bool = True,
    ) -> tuple[Button, Button | None]:
        """追加按钮行并按内容自适应尺寸；返回 ``(取消按钮, 确认按钮或 None)``。

        ``fit=False`` 用于表格 / 日志为主、确实要用户自己拖高的对话框。
        """
        row, cancel, ok = dialog_footer(
            cancel_text=cancel_text,
            ok_text=ok_text,
            ok_kind=ok_kind,
            on_cancel=on_cancel if on_cancel is not None else self.reject,
            on_ok=on_ok,
            extra=extra,
        )
        self.body.addLayout(row)
        self._footer_added = True
        if fit:
            fit_dialog_to_content(self, min_width=self._min_width)
        return cancel, ok


class FormDialog(DialogShell):  # pragma: no cover - Qt 控件
    """带 :class:`~fcmd.gui.widgets.forms.FormGrid` 的对话框外壳。

    表单网格在构造时就装进内容区（位置在 ``add_hint`` 之后可继续用 ``add`` 追加）。
    """

    def __init__(self, parent: QWidget | None = None, **kwargs: object) -> None:
        super().__init__(parent, **kwargs)  # type: ignore[arg-type]
        self.form = FormGrid(self)
        self.body.addWidget(self.form)

    def add_row(self, label: str, widget: QWidget, action: QWidget | None = None) -> QWidget:
        """在表单网格里加一行（可带行内动作按钮），并返回该控件。"""
        self.form.add_row(label, widget, action)
        return widget

    def add_pair(self, label: str, left: QWidget, right_label: str = "", right: QWidget | None = None) -> None:
        """在表单网格里加一行双控件（如输入框 + 浏览按钮）。"""
        self.form.add_pair(label, left, right_label, right)
