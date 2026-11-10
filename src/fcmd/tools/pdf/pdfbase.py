"""pdfbase - PDF 处理公共逻辑（无工具注册副作用）。

供 ``pdftool`` 子命令门面、窗口版工具及外部代码复用：依赖守卫、页码解析
与 19 个 PDF 操作函数都在此定义，本模块不注册任何 ``@fcmd.tool``。

外部库按需延迟导入——导入本模块不触发重型 PDF 库加载；执行期 import
失败时打印友好提示并返回，退出码语义由调用方决定。
"""

from __future__ import annotations

import importlib.util
from collections.abc import Sequence
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
    "pdf_attach_add",
    "pdf_attach_extract",
    "pdf_compare",
    "pdf_compress",
    "pdf_crop",
    "pdf_decrypt",
    "pdf_encrypt",
    "pdf_extract_images",
    "pdf_extract_text",
    "pdf_info",
    "pdf_layout",
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


def pdf_add_watermark(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
    image: Path | None = None,
    font_size: int = 48,
    rotation: int = 0,
    scale: float = 0.5,
) -> None:
    """添加文本或图片水印（提供 ``image`` 时使用图片水印）。"""
    if rotation % 90 != 0:
        print("错误: --rotation 必须为 90 的倍数")
        return
    if not 0 < scale <= 1:
        print("错误: --scale 取值范围为 (0, 1]")
        return
    if image is not None and not image.is_file():
        print(f"错误: 水印图片不存在: {image}")
        return
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    pixmap = None
    if image is not None:
        try:
            pixmap = fitz.Pixmap(str(image))
        except Exception:
            print(f"错误: 无法读取水印图片: {image}")
            doc.close()
            return

    try:
        indices = _resolve_page_indices(pages, doc.page_count)
        if indices is None:
            return
        for page_idx in indices:
            page = doc[page_idx]
            rect = page.rect
            if pixmap is None:
                text_width = fitz.get_text_length(text, fontsize=font_size)
                x = (rect.width - text_width) / 2
                y = rect.height / 2
                page.insert_text(
                    (x, y),
                    text,
                    fontsize=font_size,
                    rotate=rotation,
                    color=(0, 0, 0),
                )
                continue

            target_width = rect.width * scale
            target_height = target_width * pixmap.height / pixmap.width
            if target_height > rect.height * scale:
                target_height = rect.height * scale
                target_width = target_height * pixmap.width / pixmap.height
            if rotation in (90, 270):
                target_width, target_height = target_height, target_width
            x0 = (rect.width - target_width) / 2
            y0 = (rect.height - target_height) / 2
            target = fitz.Rect(x0, y0, x0 + target_width, y0 + target_height)
            page.insert_image(target, filename=str(image), rotate=rotation, overlay=True)

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


def _auto_crop_rect(page: fitz.Page, margin: int) -> fitz.Rect:
    """按文本/图片块边界计算自动裁剪区域；无内容时返回原页面。"""
    rect = page.rect
    boxes = page.get_text("blocks")
    if not boxes:
        return rect
    x0 = max(rect.x0, min(b[0] for b in boxes) - margin)
    y0 = max(rect.y0, min(b[1] for b in boxes) - margin)
    x1 = min(rect.x1, max(b[2] for b in boxes) + margin)
    y1 = min(rect.y1, max(b[3] for b in boxes) + margin)
    return fitz.Rect(x0, y0, x1, y1)


def pdf_crop(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
    auto: bool = False,
    margin: int = 10,
) -> None:
    """裁剪 PDF 页面。

    ``auto=True`` 时按文本/图片内容边界自动裁剪，``margin`` 为外扩边距（点）。
    """
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
            if auto:
                new_rect = _auto_crop_rect(page, margin)
            else:
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


