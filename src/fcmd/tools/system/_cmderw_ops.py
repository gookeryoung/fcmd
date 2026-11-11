"""cmderw 的纯逻辑补充：回收站删除、通配选择、快速过滤、目录统计、菜单状态。

下划线前缀使其不参与 :mod:`fcmd` 的工具发现；窗口层 ``cmderw.py`` 只做 Tk
装配，这里保持无 UI 依赖、可单测。口径见 ADR 0005 的 D1–D14。
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import shutil
import stat
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "BROWSE_STEP_PARENT",
    "COLOR_KIND_ANY",
    "COLOR_KIND_DIR",
    "COLOR_KIND_FILE",
    "COLOR_KIND_LINK",
    "COLOR_KIND_TEXT",
    "COLUMN_MAX_WIDTH",
    "COLUMN_MIN_WIDTH",
    "COLUMN_SORT",
    "COMMAND_HISTORY_LIMIT",
    "COMPARE_DIFFERENT",
    "COMPARE_LEFT_ONLY",
    "COMPARE_MODES",
    "COMPARE_MODE_CONTENT",
    "COMPARE_MODE_NAME",
    "COMPARE_MODE_SIZE",
    "COMPARE_MODE_TEXT",
    "COMPARE_MODE_TIME",
    "COMPARE_RIGHT_ONLY",
    "COMPARE_SAME",
    "COMPARE_STATUS_TEXT",
    "DEFAULT_VIEW_MODE",
    "DESTRUCTIVE_COMMANDS",
    "IMAGE_SUFFIXES",
    "ITEM_CANCELLED",
    "ITEM_DONE",
    "ITEM_FAILED",
    "ITEM_PENDING",
    "ITEM_RUNNING",
    "OPEN_WITH_LIMIT",
    "PANEL_COLUMNS",
    "PREVIEW_BINARY",
    "PREVIEW_HEX_LIMIT",
    "PREVIEW_IMAGE",
    "PREVIEW_TEXT",
    "PREVIEW_TEXT_LIMIT",
    "PREVIEW_UNSUPPORTED",
    "QUEUE_CANCELLED",
    "QUEUE_DONE",
    "QUEUE_FAILED",
    "QUEUE_PENDING",
    "QUEUE_RUNNING",
    "SYNC_ACTION_TEXT",
    "SYNC_BOTH",
    "SYNC_CONFLICTS",
    "SYNC_CONFLICT_NEWER",
    "SYNC_CONFLICT_OVERWRITE",
    "SYNC_CONFLICT_SKIP",
    "SYNC_CONFLICT_TEXT",
    "SYNC_COPY",
    "SYNC_DELETE",
    "SYNC_DIRECTIONS",
    "SYNC_LTR",
    "SYNC_RTL",
    "THUMBNAIL_BADGE",
    "THUMBNAIL_CACHE_BYTES",
    "THUMBNAIL_CACHE_ITEMS",
    "THUMBNAIL_GAP",
    "THUMBNAIL_IMAGE",
    "THUMBNAIL_INVALIDATION_REASONS",
    "THUMBNAIL_TILE_SIZE",
    "VIEW_MODES",
    "VIEW_MODE_BRIEF",
    "VIEW_MODE_DETAILED",
    "VIEW_MODE_THUMBNAILS",
    "ColorRule",
    "ColorStyle",
    "ColorSubject",
    "CompareEntry",
    "CompareOptions",
    "CompareReport",
    "Completion",
    "MenuFacts",
    "MenuState",
    "PreviewResult",
    "QueueItem",
    "QueueTask",
    "RenameItem",
    "RenamePlan",
    "RenameReport",
    "SyncDeleteReport",
    "SyncItem",
    "SyncOptions",
    "SyncPlan",
    "SyncRunReport",
    "ThumbnailCache",
    "ThumbnailRequestTracker",
    "TransferQueue",
    "TrashReport",
    "apply_rename_plan",
    "branch_relative_path",
    "browse_follow_path",
    "browse_step",
    "build_sync_plan",
    "cleanup_incomplete_items",
    "cleanup_partial_target",
    "column_display_names",
    "command_history_push",
    "command_history_step",
    "compare_trees",
    "complete_token",
    "completion_mode",
    "destructive_command",
    "directory_size",
    "dump_color_rules",
    "execute_sync_deletes",
    "filter_names",
    "format_sync_report",
    "invert_names",
    "item_status_text",
    "match_names",
    "matches_mask",
    "menu_state",
    "normalize_color",
    "normalize_column_widths",
    "normalize_command_history",
    "normalize_open_with_list",
    "normalize_view_mode",
    "open_with_command",
    "parse_color_rules",
    "read_preview",
    "relative_parent_dir",
    "remember_open_with",
    "resolve_color_style",
    "revalidate_sync_item",
    "revalidate_sync_items",
    "send_to_trash",
    "split_command_token",
    "split_masks",
    "split_sync_selection",
    "sync_invert_selection",
    "sync_item_key",
    "sync_select_all",
    "sync_selectable_keys",
    "sync_toggle_selection",
    "thumbnail_cache_key",
    "thumbnail_columns",
    "thumbnail_plan",
    "thumbnail_unsupported_hint",
    "thumbnail_visible_rows",
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

#: 比较口径：仅文件名 / 按大小 / 按时间 / 按内容哈希。
COMPARE_MODE_NAME = "name"
COMPARE_MODE_SIZE = "size"
COMPARE_MODE_TIME = "time"
COMPARE_MODE_CONTENT = "content"
COMPARE_MODES: tuple[str, ...] = (
    COMPARE_MODE_NAME,
    COMPARE_MODE_SIZE,
    COMPARE_MODE_TIME,
    COMPARE_MODE_CONTENT,
)
COMPARE_MODE_TEXT = {
    COMPARE_MODE_NAME: "仅文件名",
    COMPARE_MODE_SIZE: "按大小",
    COMPARE_MODE_TIME: "按时间",
    COMPARE_MODE_CONTENT: "按内容",
}


@dataclass(frozen=True)
class CompareOptions:
    """比较口径：递归、隐藏项、时间容差、比较模式与掩码过滤。"""

    recursive: bool = True
    include_hidden: bool = False
    mtime_tolerance: float = 2.0
    mode: str = COMPARE_MODE_TIME
    mask: str = ""
    exclude_mask: str = ""
    files_only: bool = False


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
            if not is_dir and not _name_allowed(name, options):
                continue
            if is_dir and options.files_only:
                if not is_symlink and options.recursive:
                    stack.append((Path(item.path), f"{relative}/"))
                continue
            nodes[relative] = _CompareNode(Path(item.path), is_dir, is_symlink, stat.st_size, stat.st_mtime)
            if is_dir and not is_symlink and options.recursive:
                stack.append((Path(item.path), f"{relative}/"))
    return nodes


def _name_allowed(name: str, options: CompareOptions) -> bool:
    """掩码过滤：包含掩码为空表示全收，排除掩码命中则丢弃。"""
    if options.mask and not matches_mask(name, options.mask):
        return False
    return not (options.exclude_mask and matches_mask(name, options.exclude_mask))


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


def _compare_contents(
    left: _CompareNode,
    right: _CompareNode,
    errors: list[tuple[Path, str]],
) -> tuple[bool, str]:
    """按 SHA256 比较内容；读取失败如实记录。"""
    try:
        if _hash_file(left.path) == _hash_file(right.path):
            return True, ""
    except OSError as exc:
        errors.append((left.path, str(exc)))
        return False, "无法读取内容"
    return False, "内容不同"


def _compare_files(
    left: _CompareNode,
    right: _CompareNode,
    options: CompareOptions,
    errors: list[tuple[Path, str]],
) -> tuple[bool, str]:
    """比较两个普通文件，返回 (是否相同, 不同原因)。"""
    mode = options.mode if options.mode in COMPARE_MODES else COMPARE_MODE_TIME
    if mode == COMPARE_MODE_NAME:
        return True, ""
    if left.size != right.size:
        return False, "大小不同"
    if mode == COMPARE_MODE_SIZE:
        return True, ""
    if mode == COMPARE_MODE_CONTENT:
        return _compare_contents(left, right, errors)
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
SYNC_BOTH = "both"
SYNC_DIRECTIONS: tuple[str, ...] = (SYNC_LTR, SYNC_RTL, SYNC_BOTH)
SYNC_COPY = "copy"
SYNC_DELETE = "delete"

SYNC_ACTION_TEXT = {
    SYNC_COPY: "复制",
    SYNC_DELETE: "删除",
}

#: 冲突策略：总是覆盖 / 仅较新覆盖 / 跳过冲突。
SYNC_CONFLICT_OVERWRITE = "overwrite"
SYNC_CONFLICT_NEWER = "newer"
SYNC_CONFLICT_SKIP = "skip"
SYNC_CONFLICTS: tuple[str, ...] = (SYNC_CONFLICT_OVERWRITE, SYNC_CONFLICT_NEWER, SYNC_CONFLICT_SKIP)
SYNC_CONFLICT_TEXT = {
    SYNC_CONFLICT_OVERWRITE: "总是覆盖",
    SYNC_CONFLICT_NEWER: "仅较新覆盖",
    SYNC_CONFLICT_SKIP: "跳过冲突",
}


@dataclass(frozen=True)
class SyncOptions:
    """同步计划选项；删除默认关闭，目录删除需再开一层。"""

    direction: str = SYNC_LTR
    conflict: str = SYNC_CONFLICT_OVERWRITE
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
        """复制来源根目录（双向同步时按左→右展示）。"""
        return self.left_root if self.direction != SYNC_RTL else self.right_root

    @property
    def target_root(self) -> Path:
        """复制目标 / 删除所在根目录（双向同步时按左→右展示）。"""
        return self.right_root if self.direction != SYNC_RTL else self.left_root

    @property
    def is_bidirectional(self) -> bool:
        """是否双向镜像同步。"""
        return self.direction == SYNC_BOTH

    def root_for(self, path: Path) -> Path:
        """返回目标路径所属的面板根目录，供分方向入队使用。"""
        for root in (self.left_root, self.right_root):
            try:
                path.relative_to(root)
            except ValueError:
                continue
            return root
        return self.right_root

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


def _make_copy_item(
    entry: CompareEntry,
    plan: SyncPlan,
    source_is_left: bool,
    is_dir: bool,
    skip_reason: str = "",
) -> SyncItem:
    """按指定来源侧生成复制项或跳过项；不跟随符号链接。"""
    source = entry.left if source_is_left else entry.right
    target = entry.right if source_is_left else entry.left
    target_root = plan.right_root if source_is_left else plan.left_root
    target = target or _safe_join(target_root, entry.relative)
    if target is None:
        return _skip_item(SYNC_COPY, entry.relative, target_root, is_dir, "路径越出面板范围")
    if source is None:
        return _skip_item(SYNC_COPY, entry.relative, target, is_dir, "源端条目缺失")
    if source.is_symlink():
        return _skip_item(SYNC_COPY, entry.relative, target, is_dir, "符号链接不处理")
    if target.is_symlink():
        return _skip_item(SYNC_COPY, entry.relative, target, is_dir, "目标为符号链接，未处理")
    if skip_reason:
        return _skip_item(SYNC_COPY, entry.relative, target, is_dir, skip_reason)
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


def _newer_side(entry: CompareEntry, tolerance: float) -> str | None:
    """两侧 mtime 超过容差时返回较新一侧；无法判定返回 ``None``。"""
    if entry.left_mtime is None or entry.right_mtime is None:
        return None
    if entry.left_mtime - entry.right_mtime > tolerance:
        return "left"
    if entry.right_mtime - entry.left_mtime > tolerance:
        return "right"
    return None


def _source_newer(entry: CompareEntry, source_is_left: bool, tolerance: float) -> bool:
    """单向同步时源端是否严格新于目标端（超出容差）。"""
    newer = _newer_side(entry, tolerance)
    if newer is None:
        return False
    return newer == ("left" if source_is_left else "right")


def _make_directed_item(
    entry: CompareEntry,
    options: SyncOptions,
    plan: SyncPlan,
    conflict: str,
) -> SyncItem:
    """单向同步：按方向与冲突策略生成复制 / 删除 / 跳过项。"""
    source_is_left = options.direction == SYNC_LTR
    is_dir = entry.left_is_dir if source_is_left else entry.right_is_dir
    if entry.status != COMPARE_DIFFERENT:
        if (entry.status == COMPARE_LEFT_ONLY) == source_is_left:
            return _make_copy_item(entry, plan, source_is_left, bool(is_dir))
        target_is_dir = entry.right_is_dir if source_is_left else entry.left_is_dir
        return _make_delete_item(entry, options, plan, bool(target_is_dir))
    reason = ""
    if conflict == SYNC_CONFLICT_SKIP:
        reason = "两侧内容不同，按策略跳过"
    elif conflict == SYNC_CONFLICT_NEWER and not _source_newer(entry, source_is_left, options.mtime_tolerance):
        reason = "源端不较新，按策略跳过"
    return _make_copy_item(entry, plan, source_is_left, bool(is_dir), reason)


def _make_both_item(
    entry: CompareEntry,
    plan: SyncPlan,
    conflict: str,
    tolerance: float,
) -> SyncItem:
    """双向镜像：单侧独有项复制到另一侧，两侧不同项按冲突策略决定方向。"""
    if entry.status == COMPARE_LEFT_ONLY:
        return _make_copy_item(entry, plan, True, bool(entry.left_is_dir))
    if entry.status == COMPARE_RIGHT_ONLY:
        return _make_copy_item(entry, plan, False, bool(entry.right_is_dir))
    if conflict == SYNC_CONFLICT_SKIP:
        return _make_copy_item(entry, plan, True, bool(entry.left_is_dir), "两侧内容不同，按策略跳过")
    if conflict == SYNC_CONFLICT_NEWER:
        newer = _newer_side(entry, tolerance)
        if newer is None:
            return _make_copy_item(entry, plan, True, bool(entry.left_is_dir), "两侧时间相同，无法判定较新一侧")
        source_is_left = newer == "left"
        is_dir = entry.left_is_dir if source_is_left else entry.right_is_dir
        return _make_copy_item(entry, plan, source_is_left, bool(is_dir))
    return _make_copy_item(entry, plan, True, bool(entry.left_is_dir))


@dataclass(frozen=True)
class SyncRunReport:
    """一次同步执行的结果快照，用于结果窗口。"""

    copied: int = 0
    copied_new: int = 0
    deleted: int = 0
    delete_not_executed: int = 0
    unselected: int = 0
    enqueue_ok: bool = True
    skipped: tuple[tuple[str, str], ...] = ()
    failed: tuple[tuple[str, str], ...] = ()


def sync_item_key(item: SyncItem) -> str:
    """预演行的稳定键：动作 + 相对路径。"""
    return f"{item.action}\u0000{item.relative}"


def sync_selectable_keys(plan: SyncPlan) -> tuple[str, ...]:
    """可勾选的预演行（复制 + 删除）键，按预演顺序。"""
    return tuple(sync_item_key(item) for item in (*plan.copies, *plan.deletes))


def sync_select_all(plan: SyncPlan) -> frozenset[str]:
    """默认全选：复制与删除项全部勾选。"""
    return frozenset(sync_selectable_keys(plan))


def sync_toggle_selection(selection: frozenset[str], key: str) -> frozenset[str]:
    """切换一行的勾选状态。"""
    if key in selection:
        return selection - {key}
    return selection | {key}


def sync_invert_selection(plan: SyncPlan, selection: frozenset[str]) -> frozenset[str]:
    """反选：只保留未勾选的行。"""
    return frozenset(key for key in sync_selectable_keys(plan) if key not in selection)


def split_sync_selection(
    plan: SyncPlan,
    selection: frozenset[str],
) -> tuple[list[SyncItem], list[SyncItem], int]:
    """按勾选状态拆分执行范围：复制的项、删除的项、未勾选数量。"""
    copies = [item for item in plan.copies if sync_item_key(item) in selection]
    deletes = [item for item in plan.deletes if sync_item_key(item) in selection]
    unselected = len(plan.copies) + len(plan.deletes) - len(copies) - len(deletes)
    return copies, deletes, unselected


def format_sync_report(report: SyncRunReport, *, max_details: int = 20) -> list[str]:
    """把执行结果格式化成结果窗口文本；明细超限时折叠。"""
    lines: list[str] = []
    if report.copied:
        lines.append(
            f"复制已入队：{report.copied} 项（新建 {report.copied_new}，覆盖 {report.copied - report.copied_new}）"
        )
    elif not report.enqueue_ok:
        lines.append("复制未入队：目标与队列中未完成任务冲突")
    if report.deleted:
        lines.append(f"删除完成：{report.deleted} 项（已移入回收站）")
    if report.delete_not_executed:
        lines.append(f"删除未执行：{report.delete_not_executed} 项（失败即停）")
    if report.unselected:
        lines.append(f"未勾选 {report.unselected} 项，未执行")
    if report.skipped:
        lines.append(f"执行前校验跳过 {len(report.skipped)} 项：")
        lines.extend(f"- {target}：{reason}" for target, reason in report.skipped[:max_details])
        if len(report.skipped) > max_details:
            lines.append(f"- 其余 {len(report.skipped) - max_details} 项见预演清单")
    if report.failed:
        lines.append(f"删除失败 {len(report.failed)} 项：")
        lines.extend(f"- {target}：{error}" for target, error in report.failed[:max_details])
    if report.copied:
        lines += ["", "复制任务在传输队列中继续，失败项可在传输面板查看。"]
    if not lines:
        lines.append("没有可执行的操作（全部被跳过或未勾选）。")
    return lines


def build_sync_plan(report: CompareReport, options: SyncOptions | None = None) -> SyncPlan:
    """把比较结果转成同步预演；只读，不修改任何文件、不跟随符号链接。"""
    options = options or SyncOptions()
    if options.direction not in SYNC_DIRECTIONS:
        raise ValueError(f"未知的同步方向: {options.direction}")
    conflict = options.conflict if options.conflict in SYNC_CONFLICTS else SYNC_CONFLICT_OVERWRITE
    plan = SyncPlan(Path(report.left_root), Path(report.right_root), options.direction)
    covered: list[str] = []
    for entry in sorted(report.entries, key=lambda item: item.relative):
        if entry.status == COMPARE_SAME:
            plan.same_count += 1
            continue
        if _covered_by(entry.relative, covered):
            continue
        if options.direction == SYNC_BOTH:
            item = _make_both_item(entry, plan, conflict, options.mtime_tolerance)
        else:
            item = _make_directed_item(entry, options, plan, conflict)
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


# ============================================================================ #
# 快速查看（文本 / 图片 / 二进制，限读 + 截断提示）
# ============================================================================ #
PREVIEW_TEXT = "text"
PREVIEW_IMAGE = "image"
PREVIEW_BINARY = "binary"
PREVIEW_UNSUPPORTED = "unsupported"

PREVIEW_TEXT_LIMIT = 256 * 1024
PREVIEW_HEX_LIMIT = 4096

_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"\xff\xd8\xff", "jpeg"),
    (b"BM", "bmp"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
    (b"\x00\x00\x01\x00", "ico"),
)


@dataclass(frozen=True)
class PreviewResult:
    """快速查看结果；``text`` 对图片为空，由 UI 负责渲染。"""

    kind: str
    text: str = ""
    note: str = ""
    truncated: bool = False
    size: int = 0


def _human_size(size: int) -> str:
    """字节数转可读文本。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _image_kind(data: bytes, suffix: str) -> str:
    """按魔数与扩展名识别图片格式；非图片返回空串。"""
    for magic, name in _IMAGE_MAGIC:
        if data.startswith(magic):
            return name
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if suffix in {".png", ".gif", ".jpg", ".jpeg", ".bmp", ".webp", ".ico", ".tif", ".tiff"}:
        return suffix.lstrip(".")
    return ""


