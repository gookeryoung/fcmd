"""cmderw - 双面板文件管理器（Qt GUI，Total Commander 风格）。

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
:mod:`fcmd.infras.hashing`，文件名/内容搜索走 :mod:`fcmd.infras.fs`。
窗口实现在 :mod:`fcmd.apps.cmder._cmder_qt` 与 ``_cmder_qt_panels`` / ``_cmder_qt_dialogs``；
本模块保留纯逻辑（设置、扫描、排序、历史、传输计划、归档成员、预览与格式化）。

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
import time
import zipfile
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from stat import S_ISDIR
from typing import TYPE_CHECKING, Any, cast

import fcmd
from fcmd.apps.cmder._commands import (
    UserCommand,
    normalize_user_commands,
    user_commands_to_json,
)
from fcmd.apps.cmder._launch import LaunchRequest
from fcmd.apps.cmder._ops import (
    COLUMN_SORT,
    DEFAULT_VIEW_MODE,
    THUMBNAIL_TILE_SIZE,
    ColorRule,
    TrashReport,
    branch_relative_path,
    directory_size,
    dump_color_rules,
    filter_names,
    invert_names,
    match_names,
    normalize_column_widths,
    normalize_command_history,
    normalize_open_with_list,
    normalize_view_mode,
    parse_color_rules,
    validate_name,
)
from fcmd.apps.cmder._queue import (
    DEFAULT_QUEUE_PARALLEL,
    clamp_parallel,
)
from fcmd.apps.cmder._rename import (
    RenamePreset,
    normalize_rename_presets,
    rename_presets_to_json,
)
from fcmd.apps.cmder._search import (
    SearchFilters,
    filter_paths,
)
from fcmd.apps.cmder._tabs import (
    FilterDefinition,
    apply_filter_definition,
    normalize_filter_definition,
    normalize_selection,
    normalize_tab_title,
)
from fcmd.apps.cmder._volumes import format_bytes as _format_bytes
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _geometry
from fcmd.gui import theme as _guitheme

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = [
    "ARCHIVE_SUFFIXES",
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
    "PanelScanChannel",
    "PanelScanRequest",
    "PanelScanResult",
    "PanelState",
    "SearchHit",
    "TransferItem",
    "TrashReport",
    "archive_browsable",
    "archive_directory_entries",
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
    "filter_entries",
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
    "list_branch",
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
    "run_panel_scan",
    "save_settings",
    "scan_panel_entries",
    "search_paths",
    "sort_entries",
    "stripe_tag",
    "transfer_entries",
    "unique_path",
    "validate_name",
]

_DESCRIPTION = (
    "双面板文件管理器窗口版（Qt，Total Commander 风格）：左右面板独立导航与历史，"
    "F5 复制/F6 移动/F7 新建/F8 删除到回收站/Shift+F8 永久删除/F2 重命名/F3 查看，"
    "Num+/Num- 按掩码选择，Ctrl+S 快速过滤，Ctrl+M 多重命名工具，Space 统计大小，"
    "每面板多标签页（Ctrl+T/Ctrl+W/Ctrl+Tab），Ctrl+D 书签，Alt+Down 历史跳转，"
    "复制/移动进入串行传输队列（逐项进度、按项取消、失败重试），"
    "Ctrl+Shift+C 只读比较与预演式目录同步，"
    "Ctrl+Q 快速查看、Ctrl+F8 目录树、Ctrl+Enter 命令行、配置→颜色规则，"
    "Alt+F5 打包、Alt+F9 解压，搜索结果可直接复制/移动/删除/加入选择，"
    "复用 archivex/hashing/filesearch 能力，设置持久化到 ~/.fcmd/ui/cmderw.json"
)

DEFAULT_GEOMETRY = "1240x760+60+40"
MIN_WIDTH = 960
MIN_HEIGHT = 600
MAX_HISTORY = 50
DEFAULT_SORT_KEY = "name"
SORT_KEYS: tuple[str, ...] = ("name", "size", "mtime", "type")

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
#: 分支视图：递归深度与条目上限，避免误入超深目录树时卡死界面。
BRANCH_MAX_DEPTH = 32
BRANCH_MAX_ENTRIES = 20000
#: 缩略图：单文件读取上限与目标尺寸（后台解码由 Qt 面板调用）。
_THUMB_READ_LIMIT = 8 * 1024 * 1024
_THUMB_TILE = THUMBNAIL_TILE_SIZE


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
    return _geometry.resolve_geometry(
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


def _entry_from_dir_entry(child: os.DirEntry[str]) -> FileEntry:
    """把 ``os.scandir`` 条目转成 :class:`FileEntry`，只做必要的元数据查询。

    ``DirEntry`` 在 Windows 上已缓存 ``FindFirstFile`` 的结果，读取类型、大小、
    修改时间不会重复触发系统调用；旧实现用 ``Path.stat()`` + ``is_dir()`` +
    ``is_symlink()``，等于对每个条目做三次独立查询。

    语义与旧实现保持一致：符号链接跟随到目标判断类型、取目标大小与时间
    （断链回退为 0），``is_symlink`` 只看链接本身，隐藏属性取目标属性。
    """
    is_symlink = child.is_symlink()
    stat: os.stat_result | None = None
    with contextlib.suppress(OSError):
        # 普通条目用不跟随的查询（POSIX 上可命中 lstat 缓存）；符号链接
        # 跟随目标，保持与 ``Path.stat()`` 一致的大小/时间/隐藏属性。
        stat = child.stat() if is_symlink else child.stat(follow_symlinks=False)
    is_dir = stat is not None and S_ISDIR(stat.st_mode)
    return FileEntry(
        name=child.name,
        path=Path(child.path),
        is_dir=is_dir,
        is_symlink=is_symlink,
        size=0 if is_dir else (stat.st_size if stat is not None else 0),
        mtime=stat.st_mtime if stat is not None else 0.0,
        hidden=is_hidden(child.name, stat),
    )


def list_directory(directory: Path, *, show_hidden: bool = False) -> list[FileEntry]:
    """读取目录内容（不递归），返回按默认规则排序的条目列表。"""
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    entries: list[FileEntry] = []
    with os.scandir(directory) as iterator:
        for child in iterator:
            entry = _entry_from_dir_entry(child)
            if entry.hidden and not show_hidden:
                continue
            entries.append(entry)
    return sort_entries(entries)


def list_branch(
    directory: Path,
    *,
    show_hidden: bool = False,
    max_depth: int = BRANCH_MAX_DEPTH,
    max_entries: int = BRANCH_MAX_ENTRIES,
) -> tuple[list[FileEntry], bool]:
    """分支视图扫描：BFS 递归列出所有文件，返回 ``(条目, 是否截断)``。

    隐藏项整体跳过；符号链接目录不递归；超过深度或条目上限时返回截断标记。
    队列使用 :class:`collections.deque`，避免 ``list.pop(0)`` 的 O(n²) 搬移。
    """
    if not directory.is_dir():
        raise NotADirectoryError(f"不是目录: {directory}")
    entries: list[FileEntry] = []
    pending: deque[tuple[Path, int]] = deque([(directory, 0)])
    truncated = False
    while pending:
        if len(entries) >= max_entries:
            truncated = True
            break
        current, depth = pending.popleft()
        try:
            with os.scandir(current) as iterator:
                children = sorted(iterator, key=lambda child: child.name)
                for child in children:
                    entry = _entry_from_dir_entry(child)
                    if entry.hidden and not show_hidden:
                        continue
                    if entry.is_dir:
                        if not entry.is_symlink and depth < max_depth:
                            pending.append((entry.path, depth + 1))
                        continue
                    entries.append(replace(entry, relative=branch_relative_path(directory, entry.path)))
        except OSError:
            continue
    return entries, truncated or bool(pending)


def archive_directory_entries(
    archive: Path,
    members: Sequence[ArchiveMember],
    prefix: str = "",
) -> list[FileEntry]:
    """把归档成员折算为当前包内目录的直接子项（归档只读浏览的数据源）。"""
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


def filter_entries(
    entries: Sequence[FileEntry],
    filter_text: str = "",
    definition: FilterDefinition | None = None,
) -> list[FileEntry]:
    """按快速过滤文本与过滤定义筛出可见条目，保持原顺序。"""
    labels = [entry.label for entry in entries]
    if filter_text:
        labels = filter_names(labels, filter_text)
    if definition is not None:
        labels = apply_filter_definition(labels, definition)
    visible = set(labels)
    return [entry for entry in entries if entry.label in visible]


def scan_panel_entries(  # noqa: PLR0913 - 数据源开关由调用方逐个透传
    directory: Path,
    *,
    archive: Path | None = None,
    archive_members: Sequence[ArchiveMember] = (),
    archive_prefix: str = "",
    branch_view: bool = False,
    show_hidden: bool = False,
    sort_key: str = DEFAULT_SORT_KEY,
    descending: bool = False,
) -> tuple[list[FileEntry], bool]:
    """面板数据源统一入口：归档 / 分支视图 / 普通目录，返回 ``(条目, 是否截断)``。"""
    if archive is not None:
        entries = archive_directory_entries(archive, archive_members, archive_prefix)
        truncated = False
    elif branch_view:
        entries, truncated = list_branch(directory, show_hidden=show_hidden)
    else:
        entries = list_directory(directory, show_hidden=show_hidden)
        truncated = False
    return sort_entries(entries, sort_key=sort_key, descending=descending), truncated


@dataclass(frozen=True)
class PanelScanRequest:
    """一次面板扫描请求：主线程打包参数，后台线程只读这些快照。"""

    generation: int
    directory: Path
    archive: Path | None = None
    archive_members: tuple[ArchiveMember, ...] = ()
    archive_prefix: str = ""
    branch_view: bool = False
    show_hidden: bool = False
    sort_key: str = DEFAULT_SORT_KEY
    descending: bool = False
    filter_text: str = ""
    filter_definition: FilterDefinition | None = None
    previous: tuple[str, ...] = ()


@dataclass(frozen=True)
class PanelScanResult:
    """后台扫描结果；``error`` 非空时其余字段无意义。"""

    generation: int
    entries: list[FileEntry] = field(default_factory=list)
    truncated: bool = False
    previous: tuple[str, ...] = ()
    error: OSError | None = None


def run_panel_scan(request: PanelScanRequest) -> PanelScanResult:
    """执行一次面板扫描（线程内调用，不触碰 Tk），异常折算为结果里的 ``error``。"""
    try:
        entries, truncated = scan_panel_entries(
            request.directory,
            archive=request.archive,
            archive_members=request.archive_members,
            archive_prefix=request.archive_prefix,
            branch_view=request.branch_view,
            show_hidden=request.show_hidden,
            sort_key=request.sort_key,
            descending=request.descending,
        )
        entries = filter_entries(entries, request.filter_text, request.filter_definition)
    except OSError as exc:
        return PanelScanResult(generation=request.generation, previous=request.previous, error=exc)
    return PanelScanResult(
        generation=request.generation,
        entries=entries,
        truncated=truncated,
        previous=request.previous,
    )


class PanelScanChannel:
    """扫描结果通道：世代号 + 线程安全队列，供后台扫描与主线程轮询协作。

    每次开始新扫描递增世代；轮询只返回当前世代的结果，旧世代直接丢弃，
    连续切换目录时过期的扫描结果因此不会覆盖新目录。
    """

    __slots__ = ("_generation", "_results")

    def __init__(self) -> None:
        self._generation = 0
        self._results: queue.Queue[PanelScanResult] = queue.Queue()

    @property
    def generation(self) -> int:
        return self._generation

    def begin(self) -> int:
        """开始一次新扫描：递增世代号并返回，旧世代结果随即失效。"""
        self._generation += 1
        return self._generation

    def put(self, result: PanelScanResult) -> None:
        """后台线程投递结果（线程安全）。"""
        self._results.put(result)

    def drain(self) -> list[PanelScanResult]:
        """主线程取出当前世代的结果，丢弃过期结果。"""
        accepted: list[PanelScanResult] = []
        while True:
            try:
                result = self._results.get_nowait()
            except queue.Empty:
                return accepted
            if result.generation == self._generation:
                accepted.append(result)

    def empty(self) -> bool:
        """队列是否已空（含未取出的过期结果）。"""
        return self._results.empty()


def format_size(size: int, *, is_dir: bool = False) -> str:
    """人类可读的大小文本；目录显示 ``<DIR>``（与卷容量格式化同一口径）。"""
    if is_dir:
        return "<DIR>"
    return _format_bytes(size)


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


def stripe_tag(index: int) -> str:
    """斑马纹行 tag：奇数行返回 ``"stripe"``，偶数行返回空串（用默认背景）。"""
    return "stripe" if index % 2 else ""


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
    from fcmd.infras.filefilter import DEFAULT_IGNORE_EXTS, DEFAULT_IGNORE_SPEC, should_skip_parts

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
# 哈希 / 搜索 / 预览（复用 infras.hashing、infras.fs、textio）
# ============================================================================ #
def hash_paths(
    paths: Sequence[Path],
    algorithm: str = "sha256",
    *,
    progress: Callable[[int, int, str], None] | None = None,
) -> list[HashResult]:
    """计算文件或目录下所有文件的哈希（目录复用 infras.hashing 遍历规则）。"""
    from fcmd.infras import hashing

    hashing.ensure_algorithm(algorithm)
    targets: list[Path] = []
    for path in paths:
        if path.is_dir():
            targets.extend(hashing.iter_directory_files(path))
        elif path.is_file():
            targets.append(path)
    results: list[HashResult] = []
    total = len(targets)
    for index, target in enumerate(targets, start=1):
        results.append(HashResult(path=target, digest=hashing.compute_file_hash(target, algorithm)))
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
    from fcmd.infras.fs import search_by_content, search_by_name

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
    from fcmd.infras.textio import read_text_lines

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


def run_gui(settings_path: Path | None = None, launch: LaunchRequest | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.apps.cmder._cmder_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path, launch)


@fcmd.tool("cmderw", help="打开双面板文件管理器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd(left: str | None = None, right: str | None = None, select: str | None = None) -> None:
    """打开双面板文件管理器，可选指定左右面板目录与初始选中文件。"""
    run_gui(launch=LaunchRequest(left=left, right=right, select=select))


@fcmd.main("cmderw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
