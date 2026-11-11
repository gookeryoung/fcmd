"""cmderw - 双面板文件管理器（Tk GUI，Total Commander 风格）。

左右两个独立目录面板，支持键盘与鼠标操作：Tab 切换面板，Enter 进入目录，
F2 重命名、F3 查看、F4 外部编辑、F5 复制、F6 移动、F7 新建目录、F8 删除到
回收站、Shift+F8 永久删除，Alt+F5 打包、Alt+F9 解压，Ctrl+F 搜索，Ctrl+S
快速过滤，Num+/Num- 按掩码选择/取消，Space 统计选中项大小，Ctrl+H 显示隐藏文件，
Alt+Left/Alt+Right 在面板历史中后退/前进。顶部菜单栏按 文件/选中/视图/工具/命令/
配置/帮助 分组，菜单项与快捷键、右键菜单、工具栏调用同一套实现，并按当前选择数、
标签数、历史边界、忙碌状态与目录可写性动态启用/禁用。
每个面板支持多标签页（Ctrl+T 新建、Ctrl+W 关闭、Ctrl+Tab 切换，至少保留一个
标签），Ctrl+D 管理书签，Alt+Down 从当前面板历史栈跳转。

能力复用现有工具：打包/解压走 :mod:`fcmd.tools.archive.archivex`，文件哈希走
:mod:`fcmd.tools.hashbase`，文件名/内容搜索走 :mod:`fcmd.dsl.actions.fileops`，
窗口骨架、主题与设置持久化走 :mod:`fcmd.gui`。

设置（左右面板标签与路径、书签、排序列、隐藏文件开关、颜色规则、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/cmderw.json``，下次启动自动恢复；旧配置文件缺少
标签、书签或颜色规则字段时按单标签、空书签、空规则处理。

F 批能力：Ctrl+Q 内嵌快速查看（文本/图片/PE 与二进制，限读并提示截断），
Ctrl+F8 只读目录树（跟随活动面板），Ctrl+Enter 命令行栏（Enter 执行、Esc 清空，
删除类命令二次确认），「配置 → 颜色规则…」按掩码/大小/类型/时间着色。

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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.tools.system._cmderw_ops import (
    COLOR_KIND_ANY,
    COLOR_KIND_DIR,
    COLOR_KIND_FILE,
    COLOR_KIND_LINK,
    COLOR_KIND_TEXT,
    COLUMN_SORT,
    COMPARE_DIFFERENT,
    COMPARE_LEFT_ONLY,
    COMPARE_RIGHT_ONLY,
    COMPARE_SAME,
    COMPARE_STATUS_TEXT,
    DEFAULT_VIEW_MODE,
    ITEM_FAILED,
    PANEL_COLUMNS,
    PREVIEW_IMAGE,
    PREVIEW_UNSUPPORTED,
    QUEUE_CANCELLED,
    QUEUE_RUNNING,
    SYNC_DELETE,
    SYNC_LTR,
    SYNC_RTL,
    THUMBNAIL_BADGE,
    THUMBNAIL_GAP,
    THUMBNAIL_PILLOW,
    THUMBNAIL_TILE_SIZE,
    VIEW_MODE_BRIEF,
    VIEW_MODE_DETAILED,
    VIEW_MODE_THUMBNAILS,
    BulkRenameOptions,
    ColorRule,
    ColorStyle,
    ColorSubject,
    CompareOptions,
    CompareReport,
    MenuFacts,
    PreviewResult,
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
    ThumbnailCache,
    ThumbnailRequestTracker,
    TransferQueue,
    TrashReport,
    apply_rename_plan,
    build_sync_plan,
    cleanup_incomplete_items,
    column_display_names,
    compare_trees,
    destructive_command,
    directory_size,
    dump_color_rules,
    execute_sync_deletes,
    filter_names,
    invert_names,
    item_status_text,
    match_names,
    menu_state,
    normalize_color,
    normalize_column_widths,
    normalize_view_mode,
    parse_color_rules,
    pillow_available,
    plan_bulk_rename,
    read_preview,
    resolve_color_style,
    revalidate_sync_items,
    thumbnail_backend_note,
    thumbnail_cache_key,
    thumbnail_columns,
    thumbnail_plan,
    thumbnail_unsupported_hint,
    thumbnail_visible_rows,
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
    "Ctrl+Shift+C 只读比较与预演式目录同步，"
    "Ctrl+Q 快速查看、Ctrl+F8 目录树、Ctrl+Enter 命令行、配置→颜色规则，"
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

#: 面板列定义：(列 id, 标题, 宽度)，顺序固定，仅宽度可拖拽调整。
COLUMNS: tuple[tuple[str, str, int], ...] = PANEL_COLUMNS

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
_QUICK_IMAGE_LIMIT = 32 * 1024 * 1024
_SORT_MENU_CHOICES: tuple[tuple[str, str, str], ...] = (
    ("名称", "name", "name"),
    ("扩展名", "attr", "type"),
    ("大小", "size", "size"),
    ("修改时间", "mtime", "mtime"),
)
_VIEW_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("简要列表\tCtrl+F1", VIEW_MODE_BRIEF),
    ("详细列表\tCtrl+F2", VIEW_MODE_DETAILED),
    ("缩略图\tCtrl+Shift+F1", VIEW_MODE_THUMBNAILS),
)

#: 缩略图：后台线程数、单文件读取上限、主线程应用节奏、滚动防抖与网格尺寸。
_THUMB_WORKERS = 2
_THUMB_READ_LIMIT = 8 * 1024 * 1024
_THUMB_POLL_MS = 60
_THUMB_APPLY_PER_TICK = 2
_THUMB_REDRAW_MS = 80
_THUMB_TILE = THUMBNAIL_TILE_SIZE
_THUMB_LABEL_HEIGHT = 20
_THUMB_PAD = THUMBNAIL_GAP
_THUMB_CELL_WIDTH = _THUMB_TILE + 2 * _THUMB_PAD
_THUMB_CELL_HEIGHT = _THUMB_TILE + _THUMB_LABEL_HEIGHT + 2 * _THUMB_PAD


def _decode_thumbnail_with_pillow(data: bytes, tile_size: int) -> bytes | None:
    """在后台线程用 Pillow 解码并等比缩放到 tile_size，返回 PNG 字节。"""
    from importlib import import_module
    from io import BytesIO

    image_module = import_module("PIL.Image")
    try:
        with image_module.open(BytesIO(data)) as image:
            image.thumbnail((tile_size, tile_size))
            converted = image if image.mode in ("RGB", "RGBA") else image.convert("RGBA")
            output = BytesIO()
            converted.save(output, format="PNG")
            return output.getvalue()
    except Exception:
        return None


def load_thumbnail_payload(path: Path, plan: str, tile_size: int = _THUMB_TILE) -> bytes | None:
    """后台读取图片字节：最多 8 MiB；Pillow 路径先解码为 PNG，原生格式直接返回。"""
    with path.open("rb") as handle:
        data = handle.read(_THUMB_READ_LIMIT + 1)
    if len(data) > _THUMB_READ_LIMIT:
        return None
    if plan == THUMBNAIL_PILLOW:
        return _decode_thumbnail_with_pillow(data, tile_size)
    return data


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
    view_mode: str = DEFAULT_VIEW_MODE
    column_widths: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """序列化为设置文件中的字典。"""
        return {
            "path": self.path,
            "sort_key": self.sort_key,
            "descending": self.descending,
            "history": list(self.history),
            "history_index": self.history_index,
            "view_mode": self.view_mode,
            "column_widths": dict(self.column_widths),
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
    color_rules: list[ColorRule] = field(default_factory=list)


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
    state.view_mode = normalize_view_mode(data.get("view_mode"))
    state.column_widths = normalize_column_widths(data.get("column_widths"))
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
    settings.color_rules = parse_color_rules(raw.get("color_rules"))
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
        "color_rules": dump_color_rules(settings.color_rules),
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
        self._tk = tk
        self._ttk = ttk
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

        self._list_host = ttk.Frame(self.frame)
        self._list_host.pack(fill="both", expand=True)
        self._list_frame = ttk.Frame(self._list_host)
        self._list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            self._list_frame,
            columns=[column for column, _label, _width in COLUMNS],
            show="headings",
            selectmode="extended",
        )
        for column, label, width in COLUMNS:
            self.tree.heading(column, text=label, command=lambda c=column: self.sort_by(c))
            self.tree.column(column, width=width, anchor="w" if column == "name" else "e")
        scrollbar = ttk.Scrollbar(self._list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<ButtonRelease-1>", lambda _event: self._capture_column_widths(), add="+")

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

        self._quick_visible = False
        self._preview_photo: Any = None
        self._quick_frame = ttk.Frame(self.frame)
        quick_header = ttk.Frame(self._quick_frame)
        quick_header.pack(fill="x")
        self._quick_title_var = tk.StringVar(value="快速查看")
        ttk.Label(quick_header, textvariable=self._quick_title_var, style="Muted.TLabel").pack(side="left")
        self._quick_note_var = tk.StringVar()
        ttk.Label(quick_header, textvariable=self._quick_note_var, style="Muted.TLabel").pack(side="right")
        self._quick_body = ttk.Frame(self._quick_frame)
        self._quick_body.pack(fill="both", expand=True)
        self._quick_text = tk.Text(
            self._quick_body,
            height=10,
            wrap="none",
            state="disabled",
            font=("Consolas", 9),
        )
        self._quick_image = ttk.Label(self._quick_body, anchor="center")
        self._quick_text.pack(fill="both", expand=True)

        self.status_var = tk.StringVar()
        self._status_label = ttk.Label(self.frame, textvariable=self.status_var, style="Muted.TLabel")
        self._status_label.pack(fill="x", pady=(spacing.xxs, 0))
        self._init_thumbnail_view()
        self._apply_column_widths()
        self.apply_view_mode(self.state.view_mode)

    # ------------------------------------------------------------------ #
    # 视图模式与列宽
    # ------------------------------------------------------------------ #
    def apply_view_mode(self, mode: str) -> None:
        """切换当前标签的视图模式；简要/详细只改显示列，不重读目录。"""
        self.state.view_mode = normalize_view_mode(mode)
        if self.state.view_mode == VIEW_MODE_THUMBNAILS:
            self._enter_thumbnail_view()
        else:
            self._leave_thumbnail_view()
            self._apply_column_widths()
            display = column_display_names(self.state.view_mode)
            if display:
                self.tree.configure(displaycolumns=display)
                self.tree.column("name", stretch=True)
        self._update_status()

    def _apply_column_widths(self) -> None:
        """把当前标签记忆的列宽写回 Treeview，缺失列用默认宽度。"""
        for column, _label, default in COLUMNS:
            self.tree.column(column, width=self.state.column_widths.get(column, default))

    def _capture_column_widths(self) -> None:
        """读回拖拽后的列宽写入当前标签状态（供保存与标签切换保留）。"""
        widths: dict[str, int] = {}
        for column, _label, _default in COLUMNS:
            try:
                width = int(self.tree.column(column, "width"))
            except (ValueError, self._tk.TclError):
                continue
            if width > 0:
                widths[column] = width
        if widths:
            self.state.column_widths = widths

    # ------------------------------------------------------------------ #
    # 缩略图视图
    # ------------------------------------------------------------------ #
    def _init_thumbnail_view(self) -> None:
        """创建缩略图画布与后台加载状态；不在该模式时保持隐藏。"""
        from tkinter import ttk

        self._thumb_tracker = ThumbnailRequestTracker()
        self._thumb_cache = ThumbnailCache()
        self._thumb_has_pillow = pillow_available()
        self._thumb_results: queue.Queue[tuple[int, tuple[str, int, int, int], bytes | None]] = queue.Queue()
        self._thumb_futures: dict[tuple[str, int, int, int], Any] = {}
        self._thumb_executor: ThreadPoolExecutor | None = None
        self._thumb_poll_id: str | None = None
        self._thumb_redraw_id: str | None = None
        self._thumb_canvas = self._tk.Canvas(self._list_host, highlightthickness=0, takefocus=True)
        self._thumb_scroll = ttk.Scrollbar(self._list_host, orient="vertical", command=self._thumb_yview)
        self._thumb_canvas.configure(yscrollcommand=self._thumb_scroll.set)
        self._thumb_canvas.bind("<Configure>", lambda _event: self._request_thumb_redraw())
        self._thumb_canvas.bind("<MouseWheel>", self._on_thumb_wheel)
        self._thumb_canvas.bind("<Button-4>", lambda _event: self._on_thumb_wheel_linux(-1))
        self._thumb_canvas.bind("<Button-5>", lambda _event: self._on_thumb_wheel_linux(1))
        self._thumb_canvas.bind("<Button-1>", self._on_thumb_click)
        self._thumb_canvas.bind("<Double-1>", self._on_thumb_double_click)
        self._thumb_canvas.bind("<Up>", lambda _event: self._thumb_move(-1))
        self._thumb_canvas.bind("<Down>", lambda _event: self._thumb_move(1))
        self._thumb_canvas.bind("<Left>", lambda _event: self._thumb_move(-self._thumb_columns()))
        self._thumb_canvas.bind("<Right>", lambda _event: self._thumb_move(self._thumb_columns()))
        self._thumb_canvas.bind("<Prior>", lambda _event: self._thumb_move(-self._thumb_page_size()))
        self._thumb_canvas.bind("<Next>", lambda _event: self._thumb_move(self._thumb_page_size()))
        self._thumb_canvas.bind("<Return>", lambda _event: self._on_activate())
        self._thumb_canvas.bind("<BackSpace>", lambda _event: self.go_up())
        self._thumb_canvas.bind("<Button-3>", self.app.show_context_menu)

    def _is_thumbnail_view(self) -> bool:
        return self.state.view_mode == VIEW_MODE_THUMBNAILS

    @property
    def thumbnail_note(self) -> str:
        """缩略图后端说明（Pillow 可用性）。"""
        return thumbnail_backend_note(self._thumb_has_pillow)

    def _enter_thumbnail_view(self) -> None:
        """隐藏列表、显示网格，并让旧世代结果失效。"""
        if self._thumb_canvas.winfo_ismapped():
            return
        self._list_frame.pack_forget()
        self._thumb_canvas.pack(side="left", fill="both", expand=True)
        self._thumb_scroll.pack(side="right", fill="y")
        self._invalidate_thumbnails("mode")
        self._request_thumb_redraw(immediate=True)
        self._schedule_thumb_poll()

    def _leave_thumbnail_view(self) -> None:
        """回到列表视图：停止轮询、让后台世代失效并恢复列表。"""
        if not self._thumb_canvas.winfo_ismapped():
            return
        self._invalidate_thumbnails("mode")
        self._stop_thumb_poll()
        self._thumb_canvas.pack_forget()
        self._thumb_scroll.pack_forget()
        self._list_frame.pack(fill="both", expand=True)

    def _invalidate_thumbnails(self, reason: str) -> None:
        """刷新/切目录/切标签/切模式统一入口：递增世代号并取消未开始的读取。"""
        self._thumb_tracker.invalidate(reason)
        for future in self._thumb_futures.values():
            future.cancel()
        self._thumb_futures.clear()
        while True:
            try:
                self._thumb_results.get_nowait()
            except queue.Empty:
                break

    def _thumb_columns(self) -> int:
        return thumbnail_columns(self._thumb_canvas.winfo_width(), _THUMB_TILE, _THUMB_PAD)

    def _thumb_page_size(self) -> int:
        height = max(_THUMB_CELL_HEIGHT, self._thumb_canvas.winfo_height())
        return max(1, height // _THUMB_CELL_HEIGHT) * self._thumb_columns()

    def _thumb_yview(self, *args: Any) -> None:
        self._thumb_canvas.yview(*args)
        self._request_thumb_redraw()

    def _on_thumb_wheel(self, event: Any) -> str:
        self._thumb_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        self._request_thumb_redraw()
        return "break"

    def _on_thumb_wheel_linux(self, delta: int) -> str:
        self._thumb_canvas.yview_scroll(delta, "units")
        self._request_thumb_redraw()
        return "break"

    def _request_thumb_redraw(self, immediate: bool = False) -> None:
        """滚动/尺寸变化时防抖重绘，避免滚动过程中反复重建画布元素。"""
        if not self._is_thumbnail_view():
            return
        if self._thumb_redraw_id is not None:
            self._thumb_canvas.after_cancel(self._thumb_redraw_id)
            self._thumb_redraw_id = None
        delay = 0 if immediate else _THUMB_REDRAW_MS
        self._thumb_redraw_id = self._thumb_canvas.after(delay, self._draw_thumbnails)

    def _draw_thumbnails(self) -> None:
        """只创建可视区域 ±1 行的网格元素，实现画布虚拟化。"""
        self._thumb_redraw_id = None
        if not self._is_thumbnail_view():
            return
        self._thumb_canvas.delete("thumb")
        total = len(self.entries)
        columns = self._thumb_columns()
        rows = (total + columns - 1) // columns if total else 0
        self._thumb_canvas.configure(scrollregion=(0, 0, columns * _THUMB_CELL_WIDTH, rows * _THUMB_CELL_HEIGHT))
        if not total:
            self._thumb_canvas.create_text(
                12,
                12,
                anchor="nw",
                text="（空目录）",
                tags="thumb",
                fill=self.app.theme.palette.muted,
            )
            return
        offset = int(self._thumb_canvas.canvasy(0))
        viewport = max(_THUMB_CELL_HEIGHT, self._thumb_canvas.winfo_height())
        first, last = thumbnail_visible_rows(offset, viewport, _THUMB_CELL_HEIGHT, rows)
        selected = set(self.tree.selection())
        wanted: set[tuple[str, int, int, int]] = set()
        for row in range(first, last + 1):
            for column in range(columns):
                index = row * columns + column
                if index >= total:
                    break
                entry = self.entries[index]
                key = self._thumb_key(entry)
                wanted.add(key)
                self._draw_thumb_cell(entry, column, row, key, entry.name in selected)
        self._sync_thumb_wanted(wanted)

    def _draw_thumb_cell(
        self,
        entry: FileEntry,
        column: int,
        row: int,
        key: tuple[str, int, int, int],
        selected: bool,
    ) -> None:
        x0 = column * _THUMB_CELL_WIDTH + _THUMB_PAD
        y0 = row * _THUMB_CELL_HEIGHT + _THUMB_PAD
        self._thumb_canvas.create_rectangle(
            x0,
            y0,
            x0 + _THUMB_TILE,
            y0 + _THUMB_TILE + _THUMB_LABEL_HEIGHT,
            tags="thumb",
            fill=self.app.theme.palette.surface,
            outline=self.app.theme.palette.accent if selected else self.app.theme.palette.border,
            width=2 if selected else 1,
        )
        photo: Any = self._thumb_cache.get(key)
        plan = THUMBNAIL_BADGE if entry.is_dir else thumbnail_plan(entry.name, self._thumb_has_pillow)
        if photo is not None:
            self._thumb_canvas.create_image(
                x0 + (_THUMB_TILE - int(photo.width())) // 2,
                y0 + (_THUMB_TILE - int(photo.height())) // 2,
                anchor="nw",
                image=photo,
                tags="thumb",
            )
        else:
            self._thumb_canvas.create_text(
                x0 + _THUMB_TILE // 2,
                y0 + _THUMB_TILE // 2,
                text=self._thumb_badge_text(entry),
                tags="thumb",
                fill=self.app.theme.palette.muted,
                font=("Consolas", 10, "bold"),
            )
            if plan != THUMBNAIL_BADGE:
                self._schedule_thumbnail(entry, key)
        label = entry.name if len(entry.name) <= 13 else entry.name[:12] + "…"
        self._thumb_canvas.create_text(
            x0 + _THUMB_TILE // 2,
            y0 + _THUMB_TILE + _THUMB_LABEL_HEIGHT // 2,
            text=label,
            tags="thumb",
            fill=self.app.theme.palette.text,
        )

    @staticmethod
    def _thumb_badge_text(entry: FileEntry) -> str:
        if entry.is_dir:
            return "DIR"
        suffix = entry.path.suffix[1:].upper()
        return suffix[:6] if suffix else "FILE"

    @staticmethod
    def _thumb_key(entry: FileEntry) -> tuple[str, int, int, int]:
        return thumbnail_cache_key(str(entry.path), int(entry.mtime * 1_000_000_000), entry.size, _THUMB_TILE)

    def _sync_thumb_wanted(self, wanted: set[tuple[str, int, int, int]]) -> None:
        """滚动后取消已移出视野且尚未开始的读取，避免无谓 I/O。"""
        for key in list(self._thumb_futures):
            if key not in wanted:
                future = self._thumb_futures.pop(key)
                future.cancel()

    def _schedule_thumbnail(self, entry: FileEntry, key: tuple[str, int, int, int]) -> None:
        """按需提交后台读取；同一键只保留一个任务。"""
        if key in self._thumb_futures or key in self._thumb_cache:
            return
        plan = thumbnail_plan(entry.name, self._thumb_has_pillow)
        if plan == THUMBNAIL_BADGE:
            return
        executor = self._thumb_executor
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=_THUMB_WORKERS, thread_name_prefix="cmderw-thumb")
            self._thumb_executor = executor
        generation = self._thumb_tracker.generation
        self._thumb_futures[key] = executor.submit(self._thumbnail_worker, entry.path, key, generation, plan)
        self._schedule_thumb_poll()

    def _thumbnail_worker(
        self,
        path: Path,
        key: tuple[str, int, int, int],
        generation: int,
        plan: str,
    ) -> None:
        """后台线程：只读文件字节并解码，绝不触碰 Tk 对象。"""
        payload: bytes | None
        try:
            payload = load_thumbnail_payload(path, plan, _THUMB_TILE)
        except Exception:
            payload = None
        self._thumb_results.put((generation, key, payload))

    def _schedule_thumb_poll(self) -> None:
        if self._thumb_poll_id is None and self._is_thumbnail_view():
            self._thumb_poll_id = self._thumb_canvas.after(_THUMB_POLL_MS, self._poll_thumbnails)

    def _stop_thumb_poll(self) -> None:
        if self._thumb_poll_id is not None:
            self._thumb_canvas.after_cancel(self._thumb_poll_id)
            self._thumb_poll_id = None

    def _poll_thumbnails(self) -> None:
        """主线程每 60ms 最多创建 2 个 PhotoImage，避免一次性卡住界面。"""
        self._thumb_poll_id = None
        if not self._is_thumbnail_view():
            return
        applied = 0
        while applied < _THUMB_APPLY_PER_TICK:
            try:
                generation, key, payload = self._thumb_results.get_nowait()
            except queue.Empty:
                break
            self._thumb_futures.pop(key, None)
            if not self._thumb_tracker.accept(generation):
                continue
            photo = self._create_thumb_photo(payload)
            self._thumb_cache.put(key, photo, self._thumb_photo_bytes(photo))
            applied += 1
        if applied:
            self._request_thumb_redraw(immediate=True)
        if self._thumb_futures or not self._thumb_results.empty():
            self._schedule_thumb_poll()

    def _create_thumb_photo(self, payload: bytes | None) -> Any:
        if payload is None:
            return None
        try:
            photo = self._tk.PhotoImage(data=payload)
        except self._tk.TclError:
            return None
        largest = max(photo.width(), photo.height())
        if largest > _THUMB_TILE * 2:
            factor = max(2, largest // _THUMB_TILE)
            with contextlib.suppress(self._tk.TclError):
                photo = photo.subsample(factor)
        return photo

    @staticmethod
    def _thumb_photo_bytes(photo: Any) -> int:
        if photo is None:
            return 0
        return int(photo.width()) * int(photo.height()) * 4

    def _on_thumb_click(self, event: Any) -> None:
        self._thumb_canvas.focus_set()
        index = self._thumb_index_at(event.x, event.y)
        if index is None:
            return
        entry = self.entries[index]
        self.tree.selection_set(entry.name)
        self.tree.focus(entry.name)
        self._update_status()
        self._request_thumb_redraw(immediate=True)

    def _on_thumb_double_click(self, event: Any) -> None:
        self._on_thumb_click(event)
        self._on_activate(event)

    def _thumb_index_at(self, x: int, y: int) -> int | None:
        columns = self._thumb_columns()
        row = int(self._thumb_canvas.canvasy(y)) // _THUMB_CELL_HEIGHT
        column = int(x) // _THUMB_CELL_WIDTH
        if column < 0 or column >= columns:
            return None
        index = row * columns + column
        if 0 <= index < len(self.entries):
            return index
        return None

    def _thumb_move(self, delta: int) -> str:
        if not self.entries:
            return "break"
        names = [entry.name for entry in self.entries]
        current = self.tree.focus()
        index = names.index(current) if current in names else 0
        target = min(max(0, index + delta), len(names) - 1)
        self.tree.selection_set(names[target])
        self.tree.focus(names[target])
        columns = self._thumb_columns()
        total_rows = max(1, (len(names) + columns - 1) // columns)
        offset = max(0, target // columns - 1) * _THUMB_CELL_HEIGHT
        self._thumb_canvas.yview_moveto(min(1.0, offset / (total_rows * _THUMB_CELL_HEIGHT)))
        self._update_status()
        self._request_thumb_redraw(immediate=True)
        return "break"

    def shutdown_thumbnails(self) -> None:
        """关窗前停止轮询、取消任务并等待线程退出，不留后台线程。"""
        self._invalidate_thumbnails("close")
        self._stop_thumb_poll()
        if self._thumb_redraw_id is not None:
            with contextlib.suppress(self._tk.TclError):
                self._thumb_canvas.after_cancel(self._thumb_redraw_id)
            self._thumb_redraw_id = None
        executor = self._thumb_executor
        self._thumb_executor = None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        self._thumb_cache.clear()

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
        self._capture_column_widths()
        self._invalidate_thumbnails("tab")
        self.tab_index = index
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        self.app.set_status(f"{self.title}标签 {self.tab_index + 1}/{len(self.tabs)}：{self.state.path}")

    def new_tab(self, path: str | None = None) -> None:
        """新建标签页：默认沿用当前目录、排序方式、视图模式与列宽。"""
        source = self.state
        self._capture_column_widths()
        target = path or source.path
        self.tabs.append(
            PanelState(
                path=target,
                sort_key=source.sort_key,
                descending=source.descending,
                history=[target],
                history_index=0,
                view_mode=source.view_mode,
                column_widths=dict(source.column_widths),
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
        self._capture_column_widths()
        self._invalidate_thumbnails("tab")
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
        self._invalidate_thumbnails("refresh")
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
        now = time.time()
        color_tags: dict[str, str] = {}
        for entry in entries:
            tags: list[str] = []
            if entry.is_dir:
                tags.append("dir")
            if entry.hidden:
                tags.append("hidden")
            style = resolve_color_style(
                self.app.settings.color_rules,
                ColorSubject(
                    name=entry.name,
                    is_dir=entry.is_dir,
                    is_symlink=entry.is_symlink,
                    hidden=entry.hidden,
                    size=entry.size,
                    mtime=entry.mtime,
                ),
                now,
            )
            tag = self._color_tag(style, color_tags)
            if tag:
                tags.append(tag)
            self.tree.insert(
                "",
                "end",
                iid=entry.name,
                values=(
                    entry.name,
                    "" if entry.is_dir else entry.path.suffix[1:],
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
        self.apply_view_mode(self.state.view_mode)
        if self._is_thumbnail_view():
            self._request_thumb_redraw(immediate=True)
        if self._quick_visible:
            self.update_quick_view()
        self.app.on_panel_refreshed(self)

    def _color_tag(self, style: ColorStyle | None, cache: dict[str, str]) -> str:
        """按合并后的颜色生成 Treeview tag；同类颜色只配置一次。"""
        if style is None:
            return ""
        tag = f"color{style.foreground}{style.background}"
        if tag not in cache:
            if style.foreground and style.background:
                self.tree.tag_configure(tag, foreground=style.foreground, background=style.background)
            elif style.foreground:
                self.tree.tag_configure(tag, foreground=style.foreground)
            elif style.background:
                self.tree.tag_configure(tag, background=style.background)
            cache[tag] = tag
        return tag

    # ------------------------------------------------------------------ #
    # 快速查看（文本 / 图片 / 二进制，限读 + 截断提示）
    # ------------------------------------------------------------------ #
    @property
    def quick_visible(self) -> bool:
        """快速查看当前是否展开（供菜单勾选状态同步）。"""
        return self._quick_visible

    def toggle_quick_view(self) -> None:
        """Ctrl+Q：显示或隐藏内嵌快速查看。"""
        if self._quick_visible:
            self.hide_quick_view()
        else:
            self.show_quick_view()

    def show_quick_view(self) -> None:
        """展开快速查看区域并预览当前选中文件。"""
        self.app.set_active(self)
        if not self._quick_visible:
            self._quick_visible = True
            self._quick_frame.pack(
                fill="both",
                expand=True,
                before=self._status_label,
                pady=(self.app.theme.spacing.xs, 0),
            )
        self.update_quick_view()

    def hide_quick_view(self) -> None:
        """收起快速查看并释放图片引用。"""
        if self._quick_visible:
            self._quick_visible = False
            self._quick_frame.pack_forget()
        self._preview_photo = None
        self._quick_image.configure(image="")
        self._set_quick_text("")

    def update_quick_view(self) -> None:
        """按当前选择刷新快速查看内容。"""
        if not self._quick_visible:
            return
        selected = self.selected_entries()
        if len(selected) != 1:
            note = "请选择一个文件" if not selected else f"已选择 {len(selected)} 项，只预览单个文件"
            self._show_quick_note("快速查看", note)
            return
        entry = selected[0]
        self._quick_title_var.set(f"快速查看 - {entry.name}")
        if entry.is_dir:
            self._show_quick_note(f"快速查看 - {entry.name}", "目录不预览，双击进入")
            return
        result = read_preview(entry.path)
        self._quick_note_var.set(result.note)
        if result.kind == PREVIEW_IMAGE:
            self._show_quick_image(entry.path, result)
        elif result.kind == PREVIEW_UNSUPPORTED:
            self._show_quick_text(result.note or "无法预览该类型")
        else:
            self._show_quick_text(result.text or "（空文件）")

    def _show_quick_note(self, title: str, note: str) -> None:
        """无文件可预览时给出明确提示，而不是空白面板。"""
        self._quick_title_var.set(title)
        self._quick_note_var.set(note)
        self._show_quick_text(note)

    def _show_quick_text(self, text: str) -> None:
        """显示文本（二进制为十六进制）并隐藏图片区。"""
        self._preview_photo = None
        self._quick_image.pack_forget()
        if not self._quick_text.winfo_ismapped():
            self._quick_text.pack(fill="both", expand=True)
        self._set_quick_text(text)

    def _set_quick_text(self, text: str) -> None:
        widget = self._quick_text
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        if text:
            widget.insert("1.0", text)
        widget.configure(state="disabled")

    def _show_quick_image(self, path: Path, result: PreviewResult) -> None:
        """在面板内渲染图片；Tk 不支持的格式给出明确提示。"""
        self._quick_text.pack_forget()
        if result.size > _QUICK_IMAGE_LIMIT:
            self._show_quick_text(f"{result.note}\n图片超过 32 MB，未在窗口内加载，可用 F3 或外部程序查看。")
            return
        try:
            photo = self._tk.PhotoImage(file=str(path))
        except self._tk.TclError:
            self._show_quick_text(f"{result.note}\n当前 Tk 不支持该图片格式，可用 F3 或外部程序查看。")
            return
        largest = max(photo.width(), photo.height())
        if largest > 640:
            factor = max(1, largest // 640)
            photo = photo.subsample(factor, factor)
        self._preview_photo = photo
        self._quick_image.configure(image=photo)
        self._quick_image.pack(fill="both", expand=True)

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
            if self._is_thumbnail_view() and len(selected) == 1 and not selected[0].is_dir:
                entry = selected[0]
                if thumbnail_plan(entry.name, self._thumb_has_pillow) == THUMBNAIL_BADGE:
                    text += f" | {thumbnail_unsupported_hint(entry.name, self._thumb_has_pillow)}"
        self.status_var.set(text)

    def _on_select(self, _event: Any = None) -> None:
        self.app.set_active(self)
        self._update_status()
        if self._quick_visible:
            self.update_quick_view()
        if self._is_thumbnail_view():
            self._request_thumb_redraw(immediate=True)
        self.app.on_panel_selection(self)

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
        if text != self.state.path:
            self._invalidate_thumbnails("navigate")
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
            self._invalidate_thumbnails("navigate")
            self.state.history_index = index
            self.state.path = self.state.history[index]
            self.path_var.set(self.state.path)
            self.refresh()

    def go_forward(self) -> None:
        """前进到历史下一项。"""
        index = history_forward(self.state.history, self.state.history_index)
        if index != self.state.history_index:
            self._invalidate_thumbnails("navigate")
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

    def clear_selection(self) -> None:
        """取消当前目录的全部选择。"""
        selection = self.tree.selection()
        if selection:
            self.tree.selection_remove(*selection)
            self._update_status()

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


_COLOR_KIND_CHOICES: tuple[tuple[str, str], ...] = (
    ("任意", COLOR_KIND_ANY),
    ("文件", COLOR_KIND_FILE),
    ("目录", COLOR_KIND_DIR),
    ("符号链接", COLOR_KIND_LINK),
)

_HIDDEN_CHOICES: tuple[tuple[str, bool | None], ...] = (
    ("任意", None),
    ("仅隐藏", True),
    ("仅可见", False),
)


class ColorRulesDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """颜色规则编辑器：列表顺序即优先级，靠下的规则覆盖靠上的同色属性。"""

    def __init__(self, app: CmderWindow, rules: Sequence[ColorRule]) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.rules = list(rules)
        self._editing: int | None = None
        spacing = app.theme.spacing
        self.window = tk.Toplevel(app.root)
        self.window.title("颜色规则")
        self.window.transient(app.root)
        _guiwindow.center_over_parent(self.window, app.root, width=940, height=560)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=spacing.sm)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text="规则按列表顺序合并：靠下的规则覆盖靠上的同色属性；留空的条件不参与匹配。"
            "颜色格式为 #rrggbb，至少设置前景或背景之一才会生效。",
            style="Muted.TLabel",
            wraplength=900,
        ).pack(fill="x", pady=(0, spacing.xs))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, padding=(spacing.sm, 0, 0, 0))
        right.pack(side="left", fill="y")

        self._list = ttk.Treeview(
            left,
            columns=("order", "pattern", "cond", "colors"),
            show="headings",
            selectmode="browse",
            height=15,
        )
        for column, title, width in (
            ("order", "#", 40),
            ("pattern", "掩码", 150),
            ("cond", "条件", 300),
            ("colors", "颜色", 210),
        ):
            self._list.heading(column, text=title)
            self._list.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self._list.yview)
        self._list.configure(yscrollcommand=scrollbar.set)
        self._list.pack(side="top", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._list.bind("<<TreeviewSelect>>", self._on_select_rule)

        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(spacing.xs, 0))
        ttk.Button(actions, text="新建", style="Toolbutton", command=self._new_rule).pack(side="left")
        ttk.Button(actions, text="删除", style="Toolbutton", command=self._delete_rule).pack(side="left")
        ttk.Button(actions, text="上移", style="Toolbutton", command=lambda: self._move(-1)).pack(side="left")
        ttk.Button(actions, text="下移", style="Toolbutton", command=lambda: self._move(1)).pack(side="left")

        self._pattern_var = tk.StringVar()
        self._min_var = tk.StringVar()
        self._max_var = tk.StringVar()
        self._kind_var = tk.StringVar()
        self._hidden_var = tk.StringVar()
        self._newer_var = tk.StringVar()
        self._older_var = tk.StringVar()
        self._fg_var = tk.StringVar()
        self._bg_var = tk.StringVar()

        row = 0
        row = self._form_entry(right, row, "掩码", self._pattern_var)
        row = self._form_entry(right, row, "最小大小", self._min_var)
        row = self._form_entry(right, row, "最大大小", self._max_var)
        row = self._form_combo(right, row, "类型", self._kind_var, [text for text, _value in _COLOR_KIND_CHOICES])
        row = self._form_combo(right, row, "隐藏属性", self._hidden_var, [text for text, _value in _HIDDEN_CHOICES])
        row = self._form_entry(right, row, "最近天数", self._newer_var)
        row = self._form_entry(right, row, "早于天数", self._older_var)
        row = self._color_row(right, row, "前景", self._fg_var)
        row = self._color_row(right, row, "背景", self._bg_var)
        ttk.Label(
            right,
            text="掩码支持 * 与 ?；大小单位为字节；时间按天。",
            style="Muted.TLabel",
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=(spacing.xs, 0))
        row += 1
        ttk.Button(right, text="添加 / 更新规则", command=self._commit_rule).grid(
            row=row,
            column=0,
            columnspan=2,
            sticky="ew",
            pady=(spacing.xs, 0),
        )

        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(footer, text="取消", command=self.window.destroy).pack(side="right")
        ttk.Button(footer, text="保存并关闭", style="Accent.TButton", command=self._save).pack(
            side="right",
            padx=(0, spacing.xs),
        )

        self._render()
        self._new_rule()

    def _form_entry(self, parent: Any, row: int, label: str, var: Any) -> int:
        from tkinter import ttk

        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var, width=24).grid(row=row, column=1, sticky="ew", pady=2)
        return row + 1

    def _form_combo(self, parent: Any, row: int, label: str, var: Any, values: Sequence[str]) -> int:
        from tkinter import ttk

        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Combobox(parent, textvariable=var, values=list(values), state="readonly", width=22).grid(
            row=row,
            column=1,
            sticky="ew",
            pady=2,
        )
        return row + 1

    def _color_row(self, parent: Any, row: int, label: str, var: Any) -> int:
        from tkinter import ttk

        frame = ttk.Frame(parent)
        frame.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
        ttk.Label(frame, text=label, width=8).pack(side="left")
        ttk.Entry(frame, textvariable=var, width=12).pack(side="left")
        ttk.Button(frame, text="选择", style="Toolbutton", command=lambda: self._pick_color(var)).pack(
            side="left",
            padx=2,
        )
        ttk.Button(frame, text="清除", style="Toolbutton", command=lambda: var.set("")).pack(side="left")
        return row + 1

    @staticmethod
    def _condition_text(rule: ColorRule) -> str:
        parts: list[str] = []
        if rule.kind:
            parts.append(COLOR_KIND_TEXT.get(rule.kind, rule.kind))
        if rule.hidden is True:
            parts.append("仅隐藏")
        elif rule.hidden is False:
            parts.append("仅可见")
        if rule.min_size is not None:
            parts.append(f"≥{rule.min_size}B")
        if rule.max_size is not None:
            parts.append(f"≤{rule.max_size}B")
        if rule.newer_days is not None:
            parts.append(f"{rule.newer_days:g} 天内")
        if rule.older_days is not None:
            parts.append(f"早于 {rule.older_days:g} 天")
        return " / ".join(parts) or "任意"

    @staticmethod
    def _color_text(rule: ColorRule) -> str:
        parts: list[str] = []
        if rule.foreground:
            parts.append(f"字体 {rule.foreground}")
        if rule.background:
            parts.append(f"背景 {rule.background}")
        return " / ".join(parts)

    def _rule_tag(self, rule: ColorRule, cache: dict[str, str]) -> str:
        tag = f"rule{rule.foreground}{rule.background}"
        if tag not in cache:
            if rule.foreground and rule.background:
                self._list.tag_configure(tag, foreground=rule.foreground, background=rule.background)
            elif rule.foreground:
                self._list.tag_configure(tag, foreground=rule.foreground)
            elif rule.background:
                self._list.tag_configure(tag, background=rule.background)
            cache[tag] = tag
        return tag

    def _render(self) -> None:
        for child in self._list.get_children():
            self._list.delete(child)
        cache: dict[str, str] = {}
        for index, rule in enumerate(self.rules):
            tag = self._rule_tag(rule, cache)
            self._list.insert(
                "",
                "end",
                iid=str(index),
                values=(index + 1, rule.pattern, self._condition_text(rule), self._color_text(rule)),
                tags=(tag,),
            )
        if self._editing is not None and self._list.exists(str(self._editing)):
            self._list.selection_set(str(self._editing))

    def _on_select_rule(self, _event: Any = None) -> None:
        selection = self._list.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self.rules):
            self._editing = index
            self._load_rule(self.rules[index])

    def _load_rule(self, rule: ColorRule) -> None:
        self._pattern_var.set(rule.pattern)
        self._min_var.set("" if rule.min_size is None else str(rule.min_size))
        self._max_var.set("" if rule.max_size is None else str(rule.max_size))
        self._kind_var.set(COLOR_KIND_TEXT.get(rule.kind, COLOR_KIND_TEXT[COLOR_KIND_ANY]))
        self._hidden_var.set(self._hidden_text(rule.hidden))
        self._newer_var.set("" if rule.newer_days is None else f"{rule.newer_days:g}")
        self._older_var.set("" if rule.older_days is None else f"{rule.older_days:g}")
        self._fg_var.set(rule.foreground)
        self._bg_var.set(rule.background)

    @staticmethod
    def _hidden_text(value: bool | None) -> str:
        for text, choice in _HIDDEN_CHOICES:
            if choice is value:
                return text
        return "任意"

    def _new_rule(self) -> None:
        self._editing = None
        selection = self._list.selection()
        if selection:
            self._list.selection_remove(*selection)
        self._load_rule(ColorRule())

    def _delete_rule(self) -> None:
        if self._editing is None or not 0 <= self._editing < len(self.rules):
            return
        del self.rules[self._editing]
        self._editing = None
        self._render()
        self._new_rule()

    def _move(self, delta: int) -> None:
        if self._editing is None:
            return
        target = self._editing + delta
        if not 0 <= target < len(self.rules):
            return
        self.rules[self._editing], self.rules[target] = self.rules[target], self.rules[self._editing]
        self._editing = target
        self._render()

    @staticmethod
    def _optional_int(text: str) -> int | None:
        text = text.strip()
        if not text:
            return None
        try:
            value = int(text)
        except ValueError:
            raise ValueError(f"大小必须是整数：{text}") from None
        if value < 0:
            raise ValueError("大小不能为负数")
        return value

    @staticmethod
    def _optional_days(text: str) -> float | None:
        text = text.strip()
        if not text:
            return None
        try:
            value = float(text)
        except ValueError:
            raise ValueError(f"天数必须是数字：{text}") from None
        if value <= 0:
            raise ValueError("天数必须大于 0")
        return value

    @staticmethod
    def _choice_value(text: str, choices: Sequence[tuple[str, Any]], fallback: Any) -> Any:
        for label, value in choices:
            if label == text:
                return value
        return fallback

    def _collect_rule(self) -> ColorRule | None:
        try:
            min_size = self._optional_int(self._min_var.get())
            max_size = self._optional_int(self._max_var.get())
            newer = self._optional_days(self._newer_var.get())
            older = self._optional_days(self._older_var.get())
        except ValueError as exc:
            self.app.show_error("颜色规则", str(exc))
            return None
        if min_size is not None and max_size is not None and min_size > max_size:
            self.app.show_error("颜色规则", "最小大小不能大于最大大小")
            return None
        if newer is not None and older is not None and newer >= older:
            self.app.show_error("颜色规则", "「最近天数」必须小于「早于天数」")
            return None
        foreground = normalize_color(self._fg_var.get())
        background = normalize_color(self._bg_var.get())
        if (self._fg_var.get().strip() and not foreground) or (self._bg_var.get().strip() and not background):
            self.app.show_error("颜色规则", "颜色必须为 #rrggbb 格式")
            return None
        if not foreground and not background:
            self.app.show_error("颜色规则", "请至少设置前景或背景颜色")
            return None
        return ColorRule(
            pattern=self._pattern_var.get().strip() or "*",
            min_size=min_size,
            max_size=max_size,
            kind=self._choice_value(self._kind_var.get(), _COLOR_KIND_CHOICES, COLOR_KIND_ANY),
            hidden=self._choice_value(self._hidden_var.get(), _HIDDEN_CHOICES, None),
            newer_days=newer,
            older_days=older,
            foreground=foreground,
            background=background,
        )

    def _commit_rule(self) -> None:
        rule = self._collect_rule()
        if rule is None:
            return
        if self._editing is None:
            self.rules.append(rule)
            self._editing = len(self.rules) - 1
        else:
            self.rules[self._editing] = rule
        self._render()
        self.app.set_status(f"颜色规则已更新（{len(self.rules)} 条，尚未保存）")

    def _pick_color(self, var: Any) -> None:
        from tkinter import colorchooser

        _rgb, selected = colorchooser.askcolor(color=normalize_color(var.get()) or "#ffffff", parent=self.window)
        if selected:
            var.set(selected.lower())

    def _save(self) -> None:
        self.app.apply_color_rules(self.rules)
        self.window.destroy()


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
        self._tree_visible = False
        self._tree_syncing = False
        self._tree_loaded: set[str] = set()
        self._command_visible = False
        self._color_dialog: ColorRulesDialog | None = None

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
        self._build_command_bar()
        self._build_statusbar()
        self._bind_shortcuts()

        self.active_side = self.settings.active if self.settings.active in ("left", "right") else "left"
        self.left.set_path(Path(self.settings.left.path))
        self.right.set_path(Path(self.settings.right.path))
        self.set_active(self.left if self.active_side == "left" else self.right)
        self._sync_menu_state()
        self.set_status("就绪")

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _menu_command(self, menu: Any, key: str, label: str, command: Callable[[], None]) -> None:
        """添加普通菜单项并登记，供展开时统一启用/禁用。"""
        menu.add_command(label=label, command=command)
        self._menu_items[key] = (menu, menu.index("end"))

    def _menu_check(
        self,
        menu: Any,
        key: str,
        label: str,
        variable: Any,
        command: Callable[[], None],
    ) -> None:
        menu.add_checkbutton(label=label, variable=variable, command=command)
        self._menu_items[key] = (menu, menu.index("end"))

    def _menu_radio(
        self,
        menu: Any,
        label: str,
        variable: Any,
        value: str,
        command: Callable[[], None],
    ) -> None:
        menu.add_radiobutton(label=label, variable=variable, value=value, command=command)

    def _build_menu(self) -> None:
        tk = self._tk
        self._menu_items: dict[str, tuple[Any, int]] = {}
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False, postcommand=self._sync_menu_state)
        self._menu_command(file_menu, "open", "打开\tEnter", self.open_selected)
        self._menu_command(file_menu, "view", "查看\tF3", self.view_selected)
        self._menu_command(file_menu, "edit", "编辑\tF4", self.edit_selected)
        file_menu.add_separator()
        self._menu_command(file_menu, "new_folder", "新建目录\tF7", self.new_folder)
        self._menu_command(file_menu, "rename", "重命名\tF2", self.rename_selected)
        self._menu_command(file_menu, "bulk_rename", "批量重命名…\tCtrl+M", self.bulk_rename)
        file_menu.add_separator()
        self._menu_command(file_menu, "copy", "复制到对面面板\tF5", self.copy_selected)
        self._menu_command(file_menu, "move", "移动到对面面板\tF6", self.move_selected)
        self._menu_command(file_menu, "delete", "删除到回收站\tF8", self.delete_selected)
        self._menu_command(
            file_menu,
            "permanent_delete",
            "永久删除\tShift+F8",
            self.permanent_delete_selected,
        )
        file_menu.add_separator()
        self._menu_command(file_menu, "properties", "属性\tAlt+Enter", self.show_properties)
        self._menu_command(file_menu, "hash", "计算校验和…", self.hash_selected)
        file_menu.add_separator()
        self._menu_command(file_menu, "pack", "打包…\tAlt+F5", self.pack_selected)
        self._menu_command(file_menu, "unpack", "解压…\tAlt+F9", self.unpack_selected)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self.close)
        menubar.add_cascade(label="文件", menu=file_menu)

        select_menu = tk.Menu(menubar, tearoff=False, postcommand=self._sync_menu_state)
        self._menu_command(select_menu, "select_all", "全选\tCtrl+A", self.select_all)
        self._menu_command(select_menu, "clear_selection", "取消全选", self.clear_selection)
        self._menu_command(select_menu, "invert_selection", "反选\tCtrl+I", self.invert_selection)
        select_menu.add_separator()
        self._menu_command(select_menu, "select_mask", "按掩码选择…\tNum+", self.select_by_mask)
        self._menu_command(select_menu, "deselect_mask", "按掩码取消选择…\tNum-", self.deselect_by_mask)
        menubar.add_cascade(label="选中", menu=select_menu)

        view_menu = tk.Menu(menubar, tearoff=False, postcommand=self._sync_menu_state)
        self._menu_command(view_menu, "refresh", "刷新\tCtrl+R", self.refresh_both)
        self._hidden_var = tk.BooleanVar(value=self.settings.show_hidden)
        self._menu_check(view_menu, "hidden", "显示隐藏文件\tCtrl+H", self._hidden_var, self._apply_hidden)
        view_menu.add_separator()
        self._menu_command(view_menu, "filter", "快速过滤\tCtrl+S", self.focus_filter)
        self._menu_command(view_menu, "clear_filter", "清除过滤\tEsc", self.clear_filter)
        view_menu.add_separator()
        self._quick_var = tk.BooleanVar(value=False)
        self._menu_check(
            view_menu,
            "quick_view",
            "快速查看\tCtrl+Q",
            self._quick_var,
            self._set_quick_from_var,
        )
        self._tree_var = tk.BooleanVar(value=False)
        self._menu_check(
            view_menu,
            "directory_tree",
            "目录树\tCtrl+F8",
            self._tree_var,
            self.toggle_directory_tree,
        )
        view_menu.add_separator()
        view_mode_menu = tk.Menu(view_menu, tearoff=False, postcommand=self._sync_menu_state)
        self._view_var = tk.StringVar(value=DEFAULT_VIEW_MODE)
        for label, mode in _VIEW_MODE_CHOICES:
            self._menu_radio(
                view_mode_menu,
                label,
                self._view_var,
                mode,
                lambda value=mode: self.set_view_mode(value),
            )
        view_menu.add_cascade(label="视图模式", menu=view_mode_menu)
        sort_menu = tk.Menu(view_menu, tearoff=False, postcommand=self._sync_menu_state)
        self._sort_var = tk.StringVar(value=DEFAULT_SORT_KEY)
        for label, column, key in _SORT_MENU_CHOICES:
            self._menu_radio(
                sort_menu,
                label,
                self._sort_var,
                key,
                lambda column=column: self.sort_active(column),
            )
            end = sort_menu.index("end")
            if end is not None:
                self._menu_items[f"sort_{key}"] = (sort_menu, end)
        view_menu.add_cascade(label="排序方式", menu=sort_menu)
        view_menu.add_separator()
        self._menu_command(view_menu, "up", "上级目录\tBackspace", self.go_up)
        self._menu_command(view_menu, "back", "后退\tAlt+Left", self.go_back)
        self._menu_command(view_menu, "forward", "前进\tAlt+Right", self.go_forward)
        view_menu.add_separator()
        self._menu_command(view_menu, "new_tab", "新建标签页\tCtrl+T", self.new_tab)
        self._menu_command(view_menu, "close_tab", "关闭标签页\tCtrl+W", self.close_tab)
        self._menu_command(view_menu, "next_tab", "下一个标签页\tCtrl+Tab", self.next_tab)
        self._menu_command(view_menu, "previous_tab", "上一个标签页\tCtrl+Shift+Tab", self.previous_tab)
        view_menu.add_separator()
        self._menu_command(view_menu, "swap_panel", "交换面板\tCtrl+U", self.swap_panels)
        self._menu_command(view_menu, "switch_panel", "切换面板\tTab", self.switch_panel)
        menubar.add_cascade(label="视图", menu=view_menu)

        tools_menu = tk.Menu(menubar, tearoff=False, postcommand=self._sync_menu_state)
        self._menu_command(tools_menu, "search", "搜索\tCtrl+F", self.open_search)
        self._menu_command(tools_menu, "compare", "目录比较…\tCtrl+Shift+C", self.open_compare)
        self._menu_command(tools_menu, "sync", "目录同步…", self.open_sync_from_compare)
        tools_menu.add_separator()
        self._menu_command(tools_menu, "queue", "传输队列…", self.show_transfer_queue)
        menubar.add_cascade(label="工具", menu=tools_menu)

        commands_menu = tk.Menu(menubar, tearoff=False, postcommand=self._sync_menu_state)
        self._menu_command(
            commands_menu,
            "command_line",
            "聚焦命令行栏\tCtrl+Enter",
            self.focus_command_bar,
        )
        self._menu_command(commands_menu, "command_hide", "隐藏命令行栏", self.hide_command_bar)
        commands_menu.add_separator()
        self._menu_command(commands_menu, "bookmarks", "书签…\tCtrl+D", self.open_bookmarks)
        self._menu_command(commands_menu, "history", "历史记录…\tAlt+Down", self.show_history_menu)
        menubar.add_cascade(label="命令", menu=commands_menu)

        config_menu = tk.Menu(menubar, tearoff=False, postcommand=self._sync_menu_state)
        self._menu_command(config_menu, "color_rules", "颜色规则…", self.open_color_rules)
        menubar.add_cascade(label="配置", menu=config_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="快捷键", command=self.show_shortcuts)
        help_menu.add_command(label="关于", command=self.show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)
        self.root.configure(menu=menubar)
        self._context_menu = self._build_context_menu()

    def _build_context_menu(self) -> Any:
        menu = self._tk.Menu(self.root, tearoff=False, postcommand=self._sync_menu_state)
        menu.add_command(label="打开", command=self.open_selected)
        menu.add_command(label="查看", command=self.view_selected)
        menu.add_command(label="编辑", command=self.edit_selected)
        view_mode_menu = self._tk.Menu(menu, tearoff=False, postcommand=self._sync_menu_state)
        for label, mode in _VIEW_MODE_CHOICES:
            view_mode_menu.add_radiobutton(
                label=label.split("\t", 1)[0],
                variable=self._view_var,
                value=mode,
                command=lambda value=mode: self.set_view_mode(value),
            )
        menu.add_cascade(label="视图模式", menu=view_mode_menu)
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
        ttk.Checkbutton(toolbar, text="隐藏文件", variable=self._hidden_var, command=self._apply_hidden).pack(
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
        self._build_directory_tree()

    def _build_directory_tree(self) -> None:
        """创建目录树容器；默认不加入分栏，Ctrl+F8 时才显示。"""
        ttk = self._ttk
        self._tree_frame = ttk.Frame(self._paned, padding=self.theme.spacing.xs)
        self._tree = ttk.Treeview(self._tree_frame, show="tree", selectmode="browse")
        self._tree.column("#0", width=240, minwidth=120, stretch=True)
        scrollbar = ttk.Scrollbar(self._tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._tree.tag_configure("current", foreground=self.theme.palette.accent)
        self._tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self._tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._populate_tree_roots()

    def _build_command_bar(self) -> None:
        """创建命令行栏；默认隐藏，Ctrl+Enter 唤出。"""
        ttk = self._ttk
        spacing = self.theme.spacing
        self._command_frame = ttk.Frame(self.root, padding=(spacing.sm, spacing.xxs))
        self._command_frame.grid(row=2, column=0, sticky="ew")
        ttk.Label(self._command_frame, text="命令：").pack(side="left")
        self._command_var = self._tk.StringVar()
        self._command_entry = ttk.Entry(self._command_frame, textvariable=self._command_var)
        self._command_entry.pack(side="left", fill="x", expand=True, padx=(0, spacing.xs))
        self._command_entry.bind("<Return>", self._on_command_enter)
        self._command_entry.bind("<KP_Enter>", self._on_command_enter)
        self._command_entry.bind("<Escape>", self._on_command_escape)
        self._command_cwd_var = self._tk.StringVar()
        ttk.Label(
            self._command_frame,
            textvariable=self._command_cwd_var,
            style="Muted.TLabel",
        ).pack(side="left")
        self._command_frame.grid_remove()

    def _build_statusbar(self) -> None:
        ttk = self._ttk
        spacing = self.theme.spacing
        status = ttk.Frame(self.root, padding=(spacing.sm, spacing.xxs))
        status.grid(row=3, column=0, sticky="ew")
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
            "<Control-q>": lambda _event: self.toggle_quick_view(),
            "<Control-F1>": lambda _event: self.set_view_mode(VIEW_MODE_BRIEF),
            "<Control-F2>": lambda _event: self.set_view_mode(VIEW_MODE_DETAILED),
            "<Control-Shift-F1>": lambda _event: self.set_view_mode(VIEW_MODE_THUMBNAILS),
            "<Control-F8>": lambda _event: self.toggle_directory_tree(),
            "<Control-Return>": lambda _event: self.focus_command_bar(),
            "<Control-KP_Enter>": lambda _event: self.focus_command_bar(),
            "<Alt-Down>": lambda _event: self.show_history_menu(),
            "<Alt-Left>": lambda _event: self.go_back(),
            "<Alt-Right>": lambda _event: self.go_forward(),
            "<Escape>": lambda _event: self.clear_filter(),
            "<Tab>": lambda _event: self.switch_panel(),
        }
        for sequence, handler in bindings.items():
            self.root.bind(sequence, handler)

    # ------------------------------------------------------------------ #
    # 面板与状态
    # ------------------------------------------------------------------ #
    def set_active(self, panel: FilePanel) -> None:
        """设置活动面板并更新标题样式、目录树与命令行工作目录。"""
        self.active_side = panel.side
        self.left.set_title_active(panel is self.left)
        self.right.set_title_active(panel is self.right)
        self.sync_tree(panel)
        self._update_command_cwd()
        self._quick_var.set(panel.quick_visible)

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

    @staticmethod
    def _directory_writable(path: Path) -> bool:
        """菜单前置判断：目录存在且当前进程可写；真正失败仍走操作自身的报错。"""
        try:
            return path.is_dir() and os.access(path, os.W_OK)
        except OSError:
            return False

    def _sync_menu_state(self) -> None:
        """菜单展开前按活动面板状态刷新禁用项与勾选变量。"""
        panel = self.active_panel()
        entries = panel.selected_entries()
        single = entries[0] if len(entries) == 1 else None
        facts = MenuFacts(
            entry_count=len(panel.tree.get_children()),
            selection_count=len(entries),
            single_is_file=bool(single and not single.is_dir),
            busy=self._busy,
            active_writable=self._directory_writable(Path(panel.state.path)),
            target_writable=self._directory_writable(Path(self.other_panel().state.path)),
            tab_count=len(panel.tabs),
            history_index=panel.state.history_index,
            history_length=len(panel.state.history),
            panels_ready=Path(self.left.state.path).is_dir() and Path(self.right.state.path).is_dir(),
            active_exists=Path(panel.state.path).is_dir(),
            show_hidden=self.settings.show_hidden,
            tree_visible=self._tree_visible,
            quick_visible=panel.quick_visible,
            sort_key=panel.state.sort_key,
            view_mode=panel.state.view_mode,
        )
        state = menu_state(facts)
        for key, (menu, index) in self._menu_items.items():
            menu.entryconfigure(index, state="disabled" if state.is_disabled(key) else "normal")
        self._hidden_var.set(state.show_hidden)
        self._quick_var.set(state.quick_visible)
        self._tree_var.set(state.tree_visible)
        self._sort_var.set(state.sort_key)
        self._view_var.set(state.view_mode)

    # ------------------------------------------------------------------ #
    # 目录树（只读，跟随活动面板）
    # ------------------------------------------------------------------ #
    def toggle_directory_tree(self) -> None:
        """Ctrl+F8：显示或隐藏目录树，显示时定位到活动面板目录。"""
        if self._tree_visible:
            self._paned.forget(self._tree_frame)
            self._tree_visible = False
            self.set_status("目录树已隐藏")
        else:
            self._paned.insert(0, self._tree_frame, weight=0)
            self._tree_visible = True
            self.sync_tree(self.active_panel(), force=True)
            self.set_status("目录树已显示（只读，单击目录定位活动面板）")
        self._tree_var.set(self._tree_visible)

    def _tree_root_paths(self) -> list[Path]:
        """目录树根：Windows 列出存在的盘符，其他平台用根目录。"""
        if os.name != "nt":
            return [Path("/")]
        roots: list[Path] = []
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:\\")
            if root.exists():
                roots.append(root)
        return roots

    def _directory_children(self, path: Path) -> list[Path]:
        """目录树子节点：只取可读子目录，不跟随符号链接。"""
        try:
            entries = list_directory(path, show_hidden=self.settings.show_hidden)
        except OSError:
            return []
        return [entry.path for entry in entries if entry.is_dir and not entry.is_symlink]

    def _add_tree_placeholder(self, iid: str) -> None:
        """未展开节点先放占位子项，展开时再读取真实内容。"""
        if not self._tree.get_children(iid):
            self._tree.insert(iid, "end", text="…")

    def _populate_tree_roots(self) -> None:
        for root in self._tree_root_paths():
            iid = str(root)
            if self._tree.exists(iid):
                continue
            self._tree.insert("", "end", iid=iid, text=root.name or iid)
            self._add_tree_placeholder(iid)

    def _populate_tree_node(self, path: Path, *, force: bool = False) -> None:
        """读取一层子目录；已加载节点默认跳过。"""
        iid = str(path)
        if not self._tree.exists(iid) or (iid in self._tree_loaded and not force):
            return
        for child in self._tree.get_children(iid):
            self._tree.delete(child)
        for child_path in self._directory_children(path):
            child_iid = str(child_path)
            if self._tree.exists(child_iid):
                continue
            self._tree.insert(iid, "end", iid=child_iid, text=child_path.name or child_iid)
            self._add_tree_placeholder(child_iid)
        self._tree_loaded.add(iid)

    def _on_tree_open(self, _event: Any = None) -> None:
        """展开节点时按需读取一层子目录。"""
        iid = self._tree.focus()
        if iid:
            self._populate_tree_node(Path(iid))

    def _ensure_tree_path(self, path: Path) -> str:
        """确保目录及其祖先已插入树并展开，返回目标节点 iid。"""
        chain = [*reversed(path.parents), path]
        roots = {str(root) for root in self._tree_root_paths()}
        start = next((index for index, item in enumerate(chain) if str(item) in roots), None)
        if start is None:
            return ""
        chain = chain[start:]
        for index, item in enumerate(chain):
            iid = str(item)
            if not self._tree.exists(iid):
                parent = chain[index - 1] if index else None
                if parent is None or not self._tree.exists(str(parent)):
                    return ""
                self._tree.insert(str(parent), "end", iid=iid, text=item.name or iid)
            if index < len(chain) - 1:
                self._populate_tree_node(item)
                self._tree.item(iid, open=True)
        return str(chain[-1])

    def _select_tree_iid(self, iid: str) -> None:
        self._tree_syncing = True
        try:
            self._tree.selection_set(iid)
            self._tree.focus(iid)
            self._tree.see(iid)
        finally:
            self._tree_syncing = False

    def sync_tree(self, panel: FilePanel, *, force: bool = False) -> None:
        """让目录树定位到面板当前目录；非活动面板默认不跟随。"""
        if not self._tree_visible:
            return
        if not force and panel is not self.active_panel():
            return
        iid = self._ensure_tree_path(Path(panel.state.path))
        if iid:
            self._select_tree_iid(iid)

    def _on_tree_select(self, _event: Any = None) -> None:
        """单击目录树定位活动面板（树本身只读，不做拖放）。"""
        if self._tree_syncing:
            return
        selection = self._tree.selection()
        if not selection:
            return
        target = Path(selection[0])
        if not target.is_dir():
            return
        panel = self.active_panel()
        if str(target) == panel.state.path:
            return
        panel.set_path(target)

    def on_panel_refreshed(self, panel: FilePanel) -> None:
        """面板刷新后同步目录树与命令行工作目录（仅活动面板）。"""
        if panel is not self.active_panel():
            return
        self.sync_tree(panel)
        self._update_command_cwd()

    def on_panel_selection(self, panel: FilePanel) -> None:
        """面板选中目录时让目录树同步高亮，切换标签页同样会定位。"""
        if not self._tree_visible or panel is not self.active_panel():
            return
        selected = panel.selected_entries()
        target = selected[0].path if len(selected) == 1 and selected[0].is_dir else Path(panel.state.path)
        iid = self._ensure_tree_path(target)
        if iid:
            self._select_tree_iid(iid)

    # ------------------------------------------------------------------ #
    # 命令行栏（默认隐藏，Enter 执行、Esc 清空返回）
    # ------------------------------------------------------------------ #
    def toggle_quick_view(self) -> None:
        """Ctrl+Q / 菜单：切换活动面板的内嵌快速查看。"""
        panel = self.active_panel()
        panel.toggle_quick_view()
        self._quick_var.set(panel.quick_visible)

    def set_quick_view(self, visible: bool) -> None:
        """按菜单勾选状态显式开关快速查看。"""
        panel = self.active_panel()
        if visible:
            panel.show_quick_view()
        else:
            panel.hide_quick_view()
        self._quick_var.set(panel.quick_visible)

    def _set_quick_from_var(self) -> None:
        self.set_quick_view(bool(self._quick_var.get()))

    def focus_command_bar(self) -> None:
        """Ctrl+Enter：唤出命令行栏并聚焦。"""
        self._command_visible = True
        self._command_frame.grid()
        self._update_command_cwd()
        self._command_entry.focus_set()
        self._command_entry.select_range(0, "end")

    def hide_command_bar(self) -> None:
        """收起命令行栏。"""
        self._command_visible = False
        self._command_frame.grid_remove()

    def _update_command_cwd(self) -> None:
        """命令行工作目录始终取活动面板当前路径。"""
        self._command_cwd_var.set(f"工作目录：{self.active_panel().state.path}")

    def _on_command_enter(self, _event: Any = None) -> str:
        self.run_command()
        return "break"

    def _on_command_escape(self, _event: Any = None) -> str:
        self._command_var.set("")
        self.hide_command_bar()
        self.active_panel().focus_tree()
        return "break"

    def run_command(self) -> None:
        """在活动面板目录执行命令；删除类命令先二次确认。"""
        command = self._command_var.get().strip()
        if not command:
            self.hide_command_bar()
            return
        cwd = Path(self.active_panel().state.path)
        if not cwd.is_dir():
            self.show_error("命令行", f"活动面板目录不存在：{cwd}")
            return
        risky = destructive_command(command)
        if risky and not self._messagebox.askyesno(
            "确认执行删除命令",
            f"命令包含删除类操作（{risky}），执行后可能无法恢复。\n\n工作目录：{cwd}\n命令：{command}\n\n确定执行吗？",
            parent=self.root,
        ):
            return
        self.set_status(f"执行命令（{cwd}）：{command}")
        self.start_worker(
            lambda: self._run_shell(command, cwd),
            lambda result: self._command_finished(command, result),
            title="命令行执行",
        )

    @staticmethod
    def _run_shell(command: str, cwd: Path) -> tuple[int, str]:
        """后台线程执行 shell 命令，合并标准输出与错误。"""
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
        )
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")

    def _command_finished(self, command: str, result: tuple[int, str]) -> None:
        code, output = result
        self.refresh_both()
        self.set_status(f"命令结束（退出码 {code}）：{command}")
        body = output.strip() or "（无输出）"
        self._show_text_dialog(f"命令行输出 - 退出码 {code}", f"$ {command}\n退出码：{code}\n\n{body}")

    # ------------------------------------------------------------------ #
    # 颜色规则
    # ------------------------------------------------------------------ #
    def open_color_rules(self) -> None:
        """配置 → 颜色规则…（无快捷键）。"""
        if self._color_dialog is not None and self._color_dialog.window.winfo_exists():
            self._color_dialog.window.lift()
            self._color_dialog.window.focus_set()
            return
        self._color_dialog = ColorRulesDialog(self, self.settings.color_rules)
        self._color_dialog.window.wait_window()
        self._color_dialog = None

    def apply_color_rules(self, rules: Sequence[ColorRule]) -> None:
        """保存颜色规则并刷新两个面板。"""
        self.settings.color_rules = list(rules)
        self._store_panel_state()
        save_settings(self.settings, self._settings_path)
        self.left.refresh()
        self.right.refresh()
        self.set_status(f"颜色规则已保存：{len(rules)} 条")

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
        """Ctrl+H：切换隐藏文件显示。"""
        self._hidden_var.set(not bool(self._hidden_var.get()))
        self._apply_hidden()

    def _apply_hidden(self) -> None:
        """采用菜单/工具栏勾选状态，并同步设置与两个面板。"""
        self.settings.show_hidden = bool(self._hidden_var.get())
        self.refresh_both()
        self.set_status("显示隐藏文件" if self.settings.show_hidden else "隐藏隐藏文件")

    def clear_selection(self) -> None:
        """取消活动面板的全部选择。"""
        self.active_panel().clear_selection()

    def go_up(self) -> None:
        """活动面板返回上级目录。"""
        self.active_panel().go_up()

    def go_back(self) -> None:
        """活动面板后退到上一历史位置。"""
        self.active_panel().go_back()

    def go_forward(self) -> None:
        """活动面板前进到下一历史位置。"""
        self.active_panel().go_forward()

    def set_view_mode(self, mode: str) -> None:
        """切换活动面板的视图模式；菜单、快捷键与右键菜单共用此入口。"""
        panel = self.active_panel()
        panel.apply_view_mode(mode)
        self._view_var.set(panel.state.view_mode)
        label = {value: text.split("\t", 1)[0] for text, value in _VIEW_MODE_CHOICES}
        text = f"{panel.title}视图：{label.get(panel.state.view_mode, panel.state.view_mode)}"
        if panel.state.view_mode == VIEW_MODE_THUMBNAILS:
            text += f" | {panel.thumbnail_note}"
        self.set_status(text)

    def sort_active(self, column: str) -> None:
        """按指定列排序活动面板；选择当前列时切换升降序。"""
        self.active_panel().sort_by(column)

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
            "Ctrl+Q 内嵌快速查看    Ctrl+F8 显示/隐藏目录树\n"
            "Ctrl+F1 简要列表    Ctrl+F2 详细列表    Ctrl+Shift+F1 缩略图\n"
            "Ctrl+Enter 唤出命令行（Enter 执行、Esc 清空返回；删除类命令先确认）\n"
            "颜色规则在 配置→颜色规则… 中设置（无快捷键）\n"
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

    def _store_panel_state(self) -> None:
        """把面板/标签/活动侧写回设置对象（不落盘）。"""
        self.left._capture_column_widths()
        self.right._capture_column_widths()
        self.settings.left = self.left.state
        self.settings.right = self.right.state
        self.settings.left_tabs = list(self.left.tabs)
        self.settings.right_tabs = list(self.right.tabs)
        self.settings.left_tab_index = self.left.tab_index
        self.settings.right_tab_index = self.right.tab_index
        self.settings.active = self.active_side
        self.settings.show_hidden = bool(self._hidden_var.get())

    def _save_and_destroy(self) -> None:
        """保存面板/标签/书签状态并销毁窗口。"""
        if self._closing:
            return
        self._closing = True
        self.left.shutdown_thumbnails()
        self.right.shutdown_thumbnails()
        self._store_panel_state()
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