def _decode_text(data: bytes) -> str | None:
    """把采样字节按常见编码解码；不像文本时返回 ``None``。"""
    if not data:
        return ""
    if b"\x00" in data:
        return None
    control = sum(1 for byte in data if byte < 9 or 13 < byte < 32)
    if control / len(data) > 0.3:
        return None
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _hex_dump(data: bytes, limit: int) -> str:
    """生成十六进制 + ASCII 预览。"""
    lines = []
    for offset in range(0, min(len(data), limit), 16):
        row = data[offset : offset + 16]
        hexpart = " ".join(f"{byte:02x}" for byte in row)
        ascii_part = "".join(chr(byte) if 32 <= byte < 127 else "." for byte in row)
        lines.append(f"{offset:08x}  {hexpart:<47}  {ascii_part}")
    return "\n".join(lines)


def _read_preview_source(path: Path, text_limit: int) -> PreviewResult | tuple[bytes, int]:
    """读取预览所需的文件头；不可预览时直接返回说明。"""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return PreviewResult(PREVIEW_UNSUPPORTED, note="文件已不存在")
    except OSError as exc:
        return PreviewResult(PREVIEW_UNSUPPORTED, note=str(exc))
    if stat.S_ISLNK(info.st_mode):
        return PreviewResult(PREVIEW_UNSUPPORTED, note="符号链接不预览（不跟随）")
    if stat.S_ISDIR(info.st_mode):
        return PreviewResult(PREVIEW_UNSUPPORTED, note="目录不预览")
    try:
        with path.open("rb") as handle:
            data = handle.read(text_limit)
    except OSError as exc:
        return PreviewResult(PREVIEW_UNSUPPORTED, note=str(exc))
    return data, info.st_size


