"""imagetool - 图片处理工具集。

提供 resize/crop/rotate/flip/convert/watermark/compress/info/exif/
histogram/colors 子命令，基于 Pillow 实现。

依赖 ``fcmd[img]`` extra 中的 ``pillow``。

示例
----
    fcmd imagetool r in.png out.png --width 100
    fcmd imagetool cv in.png out.jpg
    fcmd imagetool i in.png
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd

__all__ = [
    "image_colors",
    "image_compress",
    "image_convert",
    "image_crop",
    "image_exif",
    "image_flip",
    "image_histogram",
    "image_info",
    "image_resize",
    "image_rotate",
    "image_watermark",
]

if TYPE_CHECKING:
    from PIL import Image, ImageDraw, ImageFont

try:
    from PIL import Image, ImageDraw, ImageFont

    HAS_PIL = True
except ImportError:  # pragma: no cover - 仅在未安装 pillow 时触发
    HAS_PIL = False


def _require_pil() -> bool:
    """Pillow 未安装时打印提示，返回是否可用。"""
    if not HAS_PIL:
        print("未安装 Pillow 库，请安装: pip install fcmd[img]")
        return False
    return True


def _save_image(img: Any, output: Path, fmt: str | None = None, quality: int = 85) -> None:
    """保存图片，自动处理 JPEG 不支持 alpha 通道的情况。

    JPEG 格式不支持透明通道，保存前先转换为 RGB 模式。
    """
    output.parent.mkdir(parents=True, exist_ok=True)
    save_fmt = fmt or output.suffix.lstrip(".").upper()
    if save_fmt == "JPG":
        save_fmt = "JPEG"
    if save_fmt == "JPEG":
        img = img.convert("RGB")
    if save_fmt in ("JPEG", "WEBP"):
        img.save(output, format=save_fmt, quality=quality)
    else:
        img.save(output, format=save_fmt)


# ---------------------------------------------------------------------- #
# 基础操作
# ---------------------------------------------------------------------- #
@fcmd.tool("imagetool", subcommand="r", help="调整尺寸")
def image_resize(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int | None = None,
    keep_ratio: bool = True,
) -> None:
    """调整图片尺寸。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出图片路径
    width:
        目标宽度
    height:
        目标高度（``keep_ratio=True`` 时仅作上限，``None`` 表示按宽度等比）
    keep_ratio:
        是否保持宽高比（默认 ``True``，``--no-keep-ratio`` 拉伸到指定尺寸）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    if keep_ratio:
        target_height = height if height is not None else width
        img.thumbnail((width, target_height))
    else:
        if height is None:
            height = width
        img = img.resize((width, height))
    _save_image(img, output_path)
    print(f"调整尺寸完成: {output_path} ({img.size[0]}x{img.size[1]})")


@fcmd.tool("imagetool", subcommand="c", help="裁剪图片")
def image_crop(  # noqa: PLR0913
    input_path: Path,
    output_path: Path,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> None:
    """裁剪图片到指定矩形。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出图片路径
    left, top, right, bottom:
        裁剪矩形坐标（左上为原点）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    cropped = img.crop((left, top, right, bottom))
    _save_image(cropped, output_path)
    print(f"裁剪完成: {output_path} ({right - left}x{bottom - top})")


@fcmd.tool("imagetool", subcommand="ro", help="旋转图片")
def image_rotate(
    input_path: Path,
    output_path: Path,
    degrees: float,
    expand: bool = False,
) -> None:
    """旋转图片。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出图片路径
    degrees:
        旋转角度（正值逆时针）
    expand:
        是否扩展画布以容纳整个旋转后的图片（默认 ``False``）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    rotated = img.rotate(degrees, expand=expand)
    _save_image(rotated, output_path)
    print(f"旋转完成: {output_path} ({degrees}度)")


@fcmd.tool("imagetool", subcommand="fl", help="翻转图片")
def image_flip(
    input_path: Path,
    output_path: Path,
    direction: str = "horizontal",
) -> None:
    """翻转图片。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出图片路径
    direction:
        翻转方向：``"horizontal"``（水平镜像）/ ``"vertical"``（垂直镜像）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    method = Image.Transpose.FLIP_LEFT_RIGHT if direction == "horizontal" else Image.Transpose.FLIP_TOP_BOTTOM
    flipped = img.transpose(method)
    _save_image(flipped, output_path)
    print(f"翻转完成: {output_path} ({direction})")


@fcmd.tool("imagetool", subcommand="cv", help="格式转换")
def image_convert(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    quality: int = 85,
) -> None:
    """转换图片格式。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出图片路径（后缀决定格式，除非 ``format`` 显式指定）
    format:
        目标格式（如 ``"PNG"``/``"JPEG"``/``"WEBP"``），``None`` 时按 ``output_path`` 后缀推断
    quality:
        压缩质量（1-100，仅对 JPEG/WEBP 有效）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    _save_image(img, output_path, fmt=format, quality=quality)
    actual_fmt = format or output_path.suffix.lstrip(".").upper()
    print(f"格式转换完成: {output_path} ({actual_fmt})")


@fcmd.tool("imagetool", subcommand="wm", help="添加文字水印")
def image_watermark(  # noqa: PLR0913
    input_path: Path,
    output_path: Path,
    text: str,
    position: str = "bottom-right",
    opacity: float = 0.5,
    font_size: int = 32,
) -> None:
    """添加文字水印。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出图片路径
    text:
        水印文字
    position:
        水印位置：``top-left``/``top-right``/``bottom-left``/``bottom-right``/``center``
    opacity:
        不透明度（0.0-1.0）
    font_size:
        字体大小
    """
    if not _require_pil():
        return

    img = Image.open(input_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font = _load_font(font_size)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = int(bbox[2] - bbox[0])
    text_h = int(bbox[3] - bbox[1])
    margin = 10
    x, y = _resolve_position(position, img.size, text_w, text_h, margin)

    alpha = int(255 * max(0.0, min(1.0, opacity)))
    draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))
    result = Image.alpha_composite(img, overlay)
    _save_image(result, output_path)
    print(f"水印添加完成: {output_path}")


