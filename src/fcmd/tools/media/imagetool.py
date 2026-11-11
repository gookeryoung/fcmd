"""imagetool - 图片处理工具（缩放/裁剪/旋转/翻转/格式转换/水印/压缩/EXIF/直方图/主色调）。

示例
----
    fcmd imagetool r in.png out.png 800
    fcmd imagetool c in.png out.png 0 0 200 200
    fcmd imagetool cv in.png out.jpg --quality 90
    fcmd imagetool i in.png --json
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras import media as med
from fcmd.tools._deps import missing_dependency

__tool_aliases__ = ["imaget"]

__all__ = [
    "imagetool_c",
    "imagetool_co",
    "imagetool_cp",
    "imagetool_cv",
    "imagetool_e",
    "imagetool_fl",
    "imagetool_hi",
    "imagetool_i",
    "imagetool_r",
    "imagetool_ro",
    "imagetool_wm",
]


def _require_pil() -> bool:
    """Pillow 未安装时打印提示并返回 False；已安装则惰性导入。"""
    if not med.HAS_PIL:
        return missing_dependency("Pillow", "img")
    med.ensure_pil()
    return True


@fx.tool(
    "imagetool",
    subcommand="r",
    help="调整尺寸",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "width": "目标宽度",
        "height": "目标高度（keep_ratio=True 时仅作上限，None 表示按宽度等比）",
        "keep_ratio": "是否保持宽高比（默认 True）",
    },
)
def imagetool_r(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int | None = None,
    keep_ratio: bool = True,
) -> None:
    """调整图片尺寸（r 子命令）。"""
    if not _require_pil():
        return
    actual_width, actual_height = med.image_resize(input_path, output_path, width, height, keep_ratio)
    print(f"调整尺寸完成: {output_path} ({actual_width}x{actual_height})")


@fx.tool(
    "imagetool",
    subcommand="c",
    help="裁剪图片",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "left": "裁剪矩形左边界",
        "top": "裁剪矩形上边界",
        "right": "裁剪矩形右边界",
        "bottom": "裁剪矩形下边界",
    },
)
def imagetool_c(  # noqa: PLR0913, PLR0917
    input_path: Path,
    output_path: Path,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> None:
    """裁剪图片（c 子命令）。"""
    if not _require_pil():
        return
    actual_width, actual_height = med.image_crop(input_path, output_path, left, top, right, bottom)
    print(f"裁剪完成: {output_path} ({actual_width}x{actual_height})")


@fx.tool(
    "imagetool",
    subcommand="ro",
    help="旋转图片",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "degrees": "旋转角度（正值逆时针）",
        "expand": "是否扩展画布以容纳整个旋转后的图片（默认 False）",
    },
)
def imagetool_ro(
    input_path: Path,
    output_path: Path,
    degrees: float,
    expand: bool = False,
) -> None:
    """旋转图片（ro 子命令）。"""
    if not _require_pil():
        return
    med.image_rotate(input_path, output_path, degrees, expand)
    print(f"旋转完成: {output_path} ({degrees}度)")


@fx.tool(
    "imagetool",
    subcommand="fl",
    help="翻转图片",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "direction": "翻转方向：horizontal 或 vertical（默认 horizontal）",
    },
)
def imagetool_fl(
    input_path: Path,
    output_path: Path,
    direction: str = "horizontal",
) -> None:
    """翻转图片（fl 子命令）。"""
    if not _require_pil():
        return
    med.image_flip(input_path, output_path, direction)
    print(f"翻转完成: {output_path} ({direction})")


@fx.tool(
    "imagetool",
    subcommand="cv",
    help="格式转换",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径（后缀决定格式）",
        "format": "目标格式（如 PNG/JPEG/WEBP），None 时按 output_path 后缀推断",
        "quality": "压缩质量（1-100，仅对 JPEG/WEBP 有效，默认 85）",
    },
)
def imagetool_cv(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    quality: int = 85,
) -> None:
    """格式转换（cv 子命令）。"""
    if not _require_pil():
        return
    actual_fmt = med.image_convert(input_path, output_path, format, quality)
    print(f"格式转换完成: {output_path} ({actual_fmt})")


@fx.tool(
    "imagetool",
    subcommand="wm",
    help="添加文字水印",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "text": "水印文字",
        "position": "水印位置：top-left/top-right/bottom-left/bottom-right/center（默认 bottom-right）",
        "opacity": "不透明度（0.0-1.0，默认 0.5）",
        "font_size": "字体大小（默认 32）",
    },
)
def imagetool_wm(  # noqa: PLR0913, PLR0917
    input_path: Path,
    output_path: Path,
    text: str,
    position: str = "bottom-right",
    opacity: float = 0.5,
    font_size: int = 32,
) -> None:
    """添加文字水印（wm 子命令）。"""
    if not _require_pil():
        return
    med.image_watermark(input_path, output_path, text, position, opacity, font_size)
    print(f"水印添加完成: {output_path}")


@fx.tool(
    "imagetool",
    subcommand="cp",
    help="压缩图片",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出图片路径",
        "quality": "压缩质量（1-100，默认 85）",
    },
)
def imagetool_cp(
    input_path: Path,
    output_path: Path,
    quality: int = 85,
) -> None:
    """压缩图片（cp 子命令）。"""
    if not _require_pil():
        return
    in_size, out_size, ratio = med.image_compress(input_path, output_path, quality)
    print(f"压缩完成: {output_path} (原 {in_size}B → 新 {out_size}B, 节省 {ratio:.1f}%)")


@fx.tool(
    "imagetool",
    subcommand="i",
    help="查看图片信息",
    param_help={
        "input_path": "输入图片路径",
        "json": "是否以 JSON 格式输出（默认 False=纯文本）",
    },
)
def imagetool_i(input_path: Path, json: bool = False) -> None:
    """查看图片信息（i 子命令）。"""
    if not _require_pil():
        return
    data = med.image_info(input_path)
    if json:
        import json as json_mod

        print(json_mod.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"文件: {data['path']}")
        print(f"格式: {data['format']}")
        print(f"模式: {data['mode']}")
        print(f"尺寸: {data['width']}x{data['height']}")
        print(f"EXIF 标签数: {data['exif_tags']}")


@fx.tool(
    "imagetool",
    subcommand="e",
    help="读取/修改 EXIF",
    param_help={
        "input_path": "输入图片路径",
        "output_path": "输出路径（None 时原地覆盖；仅 show=True 时可省略）",
        "show": "是否打印 EXIF 标签（默认 True）",
        "set": "设置标签，格式 KEY=VALUE（KEY 为数字标签号）",
        "clear": "清空所有 EXIF 标签（在 set 之前执行）",
    },
)
def imagetool_e(
    input_path: Path,
    output_path: Path | None = None,
    show: bool = True,
    set: list[str] | None = None,
    clear: bool = False,
) -> None:
    """读取/修改 EXIF（e 子命令）。"""
    if not _require_pil():
        return
    result = med.image_exif(input_path, output_path, show, set, clear)
    if show:
        if result.items:
            for tag, value in result.items:
                print(f"  {tag}: {value}")
        else:
            print("  (无 EXIF 数据)")
    for warning in result.warnings:
        print(warning)
    if result.saved_path is not None:
        print(f"EXIF 已保存: {result.saved_path}")


@fx.tool(
    "imagetool",
    subcommand="hi",
    help="颜色直方图",
    param_help={
        "input_path": "输入图片路径",
        "channel": "通道：rgb 或 luminance（默认 rgb）",
    },
)
def imagetool_hi(
    input_path: Path,
    channel: str = "rgb",
) -> None:
    """颜色直方图（hi 子命令）。"""
    if not _require_pil():
        return
    for line in med.image_histogram(input_path, channel):
        print(line)


@fx.tool(
    "imagetool",
    subcommand="co",
    help="提取主色调",
    param_help={
        "input_path": "输入图片路径",
        "count": "提取的颜色数（默认 5）",
    },
)
def imagetool_co(
    input_path: Path,
    count: int = 5,
) -> None:
    """提取主色调（co 子命令）。"""
    if not _require_pil():
        return
    for line in med.image_colors(input_path, count):
        print(line)


@fx.main("imagetool")
def main() -> None:
    pass  # pragma: no cover - @fx.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
