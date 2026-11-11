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
import tarfile
import tempfile
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, cast

import fcmd
from fcmd.apps.cmderw import _cmderw_diff as diffmod
from fcmd.apps.cmderw import _cmderw_preview as previewmod
from fcmd.apps.cmderw._cmderw_archwrite import (
    DEFAULT_COMPRESSION_LEVEL,
    ArchiveWriteOptions,
    add_paths,
    archive_editable,
    archive_member_names,
    create_archive_file,
    planned_additions,
    remove_members,
    rename_member,
)
from fcmd.apps.cmderw._cmderw_arcsearch import ArchiveHit, ArchiveSearchReport, search_archive
from fcmd.apps.cmderw._cmderw_attrs import (
    AttributeChange,
    apply_attributes,
    format_timestamp,
    parse_timestamp,
)
from fcmd.apps.cmderw._cmderw_checksum import (
    VerifyReport,
    algorithm_for_suffix,
    build_manifest,
    manifest_name,
    normalize_algorithm,
    report_text,
    verify_manifest,
)
from fcmd.apps.cmderw._cmderw_commands import (
    COMMAND_LIMIT,
    PLACEHOLDERS,
    CommandContext,
    PlaceholderError,
    UserCommand,
    expand_placeholders,
    normalize_user_commands,
    user_commands_to_json,
)
from fcmd.apps.cmderw._cmderw_dupes import (
    DuplicateActionReport,
    DuplicateReport,
    duplicate_paths,
    move_duplicates,
)
from fcmd.apps.cmderw._cmderw_dupes import (
    find_duplicates as find_duplicate_files,
)
from fcmd.apps.cmderw._cmderw_launch import LaunchRequest, resolve_launch
from fcmd.apps.cmderw._cmderw_ops import (
    COLOR_KIND_ANY,
    COLOR_KIND_DIR,
    COLOR_KIND_FILE,
    COLOR_KIND_LINK,
    COLOR_KIND_TEXT,
    COLUMN_SORT,
    COMPARE_DIFFERENT,
    COMPARE_LEFT_ONLY,
    COMPARE_MODE_TEXT,
    COMPARE_MODE_TIME,
    COMPARE_MODES,
    COMPARE_RIGHT_ONLY,
    COMPARE_SAME,
    COMPARE_STATUS_TEXT,
    DEFAULT_VIEW_MODE,
    ITEM_FAILED,
    PANEL_COLUMNS,
    PREVIEW_IMAGE,
    PREVIEW_TEXT,
    PREVIEW_TEXT_LIMIT,
    PREVIEW_UNSUPPORTED,
    QUEUE_CANCELLED,
    QUEUE_RUNNING,
    SYNC_BOTH,
    SYNC_CONFLICT_OVERWRITE,
    SYNC_CONFLICT_TEXT,
    SYNC_CONFLICTS,
    SYNC_DELETE,
    SYNC_LTR,
    SYNC_RTL,
    THUMBNAIL_BADGE,
    THUMBNAIL_GAP,
    THUMBNAIL_TILE_SIZE,
    VIEW_MODE_BRIEF,
    VIEW_MODE_DETAILED,
    VIEW_MODE_THUMBNAILS,
    ColorRule,
    ColorStyle,
    ColorSubject,
    CompareOptions,
    CompareReport,
    MenuFacts,
    PreviewResult,
    QueueItem,
    QueueTask,
    RenameReport,
    SyncDeleteReport,
    SyncItem,
    SyncOptions,
    SyncPlan,
    SyncRunReport,
    ThumbnailCache,
    ThumbnailRequestTracker,
    TransferQueue,
    TrashReport,
    apply_rename_plan,
    branch_relative_path,
    browse_follow_path,
    browse_step,
    build_sync_plan,
    cleanup_incomplete_items,
    column_display_names,
    command_history_push,
    command_history_step,
    compare_trees,
    complete_token,
    completion_mode,
    destructive_command,
    directory_size,
    dump_color_rules,
    execute_sync_deletes,
    filter_names,
    format_sync_report,
    invert_names,
    item_status_text,
    match_names,
    menu_state,
    normalize_color,
    normalize_column_widths,
    normalize_command_history,
    normalize_open_with_list,
    normalize_view_mode,
    open_with_command,
    parse_color_rules,
    read_preview,
    relative_parent_dir,
    remember_open_with,
    resolve_color_style,
    revalidate_sync_items,
    split_command_token,
    split_sync_selection,
    sync_invert_selection,
    sync_item_key,
    sync_select_all,
    sync_toggle_selection,
    thumbnail_cache_key,
    thumbnail_columns,
    thumbnail_plan,
    thumbnail_unsupported_hint,
    thumbnail_visible_rows,
    trash_paths,
    undo_rename,
    validate_name,
)
from fcmd.apps.cmderw._cmderw_queue import (
    DEFAULT_QUEUE_PARALLEL,
    MAX_QUEUE_PARALLEL,
    MIN_QUEUE_PARALLEL,
    PauseGate,
    clamp_parallel,
    parallel_map,
)
from fcmd.apps.cmderw._cmderw_rename import (
    RENAME_PRESET_LIMIT,
    RenameInput,
    RenameOptions,
    RenamePreset,
    RenamePreview,
    RenamePreviewItem,
    build_rename_preview,
    normalize_rename_presets,
    preview_to_plan,
    rename_presets_to_json,
)
from fcmd.apps.cmderw._cmderw_search import (
    SORT_COLUMNS,
    SearchFilters,
    collect_results,
    filter_paths,
    parse_datetime,
    parse_size,
    sort_results,
)
from fcmd.apps.cmderw._cmderw_split import (
    CHECKSUM_SUFFIX,
    MergeResult,
    SplitResult,
    default_merged_name,
    discover_parts,
    merge_parts,
    part_names,
    split_file,
    split_plan,
)
from fcmd.apps.cmderw._cmderw_tabs import (
    FilterDefinition,
    apply_filter_definition,
    close_other_indices,
    normalize_filter_definition,
    normalize_selection,
    normalize_tab_title,
    tab_bar_label,
    tab_label,
)
from fcmd.apps.cmderw._cmderw_volumes import enumerate_volumes
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

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
    "ArchiveMember",
    "CmderSettings",
    "FileEntry",
    "HashResult",
    "OperationReport",
    "PanelState",
    "SearchHit",
    "TransferItem",
    "TrashReport",
    "archive_browsable",
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
    "extract_archive_members",
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
    "read_archive_members",
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
    "Num+/Num- 按掩码选择，Ctrl+S 快速过滤，Ctrl+M 多重命名工具，Space 统计大小，"
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
#: 分支视图：递归深度与条目上限，避免误入超深目录树时卡死界面。
BRANCH_MAX_DEPTH = 32
BRANCH_MAX_ENTRIES = 20000
_COMMAND_HINT_LIMIT = 8
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
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as image:
            image.thumbnail((tile_size, tile_size))
            converted = image if image.mode in ("RGB", "RGBA") else image.convert("RGBA")
            output = BytesIO()
            converted.save(output, format="PNG")
            return output.getvalue()
    except Exception:
        return None


def load_thumbnail_payload(path: Path, tile_size: int = _THUMB_TILE) -> bytes | None:
    """后台读取图片字节：最多 8 MiB，再交给 Pillow 解码为 PNG；失败返回 None。"""
    with path.open("rb") as handle:
        data = handle.read(_THUMB_READ_LIMIT + 1)
    if len(data) > _THUMB_READ_LIMIT:
        return None
    return _decode_thumbnail_with_pillow(data, tile_size)


# ============================================================================ #
# 数据模型
# ============================================================================ #
@dataclass(frozen=True)
class FileEntry:
    """目录中的一个条目（文件 / 目录 / 符号链接）。

    ``relative`` 仅分支视图使用：相对面板根目录的路径，作为显示名与唯一键。
    """

    name: str
    path: Path
    is_dir: bool = False
    is_symlink: bool = False
    size: int = 0
    mtime: float = 0.0
    hidden: bool = False
    relative: str = ""
    archive_member: str = ""

    @property
    def label(self) -> str:
        """列表显示名：分支视图为相对路径，普通视图为文件名。"""
        return self.relative or self.name


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
    branch_view: bool = False
    title: str = ""
    locked: bool = False
    selection: list[str] = field(default_factory=list)

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
            "branch_view": self.branch_view,
            "title": self.title,
            "locked": self.locked,
            "selection": list(self.selection),
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
    theme: str = _guitheme.DEFAULT_THEME_NAME
    left_tabs: list[PanelState] = field(default_factory=list)
    right_tabs: list[PanelState] = field(default_factory=list)
    left_tab_index: int = 0
    right_tab_index: int = 0
    bookmarks: list[str] = field(default_factory=list)
    color_rules: list[ColorRule] = field(default_factory=list)
    sync_browse: bool = False
    open_with: list[str] = field(default_factory=list)
    command_history: list[str] = field(default_factory=list)
    rename_presets: list[RenamePreset] = field(default_factory=list)
    user_commands: list[UserCommand] = field(default_factory=list)
    queue_parallel: int = DEFAULT_QUEUE_PARALLEL
    filter_definition: FilterDefinition = field(default_factory=FilterDefinition)


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
    branch_view = data.get("branch_view")
    if isinstance(branch_view, bool):
        state.branch_view = branch_view
    state.title = normalize_tab_title(data.get("title"))
    locked = data.get("locked")
    if isinstance(locked, bool):
        state.locked = locked
    state.selection = normalize_selection(data.get("selection"))
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
    sync_browse = raw.get("sync_browse")
    if isinstance(sync_browse, bool):
        settings.sync_browse = sync_browse
    settings.open_with = normalize_open_with_list(raw.get("open_with"))
    settings.command_history = normalize_command_history(raw.get("command_history"))
    settings.rename_presets = normalize_rename_presets(raw.get("rename_presets"))
    settings.user_commands = normalize_user_commands(raw.get("user_commands"))
    queue_parallel = raw.get("queue_parallel")
    if isinstance(queue_parallel, int) and not isinstance(queue_parallel, bool):
        settings.queue_parallel = clamp_parallel(queue_parallel)
    else:
        settings.queue_parallel = DEFAULT_QUEUE_PARALLEL
    geometry = raw.get("geometry")
    if isinstance(geometry, str):
        settings.geometry = _guiconfig.valid_geometry(geometry)
    theme = raw.get("theme")
    settings.theme = _guitheme.resolve_theme(theme if isinstance(theme, str) else None).name
    settings.filter_definition = normalize_filter_definition(raw.get("filter_definition"))
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
        "theme": settings.theme,
        "left_tabs": [tab.to_dict() for tab in left_tabs],
        "right_tabs": [tab.to_dict() for tab in right_tabs],
        "left_tab_index": left_index,
        "right_tab_index": right_index,
        "bookmarks": list(settings.bookmarks),
        "color_rules": dump_color_rules(settings.color_rules),
        "sync_browse": settings.sync_browse,
        "open_with": list(settings.open_with),
        "command_history": list(settings.command_history),
        "rename_presets": rename_presets_to_json(settings.rename_presets),
        "user_commands": user_commands_to_json(settings.user_commands),
        "queue_parallel": settings.queue_parallel,
        "filter_definition": settings.filter_definition.to_dict(),
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
    folded = entry.label.casefold()
    if sort_key == "size":
        return (entry.size, folded)
    if sort_key == "mtime":
        return (entry.mtime, folded)
    if sort_key == "type":
        kind = "" if entry.is_dir else entry.path.suffix.casefold()
        return (kind, folded)
    return (folded, entry.label)


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
# 压缩包内浏览（zip / tar 家族 / 单文件压缩流，只读）
# ============================================================================ #
@dataclass(frozen=True)
class ArchiveMember:
    """归档成员：``name`` 为 POSIX 相对路径，目录通常以 ``/`` 结尾。"""

    name: str
    size: int = 0
    mtime: float = 0.0
    is_dir: bool = False


def archive_browsable(path: Path) -> bool:
    """按后缀判断是否支持只读浏览（读取时再校验实际格式）。"""
    lower = path.name.lower()
    if lower.endswith((".tar.gz", ".tar.bz2", ".tar.xz")):
        return True
    return path.suffix.lower() in {".zip", ".tar", ".tgz", ".tbz2", ".txz", ".gz", ".bz2", ".xz"}


def _member_allowed(name: str) -> bool:
    """拒绝绝对路径与 ``..`` 穿越的成员名。"""
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or cleaned == ".." or cleaned.startswith("../") or "/../" in cleaned:
        return False
    return bool(cleaned.strip("/"))


def _zip_member_mtime(info: zipfile.ZipInfo) -> float:
    try:
        return time.mktime((*info.date_time, 0, 0, -1))
    except (OverflowError, ValueError):
        return 0.0


def read_archive_members(archive: Path) -> list[ArchiveMember]:
    """读取 zip / tar 成员；单文件压缩流返回一个伪成员。"""
    if not archive.is_file():
        raise FileNotFoundError(f"归档不存在: {archive}")
    lower = archive.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            return [
                ArchiveMember(
                    name=info.filename,
                    size=info.file_size,
                    mtime=_zip_member_mtime(info),
                    is_dir=info.is_dir(),
                )
                for info in handle.infolist()
                if _member_allowed(info.filename)
            ]
    if lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(archive) as handle:
            return [
                ArchiveMember(name=info.name, size=info.size, mtime=float(info.mtime), is_dir=info.isdir())
                for info in handle.getmembers()
                if (info.isfile() or info.isdir()) and _member_allowed(info.name)
            ]
    if lower.endswith((".gz", ".bz2", ".xz")):
        return [ArchiveMember(name=archive_stem(archive.name), size=archive.stat().st_size)]
    raise ValueError(f"不支持浏览该格式: {archive.name}")


def _member_in_scope(name: str, wanted: Sequence[str]) -> bool:
    """成员是否在选中范围内（选中目录时包含其整棵子树）。"""
    return any(name == item or name.startswith(f"{item.rstrip('/')}/") for item in wanted)


def extract_archive_members(archive: Path, members: Sequence[ArchiveMember], destination: Path) -> int:
    """把选中成员按包内相对路径解压到目标目录，返回解压的文件数。"""
    if not archive.is_file():
        raise FileNotFoundError(f"归档不存在: {archive}")
    wanted = [member.name for member in members if _member_allowed(member.name)]
    if not wanted:
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    lower = archive.name.lower()
    if lower.endswith(".zip"):
        with zipfile.ZipFile(archive) as handle:
            selected = [
                info for info in handle.infolist() if _member_in_scope(info.filename, wanted) and not info.is_dir()
            ]
            for info in selected:
                handle.extract(info, destination)
        return len(selected)
    if lower.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz")):
        with tarfile.open(archive) as handle:
            selected = [
                info
                for info in handle.getmembers()
                if (info.isfile() or info.isdir()) and _member_in_scope(info.name, wanted)
            ]
            handle.extractall(path=destination, members=selected, filter="data")
        return sum(1 for info in selected if info.isfile())
    extract_archive_to(archive, destination)
    return 1


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


