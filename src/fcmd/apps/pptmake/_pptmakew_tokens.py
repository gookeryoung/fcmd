"""pptmake 设计令牌编辑对话框：字号、间距、形状、符号、图表色与图片压暗。

对话框只负责收集数值并做一次 ``tokens_from_mapping`` 校验；应用与撤销
由 :class:`fcmd.apps.pptmake._pptmakew_qt.PptmakeQtWindow` 统一处理。
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from fcmd.apps.pptmake import _tokens as tokens
from fcmd.gui.widgets import tab_widget

__all__ = ["TokenDialog"]


class TokenDialog(QDialog):
    """编辑 :class:`DesignTokens` 的子集；确定后 ``values()`` 返回新令牌。"""

    def __init__(self, current: tokens.DesignTokens, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设计令牌")
        self.setMinimumWidth(560)
        self._current = current
        self._type_spins: dict[str, QDoubleSpinBox] = {}
        self._spacing_spins: dict[str, QDoubleSpinBox] = {}
        self._radius_spins: dict[str, QDoubleSpinBox] = {}
        self._color_edits: dict[str, QLineEdit] = {}
        self._result: tokens.DesignTokens | None = None

        layout = QVBoxLayout(self)
        tabs = tab_widget(self)
        tabs.addTab(self._build_type_tab(), "字号")
        tabs.addTab(self._build_spacing_tab(), "间距")
        tabs.addTab(self._build_shape_tab(), "形状与符号")
        tabs.addTab(self._build_color_tab(), "颜色")
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # 构建设置页
    # ------------------------------------------------------------------ #
    def _spin(self, value: float, minimum: float, maximum: float, step: float) -> QDoubleSpinBox:
        box = QDoubleSpinBox(self)
        box.setRange(minimum, maximum)
        box.setDecimals(2)
        box.setSingleStep(step)
        box.setValue(value)
        return box

    def _build_type_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        form.addRow(QLabel("单位与 PPT 的换算：24 = 18pt", tab))
        for name in tokens.TYPE_FIELDS:
            box = self._spin(getattr(self._current.type_scale, name), 8.0, 300.0, 1.0)
            self._type_spins[name] = box
            form.addRow(tokens.TYPE_LABELS[name], box)
        return tab

    def _build_spacing_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        form.addRow(QLabel("边距为画幅比例，行距与段距为倍数", tab))
        for name, (minimum, maximum) in tokens.SPACING_BOUNDS.items():
            step = 0.05 if "line" in name or name.startswith("space_") else 1.0
            box = self._spin(getattr(self._current.spacing, name), minimum, maximum, step)
            self._spacing_spins[name] = box
            form.addRow(tokens.SPACING_LABELS[name], box)
        return tab

    def _build_shape_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        for name in tokens.RADIUS_LABELS:
            box = self._spin(getattr(self._current.radius, name), 0.0, 200.0, 1.0)
            self._radius_spins[name] = box
            form.addRow(tokens.RADIUS_LABELS[name], box)
        self._stroke_width = self._spin(self._current.stroke.width, 0.0, 20.0, 0.5)
        form.addRow("描边宽度", self._stroke_width)
        self._bullet_indent = self._spin(self._current.bullet.indent, 0.0, 200.0, 1.0)
        form.addRow("符号缩进", self._bullet_indent)
        self._bullet_size = self._spin(self._current.bullet.size, 0.0, 100.0, 1.0)
        form.addRow("符号大小", self._bullet_size)
        self._bullet_char = QLineEdit(self._current.bullet.char, tab)
        self._bullet_char.setMaxLength(2)
        form.addRow("项目符号", self._bullet_char)
        self._image_scrim = self._spin(self._current.image_scrim, 0.0, 0.8, 0.05)
        form.addRow("图片压暗", self._image_scrim)
        return tab

    def _color_row(self, form: QFormLayout, key: str, label: str, value: str, placeholder: str = "") -> None:
        edit = QLineEdit(value, self)
        edit.setPlaceholderText(placeholder or "#RRGGBB")
        self._color_edits[key] = edit
        form.addRow(label, edit)

    def _build_color_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        self._color_row(form, "stroke.border", "描边颜色", self._current.stroke.border, "留空自动混合")
        self._color_row(form, "stroke.divider", "分隔线颜色", self._current.stroke.divider, "留空自动混合")
        for index, color in enumerate(self._current.chart_palette):
            self._color_row(form, f"chart.{index}", f"图表色 {index + 1}", color)
        for name in tokens.SEMANTIC_FIELDS:
            self._color_row(
                form, f"semantic.{name}", tokens.SEMANTIC_LABELS[name], getattr(self._current.semantic, name)
            )
        return tab

    # ------------------------------------------------------------------ #
    # 收集与校验
    # ------------------------------------------------------------------ #
    def values(self) -> tokens.DesignTokens:
        """按当前控件值返回新令牌；非法颜色等抛出 :class:`TokenError`。"""
        base = self._current
        updated = replace(
            base,
            type_scale=replace(base.type_scale, **{name: box.value() for name, box in self._type_spins.items()}),
            spacing=replace(base.spacing, **{name: box.value() for name, box in self._spacing_spins.items()}),
            radius=replace(base.radius, **{name: box.value() for name, box in self._radius_spins.items()}),
            stroke=replace(
                base.stroke,
                width=self._stroke_width.value(),
                border=self._color_edits["stroke.border"].text().strip(),
                divider=self._color_edits["stroke.divider"].text().strip(),
            ),
            bullet=replace(
                base.bullet,
                indent=self._bullet_indent.value(),
                size=self._bullet_size.value(),
                char=self._bullet_char.text().strip() or base.bullet.char,
            ),
            image_scrim=self._image_scrim.value(),
            chart_palette=tuple(
                self._color_edits[f"chart.{index}"].text().strip() for index in range(len(base.chart_palette))
            ),
            semantic=replace(
                base.semantic,
                **{name: self._color_edits[f"semantic.{name}"].text().strip() for name in tokens.SEMANTIC_FIELDS},
            ),
        )
        # 统一过一遍解析器：范围、颜色、符号长度都在这里兜底。
        return tokens.tokens_from_mapping(tokens.tokens_to_mapping(updated), base=base)

    def accept(self) -> None:
        """校验通过才关闭；非法值提示后留在对话框继续修改。"""
        try:
            self._result = self.values()
        except tokens.TokenError as error:
            QMessageBox.warning(self, "令牌无效", str(error))
            return
        super().accept()

    def result_tokens(self) -> tokens.DesignTokens | None:
        """返回确定后的令牌；取消时为 ``None``。"""
        return self._result
