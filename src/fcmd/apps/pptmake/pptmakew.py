"""pptmakew —— PPT 模板设计器（Qt 窗口）。

沿用 llmw / wslmanw / filescanw 的侧边栏分页：``设计`` 页为「页面列表与布局库 |
实时预览与缩略图 | 页面设置」三栏工作区，``样式`` 页集中模板名称、风格 / 画幅 /
装饰 / 密度 / 装饰强度、六个语义色与实时预览。菜单支持模板 JSON 导入导出与直接
导出 ``.pptx``。窗口设置（风格、画幅、装饰、最近目录、当前页、侧边栏折叠、窗口
位置）保存在 ``${FCMD_HOME:-~/.fcmd}/ui/pptmakew.json``；界面实现在
:mod:`fcmd.apps.pptmake._pptmakew_qt`，本模块保留纯逻辑、设置与几何计算。

示例
----
    fcmd pptmakew        # 打开模板设计器
    pptmakew             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.apps.pptmake import _core as core
from fcmd.apps.pptmake import _tokens as tokens
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow
from fcmd.gui.layout import parse_sizes
from fcmd.gui.settings import SettingsStore

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "DEFAULT_GEOMETRY",
    "HELP_TEXT",
    "LAYOUT_GROUPS",
    "MAX_RECENT",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "PAGE_KEYS",
    "PptmakeSettings",
    "asset_dir",
    "config_path",
    "load_settings",
    "pack_assets",
    "remember_path",
    "resolve_geometry",
    "safe_filename",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "slide_label",
]

_DESCRIPTION = (
    "PPT 模板设计器（Qt）：二十四种布局、六套风格、六个语义色实时预览，"
    "模板 JSON 与 PPTX 双向导入导出；设置持久化到 ~/.fcmd/ui/pptmakew.json"
)

DEFAULT_GEOMETRY = "1320x840"
MIN_WIDTH = 1100
MIN_HEIGHT = 700
MAX_RECENT = 8
"""「最近模板」保留条数。"""

SORTER_VIEWS: tuple[str, ...] = ("grid", "list")
"""页面排序器视图：缩略图网格 / 紧凑列表。"""

STYLE_OPTIONS: tuple[tuple[str, str], ...] = (
    *((core.STYLE_TEXT[style], style) for style in core.STYLES),
    (core.STYLE_TEXT[core.STYLE_CUSTOM], core.STYLE_CUSTOM),
)
ASPECT_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.ASPECT_TEXT[item], item) for item in core.ASPECTS)
DECOR_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.DECOR_TEXT[item], item) for item in core.DECORS)
DENSITY_OPTIONS: tuple[tuple[str, str], ...] = tuple((tokens.DENSITY_TEXT[item], item) for item in tokens.DENSITIES)
LAYOUT_OPTIONS: tuple[tuple[str, str], ...] = tuple((core.LAYOUT_TEXT[item], item) for item in core.LAYOUTS)

LAYOUT_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("封面", (core.LAYOUT_COVER, core.LAYOUT_PHOTO_COVER)),
    (
        "结构",
        (
            core.LAYOUT_AGENDA,
            core.LAYOUT_AGENDA_PAGES,
            core.LAYOUT_SECTION,
            core.LAYOUT_PHOTO_SECTION,
            core.LAYOUT_APPENDIX,
        ),
    ),
    (
        "内容",
        (
            core.LAYOUT_BULLETS,
            core.LAYOUT_CARDS,
            core.LAYOUT_TWO_COLUMN,
            core.LAYOUT_COMPARISON,
            core.LAYOUT_PROBLEM_SOLUTION,
            core.LAYOUT_PROCESS,
            core.LAYOUT_TIMELINE,
            core.LAYOUT_CODE,
            core.LAYOUT_QUOTE,
            core.LAYOUT_TESTIMONIAL,
        ),
    ),
    ("数据", (core.LAYOUT_METRICS, core.LAYOUT_CHART, core.LAYOUT_CHART_CONCLUSION)),
    ("图文", (core.LAYOUT_IMAGE,)),
    ("结束", (core.LAYOUT_TEAM, core.LAYOUT_CONTACT, core.LAYOUT_CLOSING)),
)
"""版式下拉的分组顺序：封面 / 结构 / 内容 / 数据 / 图文 / 结束，覆盖全部 24 个版式。"""

assert {layout for _group, items in LAYOUT_GROUPS for layout in items} == set(core.LAYOUTS)

PAGE_KEYS: tuple[str, ...] = ("design", "style")
"""侧边栏页面 key（顺序与 :data:`fcmd.apps.pptmake._pptmakew_qt.PAGES` 一致）。"""

_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')

HELP_TEXT = """1. 侧边栏「设计」页：左侧「页面」选择要编辑的页，「布局库」可按封面 / 结构 /
   内容 / 数据 / 图文 / 结束分类筛选，双击版式即可新增一页。
