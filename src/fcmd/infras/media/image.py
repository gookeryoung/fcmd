"""``fcmd.infras.media.image``（由 ``infras/media.py`` 拆分）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import deps as _deps

__all__ = [
    "ExifResult",
    "exif_items",
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


def _save_image(img: Any, output: Path, fmt: str | None = None, quality: int = 85) -> None:
    """保存图片，自动处理 JPEG 不支持 alpha 通道的情况。"""
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


def _load_font(size: int):
    """加载字体，优先 truetype，失败回退默认字体。"""
    _deps.ensure_pil()
    candidates = ("DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf")
    for name in candidates:
        try:
            return _deps.ImageFont.truetype(name, size)
        except OSError:
            continue
    return _deps.ImageFont.load_default()


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


def exif_items(exif: Any) -> list[tuple[Any, Any]]:
    """返回 EXIF 标签列表；空 EXIF 返回空列表。"""
    return list(exif.items()) if exif else []


def _apply_single_exif_set(exif: Any, item: str) -> str | None:
    """解析并应用单个 KEY=VALUE 设置项，返回提示信息（合法项返回 None）。"""
    if "=" not in item:
        return f"跳过无效项 (缺少 =): {item}"
    key_str, value = item.split("=", 1)
    try:
        tag = int(key_str)
    except ValueError:
        return f"跳过无效标签号: {key_str}"
    exif[tag] = value
    return None


def _apply_exif_modifications(
    exif: Any,
    set_items: list[str] | None,
    clear: bool,
) -> tuple[bool, list[str]]:
    """应用 EXIF 修改（clear + set），返回（是否有改动, 提示列表）。"""
    warnings: list[str] = []
    if clear:
        for tag in list(exif.keys()):
            del exif[tag]
    if set_items:
        for item in set_items:
            warning = _apply_single_exif_set(exif, item)
            if warning is not None:
                warnings.append(warning)
    return bool(set_items or clear), warnings


def _save_exif(img: Any, exif: Any, output_path: Path) -> None:
    """保存图片与 EXIF。"""
    exif_bytes = exif.tobytes() if exif else b""
    img.save(output_path, exif=exif_bytes)


def _histogram_bucket_lines(channel_hist: list[int], buckets: int, name: str) -> list[str]:
    """将单通道 256 桶直方图聚合为指定桶数，返回格式化文本行。"""
    bucket_size = max(1, len(channel_hist) // buckets)
    lines = [f"  {name}:"]
    for i in range(buckets):
        start = i * bucket_size
        end = min((i + 1) * bucket_size, len(channel_hist))
        count = sum(channel_hist[start:end])
        slice_max = max(channel_hist[start:end] or [1])
        bar = "#" * min(40, count * 40 // max(1, slice_max))
        lines.append(f"    [{start:3d}-{end:3d}] {count:>8d} {bar}")
    return lines


def image_resize(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int | None = None,
    keep_ratio: bool = True,
) -> tuple[int, int]:
    """调整图片尺寸，返回保存后的 ``(宽, 高)``。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        if keep_ratio:
            target_height = height if height is not None else width
            img.thumbnail((width, target_height))
        else:
            if height is None:
                height = width
            img = img.resize((width, height))
        _save_image(img, output_path)
        return img.size[0], img.size[1]
    finally:
        img.close()


