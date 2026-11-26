"""统一消息提示：错误 / 警告 / 信息三件套 + 确认框。

各窗口不再直接写 QMessageBox.critical(self, "导出失败", str(exc))：包装函数保持
与 Qt 静态方法一致的 (parent, title, text) 位置参数顺序，调用点只需把
QMessageBox.critical 换成 error()，其余一行不改。

集中在一处的好处：默认按钮 / 未来换自绘对话框只需改这个模块；测试里
monkeypatch.setattr(QMessageBox, "critical", ...) 依然命中（包装内部就是调类的
静态方法），不必逐个改窗口测试。
"""

from __future__ import annotations

from PySide6.QtWidgets import QMessageBox, QWidget

from fcmd.gui.widgets.buttons import confirm

__all__ = ["ask", "confirm", "error", "info", "warn"]


def error(parent: QWidget, title: str, text: str) -> None:  # pragma: no cover - Qt 控件
    """错误提示（QMessageBox.critical）。"""
    QMessageBox.critical(parent, title, str(text))


def warn(parent: QWidget, title: str, text: str) -> None:  # pragma: no cover - Qt 控件
    """警告提示（QMessageBox.warning）。"""
    QMessageBox.warning(parent, title, str(text))


def info(parent: QWidget, title: str, text: str) -> None:  # pragma: no cover - Qt 控件
    """信息提示（QMessageBox.information）。"""
    QMessageBox.information(parent, title, str(text))


def ask(parent: QWidget, title: str, text: str) -> bool:  # pragma: no cover - Qt 控件
    """是 / 否确认（与 confirm 等价，语义化的别名）。"""
    return confirm(parent, title, str(text))