2. 设计页右侧修改标题、副标题、要点与备注；要点按行拆分。
   - 数据页写成「98%|交付准时率」；
   - 时间线写成「01|调研」；
   - 双栏页副标题写成「左栏标题|右栏标题」；
   - 对比矩阵写成「维度|方案A|方案B」，图表写成「Q1|42」；
   - 联系页写成「邮箱|hello@example.com」。
3. 侧边栏「样式」页切换风格 / 画幅 / 装饰 / 密度与装饰强度；六个色块可逐项
   改色（自动转为自定义风格），右侧实时预览随设置刷新。
4. 设计页中间画布实时预览；缩略图点击即可跳页，工具栏可「适应窗口」或缩放。
5. 「文件 → 导出模板」保存 .pptmtpl.json，「文件 → 导出 PPTX」生成演示文稿。
6. 「文件 → 导入」既支持模板 JSON，也支持从现有 .pptx 提取配色与文字。
7. 预览底部与缩略图角标 / 状态栏会提示文本框溢出，可精简文字或调整版式。
8. 「文件 → 精确预览」需本机安装 LibreOffice，未安装时保持画布预览。
9. 标题行、页面按钮与预览工具条只显示图标，悬停可见提示；标题行的
   「新建 / 导入 / 导出模板 / 导出 PPTX」与菜单动作等价。
10. 页面排序器支持缩略图 / 列表切换（工具条最右按钮）、Ctrl / Shift 多选、
    删除 / 复制与拖拽排序；「编辑 → 撤销 / 重做」（Ctrl+Z / Ctrl+Y）可回退编辑。
    右侧检查器分「内容 / 版式 / 备注」三页，标题行右侧按钮可收起检查器。
    布局库为版式缩略图，双击即可添加。
11. 版式页可为本页选择图片（cover 裁剪填满 / contain 完整显示）与覆盖强调色；
    要点行首两空格 / Tab 表示层级，预览与 PPTX 导出按缩进显示。
12. 样式页可设置页脚、页码显隐、品牌 Logo（每页右上角）与标题 / 正文字体。
13. 有未保存改动时标题栏带 ``*``，每 60 秒自动写草稿；异常退出后下次启动可恢复。
14. 「文件 → 导出 PPTX」可选页码范围并同时导出 PDF；``F5`` 从当前页全屏放映；
    「文件 → 从示例模板新建」提供四套内置示例。
