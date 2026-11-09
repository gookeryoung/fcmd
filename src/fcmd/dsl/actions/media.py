"""媒体类 DSL 动作：img2ico / imagetool / pdftool。

三个工具原属 ``fcmd.tools.media`` 包，全部为纯 Python 实现（Pillow /
resvg-py / PyMuPDF / pypdf），无子进程调用。迁移后由 ``@action`` 装饰器
注册，TOML 声明 ``action = "<名>"`` 直接引用；CLI 参数 schema 从动作签名
自动推导（synth 层拷贝签名注入合成函数）。

外部库按需延迟导入——工具发现阶段不触发重型 PDF/PIL 库加载；动作执行期
import 失败时打印友好提示并返回，退出码保持 0。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from ..actions import action

if TYPE_CHECKING:
    from collections.abc import Callable

    import fitz  # PyMuPDF
    import pypdf

# ============================================================================
# 可选依赖检查（find_spec 仅查定位，不触发真正 import；PIL / resvg_py 由
# _require_pil / _require_resvg 首次调用时惰性导入到模块全局）
# ============================================================================

HAS_PIL = importlib.util.find_spec("PIL") is not None
HAS_RESVG = importlib.util.find_spec("resvg_py") is not None
HAS_PYMUPDF = importlib.util.find_spec("fitz") is not None
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None

# PIL / resvg_py 惰性占位（_require_pil / _require_resvg 首次调用填充；
# 类型为 Any，全模块对图片对象一律以 Any 传递）
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


def _missing_dependency(package: str, extra: str) -> bool:
    """打印可选依赖缺失提示并返回 False（供 ``_require_*`` 统一返回）。"""
    print(f"未安装 {package} 库，请安装: pip install fcmd[{extra}]")
    return False


def _require_deps() -> bool:
    """检查 PIL + resvg-py 依赖，缺失时打印提示并返回 False。"""
    return _require_pil() and _require_resvg()


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

    # 直接调用（绕过 img2ico_gen 入口）时兜底校验并填充惰性全局
    if not _require_deps():
        raise RuntimeError("缺少依赖: pillow (fcmd[img]) / resvg-py (fcmd[svg])")

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
) -> tuple[str, list[Path]]:
    """把目录内全部受支持图像批量转换为图标，返回规范格式与输出路径列表。

    单个文件失败时打印跳过原因并继续处理其余文件；``progress`` 回调
    形如 ``(已完成数, 总数, 消息)``，供 GUI 更新进度与日志。
    """
    resolved_quality = normalize_quality(quality)
    canonical = _resolve_format_alias(fmt or "win")
    suffix = ".icns" if canonical == "mac" else ".ico"
    images = collect_images(input_dir)
    if not images:
        raise ValueError(f"目录中未找到受支持的图像文件: {input_dir}")

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    total = len(images)
    for index, image in enumerate(images, start=1):
        if progress is not None:
            progress(index - 1, total, f"正在转换 {image.name}…")
        target = output_dir / f"{image.stem}{suffix}"
        try:
            icon_build(image, target, fmt=canonical, sizes=sizes, quality=resolved_quality)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"跳过 {image.name}: {exc}")
            continue
        outputs.append(target)
        if progress is not None:
            progress(index, total, f"完成 {target.name}")
    return canonical, outputs


def convert_images(  # noqa: PLR0913, PLR0917 - 转换参数由 CLI/GUI 共用
    input_path: Path,
    output_path: Path | None = None,
    fmt: str | None = None,
    sizes: list[int] | None = None,
    quality: str = "high",
    progress: Callable[[int, int, str], None] | None = None,
) -> tuple[str, list[Path], list[tuple[int, int]]]:
    """文件 / 目录统一转换入口，返回（规范格式, 输出路径列表, 使用的尺寸）。

    CLI 动作与 Tk GUI 共用此函数，保证两条入口的路径语义与转换结果一致。
    """
    if not input_path.exists():
        raise FileNotFoundError(f"输入路径不存在: {input_path}")
    if sizes is not None and not sizes:
        raise ValueError("尺寸列表为空，请至少指定一个尺寸")

    resolved_quality = normalize_quality(quality)
    target, canonical = resolve_output_target(input_path, output_path, fmt)

    if input_path.is_dir():
        canonical, outputs = icon_build_batch(
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
    return canonical, outputs, used_sizes or []


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
    if not _require_deps():
        return

    parsed_sizes = parse_size_tokens(sizes)
    canonical, outputs, used_sizes = convert_images(
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


# ============================================================================
# img2pdf 公共逻辑
# ============================================================================

# 可直接写入 PDF 的位图格式（SVG 需先栅格化，不在本工具支持范围内）。
IMG2PDF_IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".ico",
)

# 常用页面尺寸（英寸，宽 x 高）；``auto`` 表示按原图像素尺寸逐页输出。
IMG2PDF_PAGE_SIZES: dict[str, tuple[float, float]] = {
    "a3": (11.69, 16.54),
    "a4": (8.27, 11.69),
    "a5": (5.83, 8.27),
    "b5": (6.93, 9.84),
    "letter": (8.5, 11.0),
    "legal": (8.5, 14.0),
}

IMG2PDF_PAGE_SIZE_CHOICES: tuple[str, ...] = ("auto", *IMG2PDF_PAGE_SIZES)

IMG2PDF_DEFAULT_DPI = 150
IMG2PDF_MIN_DPI = 36
IMG2PDF_MAX_DPI = 1200

# ICO 文件头（Pillow 可读，但不在 _IMAGE_MAGIC_NUMBERS 中）。
_ICO_MAGIC = b"\x00\x00\x01\x00"


def normalize_page_size(value: str) -> str:
    """把页面尺寸规范为 ``auto`` 或 :data:`IMG2PDF_PAGE_SIZES` 中的小写键。

    Raises
    ------
    ValueError
        取值不在支持列表中。
    """
    key = value.strip().lower()
    if key not in IMG2PDF_PAGE_SIZE_CHOICES:
        raise ValueError(f"不支持的页面尺寸: {value}，支持: {', '.join(IMG2PDF_PAGE_SIZE_CHOICES)}")
    return key


def normalize_dpi(dpi: int) -> int:
    """校验 DPI 取值范围（36-1200），返回原值。

    Raises
    ------
    ValueError
        非整数或超出范围。
    """
    if isinstance(dpi, bool) or not isinstance(dpi, int) or not IMG2PDF_MIN_DPI <= dpi <= IMG2PDF_MAX_DPI:
        raise ValueError(f"DPI 须为 {IMG2PDF_MIN_DPI}-{IMG2PDF_MAX_DPI} 的整数: {dpi!r}")
    return dpi


def _is_pdf_image(path: Path) -> bool:
    """判断路径是否为可写入 PDF 的有效位图（扩展名 + 非空 + 文件头）。"""
    if not path.is_file() or path.suffix.lower() not in IMG2PDF_IMAGE_EXTENSIONS:
        return False
    try:
        if path.stat().st_size == 0:
            return False
        with path.open("rb") as fh:
            header = fh.read(16)
    except OSError:
        return False
    suffix = path.suffix.lower()
    if suffix == ".webp":
        return header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    if suffix == ".ico":
        return header.startswith(_ICO_MAGIC)
    return any(header.startswith(magic) for magic in _IMAGE_MAGIC_NUMBERS)


def collect_pdf_images(paths: list[Path] | tuple[Path, ...]) -> list[Path]:
    """展开文件与目录输入为按页顺序排列的图片路径列表。

    目录按文件名排序；显式文件保持用户给定顺序；完全重复的路径只保留
    首次出现（按 ``resolve()`` 判重）。

    Raises
    ------
    FileNotFoundError
        输入路径不存在。
    ValueError
        目录内无有效图片，或显式文件格式不支持 / 内容损坏。
    """
    collected: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        if path.is_dir():
            candidates = sorted(item for item in path.iterdir() if _is_pdf_image(item))
            if not candidates:
                raise ValueError(f"目录中未找到可转换的图片: {path}")
        elif path.is_file():
            if not _is_pdf_image(path):
                raise ValueError(f"不支持的图片格式或文件无效: {path}")
            candidates = [path]
        else:
            raise FileNotFoundError(f"输入路径不存在: {path}")
        for candidate in candidates:
            key = candidate.resolve()
            if key not in seen:
                seen.add(key)
                collected.append(candidate)
    if not collected:
        raise ValueError("未提供可转换的图片")
    return collected


def _to_pdf_rgb(image: Any) -> Any:
    """转为 RGB 并把透明区域铺白底（PDF 不支持 alpha 通道）。"""
    if image.mode in {"RGBA", "LA"}:
        rgba = image.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    if image.mode == "P" and "transparency" in image.info:
        return _to_pdf_rgb(image.convert("RGBA"))
    return image.convert("RGB")


def _fit_pdf_page(image: Any, page_size: tuple[int, int]) -> Any:
    """按原始宽高比缩放并居中放入白色页面画布。"""
    page_width, page_height = page_size
    ratio = min(page_width / image.width, page_height / image.height)
    width = max(1, round(image.width * ratio))
    height = max(1, round(image.height * ratio))
    if (width, height) != image.size:
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", page_size, (255, 255, 255))
    canvas.paste(image, ((page_width - width) // 2, (page_height - height) // 2))
    return canvas


def resolve_pdf_output(output_path: Path) -> Path:
    """规范化输出路径：目录 → ``output.pdf``，无后缀 → 补 ``.pdf``。

    Raises
    ------
    ValueError
        后缀不是 ``.pdf``。
    """
    if output_path.exists() and output_path.is_dir():
        return output_path / "output.pdf"
    if not output_path.suffix:
        return output_path.with_suffix(".pdf")
    if output_path.suffix.lower() != ".pdf":
        raise ValueError(f"输出文件须为 .pdf: {output_path}")
    return output_path


def images_to_pdf(  # noqa: PLR0913, PLR0917  参数由 CLI 与 GUI 共享，保持显式
    paths: list[Path] | tuple[Path, ...],
    output_path: Path,
    page_size: str = "auto",
    dpi: int = IMG2PDF_DEFAULT_DPI,
    normalize: bool = True,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """把图片按给定顺序合并为一个 PDF，返回实际输出路径。

    ``page_size="auto"`` 时每页沿用原图像素尺寸；指定固定页面尺寸时按
    ``dpi`` 换算页面像素并等比缩放居中。``normalize=True`` 会先按 EXIF
    方向校正图片。CLI 动作与 Tk GUI 共用此函数，保证两条入口结果一致。

    Raises
    ------
    FileNotFoundError
        输入路径不存在。
    ValueError
        图片列表为空、格式不支持、DPI / 页面尺寸非法、输出后缀非法。
    RuntimeError
        Pillow 未安装。
    """
    if not _require_pil():
        raise RuntimeError("缺少依赖: pillow（pip install fcmd[img]）")

    page_key = normalize_page_size(page_size)
    resolved_dpi = normalize_dpi(dpi)
    images = collect_pdf_images(paths)
    output = resolve_pdf_output(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    page_pixels: tuple[int, int] | None = None
    if page_key != "auto":
        width_inch, height_inch = IMG2PDF_PAGE_SIZES[page_key]
        page_pixels = (round(width_inch * resolved_dpi), round(height_inch * resolved_dpi))

    from PIL import ImageOps  # 惰性导入，保持工具发现阶段轻量

    total = len(images)
    pages: list[Any] = []
    try:
        for index, path in enumerate(images, start=1):
            if progress is not None:
                progress(index - 1, total, f"正在处理 {path.name}…")
            try:
                with Image.open(path) as original:
                    source = ImageOps.exif_transpose(original) if normalize else original
                    page = _to_pdf_rgb(source)
                    if page_pixels is not None:
                        page = _fit_pdf_page(page, page_pixels)
            except (OSError, ValueError) as exc:
                raise ValueError(f"无法读取图像 {path}: {exc}") from exc
            pages.append(page)
            if progress is not None:
                progress(index, total, f"已加入第 {index} 页: {path.name}")

        pages[0].save(
            output,
            "PDF",
            resolution=float(resolved_dpi),
            save_all=True,
            append_images=pages[1:],
            optimize=True,
        )
    finally:
        for page in pages:
            page.close()
    return output


@action(
    "img2pdf_gen",
    param_help={
        "images": "输入图片文件或目录（可多个；目录按文件名排序）",
        "output_path": "输出 PDF 文件路径（默认 output.pdf）",
        "page_size": "页面尺寸：auto / A4 / A3 / A5 / B5 / letter / legal（默认 auto）",
        "dpi": "页面 DPI（36-1200，默认 150）",
        "normalize": "自动校正 EXIF 方向（默认开启）",
    },
    errors=(OSError, ValueError, RuntimeError),
)
def img2pdf_gen(
    images: list[str],
    output_path: Path = Path("output.pdf"),
    page_size: str = "auto",
    dpi: int = IMG2PDF_DEFAULT_DPI,
    normalize: bool = True,
) -> None:
    """图片合并为 PDF（支持文件/目录输入、页面尺寸与 DPI 设置）。"""
    if not _require_pil():
        return

    paths = [Path(item) for item in images]
    output = images_to_pdf(paths, output_path, page_size=page_size, dpi=dpi, normalize=normalize)
    print(f"PDF 已生成: {output}")
    print(f"页面尺寸: {normalize_page_size(page_size)}；DPI: {normalize_dpi(dpi)}")


# ============================================================================
# imagetool 公共逻辑
# ============================================================================


def _require_pil() -> bool:
    """Pillow 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PIL:
        return _missing_dependency("Pillow", "img")
    global Image, ImageDraw, ImageFont  # 惰性导入需 global 注入模块，避免工具发现时加载
    if Image is None:
        from PIL import Image as _image
        from PIL import ImageDraw as _image_draw
        from PIL import ImageFont as _image_font

        Image, ImageDraw, ImageFont = _image, _image_draw, _image_font
    return True


