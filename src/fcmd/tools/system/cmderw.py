"""cmderw - 双面板文件管理器（Tk GUI，Total Commander 风格）。

左右两个独立目录面板，支持键盘与鼠标操作：Tab 切换面板，Enter 进入目录，
F2 重命名、F3 查看、F4 外部编辑、F5 复制、F6 移动、F7 新建目录、F8 删除到
回收站、Shift+F8 永久删除，Alt+F5 打包、Alt+F9 解压，Ctrl+F 搜索，Ctrl+S
快速过滤，Num+/Num- 按掩码选择/取消，Space 统计选中项大小，Ctrl+H 显示隐藏文件。
每个面板支持多标签页（Ctrl+T 新建、Ctrl+W 关闭、Ctrl+Tab 切换，至少保留一个
标签），Ctrl+D 管理书签，Alt+Down 从当前面板历史栈跳转。

能力复用现有工具：打包/解压走 :mod:`fcmd.tools.archive.archivex`，文件哈希走
:mod:`fcmd.tools.hashbase`，文件名/内容搜索走 :mod:`fcmd.dsl.actions.fileops`，
窗口骨架、主题与设置持久化走 :mod:`fcmd.gui`。

设置（左右面板标签与路径、书签、排序列、隐藏文件开关、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/cmderw.json``，下次启动自动恢复；旧配置文件缺少
标签或书签字段时按单标签、空书签处理。

示例
----
    fcmd cmderw           # 打开双面板文件管理器
    cmderw                # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.tools.system._cmderw_ops import (
    COMPARE_DIFFERENT,
    COMPARE_LEFT_ONLY,
    COMPARE_RIGHT_ONLY,
    COMPARE_SAME,
    COMPARE_STATUS_TEXT,
    ITEM_FAILED,
    QUEUE_CANCELLED,
    QUEUE_RUNNING,
    SYNC_DELETE,
    SYNC_LTR,
    SYNC_RTL,
    BulkRenameOptions,
    CompareOptions,
    CompareReport,
    QueueItem,
    QueueTask,
    RenameItem,
    RenamePlan,
    RenameReport,
    RenameSource,
    SyncDeleteReport,
    SyncItem,
    SyncOptions,
    SyncPlan,
    TransferQueue,
    TrashReport,
    apply_rename_plan,
    build_sync_plan,
    cleanup_incomplete_items,
    compare_trees,
    directory_size,
    execute_sync_deletes,
    filter_names,
    invert_names,
    item_status_text,
    match_names,
    plan_bulk_rename,
    revalidate_sync_items,
    trash_paths,
    undo_rename,
    validate_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "ARCHIVE_SUFFIXES",
    "COLUMNS",
    "COLUMN_SORT",
    "DEFAULT_GEOMETRY",
    "DEFAULT_SORT_KEY",
    "MAX_HISTORY",
    "SORT_KEYS",
    "CmderSettings",
    "FileEntry",
    "HashResult",
    "OperationReport",
    "PanelState",
    "SearchHit",
    "TransferItem",
    "TrashReport",
    "archive_stem",
    "config_path",
    "copy_entry",
    "create_directory",
    "default_archive_name",
    "default_directory",
    "default_extract_dir",
    "delete_entries",
    "delete_entry",
    "describe_attributes",
    "describe_entry",
    "directory_size",
    "extract_archive_to",
    "filter_names",
    "format_hash_report",
    "format_mtime",
    "format_search_hit",
    "format_size",
    "hash_paths",
    "history_back",
    "history_forward",
    "invert_names",
    "is_hidden",
    "is_within",
    "list_directory",
    "load_preview",
    "load_settings",
    "match_names",
    "move_entry",
    "normalize_tab_index",
    "open_externally",
    "pack_entries",
    "panel_tab_title",
    "parent_directory",
    "plan_transfer",
    "push_history",
    "rename_entry",
    "resolve_geometry",
    "resolve_path_input",
    "save_settings",
    "search_paths",
    "sort_entries",
    "transfer_entries",
    "unique_path",
    "validate_name",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "双面板文件管理器窗口版（Tk，Total Commander 风格）：左右面板独立导航与历史，"
    "F5 复制/F6 移动/F7 新建/F8 删除到回收站/Shift+F8 永久删除/F2 重命名/F3 查看，"
    "Num+/Num- 按掩码选择，Ctrl+S 快速过滤，Ctrl+M 批量重命名，Space 统计大小，"
    "每面板多标签页（Ctrl+T/Ctrl+W/Ctrl+Tab），Ctrl+D 书签，Alt+Down 历史跳转，"
    "复制/移动进入串行传输队列（逐项进度、按项取消、失败重试），"
    "Alt+F5 打包、Alt+F9 解压，搜索结果可直接复制/移动/删除/加入选择，"
    "复用 archivex/hashbase/filesearch 能力，设置持久化到 ~/.fcmd/ui/cmderw.json"
)

DEFAULT_GEOMETRY = "1240x760+60+40"
MIN_WIDTH = 960
MIN_HEIGHT = 600
MAX_HISTORY = 50
POLL_INTERVAL_MS = 100
DEFAULT_SORT_KEY = "name"
SORT_KEYS: tuple[str, ...] = ("name", "size", "mtime", "type")

#: 面板列定义：(列 id, 标题, 宽度)
COLUMNS: tuple[tuple[str, str, int], ...] = (
    ("name", "名称", 380),
    ("size", "大小", 100),
    ("mtime", "修改时间", 150),
    ("attr", "属性", 90),
)

#: 列 id → 排序列
COLUMN_SORT: dict[str, str] = {
    "name": "name",
    "size": "size",
    "mtime": "mtime",
    "attr": "type",
}

#: 归档扩展名（长的在前，供界面推导默认解压目录）
ARCHIVE_SUFFIXES: tuple[str, ...] = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
    ".tbz2",
    ".tbz",
    ".txz",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
)

_HIDDEN_ATTRIBUTE = 0x2
_MAX_PREVIEW_LINES = 500
_MAX_ERROR_DETAILS = 10


# ============================================================================ #
# 数据模型
# ============================================================================ #
@dataclass(frozen=True)
class FileEntry:
    """目录中的一个条目（文件 / 目录 / 符号链接）。"""

    name: str
    path: Path
    is_dir: bool = False
    is_symlink: bool = False
    size: int = 0
    mtime: float = 0.0
    hidden: bool = False


@dataclass(frozen=True)
class TransferItem:
    """一次复制 / 移动的目标计划。"""

    source: Path
    target: Path
    overwrite: bool = False


@dataclass
class OperationReport:
    """批量文件操作结果。"""

    completed: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)


@dataclass(frozen=True)
class HashResult:
    """单个文件的哈希结果。"""

    path: Path
    digest: str


@dataclass(frozen=True)
class SearchHit:
    """一条搜索命中：``line``/``text`` 仅在内容搜索时非空。"""

    path: Path
    line: int = 0
    text: str = ""


@dataclass
class PanelState:
    """单个面板的持久化状态。"""

    path: str = ""
    sort_key: str = DEFAULT_SORT_KEY
    descending: bool = False
    history: list[str] = field(default_factory=list)
    history_index: int = -1

    def to_dict(self) -> dict[str, Any]:
        """序列化为设置文件中的字典。"""
        return {
            "path": self.path,
            "sort_key": self.sort_key,
            "descending": self.descending,
            "history": list(self.history),
            "history_index": self.history_index,
        }


@dataclass
class CmderSettings:
    """双面板文件管理器的持久化设置。

    ``left``/``right`` 始终指向该面板的活动标签，``left_tabs``/``right_tabs``
    保存全部标签的顺序，下标字段标记当前标签。
    """

    left: PanelState = field(default_factory=PanelState)
    right: PanelState = field(default_factory=PanelState)
    active: str = "left"
    show_hidden: bool = False
    geometry: str | None = None
    left_tabs: list[PanelState] = field(default_factory=list)
    right_tabs: list[PanelState] = field(default_factory=list)
    left_tab_index: int = 0
    right_tab_index: int = 0
    bookmarks: list[str] = field(default_factory=list)


# ============================================================================ #
# 配置持久化
# ============================================================================ #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/cmderw.json``。"""
    return _guiconfig.config_path("cmderw")


def default_directory() -> Path:
    """初始目录：用户主目录；主目录不可用时退回当前工作目录。"""
    home = Path.home()
    return home if home.is_dir() else Path.cwd()


def _valid_directory(raw: object, fallback: Path) -> Path:
    """校验持久化路径；是文件时取父目录，无效时回退 ``fallback``。"""
    if not isinstance(raw, str) or not raw.strip():
        return fallback
    with contextlib.suppress(OSError, ValueError):
        path = Path(raw).expanduser()
        if path.is_dir():
            return path
        if path.is_file():
            return path.parent
    return fallback


def _panel_from_raw(raw: object, fallback: Path) -> PanelState:
    """逐字段解析面板状态，非法字段回退默认值。"""
    if not isinstance(raw, dict):
        return PanelState(path=str(fallback))
    data = cast(dict[str, Any], raw)
    state = PanelState(path=str(_valid_directory(data.get("path"), fallback)))
    sort_key = data.get("sort_key")
    if isinstance(sort_key, str) and sort_key in SORT_KEYS:
        state.sort_key = sort_key
    descending = data.get("descending")
    if isinstance(descending, bool):
        state.descending = descending
    history = data.get("history")
    if isinstance(history, list):
        state.history = [item for item in history if isinstance(item, str) and item][:MAX_HISTORY]
    index = data.get("history_index")
    if isinstance(index, int) and -1 <= index < len(state.history):
        state.history_index = index
    else:
        state.history_index = len(state.history) - 1
    return state


def normalize_tab_index(index: object, count: int) -> int:
    """把标签下标夹进 ``[0, count)``；非法值或空标签列表回退 0。"""
    if not isinstance(index, int) or isinstance(index, bool):
        return 0
    if count <= 0 or index < 0:
        return 0
    return index if index < count else count - 1


def panel_tab_title(path: str) -> str:
    """标签标题：取目录名最后一段，根目录或无名称时回退完整路径。"""
    if not path:
        return ""
    return Path(path).name or path


def _tabs_from_raw(raw: object, fallback: PanelState) -> list[PanelState]:
    """解析标签列表；缺失、非列表或全为非法项时回退为单个面板。"""
    if not isinstance(raw, list):
        return [fallback]
    base = Path(fallback.path)
    tabs = [_panel_from_raw(item, base) for item in raw if isinstance(item, dict)]
    return tabs or [fallback]


def _bookmarks_from_raw(raw: object) -> list[str]:
    """解析书签列表：丢弃非字符串与空项，去重并保持原顺序。"""
    if not isinstance(raw, list):
        return []
    bookmarks: list[str] = []
    for item in raw:
        if isinstance(item, str) and item and item not in bookmarks:
            bookmarks.append(item)
    return bookmarks


