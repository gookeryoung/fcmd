"""Qt 主题：把 :mod:`fcmd.gui.theme` 的调色板翻译成 QSS 与 QPalette。

与主题注册表共用同一份 :class:`fcmd.gui.theme.Theme`（四套内置主题、别名与
``${FCMD_HOME:-~/.fcmd}/ui/theme.json`` 持久化），因此各 GUI 窗口的
配色、字号阶梯与切换方式保持一致：Qt 侧只负责把语义色板映射到控件样式。

设计约定：

- :func:`stylesheet` / :func:`pick_font_family` / :func:`template_tokens` 是纯函数，
  不导入 Qt，CI（无 ``PySide6``）可单测；
- 只在 :func:`apply_theme` 内延迟导入 Qt，``import fcmd.gui.qt.theme`` 不会
  在 CLI 快路径上加载图形库；
- 样式表覆盖本项目实际用到的控件：卡片、按钮（主 / 危险）、输入、下拉、
  表格 / 树、标签页、进度条、滚动条、侧边栏按钮、日志区与提示文字。
"""

from __future__ import annotations

import weakref
from dataclasses import asdict
from string import Template
from typing import TYPE_CHECKING, Any

from fcmd.gui import theme as _guitheme

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fcmd.gui.theme import Theme

__all__ = [
    "CONTROL_HEIGHT_EXTRA",
    "MONO_FONT_CANDIDATES",
    "UI_FONT_CANDIDATES",
    "apply_theme",
    "control_height",
    "load_theme",
    "pick_font_family",
    "runtime_control_height",
    "save_theme",
    "stylesheet",
    "template_tokens",
    "theme_names",
    "tiered_value_html",
]

UI_FONT_CANDIDATES = _guitheme.UI_FONT_CANDIDATES
MONO_FONT_CANDIDATES = _guitheme.MONO_FONT_CANDIDATES
theme_names = _guitheme.theme_names
load_theme = _guitheme.load_theme
save_theme = _guitheme.save_theme

_RADIUS = 8
"""卡片 / 输入框圆角（Qt 逻辑像素，高 DPI 由 Qt 自动缩放）。"""

CONTROL_HEIGHT_EXTRA = 9
"""控件高度相对字体行高的补偿量（Qt 逻辑像素）。

单行件的总高 = 字体行高 + 边框（上下各 1px）+ 呼吸空间。9pt 字体行高 12px，
加 9 得到内容高 21px，再加 2px 边框即 23px，与按钮 / 输入框 / 下拉 / 数值框
实测一致。数值固定为逻辑像素，高 DPI 下由 Qt 按缩放因子换算成设备像素。
"""

_CONTROL_PAD_X_BUTTON = "0.9em"
_CONTROL_PAD_X_INPUT = "0.45em"

_APPLIED: weakref.WeakKeyDictionary[Any, str] = weakref.WeakKeyDictionary()
"""已应用主题的应用实例 → 主题 id；重复应用同一主题时直接返回。"""

_FAMILIES: weakref.WeakKeyDictionary[Any, tuple[str, ...]] = weakref.WeakKeyDictionary()
"""按应用缓存 ``QFontDatabase.families()``：字体枚举在部分平台上需要数百毫秒。"""

_RUNTIME_HEIGHTS: weakref.WeakKeyDictionary[Any, int] = weakref.WeakKeyDictionary()
"""已应用主题的应用实例 → :func:`apply_theme` 实测写入样式表的单行控件高度。"""


def runtime_control_height(app: Any) -> int:
    """返回 :func:`apply_theme` 实际写进样式表的单行控件高度（逻辑像素）。

    运行时高度按当前字体 ``QFontMetrics`` 实测行高推导，随平台字体不同而不同，
    不能用纯估算的 :func:`control_height` 代替；主题未应用时返回 0。
    """
    return _RUNTIME_HEIGHTS.get(app, 0)