def _require_resvg() -> bool:
    """resvg-py 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_RESVG:
        return _missing_dependency("resvg-py", "svg")
    global resvg_py  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if resvg_py is None:
        import resvg_py as _resvg_py

        resvg_py = _resvg_py
    return True


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
    _require_pil()  # 直接调用时兜底填充惰性全局
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


# ---------------------------------------------------------------------- #
# imagetool 公共函数（原 __all__ 导出，保持签名兼容）
# ---------------------------------------------------------------------- #


def image_resize(
    input_path: Path,
    output_path: Path,
    width: int,
    height: int | None = None,
    keep_ratio: bool = True,
) -> None:
    """调整图片尺寸。"""
    if not _require_pil():
        return

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
        print(f"调整尺寸完成: {output_path} ({img.size[0]}x{img.size[1]})")
    finally:
        img.close()


def image_crop(  # noqa: PLR0913, PLR0917
    input_path: Path,
    output_path: Path,
    left: int,
    top: int,
    right: int,
    bottom: int,
) -> None:
    """裁剪图片到指定矩形。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        cropped = img.crop((left, top, right, bottom))
        _save_image(cropped, output_path)
        print(f"裁剪完成: {output_path} ({right - left}x{bottom - top})")
    finally:
        img.close()


def image_rotate(
    input_path: Path,
    output_path: Path,
    degrees: float,
    expand: bool = False,
) -> None:
    """旋转图片。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        rotated = img.rotate(degrees, expand=expand)
        _save_image(rotated, output_path)
        print(f"旋转完成: {output_path} ({degrees}度)")
    finally:
        img.close()


def image_flip(
    input_path: Path,
    output_path: Path,
    direction: str = "horizontal",
) -> None:
    """翻转图片。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        method = Image.Transpose.FLIP_LEFT_RIGHT if direction == "horizontal" else Image.Transpose.FLIP_TOP_BOTTOM
        flipped = img.transpose(method)
        _save_image(flipped, output_path)
        print(f"翻转完成: {output_path} ({direction})")
    finally:
        img.close()


