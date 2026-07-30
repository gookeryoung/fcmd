"""console 模块测试。

覆盖：Win7/8 legacy 检测、Console 懒加载缓存、markup 解析、Table 渲染
（ASCII 边框 / 无边框 / 中文宽度对齐）、ANSI 颜色输出、非 tty 纯文本降级。
"""

from __future__ import annotations

import ctypes
import io
import sys
from collections.abc import Iterator
from unittest import mock

import pytest

from fcmd import console
from fcmd.console import Console, Table


@pytest.fixture(autouse=True)
def _reset_console_cache() -> Iterator[None]:
    """每个用例独立的 Console 缓存，避免相互污染。"""
    sentinel = console._console
    console._console = None
    yield
    console._console = sentinel


# ---------------------------------------------------------------------- #
# _is_legacy_windows
# ---------------------------------------------------------------------- #


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


# ---------------------------------------------------------------------- #
# get_console 缓存
# ---------------------------------------------------------------------- #


class TestGetConsoleCache:
    """``get_console`` 懒加载与缓存。"""

    def test_returns_cached_instance(self) -> None:
        """多次调用返回同一 Console 实例。"""
        c1 = console.get_console()
        c2 = console.get_console()
        assert c1 is c2

    def test_legacy_windows_passes_flag(self) -> None:
        """Win7/8 下 Console 用 legacy_windows=True 初始化。"""
        with mock.patch.object(console, "_is_legacy_windows", return_value=True):
            c = console.get_console()
        assert c._legacy is True

    def test_modern_windows_no_legacy_flag(self) -> None:
        """非 legacy 平台 Console 不带 legacy_windows。"""
        with mock.patch.object(console, "_is_legacy_windows", return_value=False):
            c = console.get_console()
        assert c._legacy is False


# ---------------------------------------------------------------------- #
# markup 解析
# ---------------------------------------------------------------------- #


class TestParseMarkup:
    """``_parse_markup`` 标签解析。"""

    def test_plain_text_no_tags(self) -> None:
        """无标签文本返回单段空样式。"""
        assert console._parse_markup("hello") == [("hello", frozenset())]

    def test_single_color_tag(self) -> None:
        """单颜色标签成对出现。"""
        result = console._parse_markup("[red]error[/red]")
        assert result == [("error", frozenset({"red"}))]

    def test_mixed_text_and_tags(self) -> None:
        """标签内外文本分段。"""
        result = console._parse_markup("pre [cyan]mid[/cyan] post")
        assert result == [
            ("pre ", frozenset()),
            ("mid", frozenset({"cyan"})),
            (" post", frozenset()),
        ]

    def test_combined_style_tag(self) -> None:
        """``[bold cyan]`` 解析为 bold + cyan 两个样式。"""
        result = console._parse_markup("[bold cyan]text[/bold cyan]")
        assert result == [("text", frozenset({"bold", "cyan"}))]

    def test_nested_tags_stack(self) -> None:
        """嵌套标签样式叠加。"""
        result = console._parse_markup("[bold]a [cyan]b[/cyan] c[/bold]")
        assert result == [
            ("a ", frozenset({"bold"})),
            ("b", frozenset({"bold", "cyan"})),
            (" c", frozenset({"bold"})),
        ]

    def test_implicit_close_tag(self) -> None:
        """``[/]`` 闭合最近一个开标签。"""
        result = console._parse_markup("[red]error[/]")
        assert result == [("error", frozenset({"red"}))]

    def test_empty_text(self) -> None:
        """空字符串返回空列表。"""
        assert console._parse_markup("") == []


class TestStripMarkup:
    """``_strip_markup`` 移除标签。"""

    def test_strips_all_tags(self) -> None:
        """所有 markup 标签被移除。"""
        assert console._strip_markup("[red]error[/red] [bold cyan]v1[/bold cyan]") == "error v1"

    def test_no_tags_unchanged(self) -> None:
        """无标签文本不变。"""
        assert console._strip_markup("plain text") == "plain text"


class TestDisplayWidth:
    """``_display_width`` 显示宽度。"""

    def test_ascii_width(self) -> None:
        """ASCII 字符宽度=字符数。"""
        assert console._display_width("hello") == 5

    def test_chinese_width(self) -> None:
        """中文字符（East Asian Wide）宽度=2。"""
        assert console._display_width("中文") == 4

    def test_mixed_width(self) -> None:
        """中英混合按实际宽度累加。"""
        assert console._display_width("a中") == 3

    def test_fullwidth_chars_width(self) -> None:
        """全角字符（Fullwidth，如（））占 2 列。"""
        assert console._display_width("（）") == 4
        assert console._display_width("清屏（跨平台）") == 14

    def test_empty_string(self) -> None:
        """空字符串宽度=0。"""
        assert console._display_width("") == 0


