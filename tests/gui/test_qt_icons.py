"""``fcmd.gui.qt.icons`` 的测试：SVG 生成、未知图标报错与 Qt 渲染。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from fcmd.gui.qt import icons


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
