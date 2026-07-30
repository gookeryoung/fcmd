"""console 模块测试。

覆盖 Win7/8 检测、Console 懒加载缓存、legacy 模式宽度收紧与 ASCII 强制逻辑。
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from unittest import mock

import pytest

from fcmd import console


@pytest.fixture(autouse=True)
def _reset_console_cache() -> Iterator[None]:
    """每个用例独立的 Console 缓存，避免相互污染。"""
    sentinel = console._console
    console._console = None
    yield
    console._console = sentinel


class TestIsLegacyWindows:
    """``_is_legacy_windows`` 平台判定。"""

    def test_non_windows_returns_false(self) -> None:
        """非 Windows 平台始终返回 False。"""
        with mock.patch.object(sys, "platform", "linux"):
            assert console._is_legacy_windows() is False

    def test_windows7_returns_true(self) -> None:
        """Win7（major=6）判定为 legacy。"""
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
            sys, "getwindowsversion", return_value=mock.Mock(major=6, minor=1)
        ):
            assert console._is_legacy_windows() is True

    def test_windows8_returns_true(self) -> None:
        """Win8（major=6, minor=2）判定为 legacy。"""
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
            sys, "getwindowsversion", return_value=mock.Mock(major=6, minor=2)
        ):
            assert console._is_legacy_windows() is True

    def test_windows10_returns_false(self) -> None:
        """Win10（major=10）非 legacy。"""
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
            sys, "getwindowsversion", return_value=mock.Mock(major=10, build=19041)
        ):
            assert console._is_legacy_windows() is False

    def test_windows11_returns_false(self) -> None:
        """Win11（major=10, build=22000）非 legacy。"""
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
            sys, "getwindowsversion", return_value=mock.Mock(major=10, build=22000)
        ):
            assert console._is_legacy_windows() is False

    def test_getwindowsversion_missing_returns_false(self) -> None:
        """``getwindowsversion`` 不存在时安全降级为 False。"""
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
            sys, "getwindowsversion", side_effect=AttributeError
        ):
            assert console._is_legacy_windows() is False


class TestGetConsoleCache:
    """``get_console`` 懒加载与缓存。"""

    def test_returns_cached_instance(self) -> None:
        """多次调用返回同一 Console 实例。"""
        c1 = console.get_console()
        c2 = console.get_console()
        assert c1 is c2


class TestGetConsoleLegacyWindows:
    """Win7/8 下 Console 初始化参数。"""

    def test_legacy_windows_passes_width_and_legacy_flag(self) -> None:
        """Win7 下显式传 legacy_windows=True/ascii_only=True/width=cols-2。"""
        captured: dict[str, object] = {}

        class FakeConsole:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        fake_size = mock.Mock(columns=80)
        with mock.patch.object(console, "_is_legacy_windows", return_value=True), mock.patch(
            "os.get_terminal_size", return_value=fake_size
        ), mock.patch("rich.console.Console", FakeConsole):
            console.get_console()

        assert captured.get("legacy_windows") is True
        assert captured.get("ascii_only") is True
        # cols=80 → 80-2=78
        assert captured.get("width") == 78

    def test_legacy_windows_min_width_floor(self) -> None:
        """极窄终端（cols=2）下 width 下限为 1，避免负值/零值。"""
        captured: dict[str, object] = {}

        class FakeConsole:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        fake_size = mock.Mock(columns=2)
        with mock.patch.object(console, "_is_legacy_windows", return_value=True), mock.patch(
            "os.get_terminal_size", return_value=fake_size
        ), mock.patch("rich.console.Console", FakeConsole):
            console.get_console()

        assert captured.get("width") == 1
        # ascii_only 在 OSError 之外的路径下也应保留
        assert captured.get("ascii_only") is True

    def test_legacy_windows_oserror_omits_width(self) -> None:
        """非交互环境（stdout 重定向）下不传 width，由 rich 自行 fallback。"""
        captured: dict[str, object] = {}

        class FakeConsole:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        with mock.patch.object(console, "_is_legacy_windows", return_value=True), mock.patch(
            "os.get_terminal_size", side_effect=OSError("not a tty")
        ), mock.patch("rich.console.Console", FakeConsole):
            console.get_console()

        # legacy_windows/ascii_only 仍传入，但 width 未设置
        assert captured.get("legacy_windows") is True
        assert captured.get("ascii_only") is True
        assert "width" not in captured

    def test_modern_windows_no_extra_kwargs(self) -> None:
        """Win10+ 下不传 legacy_windows/ascii_only/width，保持默认行为。"""
        captured: dict[str, object] = {}

        class FakeConsole:
            def __init__(self, **kwargs: object) -> None:
                captured.update(kwargs)

        with mock.patch.object(console, "_is_legacy_windows", return_value=False), mock.patch(
            "rich.console.Console", FakeConsole
        ):
            console.get_console()

        assert captured == {}


class TestPrintVerbose:
    """``print_verbose`` 委托 Console.print。"""

    def test_print_verbose_delegates_to_console(self) -> None:
        """``print_verbose`` 调用全局 Console 的 print 方法。"""
        with mock.patch.object(console, "get_console") as mock_get:
            fake = mock.Mock()
            mock_get.return_value = fake
            console.print_verbose("hello", style="red")
            fake.print.assert_called_once_with("hello", style="red")
