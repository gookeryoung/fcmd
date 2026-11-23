"""字体候选与挑选：拉丁 Cascadia + CJK 回退，纯 stdlib。"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable


LATIN_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Cascadia Code",
    "Cascadia Mono",
)


UI_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI Variable Text",
    "Segoe UI",
    "PingFang SC",
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Droid Sans Fallback",
    "WenQuanYi Bitmap Song",
    "DejaVu Sans",
    "Noto Sans",
    "Liberation Sans",
    "Ubuntu",
)


MONO_FONT_CANDIDATES: Final[tuple[str, ...]] = (
    "Cascadia Code",
    "Cascadia Mono",
    "Consolas",
    "JetBrains Mono",
    "Source Code Pro",
    "Noto Sans Mono CJK SC",
    "Sarasa Mono SC",
    "WenQuanYi Micro Hei Mono",
    "WenQuanYi Zen Hei Mono",
    "DejaVu Sans Mono",
    "Liberation Mono",
    "WenQuanYi Bitmap Song",
    "Courier New",
)


CJK_TEXT_SAMPLE: Final[str] = "中文测试"


def ui_font_families(available: Iterable[str]) -> tuple[str, ...]:
    """界面字体链：拉丁字体（Cascadia Code）在前，CJK 字体在后做逐字回退。

    返回值的第一个元素即 ``QFont`` 的主族（Qt 5 只认第一个），Qt 6 下整个链会通过
    ``setFamilies`` 生效，因此英文 / 数字落在 Cascadia Code、中文自动回退到 CJK 字体。
    系统没装 Cascadia系列时退化为原来的行为（只有 CJK 字体）。
    """
    lookup = {name.casefold(): name for name in available}
    chain: list[str] = []
    for candidate in LATIN_FONT_CANDIDATES:
        found = lookup.get(candidate.casefold())
        if found is not None:
            chain.append(found)
            break
    cjk = pick_font_family(available)
    if cjk not in chain:
        chain.append(cjk)
    return tuple(chain)


def pick_font_family(
    available: Iterable[str],
    candidates: tuple[str, ...] = UI_FONT_CANDIDATES,
) -> str:
    """从候选列表里挑第一个系统可用字体族，均不可用时返回首个候选。

    参数:
        available: 当前 GUI 环境报告的字体族集合。
        candidates: 按优先级排列的候选字体族。

    返回:
        选中的字体族名；大小写不敏感匹配，保留系统报告的真实写法。
    """
    lookup = {name.casefold(): name for name in available}
    for candidate in candidates:
        found = lookup.get(candidate.casefold())
        if found is not None:
            return found
    return candidates[0]


def font_renders_cjk(measure: Callable[[str], int]) -> bool:
    """按字体宽度测量回调判断能否显示中文。

    缺字形时宽度测量返回 0，此时窗口里的中文会显示为方块
    （用于检测系统字体是否覆盖中文，例如 CI 的最小字体环境）。
    """
    return measure(CJK_TEXT_SAMPLE) > 0
