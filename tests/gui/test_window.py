"""``fcmd.gui.geometry`` 的单元测试：几何解析、夹紧与居中纯函数。"""

from __future__ import annotations

from fcmd.gui import geometry as _guiwindow


class TestResolveGeometry:
    """geometry 恢复与夹紧。"""

    def test_keeps_visible_position(self) -> None:
        assert (
            _guiwindow.resolve_geometry("880x640+260+160", 1920, 1080, default="900x680", min_width=760, min_height=560)
            == "880x640+260+160"
        )

    def test_pulls_back_offscreen(self) -> None:
        assert (
            _guiwindow.resolve_geometry(
                "880x640+5000+5000", 1920, 1080, default="900x680", min_width=760, min_height=560
            )
            == "880x640+1040+440"
        )

    def test_enforces_minimum_size(self) -> None:
        assert (
            _guiwindow.resolve_geometry("200x100+0+0", 1920, 1080, default="900x680", min_width=760, min_height=560)
            == "760x560+0+0"
        )

    def test_falls_back_to_default(self) -> None:
        for saved in (None, "bad"):
            assert (
                _guiwindow.resolve_geometry(saved, 1920, 1080, default="880x640+260+160", min_width=760, min_height=560)
                == "880x640+260+160"
            )

    def test_clamps_default_to_small_screen(self) -> None:
        assert (
            _guiwindow.resolve_geometry(None, 800, 600, default="880x640+260+160", min_width=760, min_height=560)
            == "800x600+0+0"
        )

    def test_accepts_callable_default(self) -> None:
        assert (
            _guiwindow.resolve_geometry(
                None,
                1920,
                1080,
                default=lambda width, height: f"{width // 2}x{height // 2}",
                min_width=560,
                min_height=520,
            )
            == "960x540"
        )


class TestCenteredGeometry:
    """子对话框居中：按父窗口中心定位并夹回屏幕。"""

    def test_centers_in_parent(self) -> None:
        assert (
            _guiwindow.centered_geometry(
                640,
                400,
                parent_x=220,
                parent_y=110,
                parent_width=1000,
                parent_height=700,
                screen_width=1920,
                screen_height=1080,
            )
            == "640x400+400+260"
        )

    def test_clamps_to_screen_edges(self) -> None:
        assert (
            _guiwindow.centered_geometry(
                640,
                400,
                parent_x=1200,
                parent_y=800,
                parent_width=1000,
                parent_height=700,
                screen_width=1920,
                screen_height=1080,
            )
            == "640x400+1280+680"
        )

    def test_clamps_negative_parent(self) -> None:
        assert (
            _guiwindow.centered_geometry(
                640,
                400,
                parent_x=-500,
                parent_y=-400,
                parent_width=1000,
                parent_height=700,
                screen_width=1920,
                screen_height=1080,
            )
            == "640x400+0+0"
        )

    def test_dialog_larger_than_screen_stays_at_origin(self) -> None:
        assert (
            _guiwindow.centered_geometry(
                2000,
                1200,
                parent_x=100,
                parent_y=100,
                parent_width=1000,
                parent_height=700,
                screen_width=1920,
                screen_height=1080,
            )
            == "2000x1200+0+0"
        )
