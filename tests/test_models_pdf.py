"""PDF 数据模型测试。

验证 ``fcmd.models.pdf`` 模块：
- PageOrder/PageRange/PageSelection 页码表达式解析
- SplitSpec 拆分规格（步长/分组/页序）
- MergeSpec 合并规格（拼接/交叉/逐文件页序与筛选）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.models.pdf import (
    MergeInput,
    MergeMode,
    MergeSpec,
    PageOrder,
    PageRange,
    PageSelection,
    SplitSpec,
)


# ---------------------------------------------------------------------- #
# PageOrder
# ---------------------------------------------------------------------- #
class TestPageOrder:
    """PageOrder 页序解析。"""

    @pytest.mark.parametrize("value", ["forward", "f", "FORWARD", " F "])
    def test_from_str_forward(self, value: str) -> None:
        """forward 及其简写/大小写/空白变体解析为正序。"""
        assert PageOrder.from_str(value) is PageOrder.FORWARD

    @pytest.mark.parametrize("value", ["reverse", "r", "REVERSE"])
    def test_from_str_reverse(self, value: str) -> None:
        """reverse 及其简写解析为倒序。"""
        assert PageOrder.from_str(value) is PageOrder.REVERSE

    def test_from_str_invalid(self) -> None:
        """非法取值抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的页序"):
            PageOrder.from_str("x")

    def test_arrange(self) -> None:
        """arrange 按页序重排索引序列。"""
        assert PageOrder.FORWARD.arrange([0, 1, 2]) == (0, 1, 2)
        assert PageOrder.REVERSE.arrange(range(3)) == (2, 1, 0)


# ---------------------------------------------------------------------- #
# PageRange
# ---------------------------------------------------------------------- #
class TestPageRange:
    """PageRange 页码区间。"""

    def test_parse_single(self) -> None:
        """单页 token 解析为 start == end。"""
        assert PageRange.parse("5") == PageRange(5, 5)

    def test_parse_range_with_spaces(self) -> None:
        """含空白的区间表达式被归一化。"""
        assert PageRange.parse("1 - 3 : 2") == PageRange(1, 3, 2)

    def test_parse_invalid_token(self) -> None:
        """非法数字抛 ValueError（含原始 token）。"""
        with pytest.raises(ValueError, match="无效的页码表达式"):
            PageRange.parse("a-b")

    def test_parse_invalid_step(self) -> None:
        """非法步长抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的页码表达式"):
            PageRange.parse("1-3:x")

    def test_post_init_invalid_page(self) -> None:
        """页码非正整数抛 ValueError。"""
        with pytest.raises(ValueError, match="页码必须为正整数"):
            PageRange(0, 3)

    def test_post_init_invalid_step(self) -> None:
        """步长非正整数抛 ValueError。"""
        with pytest.raises(ValueError, match="步长必须为正整数"):
            PageRange(1, 3, 0)

    def test_pages_ascending(self) -> None:
        """正序区间展开（含步长）。"""
        assert PageRange(1, 5).pages() == (1, 2, 3, 4, 5)
        assert PageRange(1, 5, 2).pages() == (1, 3, 5)

    def test_pages_descending(self) -> None:
        """倒序区间（start > end）展开，含步长。"""
        assert PageRange(5, 1).pages() == (5, 4, 3, 2, 1)
        assert PageRange(5, 1, 2).pages() == (5, 3, 1)


# ---------------------------------------------------------------------- #
# PageSelection
# ---------------------------------------------------------------------- #
class TestPageSelection:
    """PageSelection 页码选择表达式。"""

    @pytest.mark.parametrize("spec", ["", "  ", "-", "all", "ALL"])
    def test_parse_all(self, spec: str) -> None:
        """空白/-/all 均视为全选。"""
        assert PageSelection.parse(spec).is_all()

    def test_parse_ranges(self) -> None:
        """逗号分隔区间解析为 PageRange 元组。"""
        selection = PageSelection.parse("1-3,5")
        assert selection.ranges == (PageRange(1, 3), PageRange(5, 5))
        assert not selection.is_all()

    def test_parse_invalid(self) -> None:
        """非法表达式抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的页码表达式"):
            PageSelection.parse("1-x")

    def test_resolve_all_forward(self) -> None:
        """全选 + 正序返回全部索引。"""
        assert PageSelection().resolve(3) == (0, 1, 2)

    def test_resolve_all_reverse(self) -> None:
        """全选 + 倒序返回倒排索引。"""
        assert PageSelection().resolve(3, PageOrder.REVERSE) == (2, 1, 0)

    def test_resolve_dedup_and_out_of_range(self) -> None:
        """去重保序且越界页号丢弃。"""
        selection = PageSelection.parse("1-3,2,9")
        assert selection.resolve(4) == (0, 1, 2)

    def test_resolve_reverse_order(self) -> None:
        """选择 + 倒序按倒排返回。"""
        selection = PageSelection.parse("1-3,5")
        assert selection.resolve(5, PageOrder.REVERSE) == (4, 2, 1, 0)

    def test_resolve_empty_when_all_out_of_range(self) -> None:
        """全部越界时解析为空。"""
        assert PageSelection.parse("8-9").resolve(2) == ()


