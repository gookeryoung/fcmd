"""PDF 工具数据模型：页码表达式解析与拆分/合并规格。

为 :mod:`fcmd.cli.media.pdftool` 提供「CLI 字符串参数 → 结构化 dataclass」的解析层，
将页序、页码选择、拆分分组、合并编排等复杂语义收敛为可独立测试的纯函数模型：

* :class:`PageOrder` —— 页序（正序/倒序），支持 ``f/r/forward/reverse`` 写法。
* :class:`PageRange` —— 页码区间（1-based 含端点），支持 ``N`` / ``N-M`` / ``N-M:S``
  （步长）语法，``N>M`` 时倒序展开。
* :class:`PageSelection` —— 页码选择表达式（逗号分隔区间，如 ``1-3,5,8-10:2``），
  空/``-``/``all`` 表示全选。
* :class:`SplitSpec` —— 拆分规格：页序 + 固定步长（每份页数）或自定义分组
  （分号分隔，如 ``1-2;3,4,5``）。
* :class:`MergeMode` / :class:`MergeInput` / :class:`MergeSpec` —— 合并模式
  （顺序拼接/交叉合并）与合并输入（文件 + 页序 + 页码筛选）。

所有页码均为 1-based（对用户友好）；``resolve*`` 方法统一返回 0-based 页索引，
越界页号静默丢弃（与 reorder 子命令既有行为一致）。
"""

from __future__ import annotations

import enum
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "MergeInput",
    "MergeMode",
    "MergeSpec",
    "PageOrder",
    "PageRange",
    "PageSelection",
    "SplitSpec",
]


class PageOrder(enum.Enum):
    """页面遍历顺序。"""

    FORWARD = "forward"  # 正序
    REVERSE = "reverse"  # 倒序

    @classmethod
    def from_str(cls, value: str) -> PageOrder:
        """解析页序字符串，支持 forward/reverse 及首字母简写 f/r。

        Parameters
        ----------
        value:
            页序字符串（大小写不敏感）

        Raises
        ------
        ValueError
            取值不在支持范围内时抛出
        """
        normalized = value.strip().lower()
        mapping = {"f": cls.FORWARD, "forward": cls.FORWARD, "r": cls.REVERSE, "reverse": cls.REVERSE}
        if normalized not in mapping:
            raise ValueError(f"无效的页序: {value!r}（支持 forward/reverse 或 f/r）")
        return mapping[normalized]

    def arrange(self, indices: Sequence[int]) -> tuple[int, ...]:
        """按页序重排 0-based 页索引序列。"""
        return tuple(indices) if self is PageOrder.FORWARD else tuple(reversed(tuple(indices)))


@dataclass(frozen=True)
class PageRange:
    """页码区间（1-based，含端点，支持步长）。

    语法：``N``（单页）、``N-M``（闭区间）、``N-M:S``（步长 S）；
    ``N>M`` 时倒序展开（如 ``5-1`` → 5,4,3,2,1）。
    """

    start: int
    end: int
    step: int = 1

    def __post_init__(self) -> None:
        """校验页码与步长均为正整数。"""
        if self.start < 1 or self.end < 1:
            raise ValueError(f"页码必须为正整数（1-based）: {self.start}-{self.end}")
        if self.step < 1:
            raise ValueError(f"步长必须为正整数: {self.step}")

    def pages(self) -> tuple[int, ...]:
        """展开为 1-based 页码元组（``start > end`` 时倒序展开）。"""
        if self.start <= self.end:
            return tuple(range(self.start, self.end + 1, self.step))
        return tuple(range(self.start, self.end - 1, -self.step))

    @classmethod
    def parse(cls, token: str) -> PageRange:
        """解析单个页码区间 token（``N`` / ``N-M`` / ``N-M:S``）。

        Parameters
        ----------
        token:
            区间表达式，允许内部空白（如 ``"1 - 3 : 2"``）

        Raises
        ------
        ValueError
            表达式格式非法时抛出
        """
        token = "".join(token.split())
        step = 1
        if ":" in token:
            token, step_text = token.split(":", 1)
            step = _to_int(step_text, token)
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start = _to_int(start_text, token)
            end = _to_int(end_text, token)
        else:
            start = end = _to_int(token, token)
        return cls(start, end, step)


