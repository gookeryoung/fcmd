"""cmderw 的纯逻辑补充：回收站删除、通配选择、快速过滤、目录统计。

下划线前缀使其不参与 :mod:`fcmd` 的工具发现；窗口层 ``cmderw.py`` 只做 Tk
装配，这里保持无 UI 依赖、可单测。口径见 ADR 0005 的 D1–D3。
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import stat
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "COMPARE_DIFFERENT",
    "COMPARE_LEFT_ONLY",
    "COMPARE_RIGHT_ONLY",
    "COMPARE_SAME",
    "COMPARE_STATUS_TEXT",
    "ITEM_CANCELLED",
    "ITEM_DONE",
    "ITEM_FAILED",
    "ITEM_PENDING",
    "ITEM_RUNNING",
    "QUEUE_CANCELLED",
    "QUEUE_DONE",
    "QUEUE_FAILED",
    "QUEUE_PENDING",
    "QUEUE_RUNNING",
    "SYNC_ACTION_TEXT",
    "SYNC_COPY",
    "SYNC_DELETE",
    "SYNC_LTR",
    "SYNC_RTL",
    "BulkRenameOptions",
    "CompareEntry",
    "CompareOptions",
    "CompareReport",
    "QueueItem",
    "QueueTask",
    "RenameItem",
    "RenamePlan",
    "RenameReport",
    "RenameSource",
    "SyncDeleteReport",
    "SyncItem",
    "SyncOptions",
    "SyncPlan",
    "TransferQueue",
    "TrashReport",
    "apply_rename_plan",
    "build_new_name",
    "build_sync_plan",
    "cleanup_incomplete_items",
    "cleanup_partial_target",
    "compare_trees",
    "directory_size",
    "execute_sync_deletes",
    "filter_names",
    "invert_names",
    "item_status_text",
    "match_names",
    "matches_mask",
    "plan_bulk_rename",
    "revalidate_sync_item",
    "revalidate_sync_items",
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


# ============================================================================ #
# 传输队列
# ============================================================================ #
QUEUE_PENDING = "pending"
QUEUE_RUNNING = "running"
QUEUE_DONE = "done"
QUEUE_FAILED = "failed"
QUEUE_CANCELLED = "cancelled"

ITEM_PENDING = "pending"
ITEM_RUNNING = "running"
ITEM_DONE = "done"
ITEM_FAILED = "failed"
ITEM_CANCELLED = "cancelled"

_TASK_STATUS_TEXT = {
    QUEUE_PENDING: "排队中",
    QUEUE_RUNNING: "进行中",
    QUEUE_DONE: "已完成",
    QUEUE_FAILED: "有失败",
    QUEUE_CANCELLED: "已取消",
}

_ITEM_STATUS_TEXT = {
    ITEM_PENDING: "等待",
    ITEM_RUNNING: "传输中",
    ITEM_DONE: "完成",
    ITEM_FAILED: "失败",
    ITEM_CANCELLED: "已取消",
}


@dataclass
class QueueItem:
    """队列中的单个传输项；``started`` 区分「已开始但未完成」与「从未开始」。"""

    source: Path
    target: Path
    overwrite: bool = False
    status: str = ITEM_PENDING
    error: str = ""
    started: bool = False
    existed_before: bool = False
    target_owned: bool = False


@dataclass
class QueueTask:
    """一次复制 / 移动：同一目标目录下的一组条目。"""

    task_id: int
    destination: Path
    move: bool
    items: list[QueueItem] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    status: str = QUEUE_PENDING
    cancel_requested: bool = False
    cleanup_errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def action(self) -> str:
        """中文动作名。"""
        return "移动" if self.move else "复制"

    @property
    def total(self) -> int:
        """条目总数。"""
        return len(self.items)

    @property
    def done_count(self) -> int:
        """已完成条目数。"""
        return sum(1 for item in self.items if item.status == ITEM_DONE)

    @property
    def failed_count(self) -> int:
        """失败条目数。"""
        return sum(1 for item in self.items if item.status == ITEM_FAILED)

    @property
    def label(self) -> str:
        """任务单行描述（冲突提示与队列列表共用）。"""
        return f"#{self.task_id} {self.action} {self.total} 项 → {self.destination}"

    @property
    def status_text(self) -> str:
        """任务状态中文文本。"""
        return _TASK_STATUS_TEXT.get(self.status, self.status)


def item_status_text(item: QueueItem) -> str:
    """条目状态中文文本。"""
    return _ITEM_STATUS_TEXT.get(item.status, item.status)


def cleanup_partial_target(target: Path) -> str | None:
    """删除取消后残留的目标；成功或本就无需删除返回 ``None``，失败返回错误文本。"""
    try:
        if target.parent == target:
            return f"拒绝删除根目录: {target}"
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
    except OSError as exc:
        return str(exc)
    return None


def cleanup_incomplete_items(task: QueueTask) -> list[tuple[Path, str]]:
    """清理取消时「已开始但未完成」的目标，返回清理失败列表。

    未开始（排队中被取消）的条目一律不碰；已完成条目一律不碰；目标在本项
    开始前就存在且没有走覆盖删除的，也视为用户文件不删。
    """
    failures: list[tuple[Path, str]] = []
    for item in task.items:
        if not item.started or item.status in (ITEM_DONE, ITEM_PENDING):
            continue
        if item.existed_before and not item.target_owned:
            continue
        if item.target == item.source:
            continue
        if not (item.target.exists() or item.target.is_symlink()):
            continue
        error = cleanup_partial_target(item.target)
        if error is not None:
            failures.append((item.target, error))
    task.cleanup_errors = failures
    return failures


class TransferQueue:
    """串行传输队列：入队去冲突、逐项执行、按项取消、失败项可重试。"""

    def __init__(self) -> None:
        self.tasks: list[QueueTask] = []
        self._next_id = 1

    # -------------------------------------------------------------- #
    # 入队与查询
    # -------------------------------------------------------------- #
    def add(
        self,
        destination: Path,
        items: Sequence[tuple[Path, Path, bool]],
        *,
        move: bool,
        skipped: Sequence[Path] = (),
    ) -> tuple[QueueTask | None, list[str]]:
        """入队；与排队中/进行中任务的目标重复时拒绝，返回可读冲突说明。"""
        targets = [target for _source, target, _overwrite in items]
        conflicts = self.find_conflicts(targets, move=move, destination=destination, count=len(items))
        if conflicts:
            return None, conflicts
        task = QueueTask(
            task_id=self._next_id,
            destination=destination,
            move=move,
            items=[QueueItem(source=source, target=target, overwrite=overwrite) for source, target, overwrite in items],
            skipped=list(skipped),
        )
        self._next_id += 1
        self.tasks.append(task)
        return task, []

    def find_conflicts(
        self,
        targets: Sequence[Path],
        *,
        move: bool,
        destination: Path,
        count: int,
    ) -> list[str]:
        """检查目标是否与未完成任务重复，逐目标说明冲突双方与路径。"""
        owners: dict[Path, QueueTask] = {}
        for task in self.tasks:
            if task.status not in (QUEUE_PENDING, QUEUE_RUNNING):
                continue
            for item in task.items:
                if item.status in (ITEM_PENDING, ITEM_RUNNING):
                    owners[item.target] = task
        action = "移动" if move else "复制"
        messages: list[str] = []
        reported: set[Path] = set()
        for target in targets:
            owner = owners.get(target)
            if owner is None or target in reported:
                continue
            reported.add(target)
            messages.append(f"{owner.label} 与新任务（{action} {count} 项 → {destination}）都会写入 {target}")
        return messages

    def get(self, task_id: int) -> QueueTask | None:
        """按 id 取任务。"""
        for task in self.tasks:
            if task.task_id == task_id:
                return task
        return None

    def unfinished(self) -> list[QueueTask]:
        """尚未结束的任务（排队中或进行中）。"""
        return [task for task in self.tasks if task.status in (QUEUE_PENDING, QUEUE_RUNNING)]

    def pending_task(self) -> QueueTask | None:
        """下一个待执行任务（FIFO）。"""
        for task in self.tasks:
            if task.status == QUEUE_PENDING:
                return task
        return None

    # -------------------------------------------------------------- #
    # 执行状态迁移
    # -------------------------------------------------------------- #
    def begin(self, task: QueueTask) -> None:
        """任务开始执行。"""
        task.status = QUEUE_RUNNING

    def begin_item(self, item: QueueItem) -> None:
        """记录单项开始前的目标存在状态。"""
        item.status = ITEM_RUNNING
        item.started = True
        item.existed_before = item.target.exists() or item.target.is_symlink()

    def complete_item(self, item: QueueItem) -> None:
        """单项成功。"""
        item.status = ITEM_DONE
        item.error = ""

    def fail_item(self, item: QueueItem, error: str) -> None:
        """单项失败，保留错误文本。"""
        item.status = ITEM_FAILED
        item.error = error

    def finish(self, task: QueueTask) -> str:
        """按逐项状态收尾，返回最终任务状态。"""
        if any(item.status == ITEM_FAILED for item in task.items):
            task.status = QUEUE_FAILED
        elif any(item.status == ITEM_CANCELLED for item in task.items):
            task.status = QUEUE_CANCELLED
        else:
            task.status = QUEUE_DONE
        task.cancel_requested = False
        return task.status

    # -------------------------------------------------------------- #
    # 取消与重试
    # -------------------------------------------------------------- #
    def cancel(self, task_id: int) -> str:
        """请求取消：排队中立即取消，进行中在当前文件完成后停止。"""
        task = self.get(task_id)
        if task is None:
            return "任务不存在"
        if task.status == QUEUE_PENDING:
            task.status = QUEUE_CANCELLED
            for item in task.items:
                item.status = ITEM_CANCELLED
            return f"{task.label} 已取消（尚未开始）"
        if task.status == QUEUE_RUNNING:
            task.cancel_requested = True
            return f"{task.label} 将在当前文件完成后停止"
        return f"{task.label} 已结束，无需取消"

    def cancel_remaining(self, task: QueueTask) -> None:
        """把尚未开始的条目标记为取消（已开始的当前项不受影响）。"""
        for item in task.items:
            if item.status in (ITEM_PENDING, ITEM_RUNNING):
                item.status = ITEM_CANCELLED

    def retry_items(
        self,
        task_id: int,
        items: Sequence[QueueItem] | None = None,
        *,
        overwrite_existing: bool = False,
    ) -> tuple[QueueTask | None, list[str]]:
        """把失败项重新入队；``items`` 为空时取该任务全部失败项。"""
        task = self.get(task_id)
        if task is None:
            return None, ["任务不存在"]
        failed = (
            [item for item in items if item.status == ITEM_FAILED]
            if items is not None
            else [item for item in task.items if item.status == ITEM_FAILED]
        )
        if not failed:
            return None, [f"{task.label} 没有失败项"]
        triples = [(item.source, item.target, True if overwrite_existing else item.overwrite) for item in failed]
        return self.add(task.destination, triples, move=task.move)


# ============================================================================ #
# 目录比较（只读，不跟随符号链接，不写任何文件）
# ============================================================================ #
COMPARE_LEFT_ONLY = "left_only"
COMPARE_RIGHT_ONLY = "right_only"
COMPARE_DIFFERENT = "different"
COMPARE_SAME = "same"

COMPARE_STATUS_TEXT = {
    COMPARE_LEFT_ONLY: "仅左侧",
    COMPARE_RIGHT_ONLY: "仅右侧",
    COMPARE_DIFFERENT: "内容不同",
    COMPARE_SAME: "相同",
}


@dataclass(frozen=True)
class CompareOptions:
    """比较口径：递归、隐藏项、时间容差与深度哈希。"""

    recursive: bool = True
    include_hidden: bool = False
    mtime_tolerance: float = 2.0
    deep: bool = False


@dataclass(frozen=True)
class CompareEntry:
    """一条比较结果；路径始终位于两侧根目录之内。"""

    relative: str
    status: str
    left: Path | None = None
    right: Path | None = None
    left_size: int | None = None
    right_size: int | None = None
    left_mtime: float | None = None
    right_mtime: float | None = None
    left_is_dir: bool = False
    right_is_dir: bool = False
    reason: str = ""


@dataclass
class CompareReport:
    """比较结果：逐条差异与无法读取的错误。"""

    left_root: Path
    right_root: Path
    entries: list[CompareEntry] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """按类别计数，额外给出 ``total``。"""
        result = dict.fromkeys(COMPARE_STATUS_TEXT, 0)
        for entry in self.entries:
            result[entry.status] = result.get(entry.status, 0) + 1
        result["total"] = len(self.entries)
        return result

    def filtered(self, status: str | None) -> list[CompareEntry]:
        """按类别过滤；``None`` / 空串 / ``all`` 返回全部。"""
        if status in (None, "", "all"):
            return list(self.entries)
        return [entry for entry in self.entries if entry.status == status]


@dataclass(frozen=True)
class _CompareNode:
    path: Path
    is_dir: bool
    is_symlink: bool
    size: int
    mtime: float


def _is_hidden_name(name: str) -> bool:
    """隐藏口径：名称以点开头。"""
    return name.startswith(".")


def _collect_nodes(
    root: Path,
    options: CompareOptions,
    errors: list[tuple[Path, str]],
) -> dict[str, _CompareNode]:
    """收集相对路径到节点信息的映射；不跟随符号链接、不越出根目录。"""
    nodes: dict[str, _CompareNode] = {}
    stack: list[tuple[Path, str]] = [(root, "")]
    while stack:
        directory, prefix = stack.pop()
        try:
            with os.scandir(directory) as scanned:
                items = list(scanned)
        except OSError as exc:
            errors.append((directory, str(exc)))
            continue
        for item in items:
            name = item.name
            if not options.include_hidden and _is_hidden_name(name):
                continue
            relative = f"{prefix}{name}"
            try:
                is_symlink = item.is_symlink()
                is_dir = item.is_dir(follow_symlinks=False)
                stat = item.stat(follow_symlinks=False)
            except OSError as exc:
                errors.append((Path(item.path), str(exc)))
                continue
            nodes[relative] = _CompareNode(Path(item.path), is_dir, is_symlink, stat.st_size, stat.st_mtime)
            if is_dir and not is_symlink and options.recursive:
                stack.append((Path(item.path), f"{relative}/"))
    return nodes


def _hash_file(path: Path) -> str:
    """流式 SHA256；只读打开，不产生临时文件。"""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare_symlinks(
    left: _CompareNode,
    right: _CompareNode,
    errors: list[tuple[Path, str]],
) -> tuple[bool, str] | None:
    """任一侧是符号链接时返回比较结果；两侧都不是时返回 ``None``。"""
    if not (left.is_symlink or right.is_symlink):
        return None
    if not (left.is_symlink and right.is_symlink):
        return False, "符号链接与普通条目"
    try:
        same_link = left.path.readlink() == right.path.readlink()
    except OSError as exc:
        errors.append((left.path, str(exc)))
        return False, "符号链接无法读取"
    return (same_link, "" if same_link else "链接目标不同")


def _compare_files(
    left: _CompareNode,
    right: _CompareNode,
    options: CompareOptions,
    errors: list[tuple[Path, str]],
) -> tuple[bool, str]:
    """比较两个普通文件，返回 (是否相同, 不同原因)。"""
    if left.size != right.size:
        return False, "大小不同"
    if options.deep:
        try:
            if _hash_file(left.path) == _hash_file(right.path):
                return True, ""
        except OSError as exc:
            errors.append((left.path, str(exc)))
            return False, "无法读取内容"
        return False, "内容不同"
    if abs(left.mtime - right.mtime) > options.mtime_tolerance:
        return False, "修改时间不同"
    return True, ""


def _compare_nodes(
    left: _CompareNode,
    right: _CompareNode,
    options: CompareOptions,
    errors: list[tuple[Path, str]],
) -> tuple[bool, str]:
    """比较同相对路径的两个节点，返回 (是否相同, 不同原因)。"""
    link_result = _compare_symlinks(left, right, errors)
    if link_result is not None:
        return link_result
    if left.is_dir != right.is_dir:
        return False, "类型不同"
    if left.is_dir:
        return True, ""
    return _compare_files(left, right, options, errors)


def compare_trees(
    left_root: Path,
    right_root: Path,
    options: CompareOptions | None = None,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> CompareReport:
    """只读比较两个目录树；任何情况下都不写文件、不跟随符号链接。"""
    options = options or CompareOptions()
    report = CompareReport(left_root=Path(left_root), right_root=Path(right_root))
    for label, root in (("左", report.left_root), ("右", report.right_root)):
        if not root.is_dir():
            raise ValueError(f"{label}侧目录不存在: {root}")
    left_nodes = _collect_nodes(report.left_root, options, report.errors)
    right_nodes = _collect_nodes(report.right_root, options, report.errors)
    keys = sorted(set(left_nodes) | set(right_nodes))
    total = len(keys)
    for index, relative in enumerate(keys, start=1):
        left = left_nodes.get(relative)
        right = right_nodes.get(relative)
        if left is None:
            assert right is not None
            entry = CompareEntry(
                relative,
                COMPARE_RIGHT_ONLY,
                right=right.path,
                right_size=right.size,
                right_mtime=right.mtime,
                right_is_dir=right.is_dir,
            )
        elif right is None:
            entry = CompareEntry(
                relative,
                COMPARE_LEFT_ONLY,
                left=left.path,
                left_size=left.size,
                left_mtime=left.mtime,
                left_is_dir=left.is_dir,
            )
        else:
            same, reason = _compare_nodes(left, right, options, report.errors)
            entry = CompareEntry(
                relative,
                COMPARE_SAME if same else COMPARE_DIFFERENT,
                left=left.path,
                right=right.path,
                left_size=left.size,
                right_size=right.size,
                left_mtime=left.mtime,
                right_mtime=right.mtime,
                left_is_dir=left.is_dir,
                right_is_dir=right.is_dir,
                reason=reason,
            )
        report.entries.append(entry)
        if progress is not None:
            progress(index, total, relative)
    return report


# ============================================================================ #
# 目录同步（计划、执行前复核、删除逐项执行）
# ============================================================================ #
SYNC_LTR = "left_to_right"
SYNC_RTL = "right_to_left"
SYNC_COPY = "copy"
SYNC_DELETE = "delete"

SYNC_ACTION_TEXT = {
    SYNC_COPY: "复制",
    SYNC_DELETE: "删除",
}


@dataclass(frozen=True)
class SyncOptions:
    """同步计划选项；删除默认关闭，目录删除需再开一层。"""

    direction: str = SYNC_LTR
    delete_target_extra: bool = False
    delete_dirs: bool = False
    mtime_tolerance: float = 2.0


@dataclass
class SyncItem:
    """一条同步动作；``target_*`` 记录预演时的目标状态，供执行前复核。"""

    action: str
    relative: str
    target: Path
    source: Path | None = None
    is_dir: bool = False
    overwrite: bool = False
    target_size: int | None = None
    target_mtime: float | None = None
    target_is_dir: bool = False
    target_existed: bool = False
    skip_reason: str = ""

    @property
    def action_text(self) -> str:
        """中文动作名（跳过项带原因）。"""
        if self.skip_reason:
            return "跳过"
        return SYNC_ACTION_TEXT.get(self.action, self.action)


@dataclass
class SyncPlan:
    """同步预演结果：复制 / 覆盖、删除、策略跳过与相同项计数。"""

    left_root: Path
    right_root: Path
    direction: str
    copies: list[SyncItem] = field(default_factory=list)
    deletes: list[SyncItem] = field(default_factory=list)
    skipped: list[SyncItem] = field(default_factory=list)
    same_count: int = 0

    @property
    def source_root(self) -> Path:
        """复制来源根目录。"""
        return self.left_root if self.direction == SYNC_LTR else self.right_root

    @property
    def target_root(self) -> Path:
        """复制目标 / 删除所在根目录。"""
        return self.right_root if self.direction == SYNC_LTR else self.left_root

    def counts(self) -> dict[str, int]:
        """汇总计数（新建 / 覆盖 / 删除 / 跳过 / 相同）。"""
        new_count = sum(1 for item in self.copies if not item.overwrite)
        return {
            "copy": len(self.copies),
            "copy_new": new_count,
            "copy_overwrite": len(self.copies) - new_count,
            "delete": len(self.deletes),
            "delete_dirs": sum(1 for item in self.deletes if item.is_dir),
            "skipped": len(self.skipped),
            "same": self.same_count,
        }


@dataclass
class SyncDeleteReport:
    """删除逐项执行结果；失败即停，剩余项进入 ``not_executed``。"""

    completed: list[SyncItem] = field(default_factory=list)
    failures: list[tuple[SyncItem, str]] = field(default_factory=list)
    not_executed: list[SyncItem] = field(default_factory=list)

    @property
    def stopped(self) -> bool:
        """是否因失败而中止。"""
        return bool(self.failures)


def _safe_join(root: Path, relative: str) -> Path | None:
    """把比较结果中的相对路径安全拼到根目录下；越界返回 ``None``。"""
    if not relative or relative.startswith(("/", "\\")):
        return None
    parts = relative.replace("\\", "/").split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    return root.joinpath(*parts)


def _covered_by(relative: str, covered: Sequence[str]) -> bool:
    """相对路径是否已被某个整目录动作覆盖。"""
    return any(relative.startswith(f"{prefix}/") for prefix in covered)


def _target_state(item: SyncItem, path: Path) -> SyncItem:
    """记录目标当前的大小 / mtime / 类型（``lstat``，不跟随符号链接）。"""
    try:
        info = path.lstat()
    except OSError:
        return item
    item.target_existed = True
    item.target_is_dir = stat.S_ISDIR(info.st_mode)
    item.target_size = info.st_size
    item.target_mtime = info.st_mtime
    return item


def _skip_item(action: str, relative: str, target: Path, is_dir: bool, reason: str) -> SyncItem:
    """构造一条策略跳过项。"""
    return SyncItem(action, relative, target, is_dir=is_dir, skip_reason=reason)


def _entry_action(entry: CompareEntry, direction: str) -> tuple[str, bool]:
    """按方向判定条目是复制还是删除，并取源侧目录标记。"""
    source_is_left = direction == SYNC_LTR
    source_is_dir = entry.left_is_dir if source_is_left else entry.right_is_dir
    target_is_dir = entry.right_is_dir if source_is_left else entry.left_is_dir
    if entry.status == COMPARE_DIFFERENT:
        return SYNC_COPY, bool(source_is_dir)
    if (entry.status == COMPARE_LEFT_ONLY) == source_is_left:
        return SYNC_COPY, bool(source_is_dir)
    return SYNC_DELETE, bool(target_is_dir)


def _make_copy_item(entry: CompareEntry, options: SyncOptions, plan: SyncPlan, is_dir: bool) -> SyncItem:
    """生成复制项或跳过项；不跟随符号链接。"""
    source = entry.left if options.direction == SYNC_LTR else entry.right
    target = entry.right if options.direction == SYNC_LTR else entry.left
    target = target or _safe_join(plan.target_root, entry.relative)
    if target is None:
        return _skip_item(SYNC_COPY, entry.relative, plan.target_root, is_dir, "路径越出面板范围")
    if source is None:
        return _skip_item(SYNC_COPY, entry.relative, target, is_dir, "源端条目缺失")
    if source.is_symlink():
        return _skip_item(SYNC_COPY, entry.relative, target, is_dir, "符号链接不处理")
    if target.is_symlink():
        return _skip_item(SYNC_COPY, entry.relative, target, is_dir, "目标为符号链接，未处理")
    item = SyncItem(
        SYNC_COPY,
        entry.relative,
        target,
        source=source,
        is_dir=is_dir,
        overwrite=entry.status == COMPARE_DIFFERENT or target.exists(),
    )
    _target_state(item, target)
    return item


def _make_delete_item(entry: CompareEntry, options: SyncOptions, plan: SyncPlan, is_dir: bool) -> SyncItem:
    """生成删除项或跳过项；删除目录需额外勾选。"""
    target = entry.right if options.direction == SYNC_LTR else entry.left
    target = target or _safe_join(plan.target_root, entry.relative) or plan.target_root
    if not options.delete_target_extra:
        return _skip_item(SYNC_DELETE, entry.relative, target, is_dir, "未勾选删除目标端多余文件")
    if is_dir and not options.delete_dirs:
        return _skip_item(SYNC_DELETE, entry.relative, target, is_dir, "未勾选删除多余目录")
    if target.is_symlink():
        return _skip_item(SYNC_DELETE, entry.relative, target, is_dir, "目标为符号链接，未处理")
    item = SyncItem(SYNC_DELETE, entry.relative, target, is_dir=is_dir)
    _target_state(item, target)
    return item


def _record_item(plan: SyncPlan, item: SyncItem, covered: list[str]) -> None:
    """把生成结果归入计划；整目录动作（含策略跳过）记入覆盖表，子项不再重复。"""
    if item.is_dir:
        covered.append(item.relative)
    if item.skip_reason:
        plan.skipped.append(item)
        return
    if item.action == SYNC_COPY:
        plan.copies.append(item)
    else:
        plan.deletes.append(item)


def build_sync_plan(report: CompareReport, options: SyncOptions | None = None) -> SyncPlan:
    """把比较结果转成同步预演；只读，不修改任何文件、不跟随符号链接。"""
    options = options or SyncOptions()
    if options.direction not in (SYNC_LTR, SYNC_RTL):
        raise ValueError(f"未知的同步方向: {options.direction}")
    plan = SyncPlan(Path(report.left_root), Path(report.right_root), options.direction)
    covered: list[str] = []
    for entry in sorted(report.entries, key=lambda item: item.relative):
        if entry.status == COMPARE_SAME:
            plan.same_count += 1
            continue
        if _covered_by(entry.relative, covered):
            continue
        action, is_dir = _entry_action(entry, options.direction)
        if action == SYNC_COPY:
            item = _make_copy_item(entry, options, plan, is_dir)
        else:
            item = _make_delete_item(entry, options, plan, is_dir)
        _record_item(plan, item, covered)
    return plan


def _sync_change_reason(item: SyncItem, size: int, mtime: float, tolerance: float) -> str | None:
    """目标当前状态与预演记录不一致时返回原因。"""
    if item.target_mtime is None:
        return "目标缺少预演记录"
    if abs(mtime - item.target_mtime) > tolerance:
        return "目标修改时间已变化"
    if not item.target_is_dir and item.target_size is not None and size != item.target_size:
        return "目标大小已变化"
    return None


def _source_reason(source: Path) -> str | None:
    """源端可用性检查：缺失 / 不可读 / 符号链接。"""
    try:
        info = source.lstat()
    except FileNotFoundError:
        return "源已不存在"
    except OSError as exc:
        return str(exc)
    return "符号链接不处理" if stat.S_ISLNK(info.st_mode) else None


def _compare_target_state(item: SyncItem, info: os.stat_result, tolerance: float) -> str | None:
    """目标类型 / 大小 / mtime 与预演记录比较。"""
    if stat.S_ISDIR(info.st_mode) != item.target_is_dir:
        return "目标类型已变化"
    return _sync_change_reason(item, info.st_size, info.st_mtime, tolerance)


def _target_reason_for_copy(item: SyncItem, tolerance: float) -> str | None:
    """复制前检查目标：符号链接、新增冲突、覆盖目标已变化。"""
    if item.target.is_symlink():
        return "目标为符号链接，未处理"
    if not item.overwrite:
        return "目标已出现，请重新预演" if item.target.exists() else None
    if not item.target.exists():
        return "目标已不存在（预演时存在）"
    try:
        info = item.target.lstat()
    except OSError as exc:
        return str(exc)
    return _compare_target_state(item, info, tolerance)


def _revalidate_delete(item: SyncItem, tolerance: float) -> str | None:
    """删除前检查目标是否仍是预演时看到的那一项。"""
    try:
        info = item.target.lstat()
    except FileNotFoundError:
        return "目标已不存在"
    except OSError as exc:
        return str(exc)
    if stat.S_ISLNK(info.st_mode):
        return "符号链接不处理"
    return _compare_target_state(item, info, tolerance)


def revalidate_sync_item(item: SyncItem, *, mtime_tolerance: float = 2.0) -> str | None:
    """执行前复核单项；可执行返回 ``None``，否则返回跳过原因（不静默略过）。"""
    if item.action == SYNC_COPY:
        if item.source is None:
            return "源端条目缺失"
        return _source_reason(item.source) or _target_reason_for_copy(item, mtime_tolerance)
    return _revalidate_delete(item, mtime_tolerance)


def revalidate_sync_items(
    items: Sequence[SyncItem],
    *,
    mtime_tolerance: float = 2.0,
) -> tuple[list[SyncItem], list[tuple[SyncItem, str]]]:
    """批量复核，返回 (可执行项, [(跳过项, 原因)])。"""
    ready: list[SyncItem] = []
    skipped: list[tuple[SyncItem, str]] = []
    for item in items:
        reason = revalidate_sync_item(item, mtime_tolerance=mtime_tolerance)
        if reason is None:
            ready.append(item)
        else:
            skipped.append((item, reason))
    return ready, skipped


def execute_sync_deletes(
    items: Sequence[SyncItem],
    *,
    mover: Callable[[Path], Any] = send_to_trash,
    progress: Callable[[int, int, str], None] | None = None,
) -> SyncDeleteReport:
    """逐项移入回收站；任一项失败立即停止，其余项记为未执行。"""
    report = SyncDeleteReport()
    total = len(items)
    for index, item in enumerate(items):
        try:
            mover(item.target)
        except (OSError, ValueError) as exc:
            report.failures.append((item, str(exc)))
            report.not_executed.extend(items[index + 1 :])
            break
        report.completed.append(item)
        if progress is not None:
            progress(index + 1, total, item.relative)
    return report