def image_convert(
    input_path: Path,
    output_path: Path,
    format: str | None = None,
    quality: int = 85,
) -> None:
    """转换图片格式。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        _save_image(img, output_path, fmt=format, quality=quality)
        actual_fmt = format or output_path.suffix.lstrip(".").upper()
        print(f"格式转换完成: {output_path} ({actual_fmt})")
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
    if not _require_pil():
        return

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
        print(f"水印添加完成: {output_path}")
    finally:
        img.close()


def image_compress(
    input_path: Path,
    output_path: Path,
    quality: int = 85,
) -> None:
    """压缩图片（重新编码以减小体积）。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        fmt = input_path.suffix.lstrip(".").upper()
        _save_image(img, output_path, fmt=fmt, quality=quality)
        in_size = input_path.stat().st_size
        out_size = output_path.stat().st_size
        ratio = (1 - out_size / in_size) * 100 if in_size > 0 else 0.0
        print(f"压缩完成: {output_path} (原 {in_size}B → 新 {out_size}B, 节省 {ratio:.1f}%)")
    finally:
        img.close()


def image_info(input_path: Path, json: bool = False) -> None:
    """打印图片信息（尺寸/格式/模式/EXIF 摘要）。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
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
    finally:
        img.close()


def image_exif(
    input_path: Path,
    output_path: Path | None = None,
    show: bool = True,
    set: list[str] | None = None,
    clear: bool = False,
) -> None:
    """读取或修改 EXIF 元数据。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        exif = img.getexif()

        if show:
            _print_exif(exif)

        modified = _apply_exif_modifications(exif, set, clear)
        if modified:
            _save_exif(img, exif, output_path if output_path is not None else input_path)
    finally:
        img.close()


