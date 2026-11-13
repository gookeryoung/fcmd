"""filesync 核心：目录扫描、比较、同步计划与执行（不注册工具）。

参考 FreeFileSync 的模型，把「比较」与「同步」拆成四步纯逻辑：

1. :func:`scan_tree` —— 递归扫描一侧目录，得到 ``相对路径 → FileEntry``；
   支持包含 / 排除通配符、跳过隐藏文件，权限错误记入 ``errors`` 而不中断。
2. :func:`compare_trees` —— 按「时间与大小 / 仅大小 / 内容」比较两侧，
   产出 :class:`DiffItem`（仅左 / 仅右 / 不同 / 相同）。
3. :func:`build_sync_plan` —— 按「双向 / 镜像 / 更新」模式把差异转成
   :class:`SyncAction`；双向下无法用修改时间判断较新者时记为冲突并跳过。
4. :func:`execute_plan` —— 执行复制 / 删除，逐项收集错误，支持 ``dry_run``
   与进度回调。

本模块只依赖标准库，不导入 Tk，也不参与工具发现；CLI 与 Tk 窗口共用。
"""

from __future__ import annotations

import fnmatch
import os
import re
import shutil
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ACTION_COPY_LTR",
    "ACTION_COPY_RTL",
    "ACTION_DELETE",
    "ACTION_NONE",
    "ACTION_TEXT",
    "CATEGORY_CONFLICT",
    "CATEGORY_DIFFERENT",
    "CATEGORY_EQUAL",
    "CATEGORY_LEFT_ONLY",
    "CATEGORY_NAMES",
    "CATEGORY_RIGHT_ONLY",
    "CATEGORY_TEXT",
    "COMPARE_CONTENT",
    "COMPARE_MODES",
    "COMPARE_MODE_TEXT",
    "COMPARE_SIZE",
    "COMPARE_TIME_SIZE",
    "SYNC_MIRROR",
    "SYNC_MODES",
    "SYNC_MODE_TEXT",
    "SYNC_TWO_WAY",
    "SYNC_UPDATE",
    "TIME_TOLERANCE_SECONDS",
    "CompareOptions",
    "CompareResult",
    "DiffItem",
    "FileEntry",
    "ScanError",
    "ScanResult",
    "SyncAction",
    "SyncError",
    "SyncPlan",
    "SyncReport",
    "build_sync_plan",
    "compare_result_data",
    "compare_trees",
    "content_equal",
    "execute_plan",
    "format_compare_report",
    "format_scan_errors",
    "format_size",
    "format_sync_report",
    "normalize_compare_mode",
    "normalize_sync_mode",
    "parse_filter_text",
    "scan_tree",
]

# ---------------------------------------------------------------------- #
# 常量
# ---------------------------------------------------------------------- #
CATEGORY_LEFT_ONLY = "left_only"
CATEGORY_RIGHT_ONLY = "right_only"
CATEGORY_DIFFERENT = "different"
CATEGORY_EQUAL = "equal"
CATEGORY_CONFLICT = "conflict"

CATEGORY_NAMES: tuple[str, ...] = (
    CATEGORY_LEFT_ONLY,
    CATEGORY_RIGHT_ONLY,
    CATEGORY_DIFFERENT,
    CATEGORY_EQUAL,
    CATEGORY_CONFLICT,
)

CATEGORY_TEXT: dict[str, str] = {
    CATEGORY_LEFT_ONLY: "仅左侧",
    CATEGORY_RIGHT_ONLY: "仅右侧",
    CATEGORY_DIFFERENT: "内容不同",
    CATEGORY_EQUAL: "相同",
    CATEGORY_CONFLICT: "冲突",
}

COMPARE_TIME_SIZE = "time_size"
COMPARE_SIZE = "size"
COMPARE_CONTENT = "content"
COMPARE_MODES: tuple[str, ...] = (COMPARE_TIME_SIZE, COMPARE_SIZE, COMPARE_CONTENT)
COMPARE_MODE_TEXT: dict[str, str] = {
    COMPARE_TIME_SIZE: "时间与大小",
    COMPARE_SIZE: "仅大小",
    COMPARE_CONTENT: "内容",
}

SYNC_TWO_WAY = "two_way"
SYNC_MIRROR = "mirror"
SYNC_UPDATE = "update"
SYNC_MODES: tuple[str, ...] = (SYNC_TWO_WAY, SYNC_MIRROR, SYNC_UPDATE)
SYNC_MODE_TEXT: dict[str, str] = {
    SYNC_TWO_WAY: "双向",
    SYNC_MIRROR: "镜像（左→右）",
    SYNC_UPDATE: "更新（左→右）",
}

