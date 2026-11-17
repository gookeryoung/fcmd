"""``fcmd.gui.qt.window`` 的几何纯函数测试（不创建 Qt 窗口）。

几何字符串与 Tk 版共用 ``WxH+X+Y`` 格式，因此 Qt 窗口与 Tk 窗口可以互读
同一个设置文件；这里覆盖解析、格式化与屏幕夹紧回退。
"""

from __future__ import annotations

import pytest

from fcmd.gui.qt.window import format_geometry, parse_geometry, resolve_geometry_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1080x880", (1080, 880, 0, 0)),
        ("1080x880+100+50", (1080, 880, 100, 50)),
        ("1080x880-100-50", (1080, 880, -100, -50)),
        (" 800x600+0+0 ", (800, 600, 0, 0)),
    ],
)
def test_parse_geometry(text: str, expected: tuple[int, int, int, int]) -> None:
    assert parse_geometry(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1080", "1080x", "0x880", "1080x0", "1080x880+", None])
def test_parse_geometry_rejects_invalid(text: str | None) -> None:
    assert parse_geometry(text) is None


def test_format_geometry_round_trips() -> None:
    assert format_geometry(1080, 880, 10, 20) == "1080x880+10+20"
    assert format_geometry(1080, 880, -10, -20) == "1080x880-10-20"
    assert parse_geometry(format_geometry(1280, 720, 5, 6)) == (1280, 720, 5, 6)


def test_format_geometry_clamps_negative_sizes() -> None:
    assert format_geometry(-1, -1, 0, 0) == "0x0+0+0"


def test_resolve_geometry_text_prefers_saved() -> None:
    text = resolve_geometry_text(
        "900x700+40+30",
        1920,
        1080,
        default="1080x880",
        min_width=1000,
        min_height=800,
    )
    assert text == "1000x800+40+30"


def test_resolve_geometry_text_falls_back_and_centers() -> None:
    text = resolve_geometry_text(
        "garbage",
        1920,
        1080,
        default="1080x880",
        min_width=1000,
        min_height=800,
    )
    assert text == "1080x880"
    assert parse_geometry(text) is not None


def test_resolve_geometry_text_clamps_offscreen_position() -> None:
    text = resolve_geometry_text(
        "1200x900+5000+5000",
        1920,
        1080,
        default="1080x880",
        min_width=1000,
        min_height=800,
    )
    rect = parse_geometry(text)
    assert rect is not None
    width, height, x, y = rect
    assert x == 1920 - width
    assert y == 1080 - height