def image_histogram(
    input_path: Path,
    channel: str = "rgb",
) -> None:
    """打印颜色直方图统计（每通道 8 桶）。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
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
    finally:
        img.close()


def image_colors(
    input_path: Path,
    count: int = 5,
) -> None:
    """提取并打印主色调。"""
    if not _require_pil():
        return

    img = Image.open(input_path)
    try:
        img = img.convert("RGB")
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
    finally:
        img.close()


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
    image_resize(input_path, output_path, width, height, keep_ratio)


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
    image_crop(input_path, output_path, left, top, right, bottom)


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
    image_rotate(input_path, output_path, degrees, expand)


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
    image_flip(input_path, output_path, direction)


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
    image_convert(input_path, output_path, format, quality)


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
    image_watermark(input_path, output_path, text, position, opacity, font_size)


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
    image_compress(input_path, output_path, quality)


@action(
    "imagetool_i",
    param_help={
        "input_path": "输入图片路径",
        "json": "是否以 JSON 格式输出（默认 False=纯文本）",
    },
)
def imagetool_i(input_path: Path, json: bool = False) -> None:
    """查看图片信息（i 子命令）。"""
    image_info(input_path, json)


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
    image_exif(input_path, output_path, show, set, clear)


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
    image_histogram(input_path, channel)


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
    image_colors(input_path, count)


# ============================================================================
# pdftool 公共逻辑
# ============================================================================

from fcmd.core.models.pdf import MergeSpec, PageSelection, SplitSpec  # noqa: E402


def _require_pymupdf() -> bool:
    """PyMuPDF 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYMUPDF:
        return _missing_dependency("PyMuPDF", "pdf")
    global fitz  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    import fitz

    return True