def image_crop(  # noqa: PLR0913, PLR0917
    input_path: Path,
    output_path: Path,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> tuple[int, int]:
    """裁剪图片到指定矩形，返回 ``(宽, 高)``。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        cropped = img.crop((left, top, right, bottom))
        _save_image(cropped, output_path)
        return right - left, bottom - top
    finally:
        img.close()


def image_rotate(
    input_path: Path,
    output_path: Path,
    degrees: float,
    expand: bool = False,
) -> None:
    """旋转图片。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        rotated = img.rotate(degrees, expand=expand)
        _save_image(rotated, output_path)
    finally:
        img.close()


def image_flip(
    input_path: Path,
    output_path: Path,
    direction: str = "horizontal",
) -> None:
    """翻转图片。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        method = (
            _deps.Image.Transpose.FLIP_LEFT_RIGHT
            if direction == "horizontal"
            else _deps.Image.Transpose.FLIP_TOP_BOTTOM
        )
        flipped = img.transpose(method)
        _save_image(flipped, output_path)
    finally:
        img.close()


def image_convert(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    quality: int = 85,
) -> str:
    """转换图片格式，返回实际使用的格式名。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        _save_image(img, output_path, fmt=format, quality=quality)
        return format or output_path.suffix.lstrip(".").upper()
    finally:
        img.close()


def image_watermark(  # noqa: PLR0913, PLR0917
    input_path: Path,
    output_path: Path,
    text: str,
    position: str = "bottom-right",
    opacity: float = 0.5,
    font_size: int = 32,
) -> None:
    """添加文字水印。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        img = img.convert("RGBA")
        overlay = _deps.Image.new("RGBA", img.size, (0, 0, 0, 0))
        draw = _deps.ImageDraw.Draw(overlay)

        font = _load_font(font_size)
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = int(bbox[2] - bbox[0])
        text_h = int(bbox[3] - bbox[1])
        margin = 10
        x, y = _resolve_position(position, img.size, text_w, text_h, margin)

        alpha = int(255 * max(0.0, min(1.0, opacity)))
        draw.text((x, y), text, font=font, fill=(255, 255, 255, alpha))
        result = _deps.Image.alpha_composite(img, overlay)
        _save_image(result, output_path)
    finally:
        img.close()


def image_compress(
    input_path: Path,
    output_path: Path,
    quality: int = 85,
) -> tuple[int, int, float]:
    """压缩图片（重新编码以减小体积），返回 ``(原大小, 新大小, 节省百分比)``。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        fmt = input_path.suffix.lstrip(".").upper()
        _save_image(img, output_path, fmt=fmt, quality=quality)
        in_size = input_path.stat().st_size
        out_size = output_path.stat().st_size
        ratio = (1 - out_size / in_size) * 100 if in_size > 0 else 0.0
        return in_size, out_size, ratio
    finally:
        img.close()


def image_info(input_path: Path) -> dict[str, Any]:
    """返回图片信息（路径/格式/模式/尺寸/EXIF 标签数）。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        exif = img.getexif()
        exif_count = len(exif) if exif else 0
        return {
            "path": str(input_path),
            "format": img.format,
            "mode": img.mode,
            "width": img.size[0],
            "height": img.size[1],
            "exif_tags": exif_count,
        }
    finally:
        img.close()


@dataclass(frozen=True)
class ExifResult:
    """EXIF 读取/修改结果。"""

    items: list[tuple[Any, Any]]
    warnings: list[str]
    saved_path: Path | None


def image_exif(
    input_path: Path,
    output_path: Path | None = None,
    show: bool = True,
    set: list[str] | None = None,
    clear: bool = False,
) -> ExifResult:
    """读取或修改 EXIF 元数据。

    ``show`` 仅控制调用方是否展示 ``items``；修改后保存路径见 ``saved_path``。
    """
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        exif = img.getexif()
        items = exif_items(exif) if show else []

        modified, warnings = _apply_exif_modifications(exif, set, clear)
        saved_path: Path | None = None
        if modified:
            saved_path = output_path if output_path is not None else input_path
            _save_exif(img, exif, saved_path)
        return ExifResult(items=items, warnings=warnings, saved_path=saved_path)
    finally:
        img.close()


def image_histogram(
    input_path: Path,
    channel: str = "rgb",
) -> list[str]:
    """返回颜色直方图统计文本行（每通道 8 桶）。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        hist = img.histogram()
        buckets = 8
        lines: list[str] = []

        if channel == "luminance":
            gray = img.convert("L")
            gray_hist = gray.histogram()
            lines.append("亮度直方图 (8 桶):")
            lines.extend(_histogram_bucket_lines(gray_hist, buckets, "L"))
        else:
            lines.append("RGB 直方图 (8 桶):")
            if len(hist) == 256:
                lines.extend(_histogram_bucket_lines(hist, buckets, "L"))
            else:
                for idx, name in enumerate(("R", "G", "B")):
                    start = idx * 256
                    lines.extend(_histogram_bucket_lines(hist[start : start + 256], buckets, name))
        return lines
    finally:
        img.close()


def image_colors(
    input_path: Path,
    count: int = 5,
) -> list[str]:
    """返回主色调文本行。"""
    _deps.ensure_pil()

    img = _deps.Image.open(input_path)
    try:
        img = img.convert("RGB")
        quantized = img.quantize(colors=count)
        palette = quantized.getpalette()
        if palette is None:  # pragma: no cover - quantize() 总会生成调色板，防御性守卫
            return ["无法提取调色板"]

        actual_count = len(palette) // 3
        lines = [f"主色调 (前 {min(count, actual_count)} 色):"]
        for i in range(min(count, actual_count)):
            r = palette[i * 3]
            g = palette[i * 3 + 1]
            b = palette[i * 3 + 2]
            hex_color = f"#{r:02X}{g:02X}{b:02X}"
            lines.append(f"  {i + 1}. {hex_color}  rgb({r}, {g}, {b})")
        return lines
    finally:
        img.close()