# ---------------------------------------------------------------------- #
# 颜色码转换
# ---------------------------------------------------------------------- #


class TestStylesToAnsi:
    """``_styles_to_ansi`` ANSI 转义码生成。"""

    def test_single_color(self) -> None:
        """单颜色生成对应前景色码。"""
        assert console._styles_to_ansi(frozenset({"red"})) == "\033[31m"

    def test_color_with_attr(self) -> None:
        """颜色+属性组合生成分号分隔码。"""
        result = console._styles_to_ansi(frozenset({"bold", "cyan"}))
        # set 迭代顺序不固定，验证包含两个码
        assert "\033[" in result and "m" in result
        assert "1" in result  # bold
        assert "36" in result  # cyan

    def test_empty_styles(self) -> None:
        """空样式返回空字符串。"""
        assert console._styles_to_ansi(frozenset()) == ""

    def test_unknown_style_ignored(self) -> None:
        """未知样式名被忽略。"""
        assert console._styles_to_ansi(frozenset({"unknown"})) == ""


class TestStylesToWinAttr:
    """``_styles_to_win_attr`` Win16 位掩码。"""

    def test_empty_returns_default_white(self) -> None:
        """空样式返回默认白前景（7），用于恢复默认颜色。"""
        assert console._styles_to_win_attr(frozenset()) == 7

    def test_red_color(self) -> None:
        """red 映射为 FOREGROUND_RED=4。"""
        assert console._styles_to_win_attr(frozenset({"red"})) == 4

    def test_bold_adds_intensity(self) -> None:
        """bold 叠加 FOREGROUND_INTENSITY=8。"""
        # bold 单独：默认白(7) | 8 = 15
        assert console._styles_to_win_attr(frozenset({"bold"})) == 15

    def test_color_with_bold(self) -> None:
        """颜色 + bold = 颜色 | 8。"""
        assert console._styles_to_win_attr(frozenset({"green", "bold"})) == 10  # 2 | 8


# ---------------------------------------------------------------------- #
# Table 渲染
# ---------------------------------------------------------------------- #


class TestTable:
    """``Table`` 渲染。"""

    def test_empty_table_str(self) -> None:
        """无列无行的 Table 渲染为空字符串（或标题）。"""
        t = Table()
        assert str(t) == ""
        t2 = Table(title="标题")
        assert str(t2) == "标题"

    def test_boxed_table_with_header(self) -> None:
        """带边框表格含表头与数据行。"""
        t = Table(show_header=True, header_style="bold")
        t.add_column("名字", style="cyan")
        t.add_column("值", justify="right")
        t.add_row("a", "1")
        t.add_row("bb", "22")
        rendered = str(t)
        # 边框线
        assert "+" in rendered and "-" in rendered and "|" in rendered
        # 表头
        assert "名字" in rendered
        assert "值" in rendered
        # 数据
        assert "a" in rendered and "bb" in rendered
        assert "1" in rendered and "22" in rendered

    def test_boxed_table_includes_title(self) -> None:
        """标题在边框上方输出。"""
        t = Table(title="我的表", show_header=True)
        t.add_column("列")
        t.add_row("x")
        rendered = str(t)
        assert rendered.startswith("我的表")

    def test_no_box_table_aligned(self) -> None:
        """``box=None`` 无边框对齐输出。"""
        t = Table(show_header=False, box=None)
        t.add_column("字段", style="bold")
        t.add_column("值")
        t.add_row("name", "value")
        t.add_row("x", "y")
        rendered = str(t)
        # 无边框线
        assert "+" not in rendered
        assert "|" not in rendered
        # 内容存在
        assert "name" in rendered and "value" in rendered
        assert "x" in rendered and "y" in rendered

    def test_chinese_column_width_alignment(self) -> None:
        """中文列按显示宽度对齐（中文字符占 2 列）。"""
        t = Table(show_header=True, header_style="bold")
        t.add_column("名字")
        t.add_column("值", justify="right")
        t.add_row("中文", "1")
        t.add_row("ab", "22")
        rendered = str(t)
        lines = rendered.split("\n")
        # 找到数据行（含 "中文" 的行）
        cn_line = next(line for line in lines if "中文" in line)
        en_line = next(line for line in lines if "ab" in line and "中文" not in line)
        # 两行的列分隔位置应一致（"中文" 4 列宽 = "ab  " 4 列宽）
        # 通过 | 分隔后，第二列起始位置应相同
        assert cn_line.count("|") == en_line.count("|")

    def test_right_justify(self) -> None:
        """右对齐：短内容左侧填充空格。"""
        t = Table(show_header=False, box=None)
        t.add_column("n", justify="right")
        t.add_row("1")
        t.add_row("22")
        rendered = str(t)
        lines = [line for line in rendered.split("\n") if line.strip()]
        # "1" 应该被左填充到与 "22" 同宽
        assert lines[0].startswith(" ")
        assert lines[1].startswith("2")

    def test_add_row_coerces_to_str(self) -> None:
        """``add_row`` 自动把非 str 转 str。"""
        t = Table(show_header=False, box=None)
        t.add_column("v")
        t.add_row(123)
        t.add_row(None)
        rendered = str(t)
        assert "123" in rendered
        assert "None" in rendered

    def test_add_row_fewer_cells_pads_empty(self) -> None:
        """行数据少于列数时空填充。"""
        t = Table(show_header=False, box=None)
        t.add_column("a")
        t.add_column("b")
        t.add_row("only_a")
        rendered = str(t)
        assert "only_a" in rendered
        # 不崩溃即可

    def test_show_header_false_omits_header_row(self) -> None:
        """``show_header=False`` 不渲染表头。"""
        t = Table(show_header=False, box=None)
        t.add_column("hdr")
        t.add_row("data")
        rendered = str(t)
        assert "hdr" not in rendered
        assert "data" in rendered


