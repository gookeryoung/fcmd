"""cmderw 的纯逻辑补充：回收站删除、通配选择、快速过滤、目录统计。

下划线前缀使其不参与 :mod:`fcmd` 的工具发现；窗口层 ``cmderw.py`` 只做 Tk
装配，这里保持无 UI 依赖、可单测。口径见 ADR 0005 的 D1–D3。
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "BulkRenameOptions",
    "RenameItem",
    "RenamePlan",
    "RenameReport",
    "RenameSource",
    "TrashReport",
    "apply_rename_plan",
    "build_new_name",
    "directory_size",
    "filter_names",
    "invert_names",
    "match_names",
    "matches_mask",
    "plan_bulk_rename",
    "send_to_trash",
    "split_masks",
    "trash_paths",
    "trash_root",
    "undo_rename",
    "validate_name",
]

# Windows SHFileOperationW 标志（win32 分支使用）
_FOF_SILENT = 0x0004
_FOF_NOCONFIRMATION = 0x0010
_FOF_ALLOWUNDO = 0x0040
_FOF_NOCONFIRMMKDIR = 0x0200
_FOF_NOERRORUI = 0x0400
_FO_DELETE = 3

_INVALID_NAME_RE = re.compile(r'[<>:"|?*\x00-\x1f]')
_CASE_MODES = frozenset({"keep", "lower", "upper", "title"})


# ============================================================================ #
# 选择与过滤
# ============================================================================ #
def split_masks(mask: str) -> tuple[str, ...]:
    """把 ``;`` 分隔的掩码串拆成非空模式元组。"""
    return tuple(part.strip() for part in mask.split(";") if part.strip())


def matches_mask(name: str, mask: str) -> bool:
    """文件名是否命中通配掩码（``*`` / ``?``，``;`` 分隔多掩码，忽略大小写）。"""
    patterns = split_masks(mask)
    if not patterns:
        return False
    folded = name.casefold()
    return any(fnmatch.fnmatchcase(folded, pattern.casefold()) for pattern in patterns)


def match_names(names: Sequence[str], mask: str) -> list[str]:
    """按掩码筛选名称，保持原顺序。"""
    return [name for name in names if matches_mask(name, mask)]


def filter_names(names: Sequence[str], keyword: str) -> list[str]:
    """快速过滤：按子串匹配名称（忽略大小写），空关键词返回全部。"""
    needle = keyword.strip().casefold()
    if not needle:
        return list(names)
    return [name for name in names if needle in name.casefold()]


def invert_names(names: Sequence[str], selected: Sequence[str]) -> list[str]:
    """反选：返回 ``names`` 中未出现在 ``selected`` 里的名称，保持原顺序。"""
    chosen = set(selected)
    return [name for name in names if name not in chosen]


# ============================================================================ #
# 目录统计
# ============================================================================ #
def directory_size(path: Path) -> tuple[int, int]:
    """统计文件或目录的总字节数与文件数。

    目录递归统计，跳过符号链接（避免跨卷环）；不可读项计入跳过而不报错。
    """
    if path.is_symlink():
        return 0, 0
    if path.is_file():
        try:
            return path.stat().st_size, 1
        except OSError:
            return 0, 0
    total = 0
    count = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_symlink() or not entry.is_file():
                continue
            total += entry.stat().st_size
        except OSError:
            continue
        count += 1
    return total, count


# ============================================================================ #
# 回收站
# ============================================================================ #
@dataclass
class TrashReport:
    """批量移入回收站的结果。"""

    completed: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


def trash_root() -> Path:
    """XDG 回收站根目录（``${XDG_DATA_HOME:-~/.local/share}/Trash``）。"""
    data_home = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(data_home) / "Trash"


def send_to_trash(path: Path) -> Path:
    """把单个文件/目录移入系统回收站，返回回收站内路径（Windows 返回原路径）。

    调用前由 UI 确认；失败抛 ``OSError``，调用方不得静默改为永久删除。
    """
    if not (path.exists() or path.is_symlink()):
        raise FileNotFoundError(f"源不存在: {path}")
    if path.parent == path:
        raise ValueError(f"不能删除根目录: {path}")
    if sys.platform.startswith("win"):
        _trash_windows(path)
        return path
    if sys.platform == "darwin":
        return _trash_macos(path)
    return _trash_linux(path)


def trash_paths(
    paths: Sequence[Path],
    *,
    progress: Callable[[int, int, str], None] | None = None,
    mover: Callable[[Path], Path] = send_to_trash,
) -> TrashReport:
    """批量移入回收站，逐项记录成功与失败（``mover`` 供测试注入）。"""
    report = TrashReport()
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        try:
            mover(path)
            report.completed.append(path)
        except (OSError, ValueError) as exc:
            report.errors.append((path, str(exc)))
        if progress is not None:
            progress(index, total, path.name)
    return report


def _unique_name(directory: Path, name: str) -> Path:
    """在 ``directory`` 下生成不与既有文件/目录冲突的路径。"""
    target = directory / name
    if not (target.exists() or target.is_symlink()):
        return target
    if target.is_dir() and not target.is_symlink():
        stem, suffix = name, ""
    else:
        suffix = target.suffix
        stem = name[: -len(suffix)] if suffix else name
    for index in range(2, 10000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not (candidate.exists() or candidate.is_symlink()):
            return candidate
    raise FileExistsError(f"无法生成唯一名称: {target}")


def _trash_target(files_dir: Path, info_dir: Path, source: Path) -> Path:
    """在回收站 files 目录选一个不与既有文件/``.trashinfo`` 冲突的目标。"""
    suffix = "" if source.is_dir() and not source.is_symlink() else source.suffix
    stem = source.name[: -len(suffix)] if suffix else source.name

    def taken(candidate: Path) -> bool:
        exists = candidate.exists() or candidate.is_symlink()
        return exists or (info_dir / f"{candidate.name}.trashinfo").exists()

    target = files_dir / source.name
    if not taken(target):
        return target
    for index in range(2, 10000):
        candidate = files_dir / f"{stem} ({index}){suffix}"
        if not taken(candidate):
            return candidate
    raise FileExistsError(f"无法生成唯一名称: {target}")


def _trash_linux(path: Path) -> Path:
    """按 XDG Trash 规范移入回收站，写 ``*.trashinfo`` 记录原路径。"""
    root = trash_root()
    files_dir = root / "files"
    info_dir = root / "info"
    files_dir.mkdir(parents=True, exist_ok=True)
    info_dir.mkdir(parents=True, exist_ok=True)
    target = _trash_target(files_dir, info_dir, path)
    info_path = info_dir / f"{target.name}.trashinfo"
    absolute = path.resolve(strict=False)
    info_path.write_text(
        "[Trash Info]\n"
        f"Path={quote(str(absolute), safe='/')}\n"
        f"DeletionDate={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}\n",
        encoding="utf-8",
    )
    try:
        shutil.move(str(path), str(target))
    except OSError:
        info_path.unlink(missing_ok=True)
        raise
    return target


def _trash_macos(path: Path) -> Path:
    """移入 ``~/.Trash``。"""
    trash = Path.home() / ".Trash"
    trash.mkdir(parents=True, exist_ok=True)
    target = _unique_name(trash, path.name)
    shutil.move(str(path), str(target))
    return target


def _trash_windows(path: Path) -> None:
    """调用 shell32 的 ``SHFileOperationW``（``FOF_ALLOWUNDO``）移入回收站。"""
    import ctypes
    from ctypes import wintypes

    class _SHFileOpStructW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", wintypes.WORD),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    operation = _SHFileOpStructW()
    operation.wFunc = _FO_DELETE
    # 双 NUL 结尾：pFrom 需要额外一个空字符串终止列表
    operation.pFrom = str(path.resolve(strict=False)) + "\0"
    operation.fFlags = _FOF_ALLOWUNDO | _FOF_NOCONFIRMATION | _FOF_SILENT | _FOF_NOCONFIRMMKDIR | _FOF_NOERRORUI
    # windll 只在 Windows 上存在，跨平台类型检查时按 Any 处理
    shell32 = cast(Any, ctypes).windll.shell32
    result = shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0:
        raise OSError(f"回收站操作失败（代码 {result}）: {path}")
    if operation.fAnyOperationsAborted:
        raise OSError(f"回收站操作被中止: {path}")


# ============================================================================ #
# 批量重命名
# ============================================================================ #
def validate_name(name: str) -> str:
    """校验新建 / 重命名名称，返回去除首尾空白后的名称。"""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("名称不能为空")
    if cleaned in (".", ".."):
        raise ValueError("名称不能是 . 或 ..")
    if "/" in cleaned or "\\" in cleaned:
        raise ValueError("名称不能包含路径分隔符")
    if _INVALID_NAME_RE.search(cleaned):
        raise ValueError('名称不能包含 < > : " | ? * 等字符')
    return cleaned


@dataclass(frozen=True)
class RenameSource:
    """批量重命名的输入项：名称与是否为目录。"""

    name: str
    is_dir: bool = False


@dataclass(frozen=True)
class BulkRenameOptions:
    """批量重命名规则（最小语法集，见 ADR 0005）。"""

    find: str = ""
    replace: str = ""
    template: str = ""
    start: int = 1
    step: int = 1
    digits: int = 3
    case: str = "keep"
    extension: str = ""


@dataclass(frozen=True)
class RenameItem:
    """单个条目的改名预览；``status`` 取 ok / unchanged / conflict。"""

    source: Path
    new_name: str
    status: str
    note: str = ""


@dataclass(frozen=True)
class RenamePlan:
    """批量重命名预览计划。"""

    items: tuple[RenameItem, ...]

    @property
    def changes(self) -> tuple[RenameItem, ...]:
        """需要实际执行的条目。"""
        return tuple(item for item in self.items if item.status == "ok")

    @property
    def conflicts(self) -> tuple[RenameItem, ...]:
        """被拦下的条目。"""
        return tuple(item for item in self.items if item.status == "conflict")


@dataclass
class RenameReport:
    """批量改名执行 / 撤销结果，``completed`` 为 (原路径, 新路径)。"""

    completed: list[tuple[Path, Path]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


def build_new_name(source: RenameSource, number: int, options: BulkRenameOptions) -> str:
    """按规则计算单个条目的新名称（不做合法性 / 冲突判断）。"""
    if options.case not in _CASE_MODES:
        raise ValueError(f"未知的大小写模式: {options.case}")
    name = source.name.replace(options.find, options.replace) if options.find else source.name
    if source.is_dir:
        stem, suffix = name, ""
    else:
        suffix = Path(name).suffix
        stem = name[: -len(suffix)] if suffix else name
    if options.case == "lower":
        stem = stem.lower()
    elif options.case == "upper":
        stem = stem.upper()
    elif options.case == "title":
        stem = stem.title()
    if options.extension.strip() == "-":
        suffix = ""
    elif options.extension.strip():
        suffix = "." + options.extension.strip().lstrip(".")
    if options.template:
        try:
            return options.template.format(
                name=stem,
                ext=suffix.lstrip("."),
                n=str(number).zfill(max(options.digits, 1)),
            )
        except (KeyError, IndexError, ValueError) as exc:
            raise ValueError("模板无效（仅支持 {name} {ext} {n}，字面花括号用 {{ }}）") from exc
    return stem + suffix


def plan_bulk_rename(
    directory: Path,
    sources: Sequence[RenameSource],
    options: BulkRenameOptions,
) -> RenamePlan:
    """生成预览：标记未变化、非法名、同批重名与目标已存在，冲突不可执行。"""
    items: list[RenameItem] = []
    seen: dict[str, int] = {}
    for position, source in enumerate(sources, start=1):
        path = directory / source.name
        try:
            new_name = build_new_name(source, options.start + (position - 1) * options.step, options)
        except ValueError as exc:
            items.append(RenameItem(path, source.name, "conflict", str(exc)))
            continue
        if new_name == source.name:
            items.append(RenameItem(path, new_name, "unchanged", "名称未变化"))
            continue
        try:
            validate_name(new_name)
        except ValueError as exc:
            items.append(RenameItem(path, new_name, "conflict", str(exc)))
            continue
        key = new_name.casefold()
        if key in seen:
            items.append(RenameItem(path, new_name, "conflict", f"与第 {seen[key]} 项目标同名"))
            continue
        seen[key] = position
        target = directory / new_name
        same_file = os.path.normcase(str(target)) == os.path.normcase(str(path))
        if not same_file and (target.exists() or target.is_symlink()):
            items.append(RenameItem(path, new_name, "conflict", "目标已存在"))
            continue
        items.append(RenameItem(path, new_name, "ok"))
    return RenamePlan(tuple(items))


def apply_rename_plan(
    plan: RenamePlan,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> RenameReport:
    """执行计划中状态为 ok 的改名；冲突项一律不执行。"""
    report = RenameReport()
    changes = plan.changes
    total = len(changes)
    for index, item in enumerate(changes, start=1):
        target = item.source.with_name(item.new_name)
        try:
            same_file = os.path.normcase(str(target)) == os.path.normcase(str(item.source))
            if not same_file and (target.exists() or target.is_symlink()):
                raise FileExistsError(f"目标已存在: {target}")
            item.source.rename(target)
            report.completed.append((item.source, target))
        except (OSError, ValueError) as exc:
            report.errors.append((item.source, str(exc)))
        if progress is not None:
            progress(index, total, item.source.name)
    return report


def undo_rename(
    report: RenameReport,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> RenameReport:
    """按相反顺序撤销一次批量改名；只对本次运行内的记录有效。"""
    result = RenameReport()
    completed = list(reversed(report.completed))
    total = len(completed)
    for index, (source, target) in enumerate(completed, start=1):
        try:
            if source.exists() or source.is_symlink():
                raise FileExistsError(f"原名称已被占用: {source.name}")
            target.rename(source)
            result.completed.append((target, source))
        except (OSError, ValueError) as exc:
            result.errors.append((target, str(exc)))
        if progress is not None:
            progress(index, total, target.name)
    return result
