"""cmderw（Qt）对话框：多重命名、搜索、属性、书签、传输队列与文本比较。

对话框只做控件编排与校验，规则求值 / 计划 / 执行全部复用
:mod:`fcmd.apps.cmder._rename` / ``_ops`` / ``_attrs`` / ``_queue`` / ``_diff``。
窗口对象（``owner``）提供活动面板、设置、状态栏与通用的复制 / 移动 / 删除入口，
对话框通过它触发跨面板动作，避免重复实现。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QSpinBox,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
)

from fcmd.apps.cmder import _diff as diffmod
from fcmd.apps.cmder import app as core
from fcmd.apps.cmder._attrs import AttributeChange, apply_attributes, format_timestamp, parse_timestamp
from fcmd.apps.cmder._ops import (
    COMPARE_MODE_TEXT,
    COMPARE_MODES,
    COMPARE_STATUS_TEXT,
    SYNC_BOTH,
    SYNC_CONFLICT_TEXT,
    SYNC_CONFLICTS,
    SYNC_LTR,
    SYNC_RTL,
    ColorRule,
    CompareOptions,
    SyncOptions,
    SyncRunReport,
    apply_rename_plan,
    build_sync_plan,
    compare_trees,
    execute_sync_deletes,
    format_sync_report,
    item_status_text,
    revalidate_sync_items,
    split_sync_selection,
    sync_invert_selection,
    sync_item_key,
    sync_select_all,
    undo_rename,
)
from fcmd.apps.cmder._rename import (
    RenameInput,
    RenameOptions,
    RenamePreset,
    build_rename_preview,
    preview_to_plan,
)
from fcmd.apps.cmder._search import SearchFilters, parse_datetime, parse_size
from fcmd.gui.widgets import Card, button, hint_label, make_table, make_table_item
from fcmd.gui.workers import submit

if TYPE_CHECKING:
    from fcmd.apps.cmder._ops import QueueTask
    from fcmd.apps.cmder.app import FileEntry

_RENAME_STATUS = {"ok": "可执行", "unchanged": "未变化", "invalid": "非法", "conflict": "冲突"}


def _rename_status(status: str) -> str:
    """多重命名预览行的中文状态。"""
    return _RENAME_STATUS.get(status, status)


__all__ = [
    "AttributeDialog",
    "BookmarkDialog",
    "MultiRenameDialog",
    "SearchDialog",
    "TextDiffDialog",
    "TransferQueueDialog",
]


def _checkbox_item(checked: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem("")
    item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
    return item


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


class SearchDialog(QDialog):
    """搜索：文件名 / 内容（含过滤器）与归档内搜索。"""

    def __init__(self, owner: Any, directory: Path, *, archive: bool = False) -> None:
        super().__init__(owner)
        self.owner = owner
        self.directory = directory
        self.archive_mode = archive
        self.hits: list[Any] = []
        self.setWindowTitle("搜索压缩包" if archive else "搜索")
        self.setMinimumSize(900, 620)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.addWidget(QLabel("目录"), 0, 0)
        self.directory_edit = QLineEdit(str(directory), self)
        form.addWidget(self.directory_edit, 0, 1)
        browse = button("浏览…")
        browse.clicked.connect(self._pick_directory)
        form.addWidget(browse, 0, 2)
        form.addWidget(QLabel("模式"), 1, 0)
        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setPlaceholderText("文件名 glob（*.txt）或内容正则")
        form.addWidget(self.pattern_edit, 1, 1)
        self.content = QCheckBox("搜索内容", self)
        form.addWidget(self.content, 1, 2)
        form.addWidget(QLabel("扩展名"), 2, 0)
        self.extension_edit = QLineEdit(self)
        self.extension_edit.setPlaceholderText(".txt（仅内容搜索）")
        form.addWidget(self.extension_edit, 2, 1)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.case_sensitive = QCheckBox("区分大小写", self)
        self.whole_word = QCheckBox("整词", self)
        flags.addWidget(self.case_sensitive)
        flags.addWidget(self.whole_word)
        flags.addStretch(1)
        form.addLayout(flags, 2, 2)
        outer.addLayout(form)

        filters = Card("过滤器（可选）")
        filter_grid = QGridLayout()
        filter_grid.setHorizontalSpacing(8)
        filter_grid.setVerticalSpacing(4)
        self.mask_edit = QLineEdit(filters)
        self.exclude_edit = QLineEdit(filters)
        self.size_min = QLineEdit(filters)
        self.size_max = QLineEdit(filters)
        self.after_edit = QLineEdit(filters)
        self.before_edit = QLineEdit(filters)
        filter_grid.addWidget(QLabel("包含掩码"), 0, 0)
        filter_grid.addWidget(self.mask_edit, 0, 1)
        filter_grid.addWidget(QLabel("排除掩码"), 0, 2)
        filter_grid.addWidget(self.exclude_edit, 0, 3)
        filter_grid.addWidget(QLabel("最小 / 最大字节"), 1, 0)
        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.addWidget(self.size_min)
        size_row.addWidget(self.size_max)
        filter_grid.addLayout(size_row, 1, 1)
        filter_grid.addWidget(QLabel("修改时间 ≥ / ≤"), 1, 2)
        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.addWidget(self.after_edit)
        time_row.addWidget(self.before_edit)
        filter_grid.addLayout(time_row, 1, 3)
        filters.body.addLayout(filter_grid)
        outer.addWidget(filters)

        self.results = make_table(
            ("路径", "行", "内容"),
            widths=(520, 70, 320),
            stretch_columns=(0, 2),
            parent=self,
        )
        self.results.setMinimumHeight(260)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        outer.addWidget(self.results, 1)
        self.status = hint_label("输入模式后点「搜索」")
        outer.addWidget(self.status)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self.search_button = button("搜索", kind="primary")
        self.search_button.clicked.connect(self._search)
        actions.addWidget(self.search_button)
        actions.addStretch(1)
        for text, handler in (
            ("定位", self._reveal),
            ("加入选择", self._adopt),
            ("复制到…", lambda: self._transfer(move=False)),
            ("移动到…", lambda: self._transfer(move=True)),
            ("删除", self._delete),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        close = button("关闭")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        outer.addLayout(actions)
        self.pattern_edit.returnPressed.connect(self._search)

    def _pick_directory(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = QFileDialog.getExistingDirectory(self, "选择搜索目录", self.directory_edit.text())
        if chosen:
            self.directory_edit.setText(chosen)

    def _filters(self) -> SearchFilters:
        return SearchFilters(
            min_size=parse_size(self.size_min.text()),
            max_size=parse_size(self.size_max.text()),
            modified_after=parse_datetime(self.after_edit.text()),
            modified_before=parse_datetime(self.before_edit.text()),
        )

    def _masked(self, hits: list[Any]) -> list[Any]:
        """按包含 / 排除掩码过滤命中（目录名与文件名都参与）。"""
        from fcmd.apps.cmder._ops import match_names

        include = self.mask_edit.text().strip()
        exclude = self.exclude_edit.text().strip()
        result = hits
        if include:
            names = match_names([hit.path.name for hit in result], include)
            result = [hit for hit in result if hit.path.name in names]
        if exclude:
            names = match_names([hit.path.name for hit in result], exclude)
            result = [hit for hit in result if hit.path.name not in names]
        return result

    def _search(self) -> None:
        pattern = self.pattern_edit.text().strip()
        directory = Path(self.directory_edit.text().strip())
        if not pattern:
            self.status.setText("搜索内容不能为空")
            return
        self.search_button.setEnabled(False)
        self.status.setText("正在搜索…")
        if self.archive_mode:
            from fcmd.apps.cmder._arcsearch import search_archive

            submit(
                search_archive,
                directory,
                pattern,
                content=self.content.isChecked(),
                case_sensitive=self.case_sensitive.isChecked(),
                whole_word=self.whole_word.isChecked(),
                on_done=self._show_archive,
                on_error=self._failed,
                on_finished=lambda: self.search_button.setEnabled(True),
            )
            return
        submit(
            core.search_paths,
            directory,
            pattern,
            content=self.content.isChecked(),
            extension=self.extension_edit.text(),
            case_sensitive=self.case_sensitive.isChecked() or None,
            whole_word=self.whole_word.isChecked(),
            filters=self._filters(),
            on_done=self._show_hits,
            on_error=self._failed,
            on_finished=lambda: self.search_button.setEnabled(True),
        )

    def _failed(self, message: str) -> None:
        self.status.setText(f"搜索失败：{message}")

    def _show_hits(self, hits: list[Any]) -> None:
        self.hits = self._masked(list(hits))
        self.results.setRowCount(len(self.hits))
        for row, hit in enumerate(self.hits):
            self.results.setItem(row, 0, make_table_item(str(hit.path)))
            self.results.setItem(row, 1, make_table_item(str(hit.line or "")))
            self.results.setItem(row, 2, make_table_item(hit.text))
        self.status.setText(f"命中 {len(self.hits)} 条")

    def _show_archive(self, report: Any) -> None:
        self.hits = list(report.hits)
        self.results.setRowCount(len(self.hits))
        for row, hit in enumerate(self.hits):
            self.results.setItem(row, 0, make_table_item(f"{hit.archive}:{hit.member}"))
            self.results.setItem(row, 1, make_table_item(str(hit.line or "")))
            self.results.setItem(row, 2, make_table_item(hit.text))
        self.status.setText(f"压缩包内命中 {len(self.hits)} 条")

    def _selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for row in sorted({index.row() for index in self.results.selectedIndexes()}):
            if 0 <= row < len(self.hits):
                hit = self.hits[row]
                if isinstance(getattr(hit, "path", None), Path):
                    paths.append(hit.path)
        return paths

    def _reveal(self) -> None:
        for path in self._selected_paths()[:1]:
            self.owner.reveal_path(path)

    def _adopt(self) -> None:
        paths = self._selected_paths()
        if paths:
            self.status.setText(self.owner.adopt_paths(paths))

    def _transfer(self, *, move: bool) -> None:
        paths = self._selected_paths()
        if not paths:
            return
        target = QFileDialog.getExistingDirectory(self, "选择目标目录")
        if target:
            self.owner.transfer_paths(paths, move=move, destination=Path(target))

    def _delete(self) -> None:
        paths = self._selected_paths()
        if paths and self.owner.ask("删除", f"删除选中的 {len(paths)} 个文件（回收站）？"):
            self.owner.delete_paths(paths)


class AttributeDialog(QDialog):
    """批量修改时间戳与 Windows 只读 / 隐藏属性。"""

    def __init__(self, owner: Any, paths: list[Path]) -> None:
        super().__init__(owner)
        self.owner = owner
        self.paths = paths
        self.setWindowTitle("修改属性")
        self.setMinimumWidth(520)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label(f"将应用到 {len(paths)} 个条目"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("修改时间"), 0, 0)
        self.mtime_edit = QLineEdit(self)
        self.mtime_edit.setPlaceholderText("YYYY-MM-DD HH:MM:SS，留空不改")
        if paths:
            with __import__("contextlib").suppress(OSError):
                self.mtime_edit.setText(format_timestamp(paths[0].stat().st_mtime))
        grid.addWidget(self.mtime_edit, 0, 1)
        self.readonly = QCheckBox("只读", self)
        self.hidden = QCheckBox("隐藏", self)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        for widget in (self.readonly, self.hidden):
            flags.addWidget(widget)
        flags.addStretch(1)
        grid.addLayout(flags, 1, 0, 1, 2)
        outer.addLayout(grid)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        apply_button = button("应用", kind="primary")
        apply_button.clicked.connect(self._apply)
        actions.addWidget(apply_button)
        outer.addLayout(actions)

    def _apply(self) -> None:
        timestamp = None
        if self.mtime_edit.text().strip():
            timestamp = parse_timestamp(self.mtime_edit.text())
            if timestamp is None:
                self.owner.show_error("时间无效", "时间格式应为 YYYY-MM-DD HH:MM:SS")
                return
        report = apply_attributes(
            self.paths,
            AttributeChange(
                modified=timestamp,
                readonly=self.readonly.isChecked() if self.readonly.isChecked() else None,
                hidden=self.hidden.isChecked() if self.hidden.isChecked() else None,
            ),
        )
        self.owner.set_status(report.message())
        self.owner.after_operation()
        self.accept()


class BookmarkDialog(QDialog):
    """书签管理：添加当前目录、删除、打开。"""

    def __init__(self, owner: Any, panel: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.panel = panel
        self.setWindowTitle("书签")
        self.setMinimumSize(560, 420)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        self.list = QListWidget(self)
        outer.addWidget(self.list, 1)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        for text, handler in (
            ("打开", self._open),
            ("添加当前目录", self._add),
            ("删除", self._remove),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        actions.addStretch(1)
        close = button("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        outer.addLayout(actions)
        self.list.itemDoubleClicked.connect(lambda _item: self._open())
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        self.list.addItems(list(self.owner.settings.bookmarks))

    def _save(self, items: list[str]) -> None:
        self.owner.settings.bookmarks = items
        self.owner.persist_settings()
        self._reload()

    def _add(self) -> None:
        path = self.panel.state.path
        items = list(self.owner.settings.bookmarks)
        if path and path not in items:
            items.append(path)
            self._save(items)

    def _remove(self) -> None:
        row = self.list.currentRow()
        items = list(self.owner.settings.bookmarks)
        if 0 <= row < len(items):
            items.pop(row)
            self._save(items)

    def _open(self) -> None:
        row = self.list.currentRow()
        items = list(self.owner.settings.bookmarks)
        if 0 <= row < len(items):
            self.panel.set_path(Path(items[row]))
            self.accept()


class TransferQueueDialog(QDialog):
    """传输队列：任务 / 条目状态、暂停、并发、取消与重试。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("传输队列")
        self.setMinimumSize(900, 600)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        self.pause_button = button("暂停队列")
        self.pause_button.clicked.connect(self._toggle_pause)
        controls.addWidget(self.pause_button)
        controls.addWidget(QLabel("并发"))
        self.parallel = QSpinBox(self)
        self.parallel.setRange(1, 8)
        self.parallel.setValue(owner.settings.queue_parallel)
        self.parallel.valueChanged.connect(self._set_parallel)
        controls.addWidget(self.parallel)
        controls.addStretch(1)
        for text, handler in (
            ("取消选中任务", self._cancel),
            ("重试选中任务", self._retry),
            ("重试全部失败", self._retry_all),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            controls.addWidget(widget)
        outer.addLayout(controls)

        self.tasks = make_table(
            ("任务", "动作", "目标", "状态", "完成 / 失败"),
            widths=(70, 80, 380, 110, 120),
            stretch_columns=(2,),
            parent=self,
        )
        self.tasks.setMinimumHeight(220)
        outer.addWidget(self.tasks, 1)
        self.items = make_table(
            ("来源", "目标", "状态", "错误"),
            widths=(360, 360, 100, 260),
            stretch_columns=(0, 1, 3),
            parent=self,
        )
        self.items.setMinimumHeight(200)
        outer.addWidget(self.items, 1)
        self.tasks.itemSelectionChanged.connect(self._refresh_items)
        close = button("关闭")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(close)
        outer.addLayout(row)
        self.refresh()

    def _selected_task(self) -> QueueTask | None:
        row = self.tasks.currentRow()
        tasks = self.owner.queue.tasks
        return tasks[row] if 0 <= row < len(tasks) else None

    def refresh(self) -> None:
        tasks = self.owner.queue.tasks
        self.tasks.setRowCount(len(tasks))
        for row, task in enumerate(tasks):
            self.tasks.setItem(row, 0, make_table_item(f"#{task.task_id}"))
            self.tasks.setItem(row, 1, make_table_item(task.action))
            self.tasks.setItem(row, 2, make_table_item(str(task.destination)))
            self.tasks.setItem(row, 3, make_table_item(task.status_text))
            self.tasks.setItem(row, 4, make_table_item(f"{task.done_count} / {task.failed_count}"))
        self.pause_button.setText("恢复队列" if self.owner.queue_paused() else "暂停队列")
        self._refresh_items()

    def _refresh_items(self) -> None:
        task = self._selected_task()
        items = task.items if task is not None else []
        self.items.setRowCount(len(items))
        for row, item in enumerate(items):
            self.items.setItem(row, 0, make_table_item(str(item.source)))
            self.items.setItem(row, 1, make_table_item(str(item.target)))
            self.items.setItem(row, 2, make_table_item(item_status_text(item)))
            self.items.setItem(row, 3, make_table_item(item.error))

    def _toggle_pause(self) -> None:
        self.owner.toggle_queue_pause()
        self.refresh()

    def _set_parallel(self, value: int) -> None:
        self.owner.set_queue_parallel(value)

    def _cancel(self) -> None:
        task = self._selected_task()
        if task is not None:
            message = self.owner.queue.cancel(task.task_id)
            self.owner.set_status(message)
            self.refresh()

    def _retry(self) -> None:
        task = self._selected_task()
        if task is not None:
            self.owner.retry_queue_items(task.task_id)
            self.refresh()

    def _retry_all(self) -> None:
        for task in list(self.owner.queue.tasks):
            if task.failed_count:
                self.owner.retry_queue_items(task.task_id)
        self.refresh()


class TextDiffDialog(QDialog):
    """两个文本文件并排只读比较：增删改着色、同步滚动。"""

    def __init__(self, owner: Any, left: Path, right: Path) -> None:
        super().__init__(owner)
        self.setWindowTitle(f"比较 {left.name} ↔ {right.name}")
        self.setMinimumSize(1000, 640)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)
        self.status = hint_label("")
        outer.addWidget(self.status)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.left = self._build_view(row)
        self.right = self._build_view(row)
        outer.addLayout(row, 1)
        self.left.verticalScrollBar().valueChanged.connect(self.right.verticalScrollBar().setValue)
        self.right.verticalScrollBar().valueChanged.connect(self.left.verticalScrollBar().setValue)
        self._render(left, right)
        close = button("关闭")
        close.clicked.connect(self.accept)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        bottom.addWidget(close)
        outer.addLayout(bottom)

    def _build_view(self, row: QHBoxLayout) -> QTextEdit:
        view = QTextEdit(self)
        view.setReadOnly(True)
        view.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        view.setFont(QFont("Consolas", 9))
        row.addWidget(view, 1)
        return view

    def _render(self, left: Path, right: Path) -> None:
        try:
            left_lines, left_truncated = diffmod.load_lines(left)
            right_lines, right_truncated = diffmod.load_lines(right)
        except (OSError, ValueError) as exc:
            self.status.setText(f"读取失败：{exc}")
            return
        result = diffmod.diff_lines(left_lines, right_lines)
        palette = self.palette()
        removed = QColor(palette.text().color())
        added = QColor("#2e7d32")
        changed = QColor("#b26a00")
        left_html: list[str] = []
        right_html: list[str] = []
        for row in result.rows:
            if row.kind == "left":
                left_html.append(f'<span style="background:#ffcdd2">{row.left_text}</span>')
                right_html.append("")
            elif row.kind == "right":
                left_html.append("")
                right_html.append(f'<span style="background:#c8e6c9">{row.right_text}</span>')
            elif row.kind == "both":
                left_html.append(row.left_text)
                right_html.append(row.right_text)
            else:
                left_html.append(f'<span style="background:#ffe0b2">{row.left_text}</span>')
                right_html.append(f'<span style="background:#ffe0b2">{row.right_text}</span>')
        self.left.setHtml("<pre>" + "\n".join(left_html) + "</pre>")
        self.right.setHtml("<pre>" + "\n".join(right_html) + "</pre>")
        del removed, added, changed
        note = diffmod.summary_text(result)
        if left_truncated or right_truncated:
            note += "（大文件仅比较前若干行）"
        self.status.setText(note)


