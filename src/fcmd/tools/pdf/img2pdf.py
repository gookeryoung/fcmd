"""img2pdf - 图片合并为 PDF。

支持图片文件与目录输入（目录按文件名排序）、页面尺寸
（auto/A3/A4/A5/B5/letter/legal）、DPI 与 EXIF 方向校正。纯 Python
（Pillow），无子进程调用；输出默认 ``output.pdf``。

示例
----
    fcmd img2pdf cover.png photos/ --output-path book.pdf --page-size A4
    img2pdf cover.png photos/ --output-path book.pdf
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "IMG2PDF_DEFAULT_DPI",
    "IMG2PDF_IMAGE_EXTENSIONS",
    "IMG2PDF_MAX_DPI",
    "IMG2PDF_MIN_DPI",
    "IMG2PDF_PAGE_SIZES",
    "IMG2PDF_PAGE_SIZE_CHOICES",
    "collect_pdf_images",
    "images_to_pdf",
    "normalize_dpi",
    "normalize_page_size",
    "resolve_pdf_output",
]

HAS_PIL = importlib.util.find_spec("PIL") is not None

# PIL 惰性占位（_require_pil 首次调用填充）
Image: Any = None

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

_IMAGE_MAGIC_NUMBERS: tuple[bytes, ...] = (
    b"\xff\xd8\xff",  # JPEG
    b"\x89PNG\r\n\x1a\n",  # PNG
    b"GIF87a",  # GIF87a
    b"GIF89a",  # GIF89a
    b"BM",  # BMP
    b"II*\x00",  # TIFF (little-endian)
    b"MM\x00*",  # TIFF (big-endian)
)

# ICO 文件头（Pillow 可读，但不在 _IMAGE_MAGIC_NUMBERS 中）。
_ICO_MAGIC = b"\x00\x00\x01\x00"


def _missing_dependency(package: str, extra: str) -> bool:
    """打印可选依赖缺失提示并返回 False（供 ``_require_*`` 统一返回）。"""
    print(f"未安装 {package} 库，请安装: pip install fcmd[{extra}]")
    return False


def _require_pil() -> bool:
    """Pillow 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PIL:
        return _missing_dependency("Pillow", "img")
    global Image  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if Image is None:
        from PIL import Image as _image

        Image = _image
    return True


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
    方向校正图片。CLI 与 Tk GUI 共用此函数，保证两条入口结果一致。

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


@fcmd.tool("img2pdf", help="图片合并为 PDF（支持文件/目录、页面尺寸与 DPI 设置）")
def img2pdf(
    images: list[Path],
    output_path: Path = Path("output.pdf"),
    page_size: str = "auto",
    dpi: int = IMG2PDF_DEFAULT_DPI,
    normalize: bool = True,
) -> None:
    """图片合并为 PDF（支持文件/目录输入、页面尺寸与 DPI 设置）。

    Parameters
    ----------
    images:
        输入图片文件或目录（可多个；目录按文件名排序）
    output_path:
        输出 PDF 文件路径（默认 output.pdf）
    page_size:
        页面尺寸：auto / A4 / A3 / A5 / B5 / letter / legal（默认 auto）
    dpi:
        页面 DPI（36-1200，默认 150）
    normalize:
        自动校正 EXIF 方向（默认开启）
    """
    if not _require_pil():
        return

    try:
        output = images_to_pdf(images, output_path, page_size=page_size, dpi=dpi, normalize=normalize)
    except (OSError, ValueError, RuntimeError) as error:
        print(f"错误: {error}")
        return
    print(f"PDF 已生成: {output}")
    print(f"页面尺寸: {normalize_page_size(page_size)}；DPI: {normalize_dpi(dpi)}")


@fcmd.main("img2pdf")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
