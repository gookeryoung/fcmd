"""pdftool - PDF 文件工具集。

提供 PDF 合并/拆分/压缩/加密/解密/提取文本/提取图片/水印/旋转/裁剪/
信息/OCR/转图片/重排/修复 等子命令。

合并支持交叉合并（多文件轮流取页）与逐文件页序/页码筛选；拆分支持
正序/倒序、固定步长（每份页数）与自定义分组（如 ``1-2;3,4,5``）。
页码表达式语法见 :mod:`fcmd.cli._pdf_models`。

依赖 ``fcmd[pdf]`` extra 中的 ``pymupdf`` 与 ``pypdf``；
OCR 子命令额外依赖 ``fcmd[ocr]`` extra 中的 ``pytesseract``。

示例
----
    fcmd pdftool m a.pdf b.pdf -o merged.pdf
    fcmd pdftool m 01.pdf 02.pdf --mode interleave --orders f,r
    fcmd pdftool s in.pdf -o split/ --every 2 --order reverse
    fcmd pdftool s in.pdf --groups "1-2;3,4,5"
    fcmd pdftool xt in.pdf -o out.txt --pages 1-3,5
    fcmd pdftool i in.pdf
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import fcmd

from ._pdf_models import MergeSpec, PageSelection, SplitSpec

__all__ = [
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

if TYPE_CHECKING:
    import fitz  # PyMuPDF
    import pypdf

# 用 find_spec 检查依赖是否安装（不触发实际导入，避免工具发现时加载重型 PDF 库）
HAS_PYMUPDF = importlib.util.find_spec("fitz") is not None
HAS_PYPDF = importlib.util.find_spec("pypdf") is not None


def _require_pymupdf() -> bool:
    """PyMuPDF 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYMUPDF:
        print("未安装 PyMuPDF 库，请安装: pip install fcmd[pdf]")
        return False
    global fitz  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载 55ms 的 fitz
    import fitz

    return True


def _require_pypdf() -> bool:
    """pypdf 未安装时打印提示，返回是否可用；已安装则惰性导入到模块全局。"""
    if not HAS_PYPDF:
        print("未安装 pypdf 库，请安装: pip install fcmd[pdf]")
        return False
    global pypdf  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载 pypdf
    import pypdf

    return True


def _resolve_page_indices(pages: str, page_count: int) -> tuple[int, ...] | None:
    """解析页码筛选表达式为 0-based 页索引序列。

    表达式为空/``-``/``all`` 时返回全部页；解析失败时打印错误并返回
    ``None``（调用方据此提前返回）。

    Parameters
    ----------
    pages:
        页码筛选表达式（如 ``1-3,5``，语法见 :mod:`fcmd.cli._pdf_models`）
    page_count:
        总页数，用于越界过滤
    """
    try:
        return PageSelection.parse(pages).resolve(page_count)
    except ValueError as exc:
        print(f"错误: {exc}")
        return None


@fcmd.tool("pdftool", subcommand="m", help="合并 PDF")
def pdf_merge(
    input_paths: list[Path],
    output_path: Path = Path("merged.pdf"),
    mode: Literal["concat", "interleave"] = "concat",
    orders: list[str] | None = None,
    pages: list[str] | None = None,
) -> None:
    """合并多个 PDF 文件。

    支持顺序拼接与交叉合并（``--mode interleave``，多文件轮流取页）；
    每个文件可按位置独立指定页序（``--orders``）与页码筛选（``--pages``）。
    示例：01.pdf 正序 + 02.pdf 倒序交叉合并::

        fcmd pdftool m 01.pdf 02.pdf --mode interleave --orders f,r

    Parameters
    ----------
    input_paths:
        输入 PDF 文件列表（不存在的文件跳过）
    output_path:
        输出文件路径（默认: ``merged.pdf``）
    mode:
        合并模式：``concat`` 顺序拼接（默认）/ ``interleave`` 交叉合并
    orders:
        各文件页序（forward/reverse 或 f/r），按位置对应输入文件，缺省正序
    pages:
        各文件页码筛选表达式（如 ``1-3,5``；空/``-``/``all`` 表示全选）
    """
    if not _require_pypdf():
        return

    # 存在性过滤须与 orders/pages 按位置同步对齐
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


