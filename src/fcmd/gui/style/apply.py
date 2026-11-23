"""应用主题：QPalette / 字体 / QSS / 图标刷新（需要 PySide）。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fcmd.gui.style.state import _ACTIVE_THEMES, _APPLIED, _FAMILIES, _RUNTIME_HEIGHTS
from fcmd.gui.style.tokens import MONO_FONT_CANDIDATES, pick_font_family, stylesheet, ui_font_families

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme


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
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.disabled_fg))
    del app
    return palette


def apply_theme(app: Any, theme: Theme) -> None:  # pragma: no cover - 需要 PySide6
    """把主题应用到 ``QApplication``：Fusion 样式 + QPalette + 字体 + QSS。

    幂等：重复调用只覆盖样式与调色板，不重建控件；主题切换即时生效。
    """
    from PySide6.QtGui import QFont, QFontDatabase, QFontMetrics

    if _APPLIED.get(app) == theme:
        return
    families = _FAMILIES.get(app)
    if families is None:
        try:
            families = tuple(QFontDatabase.families())
        except TypeError:  # PySide2 / Qt5：families 是实例方法
            families = tuple(QFontDatabase().families())
        _FAMILIES[app] = families
    # 英文 / 数字走 Cascadia Code，中文逐字回退到 CJK 字体（Qt 6 的 setFamilies）
    chain = ui_font_families(families)
    family = chain[0]
    if family.casefold() not in {name.casefold() for name in families}:
        # 候选字体一个都没装（如 CI 的 offscreen 空字体库）：别拿不存在的族名去
        # 构造 QFont——Qt 会把族替换成默认字体且 families() 变空，链路口径失真。
        family = app.font().family()
    mono = pick_font_family(families, MONO_FONT_CANDIDATES)
    app.setStyle("Fusion")
    app.setPalette(_qt_palette(theme, app))
    _ACTIVE_THEMES[app] = theme
    # 公共图标是按调色板着色的位图：切主题后必须按新正文色重绘，否则深色图标
    # 落在深色背景上会看不清。
    from fcmd.gui.icons import refresh_icons

    refresh_icons()
    font = QFont(family)
    installed = {name.casefold() for name in families}
    available_chain = [name for name in chain if name.casefold() in installed]
    if available_chain and hasattr(font, "setFamilies"):
        # Qt 5（PySide2）没有 setFamilies：只认第一个族，行为退化为主族仍是 CJK 字体。
        # 只把**系统里真的存在**的族写进链路：Qt 遇到不存在的族会把 families() 清空。
        # 只有一个族也显式写进链路，这样 ``families()`` 与 ``family()`` 在
        # Linux / offscreen 字体库下口径一致（不同平台对构造函数的记录方式不同）。
        font.setFamilies(available_chain)
    font.setPointSize(theme.fonts.body)
    font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app.setFont(font)
    height = QFontMetrics(font).height() + theme.metrics.control_height_extra
    _RUNTIME_HEIGHTS[app] = height
    app.setStyleSheet(stylesheet(theme, font_family=family, mono_family=mono, control_height_px=height))
    _APPLIED[app] = theme
