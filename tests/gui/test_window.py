"""``fcmd.gui.window`` 的单元测试：共享骨架中的纯逻辑部分。"""

from __future__ import annotations

import threading

import pytest

from fcmd.gui import window as _guiwindow


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


class TestCenterOverParent:
    """把 Toplevel 放到父窗口中央：显式尺寸与按请求尺寸两种入口。"""

    @staticmethod
    def _parent(width: int = 1000, height: int = 700, *, x: int = 220, y: int = 110) -> _FakeParent:
        return _FakeParent(width=width, height=height, x=x, y=y)

    def test_uses_explicit_size(self) -> None:
        window = _FakeWindow(requested=(200, 100))
        _guiwindow.center_over_parent(window, self._parent(), width=640, height=400)
        assert window.geometry_calls == ["640x400+400+260"]
        assert window.updates == 0

    def test_falls_back_to_requested_size(self) -> None:
        window = _FakeWindow(requested=(360, 220))
        _guiwindow.center_over_parent(window, self._parent())
        assert window.updates == 1
        assert window.geometry_calls == ["360x220+540+350"]

    def test_unmapped_parent_centers_on_screen(self) -> None:
        window = _FakeWindow(requested=(640, 400))
        _guiwindow.center_over_parent(window, self._parent(width=1, height=1))
        assert window.geometry_calls == ["640x400+640+340"]


class _FakeParent:
    """替身父窗口：只需实际尺寸、根坐标与屏幕尺寸。"""

    def __init__(self, *, width: int, height: int, x: int, y: int) -> None:
        self._width = width
        self._height = height
        self._x = x
        self._y = y

    def winfo_width(self) -> int:
        return self._width

    def winfo_height(self) -> int:
        return self._height

    def winfo_rootx(self) -> int:
        return self._x

    def winfo_rooty(self) -> int:
        return self._y

    def winfo_screenwidth(self) -> int:
        return 1920

    def winfo_screenheight(self) -> int:
        return 1080


class _FakeWindow:
    """替身 Toplevel：记录 geometry / update_idletasks 调用与请求尺寸。"""

    def __init__(self, *, requested: tuple[int, int]) -> None:
        self._requested = requested
        self.geometry_calls: list[str] = []
        self.updates = 0

    def update_idletasks(self) -> None:
        self.updates += 1

    def winfo_reqwidth(self) -> int:
        return self._requested[0]

    def winfo_reqheight(self) -> int:
        return self._requested[1]

    def geometry(self, spec: str) -> None:
        self.geometry_calls.append(spec)


class TestClampSavedGeometry:
    """只夹紧已保存值时，非法输入返回 ``None``。"""

    def test_returns_none_for_invalid(self) -> None:
        assert _guiwindow.clamp_saved_geometry(None, 1920, 1080, min_width=560, min_height=520) is None
        assert _guiwindow.clamp_saved_geometry("bad", 1920, 1080, min_width=560, min_height=520) is None

    def test_clamps_valid_value(self) -> None:
        assert (
            _guiwindow.clamp_saved_geometry("700x600+5000+5000", 1920, 1080, min_width=560, min_height=520)
            == "700x600+1220+480"
        )


class TestFitWraplength:
    """标签换行宽度：留 8px 余量，120px 下限。"""

    def test_subtracts_margin(self) -> None:
        assert _guiwindow.fit_wraplength(500) == 492

    def test_floor_prevents_collapse(self) -> None:
        assert _guiwindow.fit_wraplength(100) == 120
        assert _guiwindow.fit_wraplength(127) == 120
        assert _guiwindow.fit_wraplength(128) == 120
        assert _guiwindow.fit_wraplength(129) == 121


class _FakeLog:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.inserted: list[str] = []

    def configure(self, **kwargs: object) -> None:
        self.calls.append(("configure", kwargs))

    def insert(self, index: str, text: str) -> None:
        self.calls.append(("insert", (index, text)))
        self.inserted.append(text)

    def see(self, index: str) -> None:
        self.calls.append(("see", index))

    def delete(self, first: str, last: str) -> None:
        self.calls.append(("delete", (first, last)))


class TestLogHelpers:
    """日志追加与清空。"""

    def test_append_without_timestamp(self) -> None:
        log = _FakeLog()
        _guiwindow.append_log(log, "hello")
        assert log.calls == [
            ("configure", {"state": "normal"}),
            ("insert", ("end", "hello\n")),
            ("see", "end"),
            ("configure", {"state": "disabled"}),
        ]

    def test_append_with_timestamp(self) -> None:
        log = _FakeLog()
        _guiwindow.append_log(log, "hello", timestamp=True)
        text = log.inserted[0]
        assert text.endswith(" hello\n")
        assert text.startswith("[")
        assert len(text.split("]")[0]) == 9  # ``[HH:MM:SS``

    def test_clear_resets_readonly_state(self) -> None:
        log = _FakeLog()
        _guiwindow.clear_log(log)
        assert log.calls == [
            ("configure", {"state": "normal"}),
            ("delete", ("1.0", "end")),
            ("configure", {"state": "disabled"}),
        ]


class _FakeRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []

    def after(self, delay: int, callback: object) -> None:
        self.after_calls.append((delay, callback))


class TestStartWorker:
    """后台线程 + 主线程轮询。"""

    def test_starts_daemon_thread_and_schedules_poll(self) -> None:
        root = _FakeRoot()
        done = threading.Event()

        def poll() -> None:
            return

        worker = _guiwindow.start_worker(root, done.set, poll=poll)
        assert done.wait(2)
        assert worker.daemon
        assert root.after_calls == [(_guiwindow.WORKER_INTERVAL_MS, poll)]

    def test_without_poll_does_not_schedule(self) -> None:
        root = _FakeRoot()
        _guiwindow.start_worker(root, lambda: None)
        assert root.after_calls == []


class TestRunWindow:
    """GUI 入口包装。"""

    def test_import_error_is_reported(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def factory() -> None:
            raise ImportError("no tkinter")

        _guiwindow.run_window(factory, "测试窗口")  # 不应抛出
        assert "tkinter 不可用" in capsys.readouterr().out

    def test_success_enters_mainloop(self) -> None:
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        _guiwindow.run_window(_Window, "测试窗口")
        assert calls == ["mainloop"]