@fcmd.tool("pdftool", subcommand="s", help="拆分 PDF")
def pdf_split(
    input_path: Path,
    output_dir: Path = Path("split"),
    order: str = "forward",
    every: int = 1,
    groups: str = "",
) -> None:
    """拆分 PDF 文件。

    支持正序/倒序（``--order``）、固定步长（``--every``，每份页数）与
    自定义分组（``--groups``，分号分隔多组页码，如 ``"1-2;3,4,5"`` 表示
    第一份含页 1-2、第二份含页 3、4、5；非空时优先于 ``--every``）。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_dir:
        输出目录（默认: ``split``）
    order:
        页序：forward/f 正序（默认），reverse/r 倒序
    every:
        固定步长，每份页数（默认 1 = 每页一份）
    groups:
        自定义分组表达式（分号分隔的页码选择，如 ``"1-2;3,4,5"``）
    """
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
        # 单页份沿用 _page_N 命名（与既有行为兼容），多页份用 _part_N
        suffix = "page" if len(chunk) == 1 else "part"
        output_file = output_dir / f"{input_path.stem}_{suffix}_{seq}.pdf"
        with output_file.open("wb") as f:
            writer.write(f)

    print(f"拆分完成: {output_dir} (共 {len(chunks)} 份)")


@fcmd.tool("pdftool", subcommand="c", help="压缩 PDF")
def pdf_compress(input_path: Path, output_path: Path = Path("compressed.pdf")) -> None:
    """压缩 PDF 文件。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``compressed.pdf``）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()

    original_size = input_path.stat().st_size
    new_size = output_path.stat().st_size
    ratio = (1 - new_size / original_size) * 100 if original_size > 0 else 0.0
    print(f"压缩完成: {output_path} (缩小 {ratio:.1f}%)")


@fcmd.tool("pdftool", subcommand="e", help="加密 PDF")
def pdf_encrypt(
    input_path: Path,
    output_path: Path = Path("encrypted.pdf"),
    password: str = "",
) -> None:
    """加密 PDF 文件。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``encrypted.pdf``）
    password:
        密码（必填）
    """
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


@fcmd.tool("pdftool", subcommand="d", help="解密 PDF")
def pdf_decrypt(
    input_path: Path,
    output_path: Path = Path("decrypted.pdf"),
    password: str = "",
) -> None:
    """解密 PDF 文件。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``decrypted.pdf``）
    password:
        密码（必填）
    """
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


@fcmd.tool("pdftool", subcommand="xt", help="提取文本")
def pdf_extract_text(
    input_path: Path,
    output_path: Path = Path("output.txt"),
    pages: str = "",
) -> None:
    """提取 PDF 文本。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``output.txt``）
    pages:
        页码筛选表达式（如 ``1-3,5``；空/``-``/``all`` 表示全选）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
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


@fcmd.tool("pdftool", subcommand="xi", help="提取图片")
def pdf_extract_images(
    input_path: Path,
    output_dir: Path = Path("images"),
    pages: str = "",
) -> None:
    """提取 PDF 图片。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_dir:
        输出目录（默认: ``images``）
    pages:
        页码筛选表达式（如 ``1-3,5``；空/``-``/``all`` 表示全选）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
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


@fcmd.tool("pdftool", subcommand="w", help="添加水印")
def pdf_add_watermark(
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
) -> None:
    """添加 PDF 水印。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``watermarked.pdf``）
    text:
        水印文字（默认: ``CONFIDENTIAL``）
    pages:
        页码筛选表达式（如 ``1-3,5``；空/``-``/``all`` 表示全选）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
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
            # fitz insert_text 的 rotate 仅接受 0/90/180/270，用 0 输出水平水印
            page.insert_text((x, y), text, fontsize=48, rotate=0, color=(0, 0, 0))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path))
    finally:
        doc.close()
    print(f"水印添加完成: {output_path}")


