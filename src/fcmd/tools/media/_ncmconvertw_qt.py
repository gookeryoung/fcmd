"""ncmconvertw 的 Qt 主窗口与入口。

界面分四段：输出设置（目录 + 浏览 + 提示）、待转换文件（工具栏 + 文件列表）、
转换动作行（开始转换 + 进度条）、日志。转换在后台线程逐文件执行，进度、成功
与失败明细经信号桥回投 GUI 线程，与旧版的队列事件语义一致。

布局口径：输出目录行整行省略并保留完整 tooltip；文件列表横向省略中间段
（保留文件名与目录尾），悬停看全路径；日志吸收剩余高度，窗口变矮时优先压缩
日志区；进度条不显示百分比文本，由状态栏给出 ``n/total``。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QWidget,
)

from fcmd import __version__
from fcmd.gui import config as _guiconfig
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, PathField, button, confirm
from fcmd.gui.qt.workers import EventBridge, submit
from fcmd.tools.media.ncmconvertw import (
    MIN_HEIGHT,
    MIN_WIDTH,
    NcmConvertSettings,
    NcmError,
    collect_ncm_files,
    decrypt_ncm,
    load_settings,
    save_settings,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["NcmConvertWindow", "run_qt"]

DEFAULT_SIZE = (860, 640)

_OUTPUT_HINT = "留空时解锁到每个 NCM 文件所在目录，文件名为 <原名>.<格式>"


class _OutputRow(PathField):  # pragma: no cover - Qt 控件
    """输出目录行：输入框 + 浏览按钮 + 留空说明。"""

    def __init__(self, *, value: str = "", parent: QWidget | None = None) -> None:
        super().__init__(
            value=value,
            placeholder="留空时输出到各 NCM 文件所在目录",
            title="选择输出目录",
            hint_object_name="Hint",
            parent=parent,
        )

    def _refresh_hint(self, text: str) -> None:
        if text.strip():
            shown = str(Path(text).expanduser())
            self.hint.setText(shown)
            self.hint.setToolTip(shown)
        else:
            self.hint.setText(_OUTPUT_HINT)
            self.hint.setToolTip("")

    def _pick(self) -> None:
        """选择输出目录并写回输入框。"""
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.value())
        if directory:
            self.edit.setText(directory)


def _work_convert(
    files: list[Path],
    output_dir: Path | None,
    cancel: threading.Event,
    bridge: EventBridge,
) -> None:
    """线程池里逐文件解锁，进度与结果逐条投回界面。"""
    total = len(files)
    succeeded = 0
    failed = 0
    for index, path in enumerate(files, 1):
        if cancel.is_set():
            bridge.event.emit(("cancelled", index - 1, total))
            break
        try:
            target = decrypt_ncm(path, output_dir)
        except (NcmError, OSError) as exc:
            failed += 1
            bridge.event.emit(("error", path.name, str(exc)))
        else:
            succeeded += 1
            bridge.event.emit(("log", f"[{index}/{total}] {path.name} → {target}"))
        bridge.event.emit(("progress", index, total))
    bridge.event.emit(("done", succeeded, failed, total))


class NcmConvertWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """NCM 解锁转换器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._settings_override = settings_path
        self._files: list[Path] = []
        self._last_dir = self._settings.last_input_dir
        self._running = False
        self._cancelled = False
        self._cancel = threading.Event()
        self._bridge: EventBridge | None = None
        super().__init__(
            title=f"NCM 转换器 v{__version__}",
            config_name="ncmconvertw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._confirm_close,
            persist=self._persist,
        )
        if settings_path is not None:
            self._settings_path = settings_path
            self._raw_settings = _guiconfig.read_settings(settings_path) or {}
        self._build()
        self._log.append_timestamped("添加 .ncm 文件或导入目录后点击「开始转换」。")

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """输出设置 / 文件列表 / 动作行 / 日志 / 页脚。"""
        self.add(self._build_output_card())
        self.add(self._build_files_card(), 3)
        self.add(self._build_action_row())
        self.add(self._build_log_card(), 2)
        self.add(self._build_footer())

    def _build_output_card(self) -> Card:
        card = Card("输出设置", self)
        self._output_row = _OutputRow(value=self._settings.output_dir, parent=card)
        card.body.addWidget(self._output_row)
        return card

    def _build_files_card(self) -> Card:
        """工具栏 + 多选文件列表（中间省略，悬停看全路径）。"""
        card = Card("待转换的 NCM 文件", self)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(8)
        self._add_files_button = button("添加文件…")
        self._add_files_button.clicked.connect(self._add_files)
        self._import_dir_button = button("导入目录…")
        self._import_dir_button.clicked.connect(self._import_dir)
        self._remove_button = button("移除选中")
        self._remove_button.clicked.connect(self._remove_selected)
        self._clear_button = button("清空", kind="danger")
        self._clear_button.clicked.connect(self._clear_files)
        toolbar.addWidget(self._add_files_button)
        toolbar.addWidget(self._import_dir_button)
        toolbar.addWidget(self._remove_button)
        toolbar.addStretch(1)
        toolbar.addWidget(self._clear_button)

        self._list = QListWidget(card)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._list.setAlternatingRowColors(True)
        card.body.addLayout(toolbar)
        card.body.addWidget(self._list, 1)
        return card

    def _build_action_row(self) -> QWidget:
        """开始转换 + 进度条（百分比由状态栏给出）。"""
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self._convert_button = button("开始转换", kind="primary")
        self._convert_button.clicked.connect(self._start_conversion)
        self._progress = QProgressBar(row)
        self._progress.setRange(0, 1)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)
        layout.addWidget(self._convert_button)
        layout.addWidget(self._progress, 1)
        return row

    def _build_log_card(self) -> Card:
        card = Card("日志", self)
        self._log = card.add_log(height=140, minimum_height=100)
        return card

    def _build_footer(self) -> QWidget:
        """左侧文件计数，右侧依赖提示。"""
        footer = QWidget(self)
        layout = QHBoxLayout(footer)
        layout.setContentsMargins(0, 0, 0, 0)
        self._count_label = QLabel("共 0 个文件", footer)
        self._count_label.setObjectName("Hint")
        hint = QLabel("仅支持 .ncm 文件，依赖 fcmd[crypto]", footer)
        hint.setObjectName("Hint")
        layout.addWidget(self._count_label)
        layout.addStretch(1)
        layout.addWidget(hint)
        return footer

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _add_files(self) -> None:
        """选择一个或多个 NCM 文件加入列表。"""
        filenames, _ = QFileDialog.getOpenFileNames(
            self,
            "选择 NCM 文件",
            self._last_dir,
            "NCM 文件 (*.ncm);;所有文件 (*.*)",
        )
        if not filenames:
            return
        self._last_dir = str(Path(filenames[0]).parent)
        self._add_paths(Path(name) for name in filenames)

    def _import_dir(self) -> None:
        """导入目录下（不递归）的全部 NCM 文件。"""
        directory = QFileDialog.getExistingDirectory(self, "选择包含 NCM 文件的目录", self._last_dir)
        if not directory:
            return
        self._last_dir = directory
        try:
            found = collect_ncm_files(Path(directory).expanduser())
        except NcmError as exc:
            self._log.append_timestamped(f"错误：{exc}")
            QMessageBox.critical(self, "导入失败", str(exc))
            return
        if not found:
            self._log.append_timestamped(f"目录中没有 .ncm 文件：{directory}")
            return
        self._add_paths(found)

    def _add_paths(self, paths: Iterable[Path]) -> None:
        """把合法 NCM 文件去重后加入列表并刷新界面。"""
        known = {str(path) for path in self._files}
        added = 0
        ignored = 0
        for path in paths:
            if path.suffix.lower() != ".ncm":
                ignored += 1
                continue
            text = str(path)
            if text in known:
                continue
            known.add(text)
            self._files.append(path)
            added += 1
        self._refresh_list()
        if added:
            self._log.append_timestamped(f"已添加 {added} 个文件")
        if ignored:
            self._log.append_timestamped(f"已忽略 {ignored} 个非 .ncm 文件")

    def _remove_selected(self) -> None:
        """移除列表中选中的文件。"""
        rows = sorted({self._list.row(item) for item in self._list.selectedItems()}, reverse=True)
        if not rows:
            return
        for row in rows:
            del self._files[row]
        self._refresh_list()
        self._log.append_timestamped(f"已移除 {len(rows)} 个文件")

    def _clear_files(self) -> None:
        """清空待转换列表。"""
        if not self._files:
            return
        count = len(self._files)
        self._files.clear()
        self._refresh_list()
        self._log.append_timestamped(f"已清空 {count} 个文件")

    def _refresh_list(self) -> None:
        """按 ``self._files`` 重绘列表与计数。"""
        self._list.clear()
        for path in self._files:
            item = QListWidgetItem(str(path))
            item.setToolTip(str(path))
            self._list.addItem(item)
        self._count_label.setText(f"共 {len(self._files)} 个文件")

    def _set_busy(self, busy: bool) -> None:
        """转换期间禁用输入控件，避免重复提交或改列表。"""
        self._running = busy
        for widget in (
            self._add_files_button,
            self._import_dir_button,
            self._remove_button,
            self._clear_button,
            self._output_row.browse,
            self._output_row.edit,
            self._convert_button,
        ):
            widget.setEnabled(not busy)

    # ------------------------------------------------------------------ #
    # 转换
    # ------------------------------------------------------------------ #
    def _start_conversion(self) -> None:
        """校验输出目录与文件列表，启动后台转换。"""
        if self._running:
            return
        if not self._files:
            QMessageBox.warning(self, "提醒", "没有输入的文件")
            return
        raw_output = self._output_row.value()
        output_dir = Path(raw_output).expanduser() if raw_output else None
        if output_dir is not None:
            if not output_dir.exists():
                QMessageBox.critical(self, "错误", "输出目录不存在")
                return
            if not output_dir.is_dir():
                QMessageBox.critical(self, "错误", "不是目录")
                return

        self._cancel = threading.Event()
        self._cancelled = False
        self._progress.setRange(0, max(len(self._files), 1))
        self._progress.setValue(0)
        self._set_busy(True)
        self.set_status("正在转换…")
        location = str(output_dir) if output_dir is not None else "各文件所在目录"
        self._log.append_timestamped(f"开始转换 {len(self._files)} 个文件，输出到：{location}")

        bridge = EventBridge(self)
        bridge.event.connect(self._on_event)
        self._bridge = bridge
        submit(
            _work_convert,
            list(self._files),
            output_dir,
            self._cancel,
            bridge,
            on_error=self._on_worker_error,
        )

    def _on_worker_error(self, message: str) -> None:
        """线程池兜底异常：记日志并恢复界面。"""
        self._log.append_timestamped(f"错误：{message}")
        self.set_status("转换失败")
        self._finish_busy()

    def _on_event(self, event: tuple[Any, ...]) -> None:
        """消费后台事件：进度、明细、失败与汇总。"""
        kind = event[0]
        if kind == "progress":
            _, done, total = event
            self._progress.setValue(done)
            self.set_status(f"{done}/{total}")
        elif kind == "log":
            self._log.append_timestamped(str(event[1]))
        elif kind == "error":
            _, name, message = event
            self._log.append_timestamped(f"失败：{name}：{message}")
        elif kind == "cancelled":
            _, done, total = event
            self._cancelled = True
            self._log.append_timestamped(f"已中止：完成 {done}/{total}")
        elif kind == "done":
            _, succeeded, failed, total = event
            self._finish(succeeded, failed, total)

    def _finish(self, succeeded: int, failed: int, total: int) -> None:
        """转换结束：恢复控件、更新状态并提示汇总结果。"""
        self._finish_busy()
        self._progress.setValue(total)
        if self._cancelled:
            self.set_status(f"已中止：成功 {succeeded}，失败 {failed}")
            self._log.append_timestamped(f"已中止：成功 {succeeded}，失败 {failed}，共 {total} 个")
            return
        if failed:
            self.set_status(f"完成：成功 {succeeded}，失败 {failed}")
            self._log.append_timestamped(f"转换结束：成功 {succeeded}，失败 {failed}，共 {total} 个")
            QMessageBox.warning(self, "完成", f"解锁已完成，但 {failed} 个文件失败，详见日志。")
        else:
            self.set_status("✓ 全部完成")
            self._log.append_timestamped(f"转换结束：全部成功，共 {succeeded} 个")
            QMessageBox.information(self, "完成", "解锁已完成")

    def _finish_busy(self) -> None:
        """恢复控件并释放信号桥。"""
        self._set_busy(False)
        self._bridge = None

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _confirm_close(self) -> bool:
        """运行中关闭时先确认；确认后通知后台尽快停止。"""
        if not self._running:
            return True
        if not confirm(self, "确认", "转换正在进行中，确定要退出吗？"):
            return False
        self._cancel.set()
        return True

    def _persist(self, geometry: str) -> None:
        """关闭时写回输出目录、上次导入目录与窗口 geometry。"""
        save_settings(
            NcmConvertSettings(
                output_dir=self._output_row.edit.text(),
                last_input_dir=self._last_dir,
                geometry=geometry,
            ),
            self._settings_path,
        )


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: NcmConvertWindow(settings_path), app_name="ncmconvertw")
