"""通用日志对话框：首次打开回放缓冲，之后按信号逐行追加。

窗口本身只做组装：``Card`` + ``LogView``，复制 / 清空 / 导出沿用日志工具条。
「清空」可绑定调用方的缓冲清理，保证再次打开时不会回放已清掉的内容。

:class:`LogChannelMixin` 是所有 GUI 窗口的日志公共组件：窗口骨架
（``QtMainWindow`` / ``QtToolWindow``）自动继承它并把「任务日志…」挂到
「帮助」菜单的「关于」之前；应用只调用 :meth:`LogChannelMixin.log` 写日志，
不再各自往页面里塞 ``LogView`` / 日志卡片 / 日志页签。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtGui import QAction, QShowEvent
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from fcmd.gui.widgets import Card, LogView, hint_label

if TYPE_CHECKING:
    from typing import Any

__all__ = ["LogChannelMixin", "LogDialog"]


class LogChannelMixin:
    """日志缓冲 + 非模态日志窗口，并把入口挂到「帮助」菜单。

    宿主自带名为 ``logLine`` 的 ``Signal(str)``（窗口骨架已声明；非 QWidget 宿主
    需自行声明）。需要给每行加前缀时覆盖 :meth:`format_log_line`。

    日志入口通过 :meth:`enable_log_menu` 统一插入「帮助 → … → 关于」，标题 /
    提示 / 菜单文字由类属性或 :meth:`configure_log` 指定。所有窗口一律走这条
    通道，页面里不再单独放日志控件。
    """

    logLine: Any

    log_action_text = "任务日志…"
    """帮助菜单里的动作文字。"""

    log_title = "任务日志"
    """日志窗口标题。"""

    log_hint = "汇总应用操作与后台任务；复制、清空、导出见日志工具条。"
    """日志窗口底部提示。"""

    log_limit = 1000
    """日志缓冲上限（行）；超出后丢弃最旧的，避免长时间运行吃内存。"""

    def _init_log_channel(self, *, limit: int | None = None) -> None:
        self._log_lines: list[str] = []
        self._log_limit = self.log_limit if limit is None else limit
        self._log_dialog: LogDialog | None = None
        self.log_action: QAction | None = None

    def configure_log(
        self,
        *,
        title: str | None = None,
        hint: str | None = None,
        action_text: str | None = None,
        limit: int | None = None,
    ) -> None:
        """覆盖日志窗口标题 / 提示 / 菜单文字 / 缓冲上限（须在启用菜单前调用）。"""
        if title is not None:
            self.log_title = title
        if hint is not None:
            self.log_hint = hint
        if action_text is not None:
            self.log_action_text = action_text
        if limit is not None:
            self.log_limit = limit
            self._log_limit = limit

    def enable_log_menu(
        self,
        *,
        action_text: str | None = None,
        title: str | None = None,
        hint: str | None = None,
        before: QAction | None = None,
        shortcut: str = "Ctrl+Shift+L",
    ) -> QAction:
        """把日志入口插到「帮助」菜单（默认「关于」之前），重复调用直接复用。

        ``before`` 省略时依次尝试窗口骨架暴露的 ``about_action``，再退化为追加。
        """
        self.configure_log(title=title, hint=hint, action_text=action_text)
        if self.log_action is not None:
            return self.log_action
        help_menu = getattr(self, "help_menu", None)
        if help_menu is None:
            menu_bar = getattr(self, "menuBar", None)
            if callable(menu_bar):
                for action in menu_bar().actions():
                    if action.text() == "帮助":
                        help_menu = action.menu()
                        break
        action = QAction(self.log_action_text, self)
        action.setShortcut(shortcut)
        action.triggered.connect(self.show_log_dialog)
        anchor = before if before is not None else getattr(self, "about_action", None)
        if help_menu is not None:
            if anchor is not None:
                help_menu.insertAction(anchor, action)
                help_menu.insertSeparator(anchor)
            else:
                help_menu.addAction(action)
        elif callable(getattr(self, "addAction", None)):
            self.addAction(action)
        self.log_action = action
        return action

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

    def show_log_dialog(
        self,
        title: str | None = None,
        hint: str | None = None,
        *,
        parent: QWidget | None = None,
    ) -> None:
        """首次创建并显示日志窗口，之后只置顶聚焦。"""
        if self._log_dialog is None:
            if parent is None and isinstance(self, QWidget):
                parent = self
            self._log_dialog = LogDialog(
                title or self.log_title,
                text=self.log_text(),
                append=self.logLine,
                on_clear=self.clear_log,
                hint=hint if hint is not None else self.log_hint,
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