ACTION_NONE = "none"
ACTION_COPY_LTR = "copy_ltr"
ACTION_COPY_RTL = "copy_rtl"
ACTION_DELETE = "delete"
ACTION_TEXT: dict[str, str] = {
    ACTION_NONE: "-",
    ACTION_COPY_LTR: "复制 → 右侧",
    ACTION_COPY_RTL: "复制 ← 右侧",
    ACTION_DELETE: "删除右侧",
}

TIME_TOLERANCE_SECONDS = 2.0
"""修改时间容差（秒）：兼容 FAT / 网络共享的 2 秒时间戳粒度。"""

_CONTENT_CHUNK_SIZE = 1024 * 1024
_COPY_KINDS = frozenset({ACTION_COPY_LTR, ACTION_COPY_RTL})
_FILTER_SPLIT_RE = re.compile(r"[;\n]+")

ProgressCallback = Callable[[int, int, "SyncAction"], None]


# ---------------------------------------------------------------------- #
# 数据模型
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class FileEntry:
    """一侧目录中的文件条目。"""

    relative: str
    size: int
    mtime: float


@dataclass(frozen=True)
class ScanError:
    """扫描 / 比较过程中的单项错误。"""

    relative: str
    message: str
    side: str = ""


@dataclass
class ScanResult:
    """一侧目录的扫描结果。"""

    entries: dict[str, FileEntry] = field(default_factory=dict)
    errors: list[ScanError] = field(default_factory=list)


@dataclass(frozen=True)
class CompareOptions:
    """比较选项（包含 / 排除模式为空表示不过滤）。"""

    mode: str = COMPARE_TIME_SIZE
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    skip_hidden: bool = False
    time_tolerance: float = TIME_TOLERANCE_SECONDS


@dataclass(frozen=True)
class DiffItem:
    """一条比较结果。"""

    relative: str
    category: str
    left: FileEntry | None = None
    right: FileEntry | None = None
    detail: str = ""

    @property
    def category_text(self) -> str:
        """比较结果的中文展示名。"""
        return CATEGORY_TEXT[self.category]


@dataclass
class CompareResult:
    """两侧目录的比较结果。"""

    left: Path
    right: Path
    items: list[DiffItem] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    options: CompareOptions = field(default_factory=CompareOptions)

    def counts(self) -> dict[str, int]:
        """返回各分类数量（含未出现的分类，便于稳定展示）。"""
        counts = dict.fromkeys(CATEGORY_NAMES, 0)
        for item in self.items:
            counts[item.category] += 1
        return counts

    def count(self, category: str) -> int:
        """返回指定分类的条目数。"""
        return sum(1 for item in self.items if item.category == category)

    def category_items(self, category: str) -> list[DiffItem]:
        """返回指定分类的条目。"""
        return [item for item in self.items if item.category == category]


def compare_result_data(result: CompareResult) -> dict[str, Any]:
    """把比较结果投影为 ``--json`` 结构（与默认差异表同源）。"""
    only_left = [item.relative for item in result.category_items(CATEGORY_LEFT_ONLY)]
    only_right = [item.relative for item in result.category_items(CATEGORY_RIGHT_ONLY)]
    different = [item.relative for item in result.category_items(CATEGORY_DIFFERENT)]
    errors = [{"path": item.relative, "error": item.message} for item in result.errors]
    return {
        "left": str(result.left),
        "right": str(result.right),
        "equal": not (only_left or only_right or different or errors),
        "only_left": only_left,
        "only_right": only_right,
        "different": different,
        "errors": errors,
    }


@dataclass(frozen=True)
class SyncAction:
    """一条待执行的同步动作（复制或删除）。"""

    kind: str
    relative: str
    source: Path | None
    target: Path | None
    size: int = 0
    reason: str = ""

    @property
    def is_copy(self) -> bool:
        """是否为复制动作。"""
        return self.kind in _COPY_KINDS

    @property
    def action_text(self) -> str:
        """动作的中文展示名。"""
        return ACTION_TEXT[self.kind]


