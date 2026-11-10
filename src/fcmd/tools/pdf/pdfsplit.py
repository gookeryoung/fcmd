"""pdfsplit - PDF 分割 / 页面提取。

提供三种分割模式（对应 bitool pdfsplit 的 ``-n`` / ``-s`` / ``-r``）：

* 按份数：平均分成 N 份（余数优先分配给前面的份）。
* 每份页数：按固定页数切分。
* 页码范围：提取指定页组成新 PDF，支持 ``1,3-5,7-`` 语法。

CLI 与 Tk 窗口版（:mod:`fcmd.tools.pdf.pdfsplitw`）共用本模块的纯函数。

示例
----
    fcmd pdfsplit report.pdf --parts 3 --output-dir out
    fcmd pdfsplit report.pdf --size 10
    fcmd pdfsplit report.pdf --pages 1,3-5 --output-path picked.pdf
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import fcmd
from fcmd.tools._deps import missing_dependency

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "DEFAULT_OUTPUT_DIR",
    "parse_page_ranges",
    "split_by_parts",
    "split_by_range",
    "split_by_size",
]

HAS_PYPDF = importlib.util.find_spec("pypdf") is not None

# pypdf 惰性占位（_require_pypdf 首次调用填充）
pypdf: Any = None

DEFAULT_OUTPUT_DIR: Final[Path] = Path()


def _require_pypdf() -> bool:
    """pypdf 未安装时返回 ``False``；已安装则惰性导入到模块全局。"""
    if not HAS_PYPDF:
        return missing_dependency("pypdf", "pdf")
    global pypdf  # noqa: PLW0603 - 惰性导入需 global 注入模块，避免工具发现时加载
    if pypdf is None:
        import pypdf as _pypdf

        pypdf = _pypdf
    return True


def parse_page_ranges(spec: str, total_pages: int) -> list[int]:
    """解析 1-based 页码范围，返回升序去重的有效页号。

    支持 ``N`` / ``N-M`` / ``N-``（到末页）/ ``-M``（从首页）以及逗号分隔的
    多个区间，如 ``"1,3-5,7-"``。

    Raises
    ------
    ValueError
        表达式为空、格式非法或区间起点大于终点。
    """
    if total_pages < 1:
        raise ValueError("PDF 没有可分割的页面")
    tokens = [token.strip() for token in spec.split(",") if token.strip()]
    if not tokens:
        raise ValueError("页码范围不能为空")

    pages: set[int] = set()
    for token in tokens:
        if "-" in token:
            start_text, _, end_text = token.partition("-")
            start = _parse_page_number(start_text, token) if start_text.strip() else 1
            end = _parse_page_number(end_text, token) if end_text.strip() else total_pages
            if start > end:
                raise ValueError(f"页码范围起点大于终点: {token!r}")
            pages.update(range(max(start, 1), min(end, total_pages) + 1))
        else:
            page = _parse_page_number(token, token)
            if 1 <= page <= total_pages:
                pages.add(page)
    return sorted(pages)


def _parse_page_number(text: str, token: str) -> int:
    """把文本转为正整数页号，失败或非正数时抛出含原始 token 的 ValueError。"""
    try:
        value = int(text)
    except ValueError as exc:
        raise ValueError(f"无效的页码表达式: {token!r}") from exc
    if value < 1:
        raise ValueError(f"页码必须为正整数: {token!r}")
    return value


def _parts(page_count: int, parts: int) -> list[int]:
    """把总页数平均分配给 ``parts`` 份，返回每份页数（余数优先给前面）。"""
    if parts < 1:
        raise ValueError(f"分割份数必须为正整数: {parts}")
    if parts > page_count:
        raise ValueError(f"分割份数 {parts} 不能超过总页数 {page_count}")
    base, remainder = divmod(page_count, parts)
    return [base + (1 if index < remainder else 0) for index in range(parts)]


def _write_pages(reader: Any, indices: Sequence[int], output_path: Path) -> None:
    """把指定 0-based 页索引写入新 PDF。"""
    writer = pypdf.PdfWriter()
    for index in indices:
        writer.add_page(reader.pages[index])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as stream:
        writer.write(stream)


def _part_paths(output_dir: Path, base_name: str, count: int) -> list[Path]:
    """生成 ``<base>_part1.pdf`` 形式的输出路径。"""
    return [output_dir / f"{base_name}_part{index}.pdf" for index in range(1, count + 1)]


def split_by_parts(
    input_path: Path,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    parts: int = 2,
    *,
    base_name: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[Path]:
    """把 PDF 平均分成 ``parts`` 份，返回输出文件列表。

    Raises
    ------
    ValueError
        份数非法或超过总页数。
    RuntimeError
        pypdf 未安装。
    OSError
        读写失败。
    """
    if not _require_pypdf():
        raise RuntimeError("缺少依赖: pypdf（pip install fcmd[pdf]）")

    reader = pypdf.PdfReader(str(input_path))
    page_count = len(reader.pages)
    sizes = _parts(page_count, parts)
    outputs = _part_paths(output_dir, base_name or input_path.stem, len(sizes))

    start = 0
    for index, (size, target) in enumerate(zip(sizes, outputs, strict=True), start=1):
        _write_pages(reader, range(start, start + size), target)
        if progress is not None:
            progress(index, len(outputs), f"已生成 {target.name}（{size} 页）")
        start += size
    return outputs


def split_by_size(
    input_path: Path,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    size: int = 1,
    *,
    base_name: str | None = None,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[Path]:
    """按每份 ``size`` 页切分 PDF，返回输出文件列表。

    Raises
    ------
    ValueError
        每份页数不是正整数。
    RuntimeError
        pypdf 未安装。
    OSError
        读写失败。
    """
    if size < 1:
        raise ValueError(f"每份页数必须为正整数: {size}")
    if not _require_pypdf():
        raise RuntimeError("缺少依赖: pypdf（pip install fcmd[pdf]）")

    reader = pypdf.PdfReader(str(input_path))
    page_count = len(reader.pages)
    chunks = (page_count + size - 1) // size
    outputs = _part_paths(output_dir, base_name or input_path.stem, chunks)

    for index, target in enumerate(outputs, start=1):
        start = (index - 1) * size
        end = min(start + size, page_count)
        _write_pages(reader, range(start, end), target)
        if progress is not None:
            progress(index, len(outputs), f"已生成 {target.name}（{end - start} 页）")
    return outputs


def split_by_range(
    input_path: Path,
    output_path: Path,
    pages: str,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """提取指定页码组成新 PDF，返回输出路径。

    Raises
    ------
    ValueError
        页码表达式非法或未命中任何有效页。
    RuntimeError
        pypdf 未安装。
    OSError
        读写失败。
    """
    if not _require_pypdf():
        raise RuntimeError("缺少依赖: pypdf（pip install fcmd[pdf]）")

    reader = pypdf.PdfReader(str(input_path))
    selected = parse_page_ranges(pages, len(reader.pages))
    if not selected:
        raise ValueError(f"指定范围未命中有效页面: {pages!r}")

    if progress is not None:
        progress(0, 1, f"正在提取第 {', '.join(map(str, selected))} 页")
    _write_pages(reader, [page - 1 for page in selected], output_path)
    if progress is not None:
        progress(1, 1, f"已生成 {output_path.name}（{len(selected)} 页）")
    return output_path


# ---------------------------------------------------------------------- #
# CLI
# ---------------------------------------------------------------------- #
def _resolve_base(input_path: Path, output_path: Path | None, output_dir: Path) -> tuple[Path, str]:
    """解析按份数 / 每份页数模式的输出目录与文件名前缀。"""
    if output_path is not None:
        return output_path.parent, output_path.stem
    return output_dir, input_path.stem


@fcmd.tool("pdfsplit", help="分割 PDF（按份数 / 每份页数 / 页码范围）")
def pdfsplit(  # noqa: PLR0913, PLR0917 - 参数与 CLI 一一对应，保持显式
    input_path: Path,
    parts: int | None = None,
    size: int | None = None,
    pages: str = "",
    output_path: Path | None = None,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
) -> None:
    """分割 PDF 文件（三种模式任选其一）。

    Parameters
    ----------
    input_path:
        输入 PDF 文件。
    parts:
        按份数平均分割（如 ``--parts 3``）。
    size:
        按每份页数分割（如 ``--size 10``）。
    pages:
        页码范围提取（如 ``--pages 1,3-5,7-``）。
    output_path:
        输出文件路径；按份数 / 每份页数模式下作为文件名前缀与输出目录，
        页码范围模式下为完整输出路径（默认 ``<原名>_extracted.pdf``）。
    output_dir:
        输出目录（默认当前目录）。
    """
    if not input_path.is_file():
        print(f"错误: 输入文件不存在: {input_path}")
        return

    selected = [parts is not None, size is not None, bool(pages.strip())]
    if sum(selected) != 1:
        print("错误: 请且仅指定 --parts / --size / --pages 之一")
        return

    try:
        if parts is not None:
            directory, base_name = _resolve_base(input_path, output_path, output_dir)
            outputs = split_by_parts(input_path, directory, parts, base_name=base_name)
            print(f"分割完成: {len(outputs)} 份 -> {directory}")
        elif size is not None:
            directory, base_name = _resolve_base(input_path, output_path, output_dir)
            outputs = split_by_size(input_path, directory, size, base_name=base_name)
            print(f"分割完成: {len(outputs)} 份 -> {directory}")
        else:
            target = output_path or output_dir / f"{input_path.stem}_extracted.pdf"
            split_by_range(input_path, target, pages)
            print(f"提取完成: {target}")
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"错误: {exc}")
        return


@fcmd.main("pdfsplit")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