def _parse_reorder_selection(order: list[str], total: int) -> list[int]:
    """把 reorder 参数解析为页索引列表；all/keep 表示原顺序，越界与非法值跳过。"""
    selected: list[int] = []
    for token in (str(item).strip().lower() for item in order):
        if token in {"all", "keep"}:
            return list(range(total))
        try:
            page_num = int(token)
        except ValueError:
            continue
        if 0 <= page_num < total:
            selected.append(page_num)
    return selected


def _apply_blank_insertions(items: list[int | None], insert_blank: str) -> bool:
    """按 ``位置:数量`` 表达式插入空白页占位；格式非法时打印错误并返回 False。"""
    for spec in insert_blank.split(","):
        clean = spec.strip()
        if not clean:
            continue
        pos_text, sep, count_text = clean.partition(":")
        try:
            pos = int(pos_text)
            count = int(count_text) if sep else 1
        except ValueError:
            print(f"错误: 无效的插入空白页格式 {clean!r}（应为 位置:数量）")
            return False
        if count < 1 or not 1 <= pos <= len(items) + 1:
            print(f"错误: 插入空白页位置或数量不合法: {clean!r}")
            return False
        for _ in range(count):
            items.insert(pos - 1, None)
            pos += 1
    return True


def pdf_reorder(
    input_path: Path,
    output_path: Path,
    order: list[str],
    delete: str = "",
    insert_blank: str = "",
) -> None:
    """重排 PDF 页面，支持删除页面与插入空白页。

    ``order`` 为新页序（0 基索引），传 ``all`` 表示保持原顺序；``delete``
    为 1 基页码表达式；``insert_blank`` 格式为 ``位置:数量``（位置 1 基）。
    """
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    total = len(reader.pages)
    selected = _parse_reorder_selection(order, total)

    if delete:
        removed = _resolve_page_indices(delete, total)
        if removed is None:
            return
        removed_set = set(removed)
        selected = [page_num for page_num in selected if page_num not in removed_set]

    items: list[int | None] = list(selected)
    blank_width, blank_height = 595.0, 842.0
    if insert_blank:
        first = reader.pages[selected[0]] if selected else None
        if first is not None:
            blank_width = float(first.mediabox.width)
            blank_height = float(first.mediabox.height)
        if not _apply_blank_insertions(items, insert_blank):
            return

    writer = pypdf.PdfWriter()
    for item in items:
        if item is None:
            writer.add_blank_page(width=blank_width, height=blank_height)
        else:
            writer.add_page(reader.pages[item])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        writer.write(f)

    print(f"重排完成: {output_path} ({len(items)} 页)")


def pdf_to_images(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
    image_format: str = "png",
    quality: int = 90,
) -> None:
    """PDF 转图片。

    ``image_format`` 支持 png/jpg/jpeg，``quality`` 仅对 JPEG 生效。
    """
    if not _require_pymupdf():
        return

    fmt = image_format.lower().lstrip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in {"png", "jpg"}:
        print(f"错误: 不支持的图片格式 {image_format!r}（可选 png/jpg/jpeg）")
        return
    if not 1 <= quality <= 100:
        print("错误: --quality 取值范围为 1-100")
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
            image_path = output_dir / f"{input_path.stem}_page_{page_idx + 1}.{fmt}"
            pix.save(str(image_path), jpg_quality=quality)
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
# 附件管理
# ---------------------------------------------------------------------- #
def pdf_attach_add(input_path: Path, output_path: Path, files: Sequence[Path]) -> None:
    """向 PDF 嵌入附件文件（保留原有附件）。"""
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    writer = pypdf.PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    for name, payloads in reader.attachments.items():
        for data in payloads:
            writer.add_attachment(name, data)

    added = 0
    for item in files:
        path = Path(item)
        if not path.is_file():
            print(f"警告: 附件不存在，已跳过: {path}")
            continue
        writer.add_attachment(path.name, path.read_bytes())
        added += 1

    if added == 0:
        print("错误: 没有可添加的附件文件")
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as fh:
        writer.write(fh)
    print(f"附件添加完成: {output_path} (新增 {added} 个)")