# ---------------------------------------------------------------------- #
# SplitSpec
# ---------------------------------------------------------------------- #
class TestSplitSpec:
    """SplitSpec 拆分规格。"""

    def test_parse_default(self) -> None:
        """默认：正序 + 单页步长 + 无分组。"""
        spec = SplitSpec.parse()
        assert spec.order is PageOrder.FORWARD
        assert spec.every == 1
        assert spec.groups == ()

    def test_parse_groups_with_empty_parts(self) -> None:
        """分组表达式中的空段（连续分号）被过滤。"""
        spec = SplitSpec.parse(groups="1-2;;3,")
        assert len(spec.groups) == 2

    def test_parse_invalid_order(self) -> None:
        """非法页序抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的页序"):
            SplitSpec.parse(order="x")

    def test_parse_invalid_every(self) -> None:
        """非正步长抛 ValueError。"""
        with pytest.raises(ValueError, match="步长必须为正整数"):
            SplitSpec.parse(every=0)

    def test_resolve_single_pages(self) -> None:
        """默认步长 1：每页一份。"""
        assert SplitSpec.parse().resolve(3) == ((0,), (1,), (2,))

    def test_resolve_every(self) -> None:
        """固定步长切分，末份可为余数。"""
        spec = SplitSpec.parse(every=2)
        assert spec.resolve(5) == ((0, 1), (2, 3), (4,))

    def test_resolve_every_reverse(self) -> None:
        """倒序 + 步长：先倒排页序列再切分。"""
        spec = SplitSpec.parse(order="r", every=2)
        assert spec.resolve(3) == ((2, 1), (0,))

    def test_resolve_groups(self) -> None:
        """自定义分组：每份页码按表达式分组。"""
        spec = SplitSpec.parse(groups="1-2;3,4,5")
        assert spec.resolve(5) == ((0, 1), (2, 3, 4))

    def test_resolve_groups_reverse(self) -> None:
        """分组 + 倒序：组内页码倒排，组顺序不变。"""
        spec = SplitSpec.parse(order="reverse", groups="1-2;3")
        assert spec.resolve(3) == ((1, 0), (2,))

    def test_resolve_groups_skip_empty(self) -> None:
        """页码全部越界的分组被跳过。"""
        spec = SplitSpec.parse(groups="1;9")
        assert spec.resolve(2) == ((0,),)

    def test_resolve_empty_document(self) -> None:
        """空文档解析为空分组。"""
        assert SplitSpec.parse().resolve(0) == ()


# ---------------------------------------------------------------------- #
# MergeSpec
# ---------------------------------------------------------------------- #
class TestMergeMode:
    """MergeMode 合并模式解析。"""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("concat", MergeMode.CONCAT),
            ("c", MergeMode.CONCAT),
            ("interleave", MergeMode.INTERLEAVE),
            ("i", MergeMode.INTERLEAVE),
        ],
    )
    def test_from_str(self, value: str, expected: MergeMode) -> None:
        """concat/interleave 及首字母简写解析。"""
        assert MergeMode.from_str(value) is expected

    def test_from_str_invalid(self) -> None:
        """非法模式抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的合并模式"):
            MergeMode.from_str("x")


