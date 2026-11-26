"""打开方式、压缩选项、分割、副本与过滤器对话框。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import QAbstractItemView, QComboBox, QLineEdit, QSpinBox

from fcmd.apps.cmder import app as core
from fcmd.gui.widgets import DialogShell, FormDialog, button, make_table, make_table_item, pick_dir, pick_file

if TYPE_CHECKING:
    from fcmd.apps.cmder.app import FileEntry

__all__ = ["ArchiveOptionsDialog", "DuplicateDialog", "FilterDialog", "OpenWithDialog", "SplitDialog"]


class OpenWithDialog(FormDialog):  # pragma: no cover - Qt 控件
    """选择外部程序打开当前文件，并记住最近使用。"""

    def __init__(self, owner: Any, entry: FileEntry) -> None:
        super().__init__(owner, title=f"打开方式 - {entry.name}", min_width=560)
        self.owner = owner
        self.entry = entry
        self.program = ""
        self.add_hint(str(entry.path))
        self.recent = QComboBox(self)
        self.recent.addItems(list(owner.settings.open_with))
        self.recent.activated.connect(self._on_recent)
        self.add_row("最近使用", self.recent)
        self.program_edit = QLineEdit(self)
        self.program_edit.setPlaceholderText("可执行文件路径")
        browse = button("浏览…")
        browse.clicked.connect(self._browse)
        self.add_row("程序", self.program_edit, browse)
        self.finish(ok_text="打开", on_ok=self._confirm)

    def _on_recent(self, index: int) -> None:
        del index
        self.program_edit.setText(self.recent.currentText())

    def _browse(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = pick_file(self, "选择程序")
        if chosen:
            self.program_edit.setText(chosen)

    def _confirm(self) -> None:
        program = self.program_edit.text().strip()
        if not program:
            self.owner.show_error("缺少程序", "请填写可执行文件路径")
            return
        self.program = program
        self.owner.remember_open_with(program)
        self.accept()


class ArchiveOptionsDialog(FormDialog):  # pragma: no cover - Qt 控件
    """压缩级别与 zip 密码（写入归档时使用）。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner, title="压缩选项")
        self.owner = owner
        self.level = QSpinBox(self)
        self.level.setRange(0, 9)
        self.level.setValue(owner.archive_options.level)
        self.add_row("压缩级别 0–9", self.level)
        self.password = QLineEdit(owner.archive_options.password, self)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.add_row("zip 密码", self.password)
        self.add_hint("密码仅对 zip 生效；需要 fcmd[crypto]。")
        self.finish(ok_text="保存", on_ok=self._confirm)

    def _confirm(self) -> None:
        from fcmd.apps.cmder._archwrite import ArchiveWriteOptions

        self.owner.archive_options = ArchiveWriteOptions(level=self.level.value(), password=self.password.text())
        self.accept()


class SplitDialog(FormDialog):  # pragma: no cover - Qt 控件
    """按大小或份数分割选中文件。"""

    def __init__(self, owner: Any, entry: FileEntry) -> None:
        super().__init__(owner, title=f"分割 - {entry.name}")
        self.owner = owner
        self.entry = entry
        self.add_hint(f"{entry.path}（{core.format_size(entry.size)}）")
        self.mode = QComboBox(self)
        self.mode.addItem("按份数", "parts")
        self.mode.addItem("按大小", "size")
        self.add_row("分割方式", self.mode)
        self.value = QSpinBox(self)
        self.value.setRange(1, 10000)
        self.value.setValue(2)
        self.add_row("数量", self.value)
        self.unit = QComboBox(self)
        self.unit.addItems(["MB", "KB", "B"])
        self.unit.setCurrentText("MB")
        self.add_row("单位", self.unit)
        self.finish(ok_text="开始", on_ok=self._confirm)

    def _confirm(self) -> None:
        factor = {"B": 1, "KB": 1024, "MB": 1024 * 1024}[self.unit.currentText()]
        if self.mode.currentData() == "parts":
            self.owner.start_split(self.entry.path, parts=self.value.value())
        else:
            self.owner.start_split(self.entry.path, part_size=self.value.value() * factor)
        self.accept()