@dataclass
class SyncPlan:
    """同步计划：动作列表与冲突项。"""

    mode: str
    actions: list[SyncAction] = field(default_factory=list)
    conflicts: list[DiffItem] = field(default_factory=list)

    @property
    def copy_actions(self) -> list[SyncAction]:
        """全部复制动作。"""
        return [action for action in self.actions if action.is_copy]

    @property
    def delete_actions(self) -> list[SyncAction]:
        """全部删除动作。"""
        return [action for action in self.actions if action.kind == ACTION_DELETE]

    @property
    def copy_count(self) -> int:
        """复制动作数量。"""
        return len(self.copy_actions)

    @property
    def delete_count(self) -> int:
        """删除动作数量。"""
        return len(self.delete_actions)

    @property
    def bytes_to_copy(self) -> int:
        """复制动作的源文件字节总数。"""
        return sum(action.size for action in self.copy_actions)


@dataclass(frozen=True)
class SyncError:
    """一项同步失败记录。"""

    relative: str
    message: str


@dataclass
class SyncReport:
    """同步执行报告。"""

    dry_run: bool
    planned: list[SyncAction] = field(default_factory=list)
    copied: int = 0
    deleted: int = 0
    bytes_copied: int = 0
    errors: list[SyncError] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """没有失败项时为真（试运行同样按计划成功计）。"""
        return not self.errors


# ---------------------------------------------------------------------- #
# 扫描与比较
# ---------------------------------------------------------------------- #
def normalize_compare_mode(name: str) -> str:
    """把比较模式规范为 :data:`COMPARE_MODES` 之一（忽略大小写与空白）。

    Raises
    ------
    ValueError
        模式不在支持列表中。
    """
    key = name.strip().lower()
    if key in COMPARE_MODES:
        return key
    raise ValueError(f"不支持的比较模式: {name}，支持: {', '.join(COMPARE_MODES)}")


def normalize_sync_mode(name: str) -> str:
    """把同步模式规范为 :data:`SYNC_MODES` 之一（忽略大小写与空白）。

    Raises
    ------
    ValueError
        模式不在支持列表中。
    """
    key = name.strip().lower()
    if key in SYNC_MODES:
        return key
    raise ValueError(f"不支持的同步模式: {name}，支持: {', '.join(SYNC_MODES)}")


def parse_filter_text(text: str) -> tuple[str, ...]:
    """把分号 / 换行分隔的过滤文本拆成模式元组（去空白、丢弃空项）。"""
    return tuple(token.strip() for token in _FILTER_SPLIT_RE.split(text) if token.strip())


def _is_hidden(name: str) -> bool:
    """按名称判断是否为隐藏项（以 ``.`` 开头）。"""
    return name.startswith(".")


def _matches_any(relative: str, patterns: Sequence[str]) -> bool:
    """相对路径或其文件名命中任一通配符时为真。"""
    name = relative.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(relative, pattern) or fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def _keeps(relative: str, options: CompareOptions) -> bool:
    """按包含 / 排除规则判断文件是否参与比较（排除优先）。"""
    if options.exclude and _matches_any(relative, options.exclude):
        return False
    return not options.include or _matches_any(relative, options.include)


def _error_relative(filename: str | bytes | None, root: Path) -> str:
    """把 ``OSError.filename`` 转成相对扫描根的展示路径。"""
    if not filename:
        return ""
    path = Path(os.fsdecode(filename))
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def scan_tree(root: Path, options: CompareOptions | None = None) -> ScanResult:
    """递归扫描 ``root`` 下的普通文件。

    ``include`` / ``exclude`` 按相对 POSIX 路径或文件名做 ``fnmatch`` 匹配，
    ``exclude`` 优先；``skip_hidden`` 会跳过以 ``.`` 开头的文件与目录（不进入
    隐藏目录）。读取失败的文件 / 目录记入 :attr:`ScanResult.errors`，不中断扫描。

    Parameters
    ----------
    root:
        待扫描目录（不存在时返回一条错误）。
    options:
        比较选项；``None`` 使用默认选项。
    """
    opts = options or CompareOptions()
    result = ScanResult()
    if not root.is_dir():
        result.errors.append(ScanError("", "目录不存在或不是目录"))
        return result

    def on_error(exc: OSError) -> None:
        result.errors.append(ScanError(_error_relative(exc.filename, root), str(exc)))

    for current, dirnames, filenames in os.walk(root, onerror=on_error):
        dirnames.sort()
        filenames.sort()
        if opts.skip_hidden:
            dirnames[:] = [name for name in dirnames if not _is_hidden(name)]
        current_path = Path(current)
        for name in filenames:
            if opts.skip_hidden and _is_hidden(name):
                continue
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if not _keeps(relative, opts):
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                result.errors.append(ScanError(relative, str(exc)))
                continue
            result.entries[relative] = FileEntry(relative=relative, size=stat.st_size, mtime=stat.st_mtime)
    return result


