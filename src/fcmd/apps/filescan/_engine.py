"""filescan 扫描引擎：遍历、过滤、编码容错、逐行匹配与并行调度（纯逻辑）。

流程：

1. :func:`walk_files` —— 迭代遍历若干根目录，应用递归 / 深度 / 符号链接 /
   **后缀白名单** / 大小过滤，产出待扫描文件与跳过记录；名单外的文件不读内容
   直接跳过，跳过原因按「后缀未命中 / 二进制 / 其他」分类计数；
2. :func:`scan_paths` —— 编译规则集后按文件并行匹配，文件级求值表达式，
   产出按路径排序的 :class:`ScanResult`；支持进度回调与取消；
3. 单文件处理 :func:`scan_file` —— 读取上限、二进制判定、编码回退、逐行匹配。

本模块只依赖标准库，不导入 GUI 库，也不参与工具发现；CLI 与 Qt 窗口共用。
"""

from __future__ import annotations

import contextlib
import os
import stat
import threading
import time
import unicodedata
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fcmd.apps.filescan._extract import (
    DOCUMENT_EXTENSIONS,
    KIND_CORRUPT,
    KIND_ENCRYPTED,
    KIND_NO_TEXT,
    KIND_TOO_LARGE,
    KIND_UNSUPPORTED,
    ExtractError,
    TextUnit,
    extract_document,
)
from fcmd.apps.filescan._filetype import archive_members, detect_file_type
from fcmd.apps.filescan._rules import (
    SIZE_BETWEEN,
    SIZE_GT,
    SIZE_LT,
    TIME_ACCESSED,
    TIME_AFTER,
    TIME_BEFORE,
    TIME_BETWEEN,
    TIME_CREATED,
    TIME_MODIFIED,
    TIME_TARGET_LABELS,
    CompiledRule,
    Expr,
    RuleSet,
    compile_rule,
    evaluate_expression,
    near_rule_ids,
    positive_rule_ids,
    referenced_rule_ids,
    resolve_expression,
)
from fcmd.apps.filescan._timeutil import format_time_value
from fcmd.apps.filescan._validators import validate_value
from fcmd.infras.system import format_bytes
from fcmd.infras.textio import detect_encoding, has_text_bom

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from fcmd.apps.filescan._cache import ScanCache

__all__ = [
    "BINARY_SNIFF_SIZE",
    "DEFAULT_ENCODINGS",
    "DEFAULT_MAX_FILE_BYTES",
    "DEFAULT_SCAN_EXTENSIONS",
    "DEFAULT_SKIP_DIRS",
    "DEFAULT_TEXT_EXTENSIONS",
    "CancelToken",
    "FileHit",
    "RuleMatch",
    "ScanContext",
    "ScanError",
    "ScanOptions",
    "ScanResult",
    "ScanStats",
    "SkipRecord",
    "decode_text",
    "format_size",
    "normalize_exts",
    "normalize_skip_dirs",
    "parse_ext_text",
    "parse_skip_dirs_text",
    "scan_file",
    "scan_paths",
    "walk_files",
]

BINARY_SNIFF_SIZE = 8192
"""二进制判定读取的前缀字节数。"""

DEFAULT_MAX_FILE_BYTES = 8 * 1024 * 1024
"""单文件读取上限（默认 8 MiB）。"""

PROCESS_MIN_FILES = 512
"""进程池调度阈值：待扫描文件数达到该值（或总字节数达标）时改用进程池。"""

PROCESS_MIN_BYTES = 2 * 1024 * 1024
"""进程池调度阈值：待扫描文件总字节数达到该值时改用进程池。"""

_PROCESS_MAX_CHUNK = 64
"""单个进程任务的批大小上限：限制取消延迟与负载不均。"""

DEFAULT_ENCODINGS: tuple[str, ...] = ("utf-8", "gb18030", "latin-1")
"""编码候选：BOM 优先，其余按顺序尝试，最后以 latin-1 兜底。"""

REASON_SYMLINK = "符号链接（未跟随）"
REASON_SKIP_DIR = "跳过目录（内置 / 自定义规则）"
REASON_HIDDEN_DIR = "隐藏目录"
REASON_BINARY = "二进制文件"
REASON_TOO_LARGE = "超出单文件读取上限"
REASON_DOC_ENCRYPTED = "文档已加密"
REASON_DOC_CORRUPT = "文档损坏"
REASON_DOC_NO_TEXT = "文档无文本层"
REASON_DOC_UNSUPPORTED = "文档格式不支持（缺少解析依赖）"
REASON_DOC_TOO_LARGE = "超出文档解析上限"

LOCATION_FILENAME = "文件名"
"""文件名命中的位置标签；此类命中 ``line_no`` 固定为 0。"""

LOCATION_PATH = "路径"
LOCATION_SIZE = "文件大小"
LOCATION_FILETYPE = "文件类型"
LOCATION_ARCHIVE = "归档成员"
LOCATION_TIME_LABELS = TIME_TARGET_LABELS
"""时间命中的位置标签（修改时间 / 创建时间 / 访问时间），``line_no`` 同样为 0。"""

DEFAULT_TEXT_EXTENSIONS: tuple[str, ...] = (
    ".txt",
    ".text",
    ".md",
    ".markdown",
    ".rst",
    ".log",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".ini",
    ".toml",
    ".cfg",
    ".conf",
    ".config",
    ".env",
    ".properties",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".sass",
    ".less",
    ".js",
    ".mjs",
    ".cjs",
    ".jsx",
    ".ts",
    ".tsx",
    ".vue",
    ".svelte",
    ".py",
    ".pyi",
    ".pyw",
    ".java",
    ".kt",
    ".kts",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".cxx",
    ".hpp",
    ".hh",
    ".cs",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".pl",
    ".pm",
    ".lua",
    ".swift",
    ".m",
    ".mm",
    ".scala",
    ".dart",
    ".r",
    ".jl",
    ".ex",
    ".exs",
    ".erl",
    ".hrl",
    ".clj",
    ".cljs",
    ".hs",
    ".lisp",
    ".el",
    ".asm",
    ".s",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".fish",
    ".ps1",
    ".psm1",
    ".psd1",
    ".bat",
    ".cmd",
    ".gradle",
    ".groovy",
    ".cmake",
    ".mk",
    ".lock",
    ".diff",
    ".patch",
    ".tex",
    ".bib",
    ".proto",
    ".graphql",
    ".gql",
    ".hcl",
    ".tf",
    ".tfvars",
    ".plist",
    ".reg",
    ".vbs",
    ".ahk",
)
"""默认后缀白名单：常见文本类后缀；不在名单内的文件直接跳过，不读内容。

条目按「文件名结尾」匹配，因此 ``.env`` 既匹配 ``.env`` 也匹配 ``prod.env``；
``*`` 表示全部后缀（关闭白名单，退回逐文件二进制嗅探）。
"""

DEFAULT_SCAN_EXTENSIONS: tuple[str, ...] = DEFAULT_TEXT_EXTENSIONS + DOCUMENT_EXTENSIONS
"""默认白名单：文本后缀 + 文档后缀（docx/xlsx/pptx/odt/ods 走文档抽取）。"""

DEFAULT_SKIP_DIRS: tuple[str, ...] = (
    # 版本控制 / IDE / 依赖 / 构建产物
    ".git",
    ".svn",
    ".hg",
    ".idea",
    ".vscode",
    ".vs",
    "node_modules",
    "bower_components",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".venv",
    "venv",
    "dist",
    "build",
    "out",
    "target",
    "vendor",
    ".gradle",
    ".next",
    ".nuxt",
    # 包管理器 / 工具链缓存（通常是海量二进制依赖）
    ".m2",
    ".npm",
    ".nuget",
    ".cargo/registry",
    ".cache",
    # 系统目录：盘符级扫描时剪枝，避免回收站 / 系统文件拖慢扫描
    "$recycle.bin",
    "system volume information",
    ".trash",
)
"""默认跳过的目录名（小写，按任意层级匹配）。

不含斜杠的条目匹配目录名；含斜杠的条目（如 ``.cargo/registry``）匹配相对根
目录的路径后缀。用户可在设置页 / 配置文件里整体替换这份列表（空列表表示不跳过）。
"""


