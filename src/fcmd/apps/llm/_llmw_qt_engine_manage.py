"""llmw 推理引擎管理对话框：备份、删除与清理。

设置页的引擎卡片只保留列表与「管理…」入口；备份 / 删除 / 清理集中在这个非模态
对话框里，避免每行堆两个按钮、也不把危险操作放在卡片标题行。

- 列表来自 :func:`llmengine.list_engines`，默认引擎行不可删除；
- 备份把整个引擎目录打包到 ``<引擎根>/backups/llama-<tag>-<时间>.zip``；
- 清理分开选项：下载缓存（引擎 / cudart zip、.part、临时解压目录）、
  llama.cpp 源码、两者；``backups/`` 永不清理；
- 所有耗时操作走共享 ``submit`` 后台线程，结果写回状态行并通知设置页刷新。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.llm import llmengine, llmenv
from fcmd.apps.llm.llmbase import EngineInfo
from fcmd.gui.qt.widgets import Card, button, confirm, hint_label
from fcmd.gui.qt.workers import submit

__all__ = ["EngineManageDialog", "backup_default_path"]


def backup_default_path(tag: str, home: Path | None = None, *, stamp: str | None = None) -> Path:
    """备份默认路径：``<引擎根>/backups/llama-<tag>[-<时间>].zip``。"""
    suffix = f"-{stamp}" if stamp else ""
    return llmenv.engines_root(home) / "backups" / f"llama-{tag}{suffix}.zip"


class EngineManageDialog(QDialog):  # pragma: no cover - Qt 控件
    """推理引擎管理：备份 / 删除 / 清理。"""

    changed = Signal()
    """引擎目录发生变化（删除 / 清理）后发出，设置页据此刷新列表与源码状态。"""

    def __init__(self, parent: QWidget | None = None, *, home: Path | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("管理推理引擎")
        self.setModal(False)
        self.resize(620, 420)
        self._home = home
        self._engines: list[EngineInfo] = []
        self._busy = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self.list = QListWidget(self)
        self.list.setObjectName("EngineManageList")
        self.list.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list.setSpacing(2)
        self.list.currentRowChanged.connect(lambda _row: self._sync_buttons())
        layout.addWidget(self.list, 1)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.backup_button = button("备份所选…", kind="ghost")
        self.backup_button.setToolTip("把所选引擎目录打包为 zip")
        self.backup_button.clicked.connect(self._backup_selected)
        actions.addWidget(self.backup_button)
        self.open_button = button("打开目录", kind="ghost")
        self.open_button.setToolTip("在文件管理器中打开所选引擎目录")
        self.open_button.clicked.connect(self._open_selected)
        actions.addWidget(self.open_button)
        self.delete_button = button("删除所选", kind="danger")
        self.delete_button.setToolTip("删除所选非默认引擎（不可恢复）")
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)

        cleanup_card = Card("清理", self)
        cleanup_row = QHBoxLayout()
        cleanup_row.setContentsMargins(0, 0, 0, 0)
        cleanup_row.setSpacing(6)
        cleanup_row.addWidget(
            hint_label("下载缓存：引擎 / cudart zip、.part、临时解压目录；源码：各引擎的 llama.cpp。", wrap=True),
            1,
        )
        self.cleanup_cache_button = button("清理下载缓存", kind="ghost")
        self.cleanup_cache_button.clicked.connect(lambda: self._cleanup(downloads=True, sources=False))
        cleanup_row.addWidget(self.cleanup_cache_button)
        self.cleanup_source_button = button("清理源码", kind="ghost")
        self.cleanup_source_button.clicked.connect(lambda: self._cleanup(downloads=False, sources=True))
        cleanup_row.addWidget(self.cleanup_source_button)
        self.cleanup_all_button = button("全部清理", kind="ghost")
        self.cleanup_all_button.clicked.connect(lambda: self._cleanup(downloads=True, sources=True))
        cleanup_row.addWidget(self.cleanup_all_button)
        cleanup_card.body.addLayout(cleanup_row)
        layout.addWidget(cleanup_card)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self.status = hint_label("", wrap=False)
        self.status.setVisible(False)
        footer.addWidget(self.status, 1)
        self.close_button = button("关闭", kind="ghost")
        self.close_button.clicked.connect(self.close)
        footer.addWidget(self.close_button)
        layout.addLayout(footer)

        self.refresh()

    # ------------------------------------------------------------------ #
    # 列表与状态
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """重新读取引擎列表并恢复选中项。"""
        selected = self.selected_tag()
        try:
            self._engines = llmengine.list_engines(self._home)
        except OSError as exc:
            self._engines = []
            self._set_status(f"读取引擎失败：{exc}")
        self.list.clear()
        for info in self._engines:
            default = info.source == "current"
            text = f"{info.tag}{' · 默认' if default else ''}    {info.exe}"
            item = QListWidgetItem(text, self.list)
            item.setData(Qt.ItemDataRole.UserRole, info.tag)
            item.setData(Qt.ItemDataRole.UserRole + 1, default)
            item.setToolTip(f"{info.tag}\n{info.exe}\n来源：{info.source or '-'}")
        if self.list.count():
            row = next((index for index, item in enumerate(self._items()) if item == selected), 0)
            self.list.setCurrentRow(row)
        self._sync_buttons()

    def _items(self) -> list[str]:
        tags: list[str] = []
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item is not None:
                tags.append(str(item.data(Qt.ItemDataRole.UserRole) or ""))
        return tags

    def selected_tag(self) -> str:
        """当前选中的引擎 tag；未选中返回空串。"""
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item is not None else ""

    def selected_info(self) -> EngineInfo | None:
        """当前选中的引擎信息。"""
        tag = self.selected_tag()
        return next((info for info in self._engines if info.tag == tag), None)

    def _sync_buttons(self) -> None:
        info = self.selected_info()
        has = info is not None and not self._busy
        current = bool(info is not None and info.source == "current")
        self.backup_button.setEnabled(has)
        self.open_button.setEnabled(has)
        self.delete_button.setEnabled(has and not current)
        self.delete_button.setToolTip(
            "默认引擎需先切换到其它引擎再删除" if current else "删除所选引擎目录（不可恢复，不影响其它引擎）"
        )

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        for widget in (
            self.backup_button,
            self.open_button,
            self.delete_button,
            self.cleanup_cache_button,
            self.cleanup_source_button,
            self.cleanup_all_button,
        ):
            widget.setEnabled(not busy)
        if not busy:
            self._sync_buttons()

    def _set_status(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    def _error(self, title: str, message: str) -> None:
        self._set_busy(False)
        self._set_status(f"{title}：{message}")
        QMessageBox.critical(self, title, message)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _backup_selected(self) -> None:
        tag = self.selected_tag()
        if not tag:
            return
        chosen, _filter = QFileDialog.getSaveFileName(
            self, "备份引擎", str(backup_default_path(tag, self._home)), "Zip 压缩包 (*.zip);;所有文件 (*.*)"
        )
        if not chosen:
            return
        dest = Path(chosen)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")
        self._set_busy(True)
        self._set_status(f"正在备份 {tag}…")
        submit(
            lambda: llmengine.backup_engine(tag, dest, self._home),
            on_done=lambda path: self._backup_done(tag, path),
            on_error=lambda message: self._error("备份失败", message),
        )

    def _backup_done(self, tag: str, path: Path) -> None:
        self._set_busy(False)
        self._set_status(f"已备份 {tag}：{path}")

    def _delete_selected(self) -> None:
        info = self.selected_info()
        if info is None:
            return
        if not confirm(self, "删除引擎", f"将删除引擎目录 {info.tag}（不可恢复，不影响其它引擎）。确定继续？"):
            return
        self._set_busy(True)
        self._set_status(f"正在删除 {info.tag}…")
        submit(
            llmengine.remove_engine,
            info.tag,
            self._home,
            on_done=self._delete_done,
            on_error=lambda message: self._error("删除失败", message),
        )

    def _delete_done(self, tag: str) -> None:
        self._set_busy(False)
        self._set_status(f"已删除引擎 {tag}")
        self.refresh()
        self.changed.emit()

    def _open_selected(self) -> None:
        info = self.selected_info()
        if info is None:
            return
        from fcmd.apps.llm._llmw_models import open_path

        open_path(info.exe.parent)

    def _cleanup(self, *, downloads: bool, sources: bool) -> None:
        parts = [
            "下载缓存（zip / .part / 临时解压目录）" if downloads else "",
            "各引擎目录下的 llama.cpp 源码" if sources else "",
        ]
        scope = " + ".join(part for part in parts if part)
        if not confirm(self, "清理引擎目录", f"将清理{scope}；下次转换会按需重新获取源码。确定继续？"):
            return
        self._set_busy(True)
        self._set_status("正在清理引擎目录…")
        submit(
            lambda: llmengine.cleanup_engines(self._home, downloads=downloads, sources=sources),
            on_done=self._cleanup_done,
            on_error=lambda message: self._error("清理失败", message),
        )

    def _cleanup_done(self, result: llmengine.CleanupResult) -> None:
        self._set_busy(False)
        self._set_status(f"引擎清理：{result.summary}")
        self.refresh()
        self.changed.emit()
