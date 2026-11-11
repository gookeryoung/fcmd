"""媒体域基础设施：图标转换与图片处理（纯函数，无打印、无 CLI）。

工具层（``fcmd.tools.media.imagetool`` / ``img2ico``）只负责参数适配与打印；
Pillow / resvg-py 按需延迟导入，导入本模块不触发重型库加载。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "HAS_PIL",
    "HAS_RESVG",
    "ConversionResult",
    "ExifResult",
    "collect_images",
    "convert_images",
    "ensure_deps",
    "ensure_pil",
    "ensure_resvg",
    "icon_build",
    "icon_build_batch",
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
    "is_supported_image",
    "normalize_quality",
    "parse_size_tokens",
    "resolve_output_target",
]

# ============================================================================
# 可选依赖检查（find_spec 仅查定位，不触发真正 import；PIL / resvg_py 由
# ensure_pil / ensure_resvg 首次调用时惰性导入到模块全局）
# ============================================================================

HAS_PIL = importlib.util.find_spec("PIL") is not None
HAS_RESVG = importlib.util.find_spec("resvg_py") is not None

Image: Any = None
ImageDraw: Any = None
ImageFont: Any = None
resvg_py: Any = None

# ============================================================================
# img2ico 常量与配置
# ============================================================================

# Windows ICO 默认尺寸，参考 GreenFish Icon Editor 的标准：
# 覆盖从浏览器 favicon 到高 DPI 应用图标的全部常见场景。
SVG_TO_ICO_WIN_SIZES: tuple[tuple[int, int], ...] = (
    (16, 16),
    (24, 24),
    (32, 32),
    (40, 40),
    (48, 48),
    (64, 64),
    (72, 72),
    (96, 96),
    (128, 128),
    (256, 256),
)

# macOS ICNS 默认尺寸，遵循 Apple Human Interface Guidelines：
# 对应 icp4/icp5/icp6/icp7/icp8/icp9 六种基础尺寸，Pillow 会
# 自动追加 2x 分辨率条目（icp10/icp11/icp12/icp13/icp14/icp15）。
SVG_TO_ICO_MAC_SIZES: tuple[tuple[int, int], ...] = (
    (16, 16),
    (32, 32),
    (128, 128),
    (256, 256),
    (512, 512),
    (1024, 1024),
)

# 支持的输入扩展名（SVG 由 resvg-py 渲染，其余由 Pillow 直接读取）。
IMG2ICO_IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".webp",
    ".tiff",
    ".tif",
    ".svg",
)

# 重采样质量档位：low 最近邻（最快）/ medium 双线性 / high Lanczos+锐化。
IMG2ICO_QUALITY_CHOICES: tuple[str, ...] = ("low", "medium", "high")

# 最大渲染尺寸：SVG 矢量可无损缩放，先渲染到最大需求尺寸再由高质量
# 重采样生成各子尺寸，避免逐尺寸重复渲染造成性能浪费。
_MAX_RENDER_SIZE = 1024

# 位图文件头魔数（目录批量扫描时过滤掉扩展名正确但内容无效的文件）。
_IMAGE_MAGIC_NUMBERS: tuple[bytes, ...] = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",  # GIF87a
    b"GIF89a",  # GIF89a
    b"BM",  # BMP
    b"II*\x00",  # TIFF (little-endian)
    b"MM\x00*",  # TIFF (big-endian)
)


def ensure_pil() -> None:
    """惰性导入 Pillow 到模块全局；未安装时抛 ``ImportError``。"""
    global Image, ImageDraw, ImageFont
    if not HAS_PIL:
        raise ImportError("Pillow")
    if Image is None:
        from PIL import Image as _image
        from PIL import ImageDraw as _image_draw
        from PIL import ImageFont as _image_font

        Image, ImageDraw, ImageFont = _image, _image_draw, _image_font


def ensure_resvg() -> None:
    """惰性导入 resvg-py 到模块全局；未安装时抛 ``ImportError``。"""
    global resvg_py  # noqa: PLW0603 - 惰性导入需注入模块全局
    if not HAS_RESVG:
        raise ImportError("resvg-py")
    if resvg_py is None:
        import resvg_py as _resvg_py

        resvg_py = _resvg_py


def ensure_deps() -> None:
    """确认图标转换依赖齐全；缺失时抛 ``RuntimeError``。"""
    if not HAS_PIL or not HAS_RESVG:
        raise RuntimeError("缺少依赖: pillow (fcmd[img]) / resvg-py (fcmd[svg])")
    ensure_pil()
    ensure_resvg()


def normalize_quality(quality: str) -> str:
    """把质量档位规范为 ``IMG2ICO_QUALITY_CHOICES`` 中的小写值。

    Raises
    ------
    ValueError
        档位不在支持列表中。
    """
    key = quality.strip().lower()
    if key not in IMG2ICO_QUALITY_CHOICES:
        raise ValueError(f"不支持的质量: {quality}，支持: {', '.join(IMG2ICO_QUALITY_CHOICES)}")
    return key


def parse_size_tokens(items: list[str] | None) -> list[int] | None:
    """解析 CLI 尺寸参数：支持逗号或空格分隔，返回去重排序后的整数列表。

    ``None`` 表示未指定（使用格式默认尺寸）；空列表或全空白视为非法。

    Raises
    ------
    ValueError
        未提供有效尺寸、包含非整数或非正数。
    """
    if items is None:
        return None
    tokens: list[str] = []
    for item in items:
        for piece in item.replace("，", ",").split(","):
            tokens.extend(piece.split())
    parts = [token for token in tokens if token]
    if not parts:
        raise ValueError("未提供有效的尺寸")
    try:
        sizes = [int(part) for part in parts]
    except ValueError as exc:
        raise ValueError(f"尺寸格式无效: {', '.join(parts)}，期望逗号或空格分隔的整数") from exc
    if any(size <= 0 for size in sizes):
        raise ValueError("尺寸必须为正整数")
    return sorted(set(sizes))


def _parse_sizes(sizes: list[int] | None) -> list[tuple[int, int]] | None:
    """将整数尺寸列表转为 (w, h) 元组列表；None 输入返回 None。"""
    if sizes is None:
        return None
    if any(size <= 0 for size in sizes):
        raise ValueError("尺寸必须为正整数")
    return [(s, s) for s in sizes]


def _validate_format(fmt: str) -> str:
    """校验显式格式别名并返回小写原值。"""
    fmt_lower = fmt.strip().lower()
    if fmt_lower not in ("win", "windows", "ico", "mac", "darwin", "icns"):
        raise ValueError(f"不支持的格式: {fmt}，支持: win/ico, mac/icns, auto")
    return fmt_lower


def _detect_format(output: Path, fmt: str | None) -> str:
    """根据显式参数或输出文件后缀推断目标格式。"""
    if fmt is not None:
        return _validate_format(fmt)

    suffix = output.suffix.lower().lstrip(".")
    if suffix == "ico":
        return "ico"
    if suffix == "icns":
        return "icns"
    raise ValueError("无法推断目标格式，请使用 --format win/mac 指定，或让输出路径以 .ico / .icns 结尾")


def _default_sizes_for(fmt: str) -> tuple[tuple[int, int], ...]:
    """返回指定格式的默认尺寸列表。"""
    return SVG_TO_ICO_MAC_SIZES if fmt in ("mac", "darwin", "icns") else SVG_TO_ICO_WIN_SIZES


def _resolve_format_alias(fmt: str) -> str:
    """将格式别名统一到规范名 ``windows`` / ``mac``。"""
    if fmt in ("win", "windows", "ico"):
        return "windows"
    return "mac"


def _render_svg_to_png(svg_path: Path, max_size: int):
    """用 resvg-py 将 SVG 渲染为 RGBA 位图（仅指定宽度，保持原始宽高比）。"""
    import io

    png_bytes = bytes(resvg_py.svg_to_bytes(svg_path=str(svg_path), width=max_size))
    return Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def _load_source_image(input_path: Path, max_size: int):
    """读取 SVG / 位图输入并统一转为 RGBA。"""
    if input_path.suffix.lower() == ".svg":
        return _render_svg_to_png(input_path, max_size)
    try:
        with Image.open(input_path) as original:
            return original.convert("RGBA")
    except OSError as exc:
        raise ValueError(f"无法读取图像: {input_path}") from exc


def _looks_like_svg(path: Path) -> bool:
    """粗略校验 SVG 文件内容（允许省略 XML 声明，直接以 ``<svg`` 开头）。"""
    try:
        with path.open("rb") as fh:
            head = fh.read(512)
    except OSError:
        return False
    return b"<svg" in head.lower()


def _has_valid_magic(path: Path) -> bool:
    """按文件头魔数校验位图格式；WebP 单独判断 RIFF....WEBP。"""
    try:
        with path.open("rb") as fh:
            header = fh.read(16)
    except OSError:
        return False
    if not header:
        return False
    if path.suffix.lower() == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    return any(header.startswith(magic) for magic in _IMAGE_MAGIC_NUMBERS)


def is_supported_image(path: Path) -> bool:
    """判断路径是否为可转换的图像文件（扩展名 + 非空 + 文件头校验）。"""
    if not path.is_file() or path.suffix.lower() not in IMG2ICO_IMAGE_EXTENSIONS:
        return False
    try:
        if path.stat().st_size == 0:
            return False
    except OSError:
        return False
    if path.suffix.lower() == ".svg":
        return _looks_like_svg(path)
    return _has_valid_magic(path)


def collect_images(directory: Path) -> list[Path]:
    """收集目录下全部受支持的图像文件（按文件名排序）。"""
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if is_supported_image(path))


def _resize_for_icon(image: Any, size: int, quality: str):
    """把图像按原始宽高比缩放并居中粘贴到 ``size`` 方形透明画布。

    质量档位：low 最近邻；medium 双线性；high 放大用 BICUBIC + 锐化、
    极端缩小用多步 LANCZOS、普通缩小用 LANCZOS。
    """
    from PIL import ImageFilter

    src_width, src_height = image.size
    ratio = min(size / src_width, size / src_height)
    new_width = max(1, round(src_width * ratio))
    new_height = max(1, round(src_height * ratio))
    resampling = Image.Resampling

    if quality == "low":
        resized = image.resize((new_width, new_height), resampling.NEAREST)
    elif quality == "medium":
        resized = image.resize((new_width, new_height), resampling.BILINEAR)
    elif ratio > 1:
        # 放大小图像时用 BICUBIC，并轻微锐化补偿放大模糊
        resized = image.resize((new_width, new_height), resampling.BICUBIC)
        resized = resized.filter(ImageFilter.SHARPEN)
    elif ratio < 0.25:
        # 极端缩小：分多步 LANCZOS，避免一次性缩小的锯齿与细节丢失
        step1 = image.resize((size * 4, size * 4), resampling.LANCZOS)
        step2 = step1.resize((size * 2, size * 2), resampling.LANCZOS)
        resized = step2.resize((new_width, new_height), resampling.LANCZOS)
    elif ratio < 0.5:
        # 重度缩小：两步 LANCZOS
        step = image.resize((size * 2, size * 2), resampling.LANCZOS)
        resized = step.resize((new_width, new_height), resampling.LANCZOS)
    else:
        resized = image.resize((new_width, new_height), resampling.LANCZOS)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(resized, ((size - new_width) // 2, (size - new_height) // 2), resized)
    return canvas


def _save_multisize_ico(icon_images: list[Any], sizes: list[int], output_path: Path) -> None:
    """手工构造多尺寸 ICO（每个尺寸以 PNG 数据嵌入，Vista 之后的通行做法）。

    Pillow 自带的 ICO 多尺寸保存会统一重采样，无法保留逐尺寸质量档位；
    这里直接写入目录项与 PNG 数据块，确保每个尺寸都是已处理好的位图。
    """
    import io
    import struct

    payloads: list[tuple[bytes, int]] = []
    for image, size in zip(icon_images, sizes, strict=True):
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        payloads.append((buffer.getvalue(), size))
        buffer.close()

    count = len(payloads)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + count * 16
    entries: list[bytes] = []
    blocks: list[bytes] = []
    for data, size in payloads:
        # ICO 目录项宽高为 1 字节，0 表示 256（及更大尺寸）
        dimension = size if size < 256 else 0
        entries.append(
            struct.pack(
                "<BBBBHHII",
                dimension & 0xFF,
                dimension & 0xFF,
                0,  # 调色板颜色数（PNG 不使用）
                0,  # 保留字段
                1,  # 颜色平面数
                32,  # 位深 RGBA
                len(data),
                offset,
            )
        )
        blocks.append(data)
        offset += len(data)

    output_path.write_bytes(header + b"".join(entries) + b"".join(blocks))


def icon_build(
    input_path: Path,
    output_path: Path,
    fmt: str | None = None,
    sizes: list[int] | None = None,
    quality: str = "high",
) -> tuple[str, list[tuple[int, int]]]:
    """将 SVG / 位图转换为 ICO / ICNS 图标文件。

    图像按原始宽高比缩放并居中放入透明方形画布；ICO 逐尺寸独立重采样后
    手工写入 PNG 数据块，ICNS 由 Pillow 按尺寸表生成。
    """
    if not input_path.exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    if not input_path.is_file():
        raise ValueError(f"输入路径不是文件: {input_path}")
    if input_path.suffix.lower() not in IMG2ICO_IMAGE_EXTENSIONS:
        raise ValueError(
            f"不支持的图像格式: {input_path.suffix or '(无后缀)'}，支持: {', '.join(IMG2ICO_IMAGE_EXTENSIONS)}"
        )

    ensure_deps()

    resolved_quality = normalize_quality(quality)
    resolved_fmt = _detect_format(output_path, fmt)
    default_sizes = _default_sizes_for(resolved_fmt)
    actual_sizes = _parse_sizes(sizes) if sizes is not None else list(default_sizes)

    if not actual_sizes:
        raise ValueError("尺寸列表为空，请至少指定一个尺寸")

    max_size = max(s for s, _ in actual_sizes)
    render_size = min(max(max_size, 32), _MAX_RENDER_SIZE)
    img = _load_source_image(input_path, render_size)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    canonical = _resolve_format_alias(resolved_fmt)
    if canonical == "windows":
        icon_images = [_resize_for_icon(img, size, resolved_quality) for size, _ in actual_sizes]
        _save_multisize_ico(icon_images, [size for size, _ in actual_sizes], output_path)
    else:
        base_size = max(size for size, _ in actual_sizes)
        base_image = _resize_for_icon(img, base_size, resolved_quality)
        base_image.save(output_path, format="ICNS", sizes=actual_sizes)

    return canonical, actual_sizes


def _resolve_conversion_format(input_path: Path, output_path: Path | None, fmt: str | None) -> str:
    """为一次转换推断目标格式（文件看后缀，目录/未指定时默认 Windows）。"""
    if fmt is not None:
        return _validate_format(fmt)
    if input_path.is_file() and output_path is not None:
        if output_path.is_dir():
            return "win"
        suffix = output_path.suffix.lower()
        if suffix == ".ico":
            return "ico"
        if suffix == ".icns":
            return "icns"
        if not suffix:
            return "win"
        raise ValueError("无法推断目标格式，请使用 --format win/mac 指定，或让输出路径以 .ico / .icns 结尾")
    return "win"


def resolve_output_target(
    input_path: Path,
    output_path: Path | None = None,
    fmt: str | None = None,
) -> tuple[Path, str]:
    """解析实际输出目标及规范格式。

    - 目录输入：输出为目录（缺省 ``<输入目录>/ico_output``）
    - 文件输入且未指定输出：同目录同名 ``.ico`` / ``.icns``
    - 输出路径是已存在目录或无后缀路径：视为目录，内部使用输入文件名
    """
    resolved_fmt = _resolve_conversion_format(input_path, output_path, fmt)
    canonical = _resolve_format_alias(resolved_fmt)
    suffix = ".icns" if canonical == "mac" else ".ico"

    if input_path.is_dir():
        base = output_path if output_path is not None else input_path / "ico_output"
        if base.exists() and not base.is_dir():
            raise ValueError(f"目录输入的输出路径必须是目录: {base}")
        return base, canonical

    if output_path is None:
        return input_path.with_suffix(suffix), canonical
    if output_path.exists() and output_path.is_dir():
        return output_path / f"{input_path.stem}{suffix}", canonical
    if not output_path.suffix and not output_path.exists():
        return output_path / f"{input_path.stem}{suffix}", canonical
    return output_path, canonical


def icon_build_batch(  # noqa: PLR0913, PLR0917 - 转换参数由 CLI/GUI 共用
    input_dir: Path,
    output_dir: Path,
    fmt: str | None = None,
    sizes: list[int] | None = None,
    quality: str = "high",
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[str, list[Path], list[tuple[str, str]]]:
    """把目录内全部受支持图像批量转换为图标。

    Returns
    -------
    tuple[str, list[Path], list[tuple[str, str]]]
        ``(规范格式, 输出路径列表, 跳过列表)``；跳过项为
        ``(文件名, 错误信息)``，由调用方决定如何输出。
    """
    resolved_quality = normalize_quality(quality)
    canonical = _resolve_format_alias(fmt or "win")
    suffix = ".icns" if canonical == "mac" else ".ico"
    images = collect_images(input_dir)
    if not images:
        raise ValueError(f"目录中未找到受支持的图像文件: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    skipped: list[tuple[str, str]] = []
    total = len(images)
    for index, image in enumerate(images, start=1):
        if progress is not None:
            progress(index - 1, total, f"正在转换 {image.name}…")
        target = output_dir / f"{image.stem}{suffix}"
        try:
            icon_build(image, target, fmt=canonical, sizes=sizes, quality=resolved_quality)
        except (OSError, RuntimeError, ValueError) as exc:
            skipped.append((image.name, str(exc)))
            continue
        outputs.append(target)
        if progress is not None:
            progress(index, total, f"完成 {target.name}")
    return canonical, outputs, skipped


@dataclass(frozen=True)
class ConversionResult:
    """批量/单文件转换结果。"""

    canonical: str
    outputs: list[Path]
    used_sizes: list[tuple[int, int]]
    skipped: list[tuple[str, str]]


def convert_images(  # noqa: PLR0913, PLR0917 - 转换参数由 CLI/GUI 共用
    input_path: Path,
    output_path: Path | None = None,
    fmt: str | None = None,
    sizes: list[int] | None = None,
    quality: str = "high",
    progress: Callable[[int, int, str], None] | None = None,
) -> ConversionResult:
    """文件 / 目录统一转换入口，返回规范格式、输出路径、使用尺寸与跳过项。"""
    if not input_path.exists():
        raise FileNotFoundError(f"输入路径不存在: {input_path}")
    if sizes is not None and not sizes:
        raise ValueError("尺寸列表为空，请至少指定一个尺寸")

    resolved_quality = normalize_quality(quality)
    target, canonical = resolve_output_target(input_path, output_path, fmt)

    skipped: list[tuple[str, str]] = []
    if input_path.is_dir():
        canonical, outputs, skipped = icon_build_batch(
            input_path,
            target,
            fmt=canonical,
            sizes=sizes,
            quality=resolved_quality,
            progress=progress,
        )
    else:
        icon_build(input_path, target, fmt=canonical, sizes=sizes, quality=resolved_quality)
        outputs = [target]
        if progress is not None:
            progress(1, 1, f"完成 {target.name}")

    used_sizes = _parse_sizes(sizes) if sizes is not None else list(_default_sizes_for(canonical))
    return ConversionResult(
        canonical=canonical,
        outputs=outputs,
        used_sizes=used_sizes or [],
        skipped=skipped,
    )


# ============================================================================
# imagetool
# ============================================================================


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
    ensure_pil()
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
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
    try:
        method = Image.Transpose.FLIP_LEFT_RIGHT if direction == "horizontal" else Image.Transpose.FLIP_TOP_BOTTOM
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
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
    try:
        img = img.convert("RGBA")
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
    finally:
        img.close()


def image_compress(
    input_path: Path,
    output_path: Path,
    quality: int = 85,
) -> tuple[int, int, float]:
    """压缩图片（重新编码以减小体积），返回 ``(原大小, 新大小, 节省百分比)``。"""
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
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
    ensure_pil()

    img = Image.open(input_path)
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