def content_equal(left: Path, right: Path, chunk_size: int = _CONTENT_CHUNK_SIZE) -> bool:
    """分块比较两个文件的内容是否完全一致。

    Raises
    ------
    OSError
        任一侧无法读取（文件被删除、权限不足等）。
    """
    with left.open("rb") as left_handle, right.open("rb") as right_handle:
        while True:
            left_chunk = left_handle.read(chunk_size)
            right_chunk = right_handle.read(chunk_size)
            if left_chunk != right_chunk:
                return False
            if not left_chunk:
                return True


def _content_comparison(
    left: Path,
    right: Path,
    relative: str,
    errors: list[ScanError],
) -> bool:
    """内容比较；读取失败时记错并按「不同」处理。"""
    try:
        return content_equal(left, right)
    except OSError as exc:
        errors.append(ScanError(relative, f"内容比较失败: {exc}"))
        return False


def _compare_common(  # noqa: PLR0913, PLR0917 - 比较上下文参数较多，显式传递避免临时对象
    relative: str,
    left_entry: FileEntry,
    right_entry: FileEntry,
    left_root: Path,
    right_root: Path,
    options: CompareOptions,
    errors: list[ScanError],
) -> DiffItem:
    """比较两侧都存在的同一相对路径。"""
    if left_entry.size != right_entry.size:
        detail = f"大小不同（{format_size(left_entry.size)} / {format_size(right_entry.size)}）"
        return DiffItem(relative, CATEGORY_DIFFERENT, left_entry, right_entry, detail)
    if options.mode == COMPARE_SIZE:
        return DiffItem(relative, CATEGORY_EQUAL, left_entry, right_entry)
    if options.mode == COMPARE_TIME_SIZE:
        if abs(left_entry.mtime - right_entry.mtime) <= options.time_tolerance:
            return DiffItem(relative, CATEGORY_EQUAL, left_entry, right_entry)
        return DiffItem(relative, CATEGORY_DIFFERENT, left_entry, right_entry, "修改时间不同")
    same_content = _content_comparison(left_root / relative, right_root / relative, relative, errors)
    if same_content:
        return DiffItem(relative, CATEGORY_EQUAL, left_entry, right_entry)
    return DiffItem(relative, CATEGORY_DIFFERENT, left_entry, right_entry, "内容不同")


def compare_trees(left: Path, right: Path, options: CompareOptions | None = None) -> CompareResult:
    """扫描并比较两个目录，结果按相对路径排序。

    Raises
    ------
    ValueError
        ``options.mode`` 不是受支持的比较模式。
    """
    opts = options or CompareOptions()
    mode = normalize_compare_mode(opts.mode)
    opts = CompareOptions(
        mode=mode,
        include=opts.include,
        exclude=opts.exclude,
        skip_hidden=opts.skip_hidden,
        time_tolerance=opts.time_tolerance,
    )
    left_scan = scan_tree(left, opts)
    right_scan = scan_tree(right, opts)
    errors = [ScanError(item.relative, item.message, "left") for item in left_scan.errors]
    errors += [ScanError(item.relative, item.message, "right") for item in right_scan.errors]

    items: list[DiffItem] = []
    for relative in sorted(set(left_scan.entries) | set(right_scan.entries)):
        left_entry = left_scan.entries.get(relative)
        right_entry = right_scan.entries.get(relative)
        if left_entry is None and right_entry is not None:
            items.append(DiffItem(relative, CATEGORY_RIGHT_ONLY, None, right_entry, "仅右侧存在"))
        elif right_entry is None and left_entry is not None:
            items.append(DiffItem(relative, CATEGORY_LEFT_ONLY, left_entry, None, "仅左侧存在"))
        elif left_entry is not None and right_entry is not None:
            items.append(_compare_common(relative, left_entry, right_entry, left, right, opts, errors))
    return CompareResult(left=left, right=right, items=items, errors=errors, options=opts)


# ---------------------------------------------------------------------- #
# 同步计划
# ---------------------------------------------------------------------- #
def _newer_side(item: DiffItem, tolerance: float) -> str:
    """返回较新一侧（``"left"`` / ``"right"`` / ``"unknown"``）。"""
    if item.left is None or item.right is None:
        return "unknown"
    if item.left.mtime > item.right.mtime + tolerance:
        return "left"
    if item.right.mtime > item.left.mtime + tolerance:
        return "right"
    return "unknown"