class DuplicateDialog(DialogShell):  # pragma: no cover - Qt 控件
    """重复文件：按组展示，多余副本可删除或移动到指定目录。"""

    def __init__(self, owner: Any, report: Any) -> None:
        super().__init__(owner, title="查找重复文件", min_width=900)
        self.owner = owner
        self.report = report
        self.add_hint(
            f"扫描 {report.scanned} 个文件，发现 {len(report.groups)} 组重复，可回收 {core.format_size(report.wasted)}"
        )
        self.table = make_table(
            ("组", "大小", "保留", "多余副本"),
            widths=(60, 100, 340, 500),
            stretch_columns=(3,),
            parent=self,
        )
        self.table.setMinimumHeight(420)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.body.addWidget(self.table, 1)
        for row, group in enumerate(report.groups):
            self.table.setItem(row, 0, make_table_item(f"#{row + 1}"))
            self.table.setItem(row, 1, make_table_item(core.format_size(group.size)))
            self.table.setItem(row, 2, make_table_item(str(group.paths[0])))
            self.table.setItem(row, 3, make_table_item("\n".join(str(path) for path in group.paths[1:])))
        move = button("移动多余副本到…")
        move.clicked.connect(self._move)
        delete = button("删除多余副本", kind="danger")
        delete.clicked.connect(self._delete)
        self.finish(cancel_text="关闭", on_cancel=self.accept, extra=(move, delete), fit=False)
        self.setMinimumSize(900, 600)

    def _extras(self) -> list[Path]:
        paths: list[Path] = []
        for row in sorted({index.row() for index in self.table.selectedIndexes()}):
            if 0 <= row < len(self.report.groups):
                paths.extend(self.report.groups[row].paths[1:])
        return paths

    def _move(self) -> None:
        from fcmd.apps.cmder._dupes import move_duplicates

        paths = self._extras()
        if not paths:
            return
        target = pick_dir(self, "选择移动目标目录")
        if not target:
            return
        result = move_duplicates(paths, Path(target))
        self.owner.set_status(f"已移动 {len(result.completed)} 项，失败 {len(result.errors)} 项")
        self.owner.after_operation()
        self.accept()

    def _delete(self) -> None:
        paths = self._extras()
        if paths and self.owner.ask("删除重复副本", f"将 {len(paths)} 个多余副本移入回收站？"):
            self.owner.delete_paths(paths)
            self.owner.after_operation()
            self.accept()


class FilterDialog(FormDialog):  # pragma: no cover - Qt 控件
    """快速过滤器定义：包含 / 排除掩码。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner, title="定义过滤器")
        self.owner = owner
        self.mask_edit = QLineEdit(owner.settings.filter_definition.include, self)
        self.mask_edit.setPlaceholderText("包含掩码，如 *.py;*.md")
        self.exclude_edit = QLineEdit(owner.settings.filter_definition.exclude, self)
        self.exclude_edit.setPlaceholderText("排除掩码，如 *.min.js")
        self.add_row("包含掩码", self.mask_edit)
        self.add_row("排除掩码", self.exclude_edit)
        clear = button("清除过滤器")
        clear.clicked.connect(self._clear)
        self.finish(ok_text="应用", on_ok=self._apply, extra=(clear,))

    def _apply(self) -> None:
        from fcmd.apps.cmder._tabs import FilterDefinition

        self.owner.apply_filter_definition(
            FilterDefinition(include=self.mask_edit.text().strip(), exclude=self.exclude_edit.text().strip())
        )
        self.accept()

    def _clear(self) -> None:
        self.owner.clear_filter_definition()
        self.accept()