# ---------------------------------------------------------------------- #
# 取消与选项
# ---------------------------------------------------------------------- #
class CancelToken:
    """线程安全的取消标记；引擎在每个文件处理前检查。"""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """置位取消。"""
        self._event.set()

    def is_set(self) -> bool:
        """是否已取消。"""
        return self._event.is_set()

    @property
    def cancelled(self) -> bool:
        """是否已取消（语义化别名）。"""
        return self._event.is_set()


@dataclass(frozen=True)
class ScanOptions:
    """扫描范围与单文件读取选项。

    ``ext_whitelist`` 是后缀白名单（默认 :data:`DEFAULT_SCAN_EXTENSIONS`，含文本与
    文档后缀），名单外的文件不读内容直接跳过；``include_exts`` 只做白名单内的收窄，
    ``force_exts`` 强制扫描名单外的后缀（逃生口），``exclude_exts`` 优先级最高。
    ``scan_documents`` 为真时 docx/xlsx/pptx/odt/ods 走文档抽取（默认开启），
    关闭后这些后缀按普通文本读取（通常会被二进制判定跳过）。
    ``use_processes`` 只影响调度方式：达到 :data:`PROCESS_MIN_FILES` /
    :data:`PROCESS_MIN_BYTES` 阈值时用进程池绕开 GIL，结果与线程池逐字节一致。

    ``modified_after`` / ``modified_before`` 按文件 mtime 过滤（epoch 秒，边界
    含端点），``None`` 表示不限；与大小过滤同属遍历阶段的「其他跳过」。

    ``skip_dirs`` 是跳过目录名列表（默认 :data:`DEFAULT_SKIP_DIRS`，按任意层级
    匹配，含斜杠的条目匹配相对根目录的路径后缀）；``skip_hidden`` 为真时跳过
    隐藏文件与隐藏目录（名称以 ``.`` 开头，Windows 另含隐藏属性），默认关闭，
    避免漏扫 ``.env`` 这类常见敏感配置。``normalize_text`` 为真（默认）时，匹配
    前对每行做 NFKC 归一化（全角字母 / 数字、①、㈱ 等按兼容形式匹配），命中
    位置与片段仍映射回原文；纯 ASCII 行走快速路径，不产生额外开销。
    """

    recursive: bool = True
    max_depth: int = 0
    follow_symlinks: bool = False
    skip_dirs: tuple[str, ...] = DEFAULT_SKIP_DIRS
    skip_hidden: bool = False
    normalize_text: bool = True
    ext_whitelist: tuple[str, ...] = DEFAULT_SCAN_EXTENSIONS
    include_exts: tuple[str, ...] = ()
    force_exts: tuple[str, ...] = ()
    exclude_exts: tuple[str, ...] = ()
    min_size: int = 0
    max_size: int | None = None
    modified_after: float | None = None
    modified_before: float | None = None
    skip_binary: bool = True
    scan_documents: bool = True
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    encodings: tuple[str, ...] = DEFAULT_ENCODINGS
    workers: int = 4
    context_chars: int = 40
    max_hits_per_file: int = 200
    use_processes: bool = True


@dataclass(frozen=True)
class ScanContext:
    """单文件扫描所需的规则、表达式与取消标记（由 :func:`scan_paths` 构造）。"""

    compiled_rules: tuple[CompiledRule, ...]
    expression: Expr
    positive_ids: frozenset[str]
    options: ScanOptions
    token: CancelToken | None = None
    near_ids: frozenset[str] = frozenset()


# ---------------------------------------------------------------------- #
# 结果模型
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class RuleMatch:
    """一条命中：规则、行号与上下文（原文，展示时再打码）。

    ``location`` 仅在文档路径下非空（如 ``段落 3`` / ``Sheet1!A12`` / ``幻灯片 2``），
    纯文本路径保持空串，报告与导出据此保持向后兼容。``severity`` 复制自规则，
    便于报告 / GUI 无需回查规则集即可着色与筛选。``sensitive=False`` 用于时间等
    非敏感命中：展示 / 导出 / 复制都不打码。
    """

    rule_id: str
    rule_name: str
    line_no: int
    start: int
    end: int
    excerpt: str
    context: str
    context_start: int
    location: str = ""
    severity: str = ""
    sensitive: bool = True


@dataclass
class FileHit:
    """按文件聚合的命中；``matches`` 为空表示表达式命中但没有正向规则明细。"""

    path: Path
    size: int
    encoding: str
    matches: list[RuleMatch] = field(default_factory=list)


@dataclass(frozen=True)
class SkipRecord:
    """被跳过的文件与原因（不含过滤掉的普通文件）。"""

    path: Path
    reason: str


@dataclass(frozen=True)
class ScanError:
    """遍历或读取错误。"""

    path: Path
    message: str


@dataclass
class ScanStats:
    """扫描统计，用于进度、报告与吞吐口径。"""

    files_seen: int = 0
    files_scanned: int = 0
    files_reused: int = 0
    files_skipped: int = 0
    files_skipped_extension: int = 0
    # 后缀在白名单内，但被大小 / 修改时间过滤掉、从未读取的文件数。
    files_skipped_size_time: int = 0
    # 因 ``skip_hidden`` 在遍历阶段直接跳过的文件数（从未读取）。
    files_skipped_hidden: int = 0
    # 被 ``skip_dirs`` / 隐藏目录剪枝、未下探的目录数（不是文件数）。
    dirs_skipped: int = 0
    files_skipped_binary: int = 0
    files_skipped_other: int = 0
    files_skipped_encrypted: int = 0
    files_skipped_corrupt: int = 0
    files_skipped_no_text: int = 0
    files_skipped_unsupported: int = 0
    files_failed: int = 0
    bytes_read: int = 0
    hit_files: int = 0
    hit_count: int = 0
    elapsed: float = 0.0
    cancelled: bool = False

    @property
    def files_out_of_scope(self) -> int:
        """从未读取的文件数：后缀白名单外 + 大小 / 时间过滤 + 隐藏文件。"""
        return self.files_skipped_extension + self.files_skipped_size_time + self.files_skipped_hidden

    @property
    def files_skipped_after_read(self) -> int:
        """读盘（或解析）之后才跳过的文件数：二进制、文档四类与其他。"""
        return self.files_skipped - self.files_out_of_scope

    @property
    def bytes_per_second(self) -> float:
        """读取吞吐（字节/秒）。"""
        return self.bytes_read / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def files_per_second(self) -> float:
        """文件吞吐（文件/秒）。"""
        return self.files_scanned / self.elapsed if self.elapsed > 0 else 0.0


@dataclass
class ScanResult:
    """一次扫描的完整结果。"""

    roots: tuple[Path, ...]
    hits: list[FileHit] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    stats: ScanStats = field(default_factory=ScanStats)


@dataclass
class WalkResult:
    """遍历结果（内部使用，也供单测直接断言过滤行为）。"""

    files: list[Path] = field(default_factory=list)
    # 与 ``files`` 一一对应的文件大小（决定是否值得走进程池）。
    sizes: list[int] = field(default_factory=list)
    # 与 ``files`` 一一对应的修改时间（纳秒），供缓存校验复用。
    mtimes: list[int] = field(default_factory=list)
    skipped: list[SkipRecord] = field(default_factory=list)
    errors: list[ScanError] = field(default_factory=list)
    seen: int = 0
    skipped_extension: int = 0
    skipped_size: int = 0
    skipped_hidden: int = 0
    skipped_dirs: int = 0
    cancelled: bool = False


