"""文本类控件：次要说明、数值、状态胶囊、等宽标签、分节标题与长文本省略。

这些控件只负责展示与最小交互，颜色 / 字号全部由主题 QSS 决定，不含业务逻辑。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

__all__ = [
    "ElidedLabel",
    "HintLabel",
    "StatusTag",
    "hint_label",
    "mono_font",
    "mono_label",
    "section_header",
    "value_label",
]


def hint_label(text: str = "", *, wrap: bool = True) -> QLabel:  # pragma: no cover - Qt 控件
    """次要说明文字（灰色小字），默认自动换行。"""
    label = QLabel(text)
    label.setObjectName("Hint")
    label.setWordWrap(wrap)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def value_label(text: str = "") -> QLabel:  # pragma: no cover - Qt 控件
    """强调数值文字（用于指标网格）。"""
    label = QLabel(text)
    label.setObjectName("Value")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


_STATUS_TONES = frozenset({"muted", "success", "warning", "danger", "accent"})


class StatusTag(QLabel):  # pragma: no cover - Qt 控件
    """状态标签（胶囊）：短状态文本 + 语义色背景，用于「运行中 / 监控中 / 成功」等。

    与 ``Hint`` 的区别是自带底色与边框，能在卡片标题行、工具行里作为独立状态块；
    配色按 ``tone`` 动态属性由 QSS 决定，切主题后自动跟随。
    """

    def __init__(self, text: str = "", *, tone: str = "muted", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("StatusTag")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_tone(tone)

    def tone(self) -> str:
        """当前语义色：``muted`` / ``success`` / ``warning`` / ``danger`` / ``accent``。"""
        return str(self.property("tone") or "muted")

    def set_tone(self, tone: str) -> None:
        """切换语义色；未知值回落到 ``muted``。"""
        normalized = tone if tone in _STATUS_TONES else "muted"
        if self.property("tone") == normalized:
            return
        self.setProperty("tone", normalized)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_status(self, text: str, tone: str | None = None) -> None:
        """一次写入文案与可选语义色（状态刷新最常用）；完全没变时不重绘。"""
        normalized = None if tone is None else (tone if tone in _STATUS_TONES else "muted")
        if self.text() == text and (normalized is None or self.tone() == normalized):
            return
        self.setText(text)
        if tone is not None:
            self.set_tone(tone)


def mono_font(point_size: int = 9) -> QFont:  # pragma: no cover - Qt 控件
    """等宽字体，字号不小于 ``point_size``（日志 / 代码 / 十六进制视图共用）。

    优先用界面约定的拉丁字体 Cascadia Code（GUI 英文一律这一族），系统没装时回退
    到 Qt 的系统等宽字体；中文由系统的逐字回退补上。
    """
    from fcmd.gui import style as _qt_theme
    from fcmd.gui.theme import MONO_FONT_CANDIDATES

    base = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont).pointSize()
    size = max(base, point_size)
    families = _qt_theme.font_families()
    font = QFont(_qt_theme.pick_font_family(families, MONO_FONT_CANDIDATES)) if families else QFont()
    if not families:
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    font.setPointSize(size)
    return font


def mono_label(text: str = "") -> QLabel:  # pragma: no cover - Qt 控件
    """等宽小字（命令、路径、CSV 片段）。"""
    label = QLabel(text)
    label.setObjectName("Mono")
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class HintLabel(QLabel):  # pragma: no cover - Qt 控件
    """次要说明文字，``text`` 可随时替换（替代旧版的可变提示行）。"""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("Hint")
        self.setWordWrap(True)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)


def section_header(title: str, parent: QWidget | None = None) -> tuple[QHBoxLayout, QLabel]:
    """卡片内的分节标题行：``SectionTitle`` 标签 + 弹性空白。

    返回 ``(布局, 标题标签)``，调用方把布局加进 ``Card.body`` 后继续往布局右侧
    追加状态 / 动作控件。与直接使用 ``Card`` 标题的区别是保留 ``SectionTitle``
    的标题字号，避免改变既有卡片的视觉层级。
    """
    header = QHBoxLayout()
    header.setContentsMargins(0, 0, 0, 0)
    header.setSpacing(8)
    label = QLabel(title, parent)
    label.setObjectName("SectionTitle")
    header.addWidget(label)
    header.addStretch(1)
    return header, label


class ElidedLabel(QLabel):  # pragma: no cover - Qt 控件
    """长文本标签：按可用宽度中间省略，完整内容放进 tooltip。

    用于长路径、长 URL、长命令。水平尺寸策略为 ``Ignored``，避免超长文本
    反过来把窗口撑宽；窗口放大后自动显示更多字符。
    """

    def __init__(
        self,
        text: str = "",
        *,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideMiddle,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._full_text = text
        self._elide_mode = mode
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._refresh()

    def setText(self, text: str) -> None:
        """设置完整文本（显示内容按当前宽度省略）；文本未变不重绘。"""
        if self._full_text == text:
            return
        self._full_text = text
        self._refresh()

    def fullText(self) -> str:
        """完整文本（未省略）。"""
        return self._full_text

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        """按当前宽度重算显示文本与 tooltip。"""
        available = self.contentsRect().width()
        if available <= 0:
            super().setText(self._full_text)
            super().setToolTip("")
            return
        shown = self.fontMetrics().elidedText(self._full_text, self._elide_mode, available)
        if super().text() != shown:
            super().setText(shown)
        tooltip = self._full_text if shown != self._full_text else ""
        if super().toolTip() != tooltip:
            super().setToolTip(tooltip)