class CompareDialog(QDialog):
    """只读目录比较：口径 / 掩码 / 容差 + 结果表，可转入同步预演。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.report: Any = None
        self.setWindowTitle("目录比较")
        self.setMinimumSize(1040, 680)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.mode_combo = QComboBox(self)
        for mode in COMPARE_MODES:
            self.mode_combo.addItem(COMPARE_MODE_TEXT[mode], mode)
        self.mode_combo.setCurrentIndex(COMPARE_MODES.index("time"))
        grid.addWidget(QLabel("比较口径"), 0, 0)
        grid.addWidget(self.mode_combo, 0, 1)
        self.tolerance = QLineEdit("2", self)
        grid.addWidget(QLabel("时间容差(秒)"), 0, 2)
        grid.addWidget(self.tolerance, 0, 3)
        self.mask_edit = QLineEdit(self)
        self.exclude_edit = QLineEdit(self)
        grid.addWidget(QLabel("包含掩码"), 1, 0)
        grid.addWidget(self.mask_edit, 1, 1)
        grid.addWidget(QLabel("排除掩码"), 1, 2)
        grid.addWidget(self.exclude_edit, 1, 3)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.recursive = QCheckBox("递归", self)
        self.recursive.setChecked(True)
        self.include_hidden = QCheckBox("包含隐藏项", self)
        self.files_only = QCheckBox("仅比较文件", self)
        for widget in (self.recursive, self.include_hidden, self.files_only):
            flags.addWidget(widget)
        flags.addStretch(1)
        grid.addLayout(flags, 2, 0, 1, 4)
        outer.addLayout(grid)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self.compare_button = button("开始比较", kind="primary")
        self.compare_button.clicked.connect(self._start)
        toolbar.addWidget(self.compare_button)
        toolbar.addWidget(QLabel("筛选"))
        self.filter_combo = QComboBox(self)
        self.filter_combo.addItem("全部", "all")
        for status, label in COMPARE_STATUS_TEXT.items():
            self.filter_combo.addItem(label, status)
        self.filter_combo.currentIndexChanged.connect(lambda _index: self._render())
        toolbar.addWidget(self.filter_combo)
        toolbar.addStretch(1)
        self.sync_button = button("→ 同步预演")
        self.sync_button.setEnabled(False)
        self.sync_button.clicked.connect(self._open_sync)
        toolbar.addWidget(self.sync_button)
        outer.addLayout(toolbar)

        self.table = make_table(
            ("相对路径", "状态", "左大小", "右大小", "左时间", "右时间", "说明"),
            widths=(360, 100, 100, 100, 150, 150, 220),
            stretch_columns=(6,),
            parent=self,
        )
        self.table.setMinimumHeight(420)
        outer.addWidget(self.table, 1)
        self.status = hint_label("点「开始比较」比较左右面板目录")
        outer.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = button("关闭")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        outer.addLayout(bottom)

    def _options(self) -> CompareOptions:
        try:
            tolerance = max(0.0, float(self.tolerance.text() or "0"))
        except ValueError:
            tolerance = 2.0
        return CompareOptions(
            recursive=self.recursive.isChecked(),
            include_hidden=self.include_hidden.isChecked(),
            mtime_tolerance=tolerance,
            mode=self.mode_combo.currentData(),
            mask=self.mask_edit.text().strip(),
            exclude_mask=self.exclude_edit.text().strip(),
            files_only=self.files_only.isChecked(),
        )

    def _start(self) -> None:
        left = self.owner.left_panel.state.path
        right = self.owner.right_panel.state.path
        self.compare_button.setEnabled(False)
        self.status.setText("正在比较…")
        submit(
            compare_trees,
            Path(left),
            Path(right),
            self._options(),
            on_done=self._done,
            on_error=self._failed,
            on_finished=lambda: self.compare_button.setEnabled(True),
        )

    def _failed(self, message: str) -> None:
        self.status.setText(f"比较失败：{message}")

    def _done(self, report: Any) -> None:
        self.report = report
        counts = report.counts()
        self.status.setText(
            f"共 {counts.get('total', 0)} 项：仅左侧 {counts.get('left_only', 0)}，"
            f"仅右侧 {counts.get('right_only', 0)}，不同 {counts.get('different', 0)}，相同 {counts.get('same', 0)}"
            + (f"；{len(report.errors)} 项读取失败" if report.errors else "")
        )
        self.sync_button.setEnabled(bool(report.entries))
        self._render()

    def _render(self) -> None:
        status = self.filter_combo.currentData()
        entries = self.report.filtered(status) if self.report is not None else []
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            self.table.setItem(row, 0, make_table_item(entry.relative))
            self.table.setItem(row, 1, make_table_item(COMPARE_STATUS_TEXT.get(entry.status, entry.status)))
            self.table.setItem(row, 2, make_table_item(core.format_size(entry.left_size or 0)))
            self.table.setItem(row, 3, make_table_item(core.format_size(entry.right_size or 0)))
            self.table.setItem(row, 4, make_table_item(core.format_mtime(entry.left_mtime or 0)))
            self.table.setItem(row, 5, make_table_item(core.format_mtime(entry.right_mtime or 0)))
            self.table.setItem(row, 6, make_table_item(entry.reason))

    def _open_sync(self) -> None:
        if self.report is not None:
            self.owner.open_sync(self.report, self._options().mtime_tolerance)


class SyncDialog(QDialog):
    """预演式目录同步：方向 / 冲突策略 / 删除开关 + 可勾选计划。"""

    def __init__(self, owner: Any, report: Any, tolerance: float = 2.0) -> None:
        super().__init__(owner)
        self.owner = owner
        self.report = report
        self.tolerance = tolerance
        self.plan: Any = None
        self.selection: frozenset[str] = frozenset()
        self.setWindowTitle("目录同步")
        self.setMinimumSize(1080, 700)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.direction_combo = QComboBox(self)
        for direction, label in ((SYNC_LTR, "左 → 右"), (SYNC_RTL, "右 → 左"), (SYNC_BOTH, "双向镜像")):
            self.direction_combo.addItem(label, direction)
        self.direction_combo.currentIndexChanged.connect(lambda _index: self.refresh_plan())
        grid.addWidget(QLabel("方向"), 0, 0)
        grid.addWidget(self.direction_combo, 0, 1)
        self.conflict_combo = QComboBox(self)
        for policy in SYNC_CONFLICTS:
            self.conflict_combo.addItem(SYNC_CONFLICT_TEXT[policy], policy)
        self.conflict_combo.currentIndexChanged.connect(lambda _index: self.refresh_plan())
        grid.addWidget(QLabel("冲突"), 0, 2)
        grid.addWidget(self.conflict_combo, 0, 3)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.delete_extra = QCheckBox("删除目标端多余项（回收站）", self)
        self.delete_extra.toggled.connect(lambda _checked: self.refresh_plan())
        self.delete_dirs = QCheckBox("包含目录", self)
        self.delete_dirs.toggled.connect(lambda _checked: self.refresh_plan())
        for widget in (self.delete_extra, self.delete_dirs):
            flags.addWidget(widget)
        flags.addStretch(1)
        grid.addLayout(flags, 1, 0, 1, 4)
        outer.addLayout(grid)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        for text, handler in (
            ("全选", lambda: self._set_selection(sync_select_all(self.plan)) if self.plan else None),
            ("全不选", lambda: self._set_selection(frozenset())),
            (
                "反选",
                lambda: self._set_selection(sync_invert_selection(self.plan, self.selection)) if self.plan else None,
            ),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        self.execute_button = button("执行勾选项", kind="primary")
        self.execute_button.clicked.connect(self._execute)
        toolbar.addWidget(self.execute_button)
        outer.addLayout(toolbar)

        self.table = make_table(
            ("", "动作", "相对路径", "目标", "说明"),
            widths=(36, 100, 360, 420, 220),
            stretch_columns=(2, 3),
            parent=self,
        )
        self.table.setMinimumHeight(460)
        outer.addWidget(self.table, 1)
        self.status = hint_label("")
        outer.addWidget(self.status)
        bottom = QHBoxLayout()
        bottom.addStretch(1)
        close = button("关闭")
        close.clicked.connect(self.accept)
        bottom.addWidget(close)
        outer.addLayout(bottom)
        self.refresh_plan()

    def _options(self) -> SyncOptions:
        return SyncOptions(
            direction=self.direction_combo.currentData(),
            conflict=self.conflict_combo.currentData(),
            delete_target_extra=self.delete_extra.isChecked(),
            delete_dirs=self.delete_dirs.isChecked(),
            mtime_tolerance=self.tolerance,
        )

    def refresh_plan(self) -> None:
        try:
            self.plan = build_sync_plan(self.report, self._options())
        except ValueError as exc:
            self.status.setText(f"预演失败：{exc}")
            return
        self.selection = sync_select_all(self.plan)
        self._render()

    def _render(self) -> None:
        plan = self.plan
        items = [*plan.copies, *plan.deletes] if plan is not None else []
        self.table.setRowCount(len(items))
        for row, item in enumerate(items):
            cell = _checkbox_item(sync_item_key(item) in self.selection)
            self.table.setItem(row, 0, cell)
            self.table.setItem(row, 1, make_table_item(item.action_text))
            self.table.setItem(row, 2, make_table_item(item.relative))
            self.table.setItem(row, 3, make_table_item(str(item.target)))
            self.table.setItem(row, 4, make_table_item(item.skip_reason))
        if plan is not None:
            counts = plan.counts()
            self.status.setText(
                f"复制 {counts['copy']}（新建 {counts['copy_new']} / 覆盖 {counts['copy_overwrite']}），"
                f"删除 {counts['delete']}，跳过 {counts['skipped']}，相同 {counts['same']}"
            )

    def _set_selection(self, selection: frozenset[str]) -> None:
        self.selection = selection
        self._render()

    def _execute(self) -> None:
        plan = self.plan
        if plan is None or not self.selection:
            self.status.setText("没有勾选任何计划项")
            return
        copies, deletes, unselected = split_sync_selection(plan, self.selection)
        if not self.owner.ask(
            "确认执行同步",
            f"将复制 {len(copies)} 项、删除 {len(deletes)} 项（目标端多余项移入回收站），继续？",
        ):
            return
        ready_copies, skipped_copies = revalidate_sync_items(copies, mtime_tolerance=self.tolerance)
        ready_deletes, skipped_deletes = revalidate_sync_items(deletes, mtime_tolerance=self.tolerance)
        groups: dict[Path, list[Any]] = {}
        for item in ready_copies:
            if item.source is None:
                continue
            transfer = core.TransferItem(source=item.source, target=item.target, overwrite=item.overwrite)
            groups.setdefault(plan.root_for(item.target), []).append(transfer)
        enqueue_ok = True
        for destination, items in groups.items():
            if not self.owner.enqueue_transfer(items, destination, move=False):
                enqueue_ok = False
        delete_report = execute_sync_deletes(ready_deletes) if enqueue_ok else None
        self.owner.refresh_both()
        report = SyncRunReport(
            copied=len(ready_copies) if enqueue_ok else 0,
            copied_new=sum(1 for item in ready_copies if not item.overwrite),
            deleted=len(delete_report.completed) if delete_report else 0,
            delete_not_executed=len(delete_report.not_executed) if delete_report else len(ready_deletes),
            unselected=unselected,
            enqueue_ok=enqueue_ok,
            skipped=tuple((str(item.target), reason) for item, reason in (*skipped_copies, *skipped_deletes)),
            failed=tuple(
                (str(item.target), error) for item, error in (delete_report.failures if delete_report else ())
            ),
        )
        self.owner.show_text("同步结果", "\n".join(format_sync_report(report)))
        self.status.setText("已执行；复制任务在传输队列中继续")


class UserCommandsDialog(QDialog):
    """用户命令：增删改、上下移、入口开关与试运行。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.commands = list(owner.settings.user_commands)
        self.setWindowTitle("用户命令")
        self.setMinimumSize(860, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        self.table = make_table(
            ("名称", "命令", "参数", "菜单", "工具栏", "右键"),
            widths=(160, 320, 220, 70, 80, 70),
            stretch_columns=(1, 2),
            parent=self,
        )
        self.table.setMinimumHeight(260)
        self.table.itemSelectionChanged.connect(self._on_select)
        outer.addWidget(self.table, 1)
        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        form.addWidget(QLabel("名称"), 0, 0)
        self.name_edit = QLineEdit(self)
        form.addWidget(self.name_edit, 0, 1)
        form.addWidget(QLabel("命令"), 0, 2)
        self.command_edit = QLineEdit(self)
        form.addWidget(self.command_edit, 0, 3)
        form.addWidget(QLabel("参数"), 1, 0)
        self.parameters_edit = QLineEdit(self)
        form.addWidget(self.parameters_edit, 1, 1, 1, 3)
        outer.addLayout(form)
        flags = QHBoxLayout()
        flags.setContentsMargins(0, 0, 0, 0)
        self.in_menu = QCheckBox("菜单", self)
        self.in_toolbar = QCheckBox("工具栏", self)
        self.in_context = QCheckBox("右键菜单", self)
        for widget in (self.in_menu, self.in_toolbar, self.in_context):
            flags.addWidget(widget)
        flags.addStretch(1)
        outer.addLayout(flags)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        for text, handler in (
            ("新建", self._new),
            ("保存", self._commit),
            ("删除", self._delete),
            ("上移", lambda: self._move(-1)),
            ("下移", lambda: self._move(1)),
            ("试运行", self._run),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        actions.addStretch(1)
        apply_button = button("应用并关闭", kind="primary")
        apply_button.clicked.connect(self._apply)
        actions.addWidget(apply_button)
        close = button("取消")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        outer.addLayout(actions)
        self._reload()

    def _reload(self) -> None:
        self.table.setRowCount(len(self.commands))
        for row, command in enumerate(self.commands):
            self.table.setItem(row, 0, make_table_item(command.name))
            self.table.setItem(row, 1, make_table_item(command.command))
            self.table.setItem(row, 2, make_table_item(command.parameters))
            self.table.setItem(row, 3, make_table_item("是" if command.in_menu else ""))
            self.table.setItem(row, 4, make_table_item("是" if command.in_toolbar else ""))
            self.table.setItem(row, 5, make_table_item("是" if command.in_context else ""))

    def _on_select(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.commands):
            command = self.commands[row]
            self.name_edit.setText(command.name)
            self.command_edit.setText(command.command)
            self.parameters_edit.setText(command.parameters)
            self.in_menu.setChecked(command.in_menu)
            self.in_toolbar.setChecked(command.in_toolbar)
            self.in_context.setChecked(command.in_context)

    def _new(self) -> None:
        self.name_edit.clear()
        self.command_edit.clear()
        self.parameters_edit.clear()
        for widget in (self.in_menu, self.in_toolbar, self.in_context):
            widget.setChecked(True)

    def _build(self) -> Any:
        from fcmd.apps.cmder._commands import UserCommand

        return UserCommand(
            name=self.name_edit.text().strip(),
            command=self.command_edit.text().strip(),
            parameters=self.parameters_edit.text().strip(),
            in_menu=self.in_menu.isChecked(),
            in_toolbar=self.in_toolbar.isChecked(),
            in_context=self.in_context.isChecked(),
        )

    def _commit(self) -> None:
        command = self._build()
        if not command.name or not command.command:
            self.owner.show_error("命令无效", "名称与命令不能为空")
            return
        row = self.table.currentRow()
        if 0 <= row < len(self.commands):
            self.commands[row] = command
        else:
            self.commands.append(command)
        self._reload()

    def _delete(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.commands):
            self.commands.pop(row)
            self._reload()

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if 0 <= row < len(self.commands) and 0 <= target < len(self.commands):
            self.commands[row], self.commands[target] = self.commands[target], self.commands[row]
            self._reload()
            self.table.selectRow(target)

    def _run(self) -> None:
        command = self._build()
        if command.name and command.command:
            self.owner.run_user_command(command)

    def _apply(self) -> None:
        self.owner.apply_user_commands(self.commands)
        self.accept()


class ColorRulesDialog(QDialog):
    """颜色规则：列表顺序即优先级，靠后的规则覆盖靠上的同色属性。"""

    def __init__(self, owner: Any, rules: list[ColorRule]) -> None:
        super().__init__(owner)
        self.owner = owner
        self.rules = list(rules)
        self.setWindowTitle("颜色规则")
        self.setMinimumSize(940, 620)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(
            hint_label(
                "规则按列表顺序合并：靠下的规则覆盖靠上的同色属性；留空的条件不参与匹配。"
                "颜色格式为 #rrggbb，至少设置前景或背景之一才会生效。"
            )
        )
        self.table = make_table(
            ("掩码", "类型", "大小", "时间", "隐藏", "前景", "背景"),
            widths=(180, 80, 140, 140, 70, 100, 100),
            stretch_columns=(0,),
            parent=self,
        )
        self.table.setMinimumHeight(240)
        self.table.itemSelectionChanged.connect(self._on_select)
        outer.addWidget(self.table, 1)
        form = QGridLayout()
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(6)
        form.setColumnStretch(1, 1)
        form.setColumnStretch(3, 1)
        self.pattern = QLineEdit("*", self)
        self.kind = QComboBox(self)
        for value, label in (("any", "任意"), ("file", "文件"), ("dir", "目录"), ("link", "链接")):
            self.kind.addItem(label, value)
        self.min_size = QLineEdit(self)
        self.max_size = QLineEdit(self)
        self.newer_days = QLineEdit(self)
        self.older_days = QLineEdit(self)
        self.hidden = QComboBox(self)
        for value, label in (("", "任意"), ("yes", "隐藏"), ("no", "非隐藏")):
            self.hidden.addItem(label, value)
        self.foreground = QLineEdit(self)
        self.background = QLineEdit(self)
        form.addWidget(QLabel("掩码"), 0, 0)
        form.addWidget(self.pattern, 0, 1)
        form.addWidget(QLabel("类型"), 0, 2)
        form.addWidget(self.kind, 0, 3)
        form.addWidget(QLabel("最小 / 最大字节"), 1, 0)
        size_row = QHBoxLayout()
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.addWidget(self.min_size)
        size_row.addWidget(self.max_size)
        form.addLayout(size_row, 1, 1)
        form.addWidget(QLabel("N 天内 / 前"), 1, 2)
        time_row = QHBoxLayout()
        time_row.setContentsMargins(0, 0, 0, 0)
        time_row.addWidget(self.newer_days)
        time_row.addWidget(self.older_days)
        form.addLayout(time_row, 1, 3)
        form.addWidget(QLabel("隐藏"), 2, 0)
        form.addWidget(self.hidden, 2, 1)
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.addWidget(QLabel("前景"))
        color_row.addWidget(self.foreground)
        pick_fg = button("…")
        pick_fg.clicked.connect(lambda: self._pick(self.foreground))
        color_row.addWidget(pick_fg)
        color_row.addWidget(QLabel("背景"))
        color_row.addWidget(self.background)
        pick_bg = button("…")
        pick_bg.clicked.connect(lambda: self._pick(self.background))
        color_row.addWidget(pick_bg)
        form.addLayout(color_row, 2, 2, 1, 2)
        outer.addLayout(form)
        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        for text, handler in (
            ("新建", self._new),
            ("保存规则", self._commit),
            ("删除", self._delete),
            ("上移", lambda: self._move(-1)),
            ("下移", lambda: self._move(1)),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        actions.addStretch(1)
        apply_button = button("应用并关闭", kind="primary")
        apply_button.clicked.connect(self._apply)
        actions.addWidget(apply_button)
        close = button("取消")
        close.clicked.connect(self.reject)
        actions.addWidget(close)
        outer.addLayout(actions)
        self._render()

    def _render(self) -> None:
        self.table.setRowCount(len(self.rules))
        for row, rule in enumerate(self.rules):
            self.table.setItem(row, 0, make_table_item(rule.pattern))
            self.table.setItem(row, 1, make_table_item(rule.kind))
            self.table.setItem(row, 2, make_table_item(f"{rule.min_size or ''}~{rule.max_size or ''}"))
            self.table.setItem(row, 3, make_table_item(f"{rule.newer_days or ''}/{rule.older_days or ''}"))
            self.table.setItem(row, 4, make_table_item("" if rule.hidden is None else ("是" if rule.hidden else "否")))
            self.table.setItem(row, 5, make_table_item(rule.foreground))
            self.table.setItem(row, 6, make_table_item(rule.background))

    def _on_select(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self.rules)):
            return
        rule = self.rules[row]
        self.pattern.setText(rule.pattern)
        self.kind.setCurrentIndex(max(self.kind.findData(rule.kind), 0))
        self.min_size.setText("" if rule.min_size is None else str(rule.min_size))
        self.max_size.setText("" if rule.max_size is None else str(rule.max_size))
        self.newer_days.setText("" if rule.newer_days is None else str(rule.newer_days))
        self.older_days.setText("" if rule.older_days is None else str(rule.older_days))
        hidden = "" if rule.hidden is None else ("yes" if rule.hidden else "no")
        self.hidden.setCurrentIndex(max(self.hidden.findData(hidden), 0))
        self.foreground.setText(rule.foreground)
        self.background.setText(rule.background)

    def _new(self) -> None:
        self.pattern.setText("*")
        self.kind.setCurrentIndex(0)
        for widget in (
            self.min_size,
            self.max_size,
            self.newer_days,
            self.older_days,
            self.foreground,
            self.background,
        ):
            widget.clear()
        self.hidden.setCurrentIndex(0)

    def _pick(self, target: QLineEdit) -> None:  # pragma: no cover - 需要真实对话框
        from PySide6.QtWidgets import QColorDialog

        color = QColorDialog.getColor()
        if color.isValid():
            target.setText(color.name())

    def _rule(self) -> Any:
        from fcmd.apps.cmder._ops import ColorRule

        def value(widget: QLineEdit) -> int | None:
            text = widget.text().strip()
            return int(text) if text.lstrip("-").isdigit() else None

        hidden = self.hidden.currentData()
        return ColorRule(
            pattern=self.pattern.text().strip() or "*",
            min_size=value(self.min_size),
            max_size=value(self.max_size),
            kind=self.kind.currentData() or "any",
            hidden=None if hidden == "" else hidden == "yes",
            newer_days=float(self.newer_days.text()) if self.newer_days.text().strip() else None,
            older_days=float(self.older_days.text()) if self.older_days.text().strip() else None,
            foreground=self.foreground.text().strip(),
            background=self.background.text().strip(),
        )

    def _commit(self) -> None:
        rule = self._rule()
        row = self.table.currentRow()
        if 0 <= row < len(self.rules):
            self.rules[row] = rule
        else:
            self.rules.append(rule)
        self._render()

    def _delete(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rules):
            self.rules.pop(row)
            self._render()

    def _move(self, delta: int) -> None:
        row = self.table.currentRow()
        target = row + delta
        if 0 <= row < len(self.rules) and 0 <= target < len(self.rules):
            self.rules[row], self.rules[target] = self.rules[target], self.rules[row]
            self._render()
            self.table.selectRow(target)

    def _apply(self) -> None:
        self.owner.apply_color_rules(self.rules)
        self.accept()


class OpenWithDialog(QDialog):
    """选择外部程序打开当前文件，并记住最近使用。"""

    def __init__(self, owner: Any, entry: FileEntry) -> None:
        super().__init__(owner)
        self.owner = owner
        self.entry = entry
        self.program = ""
        self.setWindowTitle(f"打开方式 - {entry.name}")
        self.setMinimumWidth(560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label(str(entry.path)))
        outer.addWidget(QLabel("最近使用"))
        self.recent = QComboBox(self)
        self.recent.addItems(list(owner.settings.open_with))
        self.recent.activated.connect(lambda _index: self.program_edit.setText(self.recent.currentText()))
        outer.addWidget(self.recent)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.program_edit = QLineEdit(self)
        self.program_edit.setPlaceholderText("可执行文件路径")
        row.addWidget(self.program_edit, 1)
        browse = button("浏览…")
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        outer.addLayout(row)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("打开", kind="primary")
        confirm.clicked.connect(self._confirm)
        actions.addWidget(confirm)
        outer.addLayout(actions)

    def _browse(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen, _ = QFileDialog.getOpenFileName(self, "选择程序")
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


class ArchiveOptionsDialog(QDialog):
    """压缩级别与 zip 密码（写入归档时使用）。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("压缩选项")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QLabel("压缩级别 0–9"))
        self.level = QSpinBox(self)
        self.level.setRange(0, 9)
        self.level.setValue(owner.archive_options.level)
        row.addWidget(self.level)
        row.addStretch(1)
        outer.addLayout(row)
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.addWidget(QLabel("zip 密码"))
        self.password = QLineEdit(owner.archive_options.password, self)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        password_row.addWidget(self.password, 1)
        outer.addLayout(password_row)
        outer.addWidget(hint_label("密码仅对 zip 生效；需要 fcmd[crypto]。"))
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("保存", kind="primary")
        confirm.clicked.connect(self._confirm)
        actions.addWidget(confirm)
        outer.addLayout(actions)

    def _confirm(self) -> None:
        from fcmd.apps.cmder._archwrite import ArchiveWriteOptions

        self.owner.archive_options = ArchiveWriteOptions(level=self.level.value(), password=self.password.text())
        self.accept()


class SplitDialog(QDialog):
    """按大小或份数分割选中文件。"""

    def __init__(self, owner: Any, entry: FileEntry) -> None:
        super().__init__(owner)
        self.owner = owner
        self.entry = entry
        self.setWindowTitle(f"分割 - {entry.name}")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(hint_label(f"{entry.path}（{core.format_size(entry.size)}）"))
        self.mode = QComboBox(self)
        self.mode.addItem("按份数", "parts")
        self.mode.addItem("按大小", "size")
        outer.addWidget(self.mode)
        self.value = QSpinBox(self)
        self.value.setRange(1, 10000)
        self.value.setValue(2)
        outer.addWidget(self.value)
        self.unit = QComboBox(self)
        self.unit.addItems(["MB", "KB", "B"])
        self.unit.setCurrentText("MB")
        outer.addWidget(self.unit)
        actions = QHBoxLayout()
        actions.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("开始", kind="primary")
        confirm.clicked.connect(self._confirm)
        actions.addWidget(confirm)
        outer.addLayout(actions)

    def _confirm(self) -> None:
        factor = {"B": 1, "KB": 1024, "MB": 1024 * 1024}[self.unit.currentText()]
        if self.mode.currentData() == "parts":
            self.owner.start_split(self.entry.path, parts=self.value.value())
        else:
            self.owner.start_split(self.entry.path, part_size=self.value.value() * factor)
        self.accept()


class DuplicateDialog(QDialog):
    """重复文件：按组展示，多余副本可删除或移动到指定目录。"""

    def __init__(self, owner: Any, report: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.report = report
        self.setWindowTitle("查找重复文件")
        self.setMinimumSize(900, 600)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        outer.addWidget(
            hint_label(
                f"扫描 {report.scanned} 个文件，发现 {len(report.groups)} 组重复，"
                f"可回收 {core.format_size(report.wasted)}"
            )
        )
        self.table = make_table(
            ("组", "大小", "保留", "多余副本"),
            widths=(60, 100, 340, 500),
            stretch_columns=(3,),
            parent=self,
        )
        self.table.setMinimumHeight(420)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        outer.addWidget(self.table, 1)
        for row, group in enumerate(report.groups):
            self.table.setItem(row, 0, make_table_item(f"#{row + 1}"))
            self.table.setItem(row, 1, make_table_item(core.format_size(group.size)))
            self.table.setItem(row, 2, make_table_item(str(group.paths[0])))
            self.table.setItem(row, 3, make_table_item("\n".join(str(path) for path in group.paths[1:])))
        actions = QHBoxLayout()
        actions.addStretch(1)
        move = button("移动多余副本到…")
        move.clicked.connect(self._move)
        actions.addWidget(move)
        delete = button("删除多余副本", kind="danger")
        delete.clicked.connect(self._delete)
        actions.addWidget(delete)
        close = button("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        outer.addLayout(actions)

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
        target = QFileDialog.getExistingDirectory(self, "选择移动目标目录")
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


class FilterDialog(QDialog):
    """快速过滤器定义：包含 / 排除掩码。"""

    def __init__(self, owner: Any) -> None:
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("定义过滤器")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)
        self.mask_edit = QLineEdit(owner.settings.filter_definition.include, self)
        self.exclude_edit = QLineEdit(owner.settings.filter_definition.exclude, self)
        self.mask_edit.setPlaceholderText("包含掩码，如 *.py;*.md")
        self.exclude_edit.setPlaceholderText("排除掩码，如 *.min.js")
        outer.addWidget(QLabel("包含掩码"))
        outer.addWidget(self.mask_edit)
        outer.addWidget(QLabel("排除掩码"))
        outer.addWidget(self.exclude_edit)
        actions = QHBoxLayout()
        actions.addStretch(1)
        clear = button("清除过滤器")
        clear.clicked.connect(self._clear)
        actions.addWidget(clear)
        cancel = button("取消")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        confirm = button("应用", kind="primary")
        confirm.clicked.connect(self._apply)
        actions.addWidget(confirm)
        outer.addLayout(actions)

    def _apply(self) -> None:
        from fcmd.apps.cmder._tabs import FilterDefinition

        self.owner.apply_filter_definition(
            FilterDefinition(include=self.mask_edit.text().strip(), exclude=self.exclude_edit.text().strip())
        )
        self.accept()

    def _clear(self) -> None:
        self.owner.clear_filter_definition()
        self.accept()


__all__ += [
    "ArchiveOptionsDialog",
    "ColorRulesDialog",
    "CompareDialog",
    "DuplicateDialog",
    "FilterDialog",
    "OpenWithDialog",
    "SplitDialog",
    "SyncDialog",
    "UserCommandsDialog",
]
