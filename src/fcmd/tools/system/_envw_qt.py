"""envw 的 Qt 主窗口与入口。

窗口只做编排：作用域读取 / 写回、备份格式、PATH 拆分与变量引用展开全部复用
``fcmd.tools.system.envw`` 的纯逻辑；界面负责搜索、增删改、详情预览与保存确认。
PATH 逐条编辑与变量编辑各用一个模态 ``QDialog``，保存 / 恢复仍走原有后端。

布局口径：顶部作用域与搜索固定；变量表占满剩余高度（纵向滚动在表内）；详情区
两行只读文本；操作按钮固定在底部，保存按钮单独靠右；窗口缩小时整页滚动。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, button, hint_label, make_table, make_table_item, mono_font
from fcmd.tools.system import envw
from fcmd.tools.system.envw import (
    MIN_HEIGHT,
    MIN_WIDTH,
    SCOPE_LABELS,
    SCOPE_PROCESS,
    SCOPE_SYSTEM,
    EnvSettings,
    _is_windows,
    available_scopes,
    default_path_separator,
    expand_value,
    join_path_entries,
    load_backup,
    load_settings,
    parse_path_entries,
    read_scope,
    save_backup,
    save_scope,
    save_settings,
    scope_writable,
    validate_name,
    validate_value,
)

__all__ = ["EnvQtWindow", "run_qt"]

DEFAULT_SIZE = (1000, 700)


class PathEditorDialog(QDialog):  # pragma: no cover - 模态对话框
    """PATH 逐条编辑：增删、上下移、去重、浏览目录。"""

    def __init__(
        self,
        parent: QWidget,
        entries: list[str],
        *,
        separator: str,
        windows: bool,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑路径")
        self.setMinimumSize(560, 320)
        self.resize(720, 420)
        self._entries = [entry for entry in entries if entry]
        self._separator = separator
        self._windows = windows
        self.result: list[str] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        self._list = QListWidget(self)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setAlternatingRowColors(True)
        outer.addWidget(self._list, 1)

        input_row = QHBoxLayout()
        input_row.setContentsMargins(0, 0, 0, 0)
        input_row.setSpacing(6)
        self._entry = QLineEdit(self)
        self._entry.setPlaceholderText("输入路径后回车，或点「浏览…」选择目录")
        self._entry.returnPressed.connect(self._add)
        input_row.addWidget(self._entry, 1)
        add_button = button("添加")
        add_button.clicked.connect(self._add)
        input_row.addWidget(add_button)
        browse_button = button("浏览…")
        browse_button.clicked.connect(self._browse)
        input_row.addWidget(browse_button)
        outer.addLayout(input_row)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        for text, handler in (
            ("删除", self._remove),
            ("上移", self._move_up),
            ("下移", self._move_down),
            ("去重", self._deduplicate),
        ):
            widget = button(text)
            widget.clicked.connect(handler)
            buttons.addWidget(widget)
        buttons.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self._cancel)
        buttons.addWidget(cancel)
        confirm = button("确定", kind="primary")
        confirm.clicked.connect(self._confirm)
        buttons.addWidget(confirm)
        outer.addLayout(buttons)
        self._refresh()

    def _refresh(self) -> None:
        self._list.clear()
        self._list.addItems(self._entries)

    def _selected_indexes(self) -> list[int]:
        return sorted(index.row() for index in self._list.selectedIndexes())

    def _add(self) -> None:
        text = self._entry.text().strip()
        if not text:
            return
        self._entries.append(text.strip('"'))
        self._entry.clear()
        self._refresh()
        self._list.setCurrentRow(len(self._entries) - 1)

    def _browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择目录")
        if not directory:
            return
        self._entries.append(directory)
        self._refresh()
        self._list.setCurrentRow(len(self._entries) - 1)

    def _remove(self) -> None:
        for index in reversed(self._selected_indexes()):
            del self._entries[index]
        self._refresh()

    def _move_up(self) -> None:
        indexes = self._selected_indexes()
        if not indexes or indexes[0] == 0:
            return
        for index in indexes:
            self._entries[index - 1], self._entries[index] = self._entries[index], self._entries[index - 1]
        self._refresh()
        for index in (index - 1 for index in indexes):
            self._list.item(index).setSelected(True)

    def _move_down(self) -> None:
        indexes = self._selected_indexes()
        if not indexes or indexes[-1] == len(self._entries) - 1:
            return
        for index in reversed(indexes):
            self._entries[index], self._entries[index + 1] = self._entries[index + 1], self._entries[index]
        self._refresh()
        for index in (index + 1 for index in indexes):
            self._list.item(index).setSelected(True)

    def _deduplicate(self) -> None:
        seen: set[str] = set()
        unique: list[str] = []
        for entry in self._entries:
            key = entry.casefold() if self._windows else entry
            if key in seen:
                continue
            seen.add(key)
            unique.append(entry)
        self._entries = unique
        self._refresh()

    def _confirm(self) -> None:
        self.result = list(self._entries)
        self.accept()

    def _cancel(self) -> None:
        self.result = None
        self.reject()


class VariableDialog(QDialog):  # pragma: no cover - 模态对话框
    """新建 / 编辑变量：校验名称与值，PATH 提供路径编辑入口。"""

    def __init__(
        self,
        parent: QWidget,
        *,
        name: str,
        value: str,
        names: dict[str, str],
        platform: str,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑变量" if name else "新建变量")
        self.setMinimumSize(520, 340)
        self.resize(640, 400)
        self._original_name = name
        self._names = names
        self._platform = platform
        self._separator = default_path_separator(platform)
        self.result: tuple[str, str] | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._name_edit = QLineEdit(name, self)
        self._name_edit.textChanged.connect(lambda _text: self._update_path_button())
        grid.addWidget(QLabel("变量名："), 0, 0)
        grid.addWidget(self._name_edit, 0, 1)
        grid.addWidget(QLabel("变量值："), 1, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self._value_text = QPlainTextEdit(self)
        self._value_text.setMinimumHeight(180)
        self._value_text.setPlainText(value)
        grid.addWidget(self._value_text, 1, 1)
        grid.setRowStretch(1, 1)
        outer.addLayout(grid, 1)

        self._hint = hint_label()
        outer.addWidget(self._hint)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        self._path_button = button("路径编辑…")
        self._path_button.clicked.connect(self._edit_path)
        buttons.addWidget(self._path_button)
        buttons.addStretch(1)
        cancel = button("取消")
        cancel.clicked.connect(self._cancel)
        buttons.addWidget(cancel)
        confirm = button("确定", kind="primary")
        confirm.clicked.connect(self._confirm)
        buttons.addWidget(confirm)
        outer.addLayout(buttons)
        self._update_path_button()

    def _update_path_button(self) -> None:
        is_path = self._name_edit.text().strip().upper() == "PATH"
        self._path_button.setEnabled(is_path)
        self._hint.setText(
            "PATH 支持逐条编辑（分号/冒号分隔），也可直接修改下方文本。"
            if is_path
            else "变量名区分平台规则；保存后对新进程生效。"
        )

    def _edit_path(self) -> None:
        dialog = PathEditorDialog(
            self,
            parse_path_entries(self._value_text.toPlainText(), self._separator),
            separator=self._separator,
            windows=_is_windows(self._platform),
        )
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.result is not None:
            self._value_text.setPlainText(join_path_entries(dialog.result, self._separator))

    def _confirm(self) -> None:
        name = self._name_edit.text().strip()
        error = validate_name(name, platform=self._platform)
        if error is not None:
            QMessageBox.warning(self, "变量名无效", error)
            return
        if name != self._original_name and name in self._names:
            QMessageBox.warning(self, "变量名重复", f"变量 {name} 已存在")
            return
        value = self._value_text.toPlainText()
        error = validate_value(value)
        if error is not None:
            QMessageBox.warning(self, "变量值无效", error)
            return
        self.result = (name, value)
        self.accept()

    def _cancel(self) -> None:
        self.result = None
        self.reject()


class EnvQtWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """作用域 / 搜索 + 变量表 + 详情 + 操作条的环境变量编辑器。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._settings = load_settings(settings_path)
        self._own_settings_path = settings_path
        self._platform = sys.platform
        self._profile_path = None if _is_windows(self._platform) else envw.shell_profile_path()
        self._scopes = available_scopes(self._platform)
        self._scope = self._settings.scope if self._settings.scope in self._scopes else self._scopes[0]
        self._separator = default_path_separator(self._platform)
        self._variables: dict[str, str] = {}
        self._original: dict[str, str] = {}
        super().__init__(
            title=f"环境变量编辑器 v{envw.__version__}",
            config_name="envw",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=self._confirm_close,
            persist=self._persist_settings,
        )
        self._build_menus()
        self._build_widgets()
        self._load_scope()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_menus(self) -> None:
        menubar = self.menuBar()
        help_menu = menubar.actions()[-1].menu()
        about_action = help_menu.actions()[-1]
        for text, handler in (("备份全部作用域…", self._backup), ("从备份恢复…", self._restore)):
            action = QAction(text, self)
            action.triggered.connect(handler)
            help_menu.insertAction(about_action, action)
        help_menu.insertSeparator(about_action)

    def _build_widgets(self) -> None:
        top = Card("作用域与搜索")
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(QLabel("作用域："))
        self._scope_combo = QComboBox(top)
        for scope in self._scopes:
            self._scope_combo.addItem(SCOPE_LABELS[scope], scope)
        index = self._scope_combo.findData(self._scope)
        if index >= 0:
            self._scope_combo.setCurrentIndex(index)
        self._scope_combo.currentIndexChanged.connect(lambda _index: self._on_scope_changed())
        row.addWidget(self._scope_combo)
        row.addSpacing(10)
        row.addWidget(QLabel("搜索："))
        self._search_edit = QLineEdit(top)
        self._search_edit.setPlaceholderText("按变量名或值过滤")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(lambda _text: self._refresh_table())
        row.addWidget(self._search_edit, 1)
        reload_button = button("刷新")
        reload_button.clicked.connect(self._reload)
        row.addWidget(reload_button)
        top.body.addLayout(row)
        self.add(top)

        table_card = Card("变量")
        self._table = make_table(
            ("变量名", "值"),
            widths=(280, 640),
            stretch_columns=(1,),
            parent=table_card,
        )
        self._table.setMinimumHeight(200)
        self._table.itemSelectionChanged.connect(self._on_select)
        self._table.cellDoubleClicked.connect(lambda _row, _column: self._edit_selected())
        table_card.body.addWidget(self._table, 1)
        self.add(table_card, 1)

        detail = Card("变量详情")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._detail_name = QLabel("—", detail)
        self._detail_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        grid.addWidget(QLabel("变量名："), 0, 0)
        grid.addWidget(self._detail_name, 0, 1)
        self._raw_text = self._make_readonly_text()
        grid.addWidget(QLabel("原始值："), 1, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._raw_text, 1, 1)
        self._expanded_text = self._make_readonly_text()
        grid.addWidget(QLabel("展开值："), 2, 0, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self._expanded_text, 2, 1)
        detail.body.addLayout(grid)
        self.add(detail)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(6)
        self._new_button = button("新建")
        self._new_button.clicked.connect(self._new_variable)
        actions.addWidget(self._new_button)
        self._edit_button = button("编辑")
        self._edit_button.clicked.connect(self._edit_selected)
        actions.addWidget(self._edit_button)
        self._delete_button = button("删除", kind="danger")
        self._delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self._delete_button)
        self._path_button = button("路径编辑…")
        self._path_button.clicked.connect(self._edit_path_selected)
        actions.addWidget(self._path_button)
        actions.addSpacing(10)
        for text, handler in (("备份…", self._backup), ("恢复…", self._restore)):
            widget = button(text)
            widget.clicked.connect(handler)
            actions.addWidget(widget)
        actions.addStretch(1)
        self._save_button = button("保存", kind="primary")
        self._save_button.clicked.connect(self._save)
        actions.addWidget(self._save_button)
        self.add_layout(actions)

    @staticmethod
    def _make_readonly_text() -> QPlainTextEdit:
        widget = QPlainTextEdit()
        widget.setReadOnly(True)
        widget.setMaximumHeight(64)
        widget.setFont(mono_font())
        widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        return widget

    # ------------------------------------------------------------------ #
    # 状态与列表
    # ------------------------------------------------------------------ #
    def _has_unsaved_changes(self) -> bool:
        return self._variables != self._original

    def _scope_hint(self) -> str:
        if self._scope == SCOPE_PROCESS:
            return "当前进程快照为只读"
        if _is_windows(self._platform):
            if self._scope == SCOPE_SYSTEM:
                return "系统变量写入 HKLM，需要管理员权限"
            return "用户变量写入 HKCU\\Environment"
        return f"用户变量写入 {self._profile_path or envw.shell_profile_path()}，新终端生效"

    def _set_status(self, message: str) -> None:
        self.set_status(f"{message}｜{self._scope_hint()}")

    def _refresh_table(self) -> None:
        search = self._search_edit.text().strip().lower()
        self._table.setRowCount(0)
        rows = sorted(self._variables.items(), key=lambda item: item[0].lower())
        for name, value in rows:
            if search and search not in name.lower() and search not in value.lower():
                continue
            row = self._table.rowCount()
            self._table.insertRow(row)
            name_item = make_table_item(name)
            name_item.setData(Qt.ItemDataRole.UserRole, name)
            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, make_table_item(value.replace("\n", "\\n")))
        self._update_action_state()
        self._set_status(f"{self._table.rowCount()} / {len(self._variables)} 项")

    def _selected_name(self) -> str | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 0)
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return str(name) if name is not None else None

    def _select_name(self, name: str) -> None:
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == name:
                self._table.selectRow(row)
                self._table.scrollToItem(item)
                break
        self._on_select()

    def _update_action_state(self) -> None:
        writable = scope_writable(self._scope, self._platform)
        name = self._selected_name()
        has_selection = name is not None
        self._new_button.setEnabled(writable)
        for widget in (self._edit_button, self._delete_button):
            widget.setEnabled(writable and has_selection)
        self._path_button.setEnabled(writable and has_selection and name is not None and name.upper() == "PATH")
        self._save_button.setEnabled(writable and self._has_unsaved_changes())

    def _update_detail(self, name: str | None) -> None:
        if name is None:
            self._detail_name.setText("—")
            self._raw_text.setPlainText("")
            self._expanded_text.setPlainText("")
            return
        value = self._variables.get(name, "")
        merged = dict(os.environ)
        merged.update(self._variables)
        self._detail_name.setText(name)
        self._raw_text.setPlainText(value)
        self._expanded_text.setPlainText(expand_value(value, merged, platform=self._platform))

    def _on_select(self) -> None:
        self._update_detail(self._selected_name())
        self._update_action_state()

    # ------------------------------------------------------------------ #
    # 作用域与数据加载
    # ------------------------------------------------------------------ #
    def _load_scope(self) -> None:
        try:
            self._variables = read_scope(self._scope, platform=self._platform, profile_path=self._profile_path)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "读取失败", str(exc))
            return
        self._original = dict(self._variables)
        self._refresh_table()
        self._update_detail(None)

    def _reload(self) -> None:
        if self._has_unsaved_changes() and not self._ask("刷新会丢弃未保存的更改，是否继续？"):
            return
        self._load_scope()

    def _on_scope_changed(self) -> None:
        scope = str(self._scope_combo.currentData())
        if not scope or scope == self._scope:
            return
        if self._has_unsaved_changes() and not self._ask("切换作用域会丢弃未保存的更改，是否继续？"):
            self._scope_combo.setCurrentIndex(self._scope_combo.findData(self._scope))
            return
        self._scope = scope
        self._load_scope()

    def _ask(self, question: str) -> bool:
        answer = QMessageBox.question(
            self,
            "未保存的更改",
            question,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------ #
    # 编辑操作
    # ------------------------------------------------------------------ #
    def _open_variable_dialog(self, name: str, value: str) -> tuple[str, str] | None:
        dialog = VariableDialog(
            self,
            name=name,
            value=value,
            names=self._variables,
            platform=self._platform,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.result

    def _new_variable(self) -> None:
        if not scope_writable(self._scope, self._platform):
            return
        result = self._open_variable_dialog("", "")
        if result is None:
            return
        name, value = result
        self._variables[name] = value
        self._refresh_table()
        self._select_name(name)

    def _edit_selected(self) -> None:
        if not scope_writable(self._scope, self._platform):
            return
        name = self._selected_name()
        if name is None:
            return
        result = self._open_variable_dialog(name, self._variables.get(name, ""))
        if result is None:
            return
        new_name, new_value = result
        if new_name != name:
            del self._variables[name]
        self._variables[new_name] = new_value
        self._refresh_table()
        self._select_name(new_name)

    def _delete_selected(self) -> None:
        if not scope_writable(self._scope, self._platform):
            return
        name = self._selected_name()
        if name is None:
            return
        answer = QMessageBox.question(
            self,
            "删除变量",
            f"确定删除 {name}？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        del self._variables[name]
        self._refresh_table()
        self._update_detail(None)

    def _edit_path_selected(self) -> None:
        if not scope_writable(self._scope, self._platform):
            return
        name = self._selected_name()
        if name is None or name.upper() != "PATH":
            return
        dialog = PathEditorDialog(
            self,
            parse_path_entries(self._variables.get(name, ""), self._separator),
            separator=self._separator,
            windows=_is_windows(self._platform),
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.result is None:
            return
        self._variables[name] = join_path_entries(dialog.result, self._separator)
        self._refresh_table()
        self._select_name(name)

    # ------------------------------------------------------------------ #
    # 保存 / 备份 / 恢复
    # ------------------------------------------------------------------ #
    def _save(self) -> None:
        if not scope_writable(self._scope, self._platform):
            return
        try:
            save_scope(
                self._scope,
                self._variables,
                self._original,
                platform=self._platform,
                profile_path=self._profile_path,
            )
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self._original = dict(self._variables)
        self._set_status("已保存")
        self._update_action_state()

    def _backup(self) -> None:
        scopes: dict[str, dict[str, str]] = {}
        for scope in self._scopes:
            if scope == SCOPE_PROCESS:
                continue
            try:
                scopes[scope] = read_scope(scope, platform=self._platform, profile_path=self._profile_path)
            except (OSError, ValueError) as exc:
                QMessageBox.critical(self, "备份失败", str(exc))
                return
        if self._scope in scopes:
            scopes[self._scope] = dict(self._variables)
        filename, _filter = QFileDialog.getSaveFileName(
            self,
            "导出环境变量备份",
            "env-backup.json",
            "JSON 备份 (*.json);;所有文件 (*.*)",
        )
        if not filename:
            return
        try:
            save_backup(Path(filename), scopes)
        except OSError as exc:
            QMessageBox.critical(self, "备份失败", str(exc))
            return
        self._set_status(f"已备份到 {filename}")

    def _restore(self) -> None:
        filename, _filter = QFileDialog.getOpenFileName(
            self,
            "选择环境变量备份",
            "",
            "JSON 备份 (*.json);;所有文件 (*.*)",
        )
        if not filename:
            return
        try:
            scopes = load_backup(Path(filename))
        except ValueError as exc:
            QMessageBox.critical(self, "恢复失败", str(exc))
            return
        writable = [scope for scope in scopes if scope != SCOPE_PROCESS and scope in self._scopes]
        if not writable:
            QMessageBox.critical(self, "恢复失败", "备份中没有当前平台可写入的作用域")
            return
        if not self._ask(f"将覆盖 {len(writable)} 个作用域的环境变量，是否继续？"):
            return
        errors: list[str] = []
        for scope in writable:
            try:
                original = read_scope(scope, platform=self._platform, profile_path=self._profile_path)
                save_scope(
                    scope,
                    scopes[scope],
                    original,
                    platform=self._platform,
                    profile_path=self._profile_path,
                )
            except (OSError, ValueError) as exc:
                errors.append(f"{SCOPE_LABELS.get(scope, scope)}：{exc}")
        self._load_scope()
        if errors:
            QMessageBox.critical(self, "部分恢复失败", "\n".join(errors))
        else:
            QMessageBox.information(self, "恢复完成", f"已恢复 {len(writable)} 个作用域")

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _confirm_close(self) -> bool:
        """关闭前确认未保存的更改。"""
        return not self._has_unsaved_changes() or self._ask("确定退出并放弃未保存的更改？")

    def _persist_settings(self, geometry: str) -> None:
        save_settings(EnvSettings(scope=self._scope, geometry=geometry), self._own_settings_path)


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.qt.app import run_window

    run_window(lambda: EnvQtWindow(settings_path), "环境变量编辑器")
