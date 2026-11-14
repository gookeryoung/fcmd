"""``fcmd.gui.sidebar`` 的纯逻辑测试：key 解析与循环切换。

Tk 控件部分标注 ``pragma: no cover``（CI 无显示环境），这里只覆盖不依赖
Tk 的函数，保证迁移窗口时 ``select`` / 快捷键的语义稳定。
"""

from __future__ import annotations

import pytest

from fcmd.gui import sidebar as _guisidebar


class TestResolvePageKey:
    def test_accepts_known_key(self) -> None:
        assert _guisidebar.resolve_page_key(("config", "monitor"), "monitor") == "monitor"

    def test_accepts_index(self) -> None:
        keys = ("config", "monitor", "bench")
        assert _guisidebar.resolve_page_key(keys, 0) == "config"
        assert _guisidebar.resolve_page_key(keys, 2) == "bench"
        assert _guisidebar.resolve_page_key(keys, -1) == "bench"

    def test_unknown_key_raises(self) -> None:
        with pytest.raises(KeyError):
            _guisidebar.resolve_page_key(("config",), "missing")

    def test_out_of_range_index_raises(self) -> None:
        with pytest.raises(KeyError):
            _guisidebar.resolve_page_key(("config",), 3)

    def test_rejects_non_key_types(self) -> None:
        for target in (True, None, 1.5):
            with pytest.raises(KeyError):
                _guisidebar.resolve_page_key(("config",), target)  # type: ignore[arg-type]


class TestNextPage:
    def test_wraps_forward_and_backward(self) -> None:
        keys = ("a", "b", "c")
        assert _guisidebar.next_page(keys, "a") == "b"
        assert _guisidebar.next_page(keys, "c") == "a"
        assert _guisidebar.next_page(keys, "a", -1) == "c"

    def test_starts_from_first_when_current_unknown(self) -> None:
        keys = ("a", "b")
        assert _guisidebar.next_page(keys, None) == "a"
        assert _guisidebar.next_page(keys, "missing") == "a"
        assert _guisidebar.next_page(keys, "missing", -1) in keys

    def test_empty_sequence_returns_empty(self) -> None:
        assert _guisidebar.next_page((), "a") == ""
