"""``fcmd.infras.media.icon``（由 ``infras/media.py`` 拆分）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import deps as _deps

__all__ = [
    "IMG2ICO_IMAGE_EXTENSIONS",
    "IMG2ICO_QUALITY_CHOICES",
    "SVG_TO_ICO_MAC_SIZES",
    "SVG_TO_ICO_WIN_SIZES",
    "ConversionResult",
    "collect_images",
    "convert_images",
    "icon_build",
    "icon_build_batch",
    "is_supported_image",
    "normalize_quality",
    "parse_size_tokens",
    "resolve_output_target",
]

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


SVG_TO_ICO_MAC_SIZES: tuple[tuple[int, int], ...] = (
    (16, 16),
    (32, 32),
    (128, 128),
    (256, 256),
    (512, 512),
    (1024, 1024),
)


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


IMG2ICO_QUALITY_CHOICES: tuple[str, ...] = ("low", "medium", "high")


_MAX_RENDER_SIZE = 1024


_IMAGE_MAGIC_NUMBERS: tuple[bytes, ...] = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",  # GIF87a
    b"GIF89a",  # GIF89a
    b"BM",  # BMP
    b"II*\x00",  # TIFF (little-endian)
    b"MM\x00*",  # TIFF (big-endian)
)


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

    png_bytes = bytes(_deps.resvg_py.svg_to_bytes(svg_path=str(svg_path), width=max_size))
    return _deps.Image.open(io.BytesIO(png_bytes)).convert("RGBA")


def _load_source_image(input_path: Path, max_size: int):
    """读取 SVG / 位图输入并统一转为 RGBA。"""
    if input_path.suffix.lower() == ".svg":
        return _render_svg_to_png(input_path, max_size)
    try:
        with _deps.Image.open(input_path) as original:
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
    resampling = _deps.Image.Resampling

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

    canvas = _deps.Image.new("RGBA", (size, size), (0, 0, 0, 0))
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

    _deps.ensure_deps()

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