def _to_int(text: str, token: str) -> int:
    """将文本转为整数，失败时抛出含原始 token 的 ValueError。"""
    try:
        return int(text)
    except ValueError as exc:
        raise ValueError(f"无效的页码表达式: {token!r}") from exc


@dataclass(frozen=True)
class PageSelection:
    """页码选择表达式：逗号分隔的 :class:`PageRange` 列表。

    示例：``"1-3,5,8-10:2"`` → 页 1,2,3,5,8,10；
    空串 / ``"-"`` / ``"all"`` 表示全选。
    """

    ranges: tuple[PageRange, ...] = ()

    @classmethod
    def parse(cls, spec: str) -> PageSelection:
        """解析页码选择表达式；空白、``-``、``all`` 视为全选。

        Raises
        ------
        ValueError
            任一区间表达式非法时抛出
        """
        normalized = spec.strip().lower()
        if not normalized or normalized in ("-", "all"):
            return cls()
        ranges = tuple(PageRange.parse(token) for token in normalized.split(",") if token.strip())
        return cls(ranges)

    def is_all(self) -> bool:
        """是否全选（无区间约束）。"""
        return not self.ranges

    def resolve(self, page_count: int, order: PageOrder = PageOrder.FORWARD) -> tuple[int, ...]:
        """解析为 0-based 页索引元组（去重保序，越界丢弃）。

        Parameters
        ----------
        page_count:
            总页数，用于越界过滤
        order:
            页序；REVERSE 时按倒序返回（等价于先反转全集再应用选择）
        """
        if self.is_all():
            return order.arrange(range(page_count))
        seen: set[int] = set()
        result: list[int] = []
        for rng in self.ranges:
            for page in rng.pages():
                idx = page - 1
                if 0 <= idx < page_count and idx not in seen:
                    seen.add(idx)
                    result.append(idx)
        if order is PageOrder.REVERSE:
            result.reverse()
        return tuple(result)


@dataclass(frozen=True)
class SplitSpec:
    """拆分规格：页序 + 固定步长或自定义分组。

    ``groups`` 非空时优先于 ``every``：每个分组输出一份 PDF；
    组内页码按 ``order`` 排列。步长模式下先按 ``order`` 生成页序列，
    再按 ``every`` 切分。
    """

    order: PageOrder = PageOrder.FORWARD
    every: int = 1
    groups: tuple[PageSelection, ...] = ()

    def __post_init__(self) -> None:
        """校验步长为正整数。"""
        if self.every < 1:
            raise ValueError(f"步长必须为正整数: {self.every}")

    @classmethod
    def parse(cls, order: str = "forward", every: int = 1, groups: str = "") -> SplitSpec:
        """从 CLI 参数构建拆分规格。

        Parameters
        ----------
        order:
            页序字符串（forward/reverse 或 f/r）
        every:
            固定步长（每份页数，1 = 单页）
        groups:
            自定义分组表达式：分号分隔的多组页码选择
            （如 ``"1-2;3,4,5"`` → 第一份含页 1-2，第二份含页 3、4、5）

        Raises
        ------
        ValueError
            任一表达式非法时抛出
        """
        selections: tuple[PageSelection, ...] = ()
        if groups.strip():
            selections = tuple(PageSelection.parse(part) for part in groups.split(";") if part.strip())
        return cls(PageOrder.from_str(order), every, selections)

    def resolve(self, page_count: int) -> tuple[tuple[int, ...], ...]:
        """解析为输出分组：每份 PDF 的 0-based 页索引元组序列。

        解析为空的分组（页码全部越界）被跳过。
        """
        if self.groups:
            chunks: list[tuple[int, ...]] = []
            for selection in self.groups:
                resolved = selection.resolve(page_count, self.order)
                if resolved:
                    chunks.append(resolved)
            return tuple(chunks)
        sequence = self.order.arrange(range(page_count))
        return tuple(sequence[i : i + self.every] for i in range(0, len(sequence), self.every))


