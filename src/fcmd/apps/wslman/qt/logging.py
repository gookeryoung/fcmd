"""wslman 窗口方法（ADR 0046 P2/P3 机械迁移）。"""

from __future__ import annotations


class LoggingMixin:
    """ADR 0046 P2/P3 迁移的窗口方法。"""

    def show_log(self) -> None:
        """打开（或聚焦）任务日志窗口；历史来自 ``_log_lines`` 缓冲。"""
        self.show_log_dialog()