class TestMergeSpec:
    """MergeSpec 合并规格。"""

    def test_from_cli_defaults(self) -> None:
        """orders/pages 缺省时各输入默认正序/全选。"""
        spec = MergeSpec.from_cli([Path("a.pdf"), Path("b.pdf")])
        assert spec.mode is MergeMode.CONCAT
        assert len(spec.inputs) == 2
        assert all(inp.order is PageOrder.FORWARD and inp.selection.is_all() for inp in spec.inputs)

    def test_from_cli_per_file(self) -> None:
        """orders/pages 按位置对应输入文件。"""
        spec = MergeSpec.from_cli([Path("a.pdf"), Path("b.pdf")], orders=["f", "r"], pages=["1", ""])
        assert spec.inputs[0].order is PageOrder.FORWARD
        assert spec.inputs[1].order is PageOrder.REVERSE
        assert not spec.inputs[0].selection.is_all()
        assert spec.inputs[1].selection.is_all()

    def test_from_cli_invalid_order(self) -> None:
        """非法页序抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的页序"):
            MergeSpec.from_cli([Path("a.pdf")], orders=["x"])

    def test_from_cli_invalid_mode(self) -> None:
        """非法合并模式抛 ValueError。"""
        with pytest.raises(ValueError, match="无效的合并模式"):
            MergeSpec.from_cli([Path("a.pdf")], mode="x")

    def test_resolve_concat(self) -> None:
        """顺序拼接：逐输入按其页序列依次追加。"""
        spec = MergeSpec(
            inputs=(
                MergeInput(Path("a.pdf")),
                MergeInput(Path("b.pdf"), PageOrder.REVERSE),
            )
        )
        assert spec.resolve([2, 2]) == [(0, 0), (0, 1), (1, 1), (1, 0)]

    def test_resolve_interleave_mixed_order(self) -> None:
        """交叉合并：01.pdf 正序与 02.pdf 倒序轮流取页。"""
        spec = MergeSpec(
            mode=MergeMode.INTERLEAVE,
            inputs=(
                MergeInput(Path("01.pdf")),
                MergeInput(Path("02.pdf"), PageOrder.REVERSE),
            ),
        )
        assert spec.resolve([2, 2]) == [(0, 0), (1, 1), (0, 1), (1, 0)]

    def test_resolve_interleave_unequal_lengths(self) -> None:
        """交叉合并且长度不等：短输入耗尽后其余输入继续。"""
        spec = MergeSpec(
            mode=MergeMode.INTERLEAVE,
            inputs=(
                MergeInput(Path("a.pdf")),
                MergeInput(Path("b.pdf"), selection=PageSelection.parse("1")),
            ),
        )
        assert spec.resolve([2, 2]) == [(0, 0), (1, 0), (0, 1)]

    def test_resolve_with_page_selection(self) -> None:
        """页码筛选参与合并：仅筛选后的页进入合并序列。"""
        spec = MergeSpec(
            inputs=(
                MergeInput(Path("a.pdf"), selection=PageSelection.parse("2-3")),
                MergeInput(Path("b.pdf"), selection=PageSelection.parse("1")),
            )
        )
        assert spec.resolve([3, 2]) == [(0, 1), (0, 2), (1, 0)]

    def test_resolve_empty_inputs(self) -> None:
        """无输入时解析为空计划。"""
        assert MergeSpec().resolve([]) == []
