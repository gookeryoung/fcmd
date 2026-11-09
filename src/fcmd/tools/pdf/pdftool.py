"""pdftool - PDF 处理工具（15 子命令门面）。

可复用的 PDF 操作实现在 :mod:`fcmd.tools.pdf.pdfbase`（无工具注册副作用），
本模块只做子命令路由与参数转接。

示例
----
    fcmd pdftool i input.pdf
    fcmd pdftool m a.pdf b.pdf --output-path merged.pdf
    fcmd pdftool s input.pdf --output-dir split --every 2
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import fcmd
from fcmd.tools.pdf.pdfbase import (
    pdf_add_watermark,
    pdf_compress,
    pdf_crop,
    pdf_decrypt,
    pdf_encrypt,
    pdf_extract_images,
    pdf_extract_text,
    pdf_info,
    pdf_merge,
    pdf_ocr,
    pdf_reorder,
    pdf_repair,
    pdf_rotate,
    pdf_split,
    pdf_to_images,
)


# ---------------------------------------------------------------------- #
# CLI 子命令
# ---------------------------------------------------------------------- #
@fcmd.tool("pdftool", subcommand="m", help="合并 PDF")
def pdftool_m(
    input_paths: list[Path],
    output_path: Path = Path("merged.pdf"),
    mode: Literal["concat", "interleave"] = "concat",
    orders: list[str] | None = None,
    pages: list[str] | None = None,
) -> None:
    """合并多个 PDF 文件（m 子命令）。

    Parameters
    ----------
    input_paths:
        输入 PDF 文件列表
    output_path:
        输出文件路径（默认 merged.pdf）
    mode:
        合并模式：concat（默认）或 interleave
    orders:
        各文件页序（forward/reverse 或 f/r），按位置对应输入文件
    pages:
        各文件页码筛选表达式（如 1-3,5；空/-/all 表示全选）
    """
    pdf_merge(input_paths, output_path, mode, orders, pages)


@fcmd.tool("pdftool", subcommand="s", help="拆分 PDF")
def pdftool_s(
    input_path: Path,
    output_dir: Path = Path("split"),
    order: str = "forward",
    every: int = 1,
    groups: str = "",
) -> None:
    """拆分 PDF 文件（s 子命令）。

    Parameters
    ----------
    input_path:
        输入 PDF 文件
    output_dir:
        输出目录（默认 split）
    order:
        页序：forward/f 正序（默认），reverse/r 倒序
    every:
        固定步长，每份页数（默认 1 = 每页一份）
    groups:
        自定义分组表达式（分号分隔的页码选择，如 1-2;3,4,5）
    """
    pdf_split(input_path, output_dir, order, every, groups)


@fcmd.tool("pdftool", subcommand="c", help="压缩 PDF")
def pdftool_c(input_path: Path, output_path: Path = Path("compressed.pdf")) -> None:
    """压缩 PDF 文件（c 子命令）。"""
    pdf_compress(input_path, output_path)


@fcmd.tool("pdftool", subcommand="e", help="加密 PDF")
def pdftool_e(
    input_path: Path,
    output_path: Path = Path("encrypted.pdf"),
    password: str = "",
) -> None:
    """加密 PDF 文件（e 子命令）。"""
    pdf_encrypt(input_path, output_path, password)


@fcmd.tool("pdftool", subcommand="d", help="解密 PDF")
def pdftool_d(
    input_path: Path,
    output_path: Path = Path("decrypted.pdf"),
    password: str = "",
) -> None:
    """解密 PDF 文件（d 子命令）。"""
    pdf_decrypt(input_path, output_path, password)


@fcmd.tool("pdftool", subcommand="xt", help="提取文本")
def pdftool_xt(
    input_path: Path,
    output_path: Path = Path("output.txt"),
    pages: str = "",
) -> None:
    """提取 PDF 文本（xt 子命令）。

    Parameters
    ----------
    pages:
        页码筛选表达式（如 1-3,5；空/-/all 表示全选）
    """
    pdf_extract_text(input_path, output_path, pages)


@fcmd.tool("pdftool", subcommand="xi", help="提取图片")
def pdftool_xi(
    input_path: Path,
    output_dir: Path = Path("images"),
    pages: str = "",
) -> None:
    """提取 PDF 图片（xi 子命令）。

    Parameters
    ----------
    pages:
        页码筛选表达式（如 1-3,5；空/-/all 表示全选）
    """
    pdf_extract_images(input_path, output_dir, pages)


@fcmd.tool("pdftool", subcommand="w", help="添加水印")
def pdftool_w(
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
) -> None:
    """添加 PDF 水印（w 子命令）。"""
    pdf_add_watermark(input_path, output_path, text, pages)


@fcmd.tool("pdftool", subcommand="r", help="旋转 PDF")
def pdftool_r(
    input_path: Path,
    output_path: Path = Path("rotated.pdf"),
    rotation: int = 90,
    pages: str = "",
) -> None:
    """旋转 PDF 页面（r 子命令）。"""
    pdf_rotate(input_path, output_path, rotation, pages)


@fcmd.tool("pdftool", subcommand="crop", help="裁剪 PDF")
def pdftool_crop(
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
) -> None:
    """裁剪 PDF 页面（crop 子命令）。"""
    pdf_crop(input_path, output_path, margins, pages)


@fcmd.tool("pdftool", subcommand="i", help="查看 PDF 信息")
def pdftool_i(input_path: Path) -> None:
    """查看 PDF 信息（i 子命令）。"""
    pdf_info(input_path)


@fcmd.tool("pdftool", subcommand="ocr", help="PDF OCR 识别")
def pdftool_ocr(  # pragma: no cover
    input_path: Path,
    output_path: Path = Path("ocr.pdf"),
    lang: str = "chi_sim+eng",
) -> None:
    """PDF OCR 识别（ocr 子命令）。"""
    pdf_ocr(input_path, output_path, lang)


@fcmd.tool("pdftool", subcommand="reorder", help="重排 PDF 页面")
def pdftool_reorder(input_path: Path, output_path: Path, order: list[int]) -> None:
    """重排 PDF 页面（reorder 子命令）。"""
    pdf_reorder(input_path, output_path, order)


@fcmd.tool("pdftool", subcommand="img", help="PDF 转图片")
def pdftool_img(
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
) -> None:
    """PDF 转图片（img 子命令）。"""
    pdf_to_images(input_path, output_dir, dpi, pages)


@fcmd.tool("pdftool", subcommand="repair", help="修复 PDF")
def pdftool_repair(input_path: Path, output_path: Path = Path("repaired.pdf")) -> None:
    """修复 PDF 文件（repair 子命令）。"""
    pdf_repair(input_path, output_path)


@fcmd.main("pdftool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
