"""pptmake 预览纯逻辑：视口适配、文本测量、裁剪蒙版与溢出预检。

Tk 预览与 PPTX 共享同一份绘制列表，但控件尺寸、字号像素与换行规则不同。
本模块只做与 GUI 无关的计算：

- **视口**：把设计坐标按比例映射到画布，居中留白；缩放与窗口适配共用
  :class:`Viewport`，避免预览代码自己算坐标。
- **裁剪**：Tk Canvas 无法按页面矩形裁剪图元，负数坐标的光斑会画到画布
  留白里。:meth:`Viewport.matte_rects` 给出页面之外的四块遮罩矩形，先画
  内容再盖遮罩即可等价裁剪，不需要改动图元本身。
- **文本测量**：按字符类别估算宽度与换行行数，用于判断文本框是否溢出；
  结果偏保守（宁多勿少），只用于提示，不参与真实导出。
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Final

from fcmd.apps.pptmake._design import Primitive, Text

__all__ = [
    "OverflowIssue",
    "Viewport",
    "char_em",
    "estimate_text_height",
    "fit_viewport",
    "measure_text",
    "overflow_issues",
    "text_overflow",
    "wrap_text",
]

# 各类字符的相对字宽（em）；用于无字体环境下的保守估算。
_SPACE_EM: Final[float] = 0.28
_FULLWIDTH_EM: Final[float] = 1.0
_DIGIT_EM: Final[float] = 0.56
_UPPER_EM: Final[float] = 0.64
_LOWER_EM: Final[float] = 0.54
_NARROW_EM: Final[float] = 0.28
_OTHER_EM: Final[float] = 0.38
_BOLD_FACTOR: Final[float] = 1.03
_MIN_SCALE: Final[float] = 0.001


@dataclass(frozen=True, slots=True)
class Viewport:
    """设计坐标与画布坐标的映射：缩放 + 居中偏移。"""

    scale: float
    offset_x: float
    offset_y: float
    design_width: float
    design_height: float
    canvas_width: float
    canvas_height: float

    def to_canvas(self, x: float, y: float) -> tuple[float, float]:
        """设计坐标 → 画布坐标。"""
        return self.offset_x + x * self.scale, self.offset_y + y * self.scale

    def to_design(self, x: float, y: float) -> tuple[float, float]:
        """画布坐标 → 设计坐标。"""
        if self.scale <= 0:
            return 0.0, 0.0
        return (x - self.offset_x) / self.scale, (y - self.offset_y) / self.scale

    @property
    def page_width(self) -> float:
        """页面在画布上的宽度（像素）。"""
        return self.design_width * self.scale

    @property
    def page_height(self) -> float:
        """页面在画布上的高度（像素）。"""
        return self.design_height * self.scale

    def page_rect(self) -> tuple[float, float, float, float]:
        """页面矩形 ``(x0, y0, x1, y1)``（画布坐标）。"""
        return self.offset_x, self.offset_y, self.offset_x + self.page_width, self.offset_y + self.page_height

    def matte_rects(self) -> tuple[tuple[float, float, float, float], ...]:
        """页面之外的四块遮罩矩形，用于等价裁剪。"""
        x0, y0, x1, y1 = self.page_rect()
        width = self.canvas_width
        height = self.canvas_height
        rects: list[tuple[float, float, float, float]] = []
        if y0 > 0:
            rects.append((0.0, 0.0, width, y0))
        if y1 < height:
            rects.append((0.0, y1, width, height))
        if x0 > 0:
            rects.append((0.0, max(y0, 0.0), x0, min(y1, height)))
        if x1 < width:
            rects.append((x1, max(y0, 0.0), width, min(y1, height)))
        return tuple(rects)


def fit_viewport(  # noqa: PLR0913 - 视口工厂需要画幅、画布、留白与缩放
    design_width: float,
    design_height: float,
    canvas_width: float,
    canvas_height: float,
    *,
    padding: float = 0.0,
    zoom: float = 1.0,
) -> Viewport:
    """按「完整可见」计算视口；``zoom`` 在适口比例上再放大，``padding`` 留白。"""
    if design_width <= 0 or design_height <= 0:
        raise ValueError(f"设计尺寸需为正数：{design_width}×{design_height}")
    available_width = max(canvas_width - padding * 2, 1.0)
    available_height = max(canvas_height - padding * 2, 1.0)
    contain = min(available_width / design_width, available_height / design_height)
    scale = max(contain * max(zoom, _MIN_SCALE), _MIN_SCALE)
    offset_x = (canvas_width - design_width * scale) / 2
    offset_y = (canvas_height - design_height * scale) / 2
    return Viewport(
        scale=scale,
        offset_x=offset_x,
        offset_y=offset_y,
        design_width=design_width,
        design_height=design_height,
        canvas_width=canvas_width,
        canvas_height=canvas_height,
    )


# ---------------------------------------------------------------------- #
# 文本测量
# ---------------------------------------------------------------------- #
def char_em(char: str, *, bold: bool = False) -> float:
    """单个字符的相对字宽（em）；粗体略微加宽。"""
    if char.isspace():
        base = _SPACE_EM
    elif unicodedata.east_asian_width(char) in ("W", "F", "A"):
        base = _FULLWIDTH_EM
    elif char.isdigit():
        base = _DIGIT_EM
    elif "A" <= char <= "Z":
        base = _UPPER_EM
    elif "a" <= char <= "z":
        base = _LOWER_EM
    elif char.isalpha():
        base = _OTHER_EM
    elif char in "il.,:;'!|":
        base = _NARROW_EM
    else:
        base = _OTHER_EM
    return base * (_BOLD_FACTOR if bold else 1.0)


def measure_text(text: str, size: float, *, bold: bool = False) -> float:
    """估算单行文本宽度（设计单位）；换行符按空格处理。"""
    return sum(char_em(char, bold=bold) for char in text.replace("\n", " ")) * size


def wrap_text(text: str, width: float, size: float, *, bold: bool = False) -> tuple[str, ...]:
    """按估算宽度贪心换行；逐字符断行，兼容中英文混排。"""
    if not text:
        return ("",)
    if width <= 0 or size <= 0:
        return (text,)
    lines: list[str] = []
    current: list[str] = []
    current_width = 0.0
    for char in text:
        advance = char_em(char, bold=bold) * size
        if current and current_width + advance > width:
            lines.append("".join(current).rstrip())
            current = []
            current_width = 0.0
            if char.isspace():
                continue
        current.append(char)
        current_width += advance
    lines.append("".join(current).rstrip())
    return tuple(lines)


def estimate_text_height(
    primitive: Text,
    *,
    width: float | None = None,
) -> float:
    """估算文本框内容高度（设计单位），按文本框宽度换行。"""
    box_width = primitive.w if width is None else width
    total = 0.0
    for paragraph in primitive.paragraphs or ("",):
        count = len(wrap_text(paragraph, box_width, primitive.size, bold=primitive.bold))
        total += count * primitive.size * primitive.line_spacing + primitive.space_after
    return total


def text_overflow(primitive: Text) -> float:
    """文本框溢出量（设计单位）；负数表示还有余量。"""
    return estimate_text_height(primitive) - primitive.h


@dataclass(frozen=True, slots=True)
class OverflowIssue:
    """可能溢出的文本框；索引为绘制列表中的位置。"""

    index: int
    text: str
    overflow: float

    @property
    def message(self) -> str:
        """界面可直接展示的提示文案。"""
        snippet = self.text.strip() or "（空文本）"
        return f"第 {self.index + 1} 个文本框可能溢出约 {self.overflow:.0f} 单位：{snippet[:24]}"


def overflow_issues(primitives: tuple[Primitive, ...], *, tolerance: float = 0.5) -> tuple[OverflowIssue, ...]:
    """找出估算溢出超过 ``tolerance`` 的文本框，按绘制顺序返回。"""
    issues: list[OverflowIssue] = []
    for index, primitive in enumerate(primitives):
        if not isinstance(primitive, Text):
            continue
        overflow = text_overflow(primitive)
        if overflow > tolerance:
            text = primitive.paragraphs[0] if primitive.paragraphs else ""
            issues.append(OverflowIssue(index=index, text=text, overflow=overflow))
    return tuple(issues)
