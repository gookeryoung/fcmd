"""regexvalidatew 的 Qt 主窗口与入口。

交互：输入正则与测试文本后实时校验，展示匹配数量、位置、捕获组、
全匹配与 findall；示例与历史下拉可直接回填；标志位切换即时生效。历史记录在
输入停止 1 秒后写入，避免每次按键都落盘。

布局口径：正则输入用等宽字体整行展示；测试文本与结果用垂直 ``QSplitter``
分隔并记忆比例；结果区不换行、靠水平滚动看长行；状态标签用主题的
Success / Danger 色区分匹配与不匹配，深浅主题一致。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from fcmd import __version__
from fcmd.gui.geometry import size_from_geometry
from fcmd.gui.qt.app import run_window
from fcmd.gui.qt.toolwindow import QtToolWindow
from fcmd.gui.qt.widgets import Card, apply_splitter_sizes, button, hint_label, mono_font, splitter_sizes
from fcmd.tools.text.regexvalidate import PatternValidationError, RegexValidator, build_flags, format_validator_report
from fcmd.tools.text.regexvalidatew import (
    DEFAULT_GEOMETRY,
    HISTORY_DELAY_MS,
    MIN_HEIGHT,
    MIN_WIDTH,
    REGEX_EXAMPLES,
    REGEX_HELP_TEXT,
    RegexHistoryEntry,
    RegexValidateSettings,
    example_label,
    history_label,
    load_settings,
    push_history,
    save_settings,
)

__all__ = ["RegexValidateWindow", "run_qt"]

DEFAULT_SPLIT = (200, 260)


class _HelpDialog(QDialog):  # pragma: no cover - 需要 Qt 事件循环
    """正则语法帮助对话框（只读、可滚动）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("正则语法帮助")
        self.resize(640, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        view = QPlainTextEdit(REGEX_HELP_TEXT, self)
        view.setReadOnly(True)
        view.setFont(mono_font())
        layout.addWidget(view, 1)
        close = button("关闭")
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)


class RegexValidateWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """正则校验器主窗口。"""

    def __init__(self) -> None:
        self._settings = load_settings()
        self._history = list(self._settings.history)
        self._loading = False
        super().__init__(
            title=f"正则校验器 v{__version__}",
            config_name="regexvalidate",
            default_size=size_from_geometry(DEFAULT_GEOMETRY),
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            persist=self._persist,
        )
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(HISTORY_DELAY_MS)
        self._timer.timeout.connect(self._record_history)
        self._build()
        self._install_menu_actions()
        if self._settings.pattern or self._settings.test_string:
            self._refresh()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build(self) -> None:
        """正则卡 → 状态行 → 测试文本 / 结果分栏。"""
        self.add(self._build_pattern_card())
        self.add_layout(self._build_status_row())

        self._splitter = QSplitter(Qt.Orientation.Vertical, self)
        self._splitter.setChildrenCollapsible(False)
        text_card = Card("测试文本", self._splitter)
        self._text = QPlainTextEdit(self._settings.test_string, text_card)
        self._text.setFont(mono_font())
        self._text.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self._text.setMinimumHeight(120)
        text_card.body.addWidget(self._text)
        self._splitter.addWidget(text_card)
        result_card = Card("校验结果", self._splitter)
        self._result = QPlainTextEdit(result_card)
        self._result.setReadOnly(True)
        self._result.setFont(mono_font())
        self._result.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._result.setMinimumHeight(120)
        self._result.setPlainText("请输入正则表达式")
        result_card.body.addWidget(self._result)
        self._splitter.addWidget(result_card)
        self._splitter.setStretchFactor(0, 2)
        self._splitter.setStretchFactor(1, 3)
        sizes = self._settings.splitter or DEFAULT_SPLIT
        apply_splitter_sizes(self._splitter, tuple(sizes))
        self.add(self._splitter, 1)

        self._text.textChanged.connect(self._on_change)

    def _build_pattern_card(self) -> Card:
        """正则输入、示例 / 历史下拉与三个标志。"""
        card = Card("正则", self)
        pattern_row = QHBoxLayout()
        pattern_row.setContentsMargins(0, 0, 0, 0)
        pattern_row.setSpacing(6)
        pattern_row.addWidget(QLabel("模式", card))
        self._pattern = QLineEdit(self._settings.pattern, card)
        self._pattern.setFont(mono_font())
        self._pattern.setPlaceholderText(r"例如 ^1[3-9]\d{9}$")
        self._pattern.textChanged.connect(self._on_change)
        pattern_row.addWidget(self._pattern, 1)
        help_button = button("语法帮助")
        help_button.clicked.connect(self._show_help)
        pattern_row.addWidget(help_button)
        card.body.addLayout(pattern_row)

        select_row = QHBoxLayout()
        select_row.setContentsMargins(0, 0, 0, 0)
        select_row.setSpacing(6)
        select_row.addWidget(QLabel("示例", card))
        self._example_combo = QComboBox(card)
        self._example_combo.addItem(f"-- 选择示例（{len(REGEX_EXAMPLES)} 个） --")
        for index, example in enumerate(REGEX_EXAMPLES):
            self._example_combo.addItem(example_label(index, example))
        self._example_combo.currentIndexChanged.connect(self._on_example_selected)
        select_row.addWidget(self._example_combo, 1)
        select_row.addWidget(QLabel("历史", card))
        self._history_combo = QComboBox(card)
        self._history_combo.currentIndexChanged.connect(self._on_history_selected)
        select_row.addWidget(self._history_combo, 1)
        card.body.addLayout(select_row)
        self._update_history_combo()

        flags_row = QHBoxLayout()
        flags_row.setContentsMargins(0, 0, 0, 0)
        flags_row.setSpacing(14)
        self._ignorecase = QCheckBox("忽略大小写", card)
        self._ignorecase.setChecked(self._settings.ignorecase)
        self._multiline = QCheckBox("多行（^/$）", card)
        self._multiline.setChecked(self._settings.multiline)
        self._dotall = QCheckBox("DOTALL（. 匹配换行）", card)
        self._dotall.setChecked(self._settings.dotall)
        for widget in (self._ignorecase, self._multiline, self._dotall):
            widget.toggled.connect(self._refresh)
            flags_row.addWidget(widget)
        flags_row.addStretch(1)
        card.body.addLayout(flags_row)
        return card

    def _build_status_row(self) -> QHBoxLayout:
        """状态标签（Success / Danger 语义色）+ 右侧提示。"""
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._status = QLabel("校验结果：", self)
        self._status.setObjectName("Hint")
        row.addWidget(self._status, 1)
        row.addWidget(hint_label("输入时自动校验", wrap=False))
        return row

    def _install_menu_actions(self) -> None:
        """把「语法帮助」插入帮助菜单的「关于」之前。"""
        help_menu = self._menu("帮助")
        actions = help_menu.actions()
        about = actions[-1] if actions else None
        action = QAction("语法帮助", self)
        action.triggered.connect(self._show_help)
        if about is not None:
            help_menu.insertAction(about, action)
            help_menu.insertSeparator(about)
        else:
            help_menu.addAction(action)

    def _menu(self, title: str) -> QMenu:
        """按标题取出骨架菜单。"""
        for action in self.menuBar().actions():
            menu = action.menu()
            if menu is not None and action.text() == title:
                return menu
        raise LookupError(f"缺少菜单：{title}")

    # ------------------------------------------------------------------ #
    # 校验
    # ------------------------------------------------------------------ #
    def _current_flags(self) -> int:
        """三个标志组合成 re 标志位。"""
        return build_flags(
            ignorecase=self._ignorecase.isChecked(),
            multiline=self._multiline.isChecked(),
            dotall=self._dotall.isChecked(),
        )

    def _refresh(self, *_args: object) -> None:
        """实时校验并在结果区展示报告。"""
        pattern = self._pattern.text()
        if not pattern:
            self._show_result("请输入正则表达式", None)
            return
        try:
            validator = RegexValidator(pattern, self._current_flags())
        except PatternValidationError as exc:
            self._show_result(str(exc), False)
            return
        text = self._text.toPlainText()
        self._show_result(format_validator_report(validator, text), validator.is_valid(text))

    def _show_result(self, text: str, ok: bool | None) -> None:
        """写入只读结果区，并按匹配结果切换状态标签语义色。"""
        self._result.setPlainText(text)
        if ok is None:
            self._status.setText("校验结果：")
            name = "Hint"
        elif ok:
            self._status.setText("校验结果：✓ 匹配")
            name = "Success"
        else:
            self._status.setText("校验结果：✗ 不匹配")
            name = "Danger"
        if self._status.objectName() != name:
            self._status.setObjectName(name)
            self._status.style().unpolish(self._status)
            self._status.style().polish(self._status)

    def _on_change(self, *_args: object) -> None:
        """输入变化：立即校验，并延迟记录历史。"""
        if self._loading:
            return
        self._refresh()
        self._timer.start()

    def _record_history(self) -> None:
        """把当前输入写入历史并持久化，随后重建历史下拉。"""
        entry = RegexHistoryEntry(
            pattern=self._pattern.text(),
            test_string=self._text.toPlainText(),
            ignorecase=self._ignorecase.isChecked(),
            multiline=self._multiline.isChecked(),
            dotall=self._dotall.isChecked(),
        )
        if not entry.pattern:
            return
        self._history = push_history(self._history, entry)
        self._save()
        self._update_history_combo()

    def _apply_inputs(
        self,
        pattern: str,
        text: str,
        ignorecase: bool,
        multiline: bool,
        dotall: bool,
    ) -> None:
        """批量回填正则、文本与标志，期间抑制回调后统一刷新。"""
        self._loading = True
        self._pattern.setText(pattern)
        self._text.setPlainText(text)
        self._ignorecase.setChecked(ignorecase)
        self._multiline.setChecked(multiline)
        self._dotall.setChecked(dotall)
        self._loading = False
        self._refresh()

    def _on_example_selected(self, index: int) -> None:
        """载入选中示例，并保持该示例在下拉框中显示。"""
        if index <= 0 or index > len(REGEX_EXAMPLES):
            return
        example = REGEX_EXAMPLES[index - 1]
        self._apply_inputs(example.pattern, example.text, example.ignorecase, example.multiline, example.dotall)

    def _update_history_combo(self) -> None:
        """按当前历史重建下拉项并回到占位项。"""
        self._history_combo.blockSignals(True)
        self._history_combo.clear()
        self._history_combo.addItem("-- 选择历史 --")
        for index, entry in enumerate(self._history):
            self._history_combo.addItem(history_label(index, entry))
        self._history_combo.setCurrentIndex(0)
        self._history_combo.blockSignals(False)

    def _on_history_selected(self, index: int) -> None:
        """恢复选中历史条目。"""
        if index <= 0 or index > len(self._history):
            return
        entry = self._history[index - 1]
        self._apply_inputs(entry.pattern, entry.test_string, entry.ignorecase, entry.multiline, entry.dotall)

    def _show_help(self) -> None:
        """打开语法帮助对话框。"""
        _HelpDialog(self).exec()

    # ------------------------------------------------------------------ #
    # 保存与关闭
    # ------------------------------------------------------------------ #
    def _save(self, geometry: str | None = None) -> None:
        """保存当前输入、标志、历史与窗口状态。"""
        save_settings(
            RegexValidateSettings(
                pattern=self._pattern.text(),
                test_string=self._text.toPlainText(),
                ignorecase=self._ignorecase.isChecked(),
                multiline=self._multiline.isChecked(),
                dotall=self._dotall.isChecked(),
                history=list(self._history),
                geometry=geometry or self.geometry_text(),
                splitter=splitter_sizes(self._splitter),
            )
        )

    def _persist(self, geometry: str) -> None:
        """关闭时保存（geometry 由骨架统一传入）。"""
        self._timer.stop()
        self._save(geometry)


def run_qt() -> None:
    """创建并运行 Qt 主窗口。"""
    run_window(RegexValidateWindow, app_name="regexvalidate")