@dataclass
class _Outcome:
    """单文件处理结果（内部使用）。

    ``matched_ids`` / ``matches`` / ``size`` / ``encoding`` 是内容侧命中明细，
    表达式求值统一在 :func:`scan_file` 里做，便于合并文件名规则的结果。
    文件名命中即使内容被跳过（二进制 / 文档加密等）也照常成立。
    """

    path: Path
    hit: FileHit | None = None
    skipped: SkipRecord | None = None
    error: ScanError | None = None
    bytes_read: int = 0
    cancelled: bool = False
    matched_ids: frozenset[str] = frozenset()
    matches: tuple[RuleMatch, ...] = ()
    rule_lines: dict[str, set[int]] = field(default_factory=dict)
    rule_counts: dict[str, int] = field(default_factory=dict)
    size: int = 0
    encoding: str = ""


# ---------------------------------------------------------------------- #
# 过滤辅助
# ---------------------------------------------------------------------- #
def parse_ext_text(text: str) -> tuple[str, ...]:
    """把 ``.py;*.md, txt`` 之类的文本拆成规范化后缀元组（去重、带点、小写）。"""
    return normalize_exts(text.replace(";", ",").split(","))


def normalize_exts(items: Sequence[str]) -> tuple[str, ...]:
    """规范化后缀列表：去空白、补前导点、统一小写并去重；``*`` 原样保留。"""
    normalized: list[str] = []
    for item in items:
        value = item.strip().lower()
        if not value:
            continue
        if value == "*":
            if "*" not in normalized:
                normalized.append("*")
            continue
        if value.startswith("*"):
            value = value[1:]
        if not value:
            continue
        if not value.startswith("."):
            value = f".{value}"
        if value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def normalize_skip_dirs(items: Sequence[str]) -> tuple[str, ...]:
    """规范化跳过目录列表：去空白、统一小写、去掉首尾斜杠并去重。

    名称中的空格保留（如 ``program files``）；空条目丢弃。返回空元组表示
    「不跳过任何目录」，由调用方决定是否回落到 :data:`DEFAULT_SKIP_DIRS`。
    """
    normalized: list[str] = []
    for item in items:
        value = item.strip().lower().replace("\\", "/").strip("/")
        while "//" in value:
            value = value.replace("//", "/")
        if value and value not in normalized:
            normalized.append(value)
    return tuple(normalized)


def parse_skip_dirs_text(text: str) -> tuple[str, ...]:
    """把 ``.git;node_modules`` 之类的文本拆成规范化跳过目录元组。"""
    return normalize_skip_dirs(text.replace(",", ";").split(";"))


def _dir_skip_reason(name: str, relative: str, patterns: Sequence[str]) -> bool:
    """目录名是否命中跳过列表；含斜杠的条目按相对根目录的路径后缀匹配。"""
    if not patterns:
        return False
    lowered = name.lower()
    for pattern in patterns:
        if "/" in pattern:
            if relative.lower() == pattern or relative.lower().endswith(f"/{pattern}"):
                return True
        elif lowered == pattern:
            return True
    return False


def format_size(size: int) -> str:
    """把字节数格式化为人类可读文本（IEC 单位）。"""
    return format_bytes(size, binary=True)


def _matches_ext(name: str, exts: Sequence[str]) -> bool:
    """文件名（小写）是否以任一白名单条目结尾。"""
    return any(name.endswith(ext) for ext in exts)


def _extension_selected(path: Path, options: ScanOptions) -> bool:
    """后缀过滤：排除 > 强制 > 白名单 > 收窄列表。

    白名单按文件名结尾匹配（``.env`` 可命中 ``prod.env``）；``*`` 表示放开白名单。
    关闭 ``scan_documents`` 时文档后缀退回「后缀跳过」（与多文档支持前一致）。
    """
    name = path.name.lower()
    if _matches_ext(name, options.exclude_exts):
        return False
    if _matches_ext(name, options.force_exts):
        return True
    if not options.scan_documents and _matches_ext(name, DOCUMENT_EXTENSIONS):
        return False
    if "*" not in options.ext_whitelist and not _matches_ext(name, options.ext_whitelist):
        return False
    return not options.include_exts or _matches_ext(name, options.include_exts)


def _size_selected(size: int, options: ScanOptions) -> bool:
    """大小过滤。"""
    if size < options.min_size:
        return False
    return options.max_size is None or size <= options.max_size


def _time_selected(mtime: float, options: ScanOptions) -> bool:
    """修改时间过滤（epoch 秒，边界含端点）。"""
    if options.modified_after is not None and mtime < options.modified_after:
        return False
    return options.modified_before is None or mtime <= options.modified_before


# ---------------------------------------------------------------------- #
# 遍历
# ---------------------------------------------------------------------- #
def _check_root(root: Path) -> ScanError | None:
    """校验根目录；不合法时返回错误记录。"""
    try:
        if not root.exists():
            return ScanError(root, "目录不存在")
        if not root.is_dir():
            return ScanError(root, "不是目录")
    except OSError as exc:
        return ScanError(root, f"无法访问: {exc}")
    return None


def _enter_directory(directory: Path, options: ScanOptions, visited: set[str]) -> bool:
    """在跟随符号链接模式下按真实路径去重，防止目录环；返回是否继续遍历。"""
    if not options.follow_symlinks:
        return True
    try:
        resolved = str(directory.resolve())
    except OSError:
        resolved = str(directory)
    if resolved in visited:
        return False
    visited.add(resolved)
    return True


def _filtered_file(size: int, mtime: float, options: ScanOptions, result: WalkResult) -> bool:
    """应用大小与修改时间过滤，按原因累计计数；返回是否保留。

    后缀过滤已由 :func:`_classify_entry` 在 ``stat`` 之前完成，这里只判大小与时间。
    """
    if not _size_selected(size, options) or not _time_selected(mtime, options):
        result.skipped_size += 1
        return False
    return True


def _is_hidden(entry: os.DirEntry[str]) -> bool:
    """是否隐藏条目：名称以 ``.`` 开头，或（Windows）带隐藏属性。"""
    if entry.name.startswith("."):
        return True
    if os.name != "nt":
        return False
    attributes = 0
    try:
        attributes = getattr(entry.stat(follow_symlinks=False), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0))


def _classify_entry(  # noqa: PLR0911 - 分支逐一处理目录/链接/文件，早返回最清晰
    entry: os.DirEntry[str],
    depth: int,
    options: ScanOptions,
    result: WalkResult,
) -> tuple[Path, int] | None:
    """处理单个目录项：记录文件、跳过符号链接或返回待下探子目录。

    后缀白名单在 ``stat`` 之前判断：被后缀挡掉的文件不需要大小，省掉一次
    系统调用（混合目录实测约占遍历时间 6%）。代价是这类条目即使 ``stat``
    失败也归入「后缀跳过」，不再记错误。隐藏过滤同样在 ``stat`` 前按名称
    判定（Windows 隐藏属性需要额外一次 ``stat``，仅在开启时发生）。
    """
    path = Path(entry.path)
    try:
        if entry.is_symlink() and not options.follow_symlinks:
            result.skipped.append(SkipRecord(path, REASON_SYMLINK))
            return None
        if entry.is_dir(follow_symlinks=options.follow_symlinks):
            if options.skip_hidden and _is_hidden(entry):
                result.skipped_dirs += 1
                result.skipped.append(SkipRecord(path, REASON_HIDDEN_DIR))
                return None
            descend = options.recursive and (options.max_depth == 0 or depth + 1 <= options.max_depth)
            return (path, depth + 1) if descend else None
        if not entry.is_file(follow_symlinks=options.follow_symlinks):
            return None
        if options.skip_hidden and _is_hidden(entry):
            result.skipped_hidden += 1
            return None
        result.seen += 1
        if not _extension_selected(path, options):
            result.skipped_extension += 1
            return None
        info = entry.stat(follow_symlinks=options.follow_symlinks)
    except OSError as exc:
        result.errors.append(ScanError(path, f"无法读取属性: {exc}"))
        return None
    if _filtered_file(info.st_size, info.st_mtime, options, result):
        result.files.append(path)
        result.sizes.append(info.st_size)
        result.mtimes.append(info.st_mtime_ns)
    return None


def _is_skipped_dir(path: Path, root: Path, patterns: Sequence[str]) -> bool:
    """子目录是否命中 ``skip_dirs``；仅当存在含斜杠的条目时才计算相对路径。"""
    if not patterns:
        return False
    if not any("/" in item for item in patterns):
        return _dir_skip_reason(path.name, "", patterns)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    return _dir_skip_reason(path.name, relative, patterns)


