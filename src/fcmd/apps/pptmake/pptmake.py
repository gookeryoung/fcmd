"""pptmake —— PPT 模板生成命令行。

子命令：

- ``presets``：列出内置风格、布局与装饰样式；
- ``init``：生成一份覆盖十三种版式的示例模板 JSON；
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
from fcmd.apps.pptmake._design import Text, build_draw_list
from fcmd.apps.pptmake._pptx import export_pptx, import_pptx
from fcmd.apps.pptmake._preview import overflow_issues
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

_DESCRIPTION = "PPT 模板生成器：内置十三种版式与四套风格，支持模板 JSON 导入导出，并可直接渲染为 .pptx 演示文稿"

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
    overwrite:
        允许覆盖已存在的模板文件
    """
    if template.exists() and not overwrite:
        _print_error(f"模板已存在：{template}（使用 --overwrite 覆盖）")
        return
    try:
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
    overflow, missing, small = _quality_issues(spec, verbose=True)
    if overflow or missing or small:
        print(
            f"共有 {overflow} 处文本溢出、{missing} 个缺失图片、{small} 处小于 {_MIN_FONT_PT:g}pt 的文字；"
            "建议在 pptmakew 中精简文字 / 修正路径。"
        )
        return
    print(f"校验通过：{len(spec.slides)} 页，未发现文本溢出、缺失图片或过小字号。")


_MIN_FONT_PT: Final[float] = 12.0
# 设计坐标 96 单位 / 英寸，1 单位 = 0.75pt。
_MIN_FONT_UNITS: Final[float] = _MIN_FONT_PT * 96 / 72


def _quality_issues(spec: TemplateSpec, *, verbose: bool) -> tuple[int, int, int]:
    """统计（溢出, 缺失图片, 过小字号）；``verbose`` 为真时逐页打印细节。"""
    overflow = 0
    missing = 0
    small = 0
    for index, slide in enumerate(spec.slides, start=1):
        if slide.image and not Path(slide.image).is_file():
            missing += 1
            if verbose:
                _print_error(f"第 {index} 页图片不存在：{slide.image}")
        primitives = build_draw_list(slide, spec, page_number=index, page_total=len(spec.slides))
        issues = overflow_issues(primitives)
        if issues:
            overflow += len(issues)
            if verbose:
                print(f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{len(issues)} 处可能溢出")
                for issue in issues[:3]:
                    print(f"  - {issue.message}")
        tiny = sum(1 for primitive in primitives if isinstance(primitive, Text) and primitive.size < _MIN_FONT_UNITS)
        if tiny:
            small += tiny
            if verbose:
                print(
                    f"第 {index} 页（{LAYOUT_TEXT.get(slide.layout, slide.layout)}）：{tiny} 处字号小于 {_MIN_FONT_PT:g}pt"
                )
    return overflow, missing, small


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
def export_command(
    template: Path,
    output: Path | None = None,
    overwrite: bool = False,
    pdf: bool = False,
    force: bool = False,
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
    """
    target = output or default_pptx_path(template)
    if target.exists() and not overwrite:
        _print_error(f"输出已存在：{target}（使用 --overwrite 覆盖）")
        return
    try:
        spec = load_template(template)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    overflow, missing, small = _quality_issues(spec, verbose=not force)
    if (overflow or missing or small) and not force:
        print(
            f"导出已取消：{overflow} 处文本溢出、{missing} 个缺失图片、{small} 处过小字号；"
            "修正后重试，或使用 --force 强制导出。"
        )
        return
    try:
        count = export_pptx(spec, target)
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
    overflow, missing, small = _quality_issues(spec, verbose=not force)
    if (overflow or missing or small) and not force:
        print(
            f"导出已取消：{overflow} 处文本溢出、{missing} 个缺失图片、{small} 处过小字号；"
            "修正后重试，或使用 --force 强制导出。"
        )
        return
    try:
        count = export_pptx(spec, target)
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
        spec = import_pptx(source)
        save_template(spec, target)
    except TemplateError as exc:
        _print_error(str(exc))
        return
    print(f"已导入模板：{target}")
    print("\n".join(summary_lines(spec)))


@fcmd.main("pptmake")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