def _load_font(size: int) -> Any:
    """加载字体，优先 truetype，失败回退默认字体。"""
    candidates = ("DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf")
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _resolve_position(
    position: str,
    img_size: tuple[int, int],
    text_w: int,
    text_h: int,
    margin: int,
) -> tuple[int, int]:
    """根据位置描述符计算水印坐标。"""
    w, h = img_size
    pos_map = {
        "top-left": (margin, margin),
        "top-right": (w - text_w - margin, margin),
        "bottom-left": (margin, h - text_h - margin),
        "bottom-right": (w - text_w - margin, h - text_h - margin),
        "center": ((w - text_w) // 2, (h - text_h) // 2),
    }
    return pos_map.get(position, pos_map["bottom-right"])


@fcmd.tool("imagetool", subcommand="cp", help="压缩图片")
def image_compress(
    input_path: Path,
    output_path: Path,
    quality: int = 85,
) -> None:
    """压缩图片（重新编码以减小体积）。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出图片路径
    quality:
        压缩质量（1-100）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    fmt = input_path.suffix.lstrip(".").upper()
    _save_image(img, output_path, fmt=fmt, quality=quality)
    in_size = input_path.stat().st_size
    out_size = output_path.stat().st_size
    ratio = (1 - out_size / in_size) * 100 if in_size > 0 else 0.0
    print(f"压缩完成: {output_path} (原 {in_size}B → 新 {out_size}B, 节省 {ratio:.1f}%)")


# ---------------------------------------------------------------------- #
# 元数据与信息
# ---------------------------------------------------------------------- #
@fcmd.tool("imagetool", subcommand="i", help="查看图片信息")
def image_info(input_path: Path, json: bool = False) -> None:
    """打印图片信息（尺寸/格式/模式/EXIF 摘要）。

    Parameters
    ----------
    input_path:
        输入图片路径
    json:
        是否以 JSON 格式输出（默认纯文本表格）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    exif = img.getexif()
    exif_count = len(exif) if exif else 0

    import json as json_mod

    data = {
        "path": str(input_path),
        "format": img.format,
        "mode": img.mode,
        "width": img.size[0],
        "height": img.size[1],
        "exif_tags": exif_count,
    }
    if json:
        print(json_mod.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(f"文件: {data['path']}")
        print(f"格式: {data['format']}")
        print(f"模式: {data['mode']}")
        print(f"尺寸: {data['width']}x{data['height']}")
        print(f"EXIF 标签数: {data['exif_tags']}")


@fcmd.tool("imagetool", subcommand="e", help="读取/修改 EXIF")
def image_exif(
    input_path: Path,
    output_path: Path | None = None,
    show: bool = True,
    set: list[str] | None = None,
    clear: bool = False,
) -> None:
    """读取或修改 EXIF 元数据。

    Parameters
    ----------
    input_path:
        输入图片路径
    output_path:
        输出路径（``None`` 时原地覆盖；仅 ``show=True`` 时可省略）
    show:
        是否打印 EXIF 标签（默认 ``True``，``--no-show`` 静默）
    set:
        设置标签，格式 ``["KEY=VALUE", ...]``（KEY 为数字标签号）
    clear:
        清空所有 EXIF 标签（在 ``set`` 之前执行）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    exif = img.getexif()

    if show:
        _print_exif(exif)

    modified = _apply_exif_modifications(exif, set, clear)
    if modified:
        _save_exif(img, exif, output_path if output_path is not None else input_path)


def _print_exif(exif: Any) -> None:
    """打印 EXIF 标签。"""
    if exif:
        for tag, value in exif.items():
            print(f"  {tag}: {value}")
    else:
        print("  (无 EXIF 数据)")


def _apply_exif_modifications(exif: Any, set_items: list[str] | None, clear: bool) -> bool:
    """应用 EXIF 修改（clear + set），返回是否有改动。"""
    if clear:
        for tag in list(exif.keys()):
            del exif[tag]
    if set_items:
        for item in set_items:
            _apply_single_exif_set(exif, item)
    return bool(set_items or clear)


def _apply_single_exif_set(exif: Any, item: str) -> None:
    """解析并应用单个 KEY=VALUE 设置项。"""
    if "=" not in item:
        print(f"跳过无效项 (缺少 =): {item}")
        return
    key_str, value = item.split("=", 1)
    try:
        tag = int(key_str)
    except ValueError:
        print(f"跳过无效标签号: {key_str}")
        return
    exif[tag] = value


def _save_exif(img: Any, exif: Any, output_path: Path) -> None:
    """保存图片与 EXIF。"""
    exif_bytes = exif.tobytes() if exif else b""
    img.save(output_path, exif=exif_bytes)
    print(f"EXIF 已保存: {output_path}")


@fcmd.tool("imagetool", subcommand="hi", help="颜色直方图")
def image_histogram(
    input_path: Path,
    channel: str = "rgb",
) -> None:
    """打印颜色直方图统计（每通道 8 桶）。

    Parameters
    ----------
    input_path:
        输入图片路径
    channel:
        通道：``"rgb"``（R/G/B 三通道）/ ``"luminance"``（亮度单通道）
    """
    if not _require_pil():
        return

    img = Image.open(input_path)
    hist = img.histogram()
    buckets = 8

    if channel == "luminance":
        gray = img.convert("L")
        gray_hist = gray.histogram()
        print("亮度直方图 (8 桶):")
        _print_histogram_buckets(gray_hist, buckets, "L")
    else:
        print("RGB 直方图 (8 桶):")
        if len(hist) == 256:
            _print_histogram_buckets(hist, buckets, "L")
        else:
            for idx, name in enumerate(("R", "G", "B")):
                start = idx * 256
                _print_histogram_buckets(hist[start : start + 256], buckets, name)


def _print_histogram_buckets(channel_hist: list[int], buckets: int, name: str) -> None:
    """将单通道 256 桶直方图聚合为指定桶数并打印。"""
    bucket_size = max(1, len(channel_hist) // buckets)
    print(f"  {name}:")
    for i in range(buckets):
        start = i * bucket_size
        end = min((i + 1) * bucket_size, len(channel_hist))
        count = sum(channel_hist[start:end])
        slice_max = max(channel_hist[start:end] or [1])
        bar = "#" * min(40, count * 40 // max(1, slice_max))
        print(f"    [{start:3d}-{end:3d}] {count:>8d} {bar}")


@fcmd.tool("imagetool", subcommand="co", help="提取主色调")
def image_colors(
    input_path: Path,
    count: int = 5,
) -> None:
    """提取并打印主色调。

    Parameters
    ----------
    input_path:
        输入图片路径
    count:
        提取的颜色数（默认 5）
    """
    if not _require_pil():
        return

    img = Image.open(input_path).convert("RGB")
    quantized = img.quantize(colors=count)
    palette = quantized.getpalette()
    if palette is None:  # pragma: no cover - quantize() 总会生成调色板，防御性守卫
        print("无法提取调色板")
        return

    actual_count = len(palette) // 3
    print(f"主色调 (前 {min(count, actual_count)} 色):")
    for i in range(min(count, actual_count)):
        r = palette[i * 3]
        g = palette[i * 3 + 1]
        b = palette[i * 3 + 2]
        hex_color = f"#{r:02X}{g:02X}{b:02X}"
        print(f"  {i + 1}. {hex_color}  rgb({r}, {g}, {b})")
