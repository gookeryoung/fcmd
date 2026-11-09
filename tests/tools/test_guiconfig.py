"""``fcmd.tools._guiconfig`` 共享设置骨架测试（geometry 夹紧逻辑）。"""

from __future__ import annotations

from fcmd.tools import _guiconfig


class TestClampGeometry:
    def test_invalid_inputs_return_none(self) -> None:
        assert _guiconfig.clamp_geometry(None, 1920, 1080) is None
        assert _guiconfig.clamp_geometry("abc", 1920, 1080) is None
        assert _guiconfig.clamp_geometry("100x200+1", 1920, 1080) is None

    def test_size_only_is_preserved(self) -> None:
        assert _guiconfig.clamp_geometry("900x700", 1920, 1080) == "900x700"

    def test_size_is_clamped_to_minimum(self) -> None:
        assert _guiconfig.clamp_geometry("200x100+0+0", 1920, 1080, min_width=560, min_height=520) == "560x520+0+0"

    def test_size_is_clamped_to_screen(self) -> None:
        assert _guiconfig.clamp_geometry("2000x1500+10+10", 1920, 1080) == "1920x1080+0+0"

    def test_position_is_pulled_back_on_screen(self) -> None:
        assert _guiconfig.clamp_geometry("700x600+5000+5000", 1920, 1080) == "700x600+1220+480"

    def test_negative_position_is_clamped_to_origin(self) -> None:
        assert _guiconfig.clamp_geometry("700x600-100-50", 1920, 1080) == "700x600+0+0"

    def test_zero_screen_falls_back_to_minimum(self) -> None:
        assert _guiconfig.clamp_geometry("900x700+10+10", 0, 0) == "480x360+0+0"

    def test_whitespace_is_trimmed(self) -> None:
        assert _guiconfig.clamp_geometry(" 800x600+20+30 ", 1920, 1080) == "800x600+20+30"