15. ``Alt+1`` / ``Alt+2`` 在设计页与样式页之间切换。
16. 导入 PPTX 会显示逐页「保留 / 降级」报告；导出模板时自动把图片按内容哈希打包进
    ``<模板名>.assets/`` 并写成相对路径。窗口窄于 1200 时自动收起页面设置与侧边栏。"""


@dataclass(slots=True)
class PptmakeSettings:
    """窗口设置。"""

    style: str = core.STYLE_AURORA
    aspect: str = core.ASPECT_16_9
    decor: str = core.DECOR_ORBS
    last_dir: str = ""
    geometry: str | None = None
    sidebar_collapsed: bool = False
    active_page: str = PAGE_KEYS[0]
    design_splitter: tuple[int, ...] = ()
    style_splitter: tuple[int, ...] = ()
    recent_templates: tuple[str, ...] = ()
    sorter_view: str = "grid"
    inspector_visible: bool = True


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
        "sidebar_collapsed": settings.sidebar_collapsed,
        "active_page": settings.active_page,
        "design_splitter": list(settings.design_splitter),
        "style_splitter": list(settings.style_splitter),
        "recent_templates": list(settings.recent_templates),
        "sorter_view": settings.sorter_view,
        "inspector_visible": settings.inspector_visible,
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
    collapsed = raw.get("sidebar_collapsed")
    if isinstance(collapsed, bool):
        settings.sidebar_collapsed = collapsed
    page = raw.get("active_page")
    if isinstance(page, str) and page in PAGE_KEYS:
        settings.active_page = page
    for field_name in ("design_splitter", "style_splitter"):
        sizes = parse_sizes(raw.get(field_name))
        if sizes is not None:
            setattr(settings, field_name, tuple(sizes))
    recent = raw.get("recent_templates")
    if isinstance(recent, (list, tuple)):
        settings.recent_templates = tuple(item for item in recent if isinstance(item, str) and item)[:MAX_RECENT]
    view = raw.get("sorter_view")
    if view in SORTER_VIEWS:
        settings.sorter_view = view
    visible = raw.get("inspector_visible")
    if isinstance(visible, bool):
        settings.inspector_visible = visible
    return settings


def remember_path(recent: tuple[str, ...], path: Path | str) -> tuple[str, ...]:
    """把路径提到「最近模板」最前，去重并限制条数。"""
    text = str(path)
    kept = tuple(item for item in recent if item != text)
    return (text, *kept)[:MAX_RECENT]


def load_settings(path: Path | None = None) -> PptmakeSettings:
    """读取设置；缺失或损坏时返回默认值。"""
    raw = SettingsStore("pptmakew", path).load()
    if raw is None:
        return PptmakeSettings()
    return settings_from_mapping(raw)


def save_settings(settings: PptmakeSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略。"""
    SettingsStore("pptmakew", path).save(settings_to_mapping(settings))


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


def asset_dir(target: Path) -> Path:
    """模板文件旁的 ``<模板名>.assets/`` 目录。"""
    suffix = core.TEMPLATE_SUFFIX
    stem = target.name[: -len(suffix)] if target.name.endswith(suffix) else target.stem
    return target.parent / f"{stem}.assets"


def _image_refs(template: core.TemplateSpec) -> tuple[str, ...]:
    """模板里所有图片引用（Logo、单图、多图），按出现顺序去重。"""
    refs: list[str] = []
    if template.theme.logo:
        refs.append(template.theme.logo)
    for slide in template.slides:
        if slide.image:
            refs.append(slide.image)
        refs.extend(slide.images)
    return tuple(dict.fromkeys(refs))


def pack_assets(
    template: core.TemplateSpec, target: Path, *, base_dir: Path | None = None
) -> tuple[core.TemplateSpec, tuple[str, ...]]:
    """把模板引用的图片按内容哈希复制进 ``<模板名>.assets/``，并改写为相对路径。

    返回（改写后的模板, 缺失文件列表）。相对路径按 ``base_dir`` 解析；已经位于
    目标资产目录内的图片会原地复用，缺失文件只记录不改写。
    """
    assets = asset_dir(target)
    mapping: dict[str, str] = {}
    missing: list[str] = []
    for ref in _image_refs(template):
        path = Path(ref)
        source = path if path.is_absolute() else (base_dir / path if base_dir is not None else path)
        if not source.is_file():
            missing.append(ref)
            continue
        digest = hashlib.sha1(source.read_bytes()).hexdigest()[:8]
        name = f"{safe_filename(source.stem, fallback='image')}-{digest}{source.suffix.lower()}"
        dest = assets / name
        assets.mkdir(parents=True, exist_ok=True)
        if not dest.is_file():
            dest.write_bytes(source.read_bytes())
        mapping[ref] = f"{assets.name}/{name}"
    if not mapping:
        return template, tuple(missing)

    def rewrite(value: str) -> str:
        return mapping.get(value, value)

    theme = template.theme
    if theme.logo:
        theme = replace(theme, logo=rewrite(theme.logo))
    slides = tuple(
        replace(
            slide,
            image=rewrite(slide.image),
            images=tuple(rewrite(item) for item in slide.images),
        )
        for slide in template.slides
    )
    return replace(template, theme=theme, slides=slides), tuple(missing)


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
        from fcmd.gui.compat import qt_import_failure

        raise SystemExit(qt_import_failure(exc)) from exc
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
