"""多重命名对话框：预设、预览表与批量执行。"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)

from fcmd.apps.cmder._ops import (
    apply_rename_plan,
    undo_rename,
)
from fcmd.apps.cmder._rename import (
    RenameInput,
    RenameOptions,
    RenamePreset,
    build_rename_preview,
    preview_to_plan,
)
from fcmd.gui.widgets import button, hint_label, make_table, make_table_item

if TYPE_CHECKING:
    from fcmd.apps.cmder.app import FileEntry

from fcmd.apps.cmder.qt._common import _checkbox_item, _rename_status

__all__ = ["MultiRenameDialog"]


class MultiRenameDialog(QDialog):
    """多重命名：规则表单 + 可勾选预览 + 预设保存 / 执行 / 撤销。"""

    def __init__(self, owner: Any, panel: Any, entries: list[FileEntry]) -> None:
        super().__init__(owner)
        self.owner = owner
        self.entries = entries
        self.panel = panel
        self.preview: Any = None
        self.report: Any = None
        self.setWindowTitle("多重命名")
        self.setMinimumSize(960, 680)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        preset_row = QHBoxLayout()
        preset_row.setContentsMargins(0, 0, 0, 0)
        preset_row.addWidget(QLabel("预设"))
        self.preset_combo = QComboBox(self)
        preset_row.addWidget(self.preset_combo, 1)
        for text, handler in (
            ("载入", self._load_preset),
            ("保存", self._save_preset),
            ("删除", self._delete_preset),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            preset_row.addWidget(widget)
        outer.addLayout(preset_row)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        self.name_mask = QLineEdit("[N]", self)
        self.ext_mask = QLineEdit("[E]", self)
        self.find_edit = QLineEdit(self)
        self.replace_edit = QLineEdit(self)
        self.case_mode = QComboBox(self)
        self.case_mode.addItems(["保持", "小写", "大写", "每词首字母"])
        self.case_values = ["keep", "lower", "upper", "title"]
        self.start = QSpinBox(self)
        self.start.setRange(0, 999999)
        self.start.setValue(1)
        self.step = QSpinBox(self)
        self.step.setRange(1, 9999)
        self.digits = QSpinBox(self)
        self.digits.setRange(1, 9)
        self.digits.setValue(3)
        self.sort_key = QComboBox(self)
        self.sort_key.addItems(["名称", "修改时间", "大小"])
        self.sort_values = ["name", "mtime", "size"]
        form.addWidget(QLabel("名称掩码"), 0, 0)
        form.addWidget(self.name_mask, 0, 1)
        form.addWidget(QLabel("扩展名掩码"), 0, 2)
        form.addWidget(self.ext_mask, 0, 3)
        form.addWidget(QLabel("查找"), 1, 0)
        form.addWidget(self.find_edit, 1, 1)
        form.addWidget(QLabel("替换"), 1, 2)
        form.addWidget(self.replace_edit, 1, 3)
        form.addWidget(QLabel("大小写"), 2, 0)
        form.addWidget(self.case_mode, 2, 1)
        form.addWidget(QLabel("排序"), 2, 2)
        form.addWidget(self.sort_key, 2, 3)
        form.addWidget(QLabel("起始 / 步长 / 位数"), 3, 0)
        counter_row = QHBoxLayout()
        counter_row.setContentsMargins(0, 0, 0, 0)
        counter_row.addWidget(self.start)
        counter_row.addWidget(self.step)
        counter_row.addWidget(self.digits)
        form.addLayout(counter_row, 3, 1)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.use_regex = QCheckBox("正则替换", self)
        self.case_sensitive = QCheckBox("区分大小写", self)
        self.only_extension = QCheckBox("仅改扩展名", self)
        for widget in (self.use_regex, self.case_sensitive, self.only_extension):
            flags.addWidget(widget)
        flags.addStretch(1)
        form.addLayout(flags, 3, 2, 1, 2)
        outer.addLayout(form)

        self.table = make_table(
            ("", "原名", "新名", "状态", "说明"),
            widths=(36, 260, 260, 110, 320),
            stretch_columns=(4,),
            parent=self,
        )
        self.table.setMinimumHeight(280)
        outer.addWidget(self.table, 1)
        self.status = hint_label("规则变化后点「预览」查看新名称")
        outer.addWidget(self.status)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        for text, handler in (
            ("全选", lambda: self._set_all(True)),
            ("全不选", lambda: self._set_all(False)),
            ("反选", self._invert),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        actions.addStretch(1)
        preview = button("预览", kind="primary")
        preview.clicked.connect(self._refresh_preview)
        actions.addWidget(preview)
        self.execute_button = button("执行", kind="primary")
        self.execute_button.clicked.connect(self._execute)
        actions.addWidget(self.execute_button)
        self.undo_button = button("撤销本次", kind="danger")
        self.undo_button.setEnabled(False)
        self.undo_button.clicked.connect(self._undo)
        actions.addWidget(self.undo_button)
        cancel = button("关闭")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        outer.addLayout(actions)

        for widget in (
            self.name_mask,
            self.ext_mask,
            self.find_edit,
            self.replace_edit,
        ):
            widget.textChanged.connect(self._refresh_preview)
        for combo in (self.case_mode, self.sort_key):
            combo.currentIndexChanged.connect(self._refresh_preview)
        for spin in (self.start, self.step, self.digits):
            spin.valueChanged.connect(self._refresh_preview)
        for check in (self.use_regex, self.case_sensitive, self.only_extension):
            check.toggled.connect(self._refresh_preview)
        self._reload_presets()
        self._refresh_preview()

    # -- 规则 ---------------------------------------------------------- #
    def options(self) -> RenameOptions:
        return RenameOptions(
            name_mask=self.name_mask.text(),
            ext_mask=self.ext_mask.text(),
            find=self.find_edit.text(),
            replace=self.replace_edit.text(),
            use_regex=self.use_regex.isChecked(),
            case_sensitive=self.case_sensitive.isChecked(),
            only_extension=self.only_extension.isChecked(),
            case_mode=self.case_values[max(self.case_mode.currentIndex(), 0)],
            start=self.start.value(),
            step=self.step.value(),
            digits=self.digits.value(),
            sort_key=self.sort_values[max(self.sort_key.currentIndex(), 0)],
        )

    def _inputs(self) -> list[RenameInput]:
        return [
            RenameInput(path=entry.path, is_dir=entry.is_dir, mtime=entry.mtime, ctime=entry.mtime)
            for entry in self.entries
        ]

    def _refresh_preview(self) -> None:
        self.preview = build_rename_preview(self.options(), self._inputs())
        self.table.setRowCount(len(self.preview.items))
        for row, item in enumerate(self.preview.items):
            self.table.setItem(row, 0, _checkbox_item(item.status == "ok"))
            self.table.setItem(row, 1, make_table_item(item.old_name))
            self.table.setItem(row, 2, make_table_item(item.new_name))
            self.table.setItem(row, 3, make_table_item(_rename_status(item.status)))
            self.table.setItem(row, 4, make_table_item(item.note))
        if self.preview.error:
            self.status.setText(f"规则错误：{self.preview.error}")
        else:
            total = len(self.preview.items)
            self.status.setText(
                f"{total} 项：可执行 {len(self.preview.ok)}，未变化 {len(self.preview.unchanged)}，"
                f"冲突 {len(self.preview.conflicts)}，非法 {len(self.preview.invalid)}"
            )
        self.execute_button.setEnabled(bool(self.preview.ok))

    def _checked_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row, item in enumerate(self.preview.items if self.preview else ()):
            cell = self.table.item(row, 0)
            if cell is not None and cell.checkState() == Qt.CheckState.Checked:
                paths.append(item.path)
        return paths

    def _set_all(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None:
                cell.setCheckState(state)

    def _invert(self) -> None:
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell is not None:
                cell.setCheckState(
                    Qt.CheckState.Unchecked if cell.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked
                )

    def _execute(self) -> None:
        if self.preview is None:
            return
        plan = preview_to_plan(self.preview, self._checked_paths())
        if not plan.changes:
            self.status.setText("没有勾选可执行的条目")
            return
        self.report = apply_rename_plan(plan)
        self.undo_button.setEnabled(bool(self.report.completed))
        self.status.setText(f"已改名 {len(self.report.completed)} 项，失败 {len(self.report.errors)} 项")
        self.owner.set_status(self.status.text())
        self.owner.after_operation()

    def _undo(self) -> None:
        if self.report is None:
            return
        result = undo_rename(self.report)
        self.report = None
        self.undo_button.setEnabled(False)
        self.status.setText(f"已撤销 {len(result.completed)} 项，失败 {len(result.errors)} 项")
        self.owner.after_operation()

    # -- 预设 ---------------------------------------------------------- #
    def _presets(self) -> list[RenamePreset]:
        return list(self.owner.settings.rename_presets)

    def _reload_presets(self) -> None:
        self.preset_combo.clear()
        self.preset_combo.addItems([preset.name for preset in self._presets()])

    def _load_preset(self) -> None:
        index = self.preset_combo.currentIndex()
        presets = self._presets()
        if not (0 <= index < len(presets)):
            return
        options = presets[index].options
        self.name_mask.setText(options.name_mask)
        self.ext_mask.setText(options.ext_mask)
        self.find_edit.setText(options.find)
        self.replace_edit.setText(options.replace)
        self.use_regex.setChecked(options.use_regex)
        self.case_sensitive.setChecked(options.case_sensitive)
        self.only_extension.setChecked(options.only_extension)
        self.case_mode.setCurrentIndex(
            self.case_values.index(options.case_mode) if options.case_mode in self.case_values else 0
        )
        self.start.setValue(options.start)
        self.step.setValue(options.step)
        self.digits.setValue(options.digits)
        self.sort_key.setCurrentIndex(
            self.sort_values.index(options.sort_key) if options.sort_key in self.sort_values else 0
        )
        self._refresh_preview()

    def _save_preset(self) -> None:
        name, accepted = self.owner.ask_text("保存预设", "预设名称", self.preset_combo.currentText())
        if not accepted or not name.strip():
            return
        presets = [preset for preset in self._presets() if preset.name != name.strip()]
        presets.append(RenamePreset(name=name.strip(), options=self.options()))
        self.owner.apply_rename_presets(presets)
        self._reload_presets()

    def _delete_preset(self) -> None:
        name = self.preset_combo.currentText()
        if not name:
            return
        presets = [preset for preset in self._presets() if preset.name != name]
        self.owner.apply_rename_presets(presets)
        self._reload_presets()