def walk_files(
    roots: Sequence[Path],
    options: ScanOptions | None = None,
    token: CancelToken | None = None,
) -> WalkResult:
    """遍历根目录并返回待扫描文件；权限错误记入 ``errors`` 不中断。

    深度口径：根目录为 0 层，``max_depth=0`` 表示不限；``max_depth=1`` 表示
    只下探一层子目录。``follow_symlinks`` 为假时跳过全部符号链接（不越出根）。
    后缀过滤按 :class:`ScanOptions` 的白名单 / 收窄 / 强制 / 排除顺序判定；
    ``skip_dirs`` / ``skip_hidden`` 在目录层剪枝，被剪掉的目录记入 ``skipped``
    并累加 ``skipped_dirs``。
    """
    opts = options or ScanOptions()
    result = WalkResult()
    visited: set[str] = set()

    for root in roots:
        if token is not None and token.is_set():
            result.cancelled = True
            return result
        problem = _check_root(root)
        if problem is not None:
            result.errors.append(problem)
            continue

        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            if token is not None and token.is_set():
                result.cancelled = True
                return result
            directory, depth = stack.pop()
            if not _enter_directory(directory, opts, visited):
                continue
            try:
                with os.scandir(directory) as iterator:
                    entries = list(iterator)
            except OSError as exc:
                result.errors.append(ScanError(directory, f"无法读取目录: {exc}"))
                continue
            for entry in entries:
                child = _classify_entry(entry, depth, opts, result)
                if child is None:
                    continue
                if _is_skipped_dir(child[0], root, opts.skip_dirs):
                    result.skipped_dirs += 1
                    result.skipped.append(SkipRecord(child[0], REASON_SKIP_DIR))
                    continue
                stack.append(child)

    triples = sorted(zip(result.files, result.sizes, result.mtimes, strict=True), key=lambda item: str(item[0]))
    result.files = [path for path, _size, _mtime in triples]
    result.sizes = [size for _path, size, _mtime in triples]
    result.mtimes = [mtime for _path, _size, mtime in triples]
    return result


# ---------------------------------------------------------------------- #
# 编解码
# ---------------------------------------------------------------------- #
def _looks_binary(raw: bytes) -> bool:
    """BOM 开头的文本不算二进制；其余按前缀是否含 ``\\x00`` 判定。"""
    if has_text_bom(raw):
        return False
    return b"\x00" in raw[:BINARY_SNIFF_SIZE]


def decode_text(raw: bytes, encodings: Sequence[str] = DEFAULT_ENCODINGS) -> tuple[str, str]:
    """按 BOM → 候选编码顺序解码；全部失败时用末位候选配 ``replace``。

    Returns
    -------
    tuple[str, str]
        ``(编码名, 文本)``
    """
    candidates = tuple(encodings) or DEFAULT_ENCODINGS
    encoding = detect_encoding(raw, candidates, candidates[-1])
    try:
        return encoding, raw.decode(encoding)
    except UnicodeDecodeError:
        return encoding, raw.decode(encoding, errors="replace")
    except LookupError:
        return "utf-8", raw.decode("utf-8", errors="replace")


def _is_cancelled(token: Any) -> bool:
    """兼容 :class:`CancelToken` 与任何带 ``is_set()`` 的对象。"""
    return bool(token is not None and token.is_set())


def _normalize_line(line: str) -> tuple[str, list[int] | None]:
    """对一行做 NFKC 归一化并给出「归一化边界 → 原文偏移」映射。

    返回 ``(归一化文本, 边界表)``；原文未发生变化（含纯 ASCII）时边界表为
    ``None``，调用方直接按原偏移切片。边界表长度是 ``len(归一化文本) + 1``，
    ``boundaries[i]`` 是归一化文本第 ``i`` 个字符边界对应的原文偏移。

    实现按「基字符 + 组合记号」切簇逐簇归一化：既能处理兼容字符（①、㈱、全角
    字母），也能处理 ``e`` + 组合重音这类需要合成的序列。极少数跨簇组合（如
    韩文初声 / 中声分离）与服务端整行归一化不一致时，退化为按长度截断的近似
    映射，只影响极短片段，不影响命中判定。
    """
    if line.isascii():
        return line, None
    normalized = unicodedata.normalize("NFKC", line)
    if normalized == line:
        return line, None
    boundaries: list[int] = [0]
    pieces: list[str] = []
    index = 0
    length = len(line)
    while index < length:
        end = index + 1
        while end < length and unicodedata.combining(line[end]):
            end += 1
        segment = unicodedata.normalize("NFKC", line[index:end])
        pieces.append(segment)
        boundaries.extend([end] * len(segment))
        index = end
    joined = "".join(pieces)
    if joined != normalized:
        # 跨簇组合：按长度比例近似映射，保证索引不越界、片段非空。
        cap = len(line)
        boundaries = [min(i, cap) for i in range(len(normalized) + 1)]
        return normalized, boundaries
    return joined, boundaries


def _original_span(match: Any, line: str, boundaries: Sequence[int] | None) -> tuple[int, int]:
    """把匹配区间映射回原文偏移；边界重合时保证至少覆盖一个原文字符。"""
    if boundaries is None:
        return match.start(), match.end()
    last = len(boundaries) - 1
    start = boundaries[min(match.start(), last)]
    end = boundaries[min(match.end(), last)]
    if end <= start < len(line):
        end = start + 1
    return start, end


# ---------------------------------------------------------------------- #
# 单文件匹配
# ---------------------------------------------------------------------- #
def _build_match(  # noqa: PLR0913 - 命中五要素 + 可选位置，拆开反而更难读
    compiled: CompiledRule,
    line: str,
    line_no: int,
    match: Any,
    context_chars: int,
    *,
    location: str = "",
    boundaries: Sequence[int] | None = None,
) -> RuleMatch:
    """构造一条命中，截取左右上下文并记录命中在上下文中的起点。

    ``match`` 是在（可能归一化后的）文本上得到的匹配；``boundaries`` 非空时把
    起点 / 终点映射回原文 ``line``，保证 ``excerpt`` / ``context`` 都是原文。
    """
    hit_start, hit_end = _original_span(match, line, boundaries)
    start = max(0, hit_start - context_chars)
    end = min(len(line), hit_end + context_chars)
    return RuleMatch(
        rule_id=compiled.id,
        rule_name=compiled.name,
        severity=compiled.severity,
        line_no=line_no,
        start=hit_start,
        end=hit_end,
        excerpt=line[hit_start:hit_end],
        context=line[start:end],
        context_start=hit_start - start,
        location=location,
    )


class _SkipFile(Exception):
    """内部信号：文件按规则跳过（二进制 / 超限）。"""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _ReadError(Exception):
    """内部信号：文件读取失败。"""


def _read_text(path: Path, options: ScanOptions, size: int | None = None) -> tuple[str, str, int]:
    """读取并解码文件；超限或二进制抛 ``_SkipFile``，IO 错误抛 ``_ReadError``。

    ``size`` 由遍历阶段传入（``walk`` 已经 ``stat`` 过），省略时自行 ``stat``；
    仍以实际读取长度做上限判定，避免文件在遍历后变大而绕过限制。
    """
    if size is None:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise _ReadError(f"读取失败: {exc}") from exc
    if size > options.max_file_bytes:
        raise _SkipFile(REASON_TOO_LARGE)
    # 按实际大小申请读缓冲：read(n) 会按 n 分配缓冲区，直接用 max_file_bytes+1
    # 会让每个小文件都付出一次 8 MiB 分配/清零的成本（本机实测约占扫描时间 70%）。
    hint = min(size, options.max_file_bytes) + 1
    try:
        with path.open("rb") as handle:
            raw = handle.read(hint)
    except OSError as exc:
        raise _ReadError(f"读取失败: {exc}") from exc
    if len(raw) > options.max_file_bytes:
        raise _SkipFile(REASON_TOO_LARGE)
    if options.skip_binary and _looks_binary(raw):
        raise _SkipFile(REASON_BINARY)
    encoding, text = decode_text(raw, options.encodings)
    return encoding, text, len(raw)