def _preview_payload(data: bytes, size: int, suffix: str, hex_limit: int) -> PreviewResult:
    """按文件头判定图片/文本/二进制，并生成对应预览内容。"""
    image = _image_kind(data, suffix)
    if image:
        return PreviewResult(
            PREVIEW_IMAGE,
            note=f"图片（{image}，{_human_size(size)}）",
            size=size,
        )
    text = _decode_text(data)
    if text is None:
        note = "PE/可执行文件" if data.startswith(b"MZ") else "二进制文件"
        truncated = size > len(data)
        if truncated:
            note += f"；已截断：仅显示前 {_human_size(len(data))}（共 {_human_size(size)}）"
        return PreviewResult(PREVIEW_BINARY, text=_hex_dump(data, hex_limit), note=note, truncated=truncated, size=size)

    truncated = size > len(data)
    note = "文本"
    if truncated:
        note += f"；已截断：仅显示前 {_human_size(len(data))}（共 {_human_size(size)}）"
    return PreviewResult(PREVIEW_TEXT, text=text, note=note, truncated=truncated, size=size)


def read_preview(
    path: Path,
    *,
    text_limit: int = PREVIEW_TEXT_LIMIT,
    hex_limit: int = PREVIEW_HEX_LIMIT,
) -> PreviewResult:
    """只读预览：不跟随符号链接，最多读取 ``text_limit`` 字节。"""
    source = _read_preview_source(path, text_limit)
    if isinstance(source, PreviewResult):
        return source
    data, size = source
    return _preview_payload(data, size, path.suffix.lower(), hex_limit)


