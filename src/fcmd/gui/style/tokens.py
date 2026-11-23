"""主题 token / 字体 / QSS 生成：纯逻辑与 Qt 无关，便于无 GUI 单测。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fcmd.gui.theme import Theme

from fcmd.gui import theme as _guitheme
from fcmd.gui.style.qss import _QSS
from fcmd.gui.style.state import _ACTIVE_THEMES, _FAMILIES, _RUNTIME_HEIGHTS

UI_FONT_CANDIDATES = _guitheme.UI_FONT_CANDIDATES


MONO_FONT_CANDIDATES = _guitheme.MONO_FONT_CANDIDATES


theme_names = _guitheme.theme_names


save_theme = _guitheme.save_theme


def load_theme(path: Path | None = None, *, app: str | None = None) -> Theme:
    """读取持久化主题；``app`` 非空时叠加该窗口的调色板 / 度量覆盖。"""
    return _guitheme.load_theme(path, app=app)


_RADIUS = _guitheme.METRICS.radius


CONTROL_HEIGHT_EXTRA = _guitheme.METRICS.control_height_extra


RUN_CONTROL_HEIGHT_EXTRA = _guitheme.METRICS.run_control_height_extra


HERO_CONTROL_HEIGHT_EXTRA = 14
"""主行动按钮（Hero）比常规控件额外加高的像素数，用于扫描页这种单一大按钮。"""


_CONTROL_PAD_X_BUTTON = _guitheme.METRICS.button_pad_x


_CONTROL_PAD_X_INPUT = _guitheme.METRICS.input_pad_x


def runtime_control_height(app: Any) -> int:
    """返回 :func:`apply_theme` 实际写进样式表的单行控件高度（逻辑像素）。

    运行时高度按当前字体 ``QFontMetrics`` 实测行高推导，随平台字体不同而不同，
    不能用纯估算的 :func:`control_height` 代替；主题未应用时返回 0。
    """
    return _RUNTIME_HEIGHTS.get(app, 0)


def active_theme(app: Any = None) -> Theme:
    """返回当前实际生效的主题。

    与 :func:`load_theme` 的区别：后者读持久化配置，运行时切换主题后配置可能尚未
    落盘（或测试里直接 ``apply_theme`` 了另一套主题），这里优先返回
    :func:`apply_theme` 记录的真实主题。
    """
    if app is None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
    if app is not None:
        theme = _ACTIVE_THEMES.get(app)
        if theme is not None:
            return theme
    return load_theme()


def control_height(theme: Theme) -> int:
    """按字号推导单行控件内容高度（逻辑像素），供无 Qt 环境下的样式表快照使用。

    运行时 :func:`apply_theme` 会用 ``QFontMetrics`` 的实际行高替代这里的估算，
    保证字体替换后控件仍与文字对齐；本函数保持纯函数，便于 CI 无 ``PySide6`` 单测。
    """
    return round(theme.fonts.body * 96 / 72) + theme.metrics.control_height_extra


def ui_font_families(available: Iterable[str]) -> tuple[str, ...]:
    """界面字体链（拉丁 Cascadia Code + CJK 回退），纯函数，便于无 Qt 单测。"""
    return _guitheme.ui_font_families(available)


def font_families() -> tuple[str, ...]:
    """当前应用可见的字体族集合（缓存；无 ``QApplication`` 时返回空元组）。

    枚举字体在部分平台需要数百毫秒，``apply_theme`` 已经缓存过一份；这里复用它，
    让 ``mono_font`` 这类运行期取字体的调用不再重复枚举。
    """
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return ()
    cached = _FAMILIES.get(app)
    if cached is not None:
        return cached
    from PySide6.QtGui import QFontDatabase

    try:
        families = tuple(QFontDatabase.families())
    except TypeError:  # PySide2 / Qt5：families 是实例方法
        families = tuple(QFontDatabase().families())
    _FAMILIES[app] = families
    return families


def pick_font_family(
    available: Iterable[str],
    candidates: tuple[str, ...] = UI_FONT_CANDIDATES,
) -> str:
    """从候选列表里挑第一个系统可用字体族，均不可用时返回首个候选。

    与 :func:`fcmd.gui.theme.pick_font_family` 口径一致（大小写不敏感匹配，
    保留系统报告的真实写法），但接受的是 Qt ``QFontDatabase.families()``。
    """
    return _guitheme.pick_font_family(available, candidates)


def template_tokens(
    theme: Theme,
    *,
    font_family: str = "",
    mono_family: str = "",
    control_height_px: int | None = None,
) -> dict[str, str]:
    """把主题展开成 QSS 模板变量：调色板 token + 字号 + 圆角 + 字体族。

    调色板字段全部内联为 ``$<token>`` 变量，新增 token 时会自动进入模板
    （模板里未引用也不报错），避免样式表与 :class:`Palette` 两处维护色值。
    ``control_height_px`` 省略时按字号估算，运行时由 :func:`apply_theme` 传入
    实际字体行高推导值。
    """
    palette = asdict(theme.palette)
    tokens = {name: str(value) for name, value in palette.items()}
    # 评分档位的浅色底：由档位色按固定透明度派生，避免调色板里再堆一组近似色。
    for name in ("accent", "success", "warning", "danger"):
        tokens[f"{name}_soft"] = _rgba(palette[name], 16)
        # 选中态：同一色系加深，避免只换边框、内部仍是和未选中一样的浅底。
        tokens[f"{name}_soft_strong"] = _rgba(palette[name], 32)
    height = control_height_px if control_height_px is not None else control_height(theme)
    tokens.update(
        {
            "font_small": str(theme.fonts.small),
            "font_body": str(theme.fonts.body),
            "font_heading": str(theme.fonts.heading),
            "font_title": str(theme.fonts.title),
            "radius": str(theme.metrics.radius),
            "control_height": f"{height}px",
            "run_control_height": f"{height + theme.metrics.run_control_height_extra}px",
            "hero_control_height": f"{height + HERO_CONTROL_HEIGHT_EXTRA}px",
            "control_pad_x": theme.metrics.button_pad_x,
            "control_pad_x_input": theme.metrics.input_pad_x,
            "font_family": font_family or pick_font_family((), UI_FONT_CANDIDATES),
            "mono_family": mono_family or pick_font_family((), MONO_FONT_CANDIDATES),
        }
    )
    return tokens


def _rgba(value: str, percent: int) -> str:
    """把 ``#rrggbb`` 转成 QSS 的 ``rgba(r, g, b, n%)``；非六位十六进制原样返回。"""
    text = value.lstrip("#")
    if len(text) != 6:
        return value
    try:
        red, green, blue = (int(text[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return value
    return f"rgba({red}, {green}, {blue}, {percent}%)"


def tiered_value_html(theme: Theme, main: str, detail: str = "") -> str:
    """生成「主值大字加粗 + 说明小字灰」的富文本，供硬件信息等分层数值使用。

    纯函数：主值用标题字号加粗；``detail`` 非空时追加小号 muted 色说明。
    字号 / 颜色均取自当前主题，随主题缩放不写死物理像素。
    """
    from html import escape

    main_html = f'<span style="font-size:{theme.fonts.heading}pt; font-weight:600;">{escape(main)}</span>'
    if not detail:
        return main_html
    note = f' <span style="font-size:{theme.fonts.small}pt; color:{theme.palette.muted};">{escape(detail)}</span>'
    return main_html + note


def stylesheet(
    theme: Theme,
    *,
    font_family: str = "",
    mono_family: str = "",
    control_height_px: int | None = None,
) -> str:
    """生成整套 QSS 文本（纯函数，不依赖 Qt，便于单测与快照）。"""
    return _QSS.substitute(
        template_tokens(
            theme,
            font_family=font_family,
            mono_family=mono_family,
            control_height_px=control_height_px,
        )
    )