def search_paths(  # noqa: PLR0913 - 搜索开关由对话框逐项透传
    directory: Path,
    pattern: str,
    *,
    content: bool = False,
    extension: str = "",
    case_sensitive: bool | None = None,
    whole_word: bool = False,
    filters: SearchFilters | None = None,
) -> list[SearchHit]:
    """搜索目录（复用 fileops 的文件名 glob / 内容正则实现）。"""
    if not pattern.strip():
        raise ValueError("搜索内容不能为空")
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    from fcmd.dsl.actions.fileops import search_by_content, search_by_name

    active = filters or SearchFilters()
    if content:
        suffix = extension.strip()
        if suffix and not suffix.startswith("."):
            suffix = f".{suffix}"
        hits = search_by_content(
            directory,
            pattern,
            extension=suffix,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
        )
        allowed = set(filter_paths({path for path, _line, _text in hits}, active))
        return [SearchHit(path=path, line=line, text=text) for path, line, text in hits if path in allowed]
    paths = search_by_name(directory, pattern, case_sensitive=case_sensitive)
    return [SearchHit(path=path) for path in filter_paths(paths, active)]


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
        self._tab_signature: tuple[int, tuple[tuple[str, str, bool], ...]] | None = None
        self.entries: list[FileEntry] = []
        self._index: dict[str, FileEntry] = {}
        self._filter = ""
        self._restore_selection: list[str] | None = normalize_selection(self.state.selection)
        self._last_path = self.state.path
        self._branch_truncated = False
        self.archive: Path | None = None
        self.archive_prefix = ""
        self._archive_members: list[ArchiveMember] | None = None
        self._archive_temp: Path | None = None
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

        self.volume_button = ttk.Menubutton(top, text="卷", style="Toolbutton")
        self.volume_menu = tk.Menu(self.volume_button, tearoff=False)
        self.volume_menu.configure(postcommand=self._populate_volumes)
        self.volume_button.configure(menu=self.volume_menu)
        self.volume_button.pack(side="left", padx=(0, spacing.xs))

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
        self._quick_base_photo: Any = None
        self._quick_factor = 1
        self._quick_frame = ttk.Frame(self.frame)
        quick_header = ttk.Frame(self._quick_frame)
        quick_header.pack(fill="x")
        self._quick_title_var = tk.StringVar(value="快速查看")
        ttk.Label(quick_header, textvariable=self._quick_title_var, style="Muted.TLabel").pack(side="left")
        self._quick_note_var = tk.StringVar()
        ttk.Label(quick_header, textvariable=self._quick_note_var, style="Muted.TLabel").pack(side="right")
        self._quick_toolbar = ttk.Frame(self._quick_frame)
        self._quick_toolbar.pack(fill="x", pady=(spacing.xxs, 0))
        ttk.Label(self._quick_toolbar, text="编码：").pack(side="left")
        self._quick_encoding_var = tk.StringVar(value="auto")
        encoding_box = ttk.Combobox(
            self._quick_toolbar,
            textvariable=self._quick_encoding_var,
            values=previewmod.ENCODINGS,
            state="readonly",
            width=10,
        )
        encoding_box.pack(side="left")
        encoding_box.bind("<<ComboboxSelected>>", lambda _event: self.update_quick_view())
        self._quick_hex_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            self._quick_toolbar,
            text="十六进制",
            variable=self._quick_hex_var,
            command=self.update_quick_view,
        ).pack(side="left", padx=(spacing.xs, 0))
        self._quick_zoom_bar = ttk.Frame(self._quick_toolbar)
        ttk.Button(
            self._quick_zoom_bar,
            text="－",
            width=3,
            style="Toolbutton",
            command=lambda: self._zoom_image("out"),
        ).pack(side="left")
        self._quick_zoom_label_var = tk.StringVar(value="1:1")
        ttk.Label(
            self._quick_zoom_bar,
            textvariable=self._quick_zoom_label_var,
            style="Muted.TLabel",
        ).pack(side="left", padx=spacing.xxs)
        ttk.Button(
            self._quick_zoom_bar,
            text="＋",
            width=3,
            style="Toolbutton",
            command=lambda: self._zoom_image("in"),
        ).pack(side="left")
        ttk.Button(self._quick_zoom_bar, text="适应窗口", style="Toolbutton", command=self._fit_image).pack(
            side="left", padx=(spacing.xs, 0)
        )
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
        self._thumb_canvas.bind("<Button-3>", self._on_thumb_right_click)

    def _is_thumbnail_view(self) -> bool:
        return self.state.view_mode == VIEW_MODE_THUMBNAILS

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
                self._draw_thumb_cell(entry, column, row, key, entry.label in selected)
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
        plan = self._thumb_plan(entry)
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
        label = entry.label
        if len(label) > 18:
            label = "…" + label[-17:]
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
        suffix = Path(entry.name).suffix[1:].upper()
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
        if self._thumb_plan(entry) == THUMBNAIL_BADGE:
            return
        executor = self._thumb_executor
        if executor is None:
            executor = ThreadPoolExecutor(max_workers=_THUMB_WORKERS, thread_name_prefix="cmderw-thumb")
            self._thumb_executor = executor
        generation = self._thumb_tracker.generation
        self._thumb_futures[key] = executor.submit(self._thumbnail_worker, entry.path, key, generation)
        self._schedule_thumb_poll()

    def _thumbnail_worker(
        self,
        path: Path,
        key: tuple[str, int, int, int],
        generation: int,
    ) -> None:
        """后台线程：只读文件字节并解码，绝不触碰 Tk 对象。"""
        payload: bytes | None
        try:
            payload = load_thumbnail_payload(path, _THUMB_TILE)
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
        """把 Pillow 生成的 PNG 字节转成 Tk 图像；解码失败时返回 None 并显示徽标。"""
        if payload is None:
            return None
        try:
            return self._tk.PhotoImage(data=payload)
        except self._tk.TclError:
            return None

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
        self.tree.selection_set(entry.label)
        self.tree.focus(entry.label)
        self._update_status()
        self._request_thumb_redraw(immediate=True)

    def _on_thumb_double_click(self, event: Any) -> None:
        self._on_thumb_click(event)
        self._on_activate(event)

    def _on_thumb_right_click(self, event: Any) -> None:
        """缩略图右键：先选中命中的格子，再复用列表的右键菜单。"""
        index = self._thumb_index_at(event.x, event.y)
        if index is not None:
            entry = self.entries[index]
            if entry.label not in self.tree.selection():
                self.tree.selection_set(entry.label)
            self.tree.focus(entry.label)
            self._update_status()
            self._request_thumb_redraw(immediate=True)
        self.app.show_context_menu(event)

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
        names = [entry.label for entry in self.entries]
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

        signature = (self.tab_index, tuple((tab.path, tab.title, tab.locked) for tab in self.tabs))
        if signature == self._tab_signature:
            return
        self._tab_signature = signature
        for child in self._tab_bar.winfo_children():
            child.destroy()
        spacing = self.app.theme.spacing
        for index, tab in enumerate(self.tabs):
            label = tab_bar_label(tab.path, tab.title, locked=tab.locked)
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
        self._capture_selection()
        self._capture_column_widths()
        self._invalidate_thumbnails("tab")
        self._clear_archive_state()
        self.tab_index = index
        self._restore_selection = normalize_selection(self.state.selection)
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        self.app.set_status(f"{self.title}标签 {self.tab_index + 1}/{len(self.tabs)}：{self.state.path}")

    def new_tab(self, path: str | None = None) -> None:
        """新建标签页：默认沿用当前目录、排序方式、视图模式与列宽。"""
        source = self.state
        self._capture_selection()
        self._capture_column_widths()
        self._clear_archive_state()
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
        self._restore_selection = None
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        self.app.set_status(f"{self.title}新建标签 {self.tab_index + 1}/{len(self.tabs)}：{target}")

    def close_tab(self) -> bool:
        """关闭当前标签页；锁定或仅剩一个标签时保留并提示，返回是否真的关闭。"""
        if self.state.locked:
            self.app.set_status(f"{self.title}当前标签已锁定，请先解锁再关闭")
            return False
        if len(self.tabs) == 1:
            self.app.set_status(f"{self.title}至少保留一个标签页，已取消关闭")
            return False
        self._capture_column_widths()
        self._invalidate_thumbnails("tab")
        self._clear_archive_state()
        closed = self.tabs.pop(self.tab_index)
        self.tab_index = min(self.tab_index, len(self.tabs) - 1)
        self._restore_selection = None
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        name = tab_label(closed.path, closed.title) or closed.path
        self.app.set_status(f"已关闭标签「{name}」，当前 {self.tab_index + 1}/{len(self.tabs)}")
        return True

    def close_other_tabs(self) -> int:
        """关闭其他标签页，保留当前与锁定标签；返回关闭数量。"""
        locked = [index for index, tab in enumerate(self.tabs) if tab.locked]
        targets = close_other_indices(len(self.tabs), self.tab_index, locked)
        if not targets:
            self.app.set_status(f"{self.title}没有可关闭的其他标签页")
            return 0
        removed = set(targets)
        self._capture_selection()
        self._capture_column_widths()
        self._invalidate_thumbnails("tab")
        kept = [index for index in range(len(self.tabs)) if index not in removed]
        self.tab_index = kept.index(self.tab_index)
        self.tabs = [self.tabs[index] for index in kept]
        self._restore_selection = normalize_selection(self.state.selection)
        self.path_var.set(self.state.path)
        self._render_tabs()
        self.refresh()
        self.app.set_status(f"已关闭 {len(removed)} 个其他标签，保留 {len(self.tabs)} 个")
        return len(removed)

    def toggle_lock(self) -> bool:
        """切换当前标签锁定状态并返回新状态。"""
        self.state.locked = not self.state.locked
        self._render_tabs()
        return self.state.locked

    def rename_tab(self, title: str) -> None:
        """设置当前标签自定义名；空白名称恢复为目录名。"""
        self.state.title = normalize_tab_title(title)
        self._render_tabs()
        label = tab_label(self.state.path, self.state.title) or "（空）"
        self.app.set_status(f"标签已重命名为「{label}」")

    def _capture_selection(self) -> None:
        """把当前可见选择写入标签状态，供切换标签与重启恢复。"""
        self.state.selection = normalize_selection(list(self.tree.selection()))

    def next_tab(self, delta: int = 1) -> None:
        """在当前面板的标签间循环切换。"""
        if len(self.tabs) <= 1:
            self.app.set_status(f"{self.title}只有一个标签页")
            return
        self.select_tab((self.tab_index + delta) % len(self.tabs))

    def refresh(self, select: str | None = None) -> None:  # noqa: PLR0912 - 恢复选择、过滤与视图模式分支较集中
        """重新读取当前目录并恢复选择。"""
        self._invalidate_thumbnails("refresh")
        self._render_tabs()
        previous_path = self._last_path
        self._last_path = self.state.path
        if previous_path != self._last_path:
            self.app.on_panel_navigated(self, previous_path, self._last_path)
        if self.archive is not None:
            suffix = f"/{self.archive_prefix}" if self.archive_prefix else ""
            self.path_var.set(f"{self.archive}{suffix}")
        directory = Path(self.state.path)
        self._branch_truncated = False
        try:
            entries = self._load_entries(directory)
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
        elif self._restore_selection is not None:
            previous = set(self._restore_selection)
            self._restore_selection = None
        if self._filter:
            labels = filter_names([entry.label for entry in entries], self._filter)
        else:
            labels = [entry.label for entry in entries]
        labels = apply_filter_definition(labels, self.app.settings.filter_definition)
        visible = set(labels)
        entries = [entry for entry in entries if entry.label in visible]
        self.entries = entries
        self._index = {entry.label: entry for entry in entries}
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
                iid=entry.label,
                values=(
                    entry.label,
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

    def _load_entries(self, directory: Path) -> list[FileEntry]:
        """读取目录并排序：压缩包 / 分支视图 / 普通目录三种来源。"""
        if self.archive is not None:
            entries = self._archive_entries()
        elif self.state.branch_view:
            entries = self._list_branch(directory)
        else:
            entries = list_directory(directory, show_hidden=self.app.settings.show_hidden)
        return sort_entries(
            entries,
            sort_key=self.state.sort_key,
            descending=self.state.descending,
        )

    def _archive_entries(self) -> list[FileEntry]:
        """把归档成员折算为当前包内目录的直接子项。"""
        archive = self.archive
        if archive is None:
            return []
        members = self._archive_members or []
        prefix = self.archive_prefix
        seen_dirs: set[str] = set()
        entries: list[FileEntry] = []
        for member in members:
            name = member.name.rstrip("/")
            if not name:
                continue
            if prefix:
                if not name.startswith(f"{prefix}/"):
                    continue
                rest = name[len(prefix) + 1 :]
            else:
                rest = name
            if not rest:
                continue
            head, sep, _tail = rest.partition("/")
            if not head:
                continue
            if sep or member.is_dir:
                if head in seen_dirs:
                    continue
                seen_dirs.add(head)
                entries.append(
                    FileEntry(
                        name=head,
                        path=archive,
                        is_dir=True,
                        archive_member=f"{prefix}/{head}".strip("/") if prefix else head,
                    )
                )
                continue
            entries.append(
                FileEntry(
                    name=head,
                    path=archive,
                    size=member.size,
                    mtime=member.mtime,
                    archive_member=name,
                )
            )
        return entries

    def open_archive(self, archive: Path) -> bool:
        """进入归档只读浏览；不支持或读取失败时提示并返回 ``False``。"""
        if not archive_browsable(archive):
            self.app.show_error(
                "无法浏览",
                f"不支持浏览该压缩包格式（7z / rar 等请用「解压…」）：\n{archive.name}",
            )
            return False
        try:
            members = read_archive_members(archive)
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            self.app.show_error("无法浏览", str(exc))
            return False
        self._invalidate_thumbnails("archive")
        self.archive = archive
        self.archive_prefix = ""
        self._archive_members = members
        self.refresh()
        return True

    def archive_enter(self, name: str) -> None:
        """进入包内子目录。"""
        if self.archive is None:
            return
        self.archive_prefix = f"{self.archive_prefix}/{name}".strip("/") if self.archive_prefix else name
        self.refresh()

    def archive_up(self) -> None:
        """包内返回上级；已在包根时退出浏览。"""
        if self.archive is None:
            return
        if not self.archive_prefix:
            self.exit_archive()
            return
        parent = str(PurePosixPath(self.archive_prefix).parent)
        self.archive_prefix = "" if parent == "." else parent
        self.refresh()

    def exit_archive(self) -> None:
        """退出压缩包浏览并回到归档所在目录。"""
        if self.archive is None:
            return
        self._clear_archive_state()
        self.refresh()

    def _clear_archive_state(self) -> None:
        self.archive = None
        self.archive_prefix = ""
        self._archive_members = None
        self.path_var.set(self.state.path)
        if self._archive_temp is not None:
            shutil.rmtree(self._archive_temp, ignore_errors=True)
            self._archive_temp = None

    def reload_archive(self) -> None:
        """归档写入后重读成员并刷新，保持当前包内目录。"""
        if self.archive is None:
            return
        try:
            self._archive_members = read_archive_members(self.archive)
        except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
            self.app.show_error("无法浏览", str(exc))
            return
        self.refresh()

    def preview_path(self, entry: FileEntry) -> Path | None:
        """查看用路径：包内文件先解压到临时目录，普通条目返回真实路径。"""
        if self.archive is None:
            return entry.path
        return self.materialize(entry)

    def materialize(self, entry: FileEntry) -> Path | None:
        """按需把包内文件解压到临时目录；失败或超限时提示并返回 ``None``。"""
        if self.archive is None or not entry.archive_member:
            return None
        if entry.size > _QUICK_IMAGE_LIMIT:
            self.app.show_error("无法打开", f"文件超过 32 MiB，请先解压：{entry.label}")
            return None
        if self._archive_temp is None:
            self._archive_temp = Path(tempfile.mkdtemp(prefix="fcmd-arcview-"))
        target = self._archive_temp / entry.archive_member
        if not target.is_file():
            try:
                extract_archive_members(
                    self.archive,
                    [ArchiveMember(entry.archive_member, entry.size, entry.mtime)],
                    self._archive_temp,
                )
            except (OSError, ValueError, zipfile.BadZipFile, tarfile.TarError) as exc:
                self.app.show_error("无法解压", str(exc))
                return None
        return target if target.is_file() else None

    def _thumb_plan(self, entry: FileEntry) -> str:
        """缩略图计划：包内条目统一画类型徽标，不解码包内图片。"""
        if self.archive is not None:
            return THUMBNAIL_BADGE
        return THUMBNAIL_BADGE if entry.is_dir else thumbnail_plan(entry.name)

    def _list_branch(self, directory: Path) -> list[FileEntry]:
        """分支视图：递归列出所有文件，``relative`` 记录相对根目录的路径。

        隐藏目录按隐藏项整体跳过；符号链接目录不递归；深度与条目数设上限，
        超限时在状态栏给出提示而不是卡死界面。
        """
        if not directory.is_dir():
            raise NotADirectoryError(f"不是目录: {directory}")
        show_hidden = self.app.settings.show_hidden
        entries: list[FileEntry] = []
        pending: list[tuple[Path, int]] = [(directory, 0)]
        truncated = False
        while pending:
            if len(entries) >= BRANCH_MAX_ENTRIES:
                truncated = True
                break
            current, depth = pending.pop(0)
            try:
                children = sorted(current.iterdir())
            except OSError:
                continue
            for child in children:
                try:
                    stat = child.stat()
                except OSError:
                    stat = None
                hidden = is_hidden(child.name, stat)
                if hidden and not show_hidden:
                    continue
                if child.is_dir():
                    if not child.is_symlink() and depth < BRANCH_MAX_DEPTH:
                        pending.append((child, depth + 1))
                    continue
                entries.append(
                    FileEntry(
                        name=child.name,
                        path=child,
                        is_symlink=child.is_symlink(),
                        size=stat.st_size if stat is not None else 0,
                        mtime=stat.st_mtime if stat is not None else 0.0,
                        hidden=hidden,
                        relative=branch_relative_path(directory, child),
                    )
                )
        self._branch_truncated = truncated or bool(pending)
        return entries

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
        self._quick_title_var.set(f"快速查看 - {entry.label}")
        if entry.is_dir:
            self._show_quick_note(f"快速查看 - {entry.label}", "目录不预览，双击进入")
            return
        path = self.preview_path(entry)
        if path is None:
            self._show_quick_note(f"快速查看 - {entry.label}", "无法读取压缩包内文件")
            return
        result = read_preview(path)
        self._quick_note_var.set(result.note)
        if result.kind == PREVIEW_IMAGE:
            self._show_quick_image(path, result)
        elif result.kind == PREVIEW_UNSUPPORTED:
            self._show_quick_text(result.note or "无法预览该类型")
        else:
            self._render_quick_text(path, result)

    def _read_preview_bytes(self, path: Path) -> bytes:
        """读取预览用文件头，失败时返回空字节。"""
        try:
            with path.open("rb") as handle:
                return handle.read(PREVIEW_TEXT_LIMIT)
        except OSError:
            return b""

    def _render_quick_text(self, path: Path, result: PreviewResult) -> None:
        """按十六进制开关与所选编码渲染文本预览。"""
        data = self._read_preview_bytes(path)
        if self._quick_hex_var.get() or (result.kind != PREVIEW_TEXT and not previewmod.has_text_bom(data)):
            self._show_quick_text(previewmod.hex_dump(data) or "（空文件）")
            self._quick_note_var.set(result.note)
            return
        requested = self._quick_encoding_var.get()
        encoding = previewmod.detect_encoding(data) if requested == "auto" else requested
        try:
            text = previewmod.decode_text(data, encoding)
        except ValueError as exc:
            self._show_quick_text(str(exc))
            return
        self._quick_note_var.set(f"{result.note} · 编码 {encoding}")
        self._show_quick_text(text or "（空文件）")

    def _show_quick_note(self, title: str, note: str) -> None:
        """无文件可预览时给出明确提示，而不是空白面板。"""
        self._quick_title_var.set(title)
        self._quick_note_var.set(note)
        self._show_quick_text(note)

    def _show_quick_text(self, text: str) -> None:
        """显示文本（二进制为十六进制）并隐藏图片区。"""
        self._preview_photo = None
        self._quick_base_photo = None
        self._quick_zoom_bar.pack_forget()
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
        self._quick_base_photo = photo
        self._quick_factor = self._image_fit_factor(photo.width(), photo.height())
        self._apply_image_zoom()

    def _image_viewport(self) -> tuple[int, int]:
        """图片区当前可视尺寸；尚未布局时回退 640×480。"""
        width, height = self._quick_image.winfo_width(), self._quick_image.winfo_height()
        if width <= 1 or height <= 1:
            return 640, 480
        return width, height

    def _image_fit_factor(self, width: int, height: int) -> int:
        """图片宽高对应的适应窗口倍率。"""
        view_width, view_height = self._image_viewport()
        return previewmod.fit_factor(width, height, view_width, view_height)

    def _apply_image_zoom(self) -> None:
        """按当前倍率重新渲染图片并更新缩放标签。"""
        base = self._quick_base_photo
        if base is None:
            return
        photo = base if self._quick_factor <= 1 else base.subsample(self._quick_factor, self._quick_factor)
        self._preview_photo = photo
        self._quick_image.configure(image=photo)
        self._quick_image.pack(fill="both", expand=True)
        self._quick_zoom_label_var.set(previewmod.zoom_label(self._quick_factor))
        self._quick_zoom_bar.pack(side="right")

    def _zoom_image(self, direction: str) -> None:
        """放大 / 缩小当前图片。"""
        if self._quick_base_photo is None:
            return
        factor = self._quick_factor
        self._quick_factor = previewmod.zoom_in(factor) if direction == "in" else previewmod.zoom_out(factor)
        self._apply_image_zoom()

    def _fit_image(self) -> None:
        """恢复为适应窗口倍率。"""
        base = self._quick_base_photo
        if base is None:
            return
        self._quick_factor = self._image_fit_factor(base.width(), base.height())
        self._apply_image_zoom()

    def _update_status(self) -> None:
        total = len(self.entries)
        file_size = sum(entry.size for entry in self.entries if not entry.is_dir)
        text = f"{total} 个对象，共 {format_size(file_size)}"
        if self.archive is not None:
            inner = f"/{self.archive_prefix}" if self.archive_prefix else ""
            text = f"压缩包 · 只读 · {self.archive.name}{inner} · {total} 项"
        elif self.state.branch_view:
            text = f"分支视图 · {text}"
            if self._branch_truncated:
                text += f"（已达上限 {BRANCH_MAX_ENTRIES} 条，未列完）"
        if self._filter:
            text = f"过滤「{self._filter}」→ {text}"
        selected = self.selected_entries()
        if selected:
            selected_size = sum(entry.size for entry in selected if not entry.is_dir)
            text += f" | 已选 {len(selected)} 个，{format_size(selected_size)}"
            if self._is_thumbnail_view() and len(selected) == 1 and not selected[0].is_dir:
                entry = selected[0]
                if self._thumb_plan(entry) == THUMBNAIL_BADGE:
                    text += f" | {thumbnail_unsupported_hint(entry.name)}"
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
        if self.archive is not None:
            self.app.set_status("压缩包内为只读浏览：用 Backspace 返回上级，或输入其他目录退出浏览")
            self.path_var.set(f"{self.archive}/{self.archive_prefix}".rstrip("/"))
            return
        try:
            directory, select = resolve_path_input(self.path_var.get(), base=Path.cwd())
        except (OSError, ValueError) as exc:
            self.app.show_error("路径无效", str(exc))
            self.path_var.set(self.state.path)
            return
        self.set_path(directory)
        if select:
            self.select_name(select)

    def _populate_volumes(self) -> None:
        """打开卷菜单前刷新盘符 / 挂载点与剩余空间。"""
        self.volume_menu.delete(0, "end")
        volumes = enumerate_volumes()
        if not volumes:
            self.volume_menu.add_command(label="未检测到可用卷", state="disabled")
            return
        for volume in volumes:
            self.volume_menu.add_command(
                label=volume.label,
                command=lambda mount=volume.mount: self.set_path(mount),
            )

    def set_path(self, path: Path, *, push: bool = True) -> None:
        """切换到指定目录，默认写入历史；压缩包浏览状态下先退出浏览。"""
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
        if self.archive is not None:
            self._clear_archive_state()
        text = str(directory)
        if text != self.state.path:
            self._invalidate_thumbnails("navigate")
            self._restore_selection = None
        if push and text != self.state.path:
            history, index = push_history(self.state.history, self.state.history_index, text)
            self.state.history = history
            self.state.history_index = index
        self.state.path = text
        self.path_var.set(text)
        self.refresh()

    def go_up(self) -> None:
        """进入父目录；压缩包内先返回包内上级，包根再退出浏览。"""
        if self.archive is not None:
            self.archive_up()
            return
        parent = parent_directory(Path(self.state.path))
        if parent is not None:
            self.set_path(parent)

    def go_back(self) -> None:
        """后退到历史上一项；压缩包内先退出浏览。"""
        was_archive = self.archive is not None
        if was_archive:
            self._clear_archive_state()
        index = history_back(self.state.history, self.state.history_index)
        if index == self.state.history_index:
            if was_archive:
                self.refresh()
            return
        self._invalidate_thumbnails("navigate")
        self.state.history_index = index
        self.state.path = self.state.history[index]
        self.path_var.set(self.state.path)
        self.refresh()

    def go_forward(self) -> None:
        """前进到历史下一项；压缩包内先退出浏览。"""
        was_archive = self.archive is not None
        if was_archive:
            self._clear_archive_state()
        index = history_forward(self.state.history, self.state.history_index)
        if index == self.state.history_index:
            if was_archive:
                self.refresh()
            return
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
        """按名称选中并滚动到可见位置；分支视图下按文件名回退匹配。"""
        target = name if name in self.tree.get_children() else ""
        if not target:
            target = next(
                (entry.label for entry in self.entries if entry.name == name),
                "",
            )
        if target:
            self.tree.selection_set(target)
            self.tree.focus(target)
            self.tree.see(target)
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


_RENAME_CASE_CHOICES = (
    ("keep", "保持"),
    ("lower", "全部小写"),
    ("upper", "全部大写"),
    ("title", "每个单词首字母大写"),
    ("sentence", "仅首字母大写"),
)
_RENAME_SORT_CHOICES = (("name", "名称"), ("created", "创建时间"), ("modified", "修改时间"))
_RENAME_MASK_HINT = (
    "占位符：[N] 原名、[N1-3] 名称片段、[C] 计数器、[E] 扩展名、"
    "[Y]/[M]/[D]/[h]/[m]/[s] 日期时间；扩展名留空保持、填 - 删除"
)


def _choice_key(choices: Sequence[tuple[str, str]], label: str, fallback: str) -> str:
    """按下拉框显示文字取回设置值；找不到时回落默认。"""
    return next((key for key, text in choices if text == label), fallback)


class MultiRenameDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """多重命名工具：掩码 / 查找替换 / 大小写 / 计数器 → 预览勾选 → 执行 → 撤销。"""

    def __init__(self, app: CmderWindow, panel: FilePanel, entries: Sequence[FileEntry]) -> None:
        import tkinter as tk
        from tkinter import ttk

        self._tk = tk
        self.app = app
        self.panel = panel
        self.entries = list(entries)
        self.presets = list(app.settings.rename_presets)
        self.inputs = self._build_inputs()
        self._preview: RenamePreview | None = None
        self._row_items: dict[str, RenamePreviewItem] = {}
        self._unchecked: set[str] = set()
        self._last_report: RenameReport | None = None
        self._preview_job: str | None = None

        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title(f"多重命名工具 - {len(self.entries)} 项")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=1080, height=720)
        self.win.minsize(900, 560)
        frame = ttk.Frame(self.win, padding=spacing.md)
        frame.pack(fill="both", expand=True)

        self.preset_var = tk.StringVar()
        self.name_mask_var = tk.StringVar(value=RenameOptions().name_mask)
        self.ext_mask_var = tk.StringVar(value=RenameOptions().ext_mask)
        self.find_var = tk.StringVar()
        self.replace_var = tk.StringVar()
        self.regex_var = tk.BooleanVar(value=False)
        self.case_sensitive_var = tk.BooleanVar(value=False)
        self.only_ext_var = tk.BooleanVar(value=False)
        self.case_var = tk.StringVar(value=_RENAME_CASE_CHOICES[0][1])
        self.start_var = tk.StringVar(value="1")
        self.step_var = tk.StringVar(value="1")
        self.digits_var = tk.StringVar(value="3")
        self.sort_var = tk.StringVar(value=_RENAME_SORT_CHOICES[0][1])
        self.status_var = tk.StringVar()

        self._build_preset_row(frame, spacing)
        self._build_mask_row(frame, spacing)
        self._build_find_row(frame, spacing)
        self._build_counter_row(frame, spacing)
        self._build_buttons(frame, spacing)
        self._build_tree(frame, spacing, theme)

        for var in (
            self.name_mask_var,
            self.ext_mask_var,
            self.find_var,
            self.replace_var,
            self.regex_var,
            self.case_sensitive_var,
            self.only_ext_var,
            self.case_var,
            self.start_var,
            self.step_var,
            self.digits_var,
            self.sort_var,
        ):
            var.trace_add("write", self._on_var_change)

        self.preview()

    # ------------------------------------------------------------------ #
    # 装配
    # ------------------------------------------------------------------ #
    def _build_preset_row(self, frame: Any, spacing: Any) -> None:
        from tkinter import ttk

        row = ttk.LabelFrame(frame, text="预设", padding=spacing.sm)
        row.pack(fill="x", pady=(0, spacing.sm))
        self.preset_box = ttk.Combobox(
            row,
            textvariable=self.preset_var,
            state="readonly",
            width=28,
            values=[preset.name for preset in self.presets],
        )
        self.preset_box.pack(side="left")
        ttk.Button(row, text="加载", command=self.load_preset).pack(side="left", padx=(spacing.sm, 0))
        ttk.Button(row, text="保存为…", command=self.save_preset).pack(side="left", padx=(spacing.xs, 0))
        ttk.Button(row, text="删除", command=self.delete_preset).pack(side="left", padx=(spacing.xs, 0))
        ttk.Label(
            row,
            text=f"最多保存 {RENAME_PRESET_LIMIT} 个预设，随设置文件持久化",
            style="Muted.TLabel",
        ).pack(side="left", padx=spacing.sm)

    def _build_mask_row(self, frame: Any, spacing: Any) -> None:
        from tkinter import ttk

        row = ttk.LabelFrame(frame, text="命名掩码", padding=spacing.sm)
        row.pack(fill="x", pady=(0, spacing.sm))
        row.grid_columnconfigure(1, weight=1)
        row.grid_columnconfigure(3, weight=1)
        ttk.Label(row, text="名称：").grid(row=0, column=0, sticky="w")
        ttk.Entry(row, textvariable=self.name_mask_var).grid(row=0, column=1, sticky="ew", padx=(0, spacing.sm))
        ttk.Label(row, text="扩展名：").grid(row=0, column=2, sticky="w")
        ttk.Entry(row, textvariable=self.ext_mask_var).grid(row=0, column=3, sticky="ew")
        ttk.Label(row, text=_RENAME_MASK_HINT, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(spacing.xxs, 0)
        )

    def _build_find_row(self, frame: Any, spacing: Any) -> None:
        from tkinter import ttk

        row = ttk.LabelFrame(frame, text="查找替换", padding=spacing.sm)
        row.pack(fill="x", pady=(0, spacing.sm))
        row.grid_columnconfigure(1, weight=1)
        row.grid_columnconfigure(3, weight=1)
        ttk.Label(row, text="查找：").grid(row=0, column=0, sticky="w")
        ttk.Entry(row, textvariable=self.find_var).grid(row=0, column=1, sticky="ew", padx=(0, spacing.sm))
        ttk.Label(row, text="替换：").grid(row=0, column=2, sticky="w")
        ttk.Entry(row, textvariable=self.replace_var).grid(row=0, column=3, sticky="ew")
        ttk.Checkbutton(row, text="正则表达式", variable=self.regex_var).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(row, text="区分大小写", variable=self.case_sensitive_var).grid(row=1, column=1, sticky="w")
        ttk.Checkbutton(row, text="仅匹配扩展名", variable=self.only_ext_var).grid(
            row=1, column=2, columnspan=2, sticky="w"
        )

    def _build_counter_row(self, frame: Any, spacing: Any) -> None:
        from tkinter import ttk

        row = ttk.LabelFrame(frame, text="大小写与计数器", padding=spacing.sm)
        row.pack(fill="x", pady=(0, spacing.sm))
        ttk.Label(row, text="大小写：").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            row,
            textvariable=self.case_var,
            state="readonly",
            width=18,
            values=[label for _, label in _RENAME_CASE_CHOICES],
        ).grid(row=0, column=1, sticky="w", padx=(0, spacing.md))
        ttk.Label(row, text="起始").grid(row=0, column=2, sticky="w")
        ttk.Entry(row, textvariable=self.start_var, width=7).grid(row=0, column=3, padx=(2, spacing.sm))
        ttk.Label(row, text="步长").grid(row=0, column=4, sticky="w")
        ttk.Entry(row, textvariable=self.step_var, width=7).grid(row=0, column=5, padx=(2, spacing.sm))
        ttk.Label(row, text="位数").grid(row=0, column=6, sticky="w")
        ttk.Entry(row, textvariable=self.digits_var, width=7).grid(row=0, column=7, padx=(2, spacing.md))
        ttk.Label(row, text="计数顺序：").grid(row=0, column=8, sticky="w")
        ttk.Combobox(
            row,
            textvariable=self.sort_var,
            state="readonly",
            width=10,
            values=[label for _, label in _RENAME_SORT_CHOICES],
        ).grid(row=0, column=9, sticky="w")

    def _build_buttons(self, frame: Any, spacing: Any) -> None:
        from tkinter import ttk

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=(0, spacing.sm))
        ttk.Button(row, text="刷新预览", style="Accent.TButton", command=self.preview).pack(side="left")
        self.execute_button = ttk.Button(row, text="执行选中", command=self.execute, state="disabled")
        self.execute_button.pack(side="left", padx=spacing.sm)
        self.undo_button = ttk.Button(row, text="撤销上次执行", command=self.undo, state="disabled")
        self.undo_button.pack(side="left")
        ttk.Button(row, text="全选", command=lambda: self._set_all_checked(True)).pack(
            side="left", padx=(spacing.md, spacing.xs)
        )
        ttk.Button(row, text="全不选", command=lambda: self._set_all_checked(False)).pack(side="left")
        ttk.Label(row, text="撤销仅本次运行有效", style="Muted.TLabel").pack(side="left", padx=spacing.sm)
        ttk.Button(row, text="关闭", command=self.win.destroy).pack(side="right")

    def _build_tree(self, frame: Any, spacing: Any, theme: Any) -> None:
        from tkinter import ttk

        result_frame = ttk.Frame(frame)
        result_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            result_frame,
            columns=("sel", "old", "new", "status", "note"),
            show="headings",
            height=12,
        )
        self.tree.heading("sel", text="选")
        self.tree.heading("old", text="原名称")
        self.tree.heading("new", text="新名称")
        self.tree.heading("status", text="状态")
        self.tree.heading("note", text="说明")
        self.tree.column("sel", width=42, anchor="center", stretch=False)
        self.tree.column("old", width=280)
        self.tree.column("new", width=280)
        self.tree.column("status", width=120, stretch=False)
        self.tree.column("note", width=260)
        self.tree.tag_configure("conflict", foreground=theme.palette.danger)
        self.tree.tag_configure("invalid", foreground=theme.palette.warning)
        self.tree.tag_configure("unchanged", foreground=theme.palette.muted)
        self.tree.bind("<Button-1>", self._on_click)
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(frame, textvariable=self.status_var, style="Muted.TLabel").pack(fill="x", pady=(spacing.xs, 0))

    # ------------------------------------------------------------------ #
    # 规则与预览
    # ------------------------------------------------------------------ #
    def _build_inputs(self) -> list[RenameInput]:
        inputs: list[RenameInput] = []
        for entry in self.entries:
            try:
                info = entry.path.stat()
            except OSError:
                info = None
            inputs.append(
                RenameInput(
                    entry.path,
                    is_dir=entry.is_dir,
                    mtime=info.st_mtime if info is not None else entry.mtime,
                    ctime=info.st_ctime if info is not None else 0.0,
                )
            )
        return inputs

    def _options(self) -> RenameOptions:
        def integer(text: str, label: str) -> int:
            try:
                return int(text.strip())
            except ValueError as exc:
                raise ValueError(f"{label}必须是整数") from exc

        return RenameOptions(
            name_mask=self.name_mask_var.get(),
            ext_mask=self.ext_mask_var.get(),
            find=self.find_var.get(),
            replace=self.replace_var.get(),
            use_regex=self.regex_var.get(),
            case_sensitive=self.case_sensitive_var.get(),
            only_extension=self.only_ext_var.get(),
            case_mode=_choice_key(_RENAME_CASE_CHOICES, self.case_var.get(), "keep"),
            start=integer(self.start_var.get(), "起始值"),
            step=integer(self.step_var.get(), "步长"),
            digits=integer(self.digits_var.get(), "位数"),
            sort_key=_choice_key(_RENAME_SORT_CHOICES, self.sort_var.get(), "name"),
        )

    def _apply_options(self, options: RenameOptions) -> None:
        self.name_mask_var.set(options.name_mask)
        self.ext_mask_var.set(options.ext_mask)
        self.find_var.set(options.find)
        self.replace_var.set(options.replace)
        self.regex_var.set(options.use_regex)
        self.case_sensitive_var.set(options.case_sensitive)
        self.only_ext_var.set(options.only_extension)
        self.case_var.set(dict(_RENAME_CASE_CHOICES).get(options.case_mode, _RENAME_CASE_CHOICES[0][1]))
        self.start_var.set(str(options.start))
        self.step_var.set(str(options.step))
        self.digits_var.set(str(options.digits))
        self.sort_var.set(dict(_RENAME_SORT_CHOICES).get(options.sort_key, _RENAME_SORT_CHOICES[0][1]))

    def _on_var_change(self, *_args: Any) -> None:
        self._cancel_preview_job()
        self._preview_job = self.win.after(220, self.preview)

    def _cancel_preview_job(self) -> None:
        if self._preview_job is not None:
            with contextlib.suppress(self._tk.TclError):
                self.win.after_cancel(self._preview_job)
            self._preview_job = None

    @staticmethod
    def _key(path: Path) -> str:
        return os.path.normcase(str(path))

    def preview(self) -> None:
        """按当前规则刷新预览；规则错误时清空并提示。"""
        self._preview_job = None
        try:
            options = self._options()
        except ValueError as exc:
            self._show_error(f"规则无效：{exc}")
            return
        preview = build_rename_preview(options, self.inputs)
        self._preview = preview
        self.tree.delete(*self.tree.get_children())
        self._row_items.clear()
        if preview.error:
            self._show_error(f"规则错误：{preview.error}")
            return
        labels = {self._key(entry.path): entry for entry in self.entries}
        for item in preview.items:
            entry = labels.get(self._key(item.path))
            old_label = entry.label if entry is not None else item.old_name
            new_label = self._new_label(entry, item.new_name)
            row = self.tree.insert(
                "",
                "end",
                values=(
                    self._mark(item.status == "ok" and self._key(item.path) not in self._unchecked, item.status),
                    old_label,
                    new_label,
                    self._status_text(item),
                    item.note,
                ),
                tags=(item.status,),
            )
            self._row_items[row] = item
        self._update_status()

    def _new_label(self, entry: FileEntry | None, new_name: str) -> str:
        if entry is None or not entry.relative:
            return new_name
        parent = Path(entry.relative).parent
        return str(parent / new_name) if str(parent) not in ("", ".") else new_name

    @staticmethod
    def _mark(checked: bool, status: str) -> str:
        if status != "ok":
            return "—"
        return "☑" if checked else "☐"

    @staticmethod
    def _status_text(item: RenamePreviewItem) -> str:
        if item.status == "ok":
            return "将改名"
        if item.status == "unchanged":
            return "未变化"
        if item.status == "invalid":
            return "名称非法"
        return "冲突"

    def _show_error(self, message: str) -> None:
        self._preview = None
        self._row_items.clear()
        self.tree.delete(*self.tree.get_children())
        self.status_var.set(message)
        self.execute_button.configure(state="disabled")

    def _selected_paths(self) -> list[Path]:
        if self._preview is None:
            return []
        return [item.path for item in self._preview.ok if self._key(item.path) not in self._unchecked]

    def _update_status(self) -> None:
        if self._preview is None or self._preview.error:
            return
        preview = self._preview
        selected = len(self._selected_paths())
        self.status_var.set(
            f"预览：可执行 {len(preview.ok)} 项（已勾选 {selected}），未变化 {len(preview.unchanged)}，"
            f"冲突 {len(preview.conflicts)}，非法 {len(preview.invalid)}"
        )
        if not self.app.busy:
            self.execute_button.configure(state="normal" if selected else "disabled")

    def _on_click(self, event: Any) -> str | None:
        if self.tree.identify_column(event.x) != "#1":
            return None
        row = self.tree.identify_row(event.y)
        item = self._row_items.get(row)
        if item is None or item.status != "ok":
            return None
        key = self._key(item.path)
        if key in self._unchecked:
            self._unchecked.discard(key)
        else:
            self._unchecked.add(key)
        self.tree.set(row, "sel", self._mark(key not in self._unchecked, item.status))
        self._update_status()
        return "break"

    def _set_all_checked(self, checked: bool) -> None:
        if self._preview is None:
            return
        for item in self._preview.ok:
            key = self._key(item.path)
            if checked:
                self._unchecked.discard(key)
            else:
                self._unchecked.add(key)
        for row, item in self._row_items.items():
            if item.status == "ok":
                self.tree.set(row, "sel", self._mark(checked, item.status))
        self._update_status()

    # ------------------------------------------------------------------ #
    # 预设
    # ------------------------------------------------------------------ #
    def _sync_preset_box(self) -> None:
        self.preset_box.configure(values=[preset.name for preset in self.presets])

    def _persist_presets(self) -> None:
        self.app.apply_rename_presets(self.presets)
        self._sync_preset_box()

    def load_preset(self) -> None:
        name = self.preset_var.get()
        preset = next((item for item in self.presets if item.name == name), None)
        if preset is None:
            self.status_var.set("请先选择一个预设")
            return
        self._cancel_preview_job()
        self._apply_options(preset.options)
        self.preview()

    def save_preset(self) -> None:
        name = self.app._simpledialog.askstring("保存预设", "预设名称：", parent=self.win)
        if not name or not name.strip():
            return
        name = name.strip()
        try:
            options = self._options()
        except ValueError as exc:
            self.status_var.set(f"规则无效：{exc}")
            return
        existing = next((index for index, item in enumerate(self.presets) if item.name == name), None)
        if existing is None and len(self.presets) >= RENAME_PRESET_LIMIT:
            self.app._messagebox.showwarning(
                "保存预设",
                f"最多保存 {RENAME_PRESET_LIMIT} 个预设，请先删除旧预设",
                parent=self.win,
            )
            return
        preset = RenamePreset(name, options)
        if existing is None:
            self.presets.append(preset)
        else:
            self.presets[existing] = preset
        self._persist_presets()
        self.preset_var.set(name)
        self.status_var.set(f"已保存预设「{name}」")

    def delete_preset(self) -> None:
        name = self.preset_var.get()
        if not name:
            self.status_var.set("请先选择一个预设")
            return
        if not self.app._messagebox.askyesno("删除预设", f"删除预设「{name}」吗？", parent=self.win):
            return
        self.presets = [item for item in self.presets if item.name != name]
        self._persist_presets()
        self.preset_var.set("")
        self.status_var.set(f"已删除预设「{name}」")

    # ------------------------------------------------------------------ #
    # 执行与撤销
    # ------------------------------------------------------------------ #
    def _selection_labels(self, paths: Sequence[Path]) -> list[str]:
        root = Path(self.panel.state.path)
        if self.panel.state.branch_view:
            return [branch_relative_path(root, path) for path in paths]
        return [path.name for path in paths]

    def execute(self) -> None:
        """执行勾选且无冲突的行。"""
        if self.app.busy or self._preview is None:
            return
        selected = self._selected_paths()
        if not selected:
            return
        if not self.app._messagebox.askyesno("多重命名", f"确定执行 {len(selected)} 项改名吗？", parent=self.win):
            return
        plan = preview_to_plan(self._preview, selected)
        if not plan.changes:
            return
        self._cancel_preview_job()

        def task() -> RenameReport:
            return apply_rename_plan(plan, progress=self.app._progress_callback)

        def done(report: RenameReport) -> None:
            self._last_report = report if report.completed else None
            self.app.refresh_both()
            self.panel.select_names(self._selection_labels([target for _source, target in report.completed]))
            summary = f"改名完成：成功 {len(report.completed)}，失败 {len(report.errors)}"
            self.status_var.set(summary)
            self._preview = None
            self._row_items.clear()
            self.tree.delete(*self.tree.get_children())
            self.execute_button.configure(state="disabled")
            self.undo_button.configure(state="normal" if self._last_report else "disabled")
            if report.errors:
                details = "\n".join(f"{source.name}: {error}" for source, error in report.errors[:_MAX_ERROR_DETAILS])
                self.app._messagebox.showwarning("多重命名（有失败）", f"{summary}\n\n{details}", parent=self.win)

        self.app.start_worker(task, done, title=f"多重命名 {len(plan.changes)} 项")

    def undo(self) -> None:
        """撤销本次运行内最近一次执行。"""
        if self.app.busy or self._last_report is None:
            return
        report = self._last_report

        def task() -> RenameReport:
            return undo_rename(report, progress=self.app._progress_callback)

        def done(result: RenameReport) -> None:
            self.app.refresh_both()
            self.panel.select_names(self._selection_labels([source for _target, source in result.completed]))
            self.status_var.set(f"已撤销 {len(result.completed)} 项（仅本次运行有效）")
            if result.errors:
                details = "\n".join(f"{path.name}: {error}" for path, error in result.errors[:_MAX_ERROR_DETAILS])
                self.app._messagebox.showwarning("撤销（有失败）", details, parent=self.win)
            if len(result.completed) == len(report.completed):
                self._last_report = None
            self.undo_button.configure(state="normal" if self._last_report else "disabled")
            self._preview = None
            self._row_items.clear()
            self.tree.delete(*self.tree.get_children())
            self.execute_button.configure(state="disabled")

        self.app.start_worker(task, done, title=f"撤销改名 {len(report.completed)} 项")


class SearchDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """文件名 / 内容搜索对话框。"""

    def __init__(self, app: CmderWindow, directory: Path, *, archive: bool = False) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.directory = directory
        self.archive = directory if archive else None
        self._tk = tk
        self._ttk = ttk
        self._hits: list[SearchHit] = []
        self._archive_hits: list[ArchiveHit] = []

        theme = app.theme
        spacing = theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title(f"搜索 - {'压缩包 ' if archive else ''}{directory}")
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
        self.case_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="区分大小写", variable=self.case_var).pack(side="left")
        self.whole_word_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="整词", variable=self.whole_word_var).pack(side="left", padx=spacing.sm)
        self.extension_var = tk.StringVar()
        if archive:
            ttk.Label(form, text="（名称用 glob，内容用正则；加密与超大成员自动跳过）").pack(side="left")
        else:
            ttk.Label(form, text="扩展名：").pack(side="left")
            ttk.Entry(form, textvariable=self.extension_var, width=10).pack(side="left", padx=(0, spacing.sm))
        self.search_button = ttk.Button(form, text="搜索", style="Accent.TButton", command=self.run_search)
        self.search_button.pack(side="left")

        self.min_size_var = tk.StringVar()
        self.max_size_var = tk.StringVar()
        self.after_var = tk.StringVar()
        self.before_var = tk.StringVar()
        if not archive:
            filter_bar = ttk.Frame(frame)
            filter_bar.pack(fill="x", pady=(0, spacing.xs))
            for label, variable, width in (
                ("最小大小", self.min_size_var, 8),
                ("最大大小", self.max_size_var, 8),
                ("修改从", self.after_var, 10),
                ("修改到", self.before_var, 10),
            ):
                ttk.Label(filter_bar, text=f"{label}：").pack(side="left")
                ttk.Entry(filter_bar, textvariable=variable, width=width).pack(side="left", padx=(0, spacing.xs))
            ttk.Label(filter_bar, text="（支持 K/M/G，日期 YYYY-MM-DD）", style="Muted.TLabel").pack(side="left")

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
        if archive:
            ttk.Button(action_bar, text="定位成员", command=self._reveal_selected).pack(side="left")
        else:
            ttk.Button(action_bar, text="打开所在目录", command=self._reveal_selected).pack(side="left")
            ttk.Button(action_bar, text="复制…", command=lambda: self._transfer(move=False)).pack(
                side="left", padx=(spacing.xs, 0)
            )
            ttk.Button(action_bar, text="移动…", command=lambda: self._transfer(move=True)).pack(
                side="left", padx=(spacing.xs, 0)
            )
            ttk.Button(action_bar, text="删除到回收站", command=self._delete).pack(side="left", padx=(spacing.xs, 0))
            ttk.Button(action_bar, text="加入面板选择", command=self._adopt).pack(side="left", padx=(spacing.xs, 0))
            ttk.Button(action_bar, text="结果面板", command=self._open_results_panel).pack(
                side="left", padx=(spacing.xs, 0)
            )
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
        if self.archive is not None:
            self.search_button.configure(state="disabled")
            self.status_var.set("搜索中...")
            self.app.start_archive_search(
                self.archive,
                pattern,
                content=bool(self.content_var.get()),
                case_sensitive=bool(self.case_var.get()),
                whole_word=bool(self.whole_word_var.get()),
                on_done=self._show_archive_results,
                on_error=self._search_failed,
            )
            return
        try:
            filters = SearchFilters(
                min_size=parse_size(self.min_size_var.get()),
                max_size=parse_size(self.max_size_var.get()),
                modified_after=parse_datetime(self.after_var.get()),
                modified_before=parse_datetime(self.before_var.get(), end_of_day=True),
            )
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        if filters.min_size is not None and filters.max_size is not None and filters.min_size > filters.max_size:
            self.status_var.set("最小大小不能大于最大大小")
            return
        self.search_button.configure(state="disabled")
        self.status_var.set("搜索中...")
        self.app.start_search(
            self.directory,
            pattern,
            content=bool(self.content_var.get()),
            extension=self.extension_var.get().strip(),
            case_sensitive=bool(self.case_var.get()),
            whole_word=bool(self.whole_word_var.get()),
            filters=filters,
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

    def _show_archive_results(self, report: ArchiveSearchReport) -> None:
        self._archive_hits = list(report.hits)
        self.tree.delete(*self.tree.get_children())
        for index, hit in enumerate(report.hits):
            self.tree.insert("", "end", iid=str(index), values=(hit.member, hit.line or "", hit.text))
        self.search_button.configure(state="normal")
        status = f"找到 {len(report.hits)} 个结果（扫描 {report.scanned} 个成员"
        if report.skipped:
            status += f"，跳过 {report.skipped} 个"
        if report.truncated:
            status += "，结果已截断"
        self.status_var.set(f"{status}）")

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

    def _selected_archive_members(self) -> list[str]:
        members: list[str] = []
        for iid in self.tree.selection():
            member = self._archive_hits[int(iid)].member
            if member not in members:
                members.append(member)
        return members

    def _reveal_selected(self, _event: Any = None) -> None:
        if self.archive is not None:
            members = self._selected_archive_members()
            if not members:
                self.status_var.set("请先选择结果")
                return
            self.app.reveal_archive_member(self.archive, members[0])
            self.win.destroy()
            return
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

    def _open_results_panel(self) -> None:
        if not self._hits:
            self.status_var.set("请先执行搜索")
            return
        SearchResultsPanel(self.app, self._hits)


class SearchResultsPanel:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """搜索结果临时面板：可排序、可多选并复用复制/移动/删除操作。"""

    def __init__(self, app: CmderWindow, hits: list[SearchHit]) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.results = collect_results(hit.path for hit in hits)
        self._visible = sort_results(self.results)
        self._column = "name"
        self._reverse = False
        theme = app.theme
        spacing = theme.spacing

        self.win = tk.Toplevel(app.root)
        self.win.title(f"搜索结果面板（{len(self.results)} 项）")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=980, height=620)
        frame = ttk.Frame(self.win, padding=spacing.sm)
        frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            frame, columns=("name", "size", "modified", "path"), show="headings", selectmode="extended"
        )
        for column, width in (("name", 240), ("size", 100), ("modified", 150), ("path", 440)):
            self.tree.heading(column, text=SORT_COLUMNS[column], command=lambda key=column: self._sort_by(key))
            self.tree.column(column, width=width, anchor="e" if column == "size" else "w")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self._reveal)

        actions = ttk.Frame(self.win, padding=(spacing.sm, 0, spacing.sm, spacing.sm))
        actions.pack(fill="x")
        ttk.Button(actions, text="打开所在目录", command=self._reveal).pack(side="left")
        ttk.Button(actions, text="复制…", command=lambda: self._transfer(move=False)).pack(
            side="left", padx=(spacing.xs, 0)
        )
        ttk.Button(actions, text="移动…", command=lambda: self._transfer(move=True)).pack(
            side="left", padx=(spacing.xs, 0)
        )
        ttk.Button(actions, text="删除到回收站", command=self._delete).pack(side="left", padx=(spacing.xs, 0))
        ttk.Button(actions, text="加入面板选择", command=self._adopt).pack(side="left", padx=(spacing.xs, 0))
        ttk.Button(actions, text="关闭", command=self.win.destroy).pack(side="right")
        self.status_var = tk.StringVar(value="点击表头排序；双击定位")
        ttk.Label(actions, textvariable=self.status_var, style="Muted.TLabel").pack(side="left", padx=spacing.sm)
        self._reload()

    def _reload(self) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, result in enumerate(self._visible):
            self.tree.insert(
                "",
                "end",
                iid=str(index),
                values=(result.path.name, format_size(result.size), format_mtime(result.modified), str(result.path)),
            )
        self.status_var.set(
            f"{len(self._visible)} 项；按 {SORT_COLUMNS[self._column]} {'降序' if self._reverse else '升序'}"
        )

    def _sort_by(self, column: str) -> None:
        if column == self._column:
            self._reverse = not self._reverse
        else:
            self._column = column
            self._reverse = False
        self._visible = sort_results(self.results, self._column, reverse=self._reverse)
        self._reload()

    def _selected_paths(self) -> list[Path]:
        return [self._visible[int(iid)].path for iid in self.tree.selection()]

    def _reveal(self, _event: Any = None) -> None:
        paths = self._selected_paths()
        if not paths:
            self.status_var.set("请先选择结果")
            return
        self.app.reveal_path(paths[0])

    def _transfer(self, *, move: bool) -> None:
        paths = self._selected_paths()
        if paths and self.app.transfer_paths(paths, move=move):
            self.win.destroy()

    def _delete(self) -> None:
        paths = self._selected_paths()
        if paths and self.app.delete_paths(paths):
            self.win.destroy()

    def _adopt(self) -> None:
        paths = self._selected_paths()
        if not paths:
            self.status_var.set("请先选择结果")
            return
        self.status_var.set(self.app.adopt_paths(paths))
        self.win.destroy()