def _collect_matches(  # noqa: PLR0913, PLR0917 - 匹配参数成组传入
    text: str,
    compiled_rules: Sequence[CompiledRule],
    options: ScanOptions,
    token: CancelToken | None,
    location: str = "",
    near_ids: frozenset[str] = frozenset(),
) -> tuple[set[str], list[RuleMatch], dict[str, set[int]], dict[str, int]] | None:
    """逐行匹配全部规则；返回 ``(命中规则 id, 命中明细, 邻近规则行号)``。

    ``near_ids`` 中的规则额外记录命中行号，供 ``near`` 表达式按邻近求值；
    其余规则只记录 id / 明细，避免为大文件保存无用的行号集合。
    """
    limit = max(1, options.max_hits_per_file)
    matched_ids: set[str] = set()
    matches: list[RuleMatch] = []
    rule_lines: dict[str, set[int]] = {}
    rule_counts: dict[str, int] = {}
    for line_no, line in enumerate(text.splitlines(), start=1):
        if _is_cancelled(token):
            return None
        target = line
        boundaries: list[int] | None = None
        if options.normalize_text:
            target, boundaries = _normalize_line(line)
        for compiled in compiled_rules:
            regex = compiled.regex
            if regex is None:
                continue
            # 先用 search 判有无：绝大多数行不命中，可省掉 finditer 的迭代器分配；
            # 命中后才 finditer，匹配集合与顺序与直接 finditer 完全一致。
            if regex.search(target) is None:
                continue
            validator = compiled.rule.matcher.validator
            for match in regex.finditer(target):
                if validator and not validate_value(match.group(0), validator):
                    continue
                matched_ids.add(compiled.id)
                rule_counts[compiled.id] = rule_counts.get(compiled.id, 0) + 1
                if near_ids and compiled.id in near_ids:
                    rule_lines.setdefault(compiled.id, set()).add(line_no)
                if len(matches) < limit:
                    matches.append(
                        _build_match(
                            compiled,
                            line,
                            line_no,
                            match,
                            options.context_chars,
                            location=location,
                            boundaries=boundaries,
                        )
                    )
    return matched_ids, matches, rule_lines, rule_counts


_DOCUMENT_REASONS: dict[str, str] = {
    KIND_ENCRYPTED: REASON_DOC_ENCRYPTED,
    KIND_CORRUPT: REASON_DOC_CORRUPT,
    KIND_NO_TEXT: REASON_DOC_NO_TEXT,
    KIND_UNSUPPORTED: REASON_DOC_UNSUPPORTED,
    KIND_TOO_LARGE: REASON_DOC_TOO_LARGE,
}


def _collect_units(
    units: Sequence[TextUnit],
    context: ScanContext,
) -> tuple[set[str], list[RuleMatch], dict[str, set[int]], dict[str, int]] | None:
    """对文档抽取出的文本单元逐个复用逐行匹配，并受全局命中上限约束。"""
    limit = max(1, context.options.max_hits_per_file)
    matched_ids: set[str] = set()
    matches: list[RuleMatch] = []
    rule_lines: dict[str, set[int]] = {}
    rule_counts: dict[str, int] = {}
    for unit in units:
        if _is_cancelled(context.token):
            return None
        collected = _collect_matches(
            unit.text,
            context.compiled_rules,
            context.options,
            context.token,
            unit.location,
            context.near_ids,
        )
        if collected is None:
            return None
        unit_ids, unit_matches, unit_lines, unit_counts = collected
        matched_ids |= unit_ids
        for rule_id, lines in unit_lines.items():
            rule_lines.setdefault(rule_id, set()).update(lines)
        for rule_id, count in unit_counts.items():
            rule_counts[rule_id] = rule_counts.get(rule_id, 0) + count
        for item in unit_matches:
            if len(matches) < limit:
                matches.append(item)
    return matched_ids, matches, rule_lines, rule_counts


def _match_filename(
    path: Path,
    compiled_rules: Sequence[CompiledRule],
    options: ScanOptions,
    near_ids: frozenset[str] = frozenset(),
) -> tuple[set[str], list[RuleMatch], dict[str, set[int]], dict[str, int]]:
    """对文件名 / 完整路径规则求值；行号固定 0，位置分别为文件名 / 路径。"""
    limit = max(1, options.max_hits_per_file)
    matched_ids: set[str] = set()
    matches: list[RuleMatch] = []
    rule_lines: dict[str, set[int]] = {}
    rule_counts: dict[str, int] = {}
    for compiled in compiled_rules:
        if compiled.matches_filename:
            subject, location = path.name, LOCATION_FILENAME
        elif compiled.matches_path:
            subject, location = str(path), LOCATION_PATH
        else:
            continue
        regex = compiled.regex
        if regex is None:
            continue
        validator = compiled.rule.matcher.validator
        for match in regex.finditer(subject):
            if validator and not validate_value(match.group(0), validator):
                continue
            matched_ids.add(compiled.id)
            rule_counts[compiled.id] = rule_counts.get(compiled.id, 0) + 1
            if near_ids and compiled.id in near_ids:
                rule_lines.setdefault(compiled.id, set()).add(0)
            if len(matches) < limit:
                matches.append(
                    _build_match(
                        compiled,
                        subject,
                        0,
                        match,
                        options.context_chars,
                        location=location,
                    )
                )
    return matched_ids, matches, rule_lines, rule_counts


def _file_times(info: os.stat_result) -> dict[str, float]:
    """文件时间字段：创建时间优先 ``st_birthtime``，否则退回 ``st_ctime``。

    Windows 的 ``st_ctime`` 即创建时间；Linux 无 birthtime 时它是 inode 变更时间，
    文档与 GUI 都按「创建时间（平台近似）」提示。
    """
    birth = getattr(info, "st_birthtime", None)
    return {
        TIME_MODIFIED: info.st_mtime,
        TIME_ACCESSED: info.st_atime,
        TIME_CREATED: float(birth) if birth is not None else info.st_ctime,
    }


def _time_in_window(timestamp: float, compiled: CompiledRule) -> bool:
    """按半开区间判定时间是否命中：before <、after >=、between [start, end)。"""
    start = compiled.time_start
    if start is None:
        return False
    compare = compiled.rule.matcher.compare
    if compare == TIME_BEFORE:
        return timestamp < start
    if compare == TIME_AFTER:
        return timestamp >= start
    if compare == TIME_BETWEEN:
        end = compiled.time_end
        return end is not None and start <= timestamp < end
    return False


def _match_times(
    path: Path,
    compiled_rules: Sequence[CompiledRule],
    options: ScanOptions,
) -> tuple[set[str], list[RuleMatch], dict[str, set[int]], dict[str, int]]:
    """对文件时间规则求值；只 stat 一次，命中位置为时间字段、行号 0。

    时间值不是敏感原文，``RuleMatch.sensitive`` 标记为 ``False``，报告与结果树
    不会被 ``mask_text`` 打码。``rule_lines`` 恒为空（near 只支持内容 / 文件名）。
    """
    time_rules = [item for item in compiled_rules if item.rule.matcher.is_time()]
    if not time_rules:
        return set(), [], {}, {}
    try:
        info = path.stat()
    except OSError:
        return set(), [], {}, {}
    fields = _file_times(info)
    limit = max(1, options.max_hits_per_file)
    matched_ids: set[str] = set()
    matches: list[RuleMatch] = []
    rule_counts: dict[str, int] = {}
    for compiled in time_rules:
        target = compiled.rule.matcher.target
        timestamp = fields.get(target)
        if timestamp is None or not _time_in_window(timestamp, compiled):
            continue
        matched_ids.add(compiled.id)
        rule_counts[compiled.id] = 1
        if len(matches) < limit:
            text = format_time_value(timestamp)
            matches.append(
                RuleMatch(
                    rule_id=compiled.id,
                    rule_name=compiled.name,
                    line_no=0,
                    start=0,
                    end=0,
                    excerpt=text,
                    context=text,
                    context_start=0,
                    location=LOCATION_TIME_LABELS.get(target, target),
                    severity=compiled.severity,
                    sensitive=False,
                )
            )
    return matched_ids, matches, {}, rule_counts


