"""console 模块测试。

console 已是 rich 的薄封装，因此这里只覆盖**项目依赖的契约**：
单例、markup/纯文本降级、print 关键字透传、Table 别名映射、
``supports_unicode`` 编码探测。

rich 自身的排版细节（换行、裁剪、box 字符）由 rich 的测试保障，不再重复。
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from unittest import mock

import pytest
from rich import box as rich_box

from fcmd import console
from fcmd.console import Console, Table


@pytest.fixture(autouse=True)
def _reset_console_cache() -> Iterator[None]:
    """每个用例独立的 Console 缓存，避免相互污染。"""
    sentinel = console._console
    console._console = None
    yield
    console._console = sentinel


def _plain_console() -> tuple[Console, io.StringIO]:
    """构造非 tty Console（markup 被剥离、无 ANSI），返回 (console, buffer)。"""
    buf = io.StringIO()
    buf.isatty = lambda: False  # type: ignore[method-assign]
    return Console(file=buf), buf


class TestGetConsole:
    """全局 Console 单例。"""

    def test_returns_cached_instance(self) -> None:
        """二次调用返回同一实例。"""
        assert console.get_console() is console.get_console()

    def test_reset_yields_new_instance(self) -> None:
        """清空缓存后重新构造。"""
        first = console.get_console()
        console._console = None
        assert console.get_console() is not first


class TestSupportsUnicode:
    """``supports_unicode`` 终端编码能力探测。"""

    def test_stream_without_encoding_returns_true(self) -> None:
        """无 encoding 属性的流（StringIO）按支持处理。"""
        assert console.supports_unicode(io.StringIO()) is True

    def test_utf8_stream_returns_true(self) -> None:
        """UTF-8 流含圆角制表符，返回 True。"""
        assert console.supports_unicode(io.TextIOWrapper(io.BytesIO(), encoding="utf-8")) is True

    def test_ascii_stream_returns_false(self) -> None:
        """ASCII 流不含 ╭（U+256D），返回 False。"""
        assert console.supports_unicode(io.TextIOWrapper(io.BytesIO(), encoding="ascii")) is False

    def test_cp437_stream_returns_false(self) -> None:
        """cp437（IBM PC）流不含圆角制表符，返回 False。"""
        assert console.supports_unicode(io.TextIOWrapper(io.BytesIO(), encoding="cp437")) is False

    def test_invalid_encoding_name_returns_false(self) -> None:
        """encoding 属性存在但编码名非法时返回 False（LookupError 路径）。"""

        class _FakeStream:
            encoding = "nonexistent-xyz"

        assert console.supports_unicode(_FakeStream()) is False


class TestConsolePrint:
    """``Console.print`` 输出契约。"""

    def test_plain_text_to_non_tty(self) -> None:
        """非 tty 输出纯文本，默认末尾换行。"""
        c, buf = _plain_console()
        c.print("hello")
        assert buf.getvalue() == "hello\n"

    def test_markup_stripped_in_non_tty(self) -> None:
        """非 tty 下 markup 标签被移除，输出纯文本。"""
        c, buf = _plain_console()
        c.print("[red]error[/red] [bold]msg[/bold]")
        assert buf.getvalue() == "error msg\n"

    def test_custom_end(self) -> None:
        """``end=''`` 不追加换行。"""
        c, buf = _plain_console()
        c.print("text", end="")
        assert buf.getvalue() == "text"

    def test_multiple_args_joined_by_sep(self) -> None:
        """多参数用 sep（默认空格）拼接。"""
        c, buf = _plain_console()
        c.print("a", "b", "c")
        assert buf.getvalue() == "a b c\n"

    def test_custom_sep(self) -> None:
        """自定义 sep 分隔多参数。"""
        c, buf = _plain_console()
        c.print("a", "b", sep="-")
        assert buf.getvalue() == "a-b\n"

    def test_style_applied_in_non_tty(self) -> None:
        """``style=`` 在非 tty 下只输出纯文本。"""
        c, buf = _plain_console()
        c.print("msg", style="red")
        assert buf.getvalue() == "msg\n"

    def test_markup_false_writes_raw_text(self) -> None:
        """``markup=False`` 原样输出，不解析 [...] 标签。"""
        c, buf = _plain_console()
        c.print("b[/text/]", markup=False)
        assert buf.getvalue() == "b[/text/]\n"

    def test_unknown_kwargs_ignored(self) -> None:
        """未列入白名单的 rich 关键字被忽略，不抛异常。"""
        c, buf = _plain_console()
        c.print("x", highlight=True, overflow="ellipsis")
        assert buf.getvalue() == "x\n"

    def test_ansi_color_when_forced(self) -> None:
        """强制终端 + 指定 color_system 时输出 ANSI 转义码。"""
        buf = io.StringIO()
        c = Console(file=buf, force_terminal=True, color_system="standard")
        c.print("[red]error[/red]")
        out = buf.getvalue()
        assert "\033[31m" in out
        assert "\033[0m" in out
        assert "error" in out

    def test_long_line_not_wrapped(self) -> None:
        """长文本不被终端宽度折行（CLI 输出契约）。"""
        c, buf = _plain_console()
        text = "x" * 300
        c.print(text)
        assert buf.getvalue() == text + "\n"

    def test_print_table_renders(self) -> None:
        """``print(table)`` 渲染为带边框的表格。"""
        c, buf = _plain_console()
        t = Table(show_header=True, header_style="bold")
        t.add_column("k")
        t.add_row("v")
        c.print(t)
        out = buf.getvalue()
        assert "k" in out
        assert "v" in out
        assert "+" in out


class TestTable:
    """``Table`` 的 box 别名映射与列选项。"""

    def test_default_box_is_ascii(self) -> None:
        """默认边框为纯 ASCII（Windows conhost 安全）。"""
        assert Table().box is rich_box.ASCII

    def test_round_box(self) -> None:
        """``box="round"`` 映射到 rich ROUNDED。"""
        assert Table(box="round").box is rich_box.ROUNDED

    def test_no_box(self) -> None:
        """``box=None`` 无边框。"""
        assert Table(box=None).box is None

    def test_unknown_box_falls_back_ascii(self) -> None:
        """未知 box 名回退 ASCII。"""
        assert Table(box="nope").box is rich_box.ASCII

    def test_auto_box_follows_unicode_support(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``box="auto"`` 按终端编码能力选择圆角或 ASCII。"""
        monkeypatch.setattr(console, "supports_unicode", lambda *a: True)
        assert Table(box="auto").box is rich_box.ROUNDED
        monkeypatch.setattr(console, "supports_unicode", lambda *a: False)
        assert Table(box="auto").box is rich_box.ASCII

    def test_column_options_accepted(self) -> None:
        """``style`` / ``no_wrap`` / ``justify`` 透传给 rich 列。"""
        t = Table()
        t.add_column("a", style="cyan", no_wrap=True, justify="right")
        col = t.columns[0]
        assert col.no_wrap is True
        assert col.justify == "right"

    def test_rows_rendered_with_title_and_lines(self) -> None:
        """标题与行间分隔线生效。"""
        c, buf = _plain_console()
        t = Table(title="标题", show_lines=True)
        t.add_column("a")
        t.add_row("1")
        t.add_row("2")
        c.print(t)
        out = buf.getvalue()
        assert "标题" in out
        assert "1" in out
        assert "2" in out


class TestPrintVerbose:
    """``print_verbose`` 委托全局 Console。"""

    def test_print_verbose_delegates_to_console(self) -> None:
        """``print_verbose`` 调用全局 Console 的 print 方法。"""
        with mock.patch.object(console, "get_console") as mock_get:
            fake = mock.Mock()
            mock_get.return_value = fake
            console.print_verbose("hello", style="red")
            fake.print.assert_called_once_with("hello", style="red")


class TestPrintError:
    """``print_error`` 统一错误行格式。"""

    def test_prefix_is_red_markup(self) -> None:
        """前缀以红色 markup 传给全局 Console，正文原样。"""
        with mock.patch.object(console, "get_console") as mock_get:
            fake = mock.Mock()
            mock_get.return_value = fake
            console.print_error("boom")
            fake.print.assert_called_once_with("[red]错误:[/red] boom")

    def test_non_tty_renders_plain_text(self) -> None:
        """非 tty 下 markup 被剥离，输出纯文本 ``错误: ...``。"""
        c, buf = _plain_console()
        with mock.patch.object(console, "get_console", return_value=c):
            console.print_error("文件不存在: a.txt")
        assert buf.getvalue() == "错误: 文件不存在: a.txt\n"
