"""renamerw 的 Qt 主窗口与入口。

窗口只做编排：规则求值、预览、冲突检测、两阶段执行与撤销全部调用
:mod:`fcmd.apps.renamer._core`（与 ``fcmd renamer`` 共用的同一套口径），
GUI 不重写任何重命名逻辑。

布局口径：上半「文件」占大头（名称 / 新名 / 状态三列表格 + 底部执行条），
下半「规则」固定区域（类型与作用域常驻，其余字段按类型显隐，规则表 4 行高）。
预览表的可执行 / 未变化 / 冲突 / 非法行按主题调色板上色，切换主题即时重渲染。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from fcmd import __version__
from fcmd.apps.renamer._core import (
    ANCHOR_AFTER,
    ANCHOR_BEFORE,
    ANCHOR_END,
    ANCHOR_INDEX,
    ANCHOR_START,
    SORT_NAME,
    STATUS_CONFLICT,
    STATUS_INVALID,
    STATUS_UNCHANGED,
    RenamePreview,
    RenameReport,
    RenameRule,
    RuleError,
    build_preview,
    collect_targets,
    execute_plan,
    format_preview_status,
    make_target,
    preview_to_plan,
    undo_report,
    unique_paths,
    validate_rules,
)
from fcmd.apps.renamer._examples import EXAMPLES, Example
from fcmd.apps.renamer.renamerw import (
    ANCHOR_TEXT,
    CASE_TEXT,
    FIELD_TEXT,
    KIND_HINT,
    KIND_TEXT,
    MIN_HEIGHT,
    MIN_WIDTH,
    SORT_TEXT,
    STATUS_TEXT,
    RenamerSettings,
    _label_value,
    load_settings,
    rule_to_row,
    rules_from_text,
    rules_to_text,
    save_settings,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui.app import run_window
from fcmd.gui.help import HelpExample, HelpMenuMixin
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.uistate import dialog_remember, dialog_start
from fcmd.gui.widgets import (
    Card,
    button,
    confirm,
    hint_label,
    make_splitter,
    make_table,
    make_table_item,
    messages,
    pick_dir,
    pick_file,
    pick_files,
    save_file,
)
from fcmd.shared.helpdoc import HelpDoc, load_help_doc

__all__ = ["RenamerWindow", "run_qt"]

DEFAULT_SIZE = (1120, 760)

_FILE_HEADERS = ("名称", "新名", "状态")
_FILE_WIDTHS = (320, 460, 90)
_RULE_HEADERS = ("类型", "作用域", "规则参数", "说明")
_RULE_WIDTHS = (70, 70, 380, 420)
_STATUS_COLORS = {STATUS_CONFLICT: "danger", STATUS_INVALID: "warning", STATUS_UNCHANGED: "muted"}


class RenamerWindow(HelpMenuMixin, QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """文件预览表 + 规则流水线表单 + 规则表。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._selected = [Path(item) for item in self._settings.targets]
        try:
            self._rules = list(rules_from_text(self._settings.rules_json))
        except RuleError:
            self._rules = []
        self._preview: RenamePreview | None = None
        self._last_report: RenameReport | None = None
        self._row_targets: list[int] = []
        self._form_rows: dict[str, tuple[QLabel, QWidget]] = {}
        self._syncing_form = False
        self._help_table: Any = None
        super().__init__(
            title=f"renamer - 批量重命名 v{__version__}",
            config_name="renamerw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
        )
        if settings_path is not None:
            self._settings_path = settings_path
            self._raw_settings = _guiconfig.read_settings(settings_path) or {}
        self._build_extra_menu()
        self._build()
        self._sync_rule_form()
        self._refresh_rule_tree()
        self._selected = self._expand_paths(self._selected)
        self._refresh_preview()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        splitter = make_splitter(Qt.Orientation.Vertical, sizes=(460, 330), parent=self)
        splitter.addWidget(self._build_files_card())
        splitter.addWidget(self._build_rules_card())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        self.add(splitter, 1)

    def _build_files_card(self) -> Card:
        """文件操作栏 + 名称 / 新名 / 状态预览表 + 执行条。"""
        card = Card("文件", self)
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(6)
        for text, slot in (
            ("添加文件…", self._add_files),
            ("添加目录…", self._add_dir),
            ("移除选中", self._remove_targets),
            ("清空", self._clear_targets),
        ):
            widget = button(text)
            widget.clicked.connect(slot)
            toolbar.addWidget(widget)
        toolbar.addStretch(1)
        self._recursive_check = QCheckBox("递归子目录", card)
        self._recursive_check.setChecked(self._settings.recursive)
        self._recursive_check.toggled.connect(lambda _checked: self._refresh_preview())
        self._dirs_check = QCheckBox("包含目录", card)
        self._dirs_check.setChecked(self._settings.include_dirs)
        self._dirs_check.toggled.connect(lambda _checked: self._refresh_preview())
        self._sort_combo = QComboBox(card)
        self._sort_combo.addItems(list(SORT_TEXT.values()))
        self._sort_combo.setCurrentText(SORT_TEXT.get(self._settings.sort_key, SORT_TEXT[SORT_NAME]))
        self._sort_combo.currentIndexChanged.connect(lambda _index: self._refresh_preview())
        toolbar.addWidget(self._recursive_check)
        toolbar.addSpacing(8)
        toolbar.addWidget(self._dirs_check)
        toolbar.addSpacing(8)
        toolbar.addWidget(QLabel("计数器排序", card))
        toolbar.addWidget(self._sort_combo)
        card.body.addLayout(toolbar)

        self._file_table = make_table(
            _FILE_HEADERS,
            widths=_FILE_WIDTHS,
            stretch_columns=(0, 1),
            parent=card,
        )
        self._file_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._file_table.setMinimumHeight(120)
        card.body.addWidget(self._file_table, 1)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(6)
        execute = button("执行改名", kind="primary")
        execute.clicked.connect(self._execute)
        undo = button("撤销上次")
        undo.clicked.connect(self._undo)
        self._show_old_check = QCheckBox("旧名", card)
        self._show_old_check.setChecked(True)
        self._show_old_check.toggled.connect(self._toggle_old_column)
        bar.addWidget(execute)
        bar.addWidget(undo)
        bar.addWidget(self._show_old_check)
        bar.addStretch(1)
        card.body.addLayout(bar)
        return card

    def _build_rules_card(self) -> Card:
        """类型 / 作用域选择、按类型显隐的字段行与规则表。"""
        card = Card("规则（按顺序执行；双击列表可载入表单）", self)
        card.body.addLayout(self._build_selector(card))
        card.body.addLayout(self._build_option_row(card))
        card.body.addLayout(self._build_text_row(card))
        card.body.addLayout(self._build_rule_actions())

        self._rule_table = make_table(
            _RULE_HEADERS,
            widths=_RULE_WIDTHS,
            stretch_columns=(2, 3),
            parent=card,
        )
        self._rule_table.setMinimumHeight(90)
        self._rule_table.cellDoubleClicked.connect(lambda _row, _column: self._load_selected_rule())
        card.body.addWidget(self._rule_table)
        return card

    def _build_selector(self, parent: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._kind_combo = QComboBox(parent)
        self._kind_combo.addItems(list(KIND_TEXT.values()))
        self._kind_combo.currentIndexChanged.connect(lambda _index: self._sync_rule_form())
        self._field_combo = QComboBox(parent)
        self._field_combo.addItems(list(FIELD_TEXT.values()))
        row.addWidget(QLabel("类型", parent))
        row.addWidget(self._kind_combo)
        row.addSpacing(10)
        row.addWidget(QLabel("作用域", parent))
        row.addWidget(self._field_combo)
        row.addStretch(1)
        return row

    def _build_option_row(self, parent: QWidget) -> QHBoxLayout:
        """按类型显隐的数值 / 下拉字段；控件只创建一次，切换类型只切显隐。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self._occurrence_spin = _spin(parent, 0, 99, 0)
        self._ignore_case_check = QCheckBox("忽略大小写", parent)
        self._mode_combo = QComboBox(parent)
        self._mode_combo.addItems(list(CASE_TEXT.values()))
        self._mode_combo.setCurrentText(CASE_TEXT["lower"])
        self._start_spin = _spin(parent, -9999, 9999, 1)
        self._step_spin = _spin(parent, -99, 99, 1)
        self._digits_spin = _spin(parent, 1, 32, 3)
        self._anchor_combo = QComboBox(parent)
        self._anchor_combo.addItems(list(ANCHOR_TEXT.values()))
        self._anchor_combo.currentIndexChanged.connect(lambda _index: self._update_anchor_states())
        self._anchor_text_edit = QLineEdit(parent)
        self._position_spin = _spin(parent, -9999, 9999, 0)

        self._add_pair(row, parent, "occurrence", "第几个匹配", self._occurrence_spin)
        row.addWidget(self._ignore_case_check)
        self._form_rows["ignore_case"] = (self._ignore_case_check, self._ignore_case_check)
        self._add_pair(row, parent, "mode", "模式", self._mode_combo)
        self._add_pair(row, parent, "start", "起始", self._start_spin)
        self._add_pair(row, parent, "step", "步长", self._step_spin)
        self._add_pair(row, parent, "digits", "位数", self._digits_spin)
        self._add_pair(row, parent, "anchor", "锚点", self._anchor_combo)
        self._add_pair(row, parent, "anchor_text", "锚点文本", self._anchor_text_edit)
        self._add_pair(row, parent, "position", "位置", self._position_spin)
        row.addStretch(1)
        return row

    def _add_pair(self, row: QHBoxLayout, parent: QWidget, key: str, text: str, widget: QWidget) -> None:
        label = QLabel(text, parent)
        row.addWidget(label)
        row.addWidget(widget)
        self._form_rows[key] = (label, widget)

    def _build_text_row(self, parent: QWidget) -> QGridLayout:
        """正则 / 模板行与单文本框行；两行按规则类型二选一显示。"""
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        self._pattern_label = QLabel("正则", parent)
        self._pattern_edit = QLineEdit(parent)
        self._replacement_label = QLabel("替换 / 模板", parent)
        self._replacement_edit = QLineEdit(parent)
        grid.addWidget(self._pattern_label, 0, 0)
        grid.addWidget(self._pattern_edit, 0, 1)
        grid.addWidget(self._replacement_label, 0, 2)
        grid.addWidget(self._replacement_edit, 0, 3)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)

        self._text_label = QLabel("插入文本", parent)
        self._text_edit = QLineEdit(parent)
        grid.addWidget(self._text_label, 1, 0)
        grid.addWidget(self._text_edit, 1, 1, 1, 3)
        return grid

    def _build_rule_actions(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for text, slot in (
            ("添加规则", self._add_rule),
            ("更新选中", self._update_rule),
            ("删除", self._remove_rule),
            ("上移", lambda: self._move_rule(-1)),
            ("下移", lambda: self._move_rule(1)),
        ):
            widget = button(text)
            widget.clicked.connect(slot)
            row.addWidget(widget)
        row.addStretch(1)
        self._rule_hint = hint_label(KIND_HINT["replace"], wrap=False)
        row.addWidget(self._rule_hint)
        return row

    def _build_extra_menu(self) -> None:
        """帮助菜单：使用帮助（公共组件）+ 规则导入 / 导出。"""
        doc = load_help_doc("renamerw", title="renamer 批量重命名") or HelpDoc(
            tool="renamerw",
            title="renamer 批量重命名",
            summary="规则流水线批量生成新文件名，支持预览、两阶段执行与本次撤销。",
        )
        self.install_help_menu(
            doc,
            examples=tuple(HelpExample(example.name, example.summary) for example in EXAMPLES),
            on_example=self._apply_example_row,
        )
        help_menu = self.help_menu
        if help_menu is None:
            return
        # 规则导入 / 导出排在「使用帮助」之后、公共任务日志之前。
        anchor = self._help_separator or self.log_action
        for text, slot in (("载入规则…", self._load_rules_file), ("保存规则…", self._save_rules_file)):
            action = QAction(text, self)
            action.triggered.connect(slot)
            if anchor is not None:
                help_menu.insertAction(anchor, action)
            else:
                help_menu.addAction(action)

    # ------------------------------------------------------------------ #
    # 规则表单
    # ------------------------------------------------------------------ #
    def _kind(self) -> str:
        return _label_value(self._kind_combo.currentText(), KIND_TEXT, "replace")

    def _field(self) -> str:
        return _label_value(self._field_combo.currentText(), FIELD_TEXT, "stem")

    def _anchor(self) -> str:
        return _label_value(self._anchor_combo.currentText(), ANCHOR_TEXT, ANCHOR_START)

    def _mode(self) -> str:
        return _label_value(self._mode_combo.currentText(), CASE_TEXT, "lower")

    def _sort_key(self) -> str:
        return _label_value(self._sort_combo.currentText(), SORT_TEXT, SORT_NAME)

    def _sync_rule_form(self) -> None:
        """按当前规则类型切换字段显隐，并把锚点下拉限制到合法取值。"""
        if self._syncing_form:
            return
        kind = self._kind()
        self._rule_hint.setText(KIND_HINT.get(kind, ""))
        self._syncing_form = True
        self._fill_anchor_combo(kind)
        self._syncing_form = False

        for key, visible in (
            ("occurrence", kind in ("replace", "move")),
            ("ignore_case", kind == "replace"),
            ("mode", kind == "case"),
            ("start", kind == "sequence"),
            ("step", kind == "sequence"),
            ("digits", kind == "sequence"),
            ("anchor", kind in ("move", "insert", "sequence")),
            ("anchor_text", kind in ("move", "insert")),
            ("position", kind in ("move", "insert")),
        ):
            self._set_form_visible(key, visible)

        two_fields = kind in ("replace", "reorder")
        one_field = kind in ("move", "insert", "sequence")
        for widget in (self._pattern_label, self._pattern_edit, self._replacement_label, self._replacement_edit):
            widget.setVisible(two_fields)
        self._replacement_label.setText("替换 / 模板" if kind == "replace" else "重排模板")
        self._text_label.setVisible(one_field)
        self._text_edit.setVisible(one_field)
        if one_field:
            self._text_label.setText({"move": "正则", "insert": "插入文本", "sequence": "分隔符（可空）"}[kind])
        self._update_anchor_states()

    def _fill_anchor_combo(self, kind: str) -> None:
        """重建锚点下拉；计数器只允许开头 / 结尾。"""
        current = self._anchor_combo.currentText()
        values = (
            [ANCHOR_TEXT[ANCHOR_START], ANCHOR_TEXT[ANCHOR_END]] if kind == "sequence" else list(ANCHOR_TEXT.values())
        )
        self._anchor_combo.clear()
        self._anchor_combo.addItems(values)
        index = self._anchor_combo.findText(current)
        self._anchor_combo.setCurrentIndex(index if index >= 0 else 0)

    def _set_form_visible(self, key: str, visible: bool) -> None:
        label, widget = self._form_rows[key]
        label.setVisible(visible)
        widget.setVisible(visible)

    def _update_anchor_states(self) -> None:
        """锚点文本仅「锚点前 / 后」可用，位置仅「指定位置」可用。"""
        anchor = self._anchor()
        self._anchor_text_edit.setEnabled(anchor in (ANCHOR_BEFORE, ANCHOR_AFTER))
        self._position_spin.setEnabled(anchor == ANCHOR_INDEX)

    def _rule_from_form(self) -> RenameRule:
        """从表单构造规则；只取当前类型用得到的字段，其余保持默认值。"""
        kind = self._kind()
        return RenameRule(
            kind=kind,
            pattern=self._pattern_edit.text(),
            replacement=self._replacement_edit.text(),
            field=self._field(),
            ignore_case=self._ignore_case_check.isChecked(),
            occurrence=self._occurrence_spin.value() if kind in ("replace", "move") else 0,
            anchor=self._anchor(),
            anchor_text=self._anchor_text_edit.text(),
            position=self._position_spin.value() if kind in ("move", "insert") else 0,
            mode=self._mode(),
            text=self._text_edit.text(),
            start=self._start_spin.value() if kind == "sequence" else 1,
            step=self._step_spin.value() if kind == "sequence" else 1,
            digits=self._digits_spin.value() if kind == "sequence" else 3,
        )

    def _refresh_rule_tree(self) -> None:
        row = self._rule_table.currentRow()
        self._rule_table.setRowCount(len(self._rules))
        for index, rule in enumerate(self._rules):
            for column, text in enumerate(rule_to_row(rule)):
                self._rule_table.setItem(index, column, make_table_item(text))
        if 0 <= row < len(self._rules):
            self._rule_table.setCurrentCell(row, 0)

    def _add_rule(self) -> None:
        try:
            rule = self._rule_from_form()
            validate_rules([rule])
        except RuleError as exc:
            messages.error(self, "规则错误", str(exc))
            return
        self._rules.append(rule)
        self._refresh_rule_tree()
        self._refresh_preview()

    def _update_rule(self) -> None:
        row = self._rule_table.currentRow()
        if row < 0 or row >= len(self._rules):
            return
        try:
            rule = self._rule_from_form()
            validate_rules([rule])
        except RuleError as exc:
            messages.error(self, "规则错误", str(exc))
            return
        self._rules[row] = rule
        self._refresh_rule_tree()
        self._refresh_preview()

    def _remove_rule(self) -> None:
        rows = sorted({index.row() for index in self._rule_table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            return
        for row in rows:
            if 0 <= row < len(self._rules):
                del self._rules[row]
        self._refresh_rule_tree()
        self._refresh_preview()

    def _move_rule(self, offset: int) -> None:
        row = self._rule_table.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < len(self._rules):
            return
        self._rules[row], self._rules[target] = self._rules[target], self._rules[row]
        self._refresh_rule_tree()
        self._rule_table.setCurrentCell(target, 0)
        self._refresh_preview()

    def _load_selected_rule(self) -> None:
        """把选中规则回填到表单（双击规则表触发）。"""
        row = self._rule_table.currentRow()
        if row < 0 or row >= len(self._rules):
            return
        rule = self._rules[row]
        self._syncing_form = True
        _set_combo(self._kind_combo, KIND_TEXT, rule.kind)
        _set_combo(self._field_combo, FIELD_TEXT, rule.field)
        _set_combo(self._mode_combo, CASE_TEXT, rule.mode)
        self._pattern_edit.setText(rule.pattern)
        self._replacement_edit.setText(rule.replacement)
        self._ignore_case_check.setChecked(rule.ignore_case)
        self._occurrence_spin.setValue(rule.occurrence)
        self._anchor_text_edit.setText(rule.anchor_text)
        self._position_spin.setValue(rule.position)
        self._text_edit.setText(rule.text)
        self._start_spin.setValue(rule.start)
        self._step_spin.setValue(rule.step)
        self._digits_spin.setValue(rule.digits)
        self._syncing_form = False
        self._sync_rule_form()
        _set_combo(self._anchor_combo, ANCHOR_TEXT, rule.anchor)
        self._update_anchor_states()

    # ------------------------------------------------------------------ #
    # 文件列表
    # ------------------------------------------------------------------ #
    def _append_targets(self, candidates: list[Path]) -> int:
        """把候选路径并入目标列表（按路径去重），返回新增数量。"""
        known = {os.path.normcase(str(path)) for path in self._selected}
        added = 0
        for path in candidates:
            key = os.path.normcase(str(path))
            if key in known:
                continue
            known.add(key)
            self._selected.append(path)
            added += 1
        return added

    def _expand_paths(self, paths: list[Path]) -> list[Path]:
        """按递归 / 包含目录选项把目录展开为文件，文件原样保留。"""
        expanded: list[Path] = []
        for path in paths:
            if not path.is_dir():
                expanded.append(path)
                continue
            try:
                targets = collect_targets(
                    [path],
                    recursive=self._recursive_check.isChecked(),
                    include_dirs=self._dirs_check.isChecked(),
                )
            except OSError:
                expanded.append(path)
                continue
            expanded.extend(target.path for target in targets)
        return list(unique_paths(expanded))

    def _add_files(self) -> None:
        selected = pick_files(
            self,
            "选择待重命名文件",
            dialog_start(self._settings.last_open_dir),
        )
        if not selected:
            return
        self._settings.last_open_dir = dialog_remember(selected[0])
        self._append_targets([Path(item) for item in selected])
        self._refresh_preview()

    def _add_dir(self) -> None:
        selected = pick_dir(
            self,
            "选择目录（按当前递归 / 包含目录选项展开）",
            dialog_start(self._settings.last_open_dir),
        )
        if not selected:
            return
        self._settings.last_open_dir = dialog_remember(selected)
        added = self._append_targets(self._expand_paths([Path(selected)]))
        self._refresh_preview()
        if not added:
            self.set_status("目录中没有可添加的文件；可勾选「递归子目录」或「包含目录」后重新添加")

    def _remove_targets(self) -> None:
        rows = sorted({index.row() for index in self._file_table.selectionModel().selectedRows()}, reverse=True)
        for row in rows:
            if 0 <= row < len(self._row_targets):
                index = self._row_targets[row]
                if 0 <= index < len(self._selected):
                    del self._selected[index]
        self._refresh_preview()

    def _clear_targets(self) -> None:
        self._selected.clear()
        self._refresh_preview()

    def _toggle_old_column(self, checked: bool) -> None:
        """「旧名」取消勾选时隐藏名称列，只留新名与状态。"""
        self._file_table.setColumnHidden(0, not checked)

    # ------------------------------------------------------------------ #
    # 预览
    # ------------------------------------------------------------------ #
    def _append_file_row(self, index: int, old: str, new: str, status_text: str, status: str) -> None:
        row = self._file_table.rowCount()
        self._file_table.insertRow(row)
        color = self._status_color(status)
        for column, text in enumerate((old, new, status_text)):
            cell = make_table_item(text)
            if color is not None:
                cell.setForeground(color)
            self._file_table.setItem(row, column, cell)
        self._row_targets.append(index)

    def _status_color(self, status: str) -> QColor | None:
        key = _STATUS_COLORS.get(status)
        return None if key is None else QColor(getattr(self.theme().palette, key))

    def _render_file_table(self) -> None:
        """按当前目标与预览重建文件表，不重新计算预览。"""
        self._file_table.setRowCount(0)
        self._row_targets.clear()
        preview = self._preview
        if preview is None or preview.error:
            for index, path in enumerate(self._selected):
                self._append_file_row(index, path.name, "", "", "")
            return
        index_by_path = {os.path.normcase(str(path)): index for index, path in enumerate(self._selected)}
        for item in preview.items:
            index = index_by_path.get(os.path.normcase(str(item.source)))
            if index is None:
                continue
            self._append_file_row(
                index,
                item.old_name,
                item.new_name,
                STATUS_TEXT.get(item.status, item.status),
                item.status,
            )

    def _refresh_preview(self) -> None:
        if not self._selected or not self._rules:
            self._preview = None
            self._render_file_table()
            if not self._selected:
                self.set_status("请先添加文件或目录")
            else:
                self.set_status(f"共 {len(self._selected)} 项，添加规则后自动预览")
            return
        preview = build_preview(
            tuple(make_target(path) for path in self._selected),
            self._rules,
            self._sort_key(),
        )
        self._preview = preview
        self._render_file_table()
        self.set_status(f"规则错误：{preview.error}" if preview.error else format_preview_status(preview))

    def set_theme(self, theme: Any) -> None:
        """切换主题后重渲染预览表，让状态行颜色跟随新调色板。"""
        super().set_theme(theme)
        if hasattr(self, "_file_table"):
            self._render_file_table()

    # ------------------------------------------------------------------ #
    # 执行与撤销
    # ------------------------------------------------------------------ #
    def _execute(self) -> None:
        self._refresh_preview()
        preview = self._preview
        if preview is None:
            return
        if preview.error:
            messages.error(self, "规则错误", preview.error)
            return
        plan = preview_to_plan(preview)
        if not plan.changes:
            messages.info(self, "没有可执行项", "当前没有状态为「可执行」的改名。")
            return
        if not confirm(self, "确认改名", f"将改名 {len(plan.changes)} 项，继续？"):
            return
        report = execute_plan(plan)
        self._last_report = report
        renamed = dict(report.completed)
        self._selected = [renamed.get(path, path) for path in self._selected]
        messages.info(
            self,
            "改名结果",
            f"成功 {len(report.completed)} 项，失败 {len(report.errors)} 项。",
        )
        self._refresh_preview()

    def _undo(self) -> None:
        if self._last_report is None or not self._last_report.completed:
            messages.info(self, "无法撤销", "没有本次运行可撤销的改名。")
            return
        restored = {target: source for source, target in self._last_report.completed}
        report = undo_report(self._last_report)
        self._last_report = None
        self._selected = [restored.get(path, path) for path in self._selected]
        messages.info(
            self,
            "撤销结果",
            f"已恢复 {len(report.completed)} 项，失败 {len(report.errors)} 项。",
        )
        self._refresh_preview()

    # ------------------------------------------------------------------ #
    # 规则文件 / 帮助
    # ------------------------------------------------------------------ #
    def _save_rules_file(self) -> None:
        selected = save_file(
            self,
            "保存规则",
            str(Path(start) / "rename-rules.json")
            if (start := dialog_start(self._settings.last_save_dir))
            else "rename-rules.json",
            "JSON 规则 (*.json);;所有文件 (*.*)",
        )
        if not selected:
            return
        self._settings.last_save_dir = dialog_remember(selected)
        try:
            Path(selected).write_text(rules_to_text(self._rules), encoding="utf-8")
        except OSError as exc:
            messages.error(self, "保存失败", str(exc))
            return
        self.set_status(f"规则已保存：{selected}")

    def _load_rules_file(self) -> None:
        selected = pick_file(
            self,
            "载入规则",
            dialog_start(self._settings.last_open_dir),
            "JSON 规则 (*.json);;所有文件 (*.*)",
        )
        if not selected:
            return
        self._settings.last_open_dir = dialog_remember(selected)
        try:
            self._rules = list(rules_from_text(Path(selected).read_text(encoding="utf-8")))
        except (OSError, RuleError) as exc:
            messages.error(self, "载入失败", str(exc))
            return
        self._refresh_rule_tree()
        self._refresh_preview()

    def _show_help(self) -> None:
        """打开公共帮助窗口：Markdown 正文 + 典型示例表。"""
        self.show_help()
        dialog = self._help_dialog
        self._help_table = dialog.example_table if dialog is not None else None

    def _apply_example_row(self, row: int) -> None:
        """帮助窗口双击示例行：载入对应规则集。"""
        if 0 <= row < len(EXAMPLES):
            self._apply_example(EXAMPLES[row])

    def _load_example_row(self, table: Any) -> None:
        row = table.currentRow()
        if 0 <= row < len(EXAMPLES):
            self._apply_example(EXAMPLES[row])

    def _apply_example(self, example: Example) -> bool:
        """把示例规则写入规则表；已有规则时先确认覆盖。"""
        if self._rules and not confirm(
            self, "载入示例", f"用示例「{example.name}」替换当前 {len(self._rules)} 条规则？"
        ):
            return False
        self._rules = list(example.rules)
        self._refresh_rule_tree()
        self._refresh_preview()
        return True

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _persist(self, geometry: str) -> None:
        """关闭时保存目标、规则与窗口位置。"""
        save_settings(
            RenamerSettings(
                targets=[str(path) for path in self._selected],
                rules_json=rules_to_text(self._rules),
                recursive=self._recursive_check.isChecked(),
                include_dirs=self._dirs_check.isChecked(),
                sort_key=self._sort_key(),
                geometry=geometry,
            ),
            self._settings_path,
        )


def _spin(parent: QWidget, minimum: int, maximum: int, value: int) -> QSpinBox:
    spin = QSpinBox(parent)
    spin.setRange(minimum, maximum)
    spin.setValue(value)
    return spin


def _set_combo(combo: QComboBox, labels: dict[str, str], value: str) -> None:
    """按枚举值选中下拉项；未知值忽略。"""
    index = combo.findText(labels.get(value, value))
    if index >= 0:
        combo.setCurrentIndex(index)


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(lambda: RenamerWindow(settings_path), app_name="renamerw")