# ============================================================================ #
# 颜色规则（扩展名 / 大小 / 类型属性 / 修改时间；后匹配覆盖先匹配）
# ============================================================================ #
COLOR_KIND_ANY = ""
COLOR_KIND_FILE = "file"
COLOR_KIND_DIR = "dir"
COLOR_KIND_LINK = "link"
COLOR_KIND_TEXT = {
    COLOR_KIND_ANY: "任意",
    COLOR_KIND_FILE: "文件",
    COLOR_KIND_DIR: "目录",
    COLOR_KIND_LINK: "符号链接",
}

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass(frozen=True)
class ColorSubject:
    """参与颜色规则匹配的条目信息。"""

    name: str
    is_dir: bool = False
    is_symlink: bool = False
    hidden: bool = False
    size: int = 0
    mtime: float = 0.0


@dataclass(frozen=True)
class ColorRule:
    """一条颜色规则；空前景/背景表示不覆盖。"""

    pattern: str = "*"
    min_size: int | None = None
    max_size: int | None = None
    kind: str = COLOR_KIND_ANY
    hidden: bool | None = None
    newer_days: float | None = None
    older_days: float | None = None
    foreground: str = ""
    background: str = ""

    def matches(self, subject: ColorSubject, now: float) -> bool:
        """判断条目是否命中本规则。"""
        if not self._kind_matches(subject):
            return False
        if self.hidden is not None and subject.hidden != self.hidden:
            return False
        if not self._size_matches(subject.size):
            return False
        if not self._mask_matches(subject.name):
            return False
        return self._time_matches(subject.mtime, now)

    def _kind_matches(self, subject: ColorSubject) -> bool:
        if self.kind == COLOR_KIND_FILE:
            return not subject.is_dir and not subject.is_symlink
        if self.kind == COLOR_KIND_DIR:
            return subject.is_dir
        if self.kind == COLOR_KIND_LINK:
            return subject.is_symlink
        return True

    def _size_matches(self, size: int) -> bool:
        if self.min_size is not None and size < self.min_size:
            return False
        return self.max_size is None or size <= self.max_size

    def _mask_matches(self, name: str) -> bool:
        mask = self.pattern.strip() or "*"
        if mask.startswith("."):
            mask = f"*{mask}"
        return matches_mask(name, mask)

    def _time_matches(self, mtime: float, now: float) -> bool:
        if mtime <= 0:
            return self.newer_days is None and self.older_days is None
        age_days = max(now - mtime, 0.0) / 86400
        if self.newer_days is not None and age_days > self.newer_days:
            return False
        return self.older_days is None or age_days >= self.older_days