_ATTR_READONLY_CHOICES: dict[str, bool | None] = {"保持不变": None, "只读": True, "可写": False}
_ATTR_HIDDEN_CHOICES: dict[str, bool | None] = {"保持不变": None, "隐藏": True, "显示": False}


class AttributeDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """批量修改修改时间 / 访问时间，Windows 下切换只读与隐藏。"""

    def __init__(self, app: CmderWindow, paths: Sequence[Path]) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.paths = list(paths)
        spacing = app.theme.spacing
        self.win = tk.Toplevel(app.root)
        self.win.title(f"修改时间 / 属性（{len(self.paths)} 项）")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=520, height=300)
        frame = ttk.Frame(self.win, padding=spacing.sm)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        self.modified_var = tk.StringVar()
        self.accessed_var = tk.StringVar()
        for row, (label, variable) in enumerate((("修改时间", self.modified_var), ("访问时间", self.accessed_var))):
            ttk.Label(frame, text=f"{label}：").grid(row=row, column=0, sticky="w", pady=spacing.xxs)
            ttk.Entry(frame, textvariable=variable, width=24).grid(row=row, column=1, sticky="ew", pady=spacing.xxs)
            ttk.Button(
                frame,
                text="现在",
                style="Toolbutton",
                command=lambda var=variable: var.set(format_timestamp(time.time())),
            ).grid(row=row, column=2, padx=(spacing.xs, 0))
            ttk.Button(frame, text="清空", style="Toolbutton", command=lambda var=variable: var.set("")).grid(
                row=row, column=3, padx=(spacing.xs, 0)
            )
        ttk.Label(frame, text="留空表示保持不变；格式 YYYY-MM-DD HH:MM:SS", style="Muted.TLabel").grid(
            row=2, column=0, columnspan=4, sticky="w", pady=(0, spacing.xs)
        )

        self.readonly_var = tk.StringVar(value="保持不变")
        self.hidden_var = tk.StringVar(value="保持不变")
        if sys.platform.startswith("win"):
            for offset, (label, variable, choices) in enumerate(
                (
                    ("只读", self.readonly_var, _ATTR_READONLY_CHOICES),
                    ("隐藏", self.hidden_var, _ATTR_HIDDEN_CHOICES),
                )
            ):
                ttk.Label(frame, text=f"{label}：").grid(row=3 + offset, column=0, sticky="w", pady=spacing.xxs)
                ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=tuple(choices),
                    state="readonly",
                    width=12,
                ).grid(row=3 + offset, column=1, sticky="w", pady=spacing.xxs)

        actions = ttk.Frame(self.win, padding=(spacing.sm, 0, spacing.sm, spacing.sm))
        actions.pack(fill="x")
        ttk.Button(actions, text="应用", style="Accent.TButton", command=self._apply).pack(side="left")
        ttk.Button(actions, text="取消", command=self.win.destroy).pack(side="left", padx=(spacing.xs, 0))
        self.status_var = tk.StringVar(value=f"{len(self.paths)} 项待修改")
        ttk.Label(actions, textvariable=self.status_var, style="Muted.TLabel").pack(side="left", padx=spacing.sm)

    def _apply(self) -> None:
        try:
            change = AttributeChange(
                modified=parse_timestamp(self.modified_var.get()),
                accessed=parse_timestamp(self.accessed_var.get()),
                readonly=_ATTR_READONLY_CHOICES[self.readonly_var.get()],
                hidden=_ATTR_HIDDEN_CHOICES[self.hidden_var.get()],
            )
        except ValueError as exc:
            self.status_var.set(str(exc))
            return
        if not change.active:
            self.status_var.set("没有需要修改的属性")
            return
        report = apply_attributes(self.paths, change)
        self.app.set_status(report.message())
        if not report.ok:
            self.app.show_error("修改属性", report.message())
        self.app.left.refresh()
        self.app.right.refresh()
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
            text="队列按并行上限执行；暂停后不再启动新文件。取消按项生效：当前文件传完后停止，已完成项保留。",
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
        self.pause_button = ttk.Button(buttons, text="暂停队列", command=self.toggle_pause)
        self.pause_button.pack(side="left")
        ttk.Button(buttons, text="取消选中任务", command=self.cancel_selected).pack(side="left", padx=spacing.xs)
        ttk.Button(buttons, text="重试选中失败项", command=self.retry_selected).pack(side="left", padx=spacing.xs)
        ttk.Button(buttons, text="重试全部失败项", command=self.retry_all).pack(side="left")
        ttk.Button(buttons, text="关闭", command=self.win.destroy).pack(side="right")
        self.parallel_var = tk.StringVar(value=str(app.settings.queue_parallel))
        parallel_box = ttk.Combobox(
            buttons,
            textvariable=self.parallel_var,
            values=[str(index) for index in range(MIN_QUEUE_PARALLEL, MAX_QUEUE_PARALLEL + 1)],
            state="readonly",
            width=3,
        )
        parallel_box.pack(side="right")
        parallel_box.bind("<<ComboboxSelected>>", lambda _event: self.change_parallel())
        ttk.Label(buttons, text="并行：").pack(side="right", padx=(spacing.sm, 0))
        self.refresh()

    def refresh(self) -> None:
        """重绘任务列表与当前任务的条目明细。"""
        self.pause_button.configure(text="恢复队列" if self.app.queue_paused else "暂停队列")
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

    def toggle_pause(self) -> None:
        """暂停或恢复队列并刷新按钮文案。"""
        self.app.toggle_queue_pause()
        self.refresh()

    def change_parallel(self) -> None:
        """把并行下拉选择写回设置。"""
        self.app.set_queue_parallel(int(self.parallel_var.get()))

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


class TextDiffDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """两个文本文件的并排只读比较：增删行着色、行号对齐、同步滚动。"""

    def __init__(self, app: CmderWindow, left: Path, right: Path) -> None:
        import tkinter as tk
        from tkinter import ttk

        colors = app.theme.palette
        spacing = app.theme.spacing
        left_lines, left_cut = diffmod.load_lines(left)
        right_lines, right_cut = diffmod.load_lines(right)
        self.result = diffmod.diff_lines(left_lines, right_lines, truncated_left=left_cut, truncated_right=right_cut)
        self._syncing = False

        self.win = tk.Toplevel(app.root)
        self.win.title("文本比较（只读）")
        self.win.transient(app.root)
        _guiwindow.center_over_parent(self.win, app.root, width=1180, height=720)
        self.win.minsize(800, 480)
        frame = ttk.Frame(self.win, padding=spacing.sm)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=f"左：{left}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text=f"右：{right}", style="Muted.TLabel").pack(anchor="w")
        ttk.Label(frame, text=diffmod.summary_text(self.result)).pack(anchor="w", pady=(spacing.xxs, spacing.xs))

        body = ttk.Frame(frame)
        body.pack(fill="both", expand=True)
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(2, weight=1)
        self.left_text = self._build_text(body, colors)
        self.right_text = self._build_text(body, colors)
        self.left_text.grid(row=0, column=0, sticky="nsew")
        self.right_text.grid(row=0, column=2, sticky="nsew")

        left_y = ttk.Scrollbar(body, orient="vertical", command=self._scroll_both)
        right_y = ttk.Scrollbar(body, orient="vertical", command=self._scroll_both)
        left_y.grid(row=0, column=1, sticky="ns")
        right_y.grid(row=0, column=3, sticky="ns")
        left_x = ttk.Scrollbar(body, orient="horizontal", command=self.left_text.xview)
        right_x = ttk.Scrollbar(body, orient="horizontal", command=self.right_text.xview)
        left_x.grid(row=1, column=0, sticky="ew")
        right_x.grid(row=1, column=2, sticky="ew")
        self.left_text.configure(xscrollcommand=left_x.set, yscrollcommand=self._on_yview)
        self.right_text.configure(xscrollcommand=right_x.set, yscrollcommand=self._on_yview)

        for row in self.result.rows:
            self._insert_line(
                self.left_text,
                row.left_no,
                row.left_text,
                "delete" if row.kind == diffmod.DELETE else "change" if row.kind == diffmod.CHANGE else "",
            )
            self._insert_line(
                self.right_text,
                row.right_no,
                row.right_text,
                "add" if row.kind == diffmod.ADD else "change" if row.kind == diffmod.CHANGE else "",
            )
        self.left_text.configure(state="disabled")
        self.right_text.configure(state="disabled")
        ttk.Button(frame, text="关闭", command=self.win.destroy).pack(anchor="e", pady=(spacing.xs, 0))

    @staticmethod
    def _build_text(parent: Any, colors: Any) -> Any:
        import tkinter as tk

        widget = tk.Text(
            parent,
            wrap="none",
            font=_guitheme.MONO_FONT,
            background=colors.log_bg,
            foreground=colors.log_fg,
            insertbackground=colors.cursor,
            selectbackground=colors.select_bg,
            selectforeground=colors.select_fg,
            relief="flat",
            borderwidth=0,
        )
        widget.tag_configure("lineno", foreground=colors.muted)
        widget.tag_configure("add", background=colors.success, foreground=colors.on_accent)
        widget.tag_configure("delete", background=colors.danger, foreground=colors.on_accent)
        widget.tag_configure("change", background=colors.warning, foreground=colors.on_accent)
        return widget

    @staticmethod
    def _insert_line(widget: Any, number: int | None, content: str, tag: str) -> None:
        if number is None:
            widget.insert("end", "\n")
            return
        widget.insert("end", f"{number:>5}  ", ("lineno",))
        widget.insert("end", f"{content}\n", (tag,) if tag else ())

    def _scroll_both(self, *args: Any) -> None:
        self.left_text.yview(*args)
        self.right_text.yview(*args)

    def _on_yview(self, first: str, _last: str) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self.right_text.yview_moveto(float(first))
        finally:
            self._syncing = False


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
        self.files_only_var = tk.BooleanVar(value=False)
        self.mode_var = tk.StringVar(value=COMPARE_MODE_TEXT[COMPARE_MODE_TIME])
        self.tolerance_var = tk.StringVar(value="2.0")
        ttk.Checkbutton(options, text="递归子目录", variable=self.recursive_var).pack(side="left")
        ttk.Checkbutton(options, text="包含隐藏项", variable=self.hidden_var).pack(side="left", padx=spacing.sm)
        ttk.Checkbutton(options, text="仅比较文件", variable=self.files_only_var).pack(
            side="left", padx=(0, spacing.sm)
        )
        ttk.Label(options, text="比较").pack(side="left")
        mode_box = ttk.Combobox(
            options,
            textvariable=self.mode_var,
            values=[COMPARE_MODE_TEXT[mode] for mode in COMPARE_MODES],
            state="readonly",
            width=8,
        )
        mode_box.pack(side="left", padx=(spacing.xxs, spacing.sm))
        ttk.Label(options, text="时间容差（秒）").pack(side="left")
        ttk.Entry(options, textvariable=self.tolerance_var, width=6).pack(side="left", padx=(spacing.xxs, spacing.sm))
        self.start_button = ttk.Button(options, text="开始比较", style="Accent.TButton", command=self.start_compare)
        self.start_button.pack(side="left")

        masks = ttk.Frame(frame)
        masks.pack(fill="x", pady=(0, spacing.xxs))
        self.mask_var = tk.StringVar()
        self.exclude_var = tk.StringVar()
        ttk.Label(masks, text="包含掩码").pack(side="left")
        ttk.Entry(masks, textvariable=self.mask_var, width=24).pack(side="left", padx=(spacing.xxs, spacing.sm))
        ttk.Label(masks, text="排除掩码").pack(side="left")
        ttk.Entry(masks, textvariable=self.exclude_var, width=24).pack(side="left", padx=(spacing.xxs, spacing.sm))
        ttk.Label(masks, text="多个掩码用 ; 分隔，只作用于文件名", style="Muted.TLabel").pack(side="left")

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
        text_to_mode = {text: mode for mode, text in COMPARE_MODE_TEXT.items()}
        mode = text_to_mode.get(self.mode_var.get(), COMPARE_MODE_TIME)
        return CompareOptions(
            recursive=bool(self.recursive_var.get()),
            include_hidden=bool(self.hidden_var.get()),
            mode=mode,
            mask=self.mask_var.get().strip(),
            exclude_mask=self.exclude_var.get().strip(),
            files_only=bool(self.files_only_var.get()),
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
        self.selection: frozenset[str] = frozenset()
        self._row_keys: dict[str, str] = {}
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
            controls, text="左 → 右", value=SYNC_LTR, variable=self.direction_var, command=self._on_direction_toggle
        ).pack(side="left")
        ttk.Radiobutton(
            controls, text="右 → 左", value=SYNC_RTL, variable=self.direction_var, command=self._on_direction_toggle
        ).pack(side="left", padx=spacing.sm)
        ttk.Radiobutton(
            controls, text="双向 ⇄", value=SYNC_BOTH, variable=self.direction_var, command=self._on_direction_toggle
        ).pack(side="left")
        ttk.Label(controls, text="冲突").pack(side="left", padx=(spacing.lg, 0))
        self.conflict_var = tk.StringVar(value=SYNC_CONFLICT_TEXT[SYNC_CONFLICT_OVERWRITE])
        ttk.Combobox(
            controls,
            textvariable=self.conflict_var,
            values=[SYNC_CONFLICT_TEXT[name] for name in SYNC_CONFLICTS],
            state="readonly",
            width=10,
        ).pack(side="left", padx=(spacing.xxs, 0))
        self.delete_var = tk.BooleanVar(value=False)
        self.delete_dirs_var = tk.BooleanVar(value=False)
        self.delete_check = ttk.Checkbutton(
            controls, text="同时删除目标端多余文件", variable=self.delete_var, command=self._on_delete_toggle
        )
        self.delete_check.pack(side="left", padx=(spacing.lg, 0))
        self.delete_dirs_check = ttk.Checkbutton(
            controls, text="允许删除多余目录及其内容", variable=self.delete_dirs_var, command=self.refresh_plan
        )
        self.delete_dirs_check.pack(side="left", padx=spacing.sm)
        self.delete_dirs_check.configure(state="disabled")

        note = "勾选要执行的项（点击首列或按空格切换）；执行前会重新校验大小与时间，不一致的项将跳过。"
        ttk.Label(frame, text=note, style="Muted.TLabel").pack(fill="x", pady=(0, spacing.xxs))

        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(
            tree_frame,
            columns=("pick", "action", "relative", "source", "target", "note"),
            show="headings",
            selectmode="extended",
        )
        for column, title, width, anchor in (
            ("pick", "选中", 48, "center"),
            ("action", "操作", 80, "w"),
            ("relative", "相对路径", 240, "w"),
            ("source", "源", 300, "w"),
            ("target", "目标", 300, "w"),
            ("note", "说明", 180, "w"),
        ):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, anchor=anchor)
        self.tree.bind("<ButtonRelease-1>", self._on_tree_click)
        self.tree.bind("<space>", self._on_space_toggle)
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
        ttk.Button(bottom, text="反选", command=self._invert_selection).pack(side="right", padx=(0, spacing.xs))
        ttk.Button(bottom, text="全不选", command=self._select_none).pack(side="right", padx=(0, spacing.xs))
        ttk.Button(bottom, text="全选", command=self._select_all).pack(side="right", padx=(0, spacing.xs))
        self.refresh_plan()

    def _on_delete_toggle(self) -> None:
        """「删除多余文件」控制目录删除勾选框的可用性。"""
        if not self.delete_var.get():
            self.delete_dirs_var.set(False)
        self._sync_delete_states()
        self.refresh_plan()

    def _on_direction_toggle(self) -> None:
        """双向镜像不删除任何一侧的独有项，切回单向时恢复删除选项。"""
        if self.direction_var.get() == SYNC_BOTH:
            self.delete_var.set(False)
            self.delete_dirs_var.set(False)
        self._sync_delete_states()
        self.refresh_plan()

    def _sync_delete_states(self) -> None:
        """按方向与勾选状态刷新两级删除控件的可用性。"""
        bidirectional = self.direction_var.get() == SYNC_BOTH
        self.delete_check.configure(state="disabled" if bidirectional else "normal")
        dirs_ready = not bidirectional and bool(self.delete_var.get())
        self.delete_dirs_check.configure(state="normal" if dirs_ready else "disabled")

    def refresh_plan(self) -> None:
        """按当前方向、冲突策略与勾选项重新生成预演。"""
        text_to_conflict = {text: name for name, text in SYNC_CONFLICT_TEXT.items()}
        conflict = text_to_conflict.get(self.conflict_var.get(), SYNC_CONFLICT_OVERWRITE)
        direction = self.direction_var.get()
        options = SyncOptions(
            direction=direction,
            conflict=conflict,
            delete_target_extra=direction != SYNC_BOTH and bool(self.delete_var.get()),
            delete_dirs=direction != SYNC_BOTH and bool(self.delete_dirs_var.get()),
            mtime_tolerance=self.tolerance,
        )
        self.plan = build_sync_plan(self.report, options)
        self.selection = sync_select_all(self.plan)
        self._render(self.plan)

    def _on_tree_click(self, event: Any) -> None:
        """点击首列切换该行勾选；其它列保持普通选择行为。"""
        if self.tree.identify_column(event.x) != "#1":
            return
        row = self.tree.identify_row(event.y)
        if row:
            self._toggle_rows((row,))

    def _on_space_toggle(self, _event: Any) -> str:
        """空格切换所有选中行，返回 ``break`` 阻止默认滚动。"""
        self._toggle_rows(self.tree.selection())
        return "break"

    def _toggle_rows(self, rows: tuple[str, ...]) -> None:
        """按行键翻转勾选并立即重绘。"""
        for row in rows:
            key = self._row_keys.get(row)
            if key is not None:
                self.selection = sync_toggle_selection(self.selection, key)
        if self.plan is not None:
            self._render(self.plan)

    def _select_all(self) -> None:
        if self.plan is None:
            return
        self.selection = sync_select_all(self.plan)
        self._render(self.plan)

    def _select_none(self) -> None:
        self.selection = frozenset()
        if self.plan is not None:
            self._render(self.plan)

    def _invert_selection(self) -> None:
        if self.plan is None:
            return
        self.selection = sync_invert_selection(self.plan, self.selection)
        self._render(self.plan)

    def _render(self, plan: SyncPlan) -> None:
        """重绘预演明细（含勾选列）与汇总。"""
        self.tree.delete(*self.tree.get_children())
        self._row_keys.clear()
        selected_copies, selected_deletes, unselected = split_sync_selection(plan, self.selection)
        for index, item in enumerate(plan.copies):
            key = sync_item_key(item)
            note = "目录" if item.is_dir else ("覆盖已有文件" if item.overwrite else "新建文件")
            self._insert_row(
                index,
                (
                    self._pick_mark(key),
                    "覆盖" if item.overwrite else "复制",
                    item.relative,
                    str(item.source or ""),
                    str(item.target),
                    note,
                ),
                key,
            )
        offset = len(plan.copies)
        for index, item in enumerate(plan.deletes):
            key = sync_item_key(item)
            note = "目录" if item.is_dir else f"文件，{format_size(item.target_size or 0)}"
            self._insert_row(
                offset + index,
                (self._pick_mark(key), "删除", item.relative, "", str(item.target), note),
                key,
            )
        offset += len(plan.deletes)
        for index, item in enumerate(plan.skipped):
            self._insert_row(
                offset + index,
                ("—", "跳过", item.relative, "", str(item.target), item.skip_reason),
                None,
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
        total = len(plan.copies) + len(plan.deletes)
        if total:
            parts.append(f"已选 {len(selected_copies) + len(selected_deletes)}/{total} 项")
            if unselected:
                parts.append(f"未勾选 {unselected} 项")
        prefix = "双向镜像" if plan.is_bidirectional else "单向同步"
        self.status_var.set(f"{prefix}：" + "；".join(parts))
        self.start_button.configure(state="normal" if (selected_copies or selected_deletes) else "disabled")

    def _pick_mark(self, key: str) -> str:
        """首列勾选标记。"""
        return "☑" if key in self.selection else "☐"

    def _insert_row(self, index: int, values: tuple[str, ...], key: str | None) -> None:
        """插入一行；可执行行记录键供勾选切换。"""
        iid = str(index)
        self.tree.insert("", "end", iid=iid, values=values)
        if key is not None:
            self._row_keys[iid] = key

    def execute(self) -> None:
        """二次确认后只执行勾选项；删除前再单独确认一次。"""
        plan = self.plan
        if plan is None:
            return
        copies, deletes, unselected = split_sync_selection(plan, self.selection)
        if not copies and not deletes:
            return
        copy_new = sum(1 for item in copies if not item.overwrite)
        if plan.is_bidirectional:
            lines = [f"将执行勾选的操作（双向镜像：{plan.left_root} ⇄ {plan.right_root}）："]
        else:
            lines = [f"将执行勾选的操作（{plan.source_root} → {plan.target_root}）："]
        if copies:
            lines.append(f"- 复制 {copy_new} 个新文件")
            lines.append(f"- 覆盖 {len(copies) - copy_new} 个已有文件")
        if deletes:
            dirs = sum(1 for item in deletes if item.is_dir)
            lines.append(f"- 删除 {len(deletes)} 项（目录 {dirs} 个，移入回收站）")
        if unselected:
            lines.append(f"- 未勾选 {unselected} 项不执行")
        lines += [
            "",
            "执行前会重新校验每个目标文件的大小与修改时间；与预演不一致的项将跳过，并在结果里单独列出。",
        ]
        if deletes:
            lines.append("删除不可撤销（回收站可还原）。")
        if not self.app._messagebox.askokcancel("确认执行同步", "\n".join(lines), parent=self.win):
            return
        if deletes and not self._confirm_deletes(deletes):
            return
        self._run(plan, copies, deletes, unselected)

    def _confirm_deletes(self, deletes: list[SyncItem]) -> bool:
        """删除单独确认：逐条列出完整路径与总数。"""
        side = {SYNC_LTR: "右侧", SYNC_RTL: "左侧"}.get(self.direction_var.get(), "目标端")
        dirs = sum(1 for item in deletes if item.is_dir)
        lines = ["确认删除目标端多余项", "", f"即将从{side}移入回收站以下 {len(deletes)} 项：", ""]
        for index, item in enumerate(deletes, start=1):
            kind = "目录" if item.is_dir else f"文件，{format_size(item.target_size or 0)}"
            lines.append(f"{index}. {item.target}（{kind}）")
        lines += [
            "",
            f"共 {len(deletes)} 项，其中目录 {dirs} 个。",
            "删除不可撤销（回收站可还原）。",
            "删除逐项执行；任一项失败立即停止，并汇报已完成与未执行部分。",
            "回收站不可用时整批中止，不会改为永久删除。",
        ]
        return bool(self.app._messagebox.askokcancel("确认删除目标端多余项", "\n".join(lines), parent=self.win))

    def _run(
        self,
        plan: SyncPlan,
        copies: list[SyncItem],
        deletes: list[SyncItem],
        unselected: int,
    ) -> None:
        """复核后复制入队、删除逐项执行，并用结果窗口汇报。"""
        ready_copies, skipped_copies = revalidate_sync_items(copies, mtime_tolerance=self.tolerance)
        ready_deletes, skipped_deletes = revalidate_sync_items(deletes, mtime_tolerance=self.tolerance)
        enqueue_ok = True
        if ready_copies:
            groups: dict[Path, list[TransferItem]] = {}
            for item in ready_copies:
                if item.source is None:
                    continue
                transfer = TransferItem(source=item.source, target=item.target, overwrite=item.overwrite)
                groups.setdefault(plan.root_for(item.target), []).append(transfer)
            for destination, items in groups.items():
                if not self.app.enqueue_transfer(items, destination, move=False):
                    enqueue_ok = False
        delete_report = SyncDeleteReport()
        if enqueue_ok and ready_deletes:
            delete_report = execute_sync_deletes(ready_deletes)
        self.app.refresh_both()
        self.start_button.configure(state="disabled")
        self.status_var.set("已执行；复制任务在传输队列中继续，完成后可重新预演")
        report = SyncRunReport(
            copied=len(ready_copies) if enqueue_ok else 0,
            copied_new=sum(1 for item in ready_copies if not item.overwrite),
            deleted=len(delete_report.completed),
            delete_not_executed=len(delete_report.not_executed) if enqueue_ok else len(ready_deletes),
            unselected=unselected,
            enqueue_ok=enqueue_ok,
            skipped=tuple((str(item.target), reason) for item, reason in (*skipped_copies, *skipped_deletes)),
            failed=tuple((str(item.target), error) for item, error in delete_report.failures),
        )
        self._show_result(format_sync_report(report))

    def _show_result(self, lines: list[str]) -> None:
        """执行结果窗口：明细完整可见，可选中复制。"""
        import tkinter as tk
        from tkinter import ttk

        spacing = self.app.theme.spacing
        win = tk.Toplevel(self.win)
        win.title("同步结果")
        win.transient(self.win)
        _guiwindow.center_over_parent(win, self.win, width=760, height=460)
        frame = ttk.Frame(win, padding=spacing.md)
        frame.pack(fill="both", expand=True)
        body = tk.Text(frame, wrap="word", height=16)
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=body.yview)
        body.configure(yscrollcommand=scrollbar.set)
        body.insert("1.0", "\n".join(lines))
        body.configure(state="disabled")
        body.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Button(win, text="关闭", command=win.destroy).pack(pady=(0, spacing.sm))


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


class UserCommandsDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """用户命令管理器：列表、表单、顺序调整与试运行。"""

    def __init__(self, app: CmderWindow) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.app = app
        self._messagebox = messagebox
        self.commands = list(app.settings.user_commands)
        self._editing: int | None = None
        spacing = app.theme.spacing
        self.window = tk.Toplevel(app.root)
        self.window.title("用户命令")
        self.window.transient(app.root)
        _guiwindow.center_over_parent(self.window, app.root, width=1000, height=640)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=spacing.sm)
        outer.pack(fill="both", expand=True)
        hint = "占位符：" + "  ".join(f"%{key} {text}" for key, text in PLACEHOLDERS.items())
        ttk.Label(outer, text=hint, style="Muted.TLabel", wraplength=960).pack(fill="x", pady=(0, spacing.xs))

        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)
        right = ttk.Frame(body, padding=(spacing.sm, 0, 0, 0))
        right.pack(side="left", fill="y")

        self._list = ttk.Treeview(
            left,
            columns=("name", "command", "where"),
            show="headings",
            selectmode="browse",
            height=16,
        )
        for column, title, width in (("name", "名称", 160), ("command", "命令", 430), ("where", "入口", 170)):
            self._list.heading(column, text=title)
            self._list.column(column, width=width, anchor="w")
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self._list.yview)
        self._list.configure(yscrollcommand=scrollbar.set)
        self._list.pack(side="top", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._list.bind("<<TreeviewSelect>>", self._on_select)

        actions = ttk.Frame(left)
        actions.pack(fill="x", pady=(spacing.xs, 0))
        for text, handler in (
            ("新增", self._new),
            ("删除", self._delete),
            ("上移", lambda: self._move(-1)),
            ("下移", lambda: self._move(1)),
            ("运行", self._run),
        ):
            ttk.Button(actions, text=text, style="Toolbutton", command=handler).pack(side="left")

        self._name_var = tk.StringVar()
        self._command_var = tk.StringVar()
        self._params_var = tk.StringVar()
        self._menu_var = tk.BooleanVar(value=True)
        self._toolbar_var = tk.BooleanVar(value=True)
        self._context_var = tk.BooleanVar(value=True)

        row = 0
        row = self._form_entry(right, row, "名称", self._name_var)
        row = self._form_entry(right, row, "命令", self._command_var)
        row = self._form_entry(right, row, "参数", self._params_var)
        for text, variable in (
            ("挂到菜单", self._menu_var),
            ("挂到工具栏", self._toolbar_var),
            ("挂到右键菜单", self._context_var),
        ):
            ttk.Checkbutton(right, text=text, variable=variable).grid(row=row, column=0, columnspan=2, sticky="w")
            row += 1
        ttk.Button(right, text="保存命令", command=self._commit).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(spacing.xs, 0)
        )
        row += 1
        ttk.Button(right, text="清空表单", command=self._reset).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(spacing.xxs, 0)
        )
        row += 1
        ttk.Button(right, text="关闭", command=self.window.destroy).grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(spacing.sm, 0)
        )
        self._reload()

    @staticmethod
    def _form_entry(frame: Any, row: int, label: str, variable: Any) -> int:
        from tkinter import ttk

        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=(0, 2))
        ttk.Entry(frame, textvariable=variable, width=34).grid(row=row + 1, column=0, sticky="ew", pady=(0, 6))
        return row + 2

    def _reload(self) -> None:
        for item in self._list.get_children():
            self._list.delete(item)
        for index, command in enumerate(self.commands):
            where = " ".join(
                label
                for label, enabled in (
                    ("菜单", command.in_menu),
                    ("工具栏", command.in_toolbar),
                    ("右键", command.in_context),
                )
                if enabled
            )
            self._list.insert(
                "",
                "end",
                iid=str(index),
                values=(command.name, command.full_command, where or "—"),
            )
        if self.commands:
            last = min(self._editing if self._editing is not None else len(self.commands) - 1, len(self.commands) - 1)
            self._list.selection_set(str(last))
            self._on_select()

    def _selected_index(self) -> int | None:
        selection = self._list.selection()
        if not selection:
            return None
        return int(selection[0])

    def _on_select(self, _event: Any = None) -> None:
        index = self._selected_index()
        if index is None or not 0 <= index < len(self.commands):
            return
        command = self.commands[index]
        self._editing = index
        self._name_var.set(command.name)
        self._command_var.set(command.command)
        self._params_var.set(command.parameters)
        self._menu_var.set(command.in_menu)
        self._toolbar_var.set(command.in_toolbar)
        self._context_var.set(command.in_context)

    def _new(self) -> None:
        self._editing = None
        self._reset()

    def _reset(self) -> None:
        self._editing = None
        self._name_var.set("")
        self._command_var.set("")
        self._params_var.set("")
        self._menu_var.set(True)
        self._toolbar_var.set(True)
        self._context_var.set(True)

    def _delete(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        del self.commands[index]
        self._editing = None
        self._apply()
        self._reset()
        self._reload()

    def _move(self, delta: int) -> None:
        index = self._selected_index()
        if index is None:
            return
        target = index + delta
        if not 0 <= target < len(self.commands):
            return
        self.commands[index], self.commands[target] = self.commands[target], self.commands[index]
        self._editing = target
        self._apply()
        self._reload()

    def _commit(self) -> None:
        name = self._name_var.get().strip()
        command = self._command_var.get().strip()
        if not name or not command:
            self._messagebox.showwarning("用户命令", "名称与命令都不能为空", parent=self.window)
            return
        if any(item.name == name and index != self._editing for index, item in enumerate(self.commands)):
            self._messagebox.showwarning("用户命令", f"名称已存在：{name}", parent=self.window)
            return
        if self._editing is None and len(self.commands) >= COMMAND_LIMIT:
            self._messagebox.showwarning("用户命令", f"最多 {COMMAND_LIMIT} 条用户命令", parent=self.window)
            return
        entry = UserCommand(
            name=name,
            command=command,
            parameters=self._params_var.get().strip(),
            in_menu=bool(self._menu_var.get()),
            in_toolbar=bool(self._toolbar_var.get()),
            in_context=bool(self._context_var.get()),
        )
        if self._editing is None:
            self.commands.append(entry)
            self._editing = len(self.commands) - 1
        else:
            self.commands[self._editing] = entry
        self._apply()
        self._reload()

    def _run(self) -> None:
        index = self._selected_index()
        if index is None:
            return
        self.app.run_user_command(self.commands[index])

    def _apply(self) -> None:
        self.app.apply_user_commands(self.commands)


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


class OpenWithDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """「打开方式」：选一个程序打开当前文件，并记住最近使用。"""

    def __init__(self, app: CmderWindow, entry: FileEntry) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.entry = entry
        spacing = app.theme.spacing
        self.window = tk.Toplevel(app.root)
        self.window.title("打开方式")
        self.window.transient(app.root)
        _guiwindow.center_over_parent(self.window, app.root, width=640, height=170)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=spacing.sm)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text=f"用指定程序打开：{entry.name}",
            style="Muted.TLabel",
        ).pack(fill="x", pady=(0, spacing.xs))
        row = ttk.Frame(outer)
        row.pack(fill="x")
        recent = list(app.settings.open_with)
        self.program_var = tk.StringVar(value=recent[0] if recent else "")
        self.combo = ttk.Combobox(row, textvariable=self.program_var, values=recent)
        self.combo.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="浏览…", style="Toolbutton", command=self._browse).pack(side="left", padx=(spacing.xs, 0))
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(buttons, text="打开", command=self._confirm).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right", padx=(0, spacing.xs))
        self.combo.focus_set()

    def _browse(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askopenfilename(title="选择程序", parent=self.window)
        if chosen:
            self.program_var.set(chosen)

    def _confirm(self) -> None:
        if self.app.launch_with(self.program_var.get(), self.entry.path):
            self.window.destroy()


class ArchiveOptionsDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """归档写入选项：压缩级别与密码（密码仅 zip 生效）。"""

    def __init__(self, app: CmderWindow) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        spacing = app.theme.spacing
        self.window = tk.Toplevel(app.root)
        self.window.title("归档写入选项")
        self.window.transient(app.root)
        _guiwindow.center_over_parent(self.window, app.root, width=460, height=230)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=spacing.sm)
        outer.pack(fill="both", expand=True)
        self.level_var = tk.StringVar(value=str(app.archive_options.level))
        self.password_var = tk.StringVar(value=app.archive_options.password)
        ttk.Label(outer, text="压缩级别（0–9；zip 与 tar 压缩族生效，bz2 最小为 1）").pack(fill="x")
        ttk.Spinbox(outer, from_=0, to=9, width=6, textvariable=self.level_var).pack(anchor="w", pady=(0, spacing.xs))
        ttk.Label(outer, text="密码（仅 zip，AES-256；留空表示不加密）").pack(fill="x")
        ttk.Entry(outer, textvariable=self.password_var, show="*").pack(fill="x", pady=(0, spacing.xs))
        ttk.Label(
            outer,
            text="已加密的 zip 改写时必须填写同一密码；tar 家族不支持密码。",
            style="Muted.TLabel",
        ).pack(fill="x")
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(buttons, text="确定", command=self._confirm).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right", padx=(0, spacing.xs))

    def _confirm(self) -> None:
        try:
            level = int(self.level_var.get())
        except ValueError:
            level = self.app.archive_options.level
        self.app.archive_options = ArchiveWriteOptions(level=level, password=self.password_var.get())
        self.app.set_status(
            f"归档写入选项：级别 {self.app.archive_options.level}，密码{'已设置' if self.password_var.get() else '未设置'}"
        )
        self.window.destroy()


class SplitDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """分割文件：按大小或按份数二选一。"""

    def __init__(self, app: CmderWindow, entry: FileEntry) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.entry = entry
        spacing = app.theme.spacing
        self.window = tk.Toplevel(app.root)
        self.window.title("分割文件")
        self.window.transient(app.root)
        _guiwindow.center_over_parent(self.window, app.root, width=480, height=220)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=spacing.sm)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text=f"分割：{entry.name}（{entry.size} 字节）", style="Muted.TLabel").pack(fill="x")
        self.mode_var = tk.StringVar(value="size")
        self.size_var = tk.StringVar(value="10M")
        self.parts_var = tk.StringVar(value="2")
        size_row = ttk.Frame(outer)
        size_row.pack(fill="x", pady=(spacing.xs, 0))
        ttk.Radiobutton(size_row, text="按大小", value="size", variable=self.mode_var).pack(side="left")
        ttk.Entry(size_row, textvariable=self.size_var, width=16).pack(side="left", padx=(spacing.xs, 0))
        ttk.Label(size_row, text="例如 10M / 1.5g", style="Muted.TLabel").pack(side="left", padx=(spacing.xs, 0))
        parts_row = ttk.Frame(outer)
        parts_row.pack(fill="x", pady=(spacing.xs, 0))
        ttk.Radiobutton(parts_row, text="按份数", value="parts", variable=self.mode_var).pack(side="left")
        ttk.Entry(parts_row, textvariable=self.parts_var, width=8).pack(side="left", padx=(spacing.xs, 0))
        ttk.Label(parts_row, text="生成 .001 / .002 … 与 .sha256 校验文件", style="Muted.TLabel").pack(
            side="left", padx=(spacing.xs, 0)
        )
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(buttons, text="开始分割", command=self._confirm).pack(side="right")
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right", padx=(0, spacing.xs))

    def _confirm(self) -> None:
        try:
            if self.mode_var.get() == "size":
                part_size = parse_size(self.size_var.get())
                if part_size is None:
                    raise ValueError("请填写分卷大小")
                self.app.start_split(self.entry.path, part_size=part_size)
            else:
                parts = int(self.parts_var.get())
                self.app.start_split(self.entry.path, parts=parts)
        except ValueError as exc:
            self.app.show_error("无法分割", str(exc))
            return
        self.window.destroy()


class DuplicateDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """重复文件结果：按组展示，保留每组第一项，可删除或移动其余副本。"""

    def __init__(self, app: CmderWindow, report: DuplicateReport) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.report = report
        spacing = app.theme.spacing
        self.window = tk.Toplevel(app.root)
        self.window.title("重复文件")
        self.window.transient(app.root)
        _guiwindow.center_over_parent(self.window, app.root, width=760, height=520)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=spacing.sm)
        outer.pack(fill="both", expand=True)
        ttk.Label(
            outer,
            text=f"{len(report.groups)} 组重复，可回收 {report.wasted} 字节；每组第一项默认保留。",
        ).pack(fill="x", pady=(0, spacing.xs))

        tree_frame = ttk.Frame(outer)
        tree_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(tree_frame, columns=("size", "path"), show="tree headings")
        self.tree.heading("#0", text="组")
        self.tree.heading("size", text="大小")
        self.tree.heading("path", text="路径")
        self.tree.column("#0", width=100, stretch=False)
        self.tree.column("size", width=100, anchor="e", stretch=False)
        self.tree.column("path", width=520)
        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        for index, group in enumerate(report.groups, start=1):
            parent = self.tree.insert(
                "",
                "end",
                text=f"组 {index}",
                values=(group.size, f"{len(group.paths)} 个文件"),
                open=index == 1,
            )
            for path in group.paths:
                self.tree.insert(
                    parent,
                    "end",
                    text="保留" if path == group.paths[0] else "多余",
                    values=(group.size, str(path)),
                )

        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(buttons, text="移入回收站", command=lambda: self._delete(permanent=False)).pack(side="left")
        ttk.Button(buttons, text="永久删除", command=lambda: self._delete(permanent=True)).pack(
            side="left", padx=(spacing.xs, 0)
        )
        ttk.Button(buttons, text="移动到…", command=self._move).pack(side="left", padx=(spacing.xs, 0))
        ttk.Button(buttons, text="关闭", command=self.window.destroy).pack(side="right")

    def _extras(self) -> list[Path]:
        extras = duplicate_paths(self.report)
        if not extras:
            self.app._messagebox.showinfo("重复文件", "没有可处理的多余副本", parent=self.window)
        return extras

    def _delete(self, *, permanent: bool) -> None:
        extras = self._extras()
        if not extras:
            return
        self.window.destroy()
        self.app.delete_paths(extras, permanent=permanent)

    def _move(self) -> None:
        from tkinter import filedialog

        extras = self._extras()
        if not extras:
            return
        destination = filedialog.askdirectory(title="移动多余副本到", parent=self.window)
        if not destination:
            return
        self.window.destroy()
        self.app.move_duplicate_files(extras, Path(destination))


class FilterDialog:  # pragma: no cover - Tk 视图不纳入单元覆盖（CI 无显示环境）
    """定义持久化的包含 / 排除掩码过滤器。"""

    def __init__(self, app: CmderWindow) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        spacing = app.theme.spacing
        self.window = tk.Toplevel(app.root)
        self.window.title("定义过滤器")
        self.window.transient(app.root)
        _guiwindow.center_over_parent(self.window, app.root, width=560, height=230)
        self.window.grab_set()

        outer = ttk.Frame(self.window, padding=spacing.sm)
        outer.pack(fill="both", expand=True)
        definition = app.settings.filter_definition
        self.include_var = tk.StringVar(value=definition.include)
        self.exclude_var = tk.StringVar(value=definition.exclude)
        for label, variable, hint in (
            ("包含掩码：", self.include_var, "留空表示不限制；分号分隔多个，如 *.py;*.md"),
            ("排除掩码：", self.exclude_var, "留空表示不排除；如 test_*;*.log"),
        ):
            row = ttk.Frame(outer)
            row.pack(fill="x", pady=(0, spacing.xs))
            ttk.Label(row, text=label, width=10).pack(side="left")
            entry = ttk.Entry(row, textvariable=variable)
            entry.pack(side="left", fill="x", expand=True)
            ttk.Label(row, text=hint, style="Muted.TLabel").pack(side="left", padx=(spacing.xs, 0))
            entry.bind("<Return>", lambda _event: self._apply())
        ttk.Label(
            outer,
            text="过滤仅影响列表显示，不改变磁盘上的文件；设置会持久保存并在启动时恢复。",
            style="Muted.TLabel",
        ).pack(fill="x", pady=(spacing.xs, 0))
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(spacing.sm, 0))
        ttk.Button(buttons, text="应用", style="Accent.TButton", command=self._apply).pack(side="right")
        ttk.Button(buttons, text="清除", command=self._clear).pack(side="right", padx=(0, spacing.xs))
        ttk.Button(buttons, text="取消", command=self.window.destroy).pack(side="right", padx=(0, spacing.xs))

    def _apply(self) -> None:
        definition = normalize_filter_definition({"include": self.include_var.get(), "exclude": self.exclude_var.get()})
        self.window.destroy()
        self.app.apply_filter_definition(definition)

    def _clear(self) -> None:
        self.window.destroy()
        self.app.apply_filter_definition(FilterDefinition())


class CmderWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """双面板文件管理器主窗口。"""

    def __init__(self, settings_path: Path | None = None, launch: LaunchRequest | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, simpledialog, ttk

        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._simpledialog = simpledialog
        self._settings_path = settings_path
        self.settings = load_settings(settings_path)
        self._launch = resolve_launch(launch) if launch is not None else None
        if self._launch is not None:
            if self._launch.left:
                self.settings.left.path = self._launch.left
            if self._launch.right:
                self.settings.right.path = self._launch.right
            if self._launch.select_dir:
                side = "left" if self.settings.active != "right" else "right"
                self.settings.active = side
                (self.settings.left if side == "left" else self.settings.right).path = self._launch.select_dir
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
        self._queue_gate = PauseGate()
        self._tree_visible = False
        self._tree_syncing = False
        self._tree_loaded: set[str] = set()
        self._command_visible = False
        self._command_history_index = -1
        self._command_draft = ""
        self._color_dialog: ColorRulesDialog | None = None
        self._user_command_dialog: UserCommandsDialog | None = None
        self._user_command_menus: list[Any] = []
        self._sync_browse_lock = True
        self.archive_options = ArchiveWriteOptions()

        self.root, self.theme = _guiwindow.create_root(
            title=f"cmderw - 双面板文件管理器 v{__version__}",
            saved_geometry=self.settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
            theme=self.settings.theme,
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
        self._sync_browse_lock = False
        self.set_active(self.left if self.active_side == "left" else self.right)
        if self._launch is not None and self._launch.select_name:
            panel = self.left if self.active_side == "left" else self.right
            panel.select_name(self._launch.select_name)
        self._sync_menu_state()
        self.set_status("就绪")
        if self._launch is not None and self._launch.messages:
            self.set_status("；".join(self._launch.messages))

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
        self._menu_command(file_menu, "open_archive", "进入压缩包", self.open_archive_selected)
        self._menu_command(file_menu, "view", "查看\tF3", self.view_selected)
        self._menu_command(file_menu, "edit", "编辑\tF4", self.edit_selected)
        self._menu_command(file_menu, "open_with", "打开方式…", self.open_with_selected)
        file_menu.add_separator()
        self._menu_command(file_menu, "new_folder", "新建目录\tF7", self.new_folder)
        self._menu_command(file_menu, "rename", "重命名\tF2", self.rename_selected)
        self._menu_command(file_menu, "bulk_rename", "多重命名工具…\tCtrl+M", self.bulk_rename)
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
        self._menu_command(file_menu, "set_attributes", "修改时间 / 属性…", self.set_attributes)
        self._menu_command(file_menu, "hash", "计算校验和…", self.hash_selected)
        self._menu_command(file_menu, "checksum_generate", "生成校验文件…", self.generate_checksum_file)
        self._menu_command(file_menu, "checksum_verify", "校验文件…", self.verify_checksum_file)
        self._menu_command(file_menu, "diff_text", "比较文本内容…", self.compare_text_files)
        file_menu.add_separator()
        self._menu_command(file_menu, "pack", "打包…\tAlt+F5", self.pack_selected)
        self._menu_command(file_menu, "unpack", "解压…\tAlt+F9", self.unpack_selected)
        self._menu_command(file_menu, "add_to_archive", "加入压缩包…", self.add_to_archive)
        self._menu_command(file_menu, "archive_options", "归档写入选项…", self.show_archive_options)
        self._menu_command(file_menu, "split_file", "分割文件…", self.split_selected)
        self._menu_command(file_menu, "merge_parts", "合并分卷…", self.merge_selected)
        self._menu_command(file_menu, "find_duplicates", "查找重复文件…", self.find_duplicates)
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
        self._menu_command(view_menu, "filter_definition", "定义过滤器…", self.open_filter_definition)
        self._menu_command(view_menu, "clear_filter_definition", "清除定义过滤器", self.clear_filter_definition)
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
        self._sync_var = tk.BooleanVar(value=self.settings.sync_browse)
        self._menu_check(
            view_menu,
            "sync_browse",
            "同步浏览\tCtrl+Shift+S",
            self._sync_var,
            self._apply_sync_browse,
        )
        self._branch_var = tk.BooleanVar(value=False)
        self._menu_check(
            view_menu,
            "branch_view",
            "分支视图\tCtrl+B",
            self._branch_var,
            self._apply_branch_view,
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
        self._menu_command(view_menu, "close_others", "关闭其他标签页", self.close_other_tabs)
        self._lock_var = tk.BooleanVar(value=False)
        self._menu_check(view_menu, "lock_tab", "锁定标签页", self._lock_var, self.toggle_tab_lock)
        self._menu_command(view_menu, "rename_tab", "重命名标签页…", self.rename_tab)
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
        self._user_menu = tk.Menu(commands_menu, tearoff=False)
        commands_menu.add_cascade(label="用户命令", menu=self._user_menu)
        self._populate_user_menu()
        commands_menu.add_separator()
        self._menu_command(commands_menu, "bookmarks", "书签…\tCtrl+D", self.open_bookmarks)
        self._menu_command(commands_menu, "history", "历史记录…\tAlt+Down", self.show_history_menu)
        menubar.add_cascade(label="命令", menu=commands_menu)

        config_menu = tk.Menu(menubar, tearoff=False, postcommand=self._sync_menu_state)
        self._theme_var = tk.StringVar(value=self.settings.theme)
        theme_menu = tk.Menu(config_menu, tearoff=False)
        for theme in _guitheme.THEMES.values():
            theme_menu.add_radiobutton(
                label=theme.label,
                value=theme.name,
                variable=self._theme_var,
                command=self.save_theme_choice,
            )
        config_menu.add_cascade(label="主题", menu=theme_menu)
        self._menu_command(config_menu, "color_rules", "颜色规则…", self.open_color_rules)
        self._menu_command(config_menu, "user_commands", "用户命令…", self.open_user_commands)
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
        menu.add_command(label="进入压缩包", command=self.open_archive_selected)
        menu.add_command(label="查看", command=self.view_selected)
        menu.add_command(label="编辑", command=self.edit_selected)
        menu.add_command(label="打开方式…", command=self.open_with_selected)
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
        menu.add_command(label="比较文本内容…", command=self.compare_text_files)
        menu.add_command(label="属性", command=self.show_properties)
        menu.add_command(label="修改时间 / 属性…", command=self.set_attributes)
        context_commands = [command for command in self.settings.user_commands if command.in_context]
        if context_commands:
            user_menu = self._tk.Menu(menu, tearoff=False)
            for command in context_commands:
                user_menu.add_command(
                    label=command.name,
                    command=lambda item=command: self.run_user_command(item),
                )
            menu.add_separator()
            menu.add_cascade(label="用户命令", menu=user_menu)
            self._user_command_menus.append(user_menu)
        return menu

    def _build_toolbar(self) -> None:
        spacing = self.theme.spacing
        self._toolbar = self._ttk.Frame(self.root, padding=(spacing.sm, spacing.xs))
        self._toolbar.grid(row=0, column=0, sticky="ew")
        self._populate_toolbar()

    def _populate_toolbar(self) -> None:
        """重建工具栏内容：内置动作 + 勾选工具栏的用户命令。"""
        ttk = self._ttk
        spacing = self.theme.spacing
        toolbar = self._toolbar
        for child in toolbar.winfo_children():
            child.destroy()
        self._toolbar_buttons = []
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
        user_commands = [command for command in self.settings.user_commands if command.in_toolbar]
        if user_commands:
            ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=spacing.xs)
            for command in user_commands:
                button = ttk.Button(
                    toolbar,
                    text=command.name,
                    style="Toolbutton",
                    command=lambda item=command: self.run_user_command(item),
                )
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
        self._command_entry.bind("<Up>", self._command_history_older)
        self._command_entry.bind("<Down>", self._command_history_newer)
        self._command_entry.bind("<Tab>", self._command_complete)
        self._command_entry.bind("<KeyPress>", self._command_reset_history_cycle)
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
            "<Control-Shift-s>": lambda _event: self.toggle_sync_browse(),
            "<Control-Shift-S>": lambda _event: self.toggle_sync_browse(),
            "<Control-b>": lambda _event: self.toggle_branch_view(),
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
            two_files=len(entries) == 2 and all(not entry.is_dir and not entry.archive_member for entry in entries),
            single_is_file=bool(single and not single.is_dir),
            single_is_archive=bool(
                single and not single.is_dir and single.archive_member == "" and archive_browsable(single.path)
            ),
            busy=self._busy,
            active_writable=self._directory_writable(Path(panel.state.path)),
            target_writable=self._directory_writable(Path(self.other_panel().state.path)),
            tab_count=len(panel.tabs),
            locked_tab_count=sum(1 for tab in panel.tabs if tab.locked),
            tab_locked=panel.state.locked,
            filter_defined=self.settings.filter_definition.active,
            history_index=panel.state.history_index,
            history_length=len(panel.state.history),
            panels_ready=Path(self.left.state.path).is_dir() and Path(self.right.state.path).is_dir(),
            active_exists=Path(panel.state.path).is_dir(),
            show_hidden=self.settings.show_hidden,
            tree_visible=self._tree_visible,
            quick_visible=panel.quick_visible,
            sync_browse=self.settings.sync_browse,
            branch_view=panel.state.branch_view,
            archive_view=panel.archive is not None,
            archive_writable=panel.archive is not None and archive_editable(panel.archive),
            sort_key=panel.state.sort_key,
            view_mode=panel.state.view_mode,
        )
        state = menu_state(facts)
        for key, (menu, index) in self._menu_items.items():
            menu.entryconfigure(index, state="disabled" if state.is_disabled(key) else "normal")
        self._hidden_var.set(state.show_hidden)
        self._quick_var.set(state.quick_visible)
        self._tree_var.set(state.tree_visible)
        self._sync_var.set(state.sync_browse)
        self._branch_var.set(state.branch_view)
        self._lock_var.set(state.tab_locked)
        self._sort_var.set(state.sort_key)
        self._view_var.set(state.view_mode)
        self._sync_user_command_state(panel)

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

    def on_panel_navigated(self, panel: FilePanel, old: str, new: str) -> None:
        """同步浏览：按相对动作让对侧进入同名子目录或返回上级。

        路径栏直接跳转、书签与跨盘跳转无法折算成相对动作，对侧保持不动。
        """
        if not self.settings.sync_browse or self._sync_browse_lock:
            return
        step = browse_step(old, new)
        if step is None:
            return
        other = self.right if panel is self.left else self.left
        target = browse_follow_path(other.state.path, step)
        if target is None or not Path(target).is_dir():
            self.set_status(f"同步浏览：对侧没有「{step}」，未跟随")
            return
        self._sync_browse_lock = True
        try:
            other.set_path(Path(target))
        finally:
            self._sync_browse_lock = False

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
        self._command_history_index = -1
        self._command_draft = ""
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
        self._command_history_index = -1
        self._command_draft = ""
        self.hide_command_bar()
        self.active_panel().focus_tree()
        return "break"

    def _command_history_older(self, _event: Any = None) -> str:
        """上键：在历史中向更早的命令移动。"""
        return self._move_command_history(older=True)

    def _command_history_newer(self, _event: Any = None) -> str:
        """下键：在历史中向更新的命令移动，回到最新后恢复草稿。"""
        return self._move_command_history(older=False)

    def _move_command_history(self, *, older: bool) -> str:
        if older and self._command_history_index < 0:
            self._command_draft = self._command_var.get()
        text, index, draft = command_history_step(
            self.settings.command_history,
            self._command_history_index,
            self._command_draft,
            older=older,
        )
        self._command_history_index = index
        self._command_draft = draft
        self._command_var.set(text)
        self._command_entry.icursor("end")
        return "break"

    def _command_reset_history_cycle(self, event: Any) -> None:
        """开始编辑时退出历史浏览，避免下次上键丢失当前草稿。"""
        if event.keysym not in ("Up", "Down"):
            self._command_history_index = -1
            self._command_draft = ""

    def _command_complete(self, _event: Any = None) -> str:
        """Tab：补全第一个 token；多候选时只补公共前缀并在状态栏提示。"""
        token, rest = split_command_token(self._command_var.get())
        if not token:
            return "break"
        completion = complete_token(token, self._command_candidates(token))
        if not completion.candidates:
            return "break"
        new_token = completion.token
        if completion.unique and completion_mode(token) == "command":
            new_token += " "
        self._command_var.set(f"{new_token}{rest}")
        self._command_entry.icursor(len(new_token))
        if len(completion.candidates) > 1:
            preview = "、".join(item.rstrip("/\\") for item in completion.candidates[:_COMMAND_HINT_LIMIT])
            total = len(completion.candidates)
            more = f" … 共 {total} 项" if total > _COMMAND_HINT_LIMIT else ""
            self.set_status(f"候选：{preview}{more}")
        return "break"

    def _command_candidates(self, token: str) -> list[str]:
        """按 token 产生补全候选：路径保留已输入前缀形式，命令合并目录名与工具名。"""
        if completion_mode(token) == "path":
            return self._path_candidates(token)
        cwd = Path(self.active_panel().state.path)
        candidates: list[str] = []
        with contextlib.suppress(OSError):
            candidates = [
                entry.name
                for entry in list_directory(cwd, show_hidden=self.settings.show_hidden)
                if entry.name.startswith(token)
            ]
        from fcmd.core.toolkit import list_tools

        candidates.extend(tool for tool in list_tools() if tool.startswith(token))
        return candidates

    def _path_candidates(self, token: str) -> list[str]:
        """路径补全：按已输入目录前缀与分隔符风格生成候选（目录带结尾分隔符）。"""
        if token == "~":
            base_text, name_prefix = f"~{os.sep}", ""
        else:
            slash = max(token.rfind("/"), token.rfind("\\"))
            base_text, name_prefix = token[: slash + 1], token[slash + 1 :]
        if "\\" in base_text and "/" not in base_text:
            separator = "\\"
        elif "/" in base_text:
            separator = "/"
        else:
            separator = os.sep
        base = Path(base_text).expanduser() if base_text else Path(self.active_panel().state.path)
        if not base.is_absolute():
            base = Path(self.active_panel().state.path) / base
        if not base.is_dir():
            return []
        candidates: list[str] = []
        with contextlib.suppress(OSError):
            for child in sorted(base.iterdir()):
                if not child.name.startswith(name_prefix):
                    continue
                if child.name.startswith(".") and not name_prefix.startswith("."):
                    continue
                suffix = separator if child.is_dir() else ""
                candidates.append(f"{base_text}{child.name}{suffix}")
        return candidates

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
        self.settings.command_history = command_history_push(self.settings.command_history, command)
        self._command_history_index = -1
        self._command_draft = ""
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
    # 用户命令
    # ------------------------------------------------------------------ #
    def _command_context(self) -> CommandContext:
        """收集占位符展开所需的当前面板与选中项。"""
        panel = self.active_panel()
        entries = panel.selected_entries()
        return CommandContext(
            active_dir=panel.state.path,
            other_dir=self.other_panel().state.path,
            selected=tuple(entry.name for entry in entries),
            selected_paths=tuple(str(entry.path) for entry in entries),
        )

    def _populate_user_menu(self) -> None:
        """重建「命令 → 用户命令」子菜单并登记到禁用状态同步列表。"""
        menu = self._user_menu
        menu.delete(0, "end")
        commands = [command for command in self.settings.user_commands if command.in_menu]
        if not commands:
            menu.add_command(label="（未配置用户命令）", state="disabled")
        else:
            for command in commands:
                menu.add_command(label=command.name, command=lambda item=command: self.run_user_command(item))
        self._user_command_menus.append(menu)

    def _rebuild_user_command_entries(self) -> None:
        """命令变更后重建菜单、工具栏与右键菜单三处入口。"""
        self._user_command_menus = []
        self._populate_user_menu()
        self._populate_toolbar()
        self._context_menu = self._build_context_menu()
        self._sync_menu_state()

    def apply_user_commands(self, commands: Sequence[UserCommand]) -> None:
        """保存用户命令并立即刷新入口；非法条目按既有容错口径丢弃。"""
        self.settings.user_commands = normalize_user_commands([item.to_dict() for item in commands])
        save_settings(self.settings, self._settings_path)
        self._rebuild_user_command_entries()

    def open_user_commands(self) -> None:
        """配置 → 用户命令…（无快捷键）。"""
        if self._user_command_dialog is not None and self._user_command_dialog.window.winfo_exists():
            self._user_command_dialog.window.lift()
            self._user_command_dialog.window.focus_set()
            return
        self._user_command_dialog = UserCommandsDialog(self)
        self._user_command_dialog.window.wait_window()
        self._user_command_dialog = None

    def save_theme_choice(self) -> None:
        """配置 → 主题：写入设置，重新打开窗口后生效；非法值回落默认。"""
        chosen = _guitheme.resolve_theme(self._theme_var.get())
        self.settings.theme = chosen.name
        self._theme_var.set(chosen.name)
        save_settings(self.settings, self._settings_path)
        self.set_status(f"主题已保存：{chosen.label}；重新打开窗口后生效")

    def run_user_command(self, command: UserCommand) -> None:
        """展开占位符后在活动面板目录执行用户命令。"""
        if self._busy:
            self.set_status("当前有任务在运行，稍后再执行用户命令")
            return
        try:
            text = expand_placeholders(command.full_command, self._command_context())
        except PlaceholderError as exc:
            self.show_error("用户命令", f"{command.name}\n{exc}")
            return
        if not text.strip():
            self.show_error("用户命令", "命令为空")
            return
        cwd = Path(self.active_panel().state.path)
        if not cwd.is_dir():
            self.show_error("用户命令", f"活动面板目录不存在：{cwd}")
            return
        risky = destructive_command(text)
        if risky and not self._messagebox.askyesno(
            "确认执行删除命令",
            f"命令包含删除类操作（{risky}），执行后可能无法恢复。\n\n工作目录：{cwd}\n命令：{text}\n\n确定执行吗？",
            parent=self.root,
        ):
            return
        self.set_status(f"执行用户命令（{cwd}）：{text}")
        self.start_worker(
            lambda: self._run_shell(text, cwd),
            lambda result: self._command_finished(text, result),
            title=f"用户命令 {command.name}",
        )

    def _sync_user_command_state(self, panel: FilePanel) -> None:
        """忙碌或活动目录不存在时禁用用户命令入口。"""
        enabled = not self._busy and Path(panel.state.path).is_dir()
        for menu in self._user_command_menus:
            end = menu.index("end")
            if end is None:
                continue
            for index in range(int(end) + 1):
                if str(menu.entrycget(index, "label")).startswith("（"):
                    continue
                menu.entryconfigure(index, state="normal" if enabled else "disabled")

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

    def apply_rename_presets(self, presets: Sequence[RenamePreset]) -> None:
        """保存多重命名预设并写回设置文件。"""
        self.settings.rename_presets = list(presets)
        self._store_panel_state()
        save_settings(self.settings, self._settings_path)
        self.set_status(f"多重命名预设已保存：{len(presets)} 个")

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
        self._sync_browse_lock = True
        try:
            self.left.set_path(Path(right_path))
            self.right.set_path(Path(left_path))
        finally:
            self._sync_browse_lock = False
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

    def close_other_tabs(self) -> None:
        """关闭活动面板中除当前与锁定标签外的其他标签页。"""
        self.active_panel().close_other_tabs()
        self._sync_menu_state()

    def toggle_tab_lock(self) -> None:
        """锁定 / 解锁活动面板的当前标签页。"""
        panel = self.active_panel()
        locked = panel.toggle_lock()
        label = tab_label(panel.state.path, panel.state.title) or "（空）"
        self.set_status(f"标签「{label}」已{'锁定' if locked else '解锁'}")
        self._sync_menu_state()

    def rename_tab(self) -> None:
        """重命名活动面板的当前标签页；留空恢复默认目录名。"""
        panel = self.active_panel()
        current = panel.state.title or panel_tab_title(panel.state.path)
        title = self._simpledialog.askstring(
            "重命名标签页",
            "标签名称（留空恢复默认）：",
            initialvalue=current,
            parent=self.root,
        )
        if title is None:
            return
        panel.rename_tab(title)
        self._sync_menu_state()

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

    def toggle_sync_browse(self) -> None:
        """Ctrl+Shift+S：切换同步浏览。"""
        self._sync_var.set(not bool(self._sync_var.get()))
        self._apply_sync_browse()

    def _apply_sync_browse(self) -> None:
        """采用菜单勾选状态；开启后一侧导航时对侧按相同相对动作跟随。"""
        self.settings.sync_browse = bool(self._sync_var.get())
        if self.settings.sync_browse:
            self.set_status("同步浏览已开启：一侧进入子目录或返回上级时对侧跟随")
        else:
            self.set_status("同步浏览已关闭")

    def toggle_branch_view(self) -> None:
        """Ctrl+B：切换当前标签的分支视图（递归平铺文件）。"""
        panel = self.active_panel()
        self._set_branch_view(panel, not panel.state.branch_view)

    def _apply_branch_view(self) -> None:
        """采用菜单勾选状态切换分支视图。"""
        self._set_branch_view(self.active_panel(), bool(self._branch_var.get()))

    def _set_branch_view(self, panel: FilePanel, enabled: bool) -> None:
        """切换指定标签的分支视图并刷新目录（不改变当前路径）。"""
        panel.state.branch_view = enabled
        self._branch_var.set(enabled)
        panel.refresh()
        if enabled:
            self.set_status(f"分支视图已开启：递归列出「{panel.state.path}」下的全部文件")
        else:
            self.set_status("分支视图已关闭")

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

    def open_filter_definition(self) -> None:
        """打开包含 / 排除掩码过滤器对话框。"""
        FilterDialog(self)

    def clear_filter_definition(self) -> None:
        """清除持久化的包含 / 排除掩码过滤器。"""
        self.apply_filter_definition(FilterDefinition())

    def apply_filter_definition(self, definition: FilterDefinition) -> None:
        """保存并应用掩码过滤器，刷新两侧列表。"""
        self.settings.filter_definition = definition
        save_settings(self.settings, self._settings_path)
        self.left.refresh()
        self.right.refresh()
        self._sync_menu_state()
        self.set_status(f"过滤器：{definition.describe()}")

    def clear_filter(self) -> None:
        """Esc：清空当前面板的快速过滤。"""
        self.active_panel().clear_filter()

    def show_selected_size(self) -> None:
        """Space：后台统计选中条目的总大小与文件数。"""
        if self._busy or self._archive_readonly():
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

    def reveal_archive_member(self, archive: Path, member: str) -> None:
        """在活动面板中定位压缩包成员，必要时先进入该归档。"""
        panel = self.active_panel()
        if panel.archive != archive and not panel.open_archive(archive):
            return
        target = PurePosixPath(member)
        parent = str(target.parent)
        panel.archive_prefix = "" if parent == "." else parent
        panel.refresh(select=target.name)
        panel.focus_tree()

    def open_entry(self, entry: FileEntry) -> None:
        """双击 / Enter：目录进入，文件用系统默认程序打开。"""
        panel = self.active_panel()
        if panel.archive is not None:
            if entry.is_dir:
                panel.archive_enter(entry.name)
                return
            target = panel.materialize(entry)
            if target is not None:
                try:
                    open_externally(target)
                except OSError as exc:
                    self.show_error("无法打开", str(exc))
            return
        if entry.is_dir:
            panel.set_path(entry.path)
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

    def open_archive_selected(self) -> None:
        """文件 / 右键菜单：只读浏览选中的压缩包。"""
        panel = self.active_panel()
        if panel.archive is not None:
            self.set_status("已在压缩包内；先返回上级或退出再打开其他压缩包")
            return
        entries = panel.selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("进入压缩包", "请选择一个压缩包文件", parent=self.root)
            return
        panel.open_archive(entries[0].path)

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
        """F3 内置文本查看器（包内文件先解压到临时目录）。"""
        panel = self.active_panel()
        entries = panel.selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("查看", "请选择一个文件", parent=self.root)
            return
        path = panel.preview_path(entries[0])
        if path is None:
            return
        try:
            text = load_preview(path)
        except (OSError, ValueError) as exc:
            self._messagebox.showwarning("无法查看", str(exc), parent=self.root)
            return
        self._show_text_dialog(f"查看 - {entries[0].name}", text)

    def edit_selected(self) -> None:
        """F4 用系统默认程序编辑。"""
        if self._archive_readonly():
            return
        entries = self.active_panel().selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("编辑", "请选择一个文件", parent=self.root)
            return
        try:
            open_externally(entries[0].path)
        except OSError as exc:
            self.show_error("无法编辑", str(exc))

    def open_with_selected(self) -> None:
        """文件 / 右键菜单：选择一个程序打开当前文件。"""
        if self._archive_readonly():
            return
        entries = self.active_panel().selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("打开方式", "请选择一个文件", parent=self.root)
            return
        OpenWithDialog(self, entries[0])

    def launch_with(self, program: str, path: Path) -> bool:
        """用指定程序打开文件；成功后把程序移到最近使用首位。"""
        try:
            command = open_with_command(program, str(path))
        except ValueError as exc:
            self.show_error("打开方式", str(exc))
            return False
        name, target = command
        if any(sep and sep in name for sep in (os.sep, os.altsep)) and not Path(name).is_file():
            self.show_error("打开方式", f"程序不存在：{name}")
            return False
        try:
            subprocess.Popen([name, target], cwd=str(path.parent))
        except OSError as exc:
            self.show_error("无法打开", f"{name}\n{exc}")
            return False
        self.settings.open_with = remember_open_with(self.settings.open_with, name)
        self.set_status(f"用 {Path(name).name} 打开 {path.name}")
        return True

    def show_properties(self) -> None:
        """显示选中条目的属性。"""
        if self._archive_readonly():
            return
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

    def set_attributes(self) -> None:
        """批量修改选中条目的时间戳与（Windows）只读 / 隐藏属性。"""
        if self._archive_readonly():
            return
        entries = self.active_panel().selected_entries()
        if not entries:
            self._messagebox.showinfo("修改属性", "请先选择文件或文件夹", parent=self.root)
            return
        AttributeDialog(self, [entry.path for entry in entries])

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
    def _archive_readonly(self) -> bool:
        """压缩包内为只读浏览：写回磁盘或依赖真实路径的动作统一拦截。"""
        if self.active_panel().archive is None:
            return False
        self.set_status("压缩包内为只读浏览，请先导出到磁盘再修改")
        return True

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
        panel = self.active_panel()
        if panel.archive is not None:
            if move:
                self.set_status("压缩包为只读，F6 按导出处理")
            self._extract_selected(panel)
            return
        paths = self._selected_paths()
        if paths:
            root = Path(panel.state.path) if panel.state.branch_view else None
            self.transfer_paths(paths, move=move, preserve_root=root)

    def _extract_selected(self, panel: FilePanel) -> None:
        """包内 F5 / F6：把选中成员（含子目录）解压到对面面板或指定目录。"""
        archive = panel.archive
        if archive is None:
            return
        entries = panel.selected_entries()
        if not entries:
            self.set_status("请选择要导出的文件")
            return
        members = [
            ArchiveMember(entry.archive_member, entry.size, entry.mtime, entry.is_dir)
            for entry in entries
            if entry.archive_member
        ]
        if not members:
            self.set_status("没有可导出的条目")
            return
        default_destination = self.other_panel().state.path
        destination_text = self._simpledialog.askstring(
            "解压选中",
            f"目标目录（默认对面面板）：\n{default_destination}",
            initialvalue=default_destination,
            parent=self.root,
        )
        if destination_text is None:
            return
        raw = destination_text.strip() or default_destination
        destination = Path(raw).expanduser()
        if not destination.is_absolute():
            destination = Path(panel.state.path) / destination
        if not destination.is_dir():
            self.show_error("无法解压", f"目标目录不存在：{destination}")
            return
        existing = [member for member in members if (destination / member.name).exists()]
        if existing and not self._messagebox.askyesno(
            "目标已存在",
            f"{len(existing)} 个同名条目已存在，继续将覆盖它们。\n\n继续吗？",
            parent=self.root,
        ):
            return
        count = len(members)

        def task() -> int:
            return extract_archive_members(archive, members, destination)

        def done(extracted: int) -> None:
            self.refresh_both()
            self.set_status(f"已导出 {extracted} 个文件到 {destination}")
            self._messagebox.showinfo(
                "导出完成",
                f"{extracted} 个文件 → {destination}",
                parent=self.root,
            )

        self.start_worker(task, done, title=f"导出 {count} 项")

    def transfer_paths(self, paths: Sequence[Path], *, move: bool, preserve_root: Path | None = None) -> bool:
        """通用复制 / 移动入口：规划后加入传输队列，返回是否已入队。

        ``preserve_root`` 非空时（分支视图）按文件相对该目录的子目录结构，
        在目标下逐级创建同名子目录，而不是全部平铺到目标根。
        """
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
            items, skipped = self._plan_transfer_groups(paths, destination, preserve_root)
        except (OSError, ValueError) as exc:
            self.show_error(f"无法{action}", str(exc))
            return False
        if not items:
            self.set_status("没有需要操作的条目")
            return False
        return self.enqueue_transfer(items, destination, move=move, skipped=skipped)

    def _plan_transfer_groups(
        self,
        paths: Sequence[Path],
        destination: Path,
        preserve_root: Path | None,
    ) -> tuple[list[TransferItem], list[Path]]:
        """按目标目录分组规划；分支视图下保留相对子目录结构。"""
        if preserve_root is None:
            return plan_transfer(paths, destination, conflict="ask", chooser=self._ask_conflict)
        grouped: dict[str, list[Path]] = {}
        for path in paths:
            relative = relative_parent_dir(preserve_root, path)
            grouped.setdefault("" if relative is None else relative, []).append(path)
        items: list[TransferItem] = []
        skipped: list[Path] = []
        for relative, group in grouped.items():
            target_root = destination if not relative else destination / relative
            if relative:
                target_root.mkdir(parents=True, exist_ok=True)
            planned, group_skipped = plan_transfer(
                group,
                target_root,
                conflict="ask",
                chooser=self._ask_conflict,
            )
            items.extend(planned)
            skipped.extend(group_skipped)
        return items, skipped

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

    def compare_text_files(self) -> None:
        """对选中的两个文本文件做并排只读比较。"""
        entries = self.active_panel().selected_entries()
        if len(entries) != 2 or any(entry.is_dir or entry.archive_member for entry in entries):
            self._messagebox.showinfo("文本比较", "请在同一面板选择两个文本文件", parent=self.root)
            return
        try:
            TextDiffDialog(self, entries[0].path, entries[1].path)
        except (OSError, ValueError) as exc:
            self.show_error("文本比较", str(exc))

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
        """按并行上限执行队首任务；已有后台任务或正在退出时不启动。"""
        if self._busy or self._queue_stopping:
            return
        task = self.transfer_queue.pending_task()
        if task is None:
            return
        self.transfer_queue.begin(task)
        self._refresh_queue_dialog()
        total = task.total
        parallel = clamp_parallel(self.settings.queue_parallel)
        progress_lock = threading.Lock()
        completed = 0

        def process(item: QueueItem) -> None:
            nonlocal completed
            while self._queue_gate.paused and not self._queue_stopping:
                self._queue_gate.wait(0.2)
            if task.cancel_requested or self._queue_stopping:
                return
            self.transfer_queue.begin_item(item)
            self._queue.put(("queue",))
            with progress_lock:
                done_before = completed
            self._progress_callback(done_before, total, item.source.name)
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
            with progress_lock:
                completed += 1
                done_after = completed
            self._progress_callback(done_after, total, item.source.name)

        def work() -> str:
            parallel_map(task.items, process, parallel=parallel)
            if task.cancel_requested or self._queue_stopping:
                self.transfer_queue.cancel_remaining(task)
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

    @property
    def queue_paused(self) -> bool:
        """当前队列是否处于暂停状态。"""
        return self._queue_gate.paused

    def toggle_queue_pause(self) -> None:
        """暂停 / 恢复队列：暂停后不再启动新文件，已在传输的继续。"""
        if self._queue_gate.paused:
            self._queue_gate.resume()
            message = "队列已恢复"
        else:
            self._queue_gate.pause()
            message = "队列已暂停（当前文件传完后不再启动新文件）"
        self.set_status(message)
        self._refresh_queue_dialog()

    def set_queue_parallel(self, value: int) -> None:
        """调整队列并行上限并持久化，下一个任务起生效。"""
        parallel = clamp_parallel(value)
        if parallel == self.settings.queue_parallel:
            return
        self.settings.queue_parallel = parallel
        save_settings(self.settings, self._settings_path)
        self.set_status(f"队列并行数：{parallel}，下一个任务起生效")

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
        """F8 移入系统回收站；可写归档内则删除选中成员。"""
        panel = self.active_panel()
        if panel.archive is not None:
            self._remove_archive_members()
            return
        if self._archive_readonly():
            return
        paths = self._selected_paths()
        if paths:
            self.delete_paths(paths)

    def permanent_delete_selected(self) -> None:
        """Shift+F8 永久删除选中条目；可写归档内语义相同。"""
        panel = self.active_panel()
        if panel.archive is not None:
            self._remove_archive_members()
            return
        if self._archive_readonly():
            return
        paths = self._selected_paths()
        if paths:
            self.delete_paths(paths, permanent=True)

    def _archive_write_blocked(self) -> bool:
        """包内写入前置检查：不可写格式给出提示并返回 ``True``。"""
        panel = self.active_panel()
        if panel.archive is None or archive_editable(panel.archive):
            return False
        self.set_status("该压缩包格式为只读（仅 zip 与 tar 家族支持写入）")
        return True

    def _remove_archive_members(self) -> None:
        """从可写归档删除选中成员，复用现有确认与后台任务框架。"""
        panel = self.active_panel()
        archive = panel.archive
        if archive is None or self._archive_write_blocked():
            return
        if self._busy:
            self._messagebox.showinfo("请稍候", "已有后台任务在运行", parent=self.root)
            return
        entries = panel.selected_entries()
        names = [entry.archive_member for entry in entries if entry.archive_member]
        if not names:
            self._messagebox.showinfo("删除压缩包成员", "请选择要删除的成员", parent=self.root)
            return
        confirmed = self._messagebox.askyesno(
            "删除压缩包成员",
            f"确定从 {archive.name} 删除选中的 {len(names)} 项吗？\n目录会连同其内容一起删除。",
            parent=self.root,
        )
        if not confirmed:
            return
        options = self.archive_options

        def task() -> int:
            return remove_members(archive, names, options=options)

        def done(count: int) -> None:
            panel.reload_archive()
            self.set_status(f"已从 {archive.name} 删除 {count} 个成员")

        self.start_worker(task, done, title=f"删除压缩包成员 {len(names)} 项")

    def add_to_archive(self) -> None:
        """把对面面板选中的文件 / 目录加入当前可写归档。"""
        panel = self.active_panel()
        archive = panel.archive
        if archive is None:
            self.set_status("请先进入一个压缩包，再添加文件")
            return
        if self._archive_write_blocked() or self._busy:
            return
        other = self.other_panel()
        if other.archive is not None:
            self._messagebox.showinfo(
                "加入压缩包",
                "请先在对面面板退出压缩包，再选择要加入的文件或目录",
                parent=self.root,
            )
            return
        sources = [entry.path for entry in other.selected_entries()]
        if not sources:
            self._messagebox.showinfo("加入压缩包", "请在对面面板选择要加入的文件或目录", parent=self.root)
            return
        prefix = panel.archive_prefix
        options = self.archive_options
        try:
            additions = planned_additions(sources, prefix)
            existing = set(archive_member_names(archive, password=options.password))
        except (OSError, ValueError) as exc:
            self.show_error("无法加入压缩包", str(exc))
            return
        conflicts = sorted(name for name in additions if name in existing)
        replace = False
        if conflicts:
            details = "\n".join(conflicts[:_MAX_ERROR_DETAILS])
            confirmed = self._messagebox.askyesno(
                "成员已存在",
                f"以下 {len(conflicts)} 个成员已存在，覆盖吗？\n\n{details}",
                parent=self.root,
            )
            if not confirmed:
                return
            replace = True

        def task() -> int:
            return add_paths(archive, sources, prefix, options=options, replace=replace)

        def done(count: int) -> None:
            panel.reload_archive()
            self.set_status(f"已加入 {count} 个文件到 {archive.name}")

        self.start_worker(task, done, title=f"加入 {len(sources)} 项到 {archive.name}")

    def show_archive_options(self) -> None:
        """打开归档写入选项（压缩级别 / 密码）。"""
        if self._busy:
            self._messagebox.showinfo("请稍候", "已有后台任务在运行", parent=self.root)
            return
        ArchiveOptionsDialog(self)

    def split_selected(self) -> None:
        """文件菜单「分割文件…」：选择单个文件后打开分割选项。"""
        if self._busy or self._archive_readonly():
            return
        entries = self.active_panel().selected_entries()
        if len(entries) != 1 or entries[0].is_dir:
            self._messagebox.showinfo("分割文件", "请选择一个文件", parent=self.root)
            return
        SplitDialog(self, entries[0])

    def start_split(self, path: Path, *, part_size: int | None = None, parts: int | None = None) -> None:
        """确认分卷覆盖后后台分割；校验文件随分卷一起生成。"""
        try:
            plan = split_plan(path.stat().st_size, part_size=part_size, parts=parts)
        except (OSError, ValueError) as exc:
            self.show_error("无法分割", str(exc))
            return
        names = part_names(path.name, len(plan))
        existing = [name for name in names if (path.parent / name).exists()]
        if existing and not self._messagebox.askyesno(
            "分卷已存在",
            f"以下 {len(existing)} 个分卷已存在，覆盖吗？\n\n" + "\n".join(existing[:_MAX_ERROR_DETAILS]),
            parent=self.root,
        ):
            return
        panel = self.active_panel()

        def task() -> SplitResult:
            return split_file(
                path,
                part_size=part_size,
                parts=parts,
                overwrite=bool(existing),
                progress=self._progress_callback,
            )

        def done(result: SplitResult) -> None:
            panel.refresh(select=result.parts[0].name)
            self.set_status(f"已分割为 {len(result.parts)} 卷：{path.name}")

        self.start_worker(task, done, title=f"分割 {path.name} 为 {len(plan)} 卷")

    def merge_selected(self) -> None:
        """文件菜单「合并分卷…」：单卷自动发现同组，多选按所选合并。"""
        if self._busy or self._archive_readonly():
            return
        panel = self.active_panel()
        entries = [
            entry for entry in panel.selected_entries() if not entry.is_dir and not entry.name.endswith(CHECKSUM_SUFFIX)
        ]
        if not entries:
            self._messagebox.showinfo("合并分卷", "请选择一个分卷文件（或多选同一组的分卷）", parent=self.root)
            return
        try:
            parts = discover_parts(entries[0].path) if len(entries) == 1 else [entry.path for entry in entries]
            base = default_merged_name(parts[0])
        except (OSError, ValueError) as exc:
            self.show_error("无法合并", str(exc))
            return
        output = parts[0].parent / base
        if output.exists() and not self._messagebox.askyesno(
            "覆盖文件",
            f"{output.name} 已存在，合并结果将覆盖它，继续吗？",
            parent=self.root,
        ):
            return

        def task() -> MergeResult:
            return merge_parts(parts, output, progress=self._progress_callback)

        def done(result: MergeResult) -> None:
            panel.refresh(select=result.output.name)
            suffix = "（已按校验文件核对）" if result.verified else "（未找到校验文件，仅按顺序拼接）"
            self.set_status(f"已合并 {len(parts)} 卷为 {output.name}{suffix}")
            self._messagebox.showinfo("合并完成", f"{output}\n{suffix}", parent=self.root)

        self.start_worker(task, done, title=f"合并 {len(parts)} 卷")

    def find_duplicates(self) -> None:
        """文件菜单「查找重复文件…」：选中目录优先，否则扫描当前目录。"""
        if self._busy or self._archive_readonly():
            return
        panel = self.active_panel()
        entries = panel.selected_entries()
        roots = [entry.path for entry in entries if entry.is_dir] or [Path(panel.state.path)]

        def task() -> DuplicateReport:
            return find_duplicate_files(roots, progress=self._progress_callback)

        def done(report: DuplicateReport) -> None:
            self.set_status(f"重复文件：{len(report.groups)} 组，可回收 {report.wasted} 字节")
            if not report.groups:
                self._messagebox.showinfo("重复文件", "没有发现重复文件", parent=self.root)
                return
            DuplicateDialog(self, report)

        self.start_worker(task, done, title="查找重复文件")

    def move_duplicate_files(self, paths: list[Path], destination: Path) -> None:
        """把重复副本移动到指定目录，完成后刷新两个面板。"""

        def task() -> DuplicateActionReport:
            return move_duplicates(paths, destination, progress=self._progress_callback)

        def done(result: DuplicateActionReport) -> None:
            self.refresh_both()
            message = f"已移动 {len(result.completed)} 个文件到 {destination}"
            if result.errors:
                details = "\n".join(f"{path}: {error}" for path, error in result.errors[:_MAX_ERROR_DETAILS])
                self.show_error("部分文件移动失败", f"{message}\n\n{details}")
            else:
                self.set_status(message)

        self.start_worker(task, done, title=f"移动 {len(paths)} 个重复文件")

    def _rename_archive_member(self, panel: FilePanel) -> None:
        """重命名可写归档内的单个成员；目录按前缀整棵改名。"""
        archive = panel.archive
        if archive is None or self._archive_write_blocked():
            return
        if self._busy:
            self._messagebox.showinfo("请稍候", "已有后台任务在运行", parent=self.root)
            return
        entries = panel.selected_entries()
        if len(entries) != 1:
            self._messagebox.showinfo("重命名", "请选择一个成员", parent=self.root)
            return
        entry = entries[0]
        if not entry.archive_member:
            return
        name = self._simpledialog.askstring(
            "重命名",
            f"{entry.name} 重命名为：",
            initialvalue=entry.name,
            parent=self.root,
        )
        if not name or name == entry.name:
            return
        if "/" in name or "\\" in name:
            self.show_error("无法重命名", "名称不能包含路径分隔符")
            return
        new_member = f"{panel.archive_prefix}/{name}" if panel.archive_prefix else name
        options = self.archive_options

        def task() -> str:
            return rename_member(archive, entry.archive_member or "", new_member, options=options)

        def done(result: str) -> None:
            panel.reload_archive()
            self.set_status(f"已重命名为 {result.rsplit('/', 1)[-1]}")

        self.start_worker(task, done, title=f"重命名 {entry.name}")

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
        if self._busy or self._archive_readonly():
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
        panel = self.active_panel()
        if panel.archive is not None:
            self._rename_archive_member(panel)
            return
        if self._busy or self._archive_readonly():
            return
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
        """Ctrl+M 打开多重命名工具（支持分支视图下的相对路径）。"""
        if self._busy or self._archive_readonly():
            return
        panel = self.active_panel()
        entries = panel.selected_entries()
        if not entries:
            self._messagebox.showinfo("多重命名工具", "请先选择文件或文件夹", parent=self.root)
            return
        MultiRenameDialog(self, panel, entries)

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
        if self._busy or self._archive_readonly():
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

        options = self.archive_options
        use_options = archive_editable(target) and (
            bool(options.password) or options.level != DEFAULT_COMPRESSION_LEVEL
        )
        if options.password and not archive_editable(target):
            self.show_error("无法加密", "密码仅支持 zip 与 tar 家族归档")
            return

        def task() -> Path:
            if use_options:
                create_archive_file(target, paths, options=options)
                return target
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
        if self._busy or self._archive_readonly():
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

    def generate_checksum_file(self) -> None:
        """为选中文件生成校验文件（写入活动面板目录，GNU/SFV 兼容格式）。"""
        if self._busy or self._archive_readonly():
            return
        paths = [path for path in self._selected_paths() if path.is_file()]
        if not paths:
            self._messagebox.showinfo("生成校验文件", "请选择要计算校验值的文件", parent=self.root)
            return
        raw = self._simpledialog.askstring(
            "生成校验文件",
            "算法（sha256/sha1/md5/sfv）：",
            initialvalue="sha256",
            parent=self.root,
        )
        if not raw:
            return
        try:
            algorithm = normalize_algorithm(raw)
        except ValueError as exc:
            self.show_error("算法无效", str(exc))
            return
        base = Path(self.active_panel().state.path)
        target = base / manifest_name(base, algorithm)

        def task() -> tuple[Path, int]:
            text = build_manifest(
                paths,
                base,
                algorithm,
                progress=lambda done, total: self._progress_callback(done, total, "校验文件"),
            )
            target.write_text(text, encoding="utf-8", newline="\n")
            return target, len(paths)

        def done(result: tuple[Path, int]) -> None:
            path, count = result
            self.refresh_both()
            self.set_status(f"已生成 {path.name}（{count} 个文件）")
            self._show_text_dialog(
                "校验文件已生成",
                f"文件：{path}\n算法：{algorithm}\n条目：{count}\n\n{path.read_text(encoding='utf-8')}",
            )

        self.start_worker(task, done, title=f"生成校验文件（{algorithm}）")

    def verify_checksum_file(self) -> None:
        """对选中的校验文件逐项比对，结果窗口列出失败项。"""
        if self._busy or self._archive_readonly():
            return
        targets = [path for path in self._selected_paths() if path.is_file() and algorithm_for_suffix(path.suffix)]
        if not targets:
            self._messagebox.showinfo(
                "校验文件",
                "请选择 .sha256 / .sha1 / .md5 / .sfv 校验文件",
                parent=self.root,
            )
            return

        def task() -> list[tuple[Path, VerifyReport]]:
            reports: list[tuple[Path, VerifyReport]] = []
            for path in targets:
                algorithm = algorithm_for_suffix(path.suffix)
                if algorithm is None:  # pragma: no cover - 入口已按后缀过滤
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                reports.append((path, verify_manifest(text, path.parent, algorithm)))
            return reports

        def done(reports: list[tuple[Path, VerifyReport]]) -> None:
            failures = sum(len(report.failures) for _, report in reports)
            body = "\n\n".join(f"{path.name}\n{report_text(report)}" for path, report in reports)
            self.set_status(f"校验完成：{len(reports)} 个清单，{failures} 项失败")
            self._show_text_dialog(f"校验结果（失败 {failures} 项）", body)

        self.start_worker(task, done, title="校验文件")

    def open_search(self) -> None:
        """Ctrl+F 打开搜索对话框；归档视图内搜索成员名与内容。"""
        panel = self.active_panel()
        if panel.archive is not None:
            SearchDialog(self, panel.archive, archive=True)
            return
        SearchDialog(self, Path(panel.state.path))

    def start_archive_search(  # noqa: PLR0913 - 归档搜索需要透传模式、开关与回调
        self,
        archive: Path,
        pattern: str,
        *,
        content: bool,
        case_sensitive: bool,
        whole_word: bool,
        on_done: Callable[[ArchiveSearchReport], None],
        on_error: Callable[[str], None],
    ) -> None:
        """供搜索对话框调用的归档内后台搜索入口。"""

        def task() -> ArchiveSearchReport:
            return search_archive(
                archive,
                pattern,
                content=content,
                case_sensitive=case_sensitive,
                whole_word=whole_word,
            )

        def done(report: ArchiveSearchReport) -> None:
            self.set_status(f"归档搜索完成：{len(report.hits)} 个结果")
            on_done(report)

        self.start_worker(task, done, title="归档搜索", on_error=on_error)

    def start_search(  # noqa: PLR0913 - 搜索对话框需要透传目录、模式、内容开关与回调
        self,
        directory: Path,
        pattern: str,
        *,
        content: bool,
        extension: str,
        case_sensitive: bool | None = None,
        whole_word: bool = False,
        filters: SearchFilters | None = None,
        on_done: Callable[[list[SearchHit]], None],
        on_error: Callable[[str], None],
    ) -> None:
        """供搜索对话框调用的后台搜索入口。"""

        def task() -> list[SearchHit]:
            return search_paths(
                directory,
                pattern,
                content=content,
                extension=extension,
                case_sensitive=case_sensitive,
                whole_word=whole_word,
                filters=filters,
            )

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
            "Ctrl+M 多重命名工具    Alt+Enter 属性\n"
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
        self.left._capture_selection()
        self.right._capture_selection()
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
        self.left._clear_archive_state()
        self.right._clear_archive_state()
        self.left.shutdown_thumbnails()
        self.right.shutdown_thumbnails()
        self._store_panel_state()
        self.settings.geometry = self.root.winfo_geometry()
        save_settings(self.settings, self._settings_path)
        self.root.destroy()


def run_gui(settings_path: Path | None = None, launch: LaunchRequest | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: CmderWindow(settings_path, launch), "双面板文件管理器")


@fcmd.tool("cmderw", help="打开双面板文件管理器窗口（Tk）", description=_DESCRIPTION)
def launch_cmd(left: str | None = None, right: str | None = None, select: str | None = None) -> None:
    """打开双面板文件管理器窗口；``--left`` / ``--right`` 指定两侧目录，``--select`` 定位并选中路径。"""
    run_gui(launch=LaunchRequest(left=left, right=right, select=select))


@fcmd.main("cmderw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