def _copy_action(
    item: DiffItem,
    *,
    to_right: bool,
    source_root: Path,
    target_root: Path,
    reason: str,
) -> SyncAction:
    """构造一条复制动作（源 / 目标由根目录与相对路径拼出）。"""
    entry = item.left if to_right else item.right
    kind = ACTION_COPY_LTR if to_right else ACTION_COPY_RTL
    return SyncAction(
        kind=kind,
        relative=item.relative,
        source=source_root / item.relative,
        target=target_root / item.relative,
        size=entry.size if entry is not None else 0,
        reason=reason,
    )


def build_sync_plan(result: CompareResult, mode: str = SYNC_TWO_WAY) -> SyncPlan:
    """按同步模式把比较结果转成可执行计划。

    模式语义：

    - ``two_way``：两侧互补；同一路径内容不同时按修改时间较新者覆盖，
      时间相同（容差内）视为冲突并跳过。
    - ``mirror``：以左侧为准，右侧多出的文件删除、不同文件用左侧覆盖。
    - ``update``：以左侧为准，只复制「仅左侧」与「左侧较新」的文件，从不删除。

    Parameters
    ----------
    result:
        比较结果。
    mode:
        同步模式（``two_way`` / ``mirror`` / ``update``）。

    Raises
    ------
    ValueError
        模式不受支持。
    """
    normalized = normalize_sync_mode(mode)
    plan = SyncPlan(mode=normalized)
    tolerance = result.options.time_tolerance
    for item in result.items:
        if item.category == CATEGORY_LEFT_ONLY:
            plan.actions.append(
                _copy_action(
                    item,
                    to_right=True,
                    source_root=result.left,
                    target_root=result.right,
                    reason="仅左侧存在",
                )
            )
            continue
        if item.category == CATEGORY_RIGHT_ONLY:
            if normalized == SYNC_MIRROR:
                plan.actions.append(
                    SyncAction(
                        kind=ACTION_DELETE,
                        relative=item.relative,
                        source=None,
                        target=result.right / item.relative,
                        size=item.right.size if item.right is not None else 0,
                        reason="镜像模式删除右侧多余文件",
                    )
                )
            elif normalized == SYNC_TWO_WAY:
                plan.actions.append(
                    _copy_action(
                        item,
                        to_right=False,
                        source_root=result.right,
                        target_root=result.left,
                        reason="仅右侧存在",
                    )
                )
            continue
        if item.category != CATEGORY_DIFFERENT or item.left is None or item.right is None:
            continue
        if normalized == SYNC_MIRROR:
            plan.actions.append(
                _copy_action(
                    item,
                    to_right=True,
                    source_root=result.left,
                    target_root=result.right,
                    reason="镜像模式以左侧为准",
                )
            )
            continue
        newer = _newer_side(item, tolerance)
        if normalized == SYNC_UPDATE:
            if newer == "left":
                plan.actions.append(
                    _copy_action(
                        item,
                        to_right=True,
                        source_root=result.left,
                        target_root=result.right,
                        reason="更新模式左侧较新",
                    )
                )
            continue
        if newer == "left":
            plan.actions.append(
                _copy_action(
                    item,
                    to_right=True,
                    source_root=result.left,
                    target_root=result.right,
                    reason="左侧较新",
                )
            )
        elif newer == "right":
            plan.actions.append(
                _copy_action(
                    item,
                    to_right=False,
                    source_root=result.right,
                    target_root=result.left,
                    reason="右侧较新",
                )
            )
        else:
            plan.conflicts.append(item)
    return plan


# ---------------------------------------------------------------------- #
# 执行
# ---------------------------------------------------------------------- #
def _require_paths(action: SyncAction) -> tuple[Path, Path]:
    """校验动作的源 / 目标路径完整。"""
    if action.source is None or action.target is None:
        raise OSError("同步动作缺少源或目标路径")
    return action.source, action.target


def _execute_copy(action: SyncAction, dry_run: bool) -> None:
    """执行一条复制动作（``dry_run`` 时不写盘）。"""
    if dry_run:
        return
    source, target = _require_paths(action)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _execute_delete(action: SyncAction, dry_run: bool) -> None:
    """执行一条删除动作（``dry_run`` 时不写盘）。"""
    if dry_run:
        return
    if action.target is None:
        raise OSError("同步动作缺少目标路径")
    action.target.unlink()


