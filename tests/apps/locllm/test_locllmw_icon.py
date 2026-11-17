"""locllmw 图标资源守卫：尺寸齐全、可被 Qt/Pillow 解码。

窗口图标在运行时由 :func:`fcmd.apps.locllm._locllmw_icon.window_icon` 组装；
这里检查随包发布的资源本身，避免只改了 SVG 却忘记导出 PNG/ICO。
"""

from __future__ import annotations

import pytest

from fcmd.apps.locllm import _locllmw_icon as icon

pytest.importorskip("PIL")

from PIL import Image


@pytest.mark.parametrize("size", icon.PNG_SIZES)
def test_png_assets_match_declared_sizes(size: int) -> None:
    path = icon.png_path(size)
    assert path.is_file(), f"缺少图标: {path}"
    with Image.open(path) as image:
        assert image.size == (size, size)
        assert image.format == "PNG"


def test_ico_asset_is_multires_and_complete() -> None:
    path = icon.ICON_DIR / "locllmw.ico"
    assert path.is_file()
    with Image.open(path) as image:
        sizes = {entry[0] for entry in image.info.get("sizes", [])} or {image.size}
    for expected in (16, 32, 48):
        assert expected in sizes, f"ICO 缺少 {expected}px"


def test_svg_source_is_present() -> None:
    path = icon.ICON_DIR / "locllmw.svg"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "<svg" in text and "viewBox" in text
