"""文件与路径基础操作：递归检索、路径解析、批量重命名与文件名标记。

本模块只做计算与文件系统操作，**不打印**任何内容；逐条目的过程反馈以
字符串列表返回，由 :mod:`fcmd.tools.system` 下的命令行外壳统一输出。
"""

from __future__ import annotations

import fnmatch
import re
import time
from pathlib import Path
from typing import Any

from fcmd.infras.filefilter import DEFAULT_IGNORE_SPEC, IgnoreSpec, should_skip_parts
from fcmd.infras.textio import read_text_lines

__all__ = [
    "add_date_prefixes",
    "clear_date_prefixes",
    "compile_content_pattern",
    "file_timestamp",
    "normalize_path",
    "path_diff",
    "path_parts",
    "relative_to",
    "rename_case",
    "rename_insert",
    "rename_replace",
    "search_by_content",
    "search_by_name",
    "set_file_level",
    "strip_date_prefix",
]


# ============================================================================ #
# filesearch
# ============================================================================ #
# 内容搜索单文件最大读取行数，避免大文件内存爆炸
_MAX_LINES_PER_FILE = 100_000


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
    for path in directory.rglob("*"):
        if should_skip_parts(path.parts, spec):
            continue
        if path.is_dir():
            if include_dirs and matches(path.name):
                results.append(path)
            continue
        if matches(path.name):
            results.append(path)
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
    for path in sorted(directory.rglob("*")):
        if not path.is_file():
            continue
        if should_skip_parts(path.parts, spec):
            continue
        if extension and path.suffix != extension:
            continue
        try:
            lines = read_text_lines(path)
        except ValueError:
            continue
        for idx, raw in enumerate(lines[:_MAX_LINES_PER_FILE], start=1):
            if regex.search(raw):
                results.append((path, idx, raw.rstrip("\r\n")))
    return results


# ============================================================================ #
# pathtool
# ============================================================================ #
def normalize_path(path: Path) -> Path:
    """规范化路径：展开用户目录、解析为绝对路径、消除 ``..``/``.``。"""
    return path.expanduser().resolve(strict=False)


def relative_to(path: Path, base: Path) -> Path:
    """计算 ``path`` 相对 ``base`` 的相对路径。"""
    p1 = normalize_path(path)
    p2 = normalize_path(base)
    return p1.relative_to(p2)


def path_parts(path: Path) -> dict[str, Any]:
    """提取路径各部分信息。"""
    p = normalize_path(path)
    return {
        "input": str(path),
        "absolute": str(p),
        "anchor": p.anchor,
        "parent": str(p.parent),
        "name": p.name,
        "stem": p.stem,
        "suffix": p.suffix,
        "suffixes": p.suffixes,
        "parts": list(p.parts),
    }


def path_diff(p1: Path, p2: Path) -> tuple[list[str], list[str], list[str]]:
    """比较两路径的组件差异，返回 ``(common, only_p1, only_p2)``。"""
    parts1 = list(normalize_path(p1).parts)
    parts2 = list(normalize_path(p2).parts)
    common: list[str] = []
    for a, b in zip(parts1, parts2, strict=False):
        if a != b:
            break
        common.append(a)
    only_p1 = parts1[len(common) :]
    only_p2 = parts2[len(common) :]
    return (common, only_p1, only_p2)


# ============================================================================ #
# filedate
# ============================================================================ #
def file_timestamp(filepath: Path) -> str:
    """获取文件时间戳（取修改时间与创建时间的较大值），``YYYYMMDD`` 格式。"""
    stat = filepath.stat()
    return time.strftime("%Y%m%d", time.localtime(max(stat.st_mtime, stat.st_ctime)))


# 日期前缀正则：匹配 19xx/20xx 开头的 YYYYMMDD（允许分隔符 -_#.~）
_DATE_PATTERN = re.compile(r"(20|19)\d{2}[-_#.~]?((0[1-9])|(1[012]))[-_#.~]?((0[1-9])|([12]\d)|(3[01]))[-_#.~]?")
_DATE_SEP = "_"


def strip_date_prefix(filepath: Path) -> tuple[Path, bool]:
    """移除文件名主干中的日期前缀，返回 ``(新路径, 是否发生重命名)``。"""
    stem = filepath.stem
    new_stem = _DATE_PATTERN.sub("", stem)
    if new_stem == stem:
        return filepath, False
    new_path = filepath.with_name(new_stem + filepath.suffix)
    filepath.rename(new_path)
    return new_path, True


def add_date_prefixes(files: list[Path]) -> list[str]:
    """为文件名添加/更新日期前缀（先清后加避免重复），返回提示消息。"""
    messages: list[str] = []
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            stripped, removed = strip_date_prefix(filepath)
            if not removed:
                messages.append(f"{filepath} 无日期前缀")
            timestamp = file_timestamp(stripped)
            target = stripped.with_name(f"{timestamp}{_DATE_SEP}{stripped.stem}{stripped.suffix}")
            stripped.rename(target)
    return messages


def clear_date_prefixes(files: list[Path]) -> list[str]:
    """清除文件名中的日期前缀（缺失文件与点文件跳过），返回提示消息。"""
    messages: list[str] = []
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            _new_path, removed = strip_date_prefix(filepath)
            if not removed:
                messages.append(f"{filepath} 无日期前缀")
    return messages