def _require_pypdf() -> bool:
    """pypdf 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYPDF:
        return _missing_dependency("pypdf", "pdf")
    global pypdf  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    import pypdf

    return True


def _resolve_page_indices(pages: str, page_count: int) -> tuple[int, ...] | None:
    """解析页码筛选表达式为 0-based 页索引序列。"""
    try:
        return PageSelection.parse(pages).resolve(page_count)
    except ValueError as exc:
        print(f"错误: {exc}")
        return None


# ---------------------------------------------------------------------- #
# pdftool 公共函数（原 __all__ 导出）
# ---------------------------------------------------------------------- #


def pdf_merge(
    input_paths: list[Path],
    output_path: Path = Path("merged.pdf"),
    mode: Literal["concat", "interleave"] = "concat",
    orders: list[str] | None = None,
    pages: list[str] | None = None,
) -> None:
    """合并多个 PDF 文件。"""
    if not _require_pypdf():
        return

    order_list = list(orders or [])
    page_list = list(pages or [])
    kept: list[tuple[Path, str, str]] = []
    for i, input_path in enumerate(input_paths):
        if input_path.exists():
            kept_order = order_list[i] if i < len(order_list) else "forward"
            kept_pages = page_list[i] if i < len(page_list) else ""
            kept.append((input_path, kept_order, kept_pages))

    try:
        spec = MergeSpec.from_cli(
            [item[0] for item in kept],
            mode,
            [item[1] for item in kept],
            [item[2] for item in kept],
        )
    except ValueError as exc:
        print(f"错误: {exc}")
        return

    readers = [pypdf.PdfReader(str(item[0])) for item in kept]
    plan = spec.resolve([len(reader.pages) for reader in readers])

    writer = pypdf.PdfWriter()
    for input_idx, page_idx in plan:
        writer.add_page(readers[input_idx].pages[page_idx])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"合并完成: {output_path}")


def pdf_split(
    input_path: Path,
    output_dir: Path = Path("split"),
    order: str = "forward",
    every: int = 1,
    groups: str = "",
) -> None:
    """拆分 PDF 文件。"""
    if not _require_pypdf():
        return

    try:
        spec = SplitSpec.parse(order, every, groups)
    except ValueError as exc:
        print(f"错误: {exc}")
        return

    reader = pypdf.PdfReader(str(input_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    chunks = spec.resolve(len(reader.pages))
    for seq, chunk in enumerate(chunks, start=1):
        writer = pypdf.PdfWriter()
        for page_idx in chunk:
            writer.add_page(reader.pages[page_idx])
        suffix = "page" if len(chunk) == 1 else "part"
        output_file = output_dir / f"{input_path.stem}_{suffix}_{seq}.pdf"
        with output_file.open("wb") as f:
            writer.write(f)

    print(f"拆分完成: {output_dir} (共 {len(chunks)} 份)")


def pdf_compress(input_path: Path, output_path: Path = Path("compressed.pdf")) -> None:
    """压缩 PDF 文件。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    original_size = input_path.stat().st_size
    new_size = output_path.stat().st_size
    ratio = (1 - new_size / original_size) * 100 if original_size > 0 else 0.0
    print(f"压缩完成: {output_path} (缩小 {ratio:.1f}%)")