def load_settings(path: Path | None = None) -> CmderSettings:
    """读取设置；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    fallback = default_directory()
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return CmderSettings(
            left=PanelState(path=str(fallback)),
            right=PanelState(path=str(fallback)),
            left_tabs=[PanelState(path=str(fallback))],
            right_tabs=[PanelState(path=str(fallback))],
        )

    settings = CmderSettings(
        left=_panel_from_raw(raw.get("left"), fallback),
        right=_panel_from_raw(raw.get("right"), fallback),
    )
    settings.left_tabs = _tabs_from_raw(raw.get("left_tabs"), settings.left)
    settings.right_tabs = _tabs_from_raw(raw.get("right_tabs"), settings.right)
    settings.left_tab_index = normalize_tab_index(raw.get("left_tab_index"), len(settings.left_tabs))
    settings.right_tab_index = normalize_tab_index(raw.get("right_tab_index"), len(settings.right_tabs))
    settings.left = settings.left_tabs[settings.left_tab_index]
    settings.right = settings.right_tabs[settings.right_tab_index]
    settings.bookmarks = _bookmarks_from_raw(raw.get("bookmarks"))
    active = raw.get("active")
    if active in ("left", "right"):
        settings.active = cast(str, active)
    show_hidden = raw.get("show_hidden")
    if isinstance(show_hidden, bool):
        settings.show_hidden = show_hidden
    geometry = raw.get("geometry")
    if isinstance(geometry, str):
        settings.geometry = _guiconfig.valid_geometry(geometry)
    return settings


def save_settings(settings: CmderSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    left_tabs = settings.left_tabs or [settings.left]
    right_tabs = settings.right_tabs or [settings.right]
    left_index = normalize_tab_index(settings.left_tab_index, len(left_tabs))
    right_index = normalize_tab_index(settings.right_tab_index, len(right_tabs))
    payload = {
        "left": left_tabs[left_index].to_dict(),
        "right": right_tabs[right_index].to_dict(),
        "active": settings.active,
        "show_hidden": settings.show_hidden,
        "geometry": settings.geometry,
        "left_tabs": [tab.to_dict() for tab in left_tabs],
        "right_tabs": [tab.to_dict() for tab in right_tabs],
        "left_tab_index": left_index,
        "right_tab_index": right_index,
        "bookmarks": list(settings.bookmarks),
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ============================================================================ #
# 历史记录与路径
# ============================================================================ #
def push_history(history: list[str], index: int, path: str, *, limit: int = MAX_HISTORY) -> tuple[list[str], int]:
    """把 ``path`` 追加进历史（丢弃前进分支），返回新历史与新下标。"""
    if 0 <= index < len(history) and history[index] == path:
        return list(history), index
    entries = list(history[: index + 1]) if 0 <= index < len(history) else list(history)
    if entries and entries[-1] == path:
        entries.pop()
    entries.append(path)
    if len(entries) > limit:
        entries = entries[-limit:]
    return entries, len(entries) - 1


def history_back(history: Sequence[str], index: int) -> int:
    """后退一步的下标；已在最早位置时返回原下标。"""
    return index - 1 if 0 < index <= len(history) - 1 else index


def history_forward(history: Sequence[str], index: int) -> int:
    """前进一步的下标；已在最新位置时返回原下标。"""
    return index + 1 if index + 1 < len(history) else index


def parent_directory(path: Path) -> Path | None:
    """返回父目录；已在根目录时返回 ``None``。"""
    parent = path.parent
    return None if parent == path else parent


def resolve_path_input(raw: str, *, base: Path | None = None) -> tuple[Path, str | None]:
    """解析路径输入，返回 ``(目录, 需要选中的文件名)``。

    相对路径按 ``base``（默认当前工作目录）解析；输入是文件时返回其父目录与
    文件名，便于在面板中定位。
    """
    text = raw.strip()
    if not text:
        raise ValueError("路径不能为空")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute() and base is not None:
        candidate = base / candidate
    candidate = candidate.resolve(strict=False)
    if candidate.is_dir():
        return candidate, None
    if candidate.is_file():
        return candidate.parent, candidate.name
    raise FileNotFoundError(f"路径不存在: {candidate}")


def is_within(path: Path, parent: Path) -> bool:
    """判断 ``path`` 是否位于 ``parent`` 内（含自身）。"""
    with contextlib.suppress(OSError, ValueError):
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    return False


# ============================================================================ #
# 目录读取、排序与展示
# ============================================================================ #
def is_hidden(name: str, stat: os.stat_result | None = None) -> bool:
    """判断条目是否隐藏：点文件或 Windows 隐藏属性。"""
    if name.startswith("."):
        return True
    attributes = getattr(stat, "st_file_attributes", 0) if stat is not None else 0
    return bool(attributes & _HIDDEN_ATTRIBUTE)


def _sort_value(entry: FileEntry, sort_key: str) -> tuple[Any, ...]:
    """排序主键：目录与文件后续由稳定排序保证目录在前。"""
    folded = entry.name.casefold()
    if sort_key == "size":
        return (entry.size, folded)
    if sort_key == "mtime":
        return (entry.mtime, folded)
    if sort_key == "type":
        kind = "" if entry.is_dir else entry.path.suffix.casefold()
        return (kind, folded)
    return (folded, entry.name)


def sort_entries(
    entries: Sequence[FileEntry],
    *,
    sort_key: str = DEFAULT_SORT_KEY,
    descending: bool = False,
) -> list[FileEntry]:
    """排序条目：目录始终在前，其余按列排序（可降序）。"""
    key = sort_key if sort_key in SORT_KEYS else DEFAULT_SORT_KEY
    ordered = sorted(entries, key=lambda entry: _sort_value(entry, key), reverse=descending)
    ordered.sort(key=lambda entry: not entry.is_dir)
    return ordered


def list_directory(directory: Path, *, show_hidden: bool = False) -> list[FileEntry]:
    """读取目录内容（不递归），返回按默认规则排序的条目列表。"""
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    entries: list[FileEntry] = []
    for child in directory.iterdir():
        try:
            stat = child.stat()
        except OSError:
            stat = None
        hidden = is_hidden(child.name, stat)
        if hidden and not show_hidden:
            continue
        is_dir = child.is_dir()
        is_link = child.is_symlink()
        size = 0 if is_dir else (stat.st_size if stat is not None else 0)
        mtime = stat.st_mtime if stat is not None else 0.0
        entries.append(
            FileEntry(
                name=child.name,
                path=child,
                is_dir=is_dir,
                is_symlink=is_link,
                size=size,
                mtime=mtime,
                hidden=hidden,
            )
        )
    return sort_entries(entries)


def _compact_number(value: float) -> str:
    """去掉一位小数格式里多余的 ``.0``。"""
    text = f"{value:.1f}"
    return text[:-2] if text.endswith(".0") else text


def format_size(size: int, *, is_dir: bool = False) -> str:
    """人类可读的大小文本；目录显示 ``<DIR>``。"""
    if is_dir:
        return "<DIR>"
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        value /= 1024.0
        if value < 1024.0:
            return f"{_compact_number(value)} {unit}"
    return f"{_compact_number(value)} PB"


def format_mtime(timestamp: float) -> str:
    """把时间戳格式化为 ``YYYY-MM-DD HH:MM``；无效时间返回空串。"""
    if timestamp <= 0:
        return ""
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(timestamp))


def describe_attributes(entry: FileEntry) -> str:
    """属性列文本：类型 + 隐藏标记。"""
    if entry.is_symlink:
        kind = "LINK"
    elif entry.is_dir:
        kind = "DIR"
    else:
        kind = "FILE"
    return f"{kind} H" if entry.hidden else kind


def describe_entry(entry: FileEntry) -> str:
    """属性对话框中的多行描述。"""
    if entry.is_symlink:
        kind = "符号链接"
    elif entry.is_dir:
        kind = "文件夹"
    else:
        kind = "文件"
    lines = [
        f"名称: {entry.name}",
        f"路径: {entry.path}",
        f"类型: {kind}",
        f"大小: {format_size(entry.size, is_dir=entry.is_dir)}",
        f"修改时间: {format_mtime(entry.mtime) or '-'}",
        f"属性: {describe_attributes(entry)}",
    ]
    if entry.is_symlink:
        with contextlib.suppress(OSError):
            lines.append(f"链接目标: {entry.path.resolve(strict=True)}")
    return "\n".join(lines)


# ============================================================================ #
# 文件操作
# ============================================================================ #
def unique_path(target: Path) -> Path:
    """目标已存在时生成 ``name (2).ext`` 形式的可用路径。"""
    if not (target.exists() or target.is_symlink()):
        return target
    if target.is_dir() and not target.is_symlink():
        stem, suffix = target.name, ""
    else:
        suffix = target.suffix
        stem = target.name[: -len(suffix)] if suffix else target.name
    for index in range(2, 10000):
        candidate = target.with_name(f"{stem} ({index}){suffix}")
        if not (candidate.exists() or candidate.is_symlink()):
            return candidate
    raise FileExistsError(f"无法生成唯一名称: {target}")


def create_directory(parent: Path, name: str) -> Path:
    """在 ``parent`` 下创建目录，返回新目录路径。"""
    target = parent / validate_name(name)
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"目录已存在: {target}")
    target.mkdir()
    return target


def rename_entry(path: Path, new_name: str) -> Path:
    """重命名文件或目录（保留原位置），返回新路径。"""
    target = path.with_name(validate_name(new_name))
    if target == path:
        return target
    if (target.exists() or target.is_symlink()) and target.resolve(strict=False) != path.resolve(strict=False):
        raise FileExistsError(f"目标已存在: {target}")
    path.rename(target)
    return target


def copy_entry(source: Path, target: Path) -> None:
    """复制单个文件 / 目录（目录递归），目标已存在时报错。"""
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"目标已存在: {target}")
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, target, symlinks=True)
        return
    if source.is_file() or source.is_symlink():
        shutil.copy2(source, target, follow_symlinks=False)
        return
    raise FileNotFoundError(f"源不存在: {source}")


def move_entry(source: Path, target: Path) -> None:
    """移动单个文件 / 目录，目标已存在时报错。"""
    if target.exists() or target.is_symlink():
        raise FileExistsError(f"目标已存在: {target}")
    shutil.move(str(source), str(target))


def delete_entry(path: Path) -> None:
    """删除文件 / 目录（目录递归删除，禁止删除根目录）。"""
    if path.parent == path:
        raise ValueError(f"不能删除根目录: {path}")
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
        return
    path.unlink()


def plan_transfer(
    sources: Sequence[Path],
    destination: Path,
    *,
    conflict: str = "rename",
    chooser: Callable[[Path], str] | None = None,
) -> tuple[list[TransferItem], list[Path]]:
    """生成复制 / 移动计划，处理同名冲突与非法目标。

    ``conflict`` 取 ``rename`` / ``overwrite`` / ``skip`` / ``ask``；``ask`` 时
    通过 ``chooser(target)`` 获取决策（未提供时按 ``rename`` 处理）。
    """
    if not destination.is_dir():
        raise NotADirectoryError(f"目标不是目录: {destination}")
    items: list[TransferItem] = []
    skipped: list[Path] = []
    for source in sources:
        target = destination / source.name
        if source.resolve(strict=False) == target.resolve(strict=False):
            skipped.append(source)
            continue
        if source.is_dir() and is_within(destination, source):
            skipped.append(source)
            continue
        overwrite = False
        if target.exists() or target.is_symlink():
            decision = conflict
            if decision == "ask":
                decision = chooser(target) if chooser is not None else "rename"
            if decision == "skip":
                skipped.append(source)
                continue
            if decision == "overwrite":
                overwrite = True
            elif decision == "rename":
                target = unique_path(target)
            else:
                raise ValueError(f"未知的冲突处理方式: {decision}")
        items.append(TransferItem(source=source, target=target, overwrite=overwrite))
    return items, skipped


def transfer_entries(
    items: Sequence[TransferItem],
    *,
    move: bool = False,
    progress: Callable[[int, int, str], None] | None = None,
) -> OperationReport:
    """执行复制 / 移动计划，逐项记录成功与失败。"""
    report = OperationReport()
    total = len(items)
    for index, item in enumerate(items, start=1):
        try:
            if item.target.exists() or item.target.is_symlink():
                if not item.overwrite:
                    raise FileExistsError(f"目标已存在: {item.target}")
                delete_entry(item.target)
            if move:
                move_entry(item.source, item.target)
            else:
                copy_entry(item.source, item.target)
            report.completed.append(item.target)
        except (OSError, ValueError) as exc:
            report.errors.append((item.source, str(exc)))
        if progress is not None:
            progress(index, total, item.source.name)
    return report


def delete_entries(
    paths: Sequence[Path],
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> OperationReport:
    """批量删除，逐项记录成功与失败。"""
    report = OperationReport()
    total = len(paths)
    for index, path in enumerate(paths, start=1):
        try:
            delete_entry(path)
            report.completed.append(path)
        except OSError as exc:
            report.errors.append((path, str(exc)))
        if progress is not None:
            progress(index, total, path.name)
    return report


# ============================================================================ #
# 打包 / 解压（复用 archivex）
# ============================================================================ #
def archive_stem(name: str) -> str:
    """去掉归档扩展名，得到默认解压目录名。"""
    lowered = name.casefold()
    for suffix in ARCHIVE_SUFFIXES:
        if lowered.endswith(suffix):
            stem = name[: -len(suffix)]
            return stem or name
    return name


def default_extract_dir(archive: Path, base: Path) -> Path:
    """默认解压目录：``base / 归档名（去扩展名）``。"""
    return base / archive_stem(archive.name)


def default_archive_name(entries: Sequence[Path]) -> str:
    """默认归档文件名：单条目用条目名，多条目用所在目录名。"""
    if not entries:
        return "archive.zip"
    if len(entries) == 1:
        return f"{entries[0].name}.zip"
    parent_name = entries[0].parent.name
    return f"{parent_name or 'archive'}.zip"


def _write_zip(sources: Sequence[Path], output: Path) -> None:
    """把同目录的多个条目写入一个 zip（复用 filefilter 忽略规则）。"""
    from fcmd.core.filefilter import DEFAULT_IGNORE_EXTS, DEFAULT_IGNORE_SPEC, should_skip_parts

    base = sources[0].parent.resolve()
    if any(source.parent.resolve() != base for source in sources):
        raise ValueError("多选打包要求条目位于同一目录")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for source in sources:
            if source.is_dir() and not source.is_symlink():
                for child in sorted(source.rglob("*")):
                    if should_skip_parts(child.parts, DEFAULT_IGNORE_SPEC):
                        continue
                    arcname = child.relative_to(base).as_posix()
                    if child.is_dir():
                        archive.writestr(f"{arcname}/", b"")
                    elif child.suffix not in DEFAULT_IGNORE_EXTS:
                        archive.write(child, arcname)
            elif source.suffix not in DEFAULT_IGNORE_EXTS:
                archive.write(source, source.relative_to(base).as_posix())


def pack_entries(
    entries: Sequence[Path],
    output: Path,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """打包条目：单条目交给 archivex（支持 zip/tar/gz 等），多条目写 zip。"""
    sources = [Path(entry) for entry in entries]
    if not sources:
        raise ValueError("没有可打包的条目")
    missing = [source for source in sources if not (source.exists() or source.is_symlink())]
    if missing:
        raise FileNotFoundError(f"源不存在: {missing[0]}")
    output.parent.mkdir(parents=True, exist_ok=True)
    if len(sources) == 1:
        from fcmd.tools.archive.archivex import create_archive

        create_archive(sources[0], output)
    else:
        if output.suffix.casefold() != ".zip":
            raise ValueError("多选打包仅支持 .zip 格式")
        _write_zip(sources, output)
    if progress is not None:
        progress(1, 1, output.name)
    return output


def extract_archive_to(
    archive: Path,
    destination: Path,
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> Path:
    """解压归档到指定目录（复用 archivex 的多格式支持）。"""
    if not archive.is_file():
        raise FileNotFoundError(f"归档不存在: {archive}")
    from fcmd.tools.archive.archivex import extract_archive

    destination.mkdir(parents=True, exist_ok=True)
    extract_archive(archive, destination)
    if progress is not None:
        progress(1, 1, archive.name)
    return destination


# ============================================================================ #
# 哈希 / 搜索 / 预览（复用 hashbase、fileops、textio）
# ============================================================================ #
def hash_paths(
    paths: Sequence[Path],
    algorithm: str = "sha256",
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[HashResult]:
    """计算文件或目录下所有文件的哈希（目录复用 hashbase 遍历规则）。"""
    from fcmd.tools import hashbase

    hashbase.ensure_algorithm(algorithm)
    targets: list[Path] = []
    for path in paths:
        if path.is_dir():
            targets.extend(hashbase.iter_directory_files(path))
        elif path.is_file():
            targets.append(path)
    results: list[HashResult] = []
    total = len(targets)
    for index, target in enumerate(targets, start=1):
        results.append(HashResult(path=target, digest=hashbase.compute_file_hash(target, algorithm)))
        if progress is not None:
            progress(index, total, target.name)
    return results


def format_hash_report(results: Sequence[HashResult], algorithm: str) -> str:
    """把哈希结果格式化为 ``sha256sum`` 风格的文本。"""
    return "\n".join(f"{algorithm}  {result.digest}  {result.path}" for result in results)


def search_paths(
    directory: Path,
    pattern: str,
    *,
    content: bool = False,
    extension: str = "",
) -> list[SearchHit]:
    """搜索目录（复用 fileops 的文件名 glob / 内容正则实现）。"""
    if not pattern.strip():
        raise ValueError("搜索内容不能为空")
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    from fcmd.dsl.actions.fileops import search_by_content, search_by_name

    if content:
        suffix = extension.strip()
        if suffix and not suffix.startswith("."):
            suffix = f".{suffix}"
        hits = search_by_content(directory, pattern, extension=suffix)
        return [SearchHit(path=path, line=line, text=text) for path, line, text in hits]
    return [SearchHit(path=path) for path in search_by_name(directory, pattern)]


def format_search_hit(hit: SearchHit) -> str:
    """搜索结果的一行文本。"""
    if hit.line:
        return f"{hit.path}:{hit.line}: {hit.text}"
    return str(hit.path)


def load_preview(path: Path, *, max_lines: int = _MAX_PREVIEW_LINES) -> str:
    """读取文件预览文本；二进制文件抛 ``ValueError``，超长只取前若干行。"""
    from fcmd.core.shared.textio import read_text_lines

    lines = read_text_lines(path, binary_message="二进制文件，无法预览")
    if len(lines) > max_lines:
        return "".join(lines[:max_lines]) + f"\n…（仅显示前 {max_lines} 行）"
    return "".join(lines)


def open_externally(path: Path) -> None:
    """用系统默认程序打开文件（Windows / macOS / Linux）。"""
    target = str(path)
    if sys.platform.startswith("win"):
        os.startfile(target)
        return
    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ============================================================================ #
# Tk 界面
# ============================================================================ #
class FilePanel:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """单个目录面板：标签栏 + 路径栏 + 文件列表 + 状态行。"""

    def __init__(
        self,
        app: CmderWindow,
        parent: Any,
        side: str,
        tabs: Sequence[PanelState],
        tab_index: int = 0,
    ) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.side = side
        self.title = "左侧" if side == "left" else "右侧"
        self.tabs = list(tabs) or [PanelState(path=str(default_directory()))]
        self.tab_index = normalize_tab_index(tab_index, len(self.tabs))
        self._tab_signature: tuple[int, tuple[str, ...]] | None = None
        self.entries: list[FileEntry] = []
        self._index: dict[str, FileEntry] = {}
        self._filter = ""
        theme = app.theme
        spacing = theme.spacing

        self.frame = ttk.Frame(parent, padding=spacing.xs)
        self.title_label = ttk.Label(self.frame, text=self.title, style="Title.TLabel")
        self.title_label.pack(fill="x", pady=(0, spacing.xxs))

        self._tab_bar = ttk.Frame(self.frame)
        self._tab_bar.pack(fill="x", pady=(0, spacing.xxs))
        self._render_tabs()

        top = ttk.Frame(self.frame)
        top.pack(fill="x", pady=(0, spacing.xs))
        ttk.Button(top, text="←", width=3, style="Toolbutton", command=self.go_back).pack(side="left")
        ttk.Button(top, text="→", width=3, style="Toolbutton", command=self.go_forward).pack(side="left")
        ttk.Button(top, text="↑", width=3, style="Toolbutton", command=self.go_up).pack(side="left")
        ttk.Button(top, text="刷新", style="Toolbutton", command=self.refresh).pack(side="left", padx=(0, spacing.xs))
        self.path_var = tk.StringVar(value=self.state.path)
        self.path_entry = ttk.Entry(top, textvariable=self.path_var)
        self.path_entry.pack(side="left", fill="x", expand=True)
        self.path_entry.bind("<Return>", self._on_path_enter)
        self.path_entry.bind("<FocusIn>", lambda _event: app.set_active(self))

        filter_bar = ttk.Frame(self.frame)
        filter_bar.pack(fill="x", pady=(0, spacing.xs))
        ttk.Label(filter_bar, text="过滤：").pack(side="left")
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(filter_bar, textvariable=self.filter_var)
        self.filter_entry.pack(side="left", fill="x", expand=True)
        self.filter_entry.bind("<Escape>", lambda _event: self.clear_filter())
        self.filter_entry.bind("<Return>", lambda _event: self.focus_tree())
        self.filter_var.trace_add("write", lambda *_args: self._on_filter_change())
        ttk.Button(filter_bar, text="清除", width=5, style="Toolbutton", command=self.clear_filter).pack(side="left")

        list_frame = ttk.Frame(self.frame)
        list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            list_frame,
            columns=[column for column, _label, _width in COLUMNS],
            show="headings",
            selectmode="extended",
        )
        for column, label, width in COLUMNS:
            self.tree.heading(column, text=label, command=lambda c=column: self.sort_by(c))
            self.tree.column(column, width=width, anchor="w" if column == "name" else "e")
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.tag_configure("dir", foreground=theme.palette.accent)
        self.tree.tag_configure("hidden", foreground=theme.palette.muted)
        self.tree.bind("<Double-1>", self._on_activate)
        self.tree.bind("<Return>", self._on_activate)
        self.tree.bind("<BackSpace>", lambda _event: self.go_up())
        self.tree.bind("<Button-3>", app.show_context_menu)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<FocusIn>", lambda _event: app.set_active(self))
        self.tree.bind("<KP_Add>", lambda _event: self._select_by_mask())
        self.tree.bind("<KP_Subtract>", lambda _event: self._deselect_by_mask())
        self.tree.bind("<space>", lambda _event: self._show_size())

        self.status_var = tk.StringVar()
        ttk.Label(self.frame, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", pady=(spacing.xxs, 0))

    # ------------------------------------------------------------------ #
    # 导航
    # ------------------------------------------------------------------ #
    @property
    def state(self) -> PanelState:
        """当前标签的面板状态。"""
        return self.tabs[self.tab_index]

    def set_title_active(self, active: bool) -> None:
        """激活面板用标题样式高亮。"""
        self.title_label.configure(style="Title.TLabel" if active else "Muted.TLabel")

    # ------------------------------------------------------------------ #
    # 标签页
    # ------------------------------------------------------------------ #
    def _render_tabs(self) -> None:
        """重建标签栏：当前标签高亮，末尾为新建按钮（内容未变时跳过）。"""
        from tkinter import ttk

        signature = (self.tab_index, tuple(tab.path for tab in self.tabs))
        if signature == self._tab_signature:
            return
        self._tab_signature = signature
        for child in self._tab_bar.winfo_children():
            child.destroy()
        spacing = self.app.theme.spacing
        for index, tab in enumerate(self.tabs):
            label = panel_tab_title(tab.path) or "（空）"
            if len(label) > 24:
                label = "…" + label[-23:]
            ttk.Button(
                self._tab_bar,
                text=label,
                style="Accent.TButton" if index == self.tab_index else "Toolbutton",
                command=lambda i=index: self.select_tab(i),
            ).pack(side="left", padx=(0, spacing.xxs))
        ttk.Button(
            self._tab_bar,
            text="+",
            width=3,
            style="Toolbutton",
            command=self.new_tab,
        ).pack(side="left")

    def select_tab(self, index: int) -> None:
        """切换到指定标签并刷新列表。"""
        if not 0 <= index < len(self.tabs) or index == self.tab_index:
            return
        self.tab_index = index
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        self.app.set_status(f"{self.title}标签 {self.tab_index + 1}/{len(self.tabs)}：{self.state.path}")

    def new_tab(self, path: str | None = None) -> None:
        """新建标签页：默认沿用当前目录与排序方式。"""
        source = self.state
        target = path or source.path
        self.tabs.append(
            PanelState(
                path=target,
                sort_key=source.sort_key,
                descending=source.descending,
                history=[target],
                history_index=0,
            )
        )
        self.tab_index = len(self.tabs) - 1
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        self.app.set_status(f"{self.title}新建标签 {self.tab_index + 1}/{len(self.tabs)}：{target}")

    def close_tab(self) -> bool:
        """关闭当前标签页；仅剩一个标签时保留并提示，返回是否真的关闭。"""
        if len(self.tabs) == 1:
            self.app.set_status(f"{self.title}至少保留一个标签页，已取消关闭")
            return False
        closed = self.tabs.pop(self.tab_index)
        self.tab_index = min(self.tab_index, len(self.tabs) - 1)
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        name = panel_tab_title(closed.path) or closed.path
        self.app.set_status(f"已关闭标签「{name}」，当前 {self.tab_index + 1}/{len(self.tabs)}")
        return True

    def next_tab(self, delta: int = 1) -> None:
        """在当前面板的标签间循环切换。"""
        if len(self.tabs) <= 1:
            self.app.set_status(f"{self.title}只有一个标签页")
            return
        self.select_tab((self.tab_index + delta) % len(self.tabs))

    def refresh(self, select: str | None = None) -> None:
        """重新读取当前目录并恢复选择。"""
        self._render_tabs()
        directory = Path(self.state.path)
        try:
            entries = sort_entries(
                list_directory(directory, show_hidden=self.app.settings.show_hidden),
                sort_key=self.state.sort_key,
                descending=self.state.descending,
            )
        except OSError as exc:
            self.entries = []
            self._index = {}
            self.tree.delete(*self.tree.get_children())
            self.status_var.set(f"无法读取目录: {exc}")
            self.app.set_status(f"无法读取目录: {directory}")
            return
        previous = set(self.tree.selection())
        if select:
            previous = {select}
        if self._filter:
            visible = set(filter_names([entry.name for entry in entries], self._filter))
            entries = [entry for entry in entries if entry.name in visible]
        self.entries = entries
        self._index = {entry.name: entry for entry in entries}
        self.tree.delete(*self.tree.get_children())
        for entry in entries:
            tags: list[str] = []
            if entry.is_dir:
                tags.append("dir")
            if entry.hidden:
                tags.append("hidden")
            self.tree.insert(
                "",
                "end",
                iid=entry.name,
                values=(
                    entry.name,
                    format_size(entry.size, is_dir=entry.is_dir),
                    format_mtime(entry.mtime),
                    describe_attributes(entry),
                ),
                tags=tags,
            )
        existing = set(self.tree.get_children())
        for name in previous & existing:
            self.tree.selection_add(name)
            self.tree.see(name)
        self._update_status()

    def _update_status(self) -> None:
        total = len(self.entries)
        file_size = sum(entry.size for entry in self.entries if not entry.is_dir)
        text = f"{total} 个对象，共 {format_size(file_size)}"
        if self._filter:
            text = f"过滤「{self._filter}」→ {text}"
        selected = self.selected_entries()
        if selected:
            selected_size = sum(entry.size for entry in selected if not entry.is_dir)
            text += f" | 已选 {len(selected)} 个，{format_size(selected_size)}"
        self.status_var.set(text)

    def _on_select(self, _event: Any = None) -> None:
        self.app.set_active(self)
        self._update_status()

    def _on_path_enter(self, _event: Any = None) -> None:
        try:
            directory, select = resolve_path_input(self.path_var.get(), base=Path.cwd())
        except (OSError, ValueError) as exc:
            self.app.show_error("路径无效", str(exc))
            self.path_var.set(self.state.path)
            return
        self.set_path(directory)
        if select:
            self.select_name(select)

    def set_path(self, path: Path, *, push: bool = True) -> None:
        """切换到指定目录，默认写入历史。"""
        try:
            directory = path.resolve(strict=False)
        except OSError as exc:
            self.app.show_error("路径无效", str(exc))
            return
        if directory.is_file():
            directory = directory.parent
        if not directory.is_dir():
            self.app.show_error("路径无效", f"目录不存在: {directory}")
            return
        text = str(directory)
        if push and text != self.state.path:
            history, index = push_history(self.state.history, self.state.history_index, text)
            self.state.history = history
            self.state.history_index = index
        self.state.path = text
        self.path_var.set(text)
        self.refresh()

    def go_up(self) -> None:
        """进入父目录。"""
        parent = parent_directory(Path(self.state.path))
        if parent is not None:
            self.set_path(parent)

    def go_back(self) -> None:
        """后退到历史上一项。"""
        index = history_back(self.state.history, self.state.history_index)
        if index != self.state.history_index:
            self.state.history_index = index
            self.state.path = self.state.history[index]
            self.path_var.set(self.state.path)
            self.refresh()

    def go_forward(self) -> None:
        """前进到历史下一项。"""
        index = history_forward(self.state.history, self.state.history_index)
        if index != self.state.history_index:
            self.state.history_index = index
            self.state.path = self.state.history[index]
            self.path_var.set(self.state.path)
            self.refresh()

    # ------------------------------------------------------------------ #
    # 选择与排序
    # ------------------------------------------------------------------ #
    def sort_by(self, column: str) -> None:
        """点击列头排序：同列再次点击切换升降序。"""
        key = COLUMN_SORT.get(column, "name")
        if self.state.sort_key == key:
            self.state.descending = not self.state.descending
        else:
            self.state.sort_key = key
            self.state.descending = False
        selected = self.tree.selection()
        self.refresh(select=selected[0] if selected else None)

    def selected_entries(self) -> list[FileEntry]:
        """当前选中的条目。"""
        return [self._index[name] for name in self.tree.selection() if name in self._index]

    def select_name(self, name: str) -> None:
        """按名称选中并滚动到可见位置。"""
        if name in self.tree.get_children():
            self.tree.selection_set(name)
            self.tree.focus(name)
            self.tree.see(name)
            self._update_status()

    def select_all(self) -> None:
        """全选当前目录。"""
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)

    def select_names(self, names: Sequence[str], *, focus: bool = False) -> int:
        """在可见行中选择给定名称，返回实际选中数。"""
        visible = set(self.tree.get_children())
        targets = [name for name in names if name in visible]
        if targets:
            self.tree.selection_set(*targets)
            self.tree.see(targets[0])
        if focus:
            self.focus_tree()
        self._update_status()
        return len(targets)

    def _on_filter_change(self) -> None:
        """过滤框内容变化时重绘列表（只影响显示，不切换目录）。"""
        self._filter = self.filter_var.get().strip()
        self.refresh()

    def focus_filter(self) -> None:
        """把焦点移到快速过滤框并全选已有内容。"""
        self.filter_entry.focus_set()
        self.filter_entry.select_range(0, "end")

    def clear_filter(self) -> None:
        """清空快速过滤并回到文件列表。"""
        if self.filter_var.get():
            self.filter_var.set("")
        self.focus_tree()

    def apply_mask_selection(self, mask: str, *, select: bool) -> int:
        """按掩码增选（Num+）或取消选择（Num-），返回命中项数。"""
        targets = match_names(list(self.tree.get_children()), mask)
        if not targets:
            return 0
        if select:
            self.tree.selection_add(*targets)
        else:
            self.tree.selection_remove(*targets)
        self._update_status()
        return len(targets)

    def invert_selection(self) -> int:
        """反选当前可见条目，返回新的选中数量。"""
        targets = invert_names(list(self.tree.get_children()), self.tree.selection())
        self.tree.selection_set(*targets)
        self._update_status()
        return len(targets)

    def focus_tree(self) -> None:
        """把键盘焦点移到文件列表。"""
        self.tree.focus_set()

    def _select_by_mask(self) -> None:
        """Num+：先激活本面板，再按掩码增选。"""
        self.app.set_active(self)
        self.app.select_by_mask()

    def _deselect_by_mask(self) -> None:
        """Num-：先激活本面板，再按掩码取消选择。"""
        self.app.set_active(self)
        self.app.deselect_by_mask()

    def _show_size(self) -> None:
        """Space：先激活本面板，再统计选中项大小。"""
        self.app.set_active(self)
        self.app.show_selected_size()

    def _on_activate(self, _event: Any = None) -> None:
        self.app.set_active(self)
        selected = self.selected_entries()
        if selected:
            self.app.open_entry(selected[0])


class BulkRenameDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """批量重命名：规则 → 预览 → 冲突拦截 → 执行 → 本次运行内撤销。"""

    _CASE_CHOICES = (("keep", "保持"), ("lower", "小写"), ("upper", "大写"), ("title", "首字母大写"))

    def __init__(self, app: CmderWindow, directory: Path, entries: Sequence[FileEntry]) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.directory = directory
        self.sources = [RenameSource(entry.name, entry.is_dir) for entry in entries]
        self._plan: RenamePlan | None = None
        self._last_report: RenameReport | None = None

        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title(f"批量重命名 - {directory}")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=940, height=640)
        self.win.minsize(780, 500)
        frame = ttk.Frame(self.win, padding=spacing.md)
        frame.pack(fill="both", expand=True)

        form = ttk.LabelFrame(
            frame,
            text="规则（字面替换、{name}/{ext}/{n} 占位、大小写、扩展名）",
            padding=spacing.sm,
        )
        form.pack(fill="x", pady=(0, spacing.sm))
        form.grid_columnconfigure(1, weight=1)
        form.grid_columnconfigure(3, weight=1)

        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.template_var = tk.StringVar()
        self.start_var = tk.StringVar(value="1")
        self.step_var = tk.StringVar(value="1")
        self.digits_var = tk.StringVar(value="3")
        self.extension_var = tk.StringVar()
        self.case_var = tk.StringVar(value=self._CASE_CHOICES[0][1])

        ttk.Label(form, text="查找：").grid(row=0, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.find_var).grid(row=0, column=1, sticky="ew", padx=(0, spacing.sm))
        ttk.Label(form, text="替换：").grid(row=0, column=2, sticky="w")
        ttk.Entry(form, textvariable=self.replace_var).grid(row=0, column=3, sticky="ew")

        ttk.Label(form, text="模板：").grid(row=1, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.template_var).grid(row=1, column=1, sticky="ew", padx=(0, spacing.sm))
        ttk.Label(form, text="例：{name}_{n} 或 {name}").grid(row=1, column=2, columnspan=2, sticky="w")

        ttk.Label(form, text="序号：").grid(row=2, column=0, sticky="w")
        number_row = ttk.Frame(form)
        number_row.grid(row=2, column=1, sticky="ew", padx=(0, spacing.sm))
        ttk.Label(number_row, text="起始").pack(side="left")
        ttk.Entry(number_row, textvariable=self.start_var, width=6).pack(side="left", padx=(2, spacing.sm))
        ttk.Label(number_row, text="步长").pack(side="left")
        ttk.Entry(number_row, textvariable=self.step_var, width=6).pack(side="left", padx=(2, spacing.sm))
        ttk.Label(number_row, text="位数").pack(side="left")
        ttk.Entry(number_row, textvariable=self.digits_var, width=6).pack(side="left", padx=(2, 0))
        ttk.Label(form, text="大小写：").grid(row=2, column=2, sticky="w")
        ttk.Combobox(
            form,
            textvariable=self.case_var,
            state="readonly",
            width=12,
            values=[label for _, label in self._CASE_CHOICES],
        ).grid(row=2, column=3, sticky="w")

        ttk.Label(form, text="扩展名：").grid(row=3, column=0, sticky="w")
        ttk.Entry(form, textvariable=self.extension_var).grid(row=3, column=1, sticky="ew", padx=(0, spacing.sm))
        ttk.Label(form, text="留空不变，填 - 移除，其他值作为新扩展名").grid(row=3, column=2, columnspan=2, sticky="w")

        button_row = ttk.Frame(frame)
        button_row.pack(fill="x", pady=(0, spacing.sm))
        ttk.Button(button_row, text="预览", style="Accent.TButton", command=self.preview).pack(side="left")
        self.execute_button = ttk.Button(button_row, text="执行", command=self.execute, state="disabled")
        self.execute_button.pack(side="left", padx=spacing.sm)
        self.undo_button = ttk.Button(button_row, text="撤销上次执行", command=self.undo, state="disabled")
        self.undo_button.pack(side="left")
        ttk.Label(button_row, text="撤销仅本次运行有效，关闭窗口后不可用", style="Muted.TLabel").pack(
            side="left", padx=spacing.sm
        )
        ttk.Button(button_row, text="关闭", command=self.win.destroy).pack(side="right")

        result_frame = ttk.Frame(frame)
        result_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(result_frame, columns=("old", "new", "status"), show="headings")
        self.tree.heading("old", text="原名称")
        self.tree.heading("new", text="新名称")
        self.tree.heading("status", text="状态")
        self.tree.column("old", width=320)
        self.tree.column("new", width=320)
        self.tree.column("status", width=240)
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.status_var = tk.StringVar(value=f"已选 {len(self.sources)} 项；填写规则后点“预览”")
        ttk.Label(frame, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", pady=(spacing.xs, 0))

        self.preview()

    def _options(self) -> BulkRenameOptions:
        def integer(text: str, label: str) -> int:
            try:
                return int(text.strip())
            except ValueError as exc:
                raise ValueError(f"{label}必须是整数") from exc

        case = next((key for key, label in self._CASE_CHOICES if label == self.case_var.get()), "keep")
        return BulkRenameOptions(
            find=self.find_var.get(),
            replace=self.replace_var.get(),
            template=self.template_var.get().strip(),
            start=integer(self.start_var.get(), "起始序号"),
            step=integer(self.step_var.get(), "步长"),
            digits=integer(self.digits_var.get(), "位数"),
            case=case,
            extension=self.extension_var.get().strip(),
        )

    def preview(self) -> None:
        """按当前规则刷新预览；有冲突时禁用执行。"""
        try:
            options = self._options()
        except ValueError as exc:
            self.status_var.set(f"规则无效：{exc}")
            return
        self._plan = plan_bulk_rename(self.directory, self.sources, options)
        self.tree.delete(*self.tree.get_children())
        for item in self._plan.items:
            self.tree.insert("", "end", values=(item.source.name, item.new_name, self._status_text(item)))
        changes = len(self._plan.changes)
        conflicts = len(self._plan.conflicts)
        unchanged = len(self._plan.items) - changes - conflicts
        self.status_var.set(f"预览：可改名 {changes} 项，未变化 {unchanged} 项，冲突 {conflicts} 项")
        self.execute_button.configure(state="normal" if changes and not conflicts else "disabled")

    @staticmethod
    def _status_text(item: RenameItem) -> str:
        if item.status == "ok":
            return "将改名"
        if item.status == "unchanged":
            return "未变化"
        return f"冲突：{item.note}"

    def execute(self) -> None:
        """确认后执行预览中无冲突的改名。"""
        if self.app.busy or self._plan is None:
            return
        changes = self._plan.changes
        if not changes or self._plan.conflicts:
            return
        if not self.app._messagebox.askyesno("批量重命名", f"确定执行 {len(changes)} 项改名吗？", parent=self.win):
            return
        plan = self._plan

        def task() -> RenameReport:
            return apply_rename_plan(plan, progress=self.app._progress_callback)

        def done(report: RenameReport) -> None:
            self._last_report = report if report.completed else None
            self.app.refresh_both()
            summary = f"改名完成：成功 {len(report.completed)}，失败 {len(report.errors)}"
            self.status_var.set(summary)
            self.tree.delete(*self.tree.get_children())
            self.execute_button.configure(state="disabled")
            self.undo_button.configure(state="normal" if self._last_report else "disabled")
            if report.errors:
                details = "\n".join(f"{source.name}: {error}" for source, error in report.errors[:_MAX_ERROR_DETAILS])
                self.app._messagebox.showwarning("批量重命名（有失败）", f"{summary}\n\n{details}", parent=self.win)

        self.app.start_worker(task, done, title=f"批量重命名 {len(changes)} 项")

    def undo(self) -> None:
        """撤销本次运行内最近一次执行。"""
        if self.app.busy or self._last_report is None:
            return
        report = self._last_report

        def task() -> RenameReport:
            return undo_rename(report, progress=self.app._progress_callback)

        def done(result: RenameReport) -> None:
            self.app.refresh_both()
            self.status_var.set(f"已撤销 {len(result.completed)} 项（仅本次运行有效）")
            if result.errors:
                details = "\n".join(f"{path.name}: {error}" for path, error in result.errors[:_MAX_ERROR_DETAILS])
                self.app._messagebox.showwarning("撤销（有失败）", details, parent=self.win)
            if len(result.completed) == len(report.completed):
                self._last_report = None
            self.undo_button.configure(state="normal" if self._last_report else "disabled")
            self.preview()

        self.app.start_worker(task, done, title=f"撤销改名 {len(report.completed)} 项")


class SearchDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """文件名 / 内容搜索对话框。"""

    def __init__(self, app: CmderWindow, directory: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.directory = directory
        self._tk = tk
        self._ttk = ttk
        self._hits: list[SearchHit] = []

        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title(f"搜索 - {directory}")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=900, height=560)
        self.win.minsize(700, 420)
        frame = ttk.Frame(self.win, padding=spacing.md)
        frame.pack(fill="both", expand=True)

        form = ttk.Frame(frame)
        form.pack(fill="x", pady=(0, spacing.sm))
        ttk.Label(form, text="模式：").pack(side="left")
        self.pattern_var = tk.StringVar()
        pattern_entry = ttk.Entry(form, textvariable=self.pattern_var)
        pattern_entry.pack(side="left", fill="x", expand=True)
        pattern_entry.focus_set()
        pattern_entry.bind("<Return>", lambda _event: self.run_search())
        self.content_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="内容", variable=self.content_var).pack(side="left", padx=spacing.sm)
        ttk.Label(form, text="扩展名：").pack(side="left")
        self.extension_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.extension_var, width=10).pack(side="left", padx=(0, spacing.sm))
        self.search_button = ttk.Button(form, text="搜索", style="Accent.TButton", command=self.run_search)
        self.search_button.pack(side="left")

        result_frame = ttk.Frame(frame)
        result_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(result_frame, columns=("path", "line", "text"), show="headings")
        self.tree.heading("path", text="路径")
        self.tree.heading("line", text="行")
        self.tree.heading("text", text="内容")
        self.tree.column("path", width=420)
        self.tree.column("line", width=70, anchor="e")
        self.tree.column("text", width=340)
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._reveal_selected)

        action_bar = ttk.Frame(frame)
        action_bar.pack(fill="x", pady=(spacing.xs, 0))
        ttk.Button(action_bar, text="打开所在目录", command=self._reveal_selected).pack(side="left")
        ttk.Button(action_bar, text="复制…", command=lambda: self._transfer(move=False)).pack(
            side="left", padx=(spacing.xs, 0)
        )
        ttk.Button(action_bar, text="移动…", command=lambda: self._transfer(move=True)).pack(
            side="left", padx=(spacing.xs, 0)
        )
        ttk.Button(action_bar, text="删除到回收站", command=self._delete).pack(side="left", padx=(spacing.xs, 0))
        ttk.Button(action_bar, text="加入面板选择", command=self._adopt).pack(side="left", padx=(spacing.xs, 0))
        ttk.Button(action_bar, text="关闭", command=self.win.destroy).pack(side="right")

        self.status_var = tk.StringVar(value="输入模式后回车或点击搜索")
        ttk.Label(frame, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", pady=(spacing.xs, 0))

    def run_search(self) -> None:
        """启动后台搜索。"""
        if self.app.busy:
            self.status_var.set("已有后台任务在运行，请稍候")
            return
        pattern = self.pattern_var.get().strip()
        if not pattern:
            self.status_var.set("搜索模式不能为空")
            return
        self.search_button.configure(state="disabled")
        self.status_var.set("搜索中...")
        self.app.start_search(
            self.directory,
            pattern,
            content=bool(self.content_var.get()),
            extension=self.extension_var.get().strip(),
            on_done=self._show_results,
            on_error=self._search_failed,
        )

    def _show_results(self, hits: list[SearchHit]) -> None:
        self._hits = hits
        self.tree.delete(*self.tree.get_children())
        for index, hit in enumerate(hits):
            self.tree.insert("", "end", iid=str(index), values=(str(hit.path), hit.line or "", hit.text))
        self.search_button.configure(state="normal")
        self.status_var.set(f"找到 {len(hits)} 个结果（双击定位）")

    def _search_failed(self, message: str) -> None:
        self.search_button.configure(state="normal")
        self.status_var.set(f"搜索失败: {message}")

    def _selected_paths(self) -> list[Path]:
        """选中结果对应的去重路径（内容搜索同一文件可能命中多行）。"""
        paths: list[Path] = []
        for iid in self.tree.selection():
            path = self._hits[int(iid)].path
            if path not in paths:
                paths.append(path)
        return paths

    def _reveal_selected(self, _event: Any = None) -> None:
        paths = self._selected_paths()
        if not paths:
            self.status_var.set("请先选择结果")
            return
        self.app.reveal_path(paths[0])
        self.win.destroy()

    def _transfer(self, *, move: bool) -> None:
        paths = self._selected_paths()
        if not paths:
            self.status_var.set("请先选择结果")
            return
        if self.app.transfer_paths(paths, move=move):
            self.win.destroy()

    def _delete(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self.status_var.set("请先选择结果")
            return
        if self.app.delete_paths(paths):
            self.win.destroy()

    def _adopt(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self.status_var.set("请先选择结果")
            return
        self.status_var.set(self.app.adopt_paths(paths))
        self.win.destroy()


class BookmarkDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """书签管理：列表、添加当前目录、删除，双击打开到打开时的活动面板。"""

    def __init__(self, app: CmderWindow, panel: FilePanel) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.panel = panel
        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title("书签")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=680, height=440)
        self.win.minsize(520, 320)
        frame = ttk.Frame(self.win, padding=spacing.md)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text=f"双击打开到{panel.title}（打开本对话框时的活动面板）",
            style="Muted.TLabel",
        ).pack(fill="x", pady=(0, spacing.xs))

        list_frame = ttk.Frame(frame)
        list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(list_frame, columns=("path",), show="headings", selectmode="browse")
        self.tree.heading("path", text="路径")
        self.tree.column("path", width=600)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda _event: self.open_selected())
        self.tree.bind("<Return>", lambda _event: self.open_selected())

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(buttons, text="打开", style="Accent.TButton", command=self.open_selected).pack(side="left")
        ttk.Button(buttons, text="添加当前目录", command=self.add_current).pack(side="left", padx=spacing.xs)
        ttk.Button(buttons, text="删除", command=self.remove_selected).pack(side="left")
        ttk.Button(buttons, text="关闭", command=self.win.destroy).pack(side="right")
        self._reload()

    def _reload(self) -> None:
        """按当前书签列表重绘。"""
        self.tree.delete(*self.tree.get_children())
        for index, path in enumerate(self.app.settings.bookmarks):
            self.tree.insert("", "end", iid=str(index), values=(path,))

    def add_current(self) -> None:
        """把打开本对话框时的面板目录加入书签。"""
        path = self.panel.state.path
        bookmarks = self.app.settings.bookmarks
        if path in bookmarks:
            self.app.set_status(f"书签已存在：{path}")
            return
        bookmarks.append(path)
        self._reload()
        self.tree.selection_set(str(len(bookmarks) - 1))
        self.tree.see(str(len(bookmarks) - 1))
        self.app.set_status(f"已添加书签：{path}")

    def remove_selected(self) -> None:
        """删除选中的书签。"""
        selection = self.tree.selection()
        if not selection:
            self.app.set_status("请先选择书签")
            return
        index = int(selection[0])
        bookmarks = self.app.settings.bookmarks
        if 0 <= index < len(bookmarks):
            removed = bookmarks.pop(index)
            self._reload()
            self.app.set_status(f"已删除书签：{removed}")

    def open_selected(self) -> None:
        """打开选中书签到面板；目录不存在时提示且不关闭对话框。"""
        selection = self.tree.selection()
        if not selection:
            self.app.set_status("请先选择书签")
            return
        index = int(selection[0])
        bookmarks = self.app.settings.bookmarks
        if not 0 <= index < len(bookmarks):
            return
        path = Path(bookmarks[index])
        if not path.is_dir():
            self.app.show_error("无法打开书签", f"目录不存在: {path}")
            return
        self.panel.set_path(path)
        self.app.set_active(self.panel)
        self.app.set_status(f"已打开书签：{path}")
        self.win.destroy()


class TransferQueueDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """传输队列窗口：任务与逐项状态、失败原因、取消与重试。"""

    def __init__(self, app: CmderWindow) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title("传输队列")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=1020, height=640)
        self.win.minsize(780, 440)
        frame = ttk.Frame(self.win, padding=spacing.md)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="队列串行执行；取消按项生效：当前文件传完后停止，已完成项保留。",
            style="Muted.TLabel",
        ).pack(fill="x", pady=(0, spacing.xs))

        task_frame = ttk.Frame(frame)
        task_frame.pack(fill="both", expand=True)
        self.tasks = ttk.Treeview(
            task_frame,
            columns=("id", "action", "destination", "progress", "status", "detail"),
            show="headings",
            selectmode="browse",
        )
        for column, title, width in (
            ("id", "#", 44),
            ("action", "动作", 60),
            ("destination", "目标目录", 320),
            ("progress", "进度", 80),
            ("status", "状态", 90),
            ("detail", "说明", 320),
        ):
            self.tasks.heading(column, text=title)
            self.tasks.column(column, width=width, anchor="w")
        task_scroll = ttk.Scrollbar(task_frame, orient="vertical", command=self.tasks.yview)
        self.tasks.configure(yscrollcommand=task_scroll.set)
        self.tasks.pack(side="left", fill="both", expand=True)
        task_scroll.pack(side="right", fill="y")
        self.tasks.bind("<<TreeviewSelect>>", lambda _event: self._refresh_items())

        ttk.Label(frame, text="选中任务的条目明细", style="Muted.TLabel").pack(fill="x", pady=(spacing.sm, spacing.xxs))
        item_frame = ttk.Frame(frame)
        item_frame.pack(fill="both", expand=True)
        self.items = ttk.Treeview(
            item_frame,
            columns=("source", "target", "status", "error"),
            show="headings",
            selectmode="extended",
        )
        for column, title, width in (
            ("source", "来源", 320),
            ("target", "目标", 360),
            ("status", "状态", 90),
            ("error", "失败原因", 260),
        ):
            self.items.heading(column, text=title)
            self.items.column(column, width=width, anchor="w")
        item_scroll = ttk.Scrollbar(item_frame, orient="vertical", command=self.items.yview)
        self.items.configure(yscrollcommand=item_scroll.set)
        self.items.pack(side="left", fill="both", expand=True)
        item_scroll.pack(side="right", fill="y")

        buttons = ttk.Frame(frame)
        buttons.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(buttons, text="取消选中任务", command=self.cancel_selected).pack(side="left")
        ttk.Button(buttons, text="重试选中失败项", command=self.retry_selected).pack(side="left", padx=spacing.xs)
        ttk.Button(buttons, text="重试全部失败项", command=self.retry_all).pack(side="left")
        ttk.Button(buttons, text="关闭", command=self.win.destroy).pack(side="right")
        self.refresh()

    def refresh(self) -> None:
        """重绘任务列表与当前任务的条目明细。"""
        selection = self.tasks.selection()
        self.tasks.delete(*self.tasks.get_children())
        for task in self.app.transfer_queue.tasks:
            self.tasks.insert(
                "",
                "end",
                iid=str(task.task_id),
                values=(
                    task.task_id,
                    task.action,
                    str(task.destination),
                    f"{task.done_count}/{task.total}",
                    task.status_text,
                    self._task_detail(task),
                ),
            )
        if selection and self.tasks.exists(selection[0]):
            self.tasks.selection_set(selection[0])
        self._refresh_items()

    @staticmethod
    def _task_detail(task: QueueTask) -> str:
        """任务行说明：清理失败 > 首个失败原因 > 跳过数。"""
        if task.cleanup_errors:
            return f"半成品清理失败 {len(task.cleanup_errors)} 项"
        if task.failed_count:
            first = next(item for item in task.items if item.status == ITEM_FAILED)
            return first.error
        if task.skipped:
            return f"跳过 {len(task.skipped)} 项（同路径或目录自包含）"
        return ""

    def _refresh_items(self) -> None:
        """按当前选中任务重绘明细。"""
        selection = self.tasks.selection()
        self.items.delete(*self.items.get_children())
        if not selection:
            return
        task = self.app.transfer_queue.get(int(selection[0]))
        if task is None:
            return
        for index, item in enumerate(task.items):
            self.items.insert(
                "",
                "end",
                iid=str(index),
                values=(str(item.source), str(item.target), item_status_text(item), item.error),
            )

    def _selected_task(self) -> QueueTask | None:
        selection = self.tasks.selection()
        if not selection:
            return None
        return self.app.transfer_queue.get(int(selection[0]))

    def cancel_selected(self) -> None:
        """取消选中任务。"""
        task = self._selected_task()
        if task is None:
            self.app.set_status("请先选择任务")
            return
        self.app.cancel_queue_task(task.task_id)

    def retry_selected(self) -> None:
        """只重试明细中选中的失败项。"""
        task = self._selected_task()
        if task is None:
            self.app.set_status("请先选择任务")
            return
        chosen = [
            task.items[int(iid)] for iid in self.items.selection() if iid.isdigit() and 0 <= int(iid) < task.total
        ]
        if not chosen:
            self.app.set_status("请先在明细里选择失败项")
            return
        self.app.retry_queue_items(task.task_id, chosen)

    def retry_all(self) -> None:
        """重试选中任务的全部失败项。"""
        task = self._selected_task()
        if task is None:
            self.app.set_status("请先选择任务")
            return
        self.app.retry_queue_items(task.task_id, None)


class CompareDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """只读目录比较：选项 + 结果表，不修改任何文件、不跟随符号链接。"""

    def __init__(self, app: CmderWindow) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.left_root = app.left.state.path
        self.right_root = app.right.state.path
        self.report: CompareReport | None = None
        self._filter = "all"
        self._running = False
        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title("目录比较（只读）")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=1040, height=640)
        self.win.minsize(780, 440)
        frame = ttk.Frame(self.win, padding=spacing.md)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"左：{self.left_root}", style="Muted.TLabel").pack(fill="x")
        ttk.Label(frame, text=f"右：{self.right_root}", style="Muted.TLabel").pack(fill="x", pady=(0, spacing.xs))

        options = ttk.Frame(frame)
        options.pack(fill="x", pady=(0, spacing.xs))
        self.recursive_var = tk.BooleanVar(value=True)
        self.hidden_var = tk.BooleanVar(value=False)
        self.deep_var = tk.BooleanVar(value=False)
        self.tolerance_var = tk.StringVar(value="2.0")
        ttk.Checkbutton(options, text="递归子目录", variable=self.recursive_var).pack(side="left")
        ttk.Checkbutton(options, text="包含隐藏项", variable=self.hidden_var).pack(side="left", padx=spacing.sm)
        ttk.Checkbutton(options, text="深度比较（逐字节哈希）", variable=self.deep_var).pack(
            side="left", padx=(0, spacing.sm)
        )
        ttk.Label(options, text="时间容差（秒）").pack(side="left")
        ttk.Entry(options, textvariable=self.tolerance_var, width=6).pack(side="left", padx=(spacing.xxs, spacing.sm))
        self.start_button = ttk.Button(options, text="开始比较", style="Accent.TButton", command=self.start_compare)
        self.start_button.pack(side="left")

        filters = ttk.Frame(frame)
        filters.pack(fill="x", pady=(0, spacing.xxs))
        for value, label in (
            ("all", "全部"),
            (COMPARE_LEFT_ONLY, "仅左侧"),
            (COMPARE_RIGHT_ONLY, "仅右侧"),
            (COMPARE_DIFFERENT, "内容不同"),
            (COMPARE_SAME, "相同"),
        ):
            ttk.Button(
                filters,
                text=label,
                style="Toolbutton",
                command=lambda selected=value: self.set_filter(selected),
            ).pack(side="left", padx=(0, spacing.xxs))

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("relative", "left", "right", "status", "reason"),
            show="headings",
            selectmode="browse",
        )
        for column, title, width, anchor in (
            ("relative", "相对路径", 380, "w"),
            ("left", "左侧", 110, "e"),
            ("right", "右侧", 110, "e"),
            ("status", "类别", 100, "w"),
            ("reason", "说明", 260, "w"),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(spacing.sm, 0))
        self.status_var = tk.StringVar(value="尚未比较")
        ttk.Label(bottom, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.win.destroy).pack(side="right")
        self.sync_button = ttk.Button(bottom, text="同步…", command=self.open_sync, state="disabled")
        self.sync_button.pack(side="right", padx=(0, spacing.xs))

    def open_sync(self) -> None:
        """带着当前比较结果进入同步预演。"""
        if self.report is None:
            self.status_var.set("请先执行比较，再进入同步")
            return
        self.app.open_sync(self.report, self._collect_options().mtime_tolerance)

    def set_filter(self, status: str) -> None:
        """切换类别过滤并重绘。"""
        self._filter = status
        if self.report is not None:
            self._render(self.report)

    def _collect_options(self) -> CompareOptions:
        """读取界面选项；非法容差回退 2 秒。"""
        try:
            tolerance = float(self.tolerance_var.get())
        except ValueError:
            tolerance = 2.0
        return CompareOptions(
            recursive=bool(self.recursive_var.get()),
            include_hidden=bool(self.hidden_var.get()),
            deep=bool(self.deep_var.get()),
            mtime_tolerance=max(tolerance, 0.0),
        )

    def start_compare(self) -> None:
        """后台执行只读比较，完成后在结果表展示。"""
        if self._running:
            self.status_var.set("比较进行中…")
            return
        if self.app._busy:
            self.status_var.set("已有后台任务在运行，请稍后再比较")
            return
        options = self._collect_options()
        left_root = Path(self.left_root)
        right_root = Path(self.right_root)
        self._running = True
        self.status_var.set("正在比较…")
        self.start_button.configure(state="disabled")
        self.sync_button.configure(state="disabled")

        def task() -> CompareReport:
            return compare_trees(left_root, right_root, options, progress=self.app._progress_callback)

        def done(report: CompareReport) -> None:
            self._running = False
            self.start_button.configure(state="normal")
            self.sync_button.configure(state="normal")
            self.report = report
            self._render(report)
            if report.errors:
                details = "\n".join(f"{path}: {message}" for path, message in report.errors[:_MAX_ERROR_DETAILS])
                self.app._messagebox.showwarning(
                    "比较完成（有读取失败）",
                    f"共 {len(report.errors)} 项无法读取：\n\n{details}",
                    parent=self.win,
                )

        self.app.start_worker(task, done, title="目录比较")

    def _render(self, report: CompareReport) -> None:
        """按当前过滤条件重绘结果并更新汇总。"""
        self.tree.delete(*self.tree.get_children())
        for entry in report.filtered(self._filter):
            self.tree.insert(
                "",
                "end",
                values=(
                    entry.relative,
                    self._side_text(entry.left, entry.left_size, entry.left_is_dir),
                    self._side_text(entry.right, entry.right_size, entry.right_is_dir),
                    COMPARE_STATUS_TEXT.get(entry.status, entry.status),
                    entry.reason,
                ),
            )
        counts = report.counts()
        summary = (
            f"共 {counts['total']} 项：仅左侧 {counts[COMPARE_LEFT_ONLY]}，"
            f"仅右侧 {counts[COMPARE_RIGHT_ONLY]}，内容不同 {counts[COMPARE_DIFFERENT]}，"
            f"相同 {counts[COMPARE_SAME]}"
        )
        if report.errors:
            summary += f"；读取失败 {len(report.errors)} 项"
        self.status_var.set(summary)

    @staticmethod
    def _side_text(path: Path | None, size: int | None, is_dir: bool) -> str:
        """侧栏展示：目录显示「目录」，文件显示大小。"""
        if path is None:
            return ""
        if is_dir:
            return "目录"
        return format_size(size or 0)


class SyncDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """同步预演与执行：复制/覆盖入传输队列，删除单独逐项执行。"""

    def __init__(self, app: CmderWindow, report: CompareReport, tolerance: float = 2.0) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.report = report
        self.tolerance = tolerance
        self.plan: SyncPlan | None = None
        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title("目录同步（预演）")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=1120, height=700)
        self.win.minsize(860, 480)
        frame = ttk.Frame(self.win, padding=spacing.md)
        frame.pack(fill="both", expand=True)

        ttk.Label(frame, text=f"左：{report.left_root}", style="Muted.TLabel").pack(fill="x")
        ttk.Label(frame, text=f"右：{report.right_root}", style="Muted.TLabel").pack(fill="x", pady=(0, spacing.xs))

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=(0, spacing.xs))
        self.direction_var = tk.StringVar(value=SYNC_LTR)
        ttk.Radiobutton(
            controls, text="左 → 右", value=SYNC_LTR, variable=self.direction_var, command=self.refresh_plan
        ).pack(side="left")
        ttk.Radiobutton(
            controls, text="右 → 左", value=SYNC_RTL, variable=self.direction_var, command=self.refresh_plan
        ).pack(side="left", padx=spacing.sm)
        self.delete_var = tk.BooleanVar(value=False)
        self.delete_dirs_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            controls, text="同时删除目标端多余文件", variable=self.delete_var, command=self._on_delete_toggle
        ).pack(side="left", padx=(spacing.lg, 0))
        self.delete_dirs_check = ttk.Checkbutton(
            controls, text="允许删除多余目录及其内容", variable=self.delete_dirs_var, command=self.refresh_plan
        )
        self.delete_dirs_check.pack(side="left", padx=spacing.sm)
        self.delete_dirs_check.configure(state="disabled")

        note = "执行前会重新校验目标端的大小与时间，与预演不一致的项将跳过并在结果中列出。"
        ttk.Label(frame, text=note, style="Muted.TLabel").pack(fill="x", pady=(0, spacing.xxs))

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("action", "relative", "source", "target", "note"),
            show="headings",
            selectmode="browse",
        )
        for column, title, width, anchor in (
            ("action", "操作", 80, "w"),
            ("relative", "相对路径", 260, "w"),
            ("source", "源", 320, "w"),
            ("target", "目标", 320, "w"),
            ("note", "说明", 180, "w"),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        bottom = ttk.Frame(frame)
        bottom.pack(fill="x", pady=(spacing.sm, 0))
        self.status_var = tk.StringVar(value="")
        ttk.Label(bottom, textvariable=self.status_var, style="Muted.TLabel").pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.win.destroy).pack(side="right")
        self.start_button = ttk.Button(bottom, text="开始执行", style="Accent.TButton", command=self.execute)
        self.start_button.pack(side="right", padx=(0, spacing.xs))
        ttk.Button(bottom, text="重新预演", command=self.refresh_plan).pack(side="right", padx=(0, spacing.xs))
        self.refresh_plan()

    def _on_delete_toggle(self) -> None:
        """「删除多余文件」控制目录删除勾选框的可用性。"""
        if self.delete_var.get():
            self.delete_dirs_check.configure(state="normal")
        else:
            self.delete_dirs_var.set(False)
            self.delete_dirs_check.configure(state="disabled")
        self.refresh_plan()

    def refresh_plan(self) -> None:
        """按当前方向与勾选项重新生成预演。"""
        options = SyncOptions(
            direction=self.direction_var.get(),
            delete_target_extra=bool(self.delete_var.get()),
            delete_dirs=bool(self.delete_dirs_var.get()),
            mtime_tolerance=self.tolerance,
        )
        self.plan = build_sync_plan(self.report, options)
        self._render(self.plan)

    def _render(self, plan: SyncPlan) -> None:
        """重绘预演明细与汇总。"""
        self.tree.delete(*self.tree.get_children())
        for item in plan.copies:
            self.tree.insert(
                "",
                "end",
                values=(
                    "覆盖" if item.overwrite else "复制",
                    item.relative,
                    str(item.source or ""),
                    str(item.target),
                    "目录" if item.is_dir else ("覆盖已有文件" if item.overwrite else "新建文件"),
                ),
            )
        for item in plan.deletes:
            self.tree.insert(
                "",
                "end",
                values=(
                    "删除",
                    item.relative,
                    "",
                    str(item.target),
                    "目录" if item.is_dir else f"文件，{format_size(item.target_size or 0)}",
                ),
            )
        for item in plan.skipped:
            self.tree.insert(
                "",
                "end",
                values=("跳过", item.relative, "", str(item.target), item.skip_reason),
            )
        counts = plan.counts()
        parts = [f"将复制 {counts['copy_new']} 个文件，覆盖 {counts['copy_overwrite']} 个文件"]
        if counts["delete"]:
            parts.append(f"将删除 {counts['delete']} 项（目录 {counts['delete_dirs']} 个）")
        else:
            extra = sum(1 for item in plan.skipped if item.action == SYNC_DELETE)
            if extra:
                parts.append(f"目标端多余 {extra} 项（默认不删除）")
        if counts["skipped"]:
            parts.append(f"跳过 {counts['skipped']} 项")
        parts.append(f"相同 {counts['same']} 项")
        self.status_var.set("；".join(parts))
        self.start_button.configure(state="normal" if (plan.copies or plan.deletes) else "disabled")

    def execute(self) -> None:
        """二次确认后执行；删除前再单独确认一次。"""
        plan = self.plan
        if plan is None or not (plan.copies or plan.deletes):
            return
        counts = plan.counts()
        lines = [f"将执行以下操作（{plan.source_root} → {plan.target_root}）："]
        if plan.copies:
            lines.append(f"- 复制 {counts['copy_new']} 个新文件")
            lines.append(f"- 覆盖 {counts['copy_overwrite']} 个已有文件")
        if plan.deletes:
            lines.append(f"- 删除 {counts['delete']} 项（已勾选，移入回收站）")
        if plan.skipped:
            lines.append(f"- 跳过 {counts['skipped']} 项（符号链接 / 未勾选目录等）")
        lines += [
            "",
            "执行前会重新校验每个目标文件的大小与修改时间；与预演不一致的项将跳过，并在结果里单独列出。",
        ]
        if plan.deletes:
            lines.append("删除不可撤销（回收站可还原）。")
        if not self.app._messagebox.askokcancel("确认执行同步", "\n".join(lines), parent=self.win):
            return
        if plan.deletes and not self._confirm_deletes(plan):
            return
        self._run(plan)

    def _confirm_deletes(self, plan: SyncPlan) -> bool:
        """删除单独确认：逐条列出完整路径与总数。"""
        side = "右侧" if plan.direction == SYNC_LTR else "左侧"
        counts = plan.counts()
        lines = ["确认删除目标端多余项", "", f"即将从{side}移入回收站以下 {len(plan.deletes)} 项：", ""]
        for index, item in enumerate(plan.deletes, start=1):
            kind = "目录" if item.is_dir else f"文件，{format_size(item.target_size or 0)}"
            lines.append(f"{index}. {item.target}（{kind}）")
        lines += [
            "",
            f"共 {len(plan.deletes)} 项，其中目录 {counts['delete_dirs']} 个。",
            "删除不可撤销（回收站可还原）。",
            "删除逐项执行；任一项失败立即停止，并汇报已完成与未执行部分。",
            "回收站不可用时整批中止，不会改为永久删除。",
        ]
        return bool(self.app._messagebox.askokcancel("确认删除目标端多余项", "\n".join(lines), parent=self.win))

    def _run(self, plan: SyncPlan) -> None:
        """执行前复核，复制入队，删除逐项执行并汇报。"""
        ready_copies, skipped_copies = revalidate_sync_items(plan.copies, mtime_tolerance=self.tolerance)
        ready_deletes, skipped_deletes = revalidate_sync_items(plan.deletes, mtime_tolerance=self.tolerance)
        enqueue_ok = True
        if ready_copies:
            items = [
                TransferItem(source=item.source, target=item.target, overwrite=item.overwrite)
                for item in ready_copies
                if item.source is not None
            ]
            enqueue_ok = self.app.enqueue_transfer(items, plan.target_root, move=False)
        delete_report = SyncDeleteReport()
        if enqueue_ok and ready_deletes:
            delete_report = execute_sync_deletes(ready_deletes)
        self.app.refresh_both()
        self.start_button.configure(state="disabled")
        self.status_var.set("已执行；复制任务在传输队列中继续，完成后可重新预演")

        skipped = skipped_copies + skipped_deletes
        text = "\n".join(
            self._copy_result_lines(ready_copies, enqueue_ok)
            + self._delete_result_lines(plan, delete_report, enqueue_ok)
            + self._skipped_result_lines(skipped)
        )
        text = text or "没有可执行的操作（全部被跳过）。"
        if delete_report.failures:
            self.app._messagebox.showwarning("同步结果（删除失败）", text, parent=self.win)
        elif skipped:
            self.app._messagebox.showwarning("同步结果（有跳过项）", text, parent=self.win)
        else:
            self.app._messagebox.showinfo("同步结果", text, parent=self.win)

    @staticmethod
    def _copy_result_lines(copies: list[SyncItem], enqueue_ok: bool) -> list[str]:
        """复制入队结果行。"""
        if not copies:
            return []
        if not enqueue_ok:
            return ["复制未入队：目标与队列中未完成任务冲突"]
        new_count = sum(1 for item in copies if not item.overwrite)
        return [f"复制入队：{len(copies)} 项（新建 {new_count}，覆盖 {len(copies) - new_count}）"]

    @staticmethod
    def _delete_result_lines(plan: SyncPlan, report: SyncDeleteReport, enqueue_ok: bool) -> list[str]:
        """删除执行结果行；失败与未执行都如实列出。"""
        lines: list[str] = []
        if not enqueue_ok and plan.deletes:
            lines.append(f"删除未执行：{len(plan.deletes)} 项（复制未入队，未做删除）")
        if report.completed:
            lines.append(f"删除完成：{len(report.completed)} 项")
        for item, error in report.failures:
            lines.append(f"删除失败：{item.target}：{error}")
        if report.not_executed:
            lines.append(f"删除未执行：{len(report.not_executed)} 项（失败即停）")
        return lines

    @staticmethod
    def _skipped_result_lines(skipped: list[tuple[SyncItem, str]]) -> list[str]:
        """执行前校验跳过项；超出上限时提示见预演清单。"""
        if not skipped:
            return []
        lines = ["", "执行前校验跳过："]
        for item, reason in skipped[:_MAX_ERROR_DETAILS]:
            lines.append(f"- {item.target}：{reason}")
        if len(skipped) > _MAX_ERROR_DETAILS:
            lines.append(f"- 其余 {len(skipped) - _MAX_ERROR_DETAILS} 项见预演清单")
        return lines


class CmderWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """双面板文件管理器主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, simpledialog, ttk

        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._simpledialog = simpledialog
        self._settings_path = settings_path
        self.settings = load_settings(settings_path)
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._busy = False
        self._closing = False
        self._on_done: Callable[[Any], None] | None = None
        self._on_error: Callable[[str], None] | None = None
        self._toolbar_buttons: list[Any] = []
        self.transfer_queue = TransferQueue()
        self._queue_dialog: TransferQueueDialog | None = None
        self._compare_dialog: CompareDialog | None = None
        self._sync_dialog: SyncDialog | None = None
        self._queue_stopping = False

        self.root, self.theme = _guiwindow.create_root(
            title=f"cmderw - 双面板文件管理器 v{__version__}",
            saved_geometry=self.settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._build_menu()
        self._build_toolbar()
        self._build_panels()
        self._build_statusbar()
        self._bind_shortcuts()

        self.active_side = self.settings.active if self.settings.active in ("left", "right") else "left"
        self.left.set_path(Path(self.settings.left.path))
        self.right.set_path(Path(self.settings.right.path))
        self.set_active(self.left if self.active_side == "left" else self.right)
        self.set_status("就绪")

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _build_menu(self) -> None:
        tk = self._tk
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="新建目录\tF7", command=self.new_folder)
        file_menu.add_command(label="重命名\tF2", command=self.rename_selected)
        file_menu.add_separator()
        file_menu.add_command(label="查看\tF3", command=self.view_selected)
        file_menu.add_command(label="编辑\tF4", command=self.edit_selected)
        file_menu.add_separator()
        file_menu.add_command(label="打包\tAlt+F5", command=self.pack_selected)
        file_menu.add_command(label="解压\tAlt+F9", command=self.unpack_selected)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)

        edit_menu = tk.Menu(menubar, tearoff=False)
        edit_menu.add_command(label="复制\tF5", command=self.copy_selected)
        edit_menu.add_command(label="移动\tF6", command=self.move_selected)
        edit_menu.add_command(label="删除到回收站\tF8", command=self.delete_selected)
        edit_menu.add_command(label="永久删除\tShift+F8", command=self.permanent_delete_selected)
        edit_menu.add_separator()
        edit_menu.add_command(label="批量重命名…\tCtrl+M", command=self.bulk_rename)
        edit_menu.add_separator()
        edit_menu.add_command(label="全选\tCtrl+A", command=self.select_all)
        edit_menu.add_command(label="反选\tCtrl+I", command=self.invert_selection)
        edit_menu.add_command(label="按掩码选择…\tNum+", command=self.select_by_mask)
        edit_menu.add_command(label="按掩码取消选择…\tNum-", command=self.deselect_by_mask)
        menubar.add_cascade(label="编辑", menu=edit_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="刷新\tCtrl+R", command=self.refresh_both)
        self._hidden_var = tk.BooleanVar(value=self.settings.show_hidden)
        view_menu.add_checkbutton(
            label="显示隐藏文件\tCtrl+H",
            variable=self._hidden_var,
            command=self.toggle_hidden,
        )
        view_menu.add_separator()
        view_menu.add_command(label="快速过滤\tCtrl+S", command=self.focus_filter)
        view_menu.add_command(label="清除过滤\tEsc", command=self.clear_filter)
        view_menu.add_separator()
        view_menu.add_command(label="新建标签页\tCtrl+T", command=self.new_tab)
        view_menu.add_command(label="关闭标签页\tCtrl+W", command=self.close_tab)
        view_menu.add_command(label="下一个标签页\tCtrl+Tab", command=self.next_tab)
        view_menu.add_command(label="上一个标签页\tCtrl+Shift+Tab", command=self.previous_tab)
        view_menu.add_separator()
        view_menu.add_command(label="书签…\tCtrl+D", command=self.open_bookmarks)
        view_menu.add_command(label="历史记录…\tAlt+Down", command=self.show_history_menu)
        view_menu.add_separator()
        view_menu.add_command(label="交换面板\tCtrl+U", command=self.swap_panels)
        view_menu.add_command(label="切换面板\tTab", command=self.switch_panel)
        menubar.add_cascade(label="显示", menu=view_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
        tools_menu.add_command(label="传输队列…", command=self.show_transfer_queue)
        tools_menu.add_command(label="目录比较…\tCtrl+Shift+C", command=self.open_compare)
        tools_menu.add_command(label="目录同步…", command=self.open_sync_from_compare)
        tools_menu.add_separator()
        tools_menu.add_command(label="搜索\tCtrl+F", command=self.open_search)
        tools_menu.add_command(label="文件哈希", command=self.hash_selected)
        tools_menu.add_command(label="属性\tAlt+Enter", command=self.show_properties)
        menubar.add_cascade(label="工具", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="快捷键", command=self.show_shortcuts)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)
        self._context_menu = self._build_context_menu()

    def _build_context_menu(self) -> Any:
        menu = self._tk.Menu(self.root, tearoff=False)
        menu.add_command(label="打开", command=self.open_selected)
        menu.add_command(label="查看", command=self.view_selected)
        menu.add_command(label="编辑", command=self.edit_selected)
        menu.add_separator()
        menu.add_command(label="复制到对面面板", command=self.copy_selected)
        menu.add_command(label="移动到对面面板", command=self.move_selected)
        menu.add_separator()
        menu.add_command(label="重命名", command=self.rename_selected)
        menu.add_command(label="删除到回收站", command=self.delete_selected)
        menu.add_command(label="永久删除", command=self.permanent_delete_selected)
        menu.add_separator()
        menu.add_command(label="按掩码选择…", command=self.select_by_mask)
        menu.add_command(label="按掩码取消选择…", command=self.deselect_by_mask)
        menu.add_separator()
        menu.add_command(label="打包", command=self.pack_selected)
        menu.add_command(label="解压", command=self.unpack_selected)
        menu.add_command(label="哈希", command=self.hash_selected)
        menu.add_command(label="属性", command=self.show_properties)
        return menu

    def _build_toolbar(self) -> None:
        ttk = self._ttk
        spacing = self.theme.spacing
        toolbar = ttk.Frame(self.root, padding=(spacing.sm, spacing.xs))
        toolbar.grid(row=0, column=0, sticky="ew")
        buttons: tuple[tuple[str, Callable[[], None]], ...] = (
            ("复制 F5", self.copy_selected),
            ("移动 F6", self.move_selected),
            ("新建 F7", self.new_folder),
            ("删除 F8", self.delete_selected),
            ("重命名 F2", self.rename_selected),
            ("查看 F3", self.view_selected),
            ("编辑 F4", self.edit_selected),
            ("打包", self.pack_selected),
            ("解压", self.unpack_selected),
            ("搜索", self.open_search),
            ("哈希", self.hash_selected),
            ("刷新", self.refresh_both),
        )
        for index, (label, command) in enumerate(buttons):
            if index in (7, 9):
                ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=spacing.xs)
            button = ttk.Button(toolbar, text=label, style="Toolbutton", command=command)
            button.pack(side="left", padx=(0, spacing.xxs))
            self._toolbar_buttons.append(button)
        ttk.Checkbutton(toolbar, text="隐藏文件", variable=self._hidden_var, command=self.toggle_hidden).pack(
            side="right"
        )

    def _build_panels(self) -> None:
        ttk = self._ttk
        self._paned = ttk.PanedWindow(self.root, orient="horizontal")
        self._paned.grid(row=1, column=0, sticky="nsew")
        self.left = FilePanel(
            self,
            self._paned,
            "left",
            self.settings.left_tabs or [self.settings.left],
            self.settings.left_tab_index,
        )
        self.right = FilePanel(
            self,
            self._paned,
            "right",
            self.settings.right_tabs or [self.settings.right],
            self.settings.right_tab_index,
        )
        self._paned.add(self.left.frame, weight=1)
        self._paned.add(self.right.frame, weight=1)

    def _build_statusbar(self) -> None:
        ttk = self._ttk
        spacing = self.theme.spacing
        status = ttk.Frame(self.root, padding=(spacing.sm, spacing.xxs))
        status.grid(row=2, column=0, sticky="ew")
        status.grid_columnconfigure(0, weight=1)
        self.status_var = self._tk.StringVar(value="就绪")
        ttk.Label(status, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.progress = ttk.Progressbar(status, mode="determinate", length=220)
        self.progress.grid(row=0, column=1, sticky="e")

    def _bind_shortcuts(self) -> None:
        bindings: dict[str, Callable[..., None]] = {
            "<F2>": lambda _event: self.rename_selected(),
            "<F3>": lambda _event: self.view_selected(),
            "<F4>": lambda _event: self.edit_selected(),
            "<F5>": lambda _event: self.copy_selected(),
            "<F6>": lambda _event: self.move_selected(),
            "<F7>": lambda _event: self.new_folder(),
            "<F8>": lambda _event: self.delete_selected(),
            "<Shift-F8>": lambda _event: self.permanent_delete_selected(),
            "<Alt-F5>": lambda _event: self.pack_selected(),
            "<Alt-F9>": lambda _event: self.unpack_selected(),
            "<Alt-Return>": lambda _event: self.show_properties(),
            "<Control-f>": lambda _event: self.open_search(),
            "<Control-r>": lambda _event: self.refresh_both(),
            "<Control-h>": lambda _event: self.toggle_hidden(),
            "<Control-s>": lambda _event: self.focus_filter(),
            "<Control-i>": lambda _event: self.invert_selection(),
            "<Control-m>": lambda _event: self.bulk_rename(),
            "<Control-u>": lambda _event: self.swap_panels(),
            "<Control-a>": lambda _event: self.select_all(),
            "<Control-t>": lambda _event: self.new_tab(),
            "<Control-w>": lambda _event: self.close_tab(),
            "<Control-Tab>": lambda _event: self.next_tab(),
            "<Control-Shift-Tab>": lambda _event: self.previous_tab(),
            "<Control-ISO_Left_Tab>": lambda _event: self.previous_tab(),
            "<Control-d>": lambda _event: self.open_bookmarks(),
            "<Control-Shift-c>": lambda _event: self.open_compare(),
            "<Control-Shift-C>": lambda _event: self.open_compare(),
            "<Alt-Down>": lambda _event: self.show_history_menu(),
            "<Escape>": lambda _event: self.clear_filter(),
            "<Tab>": lambda _event: self.switch_panel(),
        }
        for sequence, handler in bindings.items():
            self.root.bind(sequence, handler)

    # ------------------------------------------------------------------ #
    # 面板与状态
    # ------------------------------------------------------------------ #
    def set_active(self, panel: FilePanel) -> None:
        """设置活动面板并更新标题样式。"""
        self.active_side = panel.side
        self.left.set_title_active(panel is self.left)
        self.right.set_title_active(panel is self.right)

    def active_panel(self) -> FilePanel:
        """当前活动面板。"""
        return self.left if self.active_side == "left" else self.right

    def other_panel(self) -> FilePanel:
        """另一个面板。"""
        return self.right if self.active_side == "left" else self.left

    @property
    def busy(self) -> bool:
        """是否有后台任务在运行。"""
        return self._busy

    def set_status(self, message: str) -> None:
        """更新底部状态文字。"""
        self.status_var.set(message)

    def refresh_both(self) -> None:
        """刷新两个面板（保留选择）。"""
        self.left.refresh()
        self.right.refresh()
        self.set_status("已刷新")

    def switch_panel(self) -> None:
        """切换活动面板。"""
        panel = self.other_panel()
        self.set_active(panel)
        panel.focus_tree()

    def swap_panels(self) -> None:
        """交换左右面板当前目录。"""
        left_path, right_path = self.left.state.path, self.right.state.path
        self.left.set_path(Path(right_path))
        self.right.set_path(Path(left_path))
        self.set_status("已交换面板")

    # ------------------------------------------------------------------ #
    # 标签页 / 书签 / 历史
    # ------------------------------------------------------------------ #
    def new_tab(self) -> None:
        """Ctrl+T：在活动面板新建标签页。"""
        self.active_panel().new_tab()

    def close_tab(self) -> None:
        """Ctrl+W：关闭活动面板的当前标签页（至少保留一个）。"""
        self.active_panel().close_tab()

    def next_tab(self) -> None:
        """Ctrl+Tab：切到活动面板的下一个标签页。"""
        self.active_panel().next_tab(1)

    def previous_tab(self) -> None:
        """Ctrl+Shift+Tab：切到活动面板的上一个标签页。"""
        self.active_panel().next_tab(-1)

    def open_bookmarks(self) -> None:
        """Ctrl+D：书签管理，打开时作用于当前活动面板。"""
        BookmarkDialog(self, self.active_panel())

    def show_history_menu(self) -> None:
        """Alt+Down：用活动面板的历史栈弹出跳转菜单。"""
        panel = self.active_panel()
        history = panel.state.history
        if not history:
            self.set_status(f"{panel.title}暂无历史记录")
            return
        menu = self._tk.Menu(self.root, tearoff=False)
        for index in range(len(history) - 1, -1, -1):
            path = history[index]
            marker = "● " if index == panel.state.history_index else "   "
            menu.add_command(label=f"{marker}{index + 1}. {path}", command=lambda i=index: self._jump_history(panel, i))
        x = panel.path_entry.winfo_rootx()
        y = panel.path_entry.winfo_rooty() + panel.path_entry.winfo_height()
        menu.tk_popup(x, y)

    def _jump_history(self, panel: FilePanel, index: int) -> None:
        """跳到历史栈中的指定条目（不新增历史）。"""
        history = panel.state.history
        if not 0 <= index < len(history):
            return
        panel.state.history_index = index
        panel.set_path(Path(history[index]), push=False)
        self.set_status(f"{panel.title}历史：{history[index]}")

    def toggle_hidden(self) -> None:
        """切换隐藏文件显示。"""
        self.settings.show_hidden = bool(self._hidden_var.get())
        self.refresh_both()
        self.set_status("显示隐藏文件" if self.settings.show_hidden else "隐藏隐藏文件")

    def select_all(self) -> None:
        """全选当前面板。"""
        self.active_panel().select_all()

    def invert_selection(self) -> None:
        """反选当前面板的可见条目。"""
        count = self.active_panel().invert_selection()
        self.set_status(f"反选后选中 {count} 项")

    def select_by_mask(self) -> None:
        """Num+：按 glob 掩码增选。"""
        self._apply_mask(select=True)

    def deselect_by_mask(self) -> None:
        """Num-：按 glob 掩码取消选择。"""
        self._apply_mask(select=False)

    def _apply_mask(self, *, select: bool) -> None:
        title = "按掩码选择" if select else "按掩码取消选择"
        mask = self._simpledialog.askstring(
            title,
            "文件名掩码（* 与 ?，分号分隔多个）：",
            initialvalue="*",
            parent=self.root,
        )
        if not mask:
            return
        count = self.active_panel().apply_mask_selection(mask, select=select)
        self.set_status(f"{title}：命中 {count} 项")

    def focus_filter(self) -> None:
        """Ctrl+S：聚焦当前面板的快速过滤框。"""
        self.active_panel().focus_filter()

    def clear_filter(self) -> None:
        """Esc：清空当前面板的快速过滤。"""
        self.active_panel().clear_filter()

    def show_selected_size(self) -> None:
        """Space：后台统计选中条目的总大小与文件数。"""
        if self._busy:
            return
        entries = self.active_panel().selected_entries()
        if not entries:
            self.set_status("请先选择文件或文件夹")
            return
        paths = [entry.path for entry in entries]

        def task() -> tuple[int, int]:
            total_bytes = 0
            total_files = 0
            for path in paths:
                size, files = directory_size(path)
                total_bytes += size
                total_files += files
            return total_bytes, total_files

        def done(result: tuple[int, int]) -> None:
            total_bytes, total_files = result
            self.set_status(f"{len(paths)} 项共 {format_size(total_bytes)}，{total_files} 个文件")

        self.start_worker(task, done, title="统计大小")

    def reveal_path(self, path: Path) -> None:
        """在活动面板中定位到指定文件。"""
        panel = self.active_panel()
        panel.set_path(path.parent)
        panel.select_name(path.name)

    def open_entry(self, entry: FileEntry) -> None:
        """双击 / Enter：目录进入，文件用系统默认程序打开。"""
        if entry.is_dir:
            self.active_panel().set_path(entry.path)
            return
        try:
            open_externally(entry.path)
        except OSError as exc:
            self.show_error("无法打开", str(exc))

    def open_selected(self) -> None:
        """打开当前选中条目。"""
        entries = self.active_panel().selected_entries()
        if entries:
            self.open_entry(entries[0])

    def show_context_menu(self, event: Any) -> None:
        """右键菜单：先把点击行与所在面板激活，再弹出菜单。"""
        panel = self.left if event.widget is self.left.tree else self.right
        self.set_active(panel)
        row = panel.tree.identify_row(event.y)
        if row and row not in panel.tree.selection():
            panel.tree.selection_set(row)
        self._context_menu.tk_popup(event.x_root, event.y_root)

    def show_error(self, title: str, message: str) -> None:
        """统一错误提示。"""
        self._messagebox.showerror(title, message, parent=self.root)

    # ------------------------------------------------------------------ #
    # 查看 / 编辑 / 属性
    # ------------------------------------------------------------------ #
    def view_selected(self) -> None:
        """F3 内置文本查看器。"""
        entries = self.active_panel().selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("查看", "请选择一个文件", parent=self.root)
            return
        try:
            text = load_preview(entries[0].path)
        except (OSError, ValueError) as exc:
            self._messagebox.showwarning("无法查看", str(exc), parent=self.root)
            return
        self._show_text_dialog(f"查看 - {entries[0].name}", text)

    def edit_selected(self) -> None:
        """F4 用系统默认程序编辑。"""
        entries = self.active_panel().selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("编辑", "请选择一个文件", parent=self.root)
            return
        try:
            open_externally(entries[0].path)
        except OSError as exc:
            self.show_error("无法编辑", str(exc))

    def show_properties(self) -> None:
        """显示选中条目的属性。"""
        entries = self.active_panel().selected_entries()
        if not entries:
            self._messagebox.showinfo("属性", "请先选择文件或文件夹", parent=self.root)
            return
        if len(entries) == 1:
            text = describe_entry(entries[0])
        else:
            total = sum(entry.size for entry in entries if not entry.is_dir)
            text = f"已选择 {len(entries)} 个条目\n总大小: {format_size(total)}"
        self._messagebox.showinfo("属性", text, parent=self.root)

    def _show_text_dialog(self, title: str, text: str) -> None:
        """只读文本窗口（查看 / 哈希结果）。"""
        tk = self._tk
        ttk = self._ttk
        spacing = self.theme.spacing
        window = tk.Toplevel(self.root)
        window.title(title)
        window.transient(self.root)
        _guiwindow.center_over_parent(window, self.root, width=900, height=600)
        frame = ttk.Frame(window, padding=spacing.sm)
        frame.pack(fill="both", expand=True)
        text_widget = tk.Text(frame, wrap="none", font=_guitheme.MONO_FONT)
        y_scroll = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        x_scroll = ttk.Scrollbar(frame, orient="horizontal", command=text_widget.xview)
        text_widget.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        text_widget.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        text_widget.insert("1.0", text)
        text_widget.configure(state="disabled")
        ttk.Button(frame, text="关闭", command=window.destroy).grid(
            row=2, column=0, columnspan=2, sticky="e", pady=(spacing.xs, 0)
        )

    # ------------------------------------------------------------------ #
    # 文件操作
    # ------------------------------------------------------------------ #
    def _selected_paths(self) -> list[Path]:
        """取选中条目路径；未选择时提示并返回空列表。"""
        entries = self.active_panel().selected_entries()
        if not entries:
            self._messagebox.showinfo("提示", "请先选择文件或文件夹", parent=self.root)
            return []
        return [entry.path for entry in entries]

    def _ask_conflict(self, target: Path) -> str:
        """同名冲突询问：是=覆盖，否=自动重命名，取消=跳过。"""
        answer = self._messagebox.askyesnocancel(
            "目标已存在",
            f"{target}\n\n是：覆盖\n否：保留两者（自动重命名）\n取消：跳过",
            parent=self.root,
        )
        if answer is None:
            return "skip"
        return "overwrite" if answer else "rename"

    def copy_selected(self) -> None:
        """F5 复制到指定目录（默认对面面板）。"""
        self._transfer(move=False)

    def move_selected(self) -> None:
        """F6 移动到指定目录（默认对面面板）。"""
        self._transfer(move=True)

    def _transfer(self, *, move: bool) -> None:
        paths = self._selected_paths()
        if paths:
            self.transfer_paths(paths, move=move)

    def transfer_paths(self, paths: Sequence[Path], *, move: bool) -> bool:
        """通用复制 / 移动入口：规划后加入传输队列，返回是否已入队。"""
        if not paths:
            return False
        action = "移动" if move else "复制"
        default_destination = self.other_panel().state.path
        destination_text = self._simpledialog.askstring(
            f"{action}目标",
            f"目标目录（默认对面面板）：\n{default_destination}",
            initialvalue=default_destination,
            parent=self.root,
        )
        if destination_text is None:
            return False
        raw = destination_text.strip() or default_destination
        destination = Path(raw).expanduser()
        if not destination.is_absolute():
            destination = Path(self.active_panel().state.path) / destination
        try:
            items, skipped = plan_transfer(paths, destination, conflict="ask", chooser=self._ask_conflict)
        except (OSError, ValueError) as exc:
            self.show_error(f"无法{action}", str(exc))
            return False
        if not items:
            self.set_status("没有需要操作的条目")
            return False
        return self.enqueue_transfer(items, destination, move=move, skipped=skipped)

    def enqueue_transfer(
        self,
        items: Sequence[TransferItem],
        destination: Path,
        *,
        move: bool,
        skipped: Sequence[Path] = (),
    ) -> bool:
        """把规划结果加入队列；同队列目标冲突时整批拒绝并说明冲突双方。"""
        task, conflicts = self.transfer_queue.add(
            destination,
            [(item.source, item.target, item.overwrite) for item in items],
            move=move,
            skipped=skipped,
        )
        if conflicts:
            details = "\n".join(conflicts[:_MAX_ERROR_DETAILS])
            self._messagebox.showwarning(
                "目标冲突，未加入队列",
                f"以下任务的目标路径重复：\n\n{details}",
                parent=self.root,
            )
            return False
        assert task is not None
        self.set_status(f"已加入队列：{task.label}")
        self._ensure_queue_dialog()
        self._run_next_task()
        return True

    # ------------------------------------------------------------------ #
    # 传输队列
    # ------------------------------------------------------------------ #
    def show_transfer_queue(self) -> None:
        """打开并聚焦传输队列窗口。"""
        self._ensure_queue_dialog()

    def open_compare(self) -> None:
        """Ctrl+Shift+C：只读比较左右面板当前目录（已有窗口则前置）。"""
        dialog = self._compare_dialog
        if dialog is not None and dialog.win.winfo_exists():
            dialog.win.deiconify()
            dialog.win.lift()
            dialog.win.focus_set()
            return
        self._compare_dialog = CompareDialog(self)

    def open_sync_from_compare(self) -> None:
        """菜单入口：带着已有比较结果打开同步；没有结果时先打开比较窗口。"""
        dialog = self._compare_dialog
        if dialog is not None and dialog.win.winfo_exists() and dialog.report is not None:
            dialog.open_sync()
            return
        self.open_compare()
        self.set_status("目录同步：请先在比较窗口执行比较")

    def open_sync(self, report: CompareReport, tolerance: float = 2.0) -> None:
        """打开同步预演窗口；已有同步窗口先关闭再按新结果重建。"""
        dialog = self._sync_dialog
        if dialog is not None and dialog.win.winfo_exists():
            dialog.win.destroy()
        self._sync_dialog = SyncDialog(self, report, tolerance)

    def _ensure_queue_dialog(self) -> None:
        """队列窗口不存在则创建，存在则刷新。"""
        dialog = self._queue_dialog
        if dialog is not None and dialog.win.winfo_exists():
            dialog.refresh()
            return
        self._queue_dialog = TransferQueueDialog(self)

    def _refresh_queue_dialog(self) -> None:
        """主线程刷新队列窗口（窗口已关闭则清理引用）。"""
        dialog = self._queue_dialog
        if dialog is None:
            return
        if not dialog.win.winfo_exists():
            self._queue_dialog = None
            return
        dialog.refresh()

    def _run_next_task(self) -> None:
        """串行执行队首任务；已有后台任务或正在退出时不启动。"""
        if self._busy or self._queue_stopping:
            return
        task = self.transfer_queue.pending_task()
        if task is None:
            return
        self.transfer_queue.begin(task)
        self._refresh_queue_dialog()
        total = task.total

        def work() -> str:
            for index, item in enumerate(task.items, start=1):
                if task.cancel_requested or self._queue_stopping:
                    self.transfer_queue.cancel_remaining(task)
                    break
                self.transfer_queue.begin_item(item)
                self._queue.put(("queue",))
                self._progress_callback(index - 1, total, item.source.name)
                try:
                    target = item.target
                    if target.exists() or target.is_symlink():
                        if not item.overwrite:
                            raise FileExistsError(f"目标已存在: {target}")
                        item.target_owned = True
                        delete_entry(target)
                    if task.move:
                        move_entry(item.source, target)
                    else:
                        copy_entry(item.source, target)
                    self.transfer_queue.complete_item(item)
                except (OSError, ValueError) as exc:
                    self.transfer_queue.fail_item(item, str(exc))
                self._queue.put(("queue",))
                self._progress_callback(index, total, item.source.name)
            status = self.transfer_queue.finish(task)
            if status == QUEUE_CANCELLED:
                cleanup_incomplete_items(task)
            return status

        self.start_worker(work, lambda _status: self._after_queue_task(task), title=task.label)

    def _after_queue_task(self, task: QueueTask) -> None:
        """单个任务结束后刷新界面并如实汇报失败与清理结果。"""
        self.refresh_both()
        self._refresh_queue_dialog()
        if self._queue_stopping:
            return
        summary = f"{task.label}：{task.status_text}（成功 {task.done_count}/{task.total}"
        if task.failed_count:
            summary += f"，失败 {task.failed_count}"
        skipped_total = len(task.skipped)
        if skipped_total:
            summary += f"，跳过 {skipped_total}"
        summary += "）"
        self.set_status(summary)
        errors = [(item.source, item.error) for item in task.items if item.status == ITEM_FAILED]
        for target, error in task.cleanup_errors:
            errors.append((target, f"半成品清理失败：{error}"))
        if errors:
            details = "\n".join(f"{path}: {message}" for path, message in errors[:_MAX_ERROR_DETAILS])
            self._messagebox.showwarning(
                "队列任务结束（有失败）",
                f"{summary}\n\n{details}",
                parent=self.root,
            )

    def cancel_queue_task(self, task_id: int) -> None:
        """取消队列任务：排队中立即取消，进行中在当前文件完成后停止。"""
        message = self.transfer_queue.cancel(task_id)
        self.set_status(message)
        self._refresh_queue_dialog()
        if not self._busy:
            self._run_next_task()

    def retry_queue_items(self, task_id: int, items: Sequence[QueueItem] | None = None) -> bool:
        """重试失败项（``items`` 为空表示该任务全部失败项）；目标残留时先确认覆盖。"""
        task = self.transfer_queue.get(task_id)
        if task is None:
            self.set_status("任务不存在")
            return False
        failed = (
            [item for item in items if item.status == ITEM_FAILED]
            if items is not None
            else [item for item in task.items if item.status == ITEM_FAILED]
        )
        if not failed:
            self.set_status(f"{task.label} 没有可重试的失败项")
            return False
        existing = [item for item in failed if item.target.exists() or item.target.is_symlink()]
        overwrite = False
        if existing:
            confirmed = self._messagebox.askyesno(
                "重试失败项",
                f"有 {len(existing)} 个目标路径已存在（可能是上次未完成的半成品），重试将覆盖这些目标。\n\n继续吗？",
                parent=self.root,
            )
            if not confirmed:
                return False
            overwrite = True
        retry_task, conflicts = self.transfer_queue.retry_items(task_id, failed, overwrite_existing=overwrite)
        if conflicts:
            details = "\n".join(conflicts[:_MAX_ERROR_DETAILS])
            self._messagebox.showwarning("目标冲突，未加入队列", details, parent=self.root)
            return False
        assert retry_task is not None
        self.set_status(f"已加入重试任务：{retry_task.label}")
        self._refresh_queue_dialog()
        self._run_next_task()
        return True

    def delete_selected(self) -> None:
        """F8 移入系统回收站（可撤销）。"""
        paths = self._selected_paths()
        if paths:
            self.delete_paths(paths)

    def permanent_delete_selected(self) -> None:
        """Shift+F8 永久删除选中条目。"""
        paths = self._selected_paths()
        if paths:
            self.delete_paths(paths, permanent=True)

    def delete_paths(self, paths: Sequence[Path], *, permanent: bool = False) -> bool:
        """通用删除入口：默认进回收站，``permanent`` 为永久删除；返回是否已启动。"""
        if self._busy:
            self._messagebox.showinfo("请稍候", "已有后台任务在运行", parent=self.root)
            return False
        if not paths:
            return False
        if permanent:
            confirmed = self._messagebox.askyesno(
                "确认永久删除",
                f"确定永久删除选中的 {len(paths)} 项吗？\n此操作不可撤销，不会进入回收站。",
                parent=self.root,
            )
            if not confirmed:
                return False

            def task() -> OperationReport:
                return delete_entries(paths, progress=self._progress_callback)

            def done(report: OperationReport) -> None:
                self.refresh_both()
                summary = f"删除完成：成功 {len(report.completed)}，失败 {len(report.errors)}"
                self.set_status(summary)
                if report.errors:
                    details = "\n".join(f"{path.name}: {error}" for path, error in report.errors[:_MAX_ERROR_DETAILS])
                    self._messagebox.showwarning("删除完成（有失败）", f"{summary}\n\n{details}", parent=self.root)

            self.start_worker(task, done, title=f"删除 {len(paths)} 项")
            return True

        confirmed = self._messagebox.askyesno(
            "移入回收站",
            f"确定将选中的 {len(paths)} 项移入回收站吗？",
            parent=self.root,
        )
        if not confirmed:
            return False

        def trash_task() -> TrashReport:
            return trash_paths(paths, progress=self._progress_callback)

        def trash_done(report: TrashReport) -> None:
            self.refresh_both()
            summary = f"移入回收站：成功 {len(report.completed)}，失败 {len(report.errors)}"
            self.set_status(summary)
            if report.errors:
                details = "\n".join(f"{path.name}: {error}" for path, error in report.errors[:_MAX_ERROR_DETAILS])
                self._messagebox.showwarning(
                    "移入回收站（有失败）",
                    f"{summary}\n\n{details}\n\n回收站不可用时可改用 Shift+F8 永久删除。",
                    parent=self.root,
                )

        self.start_worker(trash_task, trash_done, title=f"移入回收站 {len(paths)} 项")
        return True

    def new_folder(self) -> None:
        """F7 新建目录。"""
        if self._busy:
            return
        panel = self.active_panel()
        name = self._simpledialog.askstring("新建目录", "目录名称：", parent=self.root)
        if not name:
            return
        try:
            target = create_directory(Path(panel.state.path), name)
        except (OSError, ValueError) as exc:
            self.show_error("无法新建目录", str(exc))
            return
        panel.refresh(select=target.name)
        self.set_status(f"已创建目录 {target.name}")

    def rename_selected(self) -> None:
        """F2 重命名单个条目；多选时转到批量重命名。"""
        if self._busy:
            return
        panel = self.active_panel()
        entries = panel.selected_entries()
        if len(entries) > 1:
            self.bulk_rename()
            return
        if not entries:
            self._messagebox.showinfo("重命名", "请选择一个文件或文件夹", parent=self.root)
            return
        entry = entries[0]
        name = self._simpledialog.askstring(
            "重命名", f"{entry.name} 重命名为：", initialvalue=entry.name, parent=self.root
        )
        if not name or name == entry.name:
            return
        try:
            target = rename_entry(entry.path, name)
        except (OSError, ValueError) as exc:
            self.show_error("无法重命名", str(exc))
            return
        panel.refresh(select=target.name)
        self.set_status(f"已重命名为 {target.name}")

    def bulk_rename(self) -> None:
        """Ctrl+M 批量重命名选中条目。"""
        if self._busy:
            return
        panel = self.active_panel()
        entries = panel.selected_entries()
        if not entries:
            self._messagebox.showinfo("批量重命名", "请先选择文件或文件夹", parent=self.root)
            return
        BulkRenameDialog(self, Path(panel.state.path), entries)

    def adopt_paths(self, paths: Sequence[Path]) -> str:
        """把搜索结果加入活动面板选择；跨目录结果按目录统计提示。"""
        panel = self.active_panel()
        first_dir = paths[0].parent
        same_dir: list[str] = []
        skipped: dict[Path, int] = {}
        for path in paths:
            if path.parent == first_dir:
                same_dir.append(path.name)
            else:
                skipped[path.parent] = skipped.get(path.parent, 0) + 1
        panel.set_path(first_dir)
        selected = panel.select_names(same_dir, focus=True)
        message = f"已加入选择：{selected} 项（{first_dir}）"
        if skipped:
            details = "；".join(f"{directory}（{count} 项）" for directory, count in skipped.items())
            message += f"；已跳过的其他目录：{details}"
        self.set_status(message)
        return message

    def pack_selected(self) -> None:
        """Alt+F5 打包选中条目。"""
        if self._busy:
            return
        paths = self._selected_paths()
        if not paths:
            return
        panel = self.active_panel()
        default = default_archive_name(paths)
        name = self._simpledialog.askstring(
            "打包",
            "归档文件名（.zip/.tar.gz/.tar.bz2/.gz 等）：",
            initialvalue=default,
            parent=self.root,
        )
        if not name:
            return
        name = name.strip()
        if not any(name.casefold().endswith(suffix) for suffix in ARCHIVE_SUFFIXES):
            name += ".zip"
        target = Path(panel.state.path) / name
        if target.exists() and not self._messagebox.askyesno(
            "覆盖归档",
            f"{target.name} 已存在，是否覆盖？",
            parent=self.root,
        ):
            return

        def task() -> Path:
            return pack_entries(paths, target, progress=self._progress_callback)

        def done(output: Path) -> None:
            panel.refresh()
            self.set_status(f"已生成 {output.name}")
            self._messagebox.showinfo("打包完成", str(output), parent=self.root)

        self.start_worker(task, done, title=f"打包 {len(paths)} 项")

    def unpack_selected(self) -> None:
        """Alt+F9 解压选中归档到对面面板目录下的同名子目录。"""
        if self._busy:
            return
        entries = self.active_panel().selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("解压", "请选择一个归档文件", parent=self.root)
            return
        archive = entries[0].path
        from fcmd.tools.archive.archivex import detect_format

        try:
            detect_format(archive)
        except ValueError as exc:
            self.show_error("无法解压", str(exc))
            return
        base = Path(self.other_panel().state.path)
        initial = str(default_extract_dir(archive, base))
        destination_text = self._simpledialog.askstring("解压", "解压到目录：", initialvalue=initial, parent=self.root)
        if not destination_text:
            return
        destination = Path(destination_text).expanduser()
        if not destination.is_absolute():
            destination = base / destination

        def task() -> Path:
            return extract_archive_to(archive, destination, progress=self._progress_callback)

        def done(output: Path) -> None:
            self.refresh_both()
            self.set_status(f"已解压到 {output}")
            self._messagebox.showinfo("解压完成", str(output), parent=self.root)

        self.start_worker(task, done, title=f"解压 {archive.name}")

    def hash_selected(self) -> None:
        """计算选中文件 / 目录的哈希。"""
        if self._busy:
            return
        paths = self._selected_paths()
        if not paths:
            return
        algorithm = self._simpledialog.askstring(
            "文件哈希",
            "算法（md5/sha1/sha256/sha512）：",
            initialvalue="sha256",
            parent=self.root,
        )
        if not algorithm:
            return
        algorithm = algorithm.strip().lower()
        from fcmd.tools import hashbase

        try:
            hashbase.ensure_algorithm(algorithm)
        except ValueError as exc:
            self.show_error("算法无效", str(exc))
            return

        def task() -> list[HashResult]:
            return hash_paths(paths, algorithm, progress=self._progress_callback)

        def done(results: list[HashResult]) -> None:
            if not results:
                self._messagebox.showinfo("哈希", "没有可计算哈希的文件", parent=self.root)
                return
            self.set_status(f"已计算 {len(results)} 个文件的哈希")
            self._show_text_dialog(
                f"哈希结果（{algorithm}，{len(results)} 个）", format_hash_report(results, algorithm)
            )

        self.start_worker(task, done, title=f"哈希（{algorithm}）")

    def open_search(self) -> None:
        """Ctrl+F 打开搜索对话框。"""
        SearchDialog(self, Path(self.active_panel().state.path))

    def start_search(  # noqa: PLR0913 - 搜索对话框需要透传目录、模式、内容开关与回调
        self,
        directory: Path,
        pattern: str,
        *,
        content: bool,
        extension: str,
        on_done: Callable[[list[SearchHit]], None],
        on_error: Callable[[str], None],
    ) -> None:
        """供搜索对话框调用的后台搜索入口。"""

        def task() -> list[SearchHit]:
            return search_paths(directory, pattern, content=content, extension=extension)

        def done(hits: list[SearchHit]) -> None:
            self.set_status(f"搜索完成：{len(hits)} 个结果")
            on_done(hits)

        self.start_worker(task, done, title="搜索", on_error=on_error)

    # ------------------------------------------------------------------ #
    # 后台任务
    # ------------------------------------------------------------------ #
    def _progress_callback(self, done: int, total: int, name: str) -> None:
        """后台线程 → 主线程的进度事件。"""
        self._queue.put(("progress", done, total, name))

    def start_worker(
        self,
        task: Callable[[], Any],
        on_done: Callable[[Any], None],
        *,
        title: str,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        """启动 daemon 线程并在主线程轮询结果。"""
        if self._busy:
            self._messagebox.showinfo("请稍候", "已有后台任务在运行", parent=self.root)
            return
        self._busy = True
        self._on_done = on_done
        self._on_error = on_error
        self._set_toolbar_state("disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start(12)
        self.set_status(f"{title}...")

        def runner() -> None:
            try:
                result = task()
            except Exception as exc:  # 后台线程需把所有异常转回主线程
                self._queue.put(("error", str(exc)))
                return
            self._queue.put(("done", result))

        threading.Thread(target=runner, daemon=True).start()
        self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    def _set_toolbar_state(self, state: str) -> None:
        for button in self._toolbar_buttons:
            button.configure(state=state)

    def _finish_worker(self) -> None:
        self._busy = False
        self.progress.stop()
        self.progress.configure(mode="determinate", value=0)
        self._set_toolbar_state("normal")

    def _poll_queue(self) -> None:
        """主线程消费后台事件。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break
            kind = event[0]
            if kind == "progress":
                _, done, total, name = event
                self.progress.stop()
                self.progress.configure(mode="determinate", maximum=max(total, 1), value=done)
                self.set_status(f"正在处理 {done}/{total}: {name}")
            elif kind == "done":
                self._finish_worker()
                callback, self._on_done = self._on_done, None
                self._on_error = None
                if callback is not None:
                    callback(event[1])
                self._run_next_task()
                return
            elif kind == "error":
                self._finish_worker()
                on_error, self._on_error = self._on_error, None
                self._on_done = None
                self.set_status("操作失败")
                self.show_error("操作失败", str(event[1]))
                if on_error is not None:
                    on_error(str(event[1]))
                self._run_next_task()
                return
            elif kind == "queue":
                self._refresh_queue_dialog()
        if self._busy:
            self.root.after(POLL_INTERVAL_MS, self._poll_queue)

    # ------------------------------------------------------------------ #
    # 帮助与生命周期
    # ------------------------------------------------------------------ #
    def show_shortcuts(self) -> None:
        """快捷键说明。"""
        text = (
            "Tab 切换面板    Enter 打开    Backspace 上级\n"
            "F2 重命名    F3 查看    F4 编辑    F5 复制    F6 移动\n"
            "F7 新建目录    F8 删除到回收站    Shift+F8 永久删除\n"
            "Alt+F5 打包    Alt+F9 解压    Space 统计大小\n"
            "Ctrl+F 搜索    Ctrl+S 快速过滤    Esc 清除过滤\n"
            "Ctrl+H 隐藏文件    Ctrl+R 刷新    Ctrl+U 交换面板\n"
            "Ctrl+A 全选    Ctrl+I 反选    Num+ 按掩码选择    Num- 按掩码取消\n"
            "Ctrl+M 批量重命名    Alt+Enter 属性\n"
            "Ctrl+T 新建标签页    Ctrl+W 关闭标签页（至少保留一个）\n"
            "Ctrl+Tab 下一个标签页    Ctrl+Shift+Tab 上一个标签页\n"
            "Ctrl+D 书签    Alt+Down 历史记录\n"
            "F5/F6 进入串行传输队列（工具→传输队列 查看进度、取消、重试失败项）\n"
            "搜索结果可直接打开所在目录、复制、移动、删除或加入面板选择"
        )
        self._messagebox.showinfo("快捷键", text, parent=self.root)

    def show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于 cmderw",
            f"cmderw v{__version__}\n双面板文件管理器（Total Commander 风格）\n基于 Tk 与 fcmd 现有工具实现",
            parent=self.root,
        )

    def close(self) -> None:
        """保存设置并退出；传输队列或后台任务未完成时先确认。"""
        unfinished = self.transfer_queue.unfinished()
        running = [task for task in unfinished if task.status == QUEUE_RUNNING]
        if unfinished:
            confirmed = self._messagebox.askyesno(
                "传输队列未完成",
                f"仍有 {len(unfinished)} 个传输任务未完成。\n"
                "退出将取消它们：正在传输的当前文件会完成，其余条目不再开始。\n\n确定退出吗？",
                parent=self.root,
            )
            if not confirmed:
                return
            self._queue_stopping = True
            for task in unfinished:
                self.transfer_queue.cancel(task.task_id)
            if running:
                self.set_status("正在停止当前传输，当前文件完成后退出…")
                self.root.after(POLL_INTERVAL_MS, self._close_when_idle)
                return
        if self._busy and not self._messagebox.askyesno(
            "任务进行中",
            "后台任务仍在运行，确定退出吗？",
            parent=self.root,
        ):
            return
        self._save_and_destroy()

    def _close_when_idle(self) -> None:
        """等当前文件传完后退出，避免半成品被进程直接截断。"""
        if self._busy:
            self.root.after(POLL_INTERVAL_MS, self._close_when_idle)
            return
        self._save_and_destroy()

    def _save_and_destroy(self) -> None:
        """保存面板/标签/书签状态并销毁窗口。"""
        if self._closing:
            return
        self._closing = True
        self.settings.left = self.left.state
        self.settings.right = self.right.state
        self.settings.left_tabs = list(self.left.tabs)
        self.settings.right_tabs = list(self.right.tabs)
        self.settings.left_tab_index = self.left.tab_index
        self.settings.right_tab_index = self.right.tab_index
        self.settings.active = self.active_side
        self.settings.show_hidden = bool(self._hidden_var.get())
        self.settings.geometry = self.root.winfo_geometry()
        save_settings(self.settings, self._settings_path)
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: CmderWindow(settings_path), "双面板文件管理器")


@fcmd.tool("cmderw", help="打开双面板文件管理器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开双面板文件管理器窗口。"""
    run_gui()


@fcmd.main("cmderw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