def pdf_attach_extract(input_path: Path, output_dir: Path = Path("attachments")) -> None:
    """提取 PDF 内嵌附件到目录。"""
    if not _require_pypdf():
        return

    reader = pypdf.PdfReader(str(input_path))
    attachments = reader.attachments
    if not attachments:
        print("PDF 中没有附件")
        return

    output_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for name, payloads in attachments.items():
        # 附件名来自 PDF 内容，仅保留文件名部分，避免越出输出目录
        safe_name = Path(name).name or "attachment"
        multiple = len(payloads) > 1
        for index, data in enumerate(payloads, start=1):
            target = output_dir / (f"{index}_{safe_name}" if multiple else safe_name)
            target.write_bytes(data)
            count += 1
    print(f"附件提取完成: {output_dir} (共 {count} 个文件)")


# ---------------------------------------------------------------------- #
# 差异比较与页面布局
# ---------------------------------------------------------------------- #
def pdf_compare(input_path1: Path, input_path2: Path, output_path: Path) -> None:
    """比较两个 PDF 并输出差异高亮文件（以第二个文件为基准）。

    逐页比对文本；仅在文本不同的页面上标出第二个文件中新增的单词，
    第二个文件多出的页面原样保留。
    """
    if not _require_pymupdf():
        return

    doc1 = fitz.open(str(input_path1))
    doc2 = fitz.open(str(input_path2))
    diff = fitz.open()
    extra_pages = doc1.page_count - doc2.page_count
    changed = 0
    try:
        for page_idx in range(doc2.page_count):
            diff.insert_pdf(doc2, from_page=page_idx, to_page=page_idx)

            text1 = doc1[page_idx].get_text() if page_idx < doc1.page_count else ""
            page2 = doc2[page_idx]
            if text1 == page2.get_text():
                continue

            changed += 1
            words1 = {w[4] for w in doc1[page_idx].get_text("words")} if page_idx < doc1.page_count else set()
            target = diff[len(diff) - 1]
            for word in page2.get_text("words"):
                if word[4] not in words1:
                    target.add_highlight_annot(fitz.Rect(word[:4]))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        diff.save(str(output_path))
    finally:
        diff.close()
        doc1.close()
        doc2.close()

    print(f"比较完成: {output_path} (差异页 {changed} 页)")
    if extra_pages > 0:
        print(f"提示: 第一个文件比第二个多 {extra_pages} 页，未纳入输出")


# 常用页面尺寸（点）：1 点 = 1/72 英寸
_PAGE_SIZES: dict[str, tuple[int, int]] = {
    "a3": (842, 1190),
    "a4": (595, 842),
    "a5": (420, 595),
    "legal": (612, 1008),
    "letter": (612, 792),
}


def pdf_layout(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_path: Path = Path("layout.pdf"),
    page_size: str | None = None,
    orientation: str | None = None,
    margin_left: int = 0,
    margin_right: int = 0,
    margin_top: int = 0,
    margin_bottom: int = 0,
) -> None:
    """调整 PDF 页面尺寸、方向与页边距。"""
    if not _require_pymupdf():
        return

    doc = fitz.open(str(input_path))
    result = fitz.open()
    try:
        for page in doc:
            width, height = _PAGE_SIZES.get(page_size or "", (page.rect.width, page.rect.height))
            if orientation == "landscape":
                width, height = height, width

            box = fitz.Rect(margin_left, margin_top, width - margin_right, height - margin_bottom)
            if box.width <= 0 or box.height <= 0:
                print("错误: 边距超过页面尺寸，无法排版")
                return

            result.new_page(width=width, height=height).show_pdf_page(box, doc, page.number)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        result.save(str(output_path))
    finally:
        result.close()
        doc.close()
    print(f"布局调整完成: {output_path}")