def pdf_encrypt(
    input_path: Path,
    output_path: Path = Path("encrypted.pdf"),
    password: str = "",
) -> None:
    """加密 PDF 文件。"""
    if not password:
        print("错误: --password 为必填参数")
        return
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    writer = pypdf.PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    writer.encrypt(user_password=password, owner_password=password, use_128bit=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"加密完成: {output_path}")


def pdf_decrypt(
    input_path: Path,
    output_path: Path = Path("decrypted.pdf"),
    password: str = "",
) -> None:
    """解密 PDF 文件。"""
    if not password:
        print("错误: --password 为必填参数")
        return
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    if reader.is_encrypted:
        reader.decrypt(password)

    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"解密完成: {output_path}")


def pdf_extract_text(
    input_path: Path,
    output_path: Path = Path("output.txt"),
    pages: str = "",
) -> None:
    """提取 PDF 文本。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        parts = [str(doc[i].get_text()) + "\n\n" for i in indices]
    finally:
        doc.close()
    text = "".join(parts)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(text, encoding="utf-8")
    print(f"文本提取完成: {output_path}")


def pdf_extract_images(
    input_path: Path,
    output_dir: Path = Path("images"),
    pages: str = "",
) -> None:
    """提取 PDF 图片。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    indices = _resolve_page_indices(pages, doc.page_count)
    if indices is None:
        doc.close()
        return

    image_count = 0
    try:
        for page_idx in indices:
            page = doc[page_idx]
            images = page.get_images(full=True)
            for img_idx, img in enumerate(images):
                xref = img[0]
                base_image = doc.extract_image(xref)
                image_data = base_image["image"]
                image_ext = base_image["ext"]
                image_path = output_dir / f"page_{page_idx + 1}_img_{img_idx + 1}.{image_ext}"
                image_path.write_bytes(image_data)
                image_count += 1
    finally:
        doc.close()
    print(f"图片提取完成: {output_dir} (共 {image_count} 张)")


def pdf_add_watermark(
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
) -> None:
    """添加 PDF 水印。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        for page_idx in indices:
            page = doc[page_idx]
            rect = page.rect
            text_width = fitz.get_text_length(text, fontsize=48)
            x = (rect.width - text_width) / 2
            y = rect.height / 2
            page.insert_text((x, y), text, fontsize=48, rotate=0, color=(0, 0, 0))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()
    print(f"水印添加完成: {output_path}")


def pdf_rotate(
    input_path: Path,
    output_path: Path = Path("rotated.pdf"),
    rotation: int = 90,
    pages: str = "",
) -> None:
    """旋转 PDF 页面。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        for page_idx in indices:
            doc[page_idx].set_rotation(rotation)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()
    print(f"旋转完成: {output_path}")