# ---------------------------------------------------------------------- #
# Console.print
# ---------------------------------------------------------------------- #


class TestConsolePrint:
    """``Console.print`` 输出。"""

    def test_plain_text_to_non_tty(self) -> None:
        """非 tty 输出纯文本（无颜色码），默认末尾换行。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        c.print("hello")
        assert buf.getvalue() == "hello\n"

    def test_markup_stripped_in_non_tty(self) -> None:
        """非 tty 下 markup 标签被移除，输出纯文本。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        c.print("[red]error[/red] [bold]msg[/bold]")
        assert buf.getvalue() == "error msg\n"

    def test_custom_end(self) -> None:
        """``end=''`` 不追加换行。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        c.print("text", end="")
        assert buf.getvalue() == "text"

    def test_multiple_args_joined_by_sep(self) -> None:
        """多参数用 sep（默认空格）拼接。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        c.print("a", "b", "c")
        assert buf.getvalue() == "a b c\n"

    def test_custom_sep(self) -> None:
        """自定义 sep 分隔多参数。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        c.print("a", "b", sep="-")
        assert buf.getvalue() == "a-b\n"

    def test_style_wraps_text(self) -> None:
        """``style=`` 整体包裹文本（非 tty 下标签被移除）。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        c.print("msg", style="red")
        assert buf.getvalue() == "msg\n"

    def test_print_table_renders(self) -> None:
        """``print(table)`` 渲染表格字符串。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        t = Table(show_header=True, header_style="bold")
        t.add_column("k")
        t.add_row("v")
        c.print(t)
        out = buf.getvalue()
        assert "k" in out and "v" in out
        assert "+" in out  # 边框

    def test_unknown_kwargs_ignored(self) -> None:
        """rich 兼容关键字（highlight/justify 等）被忽略不报错。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        c.print("x", highlight=True, justify="center", soft_wrap=True, overflow="ellipsis", no_wrap=True)
        assert buf.getvalue() == "x\n"

    def test_ansi_color_when_tty(self) -> None:
        """tty 下 ANSI 转义码被输出（非 Windows 路径）。"""
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[method-assign]
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
            console, "_enable_vt_mode", return_value=True
        ):
            c = Console(file=buf)
        c.print("[red]error[/red]")
        out = buf.getvalue()
        assert "\033[31m" in out  # red ANSI 码
        assert "\033[0m" in out  # reset
        assert "error" in out

    def test_ansi_color_reset_at_segment_boundary(self) -> None:
        """标签结束后恢复默认（输出 reset 码）。"""
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[method-assign]
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
            console, "_enable_vt_mode", return_value=True
        ):
            c = Console(file=buf)
        c.print("[red]err[/red] plain")
        out = buf.getvalue()
        # err 段前后有 red 和 reset
        assert "\033[31m" in out
        assert "\033[0m" in out
        assert "plain" in out


# ---------------------------------------------------------------------- #
# Console legacy Windows 路径
# ---------------------------------------------------------------------- #