# ============================================================================ #
# filerename
# ============================================================================ #
def _safe_rename(filepath: Path, new_stem: str, preview: bool) -> str | None:
    """安全重命名文件名主干（保留扩展名），返回需要输出的过程反馈。

    目标名与原名相同时静默跳过；目标已存在且非同一文件时跳过并提示
    （大小写不敏感文件系统上仅大小写不同视为同一文件允许重命名）。
    """
    if new_stem == filepath.stem:
        return None
    target = filepath.with_name(new_stem + filepath.suffix)
    if target.exists() and target.resolve() != filepath.resolve():
        return f"跳过（目标已存在）: {filepath.name} -> {target.name}"
    if preview:
        return f"[预览] {filepath.name} -> {target.name}"
    filepath.rename(target)
    return f"重命名: {filepath.name} -> {target.name}"


def rename_replace(files: list[Path], pattern: str, replacement: str = "", preview: bool = False) -> list[str]:
    """正则替换文件名主干中的匹配部分（保留扩展名，仅匹配时执行）。"""
    compiled = re.compile(pattern)  # 语法错误 → 任务失败汇总 + 退出码 1
    messages: list[str] = []
    for filepath in files:
        if not filepath.exists():
            messages.append(f"文件不存在: {filepath}")
            continue
        if not compiled.search(filepath.stem):
            continue
        message = _safe_rename(filepath, compiled.sub(replacement, filepath.stem), preview)
        if message is not None:
            messages.append(message)
    return messages


def rename_insert(files: list[Path], text: str, position: int = 0, preview: bool = False) -> list[str]:
    """在文件名主干指定位置插入文本（保留扩展名）。"""
    messages: list[str] = []
    for filepath in files:
        if not filepath.exists():
            messages.append(f"文件不存在: {filepath}")
            continue
        if not text:
            continue
        stem = filepath.stem
        pos = max(0, min(position, len(stem)))
        message = _safe_rename(filepath, stem[:pos] + text + stem[pos:], preview)
        if message is not None:
            messages.append(message)
    return messages


def rename_case(files: list[Path], mode: str = "lower", preview: bool = False) -> list[str]:
    """转换文件名主干大小写（保留扩展名）。"""
    mode_map = {"lower": str.lower, "upper": str.upper, "title": str.title}
    if mode not in mode_map:
        raise ValueError(f"不支持的大小写模式: {mode}（可选: lower/upper/title）")
    messages: list[str] = []
    for filepath in files:
        if not filepath.exists():
            messages.append(f"文件不存在: {filepath}")
            continue
        message = _safe_rename(filepath, mode_map[mode](filepath.stem), preview)
        if message is not None:
            messages.append(message)
    return messages


# ============================================================================ #
# filelevel
# ============================================================================ #
# 等级标记映射：0 表示清除等级，1-4 对应不同等级标记
_LEVELS: dict[str, str] = {"0": "", "1": "PUB,NOR", "2": "INT", "3": "CON", "4": "CLA"}
# 左右括号集合：标记两侧的括号字符（用于识别并整体移除）
_LEVEL_BRACKETS: tuple[str, str] = (" ([_(【-", " )]_）】")


def _remove_level_marks(stem: str, marks: list[str]) -> str:
    """从文件名主干中移除所有被括号包裹的标记（裸字符串形式保留）。"""
    left_brackets, right_brackets = _LEVEL_BRACKETS
    for mark in marks:
        pos = 0
        while True:
            pos = stem.find(mark, pos)
            if pos == -1:
                break
            b, e = pos - 1, pos + len(mark)
            if b >= 0 and e < len(stem) and stem[b] in left_brackets and stem[e] in right_brackets:
                stem = stem[:b] + stem[e + 1 :]
            else:
                pos = e
    return stem


def _process_file_level(filepath: Path, level: int) -> str | None:
    """单文件等级标记处理：先清除所有已有标记，再按 ``level`` 添加新标记。"""
    filestem = filepath.stem
    original_stem = filestem
    for level_names in _LEVELS.values():
        if level_names:
            filestem = _remove_level_marks(filestem, level_names.split(","))
    for digit in map(str, range(1, 10)):
        filestem = _remove_level_marks(filestem, [digit])
    if level > 0:
        levelstr = _LEVELS[str(level)].split(",")[0]
        if levelstr:
            filestem = f"{filestem}({levelstr})"
    if filestem != original_stem:
        new_path = filepath.with_name(filestem + filepath.suffix)
        filepath.rename(new_path)
        return f"重命名: {filepath} -> {new_path}"
    return None


def set_file_level(files: list[Path], level: int = 0) -> list[str]:
    """为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA），返回过程反馈。"""
    if not (0 <= level < len(_LEVELS)):
        raise ValueError(f"无效的等级 {level}，必须在 0 和 {len(_LEVELS) - 1} 之间")
    messages: list[str] = []
    for filepath in files:
        if not filepath.exists():
            messages.append(f"文件不存在: {filepath}")
            continue
        message = _process_file_level(filepath, level)
        if message is not None:
            messages.append(message)
    return messages
