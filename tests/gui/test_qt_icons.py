"""``fcmd.gui.qt.icons`` 的测试：SVG 生成、未知图标报错与 Qt 渲染。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QPushButton

from fcmd.gui import theme as _guitheme
from fcmd.gui.qt import icons
from fcmd.gui.qt import theme as _qt_theme
from fcmd.gui.qt.widgets import button


def test_svg_uses_current_color_and_known_body() -> None:
    text = icons.svg("chevron-down", color="#123456")
    assert text.startswith("<svg")
    assert 'stroke="#123456"' in text
    assert 'd="m6 9 6 6 6-6"' in text
    # 未传 color 时用默认色（无 QApplication 时为 DEFAULT_COLOR，有调色板时为正文色）。
    assert "stroke=" in icons.svg("chevron-right")


def test_unknown_icon_lists_known_names() -> None:
    with pytest.raises(KeyError) as excinfo:
        icons.svg("no-such-icon")
    message = str(excinfo.value)
    assert "no-such-icon" in message
    assert "chevron-down" in message


def test_pixmap_and_icon_render() -> None:
    QApplication.instance() or QApplication([])
    image = icons.pixmap("chevron-down", size=16)
    assert not image.isNull()
    assert image.width() >= 16
    symbol = icons.icon("chevron-left")
    assert not symbol.isNull()
    assert not symbol.pixmap(16, 16).isNull()


def test_icon_pixels_are_centered_and_not_clipped() -> None:
    """回归：SVG 必须按目标矩形缩放；无参 render() 会把 24×24 画进左上角被裁掉。"""
    QApplication.instance() or QApplication([])
    image = icons.pixmap("chevron-down", size=64, color="#ffffff").toImage()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 0:
                xs.append(x)
                ys.append(y)
    assert xs and ys
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    assert right - left > image.width() * 0.4
    assert bottom - top > image.height() * 0.2
    assert abs((left + right) / 2 - image.width() / 2) < image.width() * 0.1
    assert abs((top + bottom) / 2 - image.height() / 2) < image.height() * 0.1


def test_icon_for_text_maps_common_actions() -> None:
    """按钮文案到公共图标的映射：常见动作自动命中，未知文案保持纯文字。"""
    assert icons.icon_for_text("刷新") == "refresh"
    assert icons.icon_for_text("重新检测") == "refresh"
    assert icons.icon_for_text("开始转换") == "convert"
    assert icons.icon_for_text("＋ 新对话") == "plus"
    assert icons.icon_for_text("启动服务") == "run"
    assert icons.icon_for_text("取消") is None
    assert icons.icon_for_text("关于") is None


def _first_opaque_color(widget: QPushButton) -> str:
    image = widget.icon().pixmap(16, 16).toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255:
                return color.name()
    return ""


def test_set_icon_recolors_when_theme_changes() -> None:
    """图标按调色板着色：切主题后 refresh_icons 要重绘，避免深底深色看不清。"""
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    widget = QPushButton()
    try:
        icons.set_icon(widget, "refresh")
        assert widget.property("fcmdIconName") == "refresh"
        assert widget.iconSize().width() == 16
        dark = _first_opaque_color(widget)
        assert dark

        _qt_theme.apply_theme(app, _guitheme.resolve_theme("light"))
        light = _first_opaque_color(widget)
        assert light
        assert light != dark

        _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
        assert _first_opaque_color(widget) == dark
    finally:
        widget.deleteLater()
        icons.refresh_icons()


def test_button_icons_follow_variant_tones() -> None:
    """图标与按钮文字同色：主按钮 on_accent、危险按钮 danger、次要按钮 muted。"""
    from fcmd.gui.qt.theme import active_theme

    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    palette = active_theme().palette
    primary = button("启动服务", kind="primary")
    danger = button("删除", kind="danger")
    ghost = button("刷新", kind="ghost")
    try:
        assert _first_opaque_color(primary) == palette.on_accent
        assert _first_opaque_color(danger) == palette.danger
        assert _first_opaque_color(ghost) == palette.muted
    finally:
        for widget in (primary, danger, ghost):
            widget.deleteLater()
