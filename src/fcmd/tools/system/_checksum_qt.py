"""checksum 的 Qt 主窗口与入口。

选择 MD5 / SHA1 / SHA256 / SHA384 / SHA512 / Blake2b / Blake2s，计算输入字符串或
所选文件的十六进制摘要；勾选「比较校验」后填入期望值，结果区追加不区分大小写的
比对结论（通过绿色、失败红色），可直接与 ``sha256sum`` 等工具输出比对。

摘要算法、分块大小与文案继续复用 :mod:`fcmd.tools.system.checksum` 与
:mod:`fcmd.infras.hashing`；本模块只负责 Qt 控件与结果着色。
设置写回 ``${FCMD_HOME:-~/.fcmd}/ui/checksum.json``。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QRadioButton,
    QTextEdit,
)

from fcmd.gui import theme as _guitheme
from fcmd.gui.toolwindow import QtToolWindow
from fcmd.gui.widgets import Card, button, mono_font, pick_file
from fcmd.tools.system.checksum import (
    ALGORITHMS,
    DEFAULT_ALGORITHM,
    MIN_HEIGHT,
    MIN_WIDTH,
    ChecksumSettings,
    calculate_file_checksum,
    calculate_string_checksum,
    format_result,
    load_settings,
    save_settings,
    verify_checksum,
)

__all__ = ["ChecksumQtWindow", "run_qt"]

DEFAULT_SIZE = (720, 480)


class ChecksumQtWindow(QtToolWindow):  # pragma: no cover - 需要 Qt 事件循环
    """校验和计算器：字符串 / 文件 / 算法 / 比较 / 结果。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        self._own_settings_path = settings_path
        self._settings = load_settings(settings_path)
        super().__init__(
            title="校验和计算器",
            config_name="checksum",
            default_size=DEFAULT_SIZE,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            on_close=lambda: True,
            persist=self._persist_settings,
        )
        self._build_widgets()
        self._toggle_compare()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        source = Card("数据源")
        string_row = QHBoxLayout()
        string_row.setContentsMargins(0, 0, 0, 0)
        string_row.setSpacing(6)
        label = QLabel("字符串：")
        label.setMinimumWidth(64)
        string_row.addWidget(label)
        self.string_edit = QLineEdit(source)
        self.string_edit.setClearButtonEnabled(True)
        self.string_edit.returnPressed.connect(self.compute_string)
        string_row.addWidget(self.string_edit, 1)
        compute_string = button("计算", kind="primary")
        compute_string.clicked.connect(self.compute_string)
        string_row.addWidget(compute_string)
        source.body.addLayout(string_row)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(6)
        file_label = QLabel("文件：")
        file_label.setMinimumWidth(64)
        file_row.addWidget(file_label)
        self.file_edit = QLineEdit(source)
        self.file_edit.setClearButtonEnabled(True)
        file_row.addWidget(self.file_edit, 1)
        browse = button("浏览…")
        browse.clicked.connect(self._browse_file)
        file_row.addWidget(browse)
        compute_file = button("计算")
        compute_file.clicked.connect(self.compute_file)
        file_row.addWidget(compute_file)
        source.body.addLayout(file_row)
        self.add(source)

        algorithm = Card("算法")
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        self._algorithm_group = QButtonGroup(self)
        self._algorithms: dict[str, QRadioButton] = {}
        for index, name in enumerate(ALGORITHMS):
            radio = QRadioButton(name, algorithm)
            radio.setChecked(name == self._settings.algorithm)
            self._algorithm_group.addButton(radio)
            self._algorithms[name] = radio
            grid.addWidget(radio, index // 4, index % 4)
        algorithm.body.addLayout(grid)
        self.add(algorithm)

        comparison = Card("比较校验")
        compare_row = QHBoxLayout()
        compare_row.setContentsMargins(0, 0, 0, 0)
        compare_row.setSpacing(6)
        self.compare_enabled = QCheckBox("启用比较（忽略大小写）", comparison)
        self.compare_enabled.setChecked(self._settings.compare)
        self.compare_enabled.toggled.connect(lambda _checked: self._toggle_compare())
        compare_row.addWidget(self.compare_enabled)
        self.compare_edit = QLineEdit(comparison)
        compare_row.addWidget(self.compare_edit, 1)
        comparison.body.addLayout(compare_row)
        self.add(comparison)

        result = Card("结果")
        self.result = QTextEdit(result)
        self.result.setReadOnly(True)
        self.result.setFont(mono_font())
        self.result.setMinimumHeight(120)
        self.result.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        result.body.addWidget(self.result)
        self.add(result, 1)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def algorithm(self) -> str:
        """当前选中的算法名（未选中时回落默认值）。"""
        for name, radio in self._algorithms.items():
            if radio.isChecked():
                return name
        return DEFAULT_ALGORITHM

    def _toggle_compare(self) -> None:
        """启用 / 禁用比较输入框；关闭时清空期望值。"""
        enabled = self.compare_enabled.isChecked()
        self.compare_edit.setEnabled(enabled)
        if not enabled:
            self.compare_edit.clear()

    def _browse_file(self) -> None:  # pragma: no cover - 需要真实对话框
        chosen = pick_file(self, "选择文件")
        if chosen:
            self.file_edit.setText(chosen)

    def _show_result(self, text: str, *, passed: bool | None = None) -> None:
        """把文本写入只读结果区；校验结论按通过 / 失败着色。"""
        palette = _guitheme.load_theme().palette
        digest, newline, verdict = text.partition("\n")
        self.result.clear()
        self.result.setTextColor(palette.text)
        self.result.insertPlainText(digest)
        if newline:
            color = palette.text
            if passed is not None:
                color = palette.success if passed else palette.danger
            self.result.setTextColor(color)
            self.result.insertPlainText("\n" + verdict)
        self.result.moveCursor(QTextCursor.MoveOperation.Start)

    def expected(self) -> str | None:
        """当前期望值；未启用比较时返回 ``None``。"""
        if not self.compare_enabled.isChecked():
            return None
        return self.compare_edit.text().strip()

    def _render(self, digest: str) -> None:
        """输出摘要，启用比较时校验期望值。"""
        expected = self.expected()
        if expected is not None and not expected:
            self._show_result("请输入比较值")
            return
        if expected is None:
            self._show_result(digest)
            return
        self._show_result(format_result(digest, expected), passed=verify_checksum(digest, expected))

    def compute_string(self) -> None:
        """计算字符串摘要。"""
        content = self.string_edit.text()
        if not content.strip():
            self._show_result("请输入字符串")
            return
        try:
            digest = calculate_string_checksum(content, self.algorithm())
        except (ValueError, OSError) as exc:
            self._show_result(f"错误：{exc}")
            return
        self._render(digest)

    def compute_file(self) -> None:
        """计算文件摘要。"""
        raw = self.file_edit.text().strip()
        if not raw or not Path(raw).is_file():
            self._show_result("请选择有效文件")
            return
        try:
            digest = calculate_file_checksum(Path(raw), self.algorithm())
        except (OSError, ValueError) as exc:
            self._show_result(f"错误：{exc}")
            return
        self._render(digest)

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _persist_settings(self, geometry: str) -> None:
        save_settings(
            ChecksumSettings(
                algorithm=self.algorithm(),
                compare=self.compare_enabled.isChecked(),
                geometry=geometry,
            ),
            self._own_settings_path,
        )


def run_qt(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    from fcmd.gui.app import run_window

    run_window(lambda: ChecksumQtWindow(settings_path), app_name="校验和计算器")
