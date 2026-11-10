"""``fcmd.tools._guiwindow`` 的单元测试：共享骨架中的纯逻辑部分。"""

from __future__ import annotations

import threading

import pytest

from fcmd.tools import _guiwindow


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
