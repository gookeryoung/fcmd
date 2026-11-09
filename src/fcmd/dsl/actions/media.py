"""媒体类 DSL 动作：img2ico / imagetool。

业务实现统一在 :mod:`fcmd.tools.media.mediabase`；本模块只保留 ``@action``
声明与参数转接，避免 DSL 层出现第二套图片实现。TOML 声明通过
``action = "<名>"`` 引用这些名字。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fcmd.tools.media import mediabase

from ..actions import action


@action(
    "img2ico_gen",
    param_help={
        "input_path": "输入图像/SVG 文件或目录路径",
        "output_path": "输出图标文件或目录（目录输入时输出到该目录）",
        "format": "目标格式：win 或 mac（默认根据输出后缀推断，再默认 Windows）",
        "sizes": "自定义尺寸（逗号或空格分隔，不传使用格式默认）",
        "quality": "重采样质量：low / medium / high（默认 high）",
    },
    errors=(OSError, ValueError),
)
def img2ico_gen(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    sizes: list[str] | None = None,
    quality: Literal["low", "medium", "high"] = "high",
) -> None:
    """图像/SVG 转 Windows ICO 或 macOS ICNS 图标（gen 子命令，支持目录批量）。"""
    if not mediabase._require_deps():
        return

    parsed_sizes = mediabase.parse_size_tokens(sizes)
    canonical, outputs, used_sizes = mediabase.convert_images(
        input_path,
        output_path,
        fmt=format,
        sizes=parsed_sizes,
        quality=quality,
    )

    if not outputs:
        print(f"未转换任何文件: {input_path}")
        return
    if input_path.is_dir():
        print(f"批量转换完成: {input_path}（{len(outputs)} 个图标）")
        for output in outputs:
            print(f"  {output}")
    else:
        print(f"图标已生成: {outputs[0]}")
    print(f"格式: {canonical}")
    print(f"包含 {len(used_sizes)} 种尺寸: {', '.join(f'{w}x{h}' for w, h in used_sizes)}")


# ---------------------------------------------------------------------- #
# imagetool DSL 动作
# ---------------------------------------------------------------------- #


@action(
    "imagetool_r",
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
    mediabase.image_resize(input_path, output_path, width, height, keep_ratio)


@action(
    "imagetool_c",
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
    mediabase.image_crop(input_path, output_path, left, top, right, bottom)


@action(
    "imagetool_ro",
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
    mediabase.image_rotate(input_path, output_path, degrees, expand)


@action(
    "imagetool_fl",
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
    mediabase.image_flip(input_path, output_path, direction)


@action(
    "imagetool_cv",
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
    mediabase.image_convert(input_path, output_path, format, quality)


@action(
    "imagetool_wm",
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
    mediabase.image_watermark(input_path, output_path, text, position, opacity, font_size)


@action(
    "imagetool_cp",
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
    mediabase.image_compress(input_path, output_path, quality)


@action(
    "imagetool_i",
    param_help={
        "input_path": "输入图片路径",
        "json": "是否以 JSON 格式输出（默认 False=纯文本）",
    },
)
def imagetool_i(input_path: Path, json: bool = False) -> None:
    """查看图片信息（i 子命令）。"""
    mediabase.image_info(input_path, json)


@action(
    "imagetool_e",
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
    mediabase.image_exif(input_path, output_path, show, set, clear)


@action(
    "imagetool_hi",
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
    mediabase.image_histogram(input_path, channel)


@action(
    "imagetool_co",
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
    mediabase.image_colors(input_path, count)