def pdf_crop(
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
) -> None:
    """裁剪 PDF 页面。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    left, top, right, bottom = margins

    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        for page_idx in indices:
            page = doc[page_idx]
            rect = page.rect
            new_rect = fitz.Rect(
                rect.x0 + left,
                rect.y0 + top,
                rect.x1 - right,
                rect.y1 - bottom,
            )
            page.set_cropbox(new_rect)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()
    print(f"裁剪完成: {output_path}")


def pdf_info(input_path: Path) -> None:
    """显示 PDF 信息。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        print(f"文件: {input_path}")
        print(f"页数: {doc.page_count}")
        meta = doc.metadata or {}
        print(f"标题: {meta.get('title', 'N/A')}")
        print(f"作者: {meta.get('author', 'N/A')}")
        print(f"创建日期: {meta.get('creationDate', 'N/A')}")
        print(f"修改日期: {meta.get('modDate', 'N/A')}")
        print(f"文件大小: {input_path.stat().st_size / 1024:.1f} KB")
    finally:
        doc.close()


def pdf_ocr(  # pragma: no cover - 需系统级 tesseract 可执行文件，测试环境不可用
    input_path: Path,
    output_path: Path = Path("ocr.pdf"),
    lang: str = "chi_sim+eng",
) -> None:
    """PDF OCR 识别。"""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:  # pragma: no cover - 仅在未安装 ocr extras 时触发
        print("未安装 OCR 相关库，请安装: pip install fcmd[ocr]")
        return

    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    new_doc = fitz.open()
    try:
        for page in doc:
            pix = page.get_pixmap()
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            try:
                ocr_text = pytesseract.image_to_string(img, lang=lang)
            finally:
                img.close()

            new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            new_page.insert_image(new_page.rect, pixmap=pix)
            text_rect = fitz.Rect(0, 0, page.rect.width, page.rect.height)
            new_page.insert_textbox(text_rect, str(ocr_text), fontname="china-ss", fontsize=11)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        new_doc.save(str(output_path))
    finally:
        new_doc.close()
        doc.close()
    print(f"OCR 识别完成: {output_path}")


def pdf_reorder(input_path: Path, output_path: Path, order: list[int]) -> None:
    """重排 PDF 页面顺序。"""
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    writer = pypdf.PdfWriter()

    for page_num in order:
        if 0 <= page_num < len(reader.pages):
            writer.add_page(reader.pages[page_num])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"重排完成: {output_path}")


def pdf_to_images(
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
) -> None:
    """PDF 转图片。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    output_dir.mkdir(parents=True, exist_ok=True)

    indices = _resolve_page_indices(pages, doc.page_count)
    if indices is None:
        doc.close()
        return

    try:
        for page_idx in indices:
            pix = doc[page_idx].get_pixmap(dpi=dpi)
            image_path = output_dir / f"{input_path.stem}_page_{page_idx + 1}.png"
            pix.save(str(image_path))
    finally:
        doc.close()
    print(f"转换完成: {output_dir}")


def pdf_repair(input_path: Path, output_path: Path = Path("repaired.pdf")) -> None:
    """修复 PDF 文件。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    print(f"修复完成: {output_path}")


# ---------------------------------------------------------------------- #
# pdftool DSL 动作
# ---------------------------------------------------------------------- #


@action(
    "pdftool_m",
    param_help={
        "input_paths": "输入 PDF 文件列表",
        "output_path": "输出文件路径（默认 merged.pdf）",
        "mode": "合并模式：concat（默认）或 interleave",
        "orders": "各文件页序（forward/reverse 或 f/r），按位置对应输入文件",
        "pages": "各文件页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_m(
    input_paths: list[Path],
    output_path: Path = Path("merged.pdf"),
    mode: Literal["concat", "interleave"] = "concat",
    orders: list[str] | None = None,
    pages: list[str] | None = None,
) -> None:
    """合并多个 PDF 文件（m 子命令）。"""
    pdf_merge(input_paths, output_path, mode, orders, pages)


@action(
    "pdftool_s",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_dir": "输出目录（默认 split）",
        "order": "页序：forward/f 正序（默认），reverse/r 倒序",
        "every": "固定步长，每份页数（默认 1 = 每页一份）",
        "groups": "自定义分组表达式（分号分隔的页码选择，如 1-2;3,4,5）",
    },
)
def pdftool_s(
    input_path: Path,
    output_dir: Path = Path("split"),
    order: str = "forward",
    every: int = 1,
    groups: str = "",
) -> None:
    """拆分 PDF 文件（s 子命令）。"""
    pdf_split(input_path, output_dir, order, every, groups)


