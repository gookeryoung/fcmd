"""``fcmd.infras.fs.rename_plan``（由 ``infras/fs.py`` 拆分）。"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from pathlib import Path

__all__ = ["CASE_MODES", "apply_case", "split_name", "two_phase_rename"]


CASE_MODES: tuple[str, ...] = ("keep", "lower", "upper", "title", "sentence")


_TEMP_PREFIX = ".fcmd-rename-"


def split_name(name: str, is_dir: bool = False) -> tuple[str, str]:
    """拆成不含点的名称与扩展名；目录视为没有扩展名。"""
    if is_dir:
        return name, ""
    index = name.rfind(".")
    if index <= 0:
        return name, ""
    return name[:index], name[index + 1 :]


def apply_case(text: str, mode: str) -> str:
    """大小写转换；``sentence`` 只把首字母转大写，未知模式抛 ``ValueError``。"""
    if mode == "keep":
        return text
    if mode == "lower":
        return text.lower()
    if mode == "upper":
        return text.upper()
    if mode == "title":
        return text.title()
    if mode == "sentence":
        return text[:1].upper() + text[1:]
    raise ValueError(f"未知的大小写模式：{mode}")


def two_phase_rename(pairs: list[tuple[Path, Path]]) -> tuple[list[tuple[Path, Path]], list[tuple[Path, str]]]:
    """两阶段改名：先全部改临时名，再改目标名；失败时尽力回滚。

    Returns
    -------
    (completed, errors)
        已完成的 ``(source, target)`` 列表，以及 ``(path, 错误说明)`` 列表；
        调用方负责把错误转成自己的报告类型。
    """
    completed: list[tuple[Path, Path]] = []
    errors: list[tuple[Path, str]] = []
    prepared: list[tuple[Path, Path, Path]] = []
    token = uuid.uuid4().hex[:8]
    for index, (source, target) in enumerate(pairs):
        if not source.exists() and not source.is_symlink():
            errors.append((source, "源路径不存在"))
            errors.extend(_rollback(prepared, ()))
            return completed, errors
        temp = source.with_name(f"{_TEMP_PREFIX}{token}-{index}")
        try:
            source.rename(temp)
        except OSError as exc:
            errors.append((source, f"移动到临时名失败：{exc}"))
            errors.extend(_rollback(prepared, ()))
            return completed, errors
        prepared.append((source, target, temp))
    finalized: list[tuple[Path, Path, Path]] = []
    for source, target, temp in prepared:
        try:
            temp.rename(target)
        except OSError as exc:
            errors.append((source, f"重命名失败：{exc}"))
            errors.extend(_rollback(prepared, finalized))
            return completed, errors
        finalized.append((source, target, temp))
        completed.append((source, target))
    return completed, errors


def _rollback(
    prepared: Sequence[tuple[Path, Path, Path]],
    finalized: Sequence[tuple[Path, Path, Path]],
) -> list[tuple[Path, str]]:
    """回滚：已到目标名的先退回临时名，再把所有临时名退回原名。"""
    errors: list[tuple[Path, str]] = []
    for _source, target, temp in reversed(finalized):
        try:
            target.rename(temp)
        except OSError as exc:
            errors.append((target, f"回滚到临时名失败：{exc}"))
    for source, _target, temp in prepared:
        if not (temp.exists() or temp.is_symlink()):
            continue
        try:
            temp.rename(source)
        except OSError as exc:
            errors.append((source, f"回滚原名失败：{exc}"))
    return errors
