"""pdftool 批处理辅助：目录展开、跳过已存在与静默执行。

批处理只在 CLI 门面层生效，pdfbase 中的单文件实现保持不变。
"""

from __future__ import annotations

import contextlib
import io
from collections.abc import Callable, Iterator
from pathlib import Path


def expand_pdf_inputs(input_path: Path, recursive: bool = False) -> list[Path]:
    """输入为目录时展开其中的 PDF 文件；为文件时原样返回。"""
    if not input_path.is_dir():
        return [input_path]
    candidates: Iterator[Path] = input_path.rglob("*") if recursive else input_path.iterdir()
    return sorted(p for p in candidates if p.is_file() and p.suffix.lower() == ".pdf")


def has_outputs(output_dir: Path, prefix: str) -> bool:
    """判断目录中是否已有以 ``prefix`` 开头的输出文件。"""
    if not output_dir.is_dir():
        return False
    return any(p.is_file() for p in output_dir.glob(f"{prefix}*"))


def invoke(action: Callable[..., None], silent: bool, *args: object) -> None:
    """执行 action；silent 时吞掉其标准输出。"""
    if not silent:
        action(*args)
        return
    with contextlib.redirect_stdout(io.StringIO()):
        action(*args)


def run_file_batch(  # noqa: PLR0913 - 批处理选项显式传递，便于门面复用
    input_path: Path,
    target_for: Callable[[Path], Path],
    action: Callable[[Path, Path], None],
    *,
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """逐 PDF 执行单文件输出任务。"""
    files = expand_pdf_inputs(input_path, recursive)
    if not files:
        print(f"错误: 目录中没有 PDF 文件: {input_path}")
        return

    done = 0
    for src in files:
        target = target_for(src)
        if skip_existing and target.exists():
            if not silent:
                print(f"跳过已存在: {target}")
            continue
        invoke(action, silent, src, target)
        done += 1

    if len(files) > 1 and not silent:
        print(f"批处理完成: {done}/{len(files)} 个文件")


def run_dir_batch(  # noqa: PLR0913 - 批处理选项显式传递，便于门面复用
    input_path: Path,
    output_dir: Path,
    action: Callable[[Path, Path], None],
    *,
    recursive: bool = False,
    skip_existing: bool = False,
    silent: bool = False,
) -> None:
    """逐 PDF 执行目录型输出任务，每个 PDF 使用独立子目录。"""
    files = expand_pdf_inputs(input_path, recursive)
    if not files:
        print(f"错误: 目录中没有 PDF 文件: {input_path}")
        return

    done = 0
    for src in files:
        target_dir = output_dir / src.stem
        if skip_existing and has_outputs(target_dir, ""):
            if not silent:
                print(f"跳过已存在: {target_dir}")
            continue
        invoke(action, silent, src, target_dir)
        done += 1

    if len(files) > 1 and not silent:
        print(f"批处理完成: {done}/{len(files)} 个文件")