def control_height(theme: Theme) -> int:
    """按字号推导单行控件内容高度（逻辑像素），供无 Qt 环境下的样式表快照使用。

    运行时 :func:`apply_theme` 会用 ``QFontMetrics`` 的实际行高替代这里的估算，
    保证字体替换后控件仍与文字对齐；本函数保持纯函数，便于 CI 无 ``PySide6`` 单测。
    """
    return round(theme.fonts.body * 96 / 72) + CONTROL_HEIGHT_EXTRA


_QSS = Template(
    """
QWidget { color: $text; font-size: ${font_body}pt; }
QMainWindow, QDialog, QScrollArea, QStackedWidget { background: $background; }
QWidget#PageBody { background: $background; }
QLabel { background: transparent; }
QMenuBar { background: $surface; color: $text; border-bottom: 1px solid $border; }
QMenuBar::item { padding: 4px 10px; background: transparent; }
QMenuBar::item:selected { background: $surface_active; border-radius: 4px; }
QMenu { background: $surface; color: $text; border: 1px solid $border; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 18px; border-radius: 4px; }
QMenu::item:selected { background: $surface_active; }
QMenu::separator { height: 1px; background: $border; margin: 4px 6px; }
QStatusBar { background: $surface; color: $muted; border-top: 1px solid $border; }
QStatusBar QLabel { color: $muted; }

QFrame#Card {
    background: $surface;
    border: 1px solid $border;
    border-radius: ${radius}px;
}
QLabel#CardTitle {
    color: $muted;
    font-weight: 600;
    font-size: ${font_body}pt;
    padding: 0 0 2px 0;
}

QLabel#Title { font-size: ${font_title}pt; font-weight: 600; }
QLabel#SectionTitle { font-size: ${font_heading}pt; font-weight: 600; }
QLabel#Hint { color: $muted; font-size: ${font_small}pt; }
QLabel#Value { font-weight: 600; }
QLabel#Danger { color: $danger; }
QLabel#Warning { color: $warning; }
QLabel#Success { color: $success; }
QLabel#Mono { font-family: "$mono_family"; font-size: ${font_small}pt; }

QPushButton {
    background: $surface_hover;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 0 ${control_pad_x};
    min-height: $control_height;
    max-height: $control_height;
}
QPushButton:hover { background: $surface_active; border-color: $border_strong; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $muted; background: $surface; border-color: $border; }
QPushButton#Primary {
    background: $accent;
    color: $on_accent;
    border-color: $accent;
    font-weight: 600;
}
QPushButton#Primary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#Primary:pressed { background: $accent_pressed; }
QPushButton#Primary:disabled { background: $surface_hover; color: $muted; border-color: $border; }
QPushButton#Danger { color: $danger; border-color: $danger; background: transparent; }
QPushButton#Danger:hover { background: $danger; color: $on_accent; }
QPushButton#Danger:disabled { color: $muted; border-color: $border; background: transparent; }
QPushButton#RunPrimary, QPushButton#RunAction, QPushButton#RunDanger {
    min-height: 40px;
    max-height: 40px;
    padding: 0 22px;
    border-radius: 8px;
    font-size: ${font_heading}pt;
    font-weight: 600;
}
QPushButton#RunPrimary { background: $accent; color: $on_accent; border-color: $accent; }
QPushButton#RunPrimary:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#RunPrimary:pressed { background: $accent_pressed; }
QPushButton#RunPrimary:disabled { background: $surface_hover; color: $muted; border-color: $border; }
QPushButton#RunAction { background: $surface_hover; color: $text; border-color: $border; }
QPushButton#RunAction:hover { background: $surface_active; border-color: $border_strong; }
QPushButton#RunDanger { color: $danger; border-color: $danger; background: transparent; }
QPushButton#RunDanger:hover { background: $danger; color: $on_accent; }
QPushButton#RunDanger:disabled { color: $muted; border-color: $border; background: transparent; }
QPushButton#ChoiceCard {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 0;
    text-align: left;
    min-height: 72px;
    max-height: 72px;
}
QPushButton#ChoiceCard:hover { background: $surface_hover; border-color: $border_strong; }
QPushButton#ChoiceCard:checked { background: $accent_soft; border-color: $accent; }
QLabel#ChoiceTitle {
    background: transparent;
    color: $text;
    font-size: ${font_body}pt;
    font-weight: 600;
}
QLabel#ChoiceHint {
    background: transparent;
    color: $muted;
    font-size: ${font_small}pt;
}
QPushButton#ChoiceCard[tone="danger"] { border-color: $danger; background: $danger_soft; }
QPushButton#ChoiceCard[tone="accent"] { border-color: $accent; background: $accent_soft; }
QPushButton#ChoiceCard[tone="success"] { border-color: $success; background: $success_soft; }
QPushButton#ChoiceCard[tone="warning"] { border-color: $warning; background: $warning_soft; }
QPushButton#ChoiceCard[tone="muted"] { border-color: $border_strong; background: $surface_active; }
QPushButton#ChoiceCard[tone="danger"]:checked,
QPushButton#ChoiceCard[tone="accent"]:checked,
QPushButton#ChoiceCard[tone="success"]:checked,
QPushButton#ChoiceCard[tone="warning"]:checked,
QPushButton#ChoiceCard[tone="muted"]:checked { border-width: 2px; }
QPushButton#ChoiceCard[tone="danger"] QLabel#ChoiceTitle { color: $danger; }
QPushButton#ChoiceCard[tone="accent"] QLabel#ChoiceTitle { color: $accent; }
QPushButton#ChoiceCard[tone="success"] QLabel#ChoiceTitle { color: $success; }
QPushButton#ChoiceCard[tone="warning"] QLabel#ChoiceTitle { color: $warning; }
QPushButton#ChoiceCard[tone="muted"] QLabel#ChoiceTitle { color: $text; }
QPushButton#Ghost {
    background: transparent;
    border: 1px solid $border;
    color: $muted;
    padding: 0 ${control_pad_x};
}
QPushButton#Ghost:hover {
    background: $surface_hover;
    border-color: $border_strong;
    color: $text;
}
QPushButton#Ghost:pressed { background: $surface_active; }
QPushButton#Ghost:disabled { color: $muted; border-color: $border; background: transparent; }
QPushButton#FilterChip {
    background: $surface;
    border: 1px solid $border;
    border-radius: 12px;
    color: $muted;
    font-weight: 600;
}
QPushButton#FilterChip:hover { border-color: $border_strong; color: $text; }
QPushButton#FilterChip:checked {
    background: $accent_soft;
    border-color: $accent;
    color: $text;
}
QPushButton#Link {
    background: transparent;
    border: none;
    color: $accent;
    padding: 0 2px;
    min-height: 0;
    max-height: none;
    font-size: ${font_small}pt;
    text-align: left;
}
QPushButton#Link:hover { color: $accent_hover; }
QPushButton#Link:pressed { color: $accent_pressed; }
QPushButton#Link:disabled { color: $muted; }

QToolButton { border: 1px solid $border; border-radius: 6px; background: $surface_hover; padding: 4px 8px; }
QToolButton:hover { background: $surface_active; }
QToolButton#Collapsible {
    border: none;
    background: transparent;
    color: $muted;
    font-weight: 600;
    text-align: left;
    padding: 4px 0;
}
QToolButton#Collapsible:hover { color: $text; }
QPushButton#NavButton {
    background: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: 8px;
    color: $muted;
    text-align: left;
    padding: 0 12px;
    min-height: 36px;
    max-height: 36px;
    font-size: ${font_heading}pt;
}
QPushButton#NavButton:hover { background: $surface_hover; color: $text; }
QPushButton#NavButton:checked {
    background: $surface_active;
    color: $accent;
    border-left: 3px solid $accent;
    font-weight: 600;
}
QToolButton#SidebarToggle {
    background: transparent;
    border: none;
    border-radius: 6px;
    color: $muted;
    padding: 0 8px;
    min-height: 30px;
    max-height: 30px;
    font-size: ${font_heading}pt;
}
QToolButton#SidebarToggle:hover { background: $surface_hover; color: $text; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 0 ${control_pad_x_input};
    min-height: $control_height;
    max-height: $control_height;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
}
QPlainTextEdit, QTextEdit {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 4px 8px;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color: $accent; }
QLineEdit:disabled, QComboBox:disabled { color: $muted; background: $surface_hover; }
QLineEdit[readOnly="true"] { color: $muted; background: $surface_hover; }
QComboBox::drop-down { border: none; width: 20px; }
QComboBox QAbstractItemView {
    background: $surface;
    color: $text;
    border: 1px solid $border;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
    outline: none;
}
QPlainTextEdit#Log, QTextEdit#Log {
    background: $log_bg;
    color: $log_fg;
    font-family: "$mono_family";
    font-size: ${font_small}pt;
    border: 1px solid $border;
    border-radius: 6px;
}

QTableView, QTreeView, QListView, QTreeWidget, QTableWidget {
    background: $surface;
    alternate-background-color: $stripe;
    color: $text;
    gridline-color: $border;
    border: 1px solid $border;
    border-radius: 6px;
    selection-background-color: $select_bg;
    selection-color: $select_fg;
    outline: none;
}
QTableView::item, QTreeView::item, QTreeWidget::item, QTableWidget::item { padding: 3px 4px; }
QTableView::item:selected, QTreeView::item:selected, QTreeWidget::item:selected,
QTableWidget::item:selected { background: $select_bg; color: $select_fg; }
QHeaderView::section {
    background: $surface_hover;
    color: $muted;
    padding: 5px 6px;
    border: none;
    border-right: 1px solid $border;
    border-bottom: 1px solid $border;
}
QTableCornerButton::section { background: $surface_hover; border: none; }
QFrame#Card QTableView, QFrame#Card QTreeView, QFrame#Card QListView,
QFrame#Card QTreeWidget, QFrame#Card QTableWidget {
    background: transparent;
    border: none;
    border-radius: 0;
}

QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab {
    background: $surface;
    color: $muted;
    border: 1px solid $border;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 16px;
    margin-right: 2px;
}
QTabBar::tab:selected { background: $surface_active; color: $text; font-weight: 600; }
QTabBar::tab:hover { color: $text; }

QProgressBar {
    background: $surface_hover;
    border: 1px solid $border;
    border-radius: 5px;
    color: $text;
    text-align: center;
    height: 10px;
}
QProgressBar::chunk { background: $accent; border-radius: 4px; }

QScrollBar:vertical { background: transparent; width: 10px; margin: 0; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 5px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: $accent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 0; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 5px; min-width: 28px; }
QScrollBar::add-line, QScrollBar::sub-line { width: 0; height: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

QCheckBox, QRadioButton { color: $text; spacing: 6px; }
QCheckBox::indicator, QRadioButton::indicator { width: 14px; height: 14px; }
QCheckBox::indicator:unchecked { border: 1px solid $border_strong; border-radius: 3px; background: $surface; }
QCheckBox::indicator:checked { border: 1px solid $accent; border-radius: 3px; background: $accent; }
QToolTip { background: $surface_active; color: $text; border: 1px solid $border; padding: 4px 6px; }
QFrame#ActionBar {
    background: $background;
    border-top: 1px solid $border;
}
QSplitter::handle { background: $border; }
QFrame#Sidebar { background: $surface; border-right: 1px solid $border; }
QListWidget#RecommendationList, QListWidget#ModelResultList { background: transparent; border: none; outline: 0; }
QListWidget#RecommendationList::item, QListWidget#ModelResultList::item { background: transparent; border: none; padding: 0; }
QFrame#RecommendationRow, QFrame#ModelItemRow {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
}
QFrame#RecommendationRow[band="excellent"] { background: $success_soft; border-color: $success; }
QFrame#RecommendationRow[band="good"] { background: $accent_soft; border-color: $accent; }
QFrame#RecommendationRow[band="fair"] { background: $warning_soft; border-color: $warning; }
QFrame#RecommendationRow[band="limited"] { background: $danger_soft; border-color: $danger; }
QFrame#RecommendationRow:hover, QFrame#ModelItemRow:hover { border-width: 2px; }
QFrame#RecommendationRow[selected="true"], QFrame#ModelItemRow[selected="true"] { border-width: 2px; }
QLabel#RecommendationName, QLabel#ModelItemName { font-size: ${font_heading}pt; font-weight: 600; }
QPushButton#InstalledButton, QPushButton#ConvertedButton {
    color: $success;
    background: $success_soft;
    border: 1px solid $success;
    font-weight: 600;
}
QPushButton#InstalledButton:disabled, QPushButton#ConvertedButton:disabled {
    color: $success;
    background: $success_soft;
    border: 1px solid $success;
}
QLabel#QuantTag {
    color: $accent;
    background: $accent_soft;
    border: 1px solid $accent;
    border-radius: 7px;
    padding: 0px 5px;
    font-size: ${font_small}pt;
    font-weight: 600;
}
QLabel#ScoreBadge {
    border: 2px solid $border;
    border-radius: 22px;
    font-size: ${font_heading}pt;
    font-weight: 700;
}
QLabel#ScoreBadge[band="excellent"] { color: $on_accent; background: $success; border-color: $success; }
QLabel#ScoreBadge[band="good"] { color: $on_accent; background: $accent; border-color: $accent; }
QLabel#ScoreBadge[band="fair"] { color: $on_accent; background: $warning; border-color: $warning; }
QLabel#ScoreBadge[band="limited"] { color: $on_accent; background: $danger; border-color: $danger; }
"""
)


