"""用户命令与颜色规则对话框。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from fcmd.apps.cmder._ops import (
    ColorRule,
)
from fcmd.gui.widgets import button, hint_label, make_table, make_table_item

if TYPE_CHECKING:
    pass

_RENAME_STATUS = {"ok": "可执行", "unchanged": "未变化", "invalid": "非法", "conflict": "冲突"}


def _rename_status(status: str) -> str:
    """多重命名预览行的中文状态。"""
    return _RENAME_STATUS.get(status, status)


__all__ = ["ColorRulesDialog", "UserCommandsDialog"]


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
