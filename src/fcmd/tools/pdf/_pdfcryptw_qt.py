"""pdfcryptw 的 Qt 主窗口与入口。

窗口只负责「选目录 / 填密码 / 起任务 / 展示进度与日志」，加解密本身仍在
:mod:`fcmd.tools.pdf.pdfcrypt`。批量处理放进 ``QThreadPool``，进度事件经
信号桥排队回 GUI 线程，界面不阻塞。

布局口径：内容放进 :class:`PageScroll`（窗口变矮时滚动而不是压扁控件），
目录用 :class:`PathRow`，并在下方用 :class:`ElidedLabel` 显示解析后的完整
路径（中间省略 + tooltip）；按钮与进度条固定为整行宽度，日志占满剩余空间。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSpinBox,
)

from fcmd import __version__
from fcmd.gui.app import run_window
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.widgets import Card, ElidedLabel, FormGrid, PathRow, ProgressRow, button, hint_label
from fcmd.gui.workers import EventBridge, submit
from fcmd.tools.pdf.pdfcrypt import MIN_PASSWORD_LENGTH, collect_pdf_files, process_files, validate_password
from fcmd.tools.pdf.pdfcryptw import (
    MAX_WORKERS,
    MIN_HEIGHT,
    MIN_WIDTH,
    MIN_WORKERS,
    PdfCryptSettings,
    load_settings,
    parse_workers,
    save_settings,
)

__all__ = ["PdfCryptWindow", "run_qt"]

DEFAULT_SIZE = (860, 680)


def _run_worker(  # noqa: PLR0913, PLR0917 - 任务参数逐项显式传递，便于便于对照既有行为
    directory: Path,
    password: str,
    workers: int,
    decrypt: bool,
    recursive: bool,
    bridge: EventBridge,
) -> None:
    """筛选待处理 PDF 并批量加解密，把进度事件逐条投回界面。"""
    action = "解密" if decrypt else "加密"
    try:
        files = collect_pdf_files(directory, encrypted=decrypt, recursive=recursive)
        if not files:
            kind = "已加密" if decrypt else "未加密"
            bridge.event.emit(("empty", f"目录中未找到{kind}的 PDF 文件：{directory}"))
            return
        bridge.event.emit(("message", f"找到 {len(files)} 个待{action}文件"))

        def progress(done: int, total: int, source: Path, result: Any) -> None:
            bridge.event.emit(("progress", done, total, source.name, result.ok, result.error))

        report = process_files(files, password, decrypt=decrypt, workers=workers, progress=progress)
    except Exception as exc:  # 后台线程需统一回传给界面
        bridge.event.emit(("error", str(exc)))
        return
    bridge.event.emit(("done", action, len(report.succeeded), len(report.failed)))


class PdfCryptWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """PDF 加密解密主窗口。"""

    def __init__(self) -> None:
        self._settings = load_settings()
        self._busy = False
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"PDF 加密解密 v{__version__}",
            config_name="pdfcryptw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
            scroll=True,
        )
        self._build()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """目录 / 选项 / 操作行 / 进度 / 日志，自上而下堆叠。"""
        directory_card = Card("目录", self)
        self._directory = PathRow(
            value=self._settings.directory or str(Path.cwd()),
            placeholder="选择待处理的 PDF 目录",
            parent=directory_card,
        )
        self._directory.changed.connect(self._refresh_directory_hint)
        directory_card.body.addWidget(self._directory)
        self._directory_hint = ElidedLabel("", parent=directory_card)
        self._directory_hint.setObjectName("Mono")
        directory_card.body.addWidget(self._directory_hint)
        self.add(directory_card)

        options_card = Card("选项", self)
        grid = FormGrid(options_card)
        self._password = QLineEdit(options_card)
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        self._password.setPlaceholderText("加密 / 解密密码（不保存）")
        grid.add_row("密码", self._password)
        self._workers = QSpinBox(options_card)
        self._workers.setRange(MIN_WORKERS, MAX_WORKERS)
        self._workers.setValue(self._settings.workers)
        self._recursive = QCheckBox("递归子目录", options_card)
        self._recursive.setChecked(self._settings.recursive)
        grid.add_pair("线程", self._workers, "", self._recursive)
        options_card.body.addWidget(grid)
        self.add(options_card)

        self.add(hint_label(f"密码至少 {MIN_PASSWORD_LENGTH} 字符更安全；加密输出 .enc.pdf，解密输出 .dec.pdf"))

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(8)
        actions.addStretch(1)
        self._decrypt_button = button("解密")
        self._decrypt_button.clicked.connect(lambda: self._start(decrypt=True))
        actions.addWidget(self._decrypt_button)
        self._encrypt_button = button("加密", kind="primary")
        self._encrypt_button.clicked.connect(lambda: self._start(decrypt=False))
        actions.addWidget(self._encrypt_button)
        self.add_layout(actions)

        self._progress_row = ProgressRow(parent=self)
        self._progress = self._progress_row.bar
        self.add(self._progress_row)

        self._log = self.add_log(height=180)
        self._log.append("选择目录、输入密码后点击「加密」或「解密」。")
        self._refresh_directory_hint(self._directory.value())

    def _refresh_directory_hint(self, text: str) -> None:
        """把当前目录同步到省略标签（空输入时提示占位）。"""
        self._directory_hint.setText(str(Path(text).expanduser()) if text.strip() else "当前目录：未选择")

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _set_busy(self, busy: bool) -> None:
        """处理期间禁用输入，避免重复提交。"""
        self._busy = busy
        for widget in (
            self._encrypt_button,
            self._decrypt_button,
            self._directory,
            self._password,
            self._workers,
            self._recursive,
        ):
            widget.setEnabled(not busy)

    # ------------------------------------------------------------------ #
    # 处理
    # ------------------------------------------------------------------ #
    def _start(self, *, decrypt: bool) -> None:
        """校验输入后在后台启动加解密任务。"""
        if self._busy:
            return

        directory = Path(self._directory.value() or ".").expanduser()
        try:
            password = validate_password(self._password.text())
            workers = parse_workers(self._workers.value())
        except ValueError as exc:
            self._log.append(f"错误：{exc}")
            return
        if len(password) < MIN_PASSWORD_LENGTH:
            self._log.append(f"警告：密码长度小于 {MIN_PASSWORD_LENGTH} 字符，建议使用更强的密码")

        action = "解密" if decrypt else "加密"
        self._set_busy(True)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self.set_status(f"正在{action}…")
        self._log.append(f"{action}目录：{directory}；线程：{workers}")

        bridge = EventBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(_run_worker, directory, password, workers, decrypt, self._recursive.isChecked(), bridge)

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """在主线程消费后台事件，更新进度条、日志与状态。"""
        kind = event[0]
        if kind == "message":
            self._log.append(event[1])
        elif kind == "progress":
            _, done, total, name, ok, error = event
            self._progress.setRange(0, max(total, 1))
            self._progress.setValue(done)
            if ok:
                self._log.append(f"[{done}/{total}] 完成：{name}")
            else:
                self._log.append(f"[{done}/{total}] 失败：{name}（{error}）")
        elif kind == "empty":
            self._log.append(event[1])
            self.set_status("没有待处理文件")
            self._finish()
            QMessageBox.information(self, "提示", event[1])
        elif kind == "done":
            _, action, succeeded, failed = event
            self._progress.setValue(self._progress.maximum())
            self.set_status(f"{action}完成：成功 {succeeded}，失败 {failed}")
            self._log.append(f"{action}汇总：成功 {succeeded} 个，失败 {failed} 个")
            self._finish()
            QMessageBox.information(self, f"{action}完成", f"成功 {succeeded} 个，失败 {failed} 个")
        elif kind == "error":
            self._log.append(f"错误：{event[1]}")
            self.set_status("处理失败")
            self._finish()
            QMessageBox.critical(self, "处理失败", event[1])

    def _finish(self) -> None:
        """任务结束：恢复输入并释放信号桥。"""
        self._set_busy(False)
        self._bridge = None

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时写回目录 / 线程 / 递归与窗口 geometry（不含密码）。"""
        save_settings(
            PdfCryptSettings(
                directory=self._directory.value(),
                workers=self._workers.value(),
                recursive=self._recursive.isChecked(),
                geometry=geometry,
            )
        )


def run_qt() -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(PdfCryptWindow, app_name="pdfcryptw")