class MergeMode(enum.Enum):
    """合并模式。"""

    CONCAT = "concat"  # 顺序拼接：逐文件依次追加
    INTERLEAVE = "interleave"  # 交叉合并：多文件轮流取页

    @classmethod
    def from_str(cls, value: str) -> MergeMode:
        """解析合并模式字符串，支持 concat/interleave 及首字母简写 c/i。

        Raises
        ------
        ValueError
            取值不在支持范围内时抛出
        """
        normalized = value.strip().lower()
        mapping = {"c": cls.CONCAT, "concat": cls.CONCAT, "i": cls.INTERLEAVE, "interleave": cls.INTERLEAVE}
        if normalized not in mapping:
            raise ValueError(f"无效的合并模式: {value!r}（支持 concat/interleave 或 c/i）")
        return mapping[normalized]


@dataclass(frozen=True)
class MergeInput:
    """单个合并输入：文件 + 页序 + 页码筛选。"""

    path: Path
    order: PageOrder = PageOrder.FORWARD
    selection: PageSelection = PageSelection()

    def resolve(self, page_count: int) -> tuple[int, ...]:
        """解析为该输入参与合并的 0-based 页索引序列。"""
        return self.selection.resolve(page_count, self.order)


@dataclass(frozen=True)
class MergeSpec:
    """合并规格：模式 + 输入序列。"""

    mode: MergeMode = MergeMode.CONCAT
    inputs: tuple[MergeInput, ...] = ()

    @classmethod
    def from_cli(
        cls,
        paths: Sequence[Path],
        mode: str = "concat",
        orders: Sequence[str] = (),
        pages: Sequence[str] = (),
    ) -> MergeSpec:
        """从 CLI 参数构建合并规格。

        ``orders`` / ``pages`` 与 ``paths`` 按位置一一对应：
        缺省条目分别默认正序/全选，多余条目忽略。

        Parameters
        ----------
        paths:
            输入文件路径序列
        mode:
            合并模式字符串（concat/interleave 或 c/i）
        orders:
            各文件页序字符串序列（forward/reverse 或 f/r）
        pages:
            各文件页码筛选表达式序列（空/``-``/``all`` 表示全选）

        Raises
        ------
        ValueError
            任一表达式非法时抛出
        """
        inputs = []
        for i, path in enumerate(paths):
            order = PageOrder.from_str(orders[i]) if i < len(orders) else PageOrder.FORWARD
            selection = PageSelection.parse(pages[i]) if i < len(pages) else PageSelection()
            inputs.append(MergeInput(Path(path), order, selection))
        return cls(MergeMode.from_str(mode), tuple(inputs))

    def resolve(self, page_counts: Sequence[int]) -> list[tuple[int, int]]:
        """解析为最终合并顺序：``(输入序号, 0-based 页索引)`` 列表。

        CONCAT：逐输入按其页序列依次追加；
        INTERLEAVE：各输入轮流取下一页（round-robin），如 01.pdf 正序
        ``[a1,a2]`` 与 02.pdf 倒序 ``[b2,b1]`` 交叉 → ``[a1,b2,a2,b1]``。

        Parameters
        ----------
        page_counts:
            各输入的总页数序列（与 inputs 按位置对应）
        """
        sequences = [inp.resolve(page_counts[i]) for i, inp in enumerate(self.inputs)]
        if self.mode is MergeMode.CONCAT:
            return [(i, idx) for i, seq in enumerate(sequences) for idx in seq]
        plan: list[tuple[int, int]] = []
        cursor = 0
        while any(cursor < len(seq) for seq in sequences):
            for i, seq in enumerate(sequences):
                if cursor < len(seq):
                    plan.append((i, seq[cursor]))
            cursor += 1
        return plan
