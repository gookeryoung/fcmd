"""要点层级解析：缩进、列表标记与上限。"""

from __future__ import annotations

from fcmd.apps.pptmake._bullets import MAX_LEVEL, bullet_level, split_bullet


def test_plain_bullet() -> None:
    assert split_bullet("营收增长") == (0, "营收增长")
    assert split_bullet("  - 子项") == (1, "子项")
    assert split_bullet("\t• 更深") == (1, "更深")


def test_max_level_is_capped() -> None:
    assert split_bullet("      * 第四级") == (MAX_LEVEL, "第四级")


def test_empty_and_marker_only() -> None:
    assert split_bullet("   ") == (0, "")
    assert split_bullet("  - ") == (1, "")
    assert bullet_level("    * 子要点") == 2
