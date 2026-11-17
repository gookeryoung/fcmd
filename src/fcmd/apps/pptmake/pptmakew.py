"""pptmakew —— PPT 模板设计器（Qt 窗口）。

左侧选页与布局，中间实时预览，右侧编辑当前页内容与六个语义色；顶部可切换
风格 / 画幅 / 装饰，菜单支持模板 JSON 导入导出与直接导出 ``.pptx``。窗口
设置（风格、画幅、装饰、最近目录、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/pptmakew.json``；界面实现在
:mod:`fcmd.apps.pptmake._pptmakew_qt`，本模块保留纯逻辑、设置与几何计算。

示例
----
    fcmd pptmakew        # 打开模板设计器
    pptmakew             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _tokens as tokens
from fcmd.gui import config as _guiconfig
from fcmd.gui import window as _guiwindow

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_GEOMETRY",
    "HELP_TEXT",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "PptmakeSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "safe_filename",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "slide_label",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PPT 模板设计器（Qt）：十三种布局、四套风格、六个语义色实时预览，"
    "模板 JSON 与 PPTX 双向导入导出；设置持久化到 ~/.fcmd/ui/pptmakew.json"
)

DEFAULT_GEOMETRY = "1320x840"
MIN_WIDTH = 1100
MIN_HEIGHT = 700

STYLE_OPTIONS: tuple[tuple[str, str], ...] = (
    *((core.STYLE_TEXT[style], style) for style in core.STYLES),
    (core.STYLE_TEXT[core.STYLE_CUSTOM], core.STYLE_CUSTOM),
)
ASPECT_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.ASPECT_TEXT[item], item) for item in core.ASPECTS)
DECOR_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.DECOR_TEXT[item], item) for item in core.DECORS)
DENSITY_OPTIONS: tuple[tuple[str, str], ...] = tuple((tokens.DENSITY_TEXT[item], item) for item in tokens.DENSITIES)
LAYOUT_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.LAYOUT_TEXT[item], item) for item in core.LAYOUTS)

_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')

HELP_TEXT = """1. 左侧「页面」选择要编辑的页；「布局库」双击即可新增一页。
2. 右侧修改标题、副标题、要点与备注；要点按行拆分。
   - 数据页写成「98%|交付准时率」；
   - 时间线写成「01|调研」；
   - 双栏页副标题写成「左栏标题|右栏标题」；
   - 对比矩阵写成「维度|方案A|方案B」，图表写成「Q1|42」；
   - 联系页写成「邮箱|hello@example.com」。
3. 顶部切换风格 / 画幅 / 装饰；右侧六个色块可逐项改色（自动转为自定义风格）。
   「密度」调整间距松紧，「装饰强度」滑杆调节装饰显隐（0 为完全关闭）。
4. 「文件 → 导出模板」保存 .pptmtpl.json，「文件 → 导出 PPTX」生成演示文稿。
5. 「文件 → 导入」既支持模板 JSON，也支持从现有 .pptx 提取配色与文字。
6. 预览工具栏可「适应窗口」或按比例缩放；下方缩略图点击即可跳页。
7. 预览底部出现「文本框可能溢出」提示时，可精简文字或调整版式。
8. 「文件 → 精确预览」需本机安装 LibreOffice，未安装时保持画布预览。"""


@dataclass(slots=True)
class PptmakeSettings:
    """窗口设置。"""

    style: str = core.STYLE_AURORA
    aspect: str = core.ASPECT_16_9
    decor: str = core.DECOR_ORBS
    last_dir: str = ""
    geometry: str | None = None


def config_path() -> Path:
    """设置文件路径。"""
    return _guiconfig.config_path("pptmakew")


def settings_to_mapping(settings: PptmakeSettings) -> dict[str, Any]:
    """设置转字典。"""
    return {
        "style": settings.style,
        "aspect": settings.aspect,
        "decor": settings.decor,
        "last_dir": settings.last_dir,
        "geometry": settings.geometry,
    }


def settings_from_mapping(raw: Mapping[str, Any], base: PptmakeSettings | None = None) -> PptmakeSettings:
    """从字典解析设置；非法字段保留 ``base`` 或默认值。"""
    settings = replace(base) if base is not None else PptmakeSettings()
    style = raw.get("style")
    if isinstance(style, str):
        with contextlib.suppress(core.TemplateError):
            settings.style = core.normalize_style(style)
    aspect = raw.get("aspect")
    if isinstance(aspect, str):
        with contextlib.suppress(core.TemplateError):
            settings.aspect = core.normalize_aspect(aspect)
    decor = raw.get("decor")
    if isinstance(decor, str):
        settings.decor = core.normalize_decor(decor)
    last_dir = raw.get("last_dir")
    if isinstance(last_dir, str):
        settings.last_dir = last_dir
    geometry = raw.get("geometry")
    settings.geometry = geometry if isinstance(geometry, str) and _guiconfig.valid_geometry(geometry) else None
    return settings


def load_settings(path: Path | None = None) -> PptmakeSettings:
    """读取设置；缺失或损坏时返回默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return PptmakeSettings()
    return settings_from_mapping(raw)


def save_settings(settings: PptmakeSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略。"""
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """恢复窗口位置；越界时夹回屏幕并保证最小尺寸。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def safe_filename(name: str, fallback: str = "template") -> str:
    """把模板名转成可用的文件名（去掉 Windows 非法字符）。"""
    cleaned = _FILENAME_RE.sub("_", name).strip().strip(".")
    return cleaned or fallback


def slide_label(index: int, slide: core.SlideSpec) -> str:
    """页面列表显示文案：序号 + 布局 + 截断标题。"""
    title = slide.title.strip() or "（未命名）"
    if len(title) > 18:
        title = title[:17] + "…"
    return f"{index:02d}  {core.layout_label(slide.layout)}  {title}"


def _option_value(options: tuple[tuple[str, str], ...], label: str, default: str) -> str:
    """界面标签转枚举值；无法识别时返回默认值。"""
    for text, value in options:
        if label in (text, value):
            return value
    return default


def _option_label(options: tuple[tuple[str, str], ...], value: str, default: str) -> str:
    """枚举值转界面标签。"""
    for text, item in options:
        if item == value:
            return text
    return default


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.apps.pptmake._pptmakew_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("pptmakew", help="打开 PPT 模板设计器（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PPT 模板设计器。"""
    run_gui()


@fcmd.main("pptmakew")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
