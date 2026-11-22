"""通用日志对话框：首次打开回放缓冲，之后按信号逐行追加。

窗口本身只做组装：``Card`` + ``LogView``，复制 / 清空 / 导出沿用日志工具条。
「清空」可绑定调用方的缓冲清理，保证再次打开时不会回放已清掉的内容。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from fcmd.gui.widgets import Card, LogView, hint_label

if TYPE_CHECKING:
    from typing import Any

__all__ = ["LogChannelMixin", "LogDialog"]


class LogChannelMixin:
    """日志缓冲 + 非模态日志窗口：缓冲供首次回放，窗口打开后按信号实时追加。

    宿主自带名为 ``logLine`` 的 ``Signal(str)``（mixin 不继承 QObject，避免与宿主
    已有的 QObject 基类冲突）。需要给每行加前缀时覆盖 :meth:`format_log_line`。
    """

    logLine: Any

    def _init_log_channel(self, *, limit: int = 1000) -> None:
        self._log_lines: list[str] = []
        self._log_limit = limit
        self._log_dialog: LogDialog | None = None

    def format_log_line(self, message: str) -> str:
        """日志行格式化；默认原样返回，宿主可覆盖（如加时间戳）。"""
        return message

    def log(self, message: str) -> None:
        """记录一行：进缓冲（按 ``limit`` 截断）并广播给已打开的日志窗口。"""
        line = self.format_log_line(message)
        self._log_lines.append(line)
        del self._log_lines[: -self._log_limit]
        self.logLine.emit(line)

    def log_text(self) -> str:
        """当前缓冲（换行拼接，供日志窗口首次打开时回放）。"""
        return "\n".join(self._log_lines)

    def clear_log(self) -> None:
        """清空缓冲（日志窗口点「清空」时同步）。"""
        self._log_lines.clear()

    def show_log_dialog(self, title: str, hint: str, *, parent: QWidget | None = None) -> None:
        """首次创建并显示日志窗口，之后只置顶聚焦。"""
        if self._log_dialog is None:
            self._log_dialog = LogDialog(
                title,
                text=self.log_text(),
                append=self.logLine,
                on_clear=self.clear_log,
                hint=hint,
                parent=parent,
            )
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()

    def close_log_dialog(self) -> None:
        """主窗口关闭时一并关掉日志窗口。"""
        if self._log_dialog is not None:
            self._log_dialog.close()


class LogDialog(QDialog):  # pragma: no cover - Qt 控件
    """非模态日志窗口：``text`` 为初始内容，``append`` 为后续行信号。"""

    def __init__(  # noqa: PLR0913 - 对话框参数成组出现，显式优于配置对象
        self,
        title: str,
        *,
        text: str = "",
        append: Any = None,
        on_clear: Any = None,
        hint: str = "",
        parent: QWidget | None = None,
        size: tuple[int, int] = (920, 560),
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(False)
        self.setSizeGripEnabled(True)
        self.resize(*size)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        card = Card(title, self)
        self.log_view = LogView(height=420, parent=card)
        card.body.addWidget(self.log_view, 1)
        if hint:
            card.body.addWidget(hint_label(hint))
        layout.addWidget(card, 1)

        for line in text.splitlines():
            self.log_view.append(line)
        if append is not None:
            append.connect(self.log_view.append)
        if on_clear is not None:
            self.log_view.clear_button.clicked.connect(on_clear)

    def showEvent(self, event: QShowEvent) -> None:
        """打开时置顶并聚焦（重复点菜单只聚焦已有窗口）。"""
        super().showEvent(event)
        self.raise_()
        self.activateWindow()
