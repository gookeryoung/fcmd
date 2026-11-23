"""``fcmd.infras.fs.search``（由 ``infras/fs.py`` 拆分）。"""

from __future__ import annotations

import fnmatch
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from fcmd.infras.filefilter import DEFAULT_IGNORE_SPEC, IgnoreSpec, should_skip_parts
from fcmd.infras.textio import read_text_lines

__all__ = ["compile_content_pattern", "iter_files", "search_by_content", "search_by_name"]


_MAX_LINES_PER_FILE = 100_000


class _WalkEntry(NamedTuple):
    """单条遍历结果（``path`` 为字符串，避免为未命中的条目构造 ``Path``）。"""

    path: str
    name: str
    is_dir: bool
    is_file: bool


def _walk(directory: Path, spec: IgnoreSpec) -> Iterator[_WalkEntry]:
    """深度优先遍历目录，命中忽略规则的目录整棵剪枝。

    与 ``Path.rglob("*")`` 的语义对齐：跟随符号链接判定条目类型
    （``is_dir`` / ``is_file``），但不递归进入符号链接目录；目录读取失败
    时跳过该子树（与 ``pathlib`` 的处理一致）。忽略规则按目录名逐级判定，
    因此剪枝与原「遍历后按全部路径组件过滤」结果相同。
    """
    if should_skip_parts(directory.parts, spec):
        return
    stack: list[str] = [str(directory)]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as scanner:
                entries = list(scanner)
        except OSError:
            continue
        for entry in entries:
            if should_skip_parts((entry.name,), spec):
                continue
            try:
                is_dir = entry.is_dir()
            except OSError:  # pragma: no cover - 目录项在遍历中被删除等竞态
                is_dir = False
            if is_dir:
                try:
                    is_link = entry.is_symlink()
                except OSError:  # pragma: no cover - 同上
                    is_link = False
                if not is_link:
                    stack.append(entry.path)
                yield _WalkEntry(entry.path, entry.name, True, False)
                continue
            try:
                is_file = entry.is_file()
            except OSError:  # pragma: no cover - 同上
                is_file = False
            yield _WalkEntry(entry.path, entry.name, False, is_file)


def iter_files(
    directory: Path,
    spec: IgnoreSpec = DEFAULT_IGNORE_SPEC,
    *,
    exclude_exts: frozenset[str] = frozenset(),
) -> Iterator[Path]:
    """遍历目录下的普通文件（跳过忽略规则与 ``exclude_exts`` 扩展名）。

    遍历顺序不保证；需要稳定顺序的调用方自行排序。
    """
    for entry in _walk(directory, spec):
        if entry.is_dir or not entry.is_file:
            continue
        path = Path(entry.path)
        if exclude_exts and path.suffix in exclude_exts:
            continue
        yield path


def _ensure_directory(directory: Path) -> None:
    """校验目录存在且为目录，不满足时抛对应异常。"""
    if not directory.exists():
        raise FileNotFoundError(f"目录不存在: {directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")


def search_by_name(
    directory: Path,
    pattern: str,
    include_dirs: bool = False,
    ignore_dirs: set[str] | None = None,
    *,
    case_sensitive: bool | None = None,
) -> list[Path]:
    """按文件名 glob 模式递归搜索目录；``case_sensitive`` 为 ``None`` 时沿用平台默认。"""
    _ensure_directory(directory)
    if ignore_dirs is None:
        spec = DEFAULT_IGNORE_SPEC
    else:
        spec = IgnoreSpec.from_iterable(ignore_dirs)

    def matches(name: str) -> bool:
        if case_sensitive is None:
            return fnmatch.fnmatch(name, pattern)
        if case_sensitive:
            return fnmatch.fnmatchcase(name, pattern)
        return fnmatch.fnmatchcase(name.casefold(), pattern.casefold())

    results: list[Path] = []
    for entry in _walk(directory, spec):
        if entry.is_dir:
            if include_dirs and matches(entry.name):
                results.append(Path(entry.path))
            continue
        if matches(entry.name):
            results.append(Path(entry.path))
    results.sort(key=str)
    return results


def compile_content_pattern(
    pattern: str,
    *,
    case_sensitive: bool | None = None,
    whole_word: bool = False,
) -> re.Pattern[str]:
    """把内容搜索模式编译为正则，统一大小写与整词语义。"""
    expression = pattern if not whole_word else rf"\b(?:{pattern})\b"
    return re.compile(expression, re.IGNORECASE if case_sensitive is False else 0)


def search_by_content(  # noqa: PLR0913 - 搜索开关由 CLI/GUI 直接透传
    directory: Path,
    pattern: str,
    extension: str = "",
    ignore_dirs: set[str] | None = None,
    *,
    case_sensitive: bool | None = None,
    whole_word: bool = False,
) -> list[tuple[Path, int, str]]:
    """按文件内容正则递归搜索；支持忽略大小写与整词匹配。"""
    _ensure_directory(directory)
    regex = compile_content_pattern(pattern, case_sensitive=case_sensitive, whole_word=whole_word)
    if ignore_dirs is None:
        spec = DEFAULT_IGNORE_SPEC
    else:
        spec = IgnoreSpec.from_iterable(ignore_dirs)
    results: list[tuple[Path, int, str]] = []
    for entry in _walk(directory, spec):
        if entry.is_dir or not entry.is_file:
            continue
        if extension and Path(entry.name).suffix != extension:
            continue
        path = Path(entry.path)
        try:
            lines = read_text_lines(path, limit=_MAX_LINES_PER_FILE)
        except ValueError:
            continue
        for idx, raw in enumerate(lines, start=1):
            if regex.search(raw):
                results.append((path, idx, raw.rstrip("\r\n")))
    # 遍历顺序与 rglob 不同，按路径排序还原「文件 → 行号」的稳定输出顺序
    results.sort(key=lambda item: str(item[0]))
    return results
