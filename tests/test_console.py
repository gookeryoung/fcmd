"""console 模块测试。

覆盖 Win7/8 检测、Console 懒加载缓存、legacy 模式宽度收紧、ASCII 强制逻辑
与旧版 rich（无 ``ascii_only`` 参数）的 monkey-patch 降级。
"""

from __future__ import annotations

import inspect
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


def _make_fake_console(captured: dict[str, object]) -> type:
    """构造一个记录 kwargs 的 FakeConsole，签名仅 (self, **kwargs)。

    FakeConsole 自身不声明 ``ascii_only`` 参数，使 ``inspect.signature``
    在测试中可通过 mock 返回不同签名以验证两条分支。
    """

    class FakeConsole:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

    return FakeConsole


def _make_sig(include_ascii: bool) -> inspect.Signature:
    """构造 Console.__init__ 的假签名，控制是否包含 ``ascii_only`` 参数。"""
    params = [inspect.Parameter("self", inspect.Parameter.POSITIONAL_OR_KEYWORD)]
    if include_ascii:
        params.append(inspect.Parameter("ascii_only", inspect.Parameter.KEYWORD_ONLY, default=False))
    return inspect.Signature(params)


class TestGetConsoleLegacyWindows:
    """Win7/8 下 Console 初始化参数。"""

    def test_legacy_windows_passes_width_and_legacy_flag(self) -> None:
        """Win7 + 支持 ascii_only 的 rich：传 legacy_windows/ascii_only/width。"""
        captured: dict[str, object] = {}
        FakeConsole = _make_fake_console(captured)
        fake_size = mock.Mock(columns=80)
        with mock.patch.object(console, "_is_legacy_windows", return_value=True), mock.patch(
            "os.get_terminal_size", return_value=fake_size
        ), mock.patch("rich.console.Console", FakeConsole), mock.patch.object(
            console.inspect, "signature", return_value=_make_sig(include_ascii=True)
        ):
            console.get_console()

        assert captured.get("legacy_windows") is True
        assert captured.get("ascii_only") is True
        # cols=80 → 80-2=78
        assert captured.get("width") == 78

    def test_legacy_windows_min_width_floor(self) -> None:
        """极窄终端（cols=2）下 width 下限为 1，避免负值/零值。"""
        captured: dict[str, object] = {}
        FakeConsole = _make_fake_console(captured)
        fake_size = mock.Mock(columns=2)
        with mock.patch.object(console, "_is_legacy_windows", return_value=True), mock.patch(
            "os.get_terminal_size", return_value=fake_size
        ), mock.patch("rich.console.Console", FakeConsole), mock.patch.object(
            console.inspect, "signature", return_value=_make_sig(include_ascii=True)
        ):
            console.get_console()

        assert captured.get("width") == 1
        # ascii_only 在 OSError 之外的路径下也应保留
        assert captured.get("ascii_only") is True

    def test_legacy_windows_oserror_omits_width(self) -> None:
        """非交互环境（stdout 重定向）下不传 width，由 rich 自行 fallback。"""
        captured: dict[str, object] = {}
        FakeConsole = _make_fake_console(captured)
        with mock.patch.object(console, "_is_legacy_windows", return_value=True), mock.patch(
            "os.get_terminal_size", side_effect=OSError("not a tty")
        ), mock.patch("rich.console.Console", FakeConsole), mock.patch.object(
            console.inspect, "signature", return_value=_make_sig(include_ascii=True)
        ):
            console.get_console()

        # legacy_windows/ascii_only 仍传入，但 width 未设置
        assert captured.get("legacy_windows") is True
        assert captured.get("ascii_only") is True
        assert "width" not in captured

    def test_legacy_windows_old_rich_falls_back_to_force_ascii_box(self) -> None:
        """旧版 rich（无 ascii_only 参数）：调用 _force_ascii_box 且 kwargs 不含 ascii_only。"""
        captured: dict[str, object] = {}
        FakeConsole = _make_fake_console(captured)
        with mock.patch.object(console, "_is_legacy_windows", return_value=True), mock.patch(
            "os.get_terminal_size", side_effect=OSError("not a tty")
        ), mock.patch("rich.console.Console", FakeConsole), mock.patch.object(
            console.inspect, "signature", return_value=_make_sig(include_ascii=False)
        ), mock.patch.object(console, "_force_ascii_box") as mock_force:
            console.get_console()

        # legacy_windows 仍传入；ascii_only 不在 kwargs（旧版 rich 不支持）
        assert captured.get("legacy_windows") is True
        assert "ascii_only" not in captured
        # 降级路径被调用一次
        mock_force.assert_called_once_with()

    def test_modern_windows_no_extra_kwargs(self) -> None:
        """Win10+ 下不传 legacy_windows/ascii_only/width，保持默认行为。"""
        captured: dict[str, object] = {}
        FakeConsole = _make_fake_console(captured)
        with mock.patch.object(console, "_is_legacy_windows", return_value=False), mock.patch(
            "rich.console.Console", FakeConsole
        ):
            console.get_console()

        assert captured == {}


class TestForceAsciiBox:
    """``_force_ascii_box`` 旧版 rich 降级 monkey-patch。"""

    def test_replaces_all_boxes_with_ascii(self) -> None:
        """所有 Box 实例常量被替换为 box.ASCII。"""
        from rich import box

        # 保存原始值（仅 Box 实例常量，跳过类型/函数）
        original = {
            name: getattr(box, name)
            for name in dir(box)
            if not name.startswith("_") and isinstance(getattr(box, name), box.Box)
        }
        try:
            console._force_ascii_box()
            # 所有 Box 常量应被替换为 box.ASCII
            assert original, "应至少有一个 Box 常量"
            for name in original:
                assert getattr(box, name) is box.ASCII, f"{name} 未被替换为 ASCII"
        finally:
            # 恢复原始值，避免影响其他测试
            for name, value in original.items():
                setattr(box, name, value)


class TestPrintVerbose:
    """``print_verbose`` 委托 Console.print。"""

    def test_print_verbose_delegates_to_console(self) -> None:
        """``print_verbose`` 调用全局 Console 的 print 方法。"""
        with mock.patch.object(console, "get_console") as mock_get:
            fake = mock.Mock()
            mock_get.return_value = fake
            console.print_verbose("hello", style="red")
            fake.print.assert_called_once_with("hello", style="red")
