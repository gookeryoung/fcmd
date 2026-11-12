"""filescanw - 目录敏感内容扫描（Tk GUI）。

三个分页（沿用 llmdownw 的分页口径，避免表单堆叠）：

- **扫描**：多目录列表、递归/深度/符号链接、后缀白名单表格（类型 / 后缀两列，
  每行一个类型，后缀用英文逗号分隔，可新增 / 编辑 / 删除类型并逐项校验）、
  后缀包含/排除、强制后缀、大小与修改时间上下限、跳过二进制、解析文档、
  单文件上限、并发数；开始 / 取消与进度条；导入 / 导出扫描配置。
- **结果**：结果按文件聚合（父节点为路径，子节点为规则 / 行号 / 打码上下文），
  可按规则与路径筛选，导出文本 / JSON / CSV（默认打码，勾选「导出原文」才输出
  敏感原文）。
- **规则**：规则表、新增 / 编辑 / 删除、启用 / 禁用、载入内置、清空、
  导入 / 导出规则集，以及 ``and`` / ``or`` / ``not`` 组合表达式输入与校验。

窗口设置（目录、选项、规则集、过滤条件、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/filescanw.json``，下次启动自动恢复。

扫描在后台线程执行，取消走 :class:`CancelToken`，关窗时置位取消并等待 worker
退出，不留下悬挂线程。

示例
----
    fcmd filescanw        # 打开敏感内容扫描窗口
    filescanw             # 全局命令等价入口
"""

from __future__ import annotations

