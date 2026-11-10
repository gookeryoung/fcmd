"""pdfbase - PDF 处理公共逻辑（无工具注册副作用）。

供 ``pdftool`` 子命令门面、窗口版工具及外部代码复用：依赖守卫、页码解析
与 15 个 PDF 操作函数都在此定义，本模块不注册任何 ``@fcmd.tool``。

外部库按需延迟导入——导入本模块不触发重型 PDF 库加载；执行期 import
失败时打印友好提示并返回，退出码语义由调用方决定。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from fcmd.tools._deps import missing_dependency
from fcmd.tools.pdf.models import MergeSpec, PageSelection, SplitSpec

if TYPE_CHECKING:
    import fitz  # PyMuPDF
    import pypdf

__all__ = [
    "HAS_PYMUPDF",
    "HAS_PYPDF",
    "pdf_add_watermark",
    "pdf_compress",
    "pdf_crop",
    "pdf_decrypt",
    "pdf_encrypt",
    "pdf_extract_images",
    "pdf_extract_text",
    "pdf_info",
    "pdf_merge",
    "pdf_ocr",
    "pdf_reorder",
    "pdf_repair",
    "pdf_rotate",
    "pdf_split",
    "pdf_to_images",
]

# 可选依赖检查（find_spec 仅查定位，不触发真正 import；执行期惰性导入到模块全局）
HAS_PYMUPDF = importlib.util.find_spec("fitz") is not None
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


def _require_pymupdf() -> bool:
    """PyMuPDF 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYMUPDF:
        return missing_dependency("PyMuPDF", "pdf")
    global fitz  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    import fitz

    return True


def _require_pypdf() -> bool:
    """pypdf 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYPDF:
        return missing_dependency("pypdf", "pdf")
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
# 公共函数
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