def _match_filetype(
    path: Path,
    compiled_rules: Sequence[CompiledRule],
) -> tuple[set[str], list[RuleMatch], dict[str, int]]:
    """按魔数判断真实文件类型（扩展名不可信时仍能识别改名文件）。"""
    rules = [item for item in compiled_rules if item.file_types]
    if not rules:
        return set(), [], {}
    file_type = detect_file_type(path)
    matched_ids: set[str] = set()
    matches: list[RuleMatch] = []
    rule_counts: dict[str, int] = {}
    for compiled in rules:
        if file_type not in compiled.file_types:
            continue
        matched_ids.add(compiled.id)
        rule_counts[compiled.id] = 1
        matches.append(
            RuleMatch(
                rule_id=compiled.id,
                rule_name=compiled.name,
                line_no=0,
                start=0,
                end=0,
                excerpt=file_type,
                context=file_type,
                context_start=0,
                location=LOCATION_FILETYPE,
                severity=compiled.severity,
                sensitive=False,
            )
        )
    return matched_ids, matches, rule_counts


def _match_archive(
    path: Path,
    compiled_rules: Sequence[CompiledRule],
    options: ScanOptions,
) -> tuple[set[str], list[RuleMatch], dict[str, int]]:
    """对 zip / tar 归档的成员路径求值（只读成员名，不解压内容）。"""
    rules = [item for item in compiled_rules if item.rule.matcher.target == "archive_path"]
    if not rules:
        return set(), [], {}
    file_type = detect_file_type(path)
    members = archive_members(path, file_type)
    if not members:
        return set(), [], {}
    limit = max(1, options.max_hits_per_file)
    matched_ids: set[str] = set()
    matches: list[RuleMatch] = []
    rule_counts: dict[str, int] = {}
    for compiled in rules:
        regex = compiled.regex
        if regex is None:
            continue
        validator = compiled.rule.matcher.validator
        for member in members:
            for match in regex.finditer(member):
                if validator and not validate_value(match.group(0), validator):
                    continue
                matched_ids.add(compiled.id)
                rule_counts[compiled.id] = rule_counts.get(compiled.id, 0) + 1
                if len(matches) < limit:
                    matches.append(
                        _build_match(
                            compiled,
                            member,
                            0,
                            match,
                            options.context_chars,
                            location=LOCATION_ARCHIVE,
                        )
                    )
    return matched_ids, matches, rule_counts


def _match_size(
    path: Path,
    compiled_rules: Sequence[CompiledRule],
    _options: ScanOptions,
    size: int | None = None,
) -> tuple[set[str], list[RuleMatch], dict[str, int]]:
    """对文件大小规则求值；优先用遍历阶段的 size，缺失时 stat 一次。"""
    size_rules = [item for item in compiled_rules if item.rule.matcher.is_size()]
    if not size_rules:
        return set(), [], {}
    actual = size
    if actual is None:
        try:
            actual = path.stat().st_size
        except OSError:
            return set(), [], {}
    matched_ids: set[str] = set()
    matches: list[RuleMatch] = []
    rule_counts: dict[str, int] = {}
    for compiled in size_rules:
        if not _size_in_window(actual, compiled):
            continue
        matched_ids.add(compiled.id)
        rule_counts[compiled.id] = 1
        text = format_size(actual)
        matches.append(
            RuleMatch(
                rule_id=compiled.id,
                rule_name=compiled.name,
                line_no=0,
                start=0,
                end=0,
                excerpt=text,
                context=text,
                context_start=0,
                location=LOCATION_SIZE,
                severity=compiled.severity,
                sensitive=False,
            )
        )
    return matched_ids, matches, rule_counts


def _size_in_window(size: int, compiled: CompiledRule) -> bool:
    """大小条件：lt <、gt >=、between [start, end)。"""
    start = compiled.size_start
    if start is None:
        return False
    compare = compiled.rule.matcher.compare
    if compare == SIZE_LT:
        return size < start
    if compare == SIZE_GT:
        return size >= start
    if compare == SIZE_BETWEEN:
        end = compiled.size_end
        return end is not None and start <= size < end
    return False


def _scan_document(path: Path, context: ScanContext, suffix: str, size: int | None) -> _Outcome:
    """文档类文件：先抽取带位置的文本单元，再复用逐行匹配。

    表达式求值与命中明细筛选统一由 :func:`scan_file` 处理（需合并文件名规则）。
    """
    try:
        units = extract_document(path, size)
    except ExtractError as exc:
        reason = _DOCUMENT_REASONS.get(exc.kind, REASON_DOC_CORRUPT)
        if exc.kind == KIND_UNSUPPORTED:
            # 缺依赖等具体原因（含安装提示）随跳过记录展示，不静默丢弃
            reason = f"{reason}：{exc}"
        return _Outcome(path=path, skipped=SkipRecord(path, reason))
    byte_count = size
    if byte_count is None:
        try:
            byte_count = path.stat().st_size
        except OSError as exc:
            return _Outcome(path=path, error=ScanError(path, f"读取失败: {exc}"))
    collected = _collect_units(units, context)
    if collected is None:
        return _Outcome(path=path, cancelled=True)
    matched_ids, matches, rule_lines, rule_counts = collected
    return _Outcome(
        path=path,
        bytes_read=byte_count,
        matched_ids=frozenset(matched_ids),
        matches=tuple(matches),
        rule_lines=rule_lines,
        rule_counts=rule_counts,
        size=byte_count,
        encoding=suffix.lstrip("."),
    )


def _scan_text(path: Path, context: ScanContext, size: int | None) -> _Outcome:
    """文本类文件：读取上限、二进制判定、编码回退、逐行匹配。

    表达式求值与命中明细筛选统一由 :func:`scan_file` 处理（需合并文件名规则）。
    """
    try:
        encoding, text, byte_count = _read_text(path, context.options, size)
    except _SkipFile as skip:
        return _Outcome(path=path, skipped=SkipRecord(path, skip.reason))
    except _ReadError as error:
        return _Outcome(path=path, error=ScanError(path, str(error)))

    collected = _collect_matches(
        text,
        context.compiled_rules,
        context.options,
        context.token,
        near_ids=context.near_ids,
    )
    if collected is None:
        return _Outcome(path=path, cancelled=True)
    matched_ids, matches, rule_lines, rule_counts = collected
    return _Outcome(
        path=path,
        bytes_read=byte_count,
        matched_ids=frozenset(matched_ids),
        matches=tuple(matches),
        rule_lines=rule_lines,
        rule_counts=rule_counts,
        size=byte_count,
        encoding=encoding,
    )


