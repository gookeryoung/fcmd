"""输入法（IME）组合期间的占位文字保护。

``QLineEdit`` / ``QPlainTextEdit`` 在输入法组合串（preedit）显示时仍会绘制占位文字：
preedit 不在文档里，Qt 判定「文本为空」就继续画 placeholder，于是组合串与占位文字
叠在一起，看起来像重影。这里用一个**应用级事件过滤器**在组合期间临时清空
placeholder，组合结束或失焦后还原；对所有窗口与输入框生效，新增输入框不必各自处理。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject

if TYPE_CHECKING:
    from PySide6.QtWidgets import QPlainTextEdit

    from fcmd.gui.qt.app import QApplication

__all__ = ["PlaceholderImeGuard", "install_placeholder_ime_guard"]

_STASH_PROPERTY = "fcmdImePlaceholder"
"""组合期间暂存的原占位文字（挂在输入框动态属性上，随控件销毁）。"""

_GUARDED = frozenset({QEvent.Type.InputMethod, QEvent.Type.FocusOut, QEvent.Type.Hide})
"""只关心这三类事件：其余事件（鼠标 / 绘制 / 定时器）在应用级过滤器里必须快速放过。"""


def _input_types() -> tuple[type, ...]:
    """带占位文字、需要保护的多行 / 单行输入框类型（延迟导入，导入本模块不加载 Qt 控件）。"""
    from PySide6.QtWidgets import QLineEdit, QPlainTextEdit

    return (QLineEdit, QPlainTextEdit)


class PlaceholderImeGuard(QObject):
    """应用级事件过滤器：输入法组合期间隐藏占位文字，避免与组合串重影。"""

    def eventFilter(self, obj: Any, event: QEvent) -> bool:
        # 应用级过滤器会看到全部事件：先用事件类型挡掉 99% 的流量，再做类型判断。
        kind = event.type()
        if kind not in _GUARDED:
            return False
        if isinstance(obj, _input_types()):
            if kind == QEvent.Type.InputMethod:
                self._on_input_method(obj, event)
            else:  # FocusOut / Hide
                self._restore(obj)
        return False

    @staticmethod
    def _on_input_method(widget: QPlainTextEdit, event: Any) -> None:
        if event.preeditString():
            placeholder = widget.placeholderText()
            if placeholder:
                widget.setProperty(_STASH_PROPERTY, placeholder)
                widget.setPlaceholderText("")
            return
        PlaceholderImeGuard._restore(widget)

    @staticmethod
    def _restore(widget: QPlainTextEdit) -> None:
        stashed = widget.property(_STASH_PROPERTY)
        if not isinstance(stashed, str) or not stashed:
            return
        widget.setProperty(_STASH_PROPERTY, None)
        widget.setPlaceholderText(stashed)


def install_placeholder_ime_guard(app: QApplication) -> PlaceholderImeGuard:
    """给应用装上占位文字保护；已装过时返回同一个实例（幂等）。"""
    existing = app.property("fcmdPlaceholderImeGuard")
    if isinstance(existing, PlaceholderImeGuard):
        return existing
    guard = PlaceholderImeGuard(app)
    app.installEventFilter(guard)
    app.setProperty("fcmdPlaceholderImeGuard", guard)
    return guard