def pick_font_family(
    available: Iterable[str],
    candidates: tuple[str, ...] = UI_FONT_CANDIDATES,
) -> str:
    """从候选列表里挑第一个系统可用字体族，均不可用时返回首个候选。

    与 :func:`fcmd.gui.theme.pick_font_family` 口径一致（大小写不敏感匹配，
    保留系统报告的真实写法），但接受的是 Qt ``QFontDatabase.families()``。
    """
    return _guitheme.pick_font_family(available, candidates)


def template_tokens(
    theme: Theme,
    *,
    font_family: str = "",
    mono_family: str = "",
    control_height_px: int | None = None,
) -> dict[str, str]:
    """把主题展开成 QSS 模板变量：调色板 token + 字号 + 圆角 + 字体族。

    调色板字段全部内联为 ``$<token>`` 变量，新增 token 时会自动进入模板
    （模板里未引用也不报错），避免样式表与 :class:`Palette` 两处维护色值。
    ``control_height_px`` 省略时按字号估算，运行时由 :func:`apply_theme` 传入
    实际字体行高推导值。
    """
    palette = asdict(theme.palette)
    tokens = {name: str(value) for name, value in palette.items()}
    # 评分档位的浅色底：由档位色按固定透明度派生，避免调色板里再堆一组近似色。
    for name in ("accent", "success", "warning", "danger"):
        tokens[f"{name}_soft"] = _rgba(palette[name], 16)
    height = control_height_px if control_height_px is not None else control_height(theme)
    tokens.update(
        {
            "font_small": str(theme.fonts.small),
            "font_body": str(theme.fonts.body),
            "font_heading": str(theme.fonts.heading),
            "font_title": str(theme.fonts.title),
            "radius": str(_RADIUS),
            "control_height": f"{height}px",
            "control_pad_x": _CONTROL_PAD_X_BUTTON,
            "control_pad_x_input": _CONTROL_PAD_X_INPUT,
            "font_family": font_family or pick_font_family((), UI_FONT_CANDIDATES),
            "mono_family": mono_family or pick_font_family((), MONO_FONT_CANDIDATES),
        }
    )
    return tokens