@dataclass(frozen=True)
class ColorStyle:
    """规则合并后的颜色；空串表示沿用主题默认。"""

    foreground: str = ""
    background: str = ""


def resolve_color_style(rules: Sequence[ColorRule], subject: ColorSubject, now: float) -> ColorStyle | None:
    """按列表顺序合并命中规则，靠后的规则覆盖靠前的同色属性。"""
    foreground = ""
    background = ""
    matched = False
    for rule in rules:
        if not rule.matches(subject, now):
            continue
        matched = True
        foreground = rule.foreground or foreground
        background = rule.background or background
    if not matched or not (foreground or background):
        return None
    return ColorStyle(foreground, background)


def normalize_color(value: Any) -> str:
    """仅接受 ``#rrggbb``；其余值视为未设置，避免 Tk 颜色异常。"""
    if not isinstance(value, str):
        return ""
    text = value.strip().lower()
    return text if _COLOR_RE.match(text) else ""


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        return None
    return int(value)


def _optional_days(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return float(value)


def parse_color_rules(raw: Any) -> list[ColorRule]:
    """从 JSON 原始值解析规则；旧配置缺失或格式异常时返回空列表。"""
    if not isinstance(raw, list):
        return []
    rules: list[ColorRule] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        rule = ColorRule(
            pattern=str(item.get("pattern") or "*").strip() or "*",
            min_size=_optional_int(item.get("min_size")),
            max_size=_optional_int(item.get("max_size")),
            kind=kind if kind in COLOR_KIND_TEXT else COLOR_KIND_ANY,
            hidden=item.get("hidden") if isinstance(item.get("hidden"), bool) else None,
            newer_days=_optional_days(item.get("newer_days")),
            older_days=_optional_days(item.get("older_days")),
            foreground=normalize_color(item.get("foreground")),
            background=normalize_color(item.get("background")),
        )
        if rule.foreground or rule.background:
            rules.append(rule)
    return rules


def dump_color_rules(rules: Sequence[ColorRule]) -> list[dict[str, Any]]:
    """把规则转成可写入 JSON 的字典列表。"""
    return [
        {
            "pattern": rule.pattern,
            "min_size": rule.min_size,
            "max_size": rule.max_size,
            "kind": rule.kind,
            "hidden": rule.hidden,
            "newer_days": rule.newer_days,
            "older_days": rule.older_days,
            "foreground": rule.foreground,
            "background": rule.background,
        }
        for rule in rules
    ]


# ============================================================================ #
# 命令行风险识别（删除类命令执行前确认）
# ============================================================================ #
DESTRUCTIVE_COMMANDS = frozenset(
    {
        "rm",
        "rmdir",
        "del",
        "erase",
        "rd",
        "unlink",
        "shred",
        "sdelete",
        "remove-item",
        "ri",
        "clear-content",
        "clc",
        "format",
        "mkfs",
        "diskpart",
        "dd",
        "truncate",
    }
)


def destructive_command(command: str) -> str | None:
    """返回命令串中首个删除类命令名；没有则返回 ``None``。"""
    for segment in re.split(r"&&|\|\||[;|\n]", command):
        tokens = segment.strip().split()
        while tokens and tokens[0] in {"sudo", "doas"}:
            tokens.pop(0)
        if not tokens:
            continue
        name = Path(tokens[0].strip("\"'")).name.casefold()
        name = name.removesuffix(".exe")
        if name in DESTRUCTIVE_COMMANDS:
            return name
        if name == "find" and "-delete" in tokens:
            return "find -delete"
    return None


# ============================================================================ #
# 视图模式与列宽（纯逻辑，便于无显示环境测试）
# ============================================================================ #
VIEW_MODE_BRIEF = "brief"
VIEW_MODE_DETAILED = "detailed"
VIEW_MODE_THUMBNAILS = "thumbnails"
VIEW_MODES: tuple[str, ...] = (VIEW_MODE_BRIEF, VIEW_MODE_DETAILED, VIEW_MODE_THUMBNAILS)
DEFAULT_VIEW_MODE = VIEW_MODE_DETAILED

#: 详细列表列定义：(列 id, 标题, 默认宽度)，顺序固定，仅宽度可调。
PANEL_COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("name", "名称", 380),
    ("ext", "扩展名", 80),
    ("size", "大小", 100),
    ("mtime", "修改时间", 150),
    ("attr", "属性", 90),
)

