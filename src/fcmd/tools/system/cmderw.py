"""cmderw - 双面板文件管理器（Tk GUI，Total Commander 风格）。

左右两个独立目录面板，支持键盘与鼠标操作：Tab 切换面板，Enter 进入目录，
F2 重命名、F3 查看、F4 外部编辑、F5 复制、F6 移动、F7 新建目录、F8 删除到
回收站、Shift+F8 永久删除，Alt+F5 打包、Alt+F9 解压，Ctrl+F 搜索，Ctrl+S
快速过滤，Num+/Num- 按掩码选择/取消，Space 统计选中项大小，Ctrl+H 显示隐藏文件。

能力复用现有工具：打包/解压走 :mod:`fcmd.tools.archive.archivex`，文件哈希走
:mod:`fcmd.tools.hashbase`，文件名/内容搜索走 :mod:`fcmd.dsl.actions.fileops`，
窗口骨架、主题与设置持久化走 :mod:`fcmd.gui`。

设置（左右面板路径与历史、排序列、隐藏文件开关、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/cmderw.json``，下次启动自动恢复。

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
    BulkRenameOptions,
    RenameItem,
    RenamePlan,
    RenameReport,
    RenameSource,
    TrashReport,
    apply_rename_plan,
    directory_size,
    filter_names,
    invert_names,
    match_names,
    plan_bulk_rename,
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
    "open_externally",
    "pack_entries",
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
    """双面板文件管理器的持久化设置。"""

    left: PanelState = field(default_factory=PanelState)
    right: PanelState = field(default_factory=PanelState)
    active: str = "left"
    show_hidden: bool = False
    geometry: str | None = None


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


def load_settings(path: Path | None = None) -> CmderSettings:
    """读取设置；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    fallback = default_directory()
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return CmderSettings(
            left=PanelState(path=str(fallback)),
            right=PanelState(path=str(fallback)),
        )

    settings = CmderSettings(
        left=_panel_from_raw(raw.get("left"), fallback),
        right=_panel_from_raw(raw.get("right"), fallback),
    )
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
    payload = {
        "left": settings.left.to_dict(),
        "right": settings.right.to_dict(),
        "active": settings.active,
        "show_hidden": settings.show_hidden,
        "geometry": settings.geometry,
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
    """单个目录面板：路径栏 + 文件列表 + 状态行。"""

    def __init__(self, app: CmderWindow, parent: Any, side: str, state: PanelState) -> None:
        import tkinter as tk
        from tkinter import ttk

        self.app = app
        self.side = side
        self.state = state
        self.entries: list[FileEntry] = []
        self._index: dict[str, FileEntry] = {}
        self._filter = ""
        theme = app.theme
        spacing = theme.spacing

        self.frame = ttk.Frame(parent, padding=spacing.xs)
        self.title_label = ttk.Label(self.frame, text="左侧" if side == "left" else "右侧", style="Title.TLabel")
        self.title_label.pack(fill="x", pady=(0, spacing.xxs))

        top = ttk.Frame(self.frame)
        top.pack(fill="x", pady=(0, spacing.xs))
        ttk.Button(top, text="←", width=3, style="Toolbutton", command=self.go_back).pack(side="left")
        ttk.Button(top, text="→", width=3, style="Toolbutton", command=self.go_forward).pack(side="left")
        ttk.Button(top, text="↑", width=3, style="Toolbutton", command=self.go_up).pack(side="left")
        ttk.Button(top, text="刷新", style="Toolbutton", command=self.refresh).pack(side="left", padx=(0, spacing.xs))
        self.path_var = tk.StringVar(value=state.path)
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
    def set_title_active(self, active: bool) -> None:
        """激活面板用标题样式高亮。"""
        self.title_label.configure(style="Title.TLabel" if active else "Muted.TLabel")

    def refresh(self, select: str | None = None) -> None:
        """重新读取当前目录并恢复选择。"""
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
        view_menu.add_command(label="交换面板\tCtrl+U", command=self.swap_panels)
        view_menu.add_command(label="切换面板\tTab", command=self.switch_panel)
        menubar.add_cascade(label="显示", menu=view_menu)

        tools_menu = tk.Menu(menubar, tearoff=False)
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
        self.left = FilePanel(self, self._paned, "left", self.settings.left)
        self.right = FilePanel(self, self._paned, "right", self.settings.right)
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
        """通用复制 / 移动入口：弹目标框、规划并后台执行，返回是否已启动。"""
        if self._busy:
            self._messagebox.showinfo("请稍候", "已有后台任务在运行", parent=self.root)
            return False
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

        def task() -> OperationReport:
            return transfer_entries(items, move=move, progress=self._progress_callback)

        def done(report: OperationReport) -> None:
            self.refresh_both()
            skipped_total = len(skipped) + len(report.skipped)
            summary = f"{action}完成：成功 {len(report.completed)}，跳过 {skipped_total}，失败 {len(report.errors)}"
            self.set_status(summary)
            if report.errors:
                details = "\n".join(f"{source.name}: {error}" for source, error in report.errors[:_MAX_ERROR_DETAILS])
                self._messagebox.showwarning(f"{action}完成（有失败）", f"{summary}\n\n{details}", parent=self.root)

        self.start_worker(task, done, title=f"{action} {len(items)} 项")
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
                return
            elif kind == "error":
                self._finish_worker()
                on_error, self._on_error = self._on_error, None
                self._on_done = None
                self.set_status("操作失败")
                self.show_error("操作失败", str(event[1]))
                if on_error is not None:
                    on_error(str(event[1]))
                return
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
        """保存设置并退出；后台任务运行时先确认。"""
        if self._busy and not self._messagebox.askyesno(
            "任务进行中",
            "后台任务仍在运行，确定退出吗？",
            parent=self.root,
        ):
            return
        if self._closing:
            return
        self._closing = True
        self.settings.left = self.left.state
        self.settings.right = self.right.state
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
