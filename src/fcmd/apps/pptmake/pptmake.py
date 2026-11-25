"""pptmake —— PPT 模板生成命令行。

子命令：

- ``presets``：列出内置风格、布局与装饰样式；
- ``init``：生成一份覆盖二十四种版式的示例模板 JSON；
- ``inspect``：查看模板摘要；
- ``export``：把模板渲染为 ``.pptx``；
- ``import``：把现有 ``.pptx`` 尽力转换为模板 JSON。

示例
----
    fcmd pptmake presets
    fcmd pptmake init 品牌模板.pptmtpl.json --style porcelain --aspect 16:9
    fcmd pptmake export 品牌模板.pptmtpl.json --output 品牌模板.pptx
    fcmd pptmake import 旧演示.pptx --output 旧演示.pptmtpl.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal

import fcmd
from fcmd.apps.pptmake import _office
from fcmd.apps.pptmake._core import (
    ASPECT_4_3,
    ASPECT_16_9,
    ASPECT_TEXT,
    DECOR_TEXT,
    DECORS,
    LAYOUT_TEXT,
    LAYOUTS,
    STYLE_TEXT,
    STYLES,
    TEMPLATE_SUFFIX,
    TemplateError,
    TemplateSpec,
    default_pptx_path,
    default_template_path,
    load_template,
    new_template,
    palette_for_style,
    save_template,
    summary_lines,
    template_from_mapping,
    template_to_mapping,
)
from fcmd.apps.pptmake._design import Oval, Picture, Primitive, Rect, Text, build_draw_list
from fcmd.apps.pptmake._library import build_sample
from fcmd.apps.pptmake._pptx import export_potx, export_pptx, import_pptx
from fcmd.apps.pptmake._preview import contrast_ratio, overflow_issues
from fcmd.apps.pptmake._rules import check_template
from fcmd.console import print_error

__all__ = [
    "build_command",
    "doctor_command",
    "export_command",
    "import_command",
    "init_command",
    "inspect_command",
    "presets_command",
    "validate_command",
]

_DESCRIPTION = "PPT 模板生成器：内置二十四种版式与四套风格，支持模板 JSON 导入导出，并可直接渲染为 .pptx 演示文稿"

StyleName = Literal["aurora", "porcelain", "graphite", "coral"]
AspectName = Literal["16:9", "4:3"]
DecorName = Literal["orbs", "grid", "frame", "minimal"]


def _template_stem(path: Path) -> str:
    """从模板文件名去掉 ``.pptmtpl.json`` / ``.json`` 后缀。"""
    name = path.name
    for suffix in (TEMPLATE_SUFFIX, ".json"):
        if name.lower().endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def _print_error(message: str) -> None:
    """统一错误输出。"""
    print_error(message)


@fcmd.tool("pptmake", subcommand="presets", help="列出内置风格、布局与装饰样式", description=_DESCRIPTION)
def presets_command() -> None:
    """打印可用的风格、布局与装饰样式。"""
    lines = ["风格预设："]
    for style in STYLES:
        palette = palette_for_style(style)
        lines.append(
            f"  {style:<10} {STYLE_TEXT[style]:<6} 背景 {palette.background} · 卡片 {palette.surface} · "
            f"强调 {palette.accent} · 辅助 {palette.secondary}"
        )
    lines.append("")
    lines.append("内置布局：")
    lines.extend(f"  {layout:<12} {LAYOUT_TEXT[layout]}" for layout in LAYOUTS)
    lines.append("")
    lines.append("装饰样式：")
    lines.extend(f"  {decor:<10} {DECOR_TEXT[decor]}" for decor in DECORS)
    lines.append("")
    lines.append("画幅：" + " / ".join(f"{aspect}（{ASPECT_TEXT[aspect]}）" for aspect in (ASPECT_16_9, ASPECT_4_3)))
    print("\n".join(lines))


@fcmd.tool("pptmake", subcommand="init", help="生成示例模板 JSON", description=_DESCRIPTION)
def init_command(  # noqa: PLR0913, PLR0917 - CLI 参数由 argparse 逐个生成
    template: Path,
    name: str = "",
    style: StyleName = "aurora",
    aspect: AspectName = "16:9",
    decor: DecorName = "orbs",
    scene: str = "",
    overwrite: bool = False,
) -> None:
    """生成一份覆盖全部内置布局的示例模板。

    Parameters
    ----------
    template:
        输出模板路径，建议以 ``.pptmtpl.json`` 结尾
    name:
        模板名称，缺省取文件名
    style:
        风格预设：aurora=极光蓝、porcelain=素瓷青、graphite=石墨金、coral=珊瑚暖
    aspect:
        画幅：16:9 宽屏或 4:3 经典
    decor:
        装饰样式：orbs=光斑、grid=网格、frame=边框、minimal=极简
    scene:
        场景示例：launch=产品发布、quarterly=季度汇报、proposal=方案提案；留空生成覆盖全部版式的模板
    overwrite:
        允许覆盖已存在的模板文件
    """
    if template.exists() and not overwrite:
        _print_error(f"模板已存在：{template}（使用 --overwrite 覆盖）")
        return
    try:
        if scene.strip():
            spec = build_sample(
                scene,
                style=style,
                aspect=aspect,
                decor=decor,
                name=name.strip() or _template_stem(template),
            )
        else:
            spec = new_template(
                name=name.strip() or _template_stem(template),
                style=style,
                aspect=aspect,
                decor=decor,
            )
        save_template(spec, template)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    print(f"已生成模板：{template}")
    print("\n".join(summary_lines(spec)))


@fcmd.tool("pptmake", subcommand="inspect", help="查看模板摘要", description=_DESCRIPTION)
def inspect_command(template: Path) -> None:
    """输出模板的风格、布局与逐页标题。

    Parameters
    ----------
    template:
        模板 JSON 路径
    """
    try:
        spec = load_template(template)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    print("\n".join(summary_lines(spec)))


@fcmd.tool("pptmake", subcommand="validate", help="检查模板结构与文本溢出", description=_DESCRIPTION)
def validate_command(template: Path) -> None:
    """校验模板：布局 / 颜色 / 图片路径、文本溢出与最小字号。

    Parameters
    ----------
    template:
        模板 JSON 路径
    """
    try:
        spec = load_template(template)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    report = _quality_issues(spec, verbose=True, base_dir=template.parent)
    rules = check_template(spec)
    for page, issue in rules:
        print(issue.describe(page))
    if report.total or rules:
        print(
            f"共有 {report.overflow} 处文本溢出、{report.missing} 个缺失图片、{report.small} 处小于 {_MIN_FONT_PT:g}pt 的文字、"
            f"{report.unlabeled} 张图片缺少替代文本、{report.contrast} 处对比度低于 {_MIN_CONTRAST:g}:1、"
            f"{len(rules)} 处超出内容容量；建议在 pptmakew 中精简文字 / 补齐替代文本 / 修正路径。"
        )
        return
    print(f"校验通过：{len(spec.slides)} 页，未发现文本溢出、缺失图片、过小字号、低对比度或超限内容。")


_MIN_FONT_PT: Final[float] = 12.0
# 设计坐标 96 单位 / 英寸，1 单位 = 0.75pt。
_MIN_FONT_UNITS: Final[float] = _MIN_FONT_PT * 96 / 72
_MIN_CONTRAST: Final[float] = 3.0


@dataclass(frozen=True, slots=True)
class _Quality:
    """一页或整份模板的质量检查结果。"""

    overflow: int = 0
    missing: int = 0
    small: int = 0
    unlabeled: int = 0
    contrast: int = 0

    @property
    def total(self) -> int:
        return self.overflow + self.missing + self.small + self.unlabeled + self.contrast


def _backdrop_of(primitives: tuple[Primitive, ...], text: Text, default: str) -> str:
    """取文字下方最近的实心矩形 / 椭圆颜色，找不到时用页面底色。

    背景光斑、边框等标记为 ``decorative`` 的图元不参与判断：它们只是页面
    底色的浅淡变体，文字可读性以内容卡片与页面底色为准。
    """
    center_x = text.x + text.w / 2
    center_y = text.y + text.h / 2
    backdrop = default
    for item in primitives:
        if item is text:
            break
        if isinstance(item, Rect) and item.fill and not item.decorative:
            if item.x <= center_x <= item.x + item.w and item.y <= center_y <= item.y + item.h:
                backdrop = item.fill
        elif isinstance(item, Oval) and item.fill and not item.decorative:
            radius_x = item.w / 2
            radius_y = item.h / 2
            if radius_x <= 0 or radius_y <= 0:
                continue
            dx = (center_x - item.x - radius_x) / radius_x
            dy = (center_y - item.y - radius_y) / radius_y
            if dx * dx + dy * dy <= 1:
                backdrop = item.fill
    return backdrop


def _quality_issues(spec: TemplateSpec, *, verbose: bool, base_dir: Path | None = None) -> _Quality:
    """汇总（溢出, 缺失图片, 过小字号, 缺替代文本, 对比度不足）；``verbose`` 时逐页打印。"""
    counts = _Quality()
    for index, slide in enumerate(spec.slides, start=1):
        image = Path(slide.image)
        if slide.image and not image.is_absolute() and base_dir is not None:
            image = base_dir / image
        missing = 1 if slide.image and not image.is_file() else 0
        if missing and verbose:
            _print_error(f"第 {index} 页图片不存在：{slide.image}")
        primitives = build_draw_list(slide, spec, page_number=index, page_total=len(spec.slides))
        issues = overflow_issues(primitives)
        if issues and verbose:
            print(f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{len(issues)} 处可能溢出")
            for issue in issues[:3]:
                print(f"  - {issue.message}")
        tiny = sum(1 for primitive in primitives if isinstance(primitive, Text) and primitive.size < _MIN_FONT_UNITS)
        if tiny and verbose:
            print(
                f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{tiny} 处字号小于 {_MIN_FONT_PT:g}pt"
            )
        page_background = next((item.fill for item in primitives if isinstance(item, Rect) and item.fill), "#FFFFFF")
        unlabeled = sum(1 for item in primitives if isinstance(item, Picture) and item.path and not item.alt)
        if unlabeled and verbose:
            print(f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{unlabeled} 张图片缺少替代文本")
        low = sum(
            1
            for item in primitives
            if isinstance(item, Text)
            and item.size >= _MIN_FONT_UNITS
            and not item.decorative
            and contrast_ratio(item.color, _backdrop_of(primitives, item, page_background)) < _MIN_CONTRAST
        )
        if low and verbose:
            print(
                f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{low} 处文字对比度低于 {_MIN_CONTRAST:g}:1"
            )
        counts = _Quality(
            overflow=counts.overflow + len(issues),
            missing=counts.missing + missing,
            small=counts.small + tiny,
            unlabeled=counts.unlabeled + unlabeled,
            contrast=counts.contrast + low,
        )
    return counts


@fcmd.tool("pptmake", subcommand="doctor", help="检查 PPTX / PDF 依赖与运行环境", description=_DESCRIPTION)
def doctor_command() -> None:
    """检查 python-pptx、Pillow 与 LibreOffice 是否可用。"""
    import importlib.util

    def status(module: str) -> str:
        return "已安装" if importlib.util.find_spec(module) is not None else "未安装"

    print(f"python-pptx：{status('pptx')}")
    print(f"Pillow：{status('PIL')}")
    soffice = _office.find_soffice()
    print(f"LibreOffice：{soffice if soffice is not None else '未检测到（精确预览 / PDF 导出不可用）'}")
    print("提示：导出 PPTX 需要安装 fcmd[ppt]；PDF 需要本机 LibreOffice。")


@fcmd.tool("pptmake", subcommand="export", help="把模板渲染为 PPTX", description=_DESCRIPTION)
def export_command(  # noqa: PLR0913, PLR0917 - CLI 参数即命令开关
    template: Path,
    output: Path | None = None,
    overwrite: bool = False,
    pdf: bool = False,
    force: bool = False,
    flat: bool = False,
    potx: bool = False,
) -> None:
    """把模板渲染成 ``.pptx``，可选再转一份 PDF。

    导出前会执行与 ``validate`` 相同的检查；存在溢出、缺失图片或过小
    字号时默认拒绝导出，需显式 ``--force``。

    Parameters
    ----------
    template:
        模板 JSON 路径
    output:
        输出 PPTX 路径；缺省与模板同名
    overwrite:
        允许覆盖已存在的 PPTX
    pdf:
        同时用 LibreOffice 转出同目录 PDF（未安装时给出提示）
    force:
        忽略校验问题，强制导出
    flat:
        使用纯 ``srgbClr`` 与静态页码的兼容导出（旧行为，供对照回滚）
    potx:
        输出 PowerPoint 模板（``.potx``）而不是放映稿
    """
    target = output or default_pptx_path(template)
    if potx and output is None:
        target = target.with_suffix(".potx")
    if target.exists() and not overwrite:
        _print_error(f"输出已存在：{target}（使用 --overwrite 覆盖）")
        return
    try:
        spec = load_template(template)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    report = _quality_issues(spec, verbose=not force, base_dir=template.parent)
    if report.total and not force:
        print(
            f"导出已取消：{report.overflow} 处文本溢出、{report.missing} 个缺失图片、{report.small} 处过小字号、"
            f"{report.unlabeled} 张图片缺少替代文本、{report.contrast} 处对比度不足；"
            "修正后重试，或使用 --force 强制导出。"
        )
        return
    try:
        if potx:
            count = export_potx(spec, target, base_dir=template.parent)
        else:
            count = export_pptx(spec, target, native=not flat, base_dir=template.parent)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    print(f"已生成 PPTX：{target}（{count} 页）")
    if pdf:
        if _office.find_soffice() is None:
            _print_error("未检测到 LibreOffice，PDF 未导出")
        else:
            try:
                converted = _office.convert_to_pdf(target, target.parent)
            except _office.OfficeError as exc:
                _print_error(f"PDF 导出失败：{exc}")
            else:
                print(f"已生成 PDF：{converted}")


@fcmd.tool("pptmake", subcommand="build", help="用数据文件批量生成 PPTX", description=_DESCRIPTION)
def build_command(  # noqa: PLR0911 - 每个失败分支给出具体原因并直接返回
    data: Path,
    template: Path | None = None,
    output: Path | None = None,
    overwrite: bool = False,
    force: bool = False,
) -> None:
    """按 JSON 数据文件生成 PPTX；每项字段与模板 ``slides`` 一致。

    Parameters
    ----------
    data:
        数据 JSON：幻灯片对象数组，或 ``{"slides": [...]}``
    template:
        基础模板（提供风格 / 品牌 / 字体）；缺省用内置默认风格
    output:
        输出 PPTX 路径；缺省与数据文件同名
    overwrite:
        允许覆盖已存在的输出
    force:
        忽略校验问题，强制导出
    """
    if not data.is_file():
        _print_error(f"数据文件不存在：{data}")
        return
    try:
        raw = json.loads(data.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _print_error(f"读取数据失败：{exc}")
        return
    slides = raw.get("slides") if isinstance(raw, dict) else raw
    if not isinstance(slides, (list, tuple)):
        _print_error("数据文件需为幻灯片数组，或包含 slides 数组的对象")
        return
    target = output or default_pptx_path(data)
    if target.exists() and not overwrite:
        _print_error(f"输出已存在：{target}（使用 --overwrite 覆盖）")
        return
    try:
        base = load_template(template) if template is not None else new_template(slides=())
        mapping = template_to_mapping(base)
        mapping["slides"] = list(slides)
        spec = template_from_mapping(mapping)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    base_dir = template.parent if template is not None else data.parent
    report = _quality_issues(spec, verbose=not force, base_dir=base_dir)
    if report.total and not force:
        print(
            f"导出已取消：{report.overflow} 处文本溢出、{report.missing} 个缺失图片、{report.small} 处过小字号、"
            f"{report.unlabeled} 张图片缺少替代文本、{report.contrast} 处对比度不足；"
            "修正后重试，或使用 --force 强制导出。"
        )
        return
    try:
        count = export_pptx(spec, target, base_dir=base_dir)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    print(f"已生成 PPTX：{target}（{count} 页）")


@fcmd.tool("pptmake", subcommand="import", help="把现有 PPTX 转换为模板 JSON", description=_DESCRIPTION)
def import_command(source: Path, output: Path | None = None, overwrite: bool = False) -> None:
    """把现有 PPTX 尽力转换为模板 JSON。

    提取首段文本作为封面标题，其余段落作为内容页要点，并复用主题配色与字体。

    Parameters
    ----------
    source:
        源 PPTX 路径
    output:
        输出模板路径；缺省为 ``<文件名>.pptmtpl.json``
    overwrite:
        允许覆盖已存在的模板
    """
    if not source.is_file():
        _print_error(f"PPTX 不存在：{source}")
        return
    target = output or default_template_path(source)
    if target.exists() and not overwrite:
        _print_error(f"输出已存在：{target}（使用 --overwrite 覆盖）")
        return
    try:
        report: list[str] = []
        spec = import_pptx(source, assets_dir=target.with_suffix(".assets"), report=report)
        save_template(spec, target)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    print(f"已导入模板：{target}")
    if report:
        print("\n".join(report))
    print("\n".join(summary_lines(spec)))


@fcmd.main("pptmake")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