#: 列 id → 排序列（扩展名与属性都按类型排序）。
COLUMN_SORT: dict[str, str] = {
    "name": "name",
    "ext": "type",
    "size": "size",
    "mtime": "mtime",
    "attr": "type",
}

COLUMN_MIN_WIDTH = 40
COLUMN_MAX_WIDTH = 2000
_BRIEF_COLUMNS: tuple[str, ...] = ("name",)


def normalize_view_mode(value: object) -> str:
    """校验视图模式；非字符串或未知取值回落详细列表。"""
    if isinstance(value, str) and value in VIEW_MODES:
        return value
    return DEFAULT_VIEW_MODE


def normalize_column_widths(raw: object) -> dict[str, int]:
    """解析列宽：只保留已知列，整数夹到 40–2000，未知键与非整数丢弃。"""
    if not isinstance(raw, dict):
        return {}
    widths: dict[str, int] = {}
    for column, _label, _default in PANEL_COLUMNS:
        value = raw.get(column)
        if isinstance(value, int) and not isinstance(value, bool):
            widths[column] = min(max(value, COLUMN_MIN_WIDTH), COLUMN_MAX_WIDTH)
    return widths


def column_display_names(view_mode: str) -> tuple[str, ...]:
    """该模式在列表里显示的列：简要只有名称，详细为全部列，缩略图不用列表。"""
    mode = normalize_view_mode(view_mode)
    if mode == VIEW_MODE_BRIEF:
        return _BRIEF_COLUMNS
    if mode == VIEW_MODE_THUMBNAILS:
        return ()
    return tuple(column for column, _label, _width in PANEL_COLUMNS)


# ============================================================================ #
# 缩略图：格式判定、世代号、缓存与网格几何（纯逻辑）
# ============================================================================ #
THUMBNAIL_IMAGE = "image"
THUMBNAIL_BADGE = "badge"

#: Pillow 能解码、可生成缩略图的图片格式（Pillow 为项目常驻依赖）。
IMAGE_SUFFIXES: frozenset[str] = frozenset(
    {
        ".png",
        ".gif",
        ".ppm",
        ".pgm",
        ".pbm",
        ".jpg",
        ".jpeg",
        ".bmp",
        ".webp",
        ".tif",
        ".tiff",
        ".ico",
    }
)

THUMBNAIL_TILE_SIZE = 96
THUMBNAIL_GAP = 8
THUMBNAIL_CACHE_ITEMS = 256
THUMBNAIL_CACHE_BYTES = 32 * 1024 * 1024
#: 世代号的四种失效来源：刷新、切目录、切标签、切模式。
THUMBNAIL_INVALIDATION_REASONS: tuple[str, ...] = ("refresh", "navigate", "tab", "mode")


def thumbnail_plan(name: str) -> str:
    """按扩展名决定缩略图来源：图片交给 Pillow 解码，其余显示类型徽标。"""
    suffix = Path(name).suffix.casefold()
    if suffix in IMAGE_SUFFIXES:
        return THUMBNAIL_IMAGE
    return THUMBNAIL_BADGE


def thumbnail_unsupported_hint(name: str) -> str:
    """选中无法生成缩略图的文件时的提示文本。"""
    suffix = Path(name).suffix.casefold()
    label = suffix.upper() if suffix else Path(name).name
    return f"{label} 不是支持的图片格式，显示类型徽标"


@dataclass
class ThumbnailRequestTracker:
    """缩略图世代号：刷新 / 切目录 / 切标签 / 切模式都会让旧结果失效。"""

    generation: int = 0
    reason: str = ""

    def invalidate(self, reason: str) -> int:
        """递增世代号并记录来源，返回新世代号。"""
        self.generation += 1
        self.reason = reason
        return self.generation

    def accept(self, generation: int) -> bool:
        """该世代号的结果是否仍可应用。"""
        return generation == self.generation


class ThumbnailCache:
    """内存 LRU 缩略图缓存，同时按条目数与字节上限淘汰。"""

    def __init__(self, max_items: int = THUMBNAIL_CACHE_ITEMS, max_bytes: int = THUMBNAIL_CACHE_BYTES) -> None:
        self._max_items = max(1, max_items)
        self._max_bytes = max(1, max_bytes)
        self._items: OrderedDict[tuple[str, int, int, int], tuple[object | None, int]] = OrderedDict()
        self._bytes = 0

    def __contains__(self, key: object) -> bool:
        return key in self._items

    def __len__(self) -> int:
        return len(self._items)

    @property
    def size_bytes(self) -> int:
        """当前缓存估算占用字节。"""
        return self._bytes

    def get(self, key: tuple[str, int, int, int]) -> object | None:
        """读取并刷新 LRU 顺序；未命中返回 ``None``（用 ``in`` 区分）。"""
        try:
            value, _size = self._items.pop(key)
        except KeyError:
            return None
        self._items[key] = (value, _size)
        return value

    def put(self, key: tuple[str, int, int, int], value: object | None, size_bytes: int) -> None:
        """写入（``None`` 表示解码失败），超出条目或字节上限时淘汰最旧项。"""
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= old[1]
        cost = max(0, size_bytes)
        self._items[key] = (value, cost)
        self._bytes += cost
        while len(self._items) > self._max_items or (self._bytes > self._max_bytes and len(self._items) > 1):
            _old_key, (_old_value, old_cost) = self._items.popitem(last=False)
            self._bytes -= old_cost

    def clear(self) -> None:
        """清空缓存（切换目录/模式时释放图片引用）。"""
        self._items.clear()
        self._bytes = 0


