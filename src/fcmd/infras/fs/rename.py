"""``fcmd.infras.fs.rename``（由 ``infras/fs.py`` 拆分）。"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["rename_case", "rename_insert", "rename_replace"]


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
