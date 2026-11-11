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

from pathlib import Path
from typing import Literal

import fcmd
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
    default_pptx_path,
    default_template_path,
    load_template,
    new_template,
    palette_for_style,
    save_template,
    summary_lines,
)
from fcmd.apps.pptmake._pptx import export_pptx, import_pptx
from fcmd.console import print_error

__all__ = [
    "export_command",
    "import_command",
    "init_command",
    "inspect_command",
    "presets_command",
]

__version__ = "0.1.0"

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


@fcmd.tool("pptmake", subcommand="export", help="把模板渲染为 PPTX", description=_DESCRIPTION)
def export_command(template: Path, output: Path | None = None, overwrite: bool = False) -> None:
    """把模板渲染成 ``.pptx``。

    Parameters
    ----------
    template:
        模板 JSON 路径
    output:
        输出 PPTX 路径；缺省与模板同名
    overwrite:
        允许覆盖已存在的 PPTX
    """
    target = output or default_pptx_path(template)
    if target.exists() and not overwrite:
        _print_error(f"输出已存在：{target}（使用 --overwrite 覆盖）")
        return
    try:
        spec = load_template(template)
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