def _rgba(value: str, percent: int) -> str:
    """把 ``#rrggbb`` 转成 QSS 的 ``rgba(r, g, b, n%)``；非六位十六进制原样返回。"""
    text = value.lstrip("#")
    if len(text) != 6:
        return value
    try:
        red, green, blue = (int(text[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return value
    return f"rgba({red}, {green}, {blue}, {percent}%)"


def tiered_value_html(theme: Theme, main: str, detail: str = "") -> str:
    """生成「主值大字加粗 + 说明小字灰」的富文本，供硬件信息等分层数值使用。

    纯函数：主值用标题字号加粗；``detail`` 非空时追加小号 muted 色说明。
    字号 / 颜色均取自当前主题，随主题缩放不写死物理像素。
    """
    from html import escape

    main_html = f'<span style="font-size:{theme.fonts.heading}pt; font-weight:600;">{escape(main)}</span>'
    if not detail:
        return main_html
    note = f' <span style="font-size:{theme.fonts.small}pt; color:{theme.palette.muted};">{escape(detail)}</span>'
    return main_html + note


def stylesheet(
    theme: Theme,
    *,
    font_family: str = "",
    mono_family: str = "",
    control_height_px: int | None = None,
) -> str:
    """生成整套 QSS 文本（纯函数，不依赖 Qt，便于单测与快照）。"""
    return _QSS.substitute(
        template_tokens(
            theme,
            font_family=font_family,
            mono_family=mono_family,
            control_height_px=control_height_px,
        )
    )


def _qt_palette(theme: Theme, app: Any) -> Any:  # pragma: no cover - 需要 PySide6
    """把语义调色板翻译成 ``QPalette``。"""
    from PySide6.QtGui import QColor, QPalette

    colors = theme.palette
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: colors.background,
        QPalette.ColorRole.WindowText: colors.text,
        QPalette.ColorRole.Base: colors.surface,
        QPalette.ColorRole.AlternateBase: colors.stripe,
        QPalette.ColorRole.Text: colors.text,
        QPalette.ColorRole.Button: colors.surface_hover,
        QPalette.ColorRole.ButtonText: colors.text,
        QPalette.ColorRole.Highlight: colors.select_bg,
        QPalette.ColorRole.HighlightedText: colors.select_fg,
        QPalette.ColorRole.ToolTipBase: colors.surface_active,
        QPalette.ColorRole.ToolTipText: colors.text,
        QPalette.ColorRole.PlaceholderText: colors.muted,
        QPalette.ColorRole.Link: colors.accent,
        QPalette.ColorRole.BrightText: colors.danger,
        QPalette.ColorRole.Light: colors.surface_hover,
        QPalette.ColorRole.Mid: colors.border,
        QPalette.ColorRole.Dark: colors.border_strong,
        QPalette.ColorRole.Midlight: colors.surface_active,
    }
    for role, color in roles.items():
        palette.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.muted))
    del app
    return palette


def apply_theme(app: Any, theme: Theme) -> None:  # pragma: no cover - 需要 PySide6
    """把主题应用到 ``QApplication``：Fusion 样式 + QPalette + 字体 + QSS。

    幂等：重复调用只覆盖样式与调色板，不重建控件；主题切换即时生效。
    """
    from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics

    if _APPLIED.get(app) == theme.name:
        return
    families = _FAMILIES.get(app)
    if families is None:
        families = tuple(QFontDatabase.families())
        _FAMILIES[app] = families
    family = pick_font_family(families)
    mono = pick_font_family(families, MONO_FONT_CANDIDATES)
    app.setStyle("Fusion")
    app.setPalette(_qt_palette(theme, app))
    font = QFont(family)
    font.setPointSize(theme.fonts.body)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)
    height = QFontMetrics(font).height() + CONTROL_HEIGHT_EXTRA
    _RUNTIME_HEIGHTS[app] = height
    app.setStyleSheet(stylesheet(theme, font_family=family, mono_family=mono, control_height_px=height))
    _APPLIED[app] = theme.name
