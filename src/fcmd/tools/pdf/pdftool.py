"""pdftool - PDF 处理工具（18 子命令门面）。

可复用的 PDF 操作实现在 :mod:`fcmd.tools.pdf.pdfbase`（无工具注册副作用），
本模块只做子命令路由与参数转接。

示例
----
    fcmd pdftool i input.pdf
    fcmd pdftool m a.pdf b.pdf --output-path merged.pdf
    fcmd pdftool s input.pdf --output-dir split --every 2
    fcmd pdftool s docs/ --output-dir split --recursive --silent
    fcmd pdftool attach add input.pdf --files a.txt
    fcmd pdftool compare a.pdf b.pdf --output-path diff.pdf
    fcmd pdftool layout input.pdf --page-size a4 --orientation landscape
"""

from __future__ import annotations

from functools import partial
from pathlib import Path
from typing import Literal

import fcmd
from fcmd.tools.pdf._batch import expand_pdf_inputs, has_outputs, invoke, run_dir_batch, run_file_batch
from fcmd.tools.pdf.pdfbase import (
    pdf_add_watermark,
    pdf_attach_add,
    pdf_attach_extract,
    pdf_compare,
    pdf_compress,
    pdf_crop,
    pdf_decrypt,
    pdf_encrypt,
    pdf_extract_images,
    pdf_extract_text,
    pdf_info,
    pdf_layout,
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
def pdftool_s(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_dir: Path = Path("split"),
    order: str = "forward",
    every: int = 1,
    groups: str = "",
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """拆分 PDF 文件（s 子命令）；输入为目录时每个 PDF 输出到独立子目录。

    Parameters
    ----------
    input_path:
        输入 PDF 文件或目录
    output_dir:
        输出目录（默认 split）
    order:
        页序：forward/f 正序（默认），reverse/r 倒序
    every:
        固定步长，每份页数（默认 1 = 每页一份）
    groups:
        自定义分组表达式（分号分隔的页码选择，如 1-2;3,4,5）
    recursive:
        输入为目录时递归子目录
    skip_existing:
        输出子目录已存在时跳过
    silent:
        不输出逐文件日志
    """
    action = partial(pdf_split, order=order, every=every, groups=groups)
    if input_path.is_dir():
        run_dir_batch(
            input_path,
            output_dir,
            action,
            recursive=recursive,
            skip_existing=skip_existing,
            silent=silent,
        )
        return
    if skip_existing and has_outputs(output_dir, f"{input_path.stem}_"):
        if not silent:
            print(f"跳过已存在: {output_dir}")
        return
    invoke(action, silent, input_path, output_dir)


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
def pdftool_xt(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_path: Path | None = None,
    pages: str = "",
    output_dir: Path | None = None,
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """提取 PDF 文本（xt 子命令）；输入为目录时批量提取。

    Parameters
    ----------
    output_path:
        输出文件（仅单文件模式；默认 output.txt）
    pages:
        页码筛选表达式（如 1-3,5；空/-/all 表示全选）
    output_dir:
        批处理输出目录（默认与源文件同目录）
    recursive:
        输入为目录时递归子目录
    skip_existing:
        目标文件已存在时跳过
    silent:
        不输出逐文件日志
    """

    def target_for(src: Path) -> Path:
        if input_path.is_dir():
            return (output_dir or src.parent) / f"{src.stem}.txt"
        if output_path is not None:
            return output_path
        return Path("output.txt")

    run_file_batch(
        input_path,
        target_for,
        partial(pdf_extract_text, pages=pages),
        recursive=recursive,
        skip_existing=skip_existing,
        silent=silent,
    )


@fcmd.tool("pdftool", subcommand="xi", help="提取图片")
def pdftool_xi(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_dir: Path = Path("images"),
    pages: str = "",
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """提取 PDF 图片（xi 子命令）；输入为目录时每个 PDF 输出到独立子目录。

    Parameters
    ----------
    pages:
        页码筛选表达式（如 1-3,5；空/-/all 表示全选）
    recursive:
        输入为目录时递归子目录
    skip_existing:
        输出子目录已存在时跳过
    silent:
        不输出逐文件日志
    """
    action = partial(pdf_extract_images, pages=pages)
    if input_path.is_dir():
        run_dir_batch(
            input_path,
            output_dir,
            action,
            recursive=recursive,
            skip_existing=skip_existing,
            silent=silent,
        )
        return
    if skip_existing and has_outputs(output_dir, "page_"):
        if not silent:
            print(f"跳过已存在: {output_dir}")
        return
    invoke(action, silent, input_path, output_dir)


@fcmd.tool("pdftool", subcommand="w", help="添加水印")
def pdftool_w(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_path: Path = Path("watermarked.pdf"),
    text: str = "CONFIDENTIAL",
    pages: str = "",
    image: Path | None = None,
    font_size: int = 48,
    rotation: int = 0,
    scale: float = 0.5,
) -> None:
    """添加 PDF 水印（w 子命令）。

    Parameters
    ----------
    image:
        图片水印路径；指定后忽略 text
    font_size:
        文本水印字号（默认 48）
    rotation:
        水印旋转角度，需为 90 的倍数
    scale:
        图片水印占页面宽/高的比例（0-1，默认 0.5）
    """
    pdf_add_watermark(input_path, output_path, text, pages, image, font_size, rotation, scale)


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
def pdftool_crop(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_path: Path = Path("cropped.pdf"),
    margins: tuple[int, int, int, int] = (10, 10, 10, 10),
    pages: str = "",
    auto: bool = False,
    margin: int = 10,
) -> None:
    """裁剪 PDF 页面（crop 子命令）。

    Parameters
    ----------
    auto:
        自动识别文本/图片内容边界后裁剪
    margin:
        自动裁剪时的外扩边距（点，默认 10）
    """
    pdf_crop(input_path, output_path, margins, pages, auto, margin)


@fcmd.tool("pdftool", subcommand="i", help="查看 PDF 信息")
def pdftool_i(input_path: Path, verbose: bool = False) -> None:
    """查看 PDF 信息（i 子命令）。

    Parameters
    ----------
    verbose:
        输出 PDF 版本、加密状态、是否线性化等详细统计
    """
    pdf_info(input_path, verbose)


@fcmd.tool("pdftool", subcommand="ocr", help="PDF OCR 识别")
def pdftool_ocr(  # noqa: PLR0913, PLR0917  # pragma: no cover - 需系统级 tesseract
    input_path: Path,
    output_path: Path | None = None,
    lang: str = "chi_sim+eng",
    output_dir: Path | None = None,
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """PDF OCR 识别（ocr 子命令）；输入为目录时批量识别。"""

    def target_for(src: Path) -> Path:
        if input_path.is_dir():
            return (output_dir or src.parent) / f"{src.stem}_ocr.pdf"
        if output_path is not None:
            return output_path
        return Path("ocr.pdf")

    run_file_batch(
        input_path,
        target_for,
        partial(pdf_ocr, lang=lang),
        recursive=recursive,
        skip_existing=skip_existing,
        silent=silent,
    )


@fcmd.tool("pdftool", subcommand="reorder", help="重排 PDF 页面")
def pdftool_reorder(
    input_path: Path,
    output_path: Path,
    order: list[str],
    delete: str = "",
    insert_blank: str = "",
) -> None:
    """重排 PDF 页面（reorder 子命令）。

    Parameters
    ----------
    order:
        新页序（0 基索引，空格分隔）；传 all 表示保持原顺序
    delete:
        要删除的页（1 基表达式，如 1,3-5）
    insert_blank:
        插入空白页，格式 位置:数量（位置 1 基，多组用逗号分隔）
    """
    pdf_reorder(input_path, output_path, order, delete, insert_blank)


@fcmd.tool("pdftool", subcommand="img", help="PDF 转图片")
def pdftool_img(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_dir: Path = Path("images"),
    dpi: int = 300,
    pages: str = "",
    image_format: Literal["png", "jpg", "jpeg"] = "png",
    quality: int = 90,
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """PDF 转图片（img 子命令）；输入为目录时每个 PDF 输出到独立子目录。

    Parameters
    ----------
    image_format:
        输出格式：png（默认）/jpg/jpeg
    quality:
        JPEG 质量（1-100，默认 90；PNG 忽略）
    recursive:
        输入为目录时递归子目录
    skip_existing:
        输出子目录已存在时跳过
    silent:
        不输出逐文件日志
    """
    action = partial(
        pdf_to_images,
        dpi=dpi,
        pages=pages,
        image_format=image_format,
        quality=quality,
    )
    if input_path.is_dir():
        run_dir_batch(
            input_path,
            output_dir,
            action,
            recursive=recursive,
            skip_existing=skip_existing,
            silent=silent,
        )
        return
    if skip_existing and has_outputs(output_dir, f"{input_path.stem}_"):
        if not silent:
            print(f"跳过已存在: {output_dir}")
        return
    invoke(action, silent, input_path, output_dir)


@fcmd.tool("pdftool", subcommand="repair", help="修复 PDF")
def pdftool_repair(input_path: Path, output_path: Path = Path("repaired.pdf")) -> None:
    """修复 PDF 文件（repair 子命令）。"""
    pdf_repair(input_path, output_path)


@fcmd.tool("pdftool", subcommand="attach", help="管理 PDF 附件")
def pdftool_attach(
    action: Literal["add", "extract"],
    input_path: Path,
    output_path: Path | None = None,
    files: list[Path] | None = None,
    output_dir: Path = Path("attachments"),
) -> None:
    """管理 PDF 附件（attach 子命令）。

    Parameters
    ----------
    action:
        操作类型：add 添加附件，extract 提取附件
    input_path:
        输入 PDF 文件
    output_path:
        添加附件时的输出路径（默认 <输入名>.attached.pdf）
    files:
        要嵌入的附件文件列表（add 必填）
    output_dir:
        提取附件的目标目录（默认 attachments）
    """
    if action == "add":
        target = output_path or input_path.with_suffix(".attached.pdf")
        pdf_attach_add(input_path, target, files or [])
    else:
        pdf_attach_extract(input_path, output_dir)


def _compare_dir_batch(  # noqa: PLR0913, PLR0917 - 批处理选项显式传递
    input_path1: Path,
    input_path2: Path,
    output_dir: Path | None,
    recursive: bool,
    skip_existing: bool,
    silent: bool,
) -> None:
    """按相对路径同名配对两个目录中的 PDF 并批量比较。"""
    counterparts = {p.relative_to(input_path2): p for p in expand_pdf_inputs(input_path2, recursive)}
    sources = expand_pdf_inputs(input_path1, recursive)
    if not sources:
        print(f"错误: 目录中没有 PDF 文件: {input_path1}")
        return

    done = 0
    for src1 in sources:
        rel = src1.relative_to(input_path1)
        src2 = counterparts.get(rel)
        if src2 is None:
            if not silent:
                print(f"警告: 第二个目录缺少对应文件: {rel}")
            continue
        base = output_dir / rel.parent if output_dir is not None else src1.parent
        target = base / f"{src1.stem}.diff.pdf"
        if skip_existing and target.exists():
            if not silent:
                print(f"跳过已存在: {target}")
            continue
        invoke(pdf_compare, silent, src1, src2, target)
        done += 1

    if len(sources) > 1 and not silent:
        print(f"批处理完成: {done}/{len(sources)} 个文件")


@fcmd.tool("pdftool", subcommand="compare", help="比较两个 PDF")
def pdftool_compare(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path1: Path,
    input_path2: Path,
    output_path: Path | None = None,
    output_dir: Path | None = None,
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """比较两个 PDF 并输出差异高亮文件（compare 子命令）。

    两个输入均为目录时按相对路径同名配对批量比较。

    Parameters
    ----------
    input_path1:
        基准 PDF 文件或目录
    input_path2:
        比较 PDF 文件或目录（差异高亮版以它为基准输出）
    output_path:
        输出路径（单文件模式；默认 <基准名>.diff.pdf）
    output_dir:
        批量比较输出目录（默认与基准文件同目录）
    recursive:
        输入为目录时递归子目录
    skip_existing:
        目标文件已存在时跳过
    silent:
        不输出逐文件日志
    """
    if input_path1.is_dir() or input_path2.is_dir():
        if not (input_path1.is_dir() and input_path2.is_dir()):
            print("错误: 批量比较时两个输入都必须是目录")
            return
        _compare_dir_batch(input_path1, input_path2, output_dir, recursive, skip_existing, silent)
        return

    if output_dir is not None:
        target = output_dir / f"{input_path1.stem}.diff.pdf"
    else:
        target = output_path or input_path1.with_suffix(".diff.pdf")
    if skip_existing and target.exists():
        if not silent:
            print(f"跳过已存在: {target}")
        return
    invoke(pdf_compare, silent, input_path1, input_path2, target)


@fcmd.tool("pdftool", subcommand="layout", help="调整 PDF 页面布局")
def pdftool_layout(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    output_path: Path | None = None,
    page_size: Literal["a3", "a4", "a5", "legal", "letter"] | None = None,
    orientation: Literal["landscape", "portrait"] | None = None,
    margin_left: int = 0,
    margin_right: int = 0,
    margin_top: int = 0,
    margin_bottom: int = 0,
    output_dir: Path | None = None,
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """调整 PDF 页面尺寸、方向与页边距（layout 子命令）。

    Parameters
    ----------
    page_size:
        目标页面尺寸：a3/a4/a5/legal/letter（默认保持原尺寸）
    orientation:
        页面方向：portrait 纵向、landscape 横向（默认保持原方向）
    margin_left:
        左边距（点，1 点 = 1/72 英寸）
    margin_right:
        右边距（点）
    margin_top:
        顶部边距（点）
    margin_bottom:
        底部边距（点）
    output_dir:
        批处理输出目录（默认与源文件同目录）
    recursive:
        输入为目录时递归子目录
    skip_existing:
        目标文件已存在时跳过
    silent:
        不输出逐文件日志
    """

    def target_for(src: Path) -> Path:
        if input_path.is_dir():
            return (output_dir or src.parent) / f"{src.stem}.layout.pdf"
        if output_path is not None:
            return output_path
        return input_path.with_suffix(".layout.pdf")

    run_file_batch(
        input_path,
        target_for,
        partial(
            pdf_layout,
            page_size=page_size,
            orientation=orientation,
            margin_left=margin_left,
            margin_right=margin_right,
            margin_top=margin_top,
            margin_bottom=margin_bottom,
        ),
        recursive=recursive,
        skip_existing=skip_existing,
        silent=silent,
    )


@fcmd.main("pdftool")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