@fcmd.tool("pdftool", subcommand="r", help="旋转 PDF")
def pdf_rotate(
    input_path: Path,
    output_path: Path = Path("rotated.pdf"),
    rotation: int = 90,
    pages: str = "",
) -> None:
    """旋转 PDF 页面。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``rotated.pdf``）
    rotation:
        旋转角度（默认: 90）
    pages:
        页码筛选表达式（如 ``1-3,5``；空/``-``/``all`` 表示全选）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
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


@fcmd.tool("pdftool", subcommand="crop", help="裁剪 PDF")
def pdf_crop(
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
) -> None:
    """裁剪 PDF 页面。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``cropped.pdf``）
    margins:
        边距（左, 上, 右, 下），默认 ``(10, 10, 10, 10)``
    pages:
        页码筛选表达式（如 ``1-3,5``；空/``-``/``all`` 表示全选）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
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


@fcmd.tool("pdftool", subcommand="i", help="查看 PDF 信息")
def pdf_info(input_path: Path) -> None:
    """显示 PDF 信息。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
    try:
        print(f"文件: {input_path}")
        print(f"页数: {doc.page_count}")
        print(f"标题: {doc.metadata.get('title', 'N/A')}")
        print(f"作者: {doc.metadata.get('author', 'N/A')}")
        print(f"创建日期: {doc.metadata.get('creationDate', 'N/A')}")
        print(f"修改日期: {doc.metadata.get('modDate', 'N/A')}")
        print(f"文件大小: {input_path.stat().st_size / 1024:.1f} KB")
    finally:
        doc.close()


@fcmd.tool("pdftool", subcommand="ocr", help="PDF OCR 识别")
def pdf_ocr(  # pragma: no cover - 需系统级 tesseract 可执行文件，测试环境不可用
    input_path: Path,
    output_path: Path = Path("ocr.pdf"),
    lang: str = "chi_sim+eng",
) -> None:
    """PDF OCR 识别。

    需要额外安装 ``fcmd[ocr]`` extra（包含 ``pytesseract`` 与 ``pillow``），
    以及系统级 ``tesseract`` 可执行文件。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``ocr.pdf``）
    lang:
        识别语言（默认: ``chi_sim+eng``）
    """
    try:
        import pytesseract
        from PIL import Image
    except ImportError:  # pragma: no cover - 仅在未安装 ocr extras 时触发
        print("未安装 OCR 相关库，请安装: pip install fcmd[ocr]")
        return

    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
    new_doc = fitz.open()  # pyrefly: ignore [missing-attribute]
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
            new_page.insert_textbox(text_rect, ocr_text, fontname="china-ss", fontsize=11)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        new_doc.save(str(output_path))
    finally:
        new_doc.close()
        doc.close()
    print(f"OCR 识别完成: {output_path}")


@fcmd.tool("pdftool", subcommand="reorder", help="重排 PDF 页面")
def pdf_reorder(input_path: Path, output_path: Path, order: list[int]) -> None:
    """重排 PDF 页面顺序。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径
    order:
        页面顺序列表（0-based）
    """
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


@fcmd.tool("pdftool", subcommand="img", help="PDF 转图片")
def pdf_to_images(
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
) -> None:
    """PDF 转图片。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_dir:
        输出目录（默认: ``images``）
    dpi:
        DPI（默认: 300）
    pages:
        页码筛选表达式（如 ``1-3,5``；空/``-``/``all`` 表示全选）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
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


@fcmd.tool("pdftool", subcommand="repair", help="修复 PDF")
def pdf_repair(input_path: Path, output_path: Path = Path("repaired.pdf")) -> None:
    """修复 PDF 文件。

    通过重新保存并清理冗余对象尝试修复损坏的 PDF。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_path:
        输出文件路径（默认: ``repaired.pdf``）
    """
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))  # pyrefly: ignore [missing-attribute]
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(output_path), garbage=4, deflate=True, clean=True)
    finally:
        doc.close()
    print(f"修复完成: {output_path}")


@fcmd.main("pdftool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