def thumbnail_columns(
    viewport_width: int,
    tile_size: int = THUMBNAIL_TILE_SIZE,
    gap: int = THUMBNAIL_GAP,
) -> int:
    """按可用宽度计算列数，至少 1 列。"""
    step = tile_size + gap
    if step <= 0:
        return 1
    return max(1, (max(0, viewport_width) + gap) // step)


def thumbnail_visible_rows(
    scroll_offset: int,
    viewport_height: int,
    row_height: int,
    total_rows: int,
    *,
    overscan: int = 1,
) -> tuple[int, int]:
    """可见行的闭区间 ``[first, last]``；无行时返回 ``(0, -1)``。"""
    if total_rows <= 0 or row_height <= 0:
        return (0, -1)
    first = max(0, max(0, scroll_offset) // row_height - overscan)
    last = min(total_rows - 1, (max(0, scroll_offset) + max(0, viewport_height)) // row_height + overscan)
    if first > last:
        return (0, -1)
    return (first, last)


def thumbnail_cache_key(path: str, mtime_ns: int, size: int, tile_size: int) -> tuple[str, int, int, int]:
    """缓存键：路径 + mtime + 大小 + 缩略图尺寸，任一变化都重新解码。"""
    return (path, mtime_ns, size, tile_size)


# ============================================================================ #
# 顶部菜单启用状态（纯逻辑，便于无显示环境测试）
# ============================================================================ #
_MENU_SELECTION = frozenset(
    {
        "select_all",
        "clear_selection",
        "invert_selection",
        "select_mask",
        "deselect_mask",
    }
)
_MENU_NEEDS_SELECTION = frozenset(
    {
        "copy",
        "move",
        "delete",
        "permanent_delete",
        "pack",
        "bulk_rename",
        "hash",
        "checksum_generate",
        "checksum_verify",
        "set_attributes",
    }
)
_MENU_ONE = frozenset({"open", "view", "edit", "open_with", "open_archive", "properties", "unpack", "rename"})
_MENU_ONE_FILE = frozenset({"view", "edit", "open_with", "unpack"})
_MENU_TWO_FILES = frozenset({"diff_text"})
_MENU_ACTIVE_WRITE = frozenset(
    {
        "new_folder",
        "rename",
        "bulk_rename",
        "delete",
        "permanent_delete",
        "move",
        "pack",
        "checksum_generate",
        "set_attributes",
    }
)
_MENU_TARGET_WRITE = frozenset({"copy", "move", "unpack"})
_MENU_BUSY = frozenset(
    {
        "new_folder",
        "rename",
        "bulk_rename",
        "copy",
        "move",
        "delete",
        "permanent_delete",
        "pack",
        "unpack",
        "hash",
        "checksum_generate",
        "checksum_verify",
        "diff_text",
        "properties",
        "set_attributes",
        "refresh",
        "search",
        "compare",
        "sync",
    }
)


@dataclass(frozen=True)
class MenuFacts:
    """计算菜单启用状态所需的运行时事实。"""

    entry_count: int = 0
    selection_count: int = 0
    two_files: bool = False
    single_is_file: bool = False
    single_is_archive: bool = False
    busy: bool = False
    active_writable: bool = True
    target_writable: bool = True
    tab_count: int = 1
    history_index: int = 0
    history_length: int = 1
    panels_ready: bool = True
    active_exists: bool = True
    show_hidden: bool = False
    tree_visible: bool = False
    quick_visible: bool = False
    sync_browse: bool = False
    branch_view: bool = False
    archive_view: bool = False
    sort_key: str = "name"
    view_mode: str = DEFAULT_VIEW_MODE


@dataclass(frozen=True)
class MenuState:
    """菜单项禁用集合与勾选变量应有状态。"""

    disabled: frozenset[str]
    show_hidden: bool = False
    tree_visible: bool = False
    quick_visible: bool = False
    sync_browse: bool = False
    branch_view: bool = False
    archive_view: bool = False
    sort_key: str = "name"
    view_mode: str = DEFAULT_VIEW_MODE

    def is_disabled(self, action: str) -> bool:
        """动作是否应显示为 disabled。"""
        return action in self.disabled


def _selection_disabled(facts: MenuFacts) -> set[str]:
    disabled: set[str] = set()
    if facts.entry_count <= 0:
        disabled |= _MENU_SELECTION
    if facts.selection_count <= 0:
        disabled |= _MENU_NEEDS_SELECTION | _MENU_ONE
    elif facts.selection_count > 1:
        disabled |= _MENU_ONE
    if facts.selection_count == 1 and not facts.single_is_file:
        disabled |= _MENU_ONE_FILE
    if not facts.two_files:
        disabled |= _MENU_TWO_FILES
    if facts.selection_count != 1 or not facts.single_is_archive:
        disabled.add("open_archive")
    return disabled


def _write_disabled(facts: MenuFacts) -> set[str]:
    disabled: set[str] = set()
    if facts.busy:
        disabled |= _MENU_BUSY
    if not facts.active_writable:
        disabled |= _MENU_ACTIVE_WRITE
    if not facts.target_writable:
        disabled |= _MENU_TARGET_WRITE
    return disabled


def _navigation_disabled(facts: MenuFacts) -> set[str]:
    disabled: set[str] = set()
    if facts.tab_count <= 1:
        disabled.add("close_tab")
    if facts.history_index <= 0:
        disabled.add("back")
    if facts.history_index >= facts.history_length - 1:
        disabled.add("forward")
    return disabled


def _panel_disabled(facts: MenuFacts) -> set[str]:
    disabled: set[str] = set()
    if not facts.panels_ready:
        disabled |= {"compare", "sync"}
    if not facts.active_exists:
        disabled.add("command_line")
    return disabled


_MENU_ARCHIVE_READONLY = frozenset(
    {
        "open_archive",
        "new_folder",
        "rename",
        "bulk_rename",
        "delete",
        "permanent_delete",
        "move",
        "open_with",
        "properties",
        "hash",
        "pack",
        "unpack",
    }
)


def _archive_disabled(facts: MenuFacts) -> set[str]:
    """压缩包内只读：禁用会写回磁盘或依赖真实路径的动作。"""
    return set(_MENU_ARCHIVE_READONLY) if facts.archive_view else set()


def menu_state(facts: MenuFacts) -> MenuState:
    """根据事实计算菜单禁用集合，并携带勾选变量应取的值。"""
    disabled: set[str] = set()
    disabled |= _selection_disabled(facts)
    disabled |= _write_disabled(facts)
    disabled |= _navigation_disabled(facts)
    disabled |= _panel_disabled(facts)
    disabled |= _archive_disabled(facts)
    return MenuState(
        disabled=frozenset(disabled),
        show_hidden=facts.show_hidden,
        tree_visible=facts.tree_visible,
        quick_visible=facts.quick_visible,
        sync_browse=facts.sync_browse,
        branch_view=facts.branch_view,
        archive_view=facts.archive_view,
        sort_key=facts.sort_key,
        view_mode=normalize_view_mode(facts.view_mode),
    )


# ============================================================================ #
# 同步浏览：把一侧的目录变化折算成相对动作，另一侧按同样动作跟随
# ============================================================================ #
BROWSE_STEP_PARENT = ".."


def browse_step(old: str, new: str) -> str | None:
    """折算一次目录变化：进入子目录返回目录名，返回上级返回 ``..``。

    路径栏直接跳转、书签、历史跨步与切换盘符都无法折算成相对动作，返回
    ``None``，对侧保持不动（与 TC 的“同步切换目录”口径一致）。
    """
    if not old or not new or old == new:
        return None
    old_path = Path(old)
    new_path = Path(new)
    if new_path.parent == old_path:
        return new_path.name
    if new_path == old_path.parent:
        return BROWSE_STEP_PARENT
    return None


def browse_follow_path(current: str, step: str | None) -> str | None:
    """按相对动作计算对侧目标目录；无效动作或已在根目录时返回 ``None``。"""
    if not current or step is None:
        return None
    base = Path(current)
    if step == BROWSE_STEP_PARENT:
        parent = base.parent
        return None if parent == base else str(parent)
    return str(base / step)


# ============================================================================ #
# 打开方式：最近使用程序列表与启动命令
# ============================================================================ #
OPEN_WITH_LIMIT = 8


def normalize_open_with_list(raw: object) -> list[str]:
    """解析最近使用的程序：丢弃非字符串与空项，去重保序并截断。"""
    if not isinstance(raw, (list, tuple)):
        return []
    programs: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in programs:
            programs.append(text)
        if len(programs) >= OPEN_WITH_LIMIT:
            break
    return programs


def remember_open_with(programs: Sequence[str], program: str) -> list[str]:
    """把程序移到最近使用首位，去重并截断到上限。"""
    text = program.strip()
    recent = [item for item in programs if item != text]
    if text:
        recent.insert(0, text)
    return recent[:OPEN_WITH_LIMIT]


def open_with_command(program: str, path: str) -> list[str]:
    """构造「用指定程序打开文件」的命令行；参数为空时抛 ``ValueError``。"""
    name = program.strip()
    target = path.strip()
    if not name:
        raise ValueError("请选择要使用的程序")
    if not target:
        raise ValueError("文件路径不能为空")
    return [name, target]


# ============================================================================ #
# 分支视图：相对路径显示与复制时保留目录结构
# ============================================================================ #
def branch_relative_path(root: Path, path: Path) -> str:
    """分支视图显示名：相对根目录的路径；不在根目录下时回退文件名。"""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return path.name
    return str(relative)


def relative_parent_dir(root: Path, path: Path) -> str | None:
    """文件相对根目录的父目录文本（根下为 ``""``）；不在根下返回 ``None``。"""
    try:
        relative = path.relative_to(root).parent
    except ValueError:
        return None
    return "" if relative == Path() else str(relative)


# ============================================================================ #
# 命令行：历史与第一个 token 补全
# ============================================================================ #
COMMAND_HISTORY_LIMIT = 200


def normalize_command_history(raw: object) -> list[str]:
    """解析持久化的命令历史：丢弃非字符串 / 空白项，保序去重并截断。"""
    if not isinstance(raw, list):
        return []
    history: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in history:
            continue
        history.append(text)
        if len(history) >= COMMAND_HISTORY_LIMIT:
            break
    return history


def command_history_push(history: Sequence[str], command: str) -> list[str]:
    """把成功执行的命令放到最前；空白命令不改历史，越界从最旧丢弃。"""
    text = command.strip()
    if not text:
        return list(history)
    return [text, *(item for item in history if item != text)][:COMMAND_HISTORY_LIMIT]


def command_history_step(
    history: Sequence[str],
    index: int,
    draft: str,
    *,
    older: bool,
) -> tuple[str, int, str]:
    """在历史中前后移动，返回新的 (文本, 下标, 草稿)。

    index 为 -1 表示未在浏览历史：首次向上时记录草稿，向下回到最新一条
    再按一次则恢复草稿。已在最旧 / 最新一端时保持不动。
    """
    if not history:
        return draft, -1, draft
    if older:
        next_index = 0 if index < 0 else min(index + 1, len(history) - 1)
        return history[next_index], next_index, draft
    if index <= 0:
        return draft, -1, draft
    return history[index - 1], index - 1, draft


@dataclass(frozen=True)
class Completion:
    """一次补全的结果：新 token、匹配到的候选与是否唯一。"""

    token: str
    candidates: tuple[str, ...] = ()
    unique: bool = False


def split_command_token(text: str) -> tuple[str, str]:
    """把命令行拆成第一个 token 与其余部分（其余部分保留原始空白）。"""
    stripped = text.lstrip()
    if not stripped:
        return "", text
    head = stripped.split(None, 1)[0]
    offset = text.index(head) + len(head)
    return head, text[offset:]


def completion_mode(token: str) -> str:
    """补全模式：含分隔符或以 . / ~ 开头走路径，否则走命令。"""
    if token.startswith((".", "~")) or "/" in token or "\\" in token:
        return "path"
    return "command"


def complete_token(token: str, candidates: Sequence[str]) -> Completion:
    """按候选补全 token：唯一候选补全到完整值，多候选只补公共前缀。"""
    matches = tuple(sorted({item for item in candidates if item.startswith(token)}))
    if not matches:
        return Completion(token)
    if len(matches) == 1:
        return Completion(matches[0], matches, True)
    prefix = ""
    for position, char in enumerate(matches[0]):
        if any(item[position : position + 1] != char for item in matches[1:]):
            break
        prefix += char
    return Completion(prefix if len(prefix) > len(token) else token, matches)