class TestConsoleLegacyWindows:
    """Win7/8 legacy 模式着色路径。"""

    def test_legacy_uses_set_console_text_attribute(self) -> None:
        """legacy 模式下调用 SetConsoleTextAttribute 切换颜色。"""
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[method-assign]
        fake_kernel = mock.Mock()
        with mock.patch.object(sys, "platform", "win32"):
            c = Console(legacy_windows=True, file=buf)
        c._kernel32 = fake_kernel
        c._color_enabled = True
        c.print("[red]error[/red]")
        # 调用了 SetConsoleTextAttribute（red=4，恢复默认=7）
        assert fake_kernel.SetConsoleTextAttribute.call_count >= 2
        # 输出纯文本（无 ANSI 码）
        assert buf.getvalue() == "error\n"

    def test_legacy_no_color_when_not_tty(self) -> None:
        """legacy 模式但非 tty 时不启用颜色。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(
            console, "_is_legacy_windows", return_value=True
        ):
            c = Console(legacy_windows=True, file=buf)
        assert c._color_enabled is False
        c.print("[red]error[/red]")
        assert buf.getvalue() == "error\n"

    def test_legacy_kernel32_load_failure_disables_color(self) -> None:
        """legacy 模式下 kernel32 加载失败时禁用颜色。"""
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[method-assign]
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(ctypes, "WinDLL", side_effect=OSError):
            c = Console(legacy_windows=True, file=buf)
        assert c._color_enabled is False


# ---------------------------------------------------------------------- #
# _enable_vt_mode
# ---------------------------------------------------------------------- #


class TestEnableVtMode:
    """``_enable_vt_mode`` Win10+ VT 启用。"""

    def test_non_windows_returns_true(self) -> None:
        """非 Windows 平台返回 True（无需启用）。"""
        with mock.patch.object(sys, "platform", "linux"):
            assert console._enable_vt_mode() is True

    def test_windows_load_failure_returns_false(self) -> None:
        """Win10+ 但 kernel32 加载失败时返回 False。"""
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(ctypes, "WinDLL", side_effect=OSError):
            assert console._enable_vt_mode() is False

    def test_windows_success_returns_true(self) -> None:
        """Win10+ kernel32 调用成功返回 True。"""
        fake_kernel = mock.Mock()
        fake_kernel.GetStdHandle.return_value = 1
        fake_kernel.GetConsoleMode.return_value = 1
        fake_kernel.SetConsoleMode.return_value = 1
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(ctypes, "WinDLL", return_value=fake_kernel):
            assert console._enable_vt_mode() is True
        # 验证设置了 VT 标志
        args = fake_kernel.SetConsoleMode.call_args
        new_mode = args[0][1]
        assert new_mode & 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING

    def test_windows_get_mode_failure_returns_false(self) -> None:
        """GetConsoleMode 失败时返回 False。"""
        fake_kernel = mock.Mock()
        fake_kernel.GetConsoleMode.return_value = 0  # 失败
        with mock.patch.object(sys, "platform", "win32"), mock.patch.object(ctypes, "WinDLL", return_value=fake_kernel):
            assert console._enable_vt_mode() is False


# ---------------------------------------------------------------------- #
# print_verbose
# ---------------------------------------------------------------------- #


class TestPrintVerbose:
    """``print_verbose`` 委托 Console.print。"""

    def test_print_verbose_delegates_to_console(self) -> None:
        """``print_verbose`` 调用全局 Console 的 print 方法。"""
        with mock.patch.object(console, "get_console") as mock_get:
            fake = mock.Mock()
            mock_get.return_value = fake
            console.print_verbose("hello", style="red")
            fake.print.assert_called_once_with("hello", style="red")


# ---------------------------------------------------------------------- #
# 集成：Console + Table 端到端
# ---------------------------------------------------------------------- #


class TestConsoleTableIntegration:
    """Console + Table 端到端渲染。"""

    def test_table_with_markup_cells_renders(self) -> None:
        """单元格含 markup 标签时渲染正确（非 tty 下纯文本）。"""
        buf = io.StringIO()
        buf.isatty = lambda: False  # type: ignore[method-assign]
        c = Console(file=buf)
        t = Table(show_header=True, header_style="bold")
        t.add_column("状态")
        t.add_row("[green]成功[/green]")
        t.add_row("[red]失败[/red]")
        c.print(t)
        out = buf.getvalue()
        assert "状态" in out
        assert "成功" in out
        assert "失败" in out
        assert "+" in out  # 边框

    def test_table_column_style_applied(self) -> None:
        """列 style 在 tty 下应用（验证不崩溃）。"""
        buf = io.StringIO()
        buf.isatty = lambda: True  # type: ignore[method-assign]
        with mock.patch.object(sys, "platform", "linux"), mock.patch.object(
            console, "_enable_vt_mode", return_value=True
        ):
            c = Console(file=buf)
        t = Table(show_header=True, header_style="bold")
        t.add_column("k", style="cyan")
        t.add_row("v")
        c.print(t)
        out = buf.getvalue()
        assert "k" in out and "v" in out
