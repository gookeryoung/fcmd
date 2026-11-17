"""``fcmd.gui.qt.layout`` 纯函数测试：中间省略、列宽分配、分栏尺寸解析。

这些函数不导入 Qt，用固定字宽测量即可在无显示环境下验证边界条件。
"""

from __future__ import annotations

import pytest

from fcmd.gui.qt.layout import distribute_widths, elide_middle, parse_sizes, serialize_sizes


def _measure(text: str) -> int:
    """固定字宽：每个字符 10 像素。"""
    return len(text) * 10


_LONG_PATH = "C:/Users/demo/Projects/very/long/path/to/some-file.txt"


def test_elide_keeps_short_text() -> None:
    assert elide_middle("abc", 100, _measure) == "abc"


def test_elide_empty_or_zero_width() -> None:
    assert elide_middle("", 100, _measure) == ""
    assert elide_middle(_LONG_PATH, 0, _measure) == ""
    assert elide_middle(_LONG_PATH, -10, _measure) == ""


def test_elide_middle_preserves_head_and_tail() -> None:
    shown = elide_middle(_LONG_PATH, 120, _measure, ellipsis="…")
    assert "…" in shown
    assert shown.startswith("C:/")
    assert shown.endswith(".txt")
    assert _measure(shown) <= 120


def test_elide_falls_back_to_empty_when_ellipsis_too_wide() -> None:
    assert elide_middle(_LONG_PATH, 5, _measure, ellipsis="…") == ""


def test_elide_head_ratio_one_drops_tail() -> None:
    shown = elide_middle(_LONG_PATH, 120, _measure, head_ratio=1.0)
    assert shown.startswith("C:/")
    assert shown.endswith("…")
    assert _measure(shown) <= 120


def test_elide_ratio_is_clamped() -> None:
    shown = elide_middle(_LONG_PATH, 120, _measure, head_ratio=5.0)
    assert shown.endswith("…")


def test_distribute_splits_evenly_when_ratios_empty() -> None:
    assert distribute_widths(100, [0, 0]) == [50, 50]


def test_distribute_respects_ratios_and_minimum() -> None:
    widths = distribute_widths(1000, [1, 0, 3], minimum=40)
    assert widths == [260, 40, 700]
    assert sum(widths) == 1000


def test_distribute_equalizes_when_total_below_floor() -> None:
    widths = distribute_widths(100, [1, 1, 1], minimum=40)
    assert sum(widths) == 100
    assert max(widths) - min(widths) <= 1
    assert all(width < 40 for width in widths)


def test_distribute_gives_rounding_remainder_to_widest() -> None:
    widths = distribute_widths(101, [1, 1, 1], minimum=10)
    assert sum(widths) == 101
    assert widths[0] == 35
    assert widths[1:] == [33, 33]


def test_distribute_handles_zero_total_and_empty_ratios() -> None:
    assert distribute_widths(0, [1, 1]) == [0, 0]
    assert distribute_widths(-5, [1]) == [0]
    assert distribute_widths(100, []) == []


def test_distribute_ignores_negative_ratios() -> None:
    widths = distribute_widths(200, [-1, 1], minimum=40)
    assert widths == [40, 160]


@pytest.mark.parametrize("raw", [[100, 200], (100, 200)])
def test_parse_sizes_accepts_positive_int_lists(raw: object) -> None:
    assert parse_sizes(raw) == [100, 200]


@pytest.mark.parametrize(
    "raw",
    [None, "100x200", [], [100], [0, 10], [-1, 10], [True, 10], [1.5, 2], ["1", "2"], {"a": 1}],
)
def test_parse_sizes_rejects_invalid(raw: object) -> None:
    assert parse_sizes(raw) is None


def test_serialize_sizes_normalizes_values() -> None:
    assert serialize_sizes([1.7, -2, 3]) == [1, 0, 3]  # type: ignore[list-item]