@action(
    "pdftool_c",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 compressed.pdf）",
    },
)
def pdftool_c(input_path: Path, output_path: Path = Path("compressed.pdf")) -> None:
    """压缩 PDF 文件（c 子命令）。"""
    pdf_compress(input_path, output_path)


@action(
    "pdftool_e",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 encrypted.pdf）",
        "password": "密码（必填）",
    },
)
def pdftool_e(
    input_path: Path,
    output_path: Path = Path("encrypted.pdf"),
    password: str = "",
) -> None:
    """加密 PDF 文件（e 子命令）。"""
    pdf_encrypt(input_path, output_path, password)


@action(
    "pdftool_d",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 decrypted.pdf）",
        "password": "密码（必填）",
    },
)
def pdftool_d(
    input_path: Path,
    output_path: Path = Path("decrypted.pdf"),
    password: str = "",
) -> None:
    """解密 PDF 文件（d 子命令）。"""
    pdf_decrypt(input_path, output_path, password)


@action(
    "pdftool_xt",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 output.txt）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_xt(
    input_path: Path,
    output_path: Path = Path("output.txt"),
    pages: str = "",
) -> None:
    """提取 PDF 文本（xt 子命令）。"""
    pdf_extract_text(input_path, output_path, pages)


@action(
    "pdftool_xi",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_dir": "输出目录（默认 images）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_xi(
    input_path: Path,
    output_dir: Path = Path("images"),
    pages: str = "",
) -> None:
    """提取 PDF 图片（xi 子命令）。"""
    pdf_extract_images(input_path, output_dir, pages)


@action(
    "pdftool_w",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 watermarked.pdf）",
        "text": "水印文字（默认 CONFIDENTIAL）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_w(
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
) -> None:
    """添加 PDF 水印（w 子命令）。"""
    pdf_add_watermark(input_path, output_path, text, pages)


@action(
    "pdftool_r",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 rotated.pdf）",
        "rotation": "旋转角度（默认 90）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_r(
    input_path: Path,
    output_path: Path = Path("rotated.pdf"),
    rotation: int = 90,
    pages: str = "",
) -> None:
    """旋转 PDF 页面（r 子命令）。"""
    pdf_rotate(input_path, output_path, rotation, pages)


@action(
    "pdftool_crop",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 cropped.pdf）",
        "margins": "边距（左, 上, 右, 下），默认 (10, 10, 10, 10)",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_crop(
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
) -> None:
    """裁剪 PDF 页面（crop 子命令）。"""
    pdf_crop(input_path, output_path, margins, pages)


@action("pdftool_i", param_help={"input_path": "输入 PDF 文件"})
def pdftool_i(input_path: Path) -> None:
    """查看 PDF 信息（i 子命令）。"""
    pdf_info(input_path)


@action(
    "pdftool_ocr",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 ocr.pdf）",
        "lang": "识别语言（默认 chi_sim+eng）",
    },
)
def pdftool_ocr(  # pragma: no cover
    input_path: Path,
    output_path: Path = Path("ocr.pdf"),
    lang: str = "chi_sim+eng",
) -> None:
    """PDF OCR 识别（ocr 子命令）。"""
    pdf_ocr(input_path, output_path, lang)


@action(
    "pdftool_reorder",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径",
        "order": "页面顺序列表（0-based）",
    },
)
def pdftool_reorder(input_path: Path, output_path: Path, order: list[int]) -> None:
    """重排 PDF 页面（reorder 子命令）。"""
    pdf_reorder(input_path, output_path, order)


@action(
    "pdftool_img",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_dir": "输出目录（默认 images）",
        "dpi": "DPI（默认 300）",
        "pages": "页码筛选表达式（如 1-3,5；空/-/all 表示全选）",
    },
)
def pdftool_img(
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
) -> None:
    """PDF 转图片（img 子命令）。"""
    pdf_to_images(input_path, output_dir, dpi, pages)


@action(
    "pdftool_repair",
    param_help={
        "input_path": "输入 PDF 文件",
        "output_path": "输出文件路径（默认 repaired.pdf）",
    },
)
def pdftool_repair(input_path: Path, output_path: Path = Path("repaired.pdf")) -> None:
    """修复 PDF 文件（repair 子命令）。"""
    pdf_repair(input_path, output_path)