def execute_plan(
    plan: SyncPlan,
    *,
    dry_run: bool = False,
    progress: ProgressCallback | None = None,
) -> SyncReport:
    """执行同步计划，逐项收集错误并返回报告。

    Parameters
    ----------
    plan:
        待执行的同步计划。
    dry_run:
        为真时只统计动作数量与字节数，不修改文件系统。
    progress:
        每完成一项（含失败）后调用 ``progress(done, total, action)``。
    """
    report = SyncReport(dry_run=dry_run, planned=list(plan.actions))
    total = len(plan.actions)
    for index, action in enumerate(plan.actions, start=1):
        try:
            if action.is_copy:
                _execute_copy(action, dry_run)
                report.copied += 1
                report.bytes_copied += action.size
            elif action.kind == ACTION_DELETE:
                _execute_delete(action, dry_run)
                report.deleted += 1
        except OSError as exc:
            report.errors.append(SyncError(action.relative, str(exc)))
        if progress is not None:
            progress(index, total, action)
    return report


# ---------------------------------------------------------------------- #
# 文本输出
# ---------------------------------------------------------------------- #
def format_size(size: int) -> str:
    """把字节数格式化为 ``N B`` / ``N.N KB`` / ``N.N MB`` / ``N.N GB``。"""
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    if size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MB"
    return f"{size / (1024 * 1024 * 1024):.1f} GB"


def _display_width(text: str) -> int:
    """按终端显示宽度计数（中文等宽字符按 2 列计）。"""
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int) -> str:
    """按显示宽度左对齐填充。"""
    return text + " " * max(0, width - _display_width(text))


_SIDE_TEXT: dict[str, str] = {"left": "左侧", "right": "右侧", "": "扫描"}


def format_scan_errors(errors: Sequence[ScanError]) -> str:
    """把扫描 / 比较错误格式化为多行文本。"""
    return "\n".join(
        f"  - {_SIDE_TEXT.get(item.side, '扫描')} {item.relative or '.'}: {item.message}" for item in errors
    )


def format_compare_report(result: CompareResult, *, show_equal: bool = False) -> str:
    """格式化比较结果：概要 + 差异表 + 扫描错误。

    Parameters
    ----------
    result:
        比较结果。
    show_equal:
        为真时在表格中包含「相同」项。
    """
    counts = result.counts()
    lines = [
        f"左侧: {result.left}",
        f"右侧: {result.right}",
        f"比较方式: {COMPARE_MODE_TEXT[result.options.mode]}",
        (
            f"共 {len(result.items)} 项：仅左 {counts[CATEGORY_LEFT_ONLY]}，"
            f"仅右 {counts[CATEGORY_RIGHT_ONLY]}，不同 {counts[CATEGORY_DIFFERENT]}，"
            f"相同 {counts[CATEGORY_EQUAL]}"
        ),
    ]
    visible = [item for item in result.items if show_equal or item.category != CATEGORY_EQUAL]
    lines.append("")
    if not visible:
        lines.append("(没有差异)" if result.items else "(两侧均为空)")
    else:
        header = _pad("状态", 10) + _pad("相对路径", 42) + _pad("左侧大小", 12) + _pad("右侧大小", 12) + "说明"
        lines.append(header)
        lines.append("-" * _display_width(header))
        for item in visible:
            left_size = format_size(item.left.size) if item.left is not None else "-"
            right_size = format_size(item.right.size) if item.right is not None else "-"
            lines.append(
                _pad(item.category_text, 10)
                + _pad(item.relative, 42)
                + _pad(left_size, 12)
                + _pad(right_size, 12)
                + item.detail
            )
    if result.errors:
        lines.append("")
        lines.append(f"扫描错误 {len(result.errors)} 项：")
        lines.append(format_scan_errors(result.errors))
    return "\n".join(lines)


def format_sync_report(report: SyncReport) -> str:
    """格式化同步报告（试运行 / 正式执行 + 失败明细）。"""
    prefix = "试运行" if report.dry_run else "同步完成"
    lines = [
        (
            f"{prefix}：复制 {report.copied} 项（{format_size(report.bytes_copied)}），"
            f"删除 {report.deleted} 项，失败 {len(report.errors)} 项"
        )
    ]
    if report.errors:
        lines.append("失败明细：")
        lines.extend(f"  - {item.relative}: {item.message}" for item in report.errors)
    return "\n".join(lines)