import queue
import re
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.apps.filescan._config import (
    REPORT_FORMATS,
    ScanConfig,
    format_timestamp,
    load_config,
    parse_timestamp,
)
from fcmd.apps.filescan._config import (
    save_config as save_config_file,
)
from fcmd.apps.filescan._engine import (
    DEFAULT_ENCODINGS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_SCAN_EXTENSIONS,
    CancelToken,
    ScanOptions,
    ScanResult,
    format_size,
    normalize_exts,
    parse_ext_text,
    scan_paths,
)
from fcmd.apps.filescan._extract import DOCUMENT_EXTENSIONS
from fcmd.apps.filescan._report import filter_result, render_context, write_report
from fcmd.apps.filescan._rules import (
    MATCH_KEYWORD,
    MATCH_REGEX,
    Matcher,
    Rule,
    RuleSet,
    builtin_ruleset,
    default_expression,
    parse_expression,
    resolve_expression,
    ruleset_from_mapping,
    ruleset_to_mapping,
    save_ruleset,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui import window as _guiwindow

if TYPE_CHECKING:
    from tkinter import ttk

__all__ = [
    "SETTINGS_NAME",
    "WHITELIST_GROUPS",
    "FilescanSettings",
    "FilescanWindow",
    "apply_expression",
    "group_whitelist",
    "load_settings",
    "parse_and_validate_ext_line",
    "parse_whitelist_text",
    "run_gui",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "validate_exts",
]

SETTINGS_NAME = "filescanw"
DEFAULT_GEOMETRY = "1120x760+140+70"
MIN_WIDTH = 960
MIN_HEIGHT = 620

_DESCRIPTION = "目录敏感内容扫描：规则表达式、并行扫描、按文件聚合、打码与多格式报告（Tk）"


# ---------------------------------------------------------------------- #
# 设置
# ---------------------------------------------------------------------- #
@dataclass
class FilescanSettings:
    """窗口与设置文件共用的配置。"""

    paths: tuple[str, ...] = ()
    recursive: bool = True
    max_depth: int = 0
    follow_symlinks: bool = False
    ext_whitelist: str = field(default_factory=lambda: ";".join(DEFAULT_SCAN_EXTENSIONS))
    whitelist_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    include_ext: str = ""
    force_ext: str = ""
    exclude_ext: str = ""
    min_size: int = 0
    max_size: int = 0
    modified_after: str = ""
    modified_before: str = ""
    skip_binary: bool = True
    scan_documents: bool = True
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    workers: int = 4
    context_chars: int = 40
    max_hits_per_file: int = 200
    use_processes: bool = True
    encodings: tuple[str, ...] = DEFAULT_ENCODINGS
    ruleset: RuleSet = field(default_factory=builtin_ruleset)
    filter_rule: str = ""
    filter_path: str = ""
    report_format: str = "text"
    geometry: str | None = None


def settings_to_mapping(settings: FilescanSettings) -> dict[str, Any]:
    """把设置转成可写入 JSON 的字典（含完整规则集）。"""
    return {
        "paths": list(settings.paths),
        "recursive": settings.recursive,
        "max_depth": settings.max_depth,
        "follow_symlinks": settings.follow_symlinks,
        "ext_whitelist": settings.ext_whitelist,
        "whitelist_groups": [[label, list(exts)] for label, exts in settings.whitelist_groups],
        "include_ext": settings.include_ext,
        "force_ext": settings.force_ext,
        "exclude_ext": settings.exclude_ext,
        "min_size": settings.min_size,
        "max_size": settings.max_size,
        "modified_after": settings.modified_after,
        "modified_before": settings.modified_before,
        "skip_binary": settings.skip_binary,
        "scan_documents": settings.scan_documents,
        "max_file_bytes": settings.max_file_bytes,
        "workers": settings.workers,
        "context_chars": settings.context_chars,
        "max_hits_per_file": settings.max_hits_per_file,
        "use_processes": settings.use_processes,
        "encodings": list(settings.encodings),
        "ruleset": ruleset_to_mapping(settings.ruleset),
        "filter_rule": settings.filter_rule,
        "filter_path": settings.filter_path,
        "report_format": settings.report_format,
        "geometry": settings.geometry,
    }


def _bool_value(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    """读取布尔字段；类型不符时回落默认值。"""
    value = raw.get(key, default)
    return value if isinstance(value, bool) else default


def _int_value(raw: Mapping[str, Any], key: str, default: int, *, minimum: int = 0) -> int:
    """读取整数字段；类型不符或越界时回落默认值。"""
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, value)


def _str_value(raw: Mapping[str, Any], key: str, default: str = "") -> str:
    """读取字符串字段；类型不符时回落默认值。"""
    value = raw.get(key, default)
    return value if isinstance(value, str) else default


def _format_value(raw: Mapping[str, Any]) -> str:
    """读取导出报告格式；非法取值回落 ``text``。"""
    value = raw.get("report_format", "text")
    return value if value in REPORT_FORMATS else "text"


def _encodings_value(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """读取编码候选；非列表或全为空时回落默认值。"""
    stored = raw.get("encodings")
    if not isinstance(stored, list):
        return DEFAULT_ENCODINGS
    items = tuple(item for item in stored if isinstance(item, str) and item.strip())
    return items or DEFAULT_ENCODINGS


def _groups_value(raw: Mapping[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """读取白名单「类型 → 后缀」分组；结构非法、重名或内容为空的条目逐项丢弃。"""
    stored = raw.get("whitelist_groups")
    if not isinstance(stored, list):
        return ()
    groups: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for item in stored:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        label, exts = item
        if not isinstance(label, str) or not isinstance(exts, (list, tuple)):
            continue
        name = label.strip()
        if not name or name in seen:
            continue
        valid, _ = validate_exts([ext for ext in exts if isinstance(ext, str)])
        if not valid:
            continue
        groups.append((name, valid))
        seen.add(name)
    return tuple(groups)


def settings_from_mapping(raw: Mapping[str, Any]) -> FilescanSettings:
    """从字典恢复设置；缺失或非法字段逐项回落默认值，规则集损坏时载入内置规则。"""
    paths_raw = raw.get("paths", [])
    paths = tuple(item for item in paths_raw if isinstance(item, str)) if isinstance(paths_raw, list) else ()
    ruleset = builtin_ruleset()
    stored = raw.get("ruleset")
    if isinstance(stored, Mapping):
        try:
            ruleset = ruleset_from_mapping(stored)
        except ValueError:
            ruleset = builtin_ruleset()
    return FilescanSettings(
        paths=paths,
        recursive=_bool_value(raw, "recursive", True),
        max_depth=_int_value(raw, "max_depth", 0),
        follow_symlinks=_bool_value(raw, "follow_symlinks", False),
        ext_whitelist=_str_value(raw, "ext_whitelist", ";".join(DEFAULT_SCAN_EXTENSIONS)),
        whitelist_groups=_groups_value(raw),
        include_ext=_str_value(raw, "include_ext"),
        force_ext=_str_value(raw, "force_ext"),
        exclude_ext=_str_value(raw, "exclude_ext"),
        min_size=_int_value(raw, "min_size", 0),
        max_size=_int_value(raw, "max_size", 0),
        modified_after=_str_value(raw, "modified_after"),
        modified_before=_str_value(raw, "modified_before"),
        skip_binary=_bool_value(raw, "skip_binary", True),
        scan_documents=_bool_value(raw, "scan_documents", True),
        max_file_bytes=_int_value(raw, "max_file_bytes", DEFAULT_MAX_FILE_BYTES, minimum=1),
        workers=_int_value(raw, "workers", 4, minimum=1),
        context_chars=_int_value(raw, "context_chars", 40),
        max_hits_per_file=_int_value(raw, "max_hits_per_file", 200, minimum=1),
        use_processes=_bool_value(raw, "use_processes", True),
        encodings=_encodings_value(raw),
        ruleset=ruleset,
        filter_rule=_str_value(raw, "filter_rule"),
        filter_path=_str_value(raw, "filter_path"),
        report_format=_format_value(raw),
        geometry=_guiconfig.valid_geometry(_str_value(raw, "geometry") or None),
    )


def config_path() -> Path:
    """返回设置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/filescanw.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> FilescanSettings:
    """读取设置；文件缺失或损坏时返回默认设置。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return FilescanSettings()
    return settings_from_mapping(raw)


def save_settings(settings: FilescanSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆位置，越界或非法时回落到默认 geometry。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def apply_expression(ruleset: RuleSet, text: str) -> RuleSet:
    """校验并应用表达式文本；非法时抛 ``ValueError``，空串表示恢复默认 OR。"""
    expression = text.strip()
    if expression:
        parse_expression(expression, ruleset.rule_ids())
    return replace(ruleset, expression=expression)


def _ext_group(text: str) -> tuple[str, ...]:
    """把空格分隔的后缀串转成元组（仅用于白名单分组定义）。"""
    return normalize_exts(text.split())


WHITELIST_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("文档", DOCUMENT_EXTENSIONS),
    ("文本/日志", _ext_group(".txt .text .md .markdown .rst .log")),
    (
        "数据/结构化",
        _ext_group(".csv .tsv .json .jsonl .xml .yaml .yml .toml .proto .graphql .gql .hcl .tf .tfvars"),
    ),
    ("配置", _ext_group(".ini .cfg .conf .config .env .properties .plist .reg .lock")),
    (
        "Web/前端",
        _ext_group(".html .htm .css .scss .sass .less .js .mjs .cjs .jsx .ts .tsx .vue .svelte"),
    ),
    (
        "编程语言",
        _ext_group(
            ".py .pyi .pyw .java .kt .kts .c .h .cc .cpp .cxx .hpp .hh .cs .go .rs .rb .php .pl .pm "
            ".lua .swift .m .mm .scala .dart .r .jl .ex .exs .erl .hrl .clj .cljs .hs .lisp .el .asm "
            ".s .sql .vbs .ahk"
        ),
    ),
    (
        "脚本/构建",
        _ext_group(".sh .bash .zsh .fish .ps1 .psm1 .psd1 .bat .cmd .gradle .groovy .cmake .mk"),
    ),
    ("出版/补丁", _ext_group(".tex .bib .diff .patch")),
)
"""白名单展示分组：仅用于界面排版，扫描仍按白名单整体匹配。"""

_WHITELIST_SPLIT = re.compile(r"[;,\s]+")


def parse_whitelist_text(text: str) -> tuple[str, ...]:
    """解析白名单编辑器文本：``#`` 开头为分组注释，其余按空白/分号/逗号切分。"""
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        items.extend(_WHITELIST_SPLIT.split(stripped))
    return normalize_exts(items)


def group_whitelist(
    exts: Iterable[str] = DEFAULT_SCAN_EXTENSIONS,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """把后缀按类别归类成列表分组；未归类项落入「其他」，空分组不返回。"""
    remaining = list(dict.fromkeys(normalize_exts(tuple(exts))))
    groups: list[tuple[str, tuple[str, ...]]] = []
    for label, known in WHITELIST_GROUPS:
        present = tuple(item for item in known if item in remaining)
        if present:
            groups.append((label, present))
            remaining = [item for item in remaining if item not in present]
    if remaining:
        groups.append(("其他", tuple(remaining)))
    return tuple(groups)


_EXT_TOKEN = re.compile(r"\.?[a-z0-9][a-z0-9._+-]*")
_EXT_SEPARATORS = re.compile(r"[,，]")


def _is_valid_ext_token(token: str) -> bool:
    """单个后缀是否有效：``*``、``.log``、``log``、``*.log`` 合法，含空格/中文等非法。"""
    value = token.strip().lower().removeprefix("*")
    if token.strip() == "*":
        return True
    return bool(_EXT_TOKEN.fullmatch(value))


def validate_exts(exts: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """校验后缀条目，返回 ``(有效后缀, 非法条目)``；有效项已规范化去重。"""
    valid: list[str] = []
    invalid: list[str] = []
    for raw in exts:
        token = str(raw).strip()
        if not token:
            continue
        if _is_valid_ext_token(token):
            valid.append(token)
        else:
            invalid.append(token)
    return normalize_exts(valid), tuple(invalid)


def parse_and_validate_ext_line(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """按英文/中文逗号拆分一行后缀并校验，返回 ``(有效后缀, 非法条目)``。"""
    return validate_exts(_EXT_SEPARATORS.split(text))


# ---------------------------------------------------------------------- #
# 窗口
# ---------------------------------------------------------------------- #
class FilescanWindow:  # pragma: no cover - 需要真实 Tk 窗口
    """主窗口：扫描 / 结果 / 规则三个分页、后台扫描与设置持久化。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk

        self.settings_path = settings_path or config_path()
        self.settings = load_settings(self.settings_path)
        self.root, self.theme = _guiwindow.create_root(
            title="敏感内容扫描 - filescan",
            saved_geometry=self.settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._on_close,
        )
        self.ruleset = self.settings.ruleset
        self.result: ScanResult | None = None
        self.token: CancelToken | None = None
        self.worker: threading.Thread | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self._paths: list[str] = list(self.settings.paths)
        self._progress = tk.DoubleVar(value=0.0)
        self._status = tk.StringVar(value="就绪")
        self._recursive = tk.BooleanVar(value=self.settings.recursive)
        self._follow = tk.BooleanVar(value=self.settings.follow_symlinks)
        self._skip_binary = tk.BooleanVar(value=self.settings.skip_binary)
        self._scan_documents = tk.BooleanVar(value=self.settings.scan_documents)
        self._export_raw = tk.BooleanVar(value=False)
        self._modified_after = tk.StringVar(value=self.settings.modified_after)
        self._modified_before = tk.StringVar(value=self.settings.modified_before)
        self._max_depth = tk.IntVar(value=self.settings.max_depth)
        self._min_size = tk.IntVar(value=self.settings.min_size)
        self._max_size = tk.IntVar(value=self.settings.max_size)
        self._max_file_bytes = tk.IntVar(value=self.settings.max_file_bytes)
        self._workers = tk.IntVar(value=self.settings.workers)
        self._context_chars = tk.IntVar(value=self.settings.context_chars)
        self._include_ext = tk.StringVar(value=self.settings.include_ext)
        self._force_ext = tk.StringVar(value=self.settings.force_ext)
        self._whitelist_notice = ""
        self._exclude_ext = tk.StringVar(value=self.settings.exclude_ext)
        self._filter_rule = tk.StringVar(value="全部")
        self._filter_path = tk.StringVar(value=self.settings.filter_path)
        self._expression = tk.StringVar()
        self._rule_hint = tk.StringVar(value="")
        self._dirs_list: Any = None
        self._notebook: Any = None
        self._result_page: Any = None
        self._tree: ttk.Treeview | None = None
        self._rules_tree: ttk.Treeview | None = None
        self._filter_box: ttk.Combobox | None = None

        _guiwindow.build_menu(self.root, on_close=self._on_close, on_about=self._show_about)
        self._build_ui()
        self._refresh_dirs()
        self._refresh_rules()
        self._status.set(self._whitelist_notice or self._summary_text())

    # -- 界面 --------------------------------------------------------- #
    def _build_ui(self) -> None:
        from tkinter import ttk

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 0))
        scan_page = ttk.Frame(notebook, padding=8)
        result_page = ttk.Frame(notebook, padding=8)
        rules_page = ttk.Frame(notebook, padding=8)
        notebook.add(scan_page, text="扫描")
        notebook.add(result_page, text="结果")
        notebook.add(rules_page, text="规则")
        self._notebook = notebook
        self._result_page = result_page
        self._build_scan_page(scan_page)
        self._build_result_page(result_page)
        self._build_rules_page(rules_page)
        ttk.Label(self.root, textvariable=self._status, anchor="w").grid(row=1, column=0, sticky="ew", padx=10, pady=6)

    def _build_scan_page(self, page: Any) -> None:
        import tkinter as tk
        from tkinter import ttk

        page.columnconfigure(0, weight=1)

        dirs_box = ttk.LabelFrame(page, text="扫描目录", padding=6)
        dirs_box.grid(row=0, column=0, sticky="ew")
        dirs_box.columnconfigure(0, weight=1)
        listbox = tk.Listbox(dirs_box, height=4, selectmode="extended")
        listbox.grid(row=0, column=0, rowspan=4, sticky="ew")
        scroll = ttk.Scrollbar(dirs_box, orient="vertical", command=listbox.yview)
        scroll.grid(row=0, column=1, rowspan=4, sticky="ns")
        listbox.configure(yscrollcommand=scroll.set)
        self._dirs_list = listbox
        for row, (text, command) in enumerate(
            (
                ("添加目录…", self._add_directory),
                ("移除选中", self._remove_directory),
                ("清空", self._clear_directories),
            )
        ):
            ttk.Button(dirs_box, text=text, command=command).grid(row=row, column=2, sticky="ew", padx=(6, 0), pady=2)

        options = ttk.LabelFrame(page, text="扫描选项", padding=6)
        options.grid(row=1, column=0, sticky="ew", pady=6)
        for column in (1, 3, 5, 7):
            options.columnconfigure(column, weight=1)
        ttk.Checkbutton(options, text="递归子目录", variable=self._recursive).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(options, text="跟随符号链接", variable=self._follow).grid(row=0, column=2, sticky="w")
        ttk.Checkbutton(options, text="跳过二进制", variable=self._skip_binary).grid(row=0, column=4, sticky="w")
        ttk.Checkbutton(options, text="解析文档", variable=self._scan_documents).grid(row=0, column=6, sticky="w")
        self._labeled_entry(options, 1, 0, "深度(0=不限)", self._max_depth, width=8)
        self._labeled_entry(options, 2, 0, "最小字节", self._min_size, width=8)
        self._labeled_entry(options, 3, 0, "最大字节(0=不限)", self._max_size, width=10)
        self._labeled_entry(options, 1, 2, "后缀包含", self._include_ext, width=18)
        self._labeled_entry(options, 2, 2, "后缀排除", self._exclude_ext, width=18)
        self._labeled_entry(options, 3, 2, "单文件上限", self._max_file_bytes, width=12)
        self._labeled_entry(options, 1, 4, "并发数", self._workers, width=6)
        self._labeled_entry(options, 2, 4, "上下文长度", self._context_chars, width=8)
        self._labeled_entry(options, 3, 4, "强制后缀", self._force_ext, width=12)
        self._labeled_entry(options, 1, 6, "修改时间≥", self._modified_after, width=18)
        self._labeled_entry(options, 2, 6, "修改时间≤", self._modified_before, width=18)
        ttk.Label(options, text="白名单外强制扫描（如 .pdf）").grid(row=3, column=6, columnspan=2, sticky="w")
        ttk.Label(
            options,
            text="后缀白名单（每行一个类型；双击行编辑，后缀用英文逗号【,】分隔；可新增 / 删除类型）",
        ).grid(row=4, column=0, columnspan=8, sticky="w", padx=(6, 2), pady=(4, 0))
        whitelist_box = ttk.Frame(options)
        whitelist_box.grid(row=5, column=0, columnspan=8, sticky="ew")
        whitelist_box.columnconfigure(0, weight=1)
        whitelist_tree = ttk.Treeview(
            whitelist_box, columns=("group", "exts"), show="headings", height=8, selectmode="browse"
        )
        whitelist_tree.heading("group", text="类型")
        whitelist_tree.heading("exts", text="后缀（用 , 分隔）")
        whitelist_tree.column("group", width=120, stretch=False)
        whitelist_tree.column("exts", width=560, stretch=True)
        whitelist_tree.grid(row=0, column=0, sticky="nsew")
        whitelist_scroll = ttk.Scrollbar(whitelist_box, orient="vertical", command=whitelist_tree.yview)
        whitelist_scroll.grid(row=0, column=1, sticky="ns")
        whitelist_tree.configure(yscrollcommand=whitelist_scroll.set)
        whitelist_tree.bind("<Double-1>", self._edit_whitelist_row)
        self._whitelist_tree = whitelist_tree
        if self.settings.whitelist_groups:
            self._refresh_whitelist_tree(self.settings.whitelist_groups)
        else:
            loaded, invalid = validate_exts(parse_whitelist_text(self.settings.ext_whitelist))
            self._refresh_whitelist_tree(group_whitelist(loaded))
            if invalid:
                self._whitelist_notice = f"白名单中 {'、'.join(invalid)} 无效，已忽略"
        buttons = ttk.Frame(whitelist_box)
        buttons.grid(row=0, column=2, sticky="nsw", padx=(6, 0))
        ttk.Button(buttons, text="新增类型", command=lambda: self._whitelist_dialog(None)).pack(anchor="w", fill="x")
        ttk.Button(buttons, text="编辑选中", command=self._edit_whitelist_row).pack(anchor="w", fill="x", pady=2)
        ttk.Button(buttons, text="删除选中", command=self._delete_whitelist_row).pack(anchor="w", fill="x")
        ttk.Button(buttons, text="恢复默认", command=self._reset_whitelist).pack(anchor="w", fill="x", pady=(8, 0))

        actions = ttk.Frame(page)
        actions.grid(row=2, column=0, sticky="ew")
        actions.columnconfigure(2, weight=1)
        self._start_button = ttk.Button(actions, text="开始扫描", style="Accent.TButton", command=self._start_scan)
        self._start_button.grid(row=0, column=0)
        self._cancel_button = ttk.Button(actions, text="取消", command=self._cancel_scan, state="disabled")
        self._cancel_button.grid(row=0, column=1, padx=6)
        ttk.Progressbar(actions, variable=self._progress, maximum=100).grid(row=0, column=2, sticky="ew", padx=6)
        ttk.Button(actions, text="导入配置…", command=self._import_config).grid(row=0, column=3, padx=(6, 0))
        ttk.Button(actions, text="导出配置…", command=self._export_config).grid(row=0, column=4, padx=(6, 0))

    def _build_result_page(self, page: Any) -> None:
        from tkinter import ttk

        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        result_box = ttk.LabelFrame(page, text="扫描结果", padding=6)
        result_box.grid(row=0, column=0, sticky="nsew")
        result_box.columnconfigure(0, weight=1)
        result_box.rowconfigure(0, weight=1)
        tree = ttk.Treeview(result_box, columns=("rule", "line", "context"), show="tree headings")
        tree.heading("#0", text="文件 / 规则")
        tree.heading("rule", text="规则")
        tree.heading("line", text="行号")
        tree.heading("context", text="上下文（打码）")
        tree.column("#0", width=420, stretch=True)
        tree.column("rule", width=140, stretch=False)
        tree.column("line", width=60, stretch=False)
        tree.column("context", width=520, stretch=True)
        tree.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(result_box, orient="vertical", command=tree.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(result_box, orient="horizontal", command=tree.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self._tree = tree

        filter_bar = ttk.Frame(page)
        filter_bar.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(filter_bar, text="规则筛选").pack(side="left")
        self._filter_box = ttk.Combobox(filter_bar, textvariable=self._filter_rule, width=18, state="readonly")
        self._filter_box.pack(side="left", padx=6)
        ttk.Label(filter_bar, text="路径包含").pack(side="left", padx=(12, 0))
        ttk.Entry(filter_bar, textvariable=self._filter_path, width=32).pack(side="left", padx=6)
        ttk.Button(filter_bar, text="重置", command=self._reset_filter).pack(side="left")
        ttk.Button(filter_bar, text="导出报告…", command=self._export_report).pack(side="right")
        ttk.Button(filter_bar, text="应用筛选", command=self._apply_filter).pack(side="right", padx=(0, 6))
        ttk.Checkbutton(filter_bar, text="导出原文", variable=self._export_raw).pack(side="right", padx=(0, 12))

    def _labeled_entry(  # noqa: PLR0913 - 布局辅助，行列/文案/变量/宽度一一对应
        self, parent: Any, row: int, column: int, text: str, variable: Any, *, width: int
    ) -> None:
        from tkinter import ttk

        ttk.Label(parent, text=text).grid(row=row, column=column, sticky="e", padx=(6, 2), pady=2)
        ttk.Entry(parent, textvariable=variable, width=width).grid(row=row, column=column + 1, sticky="ew", pady=2)

    def _build_rules_page(self, page: Any) -> None:
        from tkinter import ttk

        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)
        tree = ttk.Treeview(page, columns=("enabled", "group", "name", "matcher"), show="headings")
        for key, title, width in (
            ("enabled", "启用", 60),
            ("group", "分组", 110),
            ("name", "名称", 170),
            ("matcher", "匹配器", 560),
        ):
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=key == "matcher")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(page, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        self._rules_tree = tree

        buttons = ttk.Frame(page)
        buttons.grid(row=1, column=0, columnspan=2, sticky="ew", pady=6)
        for text, command in (
            ("新增规则", self._add_rule),
            ("编辑规则", self._edit_rule),
            ("删除规则", self._delete_rule),
            ("启用", lambda: self._set_rules_enabled(True)),
            ("禁用", lambda: self._set_rules_enabled(False)),
            ("载入内置", self._load_builtin),
            ("清空", self._clear_rules),
            ("导入…", self._import_rules),
            ("导出…", self._export_rules),
        ):
            ttk.Button(buttons, text=text, command=command).pack(side="left", padx=(0, 4))

        expression_box = ttk.LabelFrame(
            page, text="组合表达式（and / or / not，括号可嵌套；留空表示 OR 全部启用规则）", padding=6
        )
        expression_box.grid(row=2, column=0, columnspan=2, sticky="ew")
        expression_box.columnconfigure(0, weight=1)
        ttk.Entry(expression_box, textvariable=self._expression).grid(row=0, column=0, sticky="ew")
        ttk.Button(expression_box, text="应用", command=self._apply_expression).grid(row=0, column=1, padx=6)
        ttk.Button(expression_box, text="校验", command=self._validate_expression).grid(row=0, column=2)
        ttk.Label(expression_box, textvariable=self._rule_hint, foreground="#888888").grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

    # -- 数据刷新 ----------------------------------------------------- #
    def _refresh_dirs(self) -> None:
        if self._dirs_list is None:
            return
        self._dirs_list.delete(0, "end")
        for path in self._paths:
            self._dirs_list.insert("end", path)

    def _refresh_rules(self) -> None:
        if self._rules_tree is None:
            return
        self._rules_tree.delete(*self._rules_tree.get_children())
        for rule in self.ruleset.rules:
            self._rules_tree.insert(
                "",
                "end",
                iid=rule.id,
                values=("是" if rule.enabled else "否", rule.group, rule.name, rule.matcher.describe()),
            )
        self._expression.set(self.ruleset.expression)
        self._rule_hint.set(
            f"{len(self.ruleset.rules)} 条规则，启用 {self.ruleset.enabled_count()}；"
            f"生效表达式：{self.ruleset.expression.strip() or default_expression(self.ruleset) or '（无）'}"
        )
        if self._filter_box is not None:
            rule_ids = ["全部", *[rule.id for rule in self.ruleset.rules]]
            self._filter_box.configure(values=rule_ids)
            if self._filter_rule.get() not in rule_ids:
                self._filter_rule.set("全部")

    def _summary_text(self) -> str:
        return (
            f"规则 {len(self.ruleset.rules)} 条（启用 {self.ruleset.enabled_count()}）；"
            f"目录 {len(self._paths)} 个；单文件上限 {format_size(self._max_file_bytes.get())}"
        )

    def _sync_ruleset(self, ruleset: RuleSet, *, message: str) -> None:
        self.ruleset = ruleset
        self._refresh_rules()
        self._status.set(message)

    # -- 目录 --------------------------------------------------------- #
    def _add_directory(self) -> None:
        from tkinter import filedialog

        chosen = filedialog.askdirectory(title="选择要扫描的目录")
        if not chosen:
            return
        if chosen not in self._paths:
            self._paths.append(chosen)
            self._refresh_dirs()
            self._status.set(self._summary_text())

    def _remove_directory(self) -> None:
        if self._dirs_list is None:
            return
        selected = sorted((int(index) for index in self._dirs_list.curselection()), reverse=True)
        for index in selected:
            if 0 <= index < len(self._paths):
                self._paths.pop(index)
        self._refresh_dirs()
        self._status.set(self._summary_text())

    def _clear_directories(self) -> None:
        self._paths.clear()
        self._refresh_dirs()
        self._status.set(self._summary_text())

    # -- 规则编辑 ----------------------------------------------------- #
    def _selected_rule(self) -> Rule | None:
        if self._rules_tree is None:
            return None
        selected = self._rules_tree.selection()
        return self.ruleset.find(selected[0]) if selected else None

    def _add_rule(self) -> None:
        self._rule_dialog(None)

    def _edit_rule(self) -> None:
        rule = self._selected_rule()
        if rule is None:
            self._status.set("请先在规则表中选择一条规则")
            return
        self._rule_dialog(rule)

    def _rule_dialog(self, rule: Rule | None) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        window = tk.Toplevel(self.root)
        window.title("编辑规则" if rule else "新增规则")
        window.transient(self.root)
        window.grab_set()
        body = ttk.Frame(window, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        fields = {
            "id": tk.StringVar(value=rule.id if rule else ""),
            "name": tk.StringVar(value=rule.name if rule else ""),
            "group": tk.StringVar(value=rule.group if rule else "自定义"),
            "kind": tk.StringVar(value=rule.matcher.kind if rule else MATCH_KEYWORD),
            "text": tk.StringVar(value=rule.matcher.text if rule else ""),
            "pattern": tk.StringVar(value=rule.matcher.pattern if rule else ""),
            "case_sensitive": tk.BooleanVar(value=rule.matcher.is_case_sensitive() if rule else False),
            "whole_word": tk.BooleanVar(value=rule.matcher.whole_word if rule else False),
            "enabled": tk.BooleanVar(value=rule.enabled if rule else True),
        }
        for row, (label, key) in enumerate(
            (("规则 id", "id"), ("名称", "name"), ("分组", "group"), ("关键词", "text"), ("正则", "pattern"))
        ):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="e", padx=(0, 6), pady=3)
            ttk.Entry(body, textvariable=fields[key]).grid(row=row, column=1, sticky="ew", pady=3)
        ttk.Label(body, text="类型").grid(row=5, column=0, sticky="e", padx=(0, 6))
        ttk.Combobox(body, textvariable=fields["kind"], values=[MATCH_KEYWORD, MATCH_REGEX], state="readonly").grid(
            row=5, column=1, sticky="ew"
        )
        flags = ttk.Frame(body)
        flags.grid(row=6, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Checkbutton(flags, text="区分大小写", variable=fields["case_sensitive"]).pack(side="left")
        ttk.Checkbutton(flags, text="整词匹配（关键词）", variable=fields["whole_word"]).pack(side="left", padx=8)
        ttk.Checkbutton(flags, text="启用", variable=fields["enabled"]).pack(side="left")

        def commit() -> None:
            kind = fields["kind"].get()
            matcher = Matcher(
                kind=kind,
                text=fields["text"].get().strip() if kind == MATCH_KEYWORD else "",
                pattern=fields["pattern"].get().strip() if kind == MATCH_REGEX else "",
                case_sensitive=fields["case_sensitive"].get(),
                whole_word=fields["whole_word"].get(),
            )
            candidate = Rule(
                id=fields["id"].get().strip(),
                name=fields["name"].get().strip(),
                matcher=matcher,
                group=fields["group"].get().strip() or "自定义",
                enabled=fields["enabled"].get(),
            )
            try:
                candidate.validate()
                duplicated = any(
                    item.id == candidate.id and (rule is None or item.id != rule.id) for item in self.ruleset.rules
                )
                if duplicated:
                    raise ValueError(f"规则 id 已存在: {candidate.id}")
                rules = tuple(candidate if item.id == candidate.id else item for item in self.ruleset.rules)
                if not any(item.id == candidate.id for item in self.ruleset.rules):
                    rules = (*self.ruleset.rules, candidate)
                new_set = replace(self.ruleset, rules=rules)
                resolve_expression(new_set)
            except ValueError as exc:
                messagebox.showerror("规则无效", str(exc), parent=window)
                return
            window.destroy()
            self._sync_ruleset(new_set, message=f"已保存规则 {candidate.id}")

        ttk.Button(body, text="保存", command=commit).grid(row=7, column=0, pady=(8, 0))
        ttk.Button(body, text="取消", command=window.destroy).grid(row=7, column=1, pady=(8, 0), sticky="e")

    def _delete_rule(self) -> None:
        from tkinter import messagebox

        rule = self._selected_rule()
        if rule is None:
            self._status.set("请先在规则表中选择一条规则")
            return
        if not messagebox.askyesno("删除规则", f"确定删除规则 {rule.id}（{rule.name}）？", parent=self.root):
            return
        try:
            new_set = self.ruleset.remove_rule(rule.id)
        except ValueError as exc:
            messagebox.showerror("无法删除", str(exc), parent=self.root)
            return
        self._sync_ruleset(new_set, message=f"已删除规则 {rule.id}")

    def _set_rules_enabled(self, enabled: bool) -> None:
        if self._rules_tree is None:
            return
        selected = set(self._rules_tree.selection())
        if not selected:
            self._status.set("请先在规则表中选择规则")
            return
        rules = tuple(replace(rule, enabled=enabled) if rule.id in selected else rule for rule in self.ruleset.rules)
        self._sync_ruleset(
            replace(self.ruleset, rules=rules), message=f"已{'启用' if enabled else '禁用'} {len(selected)} 条规则"
        )

    def _load_builtin(self) -> None:
        from tkinter import messagebox

        if self.ruleset.rules and not messagebox.askyesno(
            "载入内置规则", "当前规则将被内置规则覆盖，继续？", parent=self.root
        ):
            return
        self._sync_ruleset(builtin_ruleset(), message="已载入内置敏感规则")

    def _clear_rules(self) -> None:
        from tkinter import messagebox

        if not messagebox.askyesno("清空规则", "确定清空全部规则与表达式？", parent=self.root):
            return
        self._sync_ruleset(replace(self.ruleset, rules=(), expression=""), message="规则已清空")

    def _import_rules(self) -> None:
        from tkinter import filedialog, messagebox

        chosen = filedialog.askopenfilename(title="导入规则集", filetypes=[("JSON", "*.json"), ("全部文件", "*.*")])
        if not chosen:
            return
        from fcmd.apps.filescan._rules import load_ruleset

        try:
            imported = load_ruleset(Path(chosen))
        except ValueError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return
        self._sync_ruleset(imported, message=f"已导入规则集（{len(imported.rules)} 条）")

    def _export_rules(self) -> None:
        from tkinter import filedialog, messagebox

        chosen = filedialog.asksaveasfilename(
            title="导出规则集",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="filescan-rules.json",
        )
        if not chosen:
            return
        try:
            save_ruleset(self.ruleset, Path(chosen))
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self._status.set(f"规则集已导出到 {chosen}")

    def _apply_expression(self) -> None:
        from tkinter import messagebox

        try:
            updated = apply_expression(self.ruleset, self._expression.get())
        except ValueError as exc:
            messagebox.showerror("表达式无效", str(exc), parent=self.root)
            return
        self._sync_ruleset(updated, message="表达式已应用")

    def _validate_expression(self) -> None:
        from tkinter import messagebox

        try:
            expression = self._expression.get().strip()
            resolve_expression(replace(self.ruleset, expression=expression))
        except ValueError as exc:
            messagebox.showerror("表达式无效", str(exc), parent=self.root)
            return
        messagebox.showinfo("表达式有效", "表达式解析通过", parent=self.root)

    # -- 扫描 --------------------------------------------------------- #
    def _whitelist_rows(self) -> list[tuple[str, tuple[str, ...]]]:
        """读取白名单表格：``[(类型, 该行后缀), ...]``。"""
        if self._whitelist_tree is None:
            return []
        rows: list[tuple[str, tuple[str, ...]]] = []
        for iid in self._whitelist_tree.get_children(""):
            label = str(self._whitelist_tree.set(iid, "group"))
            exts, _ = parse_and_validate_ext_line(str(self._whitelist_tree.set(iid, "exts")))
            rows.append((label, exts))
        return rows

    def _whitelist_exts(self) -> tuple[str, ...]:
        """汇总表格中所有类型的有效后缀。"""
        items = [ext for _, exts in self._whitelist_rows() for ext in exts]
        return normalize_exts(items)

    def _refresh_whitelist_tree(self, groups: Iterable[tuple[str, Iterable[str]]]) -> None:
        """按「类型 → 后缀」重建白名单表格；空数据时回落到默认分组。"""
        if self._whitelist_tree is None:
            return
        tree = self._whitelist_tree
        rows: list[tuple[str, tuple[str, ...]]] = []
        for label, exts in groups:
            valid, _ = validate_exts(exts)
            if valid:
                rows.append((str(label).strip(), valid))
        if not rows:
            rows = list(group_whitelist(DEFAULT_SCAN_EXTENSIONS))
        tree.delete(*tree.get_children(""))
        for index, (label, exts) in enumerate(rows):
            tree.insert("", "end", iid=f"row{index}", values=(label, ",".join(exts)))

    def _selected_whitelist_index(self, event: Any = None) -> int | None:
        """返回当前选中（或双击）行在表格中的下标。"""
        if self._whitelist_tree is None:
            return None
        tree = self._whitelist_tree
        iid = tree.identify_row(event.y) if event is not None else ""
        if not iid:
            selection = tree.selection()
            if not selection:
                self._status.set("请先在白名单表格中选择一行")
                return None
            iid = selection[0]
        label = str(tree.set(iid, "group"))
        rows = self._whitelist_rows()
        return next((index for index, (row_label, _) in enumerate(rows) if row_label == label), None)

    def _delete_whitelist_row(self) -> None:
        from tkinter import messagebox

        index = self._selected_whitelist_index()
        if index is None:
            return
        rows = self._whitelist_rows()
        if len(rows) <= 1:
            self._status.set("白名单至少保留一个类型，请先新增其他类型")
            return
        label, exts = rows[index]
        if not messagebox.askyesno("删除类型", f"确认删除类型「{label}」及其 {len(exts)} 个后缀？", parent=self.root):
            return
        self._refresh_whitelist_tree([row for i, row in enumerate(rows) if i != index])
        self._status.set(f"已删除类型「{label}」")

    def _edit_whitelist_row(self, event: Any = None) -> None:
        """双击表格行或点击「编辑选中」时，弹窗编辑该行的类型名与后缀串。"""
        index = self._selected_whitelist_index(event)
        if index is None:
            return
        self._whitelist_dialog(index)

    def _whitelist_dialog(self, index: int | None) -> None:
        """新增（``index is None``）或编辑指定行；类型名与后缀均在提交时校验。"""
        from tkinter import StringVar, Toplevel, ttk

        rows = self._whitelist_rows()
        current_label, current_exts = rows[index] if index is not None else ("", ())
        window = Toplevel(self.root)
        window.title("编辑白名单类型" if index is not None else "新增白名单类型")
        window.transient(self.root)
        window.resizable(False, False)
        _guiwindow.center_over_parent(window, self.root, width=680, height=230)
        body = ttk.Frame(window, padding=10)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="类型").grid(row=0, column=0, sticky="e", padx=(0, 6))
        label_value = StringVar(value=current_label)
        label_entry = ttk.Entry(body, textvariable=label_value, width=24)
        label_entry.grid(row=0, column=1, sticky="w", pady=2)

        ttk.Label(body, text="后缀").grid(row=1, column=0, sticky="e", padx=(0, 6))
        value = StringVar(value=",".join(current_exts))
        ext_entry = ttk.Entry(body, textvariable=value, width=64)
        ext_entry.grid(row=1, column=1, sticky="ew", pady=2)

        hint = StringVar(value="多个后缀用英文逗号【,】分隔；支持 .log / log / *.log / *")
        ttk.Label(body, textvariable=hint, anchor="w", foreground="#b00020").grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(4, 0)
        )

        def commit() -> None:
            label = label_value.get().strip()
            if not label:
                hint.set("请填写类型名称")
                return
            if any(row_label == label and i != index for i, (row_label, _) in enumerate(rows)):
                hint.set(f"类型「{label}」已存在")
                return
            valid, invalid = parse_and_validate_ext_line(value.get())
            if invalid:
                hint.set(f"无效条目：{'、'.join(invalid)}")
                return
            if not valid:
                hint.set("请至少填写一个有效后缀")
                return
            new_rows: list[tuple[str, tuple[str, ...]]] = []
            for i, (row_label, exts) in enumerate(rows):
                if i == index:
                    new_rows.append((label, valid))
                    continue
                kept = tuple(ext for ext in exts if ext not in valid)
                if kept:
                    new_rows.append((row_label, kept))
            if index is None:
                new_rows.append((label, valid))
            self._refresh_whitelist_tree(new_rows)
            window.destroy()
            self._status.set(f"已保存类型「{label}」：{len(valid)} 个后缀，共 {len(self._whitelist_exts())} 个")

        actions = ttk.Frame(body)
        actions.grid(row=3, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(actions, text="取消", command=window.destroy).pack(side="right")
        ttk.Button(actions, text="确定", command=commit).pack(side="right", padx=(0, 6))
        window.bind("<Return>", lambda _event: commit())
        window.bind("<Escape>", lambda _event: window.destroy())
        label_entry.focus_set()
        label_entry.select_range(0, "end")
        window.grab_set()

    def _scan_options(self) -> ScanOptions:
        after = parse_timestamp(self._modified_after.get())
        before = parse_timestamp(self._modified_before.get())
        if after is not None and before is not None and after > before:
            raise ValueError("修改时间下界（≥）晚于上界（≤），请调整时间过滤")
        return ScanOptions(
            recursive=self._recursive.get(),
            max_depth=max(0, int(self._max_depth.get())),
            follow_symlinks=self._follow.get(),
            ext_whitelist=self._whitelist_exts() or DEFAULT_SCAN_EXTENSIONS,
            include_exts=parse_ext_text(self._include_ext.get()),
            force_exts=parse_ext_text(self._force_ext.get()),
            exclude_exts=parse_ext_text(self._exclude_ext.get()),
            min_size=max(0, int(self._min_size.get())),
            max_size=None if int(self._max_size.get()) <= 0 else int(self._max_size.get()),
            modified_after=after,
            modified_before=before,
            skip_binary=self._skip_binary.get(),
            scan_documents=self._scan_documents.get(),
            max_file_bytes=max(1, int(self._max_file_bytes.get())),
            workers=max(1, int(self._workers.get())),
            context_chars=max(0, int(self._context_chars.get())),
        )

    def _start_scan(self) -> None:
        from tkinter import messagebox

        if self.worker is not None and self.worker.is_alive():
            return
        if not self._paths:
            messagebox.showwarning("缺少目录", "请先添加至少一个扫描目录", parent=self.root)
            return
        try:
            resolve_expression(self.ruleset)
        except ValueError as exc:
            messagebox.showerror("规则表达式无效", str(exc), parent=self.root)
            return
        if not self.ruleset.enabled_rules():
            messagebox.showwarning("没有启用规则", "请先启用至少一条规则", parent=self.root)
            return
        try:
            options = self._scan_options()
        except Exception as exc:  # 输入框转整数失败，统一提示
            messagebox.showerror("扫描选项无效", str(exc), parent=self.root)
            return
        self.result = None
        if self._tree is not None:
            self._tree.delete(*self._tree.get_children())
        self.token = CancelToken()
        self.events = queue.Queue()
        self._progress.set(0.0)
        self._status.set("正在扫描…")
        self._start_button.configure(state="disabled")
        self._cancel_button.configure(state="normal")
        root = self.root
        token = self.token
        ruleset = self.ruleset
        paths = [Path(item) for item in self._paths]
        events = self.events

        def work() -> None:
            try:
                result = scan_paths(
                    paths,
                    ruleset,
                    options,
                    token=token,
                    progress=lambda done, total, path: events.put(("progress", (done, total, str(path)))),
                )
                events.put(("done", result))
            except Exception as exc:  # 后台异常统一回报到界面
                events.put(("error", str(exc)))

        self.worker = _guiwindow.start_worker(root, work, poll=self._poll_events)

    def _poll_events(self) -> None:
        while True:
            try:
                kind, payload = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                done, total, path = payload
                self._progress.set(100.0 * done / max(total, 1))
                self._status.set(f"正在扫描 {done}/{total}：{path}")
            elif kind == "done":
                self._finish_scan(payload)
            elif kind == "error":
                self._finish_scan(None)
                self._status.set(f"扫描失败：{payload}")
        if (self.worker is not None and self.worker.is_alive()) or not self.events.empty():
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll_events)
        else:
            self._start_button.configure(state="normal")
            self._cancel_button.configure(state="disabled")

    def _finish_scan(self, result: ScanResult | None) -> None:
        self.worker = None
        if result is None:
            self._progress.set(0.0)
            return
        self.result = result
        self._render_result(result)
        if self._notebook is not None and self._result_page is not None:
            self._notebook.select(self._result_page)
        stats = result.stats
        state = "已取消" if stats.cancelled else "完成"
        self._progress.set(100.0)
        self._status.set(
            f"扫描{state}：文件 {stats.files_scanned}，命中 {stats.hit_files} 个 / {stats.hit_count} 条，"
            f"跳过 {stats.files_skipped}（后缀 {stats.files_skipped_extension} / 二进制 {stats.files_skipped_binary}），"
            f"耗时 {stats.elapsed:.2f}s（{format_size(int(stats.bytes_per_second))}/s）"
        )
        self._start_button.configure(state="normal")
        self._cancel_button.configure(state="disabled")

    def _render_result(self, result: ScanResult) -> None:
        if self._tree is None:
            return
        tree = self._tree
        tree.delete(*tree.get_children())
        for index, hit in enumerate(result.hits):
            parent = f"file-{index}"
            tree.insert(
                "",
                "end",
                iid=parent,
                text=str(hit.path),
                values=("", "", f"{len(hit.matches)} 条 · {hit.encoding} · {format_size(hit.size)}"),
                open=True,
            )
            for match in hit.matches:
                tree.insert(
                    parent,
                    "end",
                    text=match.rule_name,
                    values=(match.rule_name, match.location or match.line_no, render_context(match, False)),
                )
        for item in result.skipped:
            tree.insert("", "end", text=str(item.path), values=("", "", f"跳过：{item.reason}"))
        for item in result.errors:
            tree.insert("", "end", text=str(item.path), values=("", "", f"错误：{item.message}"))

    def _apply_filter(self) -> None:
        if self.result is None:
            self._status.set("还没有扫描结果")
            return
        selected = self._filter_rule.get()
        rule_ids = None if selected in ("", "全部") else [selected]
        filtered = filter_result(self.result, rule_ids=rule_ids, path_contains=self._filter_path.get())
        self._render_result(filtered)
        self._status.set(f"筛选后命中 {filtered.stats.hit_files} 个文件 / {filtered.stats.hit_count} 条")

    def _reset_filter(self) -> None:
        self._filter_rule.set("全部")
        self._filter_path.set("")
        if self.result is not None:
            self._render_result(self.result)
            self._status.set(self._summary_text())

    def _reset_whitelist(self) -> None:
        self._refresh_whitelist_tree(group_whitelist(DEFAULT_SCAN_EXTENSIONS))
        self._status.set(self._summary_text())

    def _cancel_scan(self) -> None:
        if self.token is not None:
            self.token.cancel()
            self._status.set("正在取消，等待进行中的文件结束…")

    def _export_report(self) -> None:
        from tkinter import filedialog, messagebox

        if self.result is None:
            messagebox.showinfo("没有结果", "请先完成一次扫描", parent=self.root)
            return
        chosen = filedialog.asksaveasfilename(
            title="导出扫描报告",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("JSON", "*.json"), ("文本", "*.txt")],
            initialfile="filescan-report.csv",
        )
        if not chosen:
            return
        suffix = Path(chosen).suffix.lower()
        fmt = {".json": "json", ".txt": "text", ".log": "text"}.get(suffix, "csv")
        try:
            write_report(self.result, Path(chosen), fmt, include_raw=self._export_raw.get())
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        mode = "含原文" if self._export_raw.get() else "已打码"
        self._status.set(f"报告已导出到 {chosen}（{fmt}，{mode}）")

    # -- 扫描配置 ----------------------------------------------------- #
    def _import_config(self) -> None:
        from tkinter import filedialog, messagebox

        chosen = filedialog.askopenfilename(
            title="导入扫描配置",
            filetypes=[("JSON", "*.json"), ("全部文件", "*.*")],
        )
        if not chosen:
            return
        try:
            loaded = load_config(Path(chosen))
        except ValueError as exc:
            messagebox.showerror("导入失败", str(exc), parent=self.root)
            return
        self._apply_config(loaded)
        if loaded.warnings:
            messagebox.showwarning(
                "配置已导入（部分字段回落到默认值）",
                "\n".join(loaded.warnings),
                parent=self.root,
            )
        self._status.set(f"已导入配置：{chosen}（目录 {len(loaded.roots)} 个，规则 {len(self.ruleset.rules)} 条）")

    def _apply_config(self, config: ScanConfig) -> None:
        """把配置回填到各控件；``None`` 规则集表示沿用当前规则。"""
        options = config.options
        self._paths = [str(path) for path in config.roots]
        self._refresh_dirs()
        self._recursive.set(options.recursive)
        self._follow.set(options.follow_symlinks)
        self._skip_binary.set(options.skip_binary)
        self._scan_documents.set(options.scan_documents)
        self._max_depth.set(options.max_depth)
        self._min_size.set(options.min_size)
        self._max_size.set(options.max_size or 0)
        self._max_file_bytes.set(options.max_file_bytes)
        self._workers.set(options.workers)
        self._context_chars.set(options.context_chars)
        self._include_ext.set(";".join(options.include_exts))
        self._force_ext.set(";".join(options.force_exts))
        self._exclude_ext.set(";".join(options.exclude_exts))
        self._modified_after.set(format_timestamp(options.modified_after) or "")
        self._modified_before.set(format_timestamp(options.modified_before) or "")
        self._refresh_whitelist_tree(group_whitelist(options.ext_whitelist))
        if config.ruleset is not None:
            self.ruleset = config.ruleset
            self._refresh_rules()
        self.settings = replace(
            self.settings,
            max_hits_per_file=options.max_hits_per_file,
            use_processes=options.use_processes,
            encodings=options.encodings,
            report_format=config.format,
        )

    def _export_config(self) -> None:
        from tkinter import filedialog, messagebox

        chosen = filedialog.asksaveasfilename(
            title="导出扫描配置",
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialfile="filescan-config.json",
        )
        if not chosen:
            return
        try:
            options = self._scan_options()
        except Exception as exc:  # 输入框转整数/时间失败，统一提示
            messagebox.showerror("扫描选项无效", str(exc), parent=self.root)
            return
        config = ScanConfig(
            roots=tuple(Path(item) for item in self._paths),
            options=options,
            ruleset=self.ruleset,
            format=self.settings.report_format,
            mask=not self._export_raw.get(),
        )
        try:
            save_config_file(config, Path(chosen))
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self.root)
            return
        self._status.set(f"扫描配置已导出到 {chosen}（含规则集与扫描选项）")

    # -- 生命周期 ----------------------------------------------------- #
    def _current_settings(self) -> FilescanSettings:
        return FilescanSettings(
            paths=tuple(self._paths),
            recursive=self._recursive.get(),
            max_depth=max(0, int(self._max_depth.get())),
            follow_symlinks=self._follow.get(),
            ext_whitelist=";".join(self._whitelist_exts()),
            whitelist_groups=tuple(self._whitelist_rows()),
            include_ext=self._include_ext.get(),
            force_ext=self._force_ext.get(),
            exclude_ext=self._exclude_ext.get(),
            min_size=max(0, int(self._min_size.get())),
            max_size=max(0, int(self._max_size.get())),
            modified_after=self._modified_after.get().strip(),
            modified_before=self._modified_before.get().strip(),
            skip_binary=self._skip_binary.get(),
            scan_documents=self._scan_documents.get(),
            max_file_bytes=max(1, int(self._max_file_bytes.get())),
            workers=max(1, int(self._workers.get())),
            context_chars=max(0, int(self._context_chars.get())),
            max_hits_per_file=self.settings.max_hits_per_file,
            use_processes=self.settings.use_processes,
            encodings=self.settings.encodings,
            ruleset=self.ruleset,
            filter_rule="" if self._filter_rule.get() == "全部" else self._filter_rule.get(),
            filter_path=self._filter_path.get(),
            report_format=self.settings.report_format,
            geometry=self.root.winfo_geometry(),
        )

    def _on_close(self) -> None:
        if self.token is not None:
            self.token.cancel()
        if self.worker is not None and self.worker.is_alive():
            self.worker.join(timeout=2.0)
        save_settings(self._current_settings(), self.settings_path)
        self.root.destroy()

    def _show_about(self) -> None:
        from tkinter import messagebox

        messagebox.showinfo(
            "关于 filescanw",
            "敏感内容扫描（filescan）\n\n只读扫描，报告默认打码；\n规则支持关键词/正则与 and·or·not 组合表达式。",
            parent=self.root,
        )


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: FilescanWindow(settings_path), "敏感内容扫描")


@fcmd.tool("filescanw", help="打开敏感内容扫描窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开敏感内容扫描窗口。"""
    run_gui()


@fcmd.main("filescanw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