def scan_file(path: Path, context: ScanContext, size: int | None = None) -> _Outcome:
    """扫描单个文件：文件名规则 + 文档抽取或文本读取，再做逐行匹配与文件级求值。

    ``size`` 由遍历阶段传入以免重复 ``stat``；省略时按需自行获取。文件名规则
    先对 ``path.name`` 求值，即使内容因二进制 / 文档加密等原因被跳过也照常生效。
    """
    if _is_cancelled(context.token):
        return _Outcome(path=path, cancelled=True)
    name_ids, name_matches, name_lines, name_counts = _match_filename(
        path, context.compiled_rules, context.options, context.near_ids
    )
    time_ids, time_matches, time_lines, time_counts = _match_times(path, context.compiled_rules, context.options)
    size_ids, size_matches, size_counts = _match_size(path, context.compiled_rules, context.options, size)
    type_ids, type_matches, type_counts = _match_filetype(path, context.compiled_rules)
    archive_ids, archive_matches, archive_counts = _match_archive(path, context.compiled_rules, context.options)
    metadata_matches = [*name_matches, *time_matches, *size_matches, *type_matches, *archive_matches]
    metadata_ids = name_ids | time_ids | size_ids | type_ids | archive_ids
    metadata_counts = {
        **name_counts,
        **time_counts,
        **size_counts,
        **type_counts,
        **archive_counts,
    }
    suffix = path.suffix.lower()
    if context.options.scan_documents and suffix in DOCUMENT_EXTENSIONS:
        outcome = _scan_document(path, context, suffix, size)
    else:
        outcome = _scan_text(path, context, size)
    if outcome.cancelled or outcome.error is not None:
        return outcome
    counts = {**outcome.rule_counts}
    for rule_id, value in metadata_counts.items():
        counts[rule_id] = counts.get(rule_id, 0) + value
    ids = set(outcome.matched_ids) | metadata_ids
    ids = _apply_thresholds(context.compiled_rules, ids, counts)
    rule_lines = {rule_id: set(lines) for rule_id, lines in outcome.rule_lines.items()}
    for rule_id, lines in (*name_lines.items(), *time_lines.items()):
        rule_lines.setdefault(rule_id, set()).update(lines)
    line_sets = {rule_id: frozenset(rule_lines.get(rule_id, ())) for rule_id in ids}
    if outcome.skipped is not None:
        if not evaluate_expression(context.expression, line_sets):
            return outcome
        return _metadata_only_outcome(path, context, metadata_matches, size, ids)
    if not evaluate_expression(context.expression, line_sets):
        return _Outcome(
            path=path,
            bytes_read=outcome.bytes_read,
            matched_ids=frozenset(ids),
            rule_lines=rule_lines,
            rule_counts=counts,
        )
    limit = max(1, context.options.max_hits_per_file)
    combined = [*metadata_matches, *outcome.matches]
    detail = [item for item in combined if item.rule_id in context.positive_ids and item.rule_id in ids][:limit]
    hit = FileHit(path=path, size=outcome.size, encoding=outcome.encoding, matches=detail)
    return _Outcome(
        path=path,
        hit=hit,
        bytes_read=outcome.bytes_read,
        matched_ids=frozenset(ids),
        matches=tuple(combined),
        rule_lines=rule_lines,
        rule_counts=counts,
        size=outcome.size,
        encoding=outcome.encoding,
    )


def _apply_thresholds(
    compiled_rules: Sequence[CompiledRule],
    ids: set[str],
    counts: dict[str, int],
) -> set[str]:
    """按规则 min_hits / max_hits 过滤文件级命中 id（0 表示不限上限）。"""
    limits = {item.id: (item.rule.min_hits, item.rule.max_hits) for item in compiled_rules}
    matched: set[str] = set()
    for rule_id in ids:
        minimum, maximum = limits.get(rule_id, (1, 0))
        count = counts.get(rule_id, 0)
        if count < minimum:
            continue
        if maximum and count > maximum:
            continue
        matched.add(rule_id)
    return matched


def _metadata_only_outcome(
    path: Path,
    context: ScanContext,
    metadata_matches: list[RuleMatch],
    size: int | None,
    matched_ids: set[str],
) -> _Outcome:
    """内容被跳过、但文件名 / 时间 / 大小规则命中时的结果：只报元数据命中明细。"""
    limit = max(1, context.options.max_hits_per_file)
    detail = [
        item for item in metadata_matches if item.rule_id in context.positive_ids and item.rule_id in matched_ids
    ][:limit]
    actual = size
    if actual is None:
        try:
            actual = path.stat().st_size
        except OSError:
            actual = 0
    hit = FileHit(path=path, size=actual, encoding="", matches=detail)
    return _Outcome(path=path, hit=hit, size=actual, matches=tuple(metadata_matches))


# ---------------------------------------------------------------------- #
# 调度：线程池 / 进程池
# ---------------------------------------------------------------------- #
_WORKER_CONTEXT: ScanContext | None = None


def _worker_init(context: ScanContext) -> None:
    """进程池 worker 初始化：规则上下文每个子进程只收一次（不逐任务 pickle）。"""
    global _WORKER_CONTEXT  # noqa: PLW0603 - 子进程内的模块级单例
    _WORKER_CONTEXT = context


def _worker_scan_batch(items: list[tuple[str, int]]) -> list[_Outcome]:
    """子进程任务：扫一批 ``(路径, 大小)``，返回与 :func:`scan_file` 相同的结果对象。

    只用 ``str`` 与 ``int`` 传参，保证 spawn 下可 pickle，不跨进程传文件句柄。
    """
    context = _WORKER_CONTEXT
    if context is None:  # pragma: no cover - initializer 未执行说明用法错误
        raise RuntimeError("进程池 worker 未初始化")
    return [scan_file(Path(path), context, size) for path, size in items]


def _process_worthwhile(files: Sequence[Path], sizes: Sequence[int], workers: int) -> bool:
    """是否值得走进程池：文件数或总字节数达到阈值，且并发数 > 1。

    小目录用线程（或直接串行）更快——spawn 4 个进程约 0.1 s 固定成本，超过
    小目录本身的扫描耗时，所以按「工作量」而不是只按文件数决定。
    """
    if workers <= 1:
        return False
    if len(files) >= PROCESS_MIN_FILES:
        return True
    return sum(sizes) >= PROCESS_MIN_BYTES


def _scan_in_threads(
    files: Sequence[Path],
    sizes: Sequence[int],
    context: ScanContext,
    workers: int,
    progress: Callable[[int, int, Path], None] | None,
) -> list[_Outcome]:
    """线程池路径：GIL 下正则不并行，收益仅来自 I/O 重叠。"""
    outcomes: list[_Outcome] = []
    total = len(files)
    done = 0
    executor = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [
            (path, executor.submit(scan_file, path, context, size)) for path, size in zip(files, sizes, strict=True)
        ]
        for path, future in futures:
            outcomes.append(future.result())
            done += 1
            if progress is not None:
                progress(done, total, path)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return outcomes


def _scan_in_processes(  # noqa: PLR0913 - 调度参数与线程路径对齐，保持调用点对称
    files: Sequence[Path],
    sizes: Sequence[int],
    context: ScanContext,
    workers: int,
    *,
    token: CancelToken | None,
    progress: Callable[[int, int, Path], None] | None,
) -> tuple[list[_Outcome], bool]:
    """进程池路径：遍历与统计留主进程，读 + 解码 + 逐行匹配在子进程。

    批大小取 ``len(files) // (workers * 8)``、上限 :data:`_PROCESS_MAX_CHUNK`：
    批太大限制取消延迟，批太小让 IPC 往返占掉收益。取消只在批次边界检查
    （取消延迟 ≤ 一个批次），已完成批次照常汇总。
    """
    import multiprocessing

    total = len(files)
    chunk = max(1, min(_PROCESS_MAX_CHUNK, total // (workers * 8)))
    pairs = list(zip(files, sizes, strict=True))
    batches = [pairs[index : index + chunk] for index in range(0, total, chunk)]
    outcomes: list[_Outcome] = []
    cancelled = False
    done = 0
    executor = ProcessPoolExecutor(
        max_workers=workers,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_worker_init,
        initargs=(replace(context, token=None),),
    )
    try:
        futures = [
            executor.submit(_worker_scan_batch, [(str(path), size) for path, size in batch]) for batch in batches
        ]
        for index, (batch, future) in enumerate(zip(batches, futures, strict=True)):
            if _is_cancelled(token):
                cancelled = True
                for pending in futures[index:]:
                    pending.cancel()
                break
            outcomes.extend(future.result())
            for path, _size in batch:
                done += 1
                if progress is not None:
                    progress(done, total, path)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
    return outcomes, cancelled


# ---------------------------------------------------------------------- #
# 批量扫描
# ---------------------------------------------------------------------- #
def scan_paths(  # noqa: PLR0913 - 扫描入口参数由调用方一次性传入，保持扁平
    roots: Sequence[Path],
    ruleset: RuleSet,
    options: ScanOptions | None = None,
    *,
    token: CancelToken | None = None,
    progress: Callable[[int, int, Path], None] | None = None,
    cache: ScanCache | None = None,
    cache_key: str = "",
) -> ScanResult:
    """扫描若干目录并返回按路径排序的结果。

    Parameters
    ----------
    roots:
        一个或多个根目录
    ruleset:
        规则集；表达式为空时按 OR 全部启用规则
    options:
        扫描与读取选项
    token:
        取消标记；置位后返回已完成部分并标记 ``stats.cancelled``
    progress:
        进度回调 ``(已完成, 总数, 当前文件)``，在 worker 线程调用
    cache:
        可选的扫描结果缓存；仅主进程读写，worker 不接触数据库
    cache_key:
        缓存指纹（规则与命中相关选项的哈希）；为空表示不启用缓存
    """
    opts = options or ScanOptions()
    started = time.perf_counter()
    expression = resolve_expression(ruleset)
    # 表达式引用的规则都参与匹配（含未启用规则作为构件）；表达式为空时引用集合
    # 就是全部启用规则，与默认 OR 的行为一致。
    referenced = frozenset(referenced_rule_ids(expression))
    compiled = tuple(compile_rule(rule) for rule in ruleset.rules if rule.id in referenced)
    positive = frozenset(positive_rule_ids(expression)) & {item.id for item in compiled}
    context = ScanContext(
        compiled_rules=compiled,
        expression=expression,
        positive_ids=positive,
        options=opts,
        token=token,
        near_ids=frozenset(near_rule_ids(expression)),
    )
    walk = walk_files(roots, opts, token)
    result = ScanResult(
        roots=tuple(roots),
        skipped=list(walk.skipped),
        errors=list(walk.errors),
        stats=ScanStats(files_seen=walk.seen, cancelled=walk.cancelled),
    )
    files = walk.files
    if not files:
        _finalize_stats(result, walk)
        result.stats.elapsed = time.perf_counter() - started
        return result

    # 路径 → (大小, mtime_ns)，缓存校验与回写都以遍历阶段的一次 stat 为准。
    meta = {path: (size, mtime) for path, size, mtime in zip(walk.files, walk.sizes, walk.mtimes, strict=True)}
    hits: list[FileHit] = []
    files, sizes, reused = _take_cached(files, meta, cache, cache_key, hits)
    result.stats.files_reused = reused
    total_files = len(meta)
    scan_progress = _offset_progress(progress, reused, total_files) if reused and progress is not None else progress

    outcomes: list[_Outcome] = []
    if files:
        workers = max(1, min(opts.workers, 64))
        # 进程数不超过 CPU 核数：多出来的进程只会增加 spawn 与内存开销。
        process_workers = min(workers, os.cpu_count() or workers)
        if opts.use_processes and _process_worthwhile(files, sizes, process_workers):
            outcomes, cancelled = _scan_in_processes(
                files,
                sizes,
                context,
                process_workers,
                token=token,
                progress=scan_progress,
            )
            result.stats.cancelled = result.stats.cancelled or cancelled
        else:
            outcomes = _scan_in_threads(files, sizes, context, workers, scan_progress)
            result.stats.cancelled = result.stats.cancelled or any(item.cancelled for item in outcomes)
    else:
        result.stats.cancelled = result.stats.cancelled or (token is not None and token.is_set())

    fresh: list[tuple[Path, int, int, FileHit | None]] = []
    for outcome in outcomes:
        if outcome.cancelled:
            continue
        if outcome.error is not None:
            result.errors.append(outcome.error)
            result.stats.files_failed += 1
            continue
        if outcome.skipped is not None:
            result.skipped.append(outcome.skipped)
            continue
        result.stats.files_scanned += 1
        result.stats.bytes_read += outcome.bytes_read
        if outcome.hit is not None:
            hits.append(outcome.hit)
        size, mtime = meta[outcome.path]
        fresh.append((outcome.path, size, mtime, outcome.hit))
    _store_fresh(cache, cache_key, fresh)
    hits.sort(key=lambda item: str(item.path))
    result.hits = hits
    result.skipped.sort(key=lambda item: str(item.path))
    result.errors.sort(key=lambda item: str(item.path))
    result.stats.hit_files = len(hits)
    result.stats.hit_count = sum(len(hit.matches) for hit in hits)
    _finalize_stats(result, walk)
    result.stats.elapsed = time.perf_counter() - started
    return result


def _take_cached(
    files: Sequence[Path],
    meta: dict[Path, tuple[int, int]],
    cache: ScanCache | None,
    cache_key: str,
    hits: list[FileHit],
) -> tuple[list[Path], list[int], int]:
    """把命中缓存的文件从待扫列表中剔除并追加到 ``hits``；返回剩余文件与大小。"""
    if cache is None or not cache_key:
        return list(files), [meta[path][0] for path in files], 0
    try:
        cached = cache.lookup_many(cache_key, [(path, *meta[path]) for path in files])
    except Exception:  # 缓存异常不得中断扫描
        cached: dict[Path, FileHit | None] = {}
    if not cached:
        return list(files), [meta[path][0] for path in files], 0
    remaining = [path for path in files if path not in cached]
    hits.extend(hit for hit in cached.values() if hit is not None)
    return remaining, [meta[path][0] for path in remaining], len(cached)


def _store_fresh(
    cache: ScanCache | None,
    cache_key: str,
    items: Sequence[tuple[Path, int, int, FileHit | None]],
) -> None:
    """把本次实扫结果写回缓存；写失败只降级，不影响扫描结果。"""
    if cache is None or not cache_key or not items:
        return
    with contextlib.suppress(Exception):  # 缓存异常不得中断扫描
        cache.store_many(cache_key, items)


def _offset_progress(
    progress: Callable[[int, int, Path], None],
    offset: int,
    total: int,
) -> Callable[[int, int, Path], None]:
    """把实扫进度换算成「含复用文件」的总进度。"""

    def report(done: int, _total: int, path: Path) -> None:
        progress(offset + done, total, path)

    return report


def _finalize_stats(result: ScanResult, walk: WalkResult) -> None:
    """汇总跳过分类：未进入扫描范围 / 二进制 / 文档四类 / 其他。

    后缀白名单外与大小、时间过滤、隐藏文件从未读取，分别计入
    ``files_skipped_extension`` / ``files_skipped_size_time`` / ``files_skipped_hidden``；
    文档的加密、损坏、无文本层、缺依赖分别独立计数，剩余（符号链接、单文件超限）
    归入「其他」。目录剪枝记录（``REASON_SKIP_DIR`` / ``REASON_HIDDEN_DIR``）只是
    跳过原因展示，不是文件，不计入任何文件分类。各文件分类之和等于 ``files_skipped``。
    """
    stats = result.stats
    encrypted = sum(1 for item in result.skipped if item.reason == REASON_DOC_ENCRYPTED)
    corrupt = sum(1 for item in result.skipped if item.reason == REASON_DOC_CORRUPT)
    no_text = sum(1 for item in result.skipped if item.reason == REASON_DOC_NO_TEXT)
    unsupported = sum(1 for item in result.skipped if item.reason.startswith(REASON_DOC_UNSUPPORTED))
    binary = sum(1 for item in result.skipped if item.reason == REASON_BINARY)
    dirs = sum(1 for item in result.skipped if item.reason in (REASON_SKIP_DIR, REASON_HIDDEN_DIR))
    stats.files_skipped_extension = walk.skipped_extension
    stats.files_skipped_size_time = walk.skipped_size
    stats.files_skipped_hidden = walk.skipped_hidden
    stats.dirs_skipped = walk.skipped_dirs
    stats.files_skipped_binary = binary
    stats.files_skipped_encrypted = encrypted
    stats.files_skipped_corrupt = corrupt
    stats.files_skipped_no_text = no_text
    stats.files_skipped_unsupported = unsupported
    stats.files_skipped_other = len(result.skipped) - dirs - binary - encrypted - corrupt - no_text - unsupported
    stats.files_skipped = (
        stats.files_skipped_extension
        + stats.files_skipped_size_time
        + stats.files_skipped_hidden
        + stats.files_skipped_binary
        + stats.files_skipped_other
        + encrypted
        + corrupt
        + no_text
        + unsupported
    )
