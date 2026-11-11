"""llmdownw - ModelScope 模型浏览器 / 下载管理器的 Tk 窗口版。

参考 LM Studio 的「左侧模型列表 + 右侧详情」布局：

* 左侧在 ModelScope 搜索模型（关键字为空时浏览默认排序），选中行拉取详情；
* 右侧展示模型元信息、README、文件清单（大小 / SHA256）与本地管理；
* 底栏配置目标目录、下载源、并发数与校验开关，下载复用 :mod:`fcmd.tools.llm.llmdown`
  的多源选优 / 断点续传实现，支持进度回调与中途停止；
* 「本地管理」页扫描目标目录下 ``<组织>/<模型>``，可打开目录或经确认后删除。

示例
----
    fcmd llmdownw
"""

from __future__ import annotations

import math
import queue
import re
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow
from fcmd.tools.llm.llmbase import human_size
from fcmd.tools.llm.llmdown import (
    DownloadCancelled,
    LocalModel,
    ModelFile,
    ModelInfo,
    ModelSearch,
    ModelSummary,
    ModelToolError,
    ProgressEvent,
    delete_local_model,
    download_files,
    fetch_files,
    fetch_model_info,
    filter_files,
    format_timestamp,
    scan_local_models,
    search_models,
    select_source,
)

__all__ = [
    "DownloadGuiSettings",
    "config_path",
    "detail_pairs",
    "load_settings",
    "local_row",
    "model_row",
    "open_path",
    "parse_patterns",
    "resolve_geometry",
    "save_settings",
]

_DESCRIPTION = (
    "llmdown 的 Tk 窗口版（ModelScope 模型搜索 / 详情 / 下载 / 本地管理；"
    "窗口位置与下载参数记忆到 ~/.fcmd/ui/llmdownw.json）"
)
_MIN_WIDTH = 1080
_MIN_HEIGHT = 720
_DEFAULT_WIDTH = 1280
_DEFAULT_HEIGHT = 880
_PAGE_SIZE = 30
_MAX_WORKERS = 16
_SOURCES = ("modelscope", "hf-mirror", "auto")
_SOURCE_LABELS = {"modelscope": "ModelScope", "hf-mirror": "hf-mirror", "auto": "自动测速"}
_SOURCE_KEYS: dict[str, Literal["modelscope", "hf-mirror", "auto"]] = {
    "ModelScope": "modelscope",
    "hf-mirror": "hf-mirror",
    "自动测速": "auto",
}
_SORT_LABELS = {"default": "默认排序", "modified": "最近更新"}
_SORT_KEYS = {label: key for key, label in _SORT_LABELS.items()}


@dataclass
class DownloadGuiSettings:
    """窗口与下载参数的持久化设置。"""

    geometry: str | None = None
    dest: str = "models"
    source: str = "modelscope"
    workers: int = 4
    verify: bool = True
    include: str = ""
    exclude: str = ""


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/llmdownw.json``。"""
    return _guiconfig.config_path("llmdownw")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> DownloadGuiSettings:
    """读取设置；缺失 / 损坏 / 非法字段逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = DownloadGuiSettings()
    if raw is None:
        return settings

    geometry = raw.get("geometry")
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    dest = raw.get("dest")
    if isinstance(dest, str) and dest.strip():
        settings.dest = dest
    source = raw.get("source")
    if isinstance(source, str) and source in _SOURCES:
        settings.source = source
    workers = raw.get("workers")
    if isinstance(workers, int) and not isinstance(workers, bool):
        settings.workers = min(max(workers, 1), _MAX_WORKERS)
    verify = raw.get("verify")
    if isinstance(verify, bool):
        settings.verify = verify
    for field in ("include", "exclude"):
        value = raw.get(field)
        if isinstance(value, str):
            setattr(settings, field, value)
    return settings


def save_settings(settings: DownloadGuiSettings, path: Path | None = None) -> None:
    """保存设置；写入失败静默忽略（不影响窗口关闭）。"""
    _guiconfig.write_settings(
        {
            "geometry": settings.geometry,
            "dest": settings.dest,
            "source": settings.source,
            "workers": settings.workers,
            "verify": settings.verify,
            "include": settings.include,
            "exclude": settings.exclude,
        },
        path or config_path(),
    )


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=f"{_DEFAULT_WIDTH}x{_DEFAULT_HEIGHT}",
        min_width=_MIN_WIDTH,
        min_height=_MIN_HEIGHT,
    )


def parse_patterns(text: str) -> list[str] | None:
    """把空格 / 逗号 / 分号分隔的掩码文本转换为列表；空文本返回 ``None``。"""
    items = [item for item in re.split(r"[\s,;]+", text.strip()) if item]
    return items or None


def model_row(item: ModelSummary) -> tuple[str, str, str, str, str]:
    """模型列表的一行：仓库、任务、下载量、更新时间、体积。"""
    return (
        item.repo,
        "、".join(item.tasks) or "-",
        f"{item.downloads:,}",
        format_timestamp(item.updated),
        human_size(item.storage_size) if item.storage_size else "-",
    )


def local_row(item: LocalModel) -> tuple[str, str, str, str, str]:
    """本地模型列表的一行：仓库、大小、文件数、状态、路径。"""
    status = "完整" if item.complete else f"未完成（{item.partial} 个 .part）"
    return (item.repo, human_size(item.size), str(item.files), status, str(item.path))


def detail_pairs(info: ModelInfo) -> tuple[tuple[str, str], ...]:
    """把模型详情转换为「字段 / 值」对，供界面与测试复用。"""
    summary = info.summary
    return (
        ("中文名", summary.chinese_name or "-"),
        ("组织", info.organization or summary.owner or "-"),
        ("任务", "、".join(summary.tasks) or "-"),
        ("框架", "、".join(info.frameworks) or "-"),
        ("架构", "、".join(info.architectures) or "-"),
        ("模型类型", "、".join(info.model_types) or "-"),
        ("语言", "、".join(info.languages) or "-"),
        ("许可证", summary.license or "-"),
        ("下载", f"{summary.downloads:,}"),
        ("星标", f"{summary.stars:,}"),
        ("大小", human_size(summary.storage_size) if summary.storage_size else "-"),
        ("版本", info.revision or "-"),
        ("更新时间", format_timestamp(summary.updated)),
        ("推理支持", info.inference or "-"),
    )


def open_path(path: Path) -> None:
    """用系统文件管理器打开目录（Windows / macOS / Linux）。"""
    if sys.platform == "win32":
        import os

        os.startfile(path)
        return
    command = ["open", str(path)] if sys.platform == "darwin" else ["xdg-open", str(path)]
    subprocess.Popen(command)


class DownloadWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """ModelScope 模型浏览、详情与下载管理窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._ttk = ttk
        self._messagebox = messagebox
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self.root, self._theme = _guiwindow.create_root(
            title="模型下载器（ModelScope）",
            saved_geometry=self._settings.geometry,
            default_geometry=f"{_DEFAULT_WIDTH}x{_DEFAULT_HEIGHT}",
            min_width=_MIN_WIDTH,
            min_height=_MIN_HEIGHT,
            on_close=self._close,
        )

        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._jobs = 0
        self._poll_scheduled = False
        self._search_page = 1
        self._search_total = 0
        self._sort_key = "default"
        self._detail_generation = 0
        self._current_repo: str | None = None
        self._current_info: ModelInfo | None = None
        self._current_files: tuple[ModelFile, ...] = ()
        self._local_models: list[LocalModel] = []
        self._downloading = False
        self._stop_event = threading.Event()
        self._download_target = Path("models")

        self._keyword_var = tk.StringVar()
        self._sort_var = tk.StringVar(value=_SORT_LABELS["default"])
        self._page_var = tk.StringVar(value="尚未搜索")
        self._status_var = tk.StringVar(value="就绪")
        self._dest_var = tk.StringVar(value=self._settings.dest)
        self._source_var = tk.StringVar(value=_SOURCE_LABELS[self._settings.source])
        self._workers_var = tk.StringVar(value=str(self._settings.workers))
        self._verify_var = tk.BooleanVar(value=self._settings.verify)
        self._include_var = tk.StringVar(value=self._settings.include)
        self._exclude_var = tk.StringVar(value=self._settings.exclude)
        self._progress_var = tk.StringVar(value="-")
        self._file_var = tk.StringVar(value="-")
        self._title_var = tk.StringVar(value="选择左侧模型查看详情")
        self._subtitle_var = tk.StringVar(value="")
        self._files_total_var = tk.StringVar(value="")
        self._meta_vars: dict[str, Any] = {}

        self._build_widgets()
        _guiwindow.build_menu(self.root, on_close=self._close, on_about=self._about)
        _guitheme.install_theme_switcher(self.root)
        self._refresh_local()
        self._start_search(1)

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        outer = ttk.Frame(self.root, padding=(self._theme.spacing.md, self._theme.spacing.md))
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        self._build_search(outer)
        self._build_body(outer)
        self._build_download(outer)
        self._build_log(outer)

        status = ttk.Label(
            outer,
            textvariable=self._status_var,
            anchor="w",
            relief="sunken",
            padding=(self._theme.spacing.sm, self._theme.spacing.xs),
        )
        status.grid(row=4, column=0, sticky="ew", pady=(self._theme.spacing.sm, 0))

    def _build_search(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.Frame(parent)
        box.grid(row=0, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="搜索 ModelScope").grid(row=0, column=0, sticky="w", padx=(0, self._theme.spacing.sm))
        entry = ttk.Entry(box, textvariable=self._keyword_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, self._theme.spacing.sm))
        entry.bind("<Return>", self._on_search_enter)
        self._search_btn = ttk.Button(box, text="搜索", command=self._on_search, style="Accent.TButton", width=8)
        self._search_btn.grid(row=0, column=2, sticky="w", padx=(0, self._theme.spacing.lg))

        ttk.Label(box, text="排序").grid(row=0, column=3, sticky="w", padx=(0, self._theme.spacing.sm))
        sort_box = ttk.Combobox(
            box,
            textvariable=self._sort_var,
            values=list(_SORT_LABELS.values()),
            state="readonly",
            width=10,
        )
        sort_box.grid(row=0, column=4, sticky="w")
        sort_box.bind("<<ComboboxSelected>>", self._on_sort_changed)

        self._prev_btn = ttk.Button(
            box, text="上一页", command=lambda: self._start_search(self._search_page - 1), width=8
        )
        self._prev_btn.grid(row=0, column=5, sticky="w", padx=(self._theme.spacing.lg, 0))
        self._next_btn = ttk.Button(
            box, text="下一页", command=lambda: self._start_search(self._search_page + 1), width=8
        )
        self._next_btn.grid(row=0, column=6, sticky="w", padx=(self._theme.spacing.xs, 0))
        ttk.Label(box, textvariable=self._page_var, style="Muted.TLabel").grid(
            row=0, column=7, sticky="w", padx=(self._theme.spacing.sm, 0)
        )

    def _build_body(self, parent: Any) -> None:
        ttk = self._ttk
        paned = ttk.PanedWindow(parent, orient="horizontal")
        paned.grid(row=1, column=0, sticky="nsew", pady=(0, self._theme.spacing.sm))

        left = ttk.LabelFrame(paned, text="模型列表", padding=(self._theme.spacing.sm, self._theme.spacing.xs))
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)
        columns = ("repo", "tasks", "downloads", "updated", "size")
        self._models_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        headings = {
            "repo": ("模型", 260),
            "tasks": ("任务", 150),
            "downloads": ("下载", 90),
            "updated": ("更新", 90),
            "size": ("体积", 80),
        }
        for key, (label, width) in headings.items():
            self._models_tree.heading(key, text=label)
            self._models_tree.column(key, width=width, anchor="w", stretch=key in {"repo", "tasks"})
        scrollbar = ttk.Scrollbar(left, orient="vertical", command=self._models_tree.yview)
        self._models_tree.configure(yscrollcommand=scrollbar.set)
        self._models_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self._models_tree.bind("<<TreeviewSelect>>", self._on_model_selected)
        paned.add(left, weight=3)

        right = ttk.Notebook(paned)
        self._detail_tab = ttk.Frame(right, padding=(self._theme.spacing.md, self._theme.spacing.sm))
        self._files_tab = ttk.Frame(right, padding=(self._theme.spacing.md, self._theme.spacing.sm))
        self._local_tab = ttk.Frame(right, padding=(self._theme.spacing.md, self._theme.spacing.sm))
        right.add(self._detail_tab, text="模型详情")
        right.add(self._files_tab, text="文件列表")
        right.add(self._local_tab, text="本地管理")
        paned.add(right, weight=5)
        self._notebook = right

        self._build_detail(self._detail_tab)
        self._build_files(self._files_tab)
        self._build_local(self._local_tab)

    def _build_detail(self, parent: Any) -> None:
        ttk = self._ttk
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        ttk.Label(parent, textvariable=self._title_var, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, textvariable=self._subtitle_var, style="Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(0, self._theme.spacing.sm)
        )

        meta = ttk.Frame(parent)
        meta.grid(row=2, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        meta.columnconfigure(1, weight=1)
        meta.columnconfigure(3, weight=1)
        labels = (
            "中文名",
            "组织",
            "任务",
            "框架",
            "架构",
            "模型类型",
            "语言",
            "许可证",
            "下载",
            "星标",
            "大小",
            "版本",
            "更新时间",
            "推理支持",
        )
        for index, name in enumerate(labels):
            row, column = divmod(index, 2)
            ttk.Label(meta, text=f"{name}：", style="Muted.TLabel").grid(
                row=row, column=column * 2, sticky="e", padx=(0, self._theme.spacing.xs), pady=self._theme.spacing.xxs
            )
            var = self._tk.StringVar(value="-")
            ttk.Label(meta, textvariable=var).grid(
                row=row,
                column=column * 2 + 1,
                sticky="w",
                padx=(0, self._theme.spacing.lg),
                pady=self._theme.spacing.xxs,
            )
            self._meta_vars[name] = var

        readme_box = ttk.LabelFrame(parent, text="README", padding=(self._theme.spacing.sm, self._theme.spacing.xs))
        readme_box.grid(row=3, column=0, sticky="nsew")
        readme_box.columnconfigure(0, weight=1)
        readme_box.rowconfigure(0, weight=1)
        self._readme = _guiwindow.create_log(readme_box, height=14, grid=True)

    def _build_files(self, parent: Any) -> None:
        ttk = self._ttk
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        columns = ("path", "size", "sha")
        self._files_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for key, label, width, stretch in (
            ("path", "文件", 420, True),
            ("size", "大小", 100, False),
            ("sha", "SHA256", 260, False),
        ):
            self._files_tree.heading(key, text=label)
            self._files_tree.column(key, width=width, anchor="w", stretch=stretch)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self._files_tree.yview)
        self._files_tree.configure(yscrollcommand=scrollbar.set)
        self._files_tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        ttk.Label(parent, textvariable=self._files_total_var, style="Muted.TLabel").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(self._theme.spacing.sm, 0)
        )

    def _build_local(self, parent: Any) -> None:
        ttk = self._ttk
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        bar = ttk.Frame(parent)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        ttk.Button(bar, text="刷新", command=self._refresh_local, width=8).pack(side="left")
        ttk.Button(bar, text="打开目录", command=self._open_selected_local, width=10).pack(
            side="left", padx=(self._theme.spacing.sm, 0)
        )
        self._delete_btn = ttk.Button(
            bar, text="删除模型", command=self._delete_selected_local, style="Danger.TButton", width=10
        )
        self._delete_btn.pack(side="left", padx=(self._theme.spacing.sm, 0))
        ttk.Label(bar, text="仅在二次确认后删除整个模型目录", style="Hint.TLabel").pack(
            side="left", padx=(self._theme.spacing.sm, 0)
        )

        columns = ("repo", "size", "files", "status", "path")
        self._local_tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="browse")
        for key, label, width, stretch in (
            ("repo", "仓库", 220, True),
            ("size", "大小", 90, False),
            ("files", "文件", 70, False),
            ("status", "状态", 150, False),
            ("path", "路径", 320, True),
        ):
            self._local_tree.heading(key, text=label)
            self._local_tree.column(key, width=width, anchor="w", stretch=stretch)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=self._local_tree.yview)
        self._local_tree.configure(yscrollcommand=scrollbar.set)
        self._local_tree.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        self._local_tree.bind("<Double-1>", self._on_local_double_click)

    def _build_download(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="下载", padding=(self._theme.spacing.md, self._theme.spacing.sm))
        box.grid(row=2, column=0, sticky="ew", pady=(0, self._theme.spacing.sm))
        box.columnconfigure(1, weight=1)
        box.columnconfigure(4, weight=1)

        ttk.Label(box, text="目标目录").grid(row=0, column=0, sticky="e", padx=(0, self._theme.spacing.sm))
        ttk.Entry(box, textvariable=self._dest_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(box, text="浏览…", command=self._browse_dest, width=8).grid(
            row=0, column=2, sticky="w", padx=(self._theme.spacing.sm, self._theme.spacing.lg)
        )
        ttk.Label(box, text="下载源").grid(row=0, column=3, sticky="e", padx=(0, self._theme.spacing.sm))
        ttk.Combobox(
            box,
            textvariable=self._source_var,
            values=list(_SOURCE_LABELS.values()),
            state="readonly",
            width=12,
        ).grid(row=0, column=4, sticky="w")

        ttk.Label(box, text="并发").grid(
            row=0, column=5, sticky="e", padx=(self._theme.spacing.lg, self._theme.spacing.sm)
        )
        ttk.Spinbox(box, from_=1, to=_MAX_WORKERS, textvariable=self._workers_var, width=5).grid(
            row=0, column=6, sticky="w"
        )
        ttk.Checkbutton(box, text="校验 SHA256", variable=self._verify_var).grid(
            row=0, column=7, sticky="w", padx=(self._theme.spacing.lg, 0)
        )

        ttk.Label(box, text="包含掩码").grid(
            row=1, column=0, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xs
        )
        ttk.Entry(box, textvariable=self._include_var).grid(row=1, column=1, sticky="ew", pady=self._theme.spacing.xs)
        ttk.Label(box, text="排除掩码").grid(
            row=1, column=3, sticky="e", padx=(0, self._theme.spacing.sm), pady=self._theme.spacing.xs
        )
        ttk.Entry(box, textvariable=self._exclude_var).grid(row=1, column=4, sticky="ew", pady=self._theme.spacing.xs)
        self._download_btn = ttk.Button(
            box, text="下载", command=self._start_download, style="Accent.TButton", width=10
        )
        self._download_btn.grid(row=1, column=6, sticky="e", padx=(self._theme.spacing.sm, 0))
        self._stop_btn = ttk.Button(
            box, text="停止", command=self._stop_download, style="Danger.TButton", state="disabled", width=8
        )
        self._stop_btn.grid(row=1, column=7, sticky="e", padx=(self._theme.spacing.sm, 0))

        ttk.Label(box, text="总进度").grid(row=2, column=0, sticky="e", padx=(0, self._theme.spacing.sm))
        self._overall_bar = ttk.Progressbar(box, mode="determinate", maximum=1, value=0)
        self._overall_bar.grid(row=2, column=1, columnspan=5, sticky="ew", pady=(self._theme.spacing.sm, 0))
        ttk.Label(box, textvariable=self._progress_var, width=24, anchor="e").grid(
            row=2, column=6, columnspan=2, sticky="e", pady=(self._theme.spacing.sm, 0)
        )

        ttk.Label(box, text="当前文件").grid(row=3, column=0, sticky="e", padx=(0, self._theme.spacing.sm))
        self._file_bar = ttk.Progressbar(box, mode="determinate", maximum=1, value=0)
        self._file_bar.grid(row=3, column=1, columnspan=5, sticky="ew", pady=(self._theme.spacing.xs, 0))
        ttk.Label(box, textvariable=self._file_var, width=40, anchor="e").grid(
            row=3, column=6, columnspan=2, sticky="e", pady=(self._theme.spacing.xs, 0)
        )

    def _build_log(self, parent: Any) -> None:
        box = self._ttk.LabelFrame(parent, text="日志", padding=(self._theme.spacing.sm, self._theme.spacing.xs))
        box.grid(row=3, column=0, sticky="ew")
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self._log_view = _guiwindow.create_log(box, height=5, grid=True)

    # ------------------------------------------------------------------ #
    # 模型搜索 / 详情
    # ------------------------------------------------------------------ #
    def _on_search_enter(self, _event: Any) -> str:
        """回车触发搜索；输入 ``组织/模型`` 时直接加载详情。"""
        self._on_search()
        return "break"

    def _on_search(self) -> None:
        self._start_search(1)

    def _on_sort_changed(self, _event: Any = None) -> None:
        self._sort_key = _SORT_KEYS.get(self._sort_var.get(), "default")
        self._start_search(1)

    def _start_search(self, page: int) -> None:
        """在后台搜索指定页；关键字为 ``组织/模型`` 时直接加载该仓库。"""
        if self._downloading:
            self._status_var.set("下载进行中，请稍后再搜索")
            return
        keyword = self._keyword_var.get().strip()
        if keyword.count("/") == 1 and " " not in keyword:
            self._load_repo(keyword)
            return
        page = max(1, page)
        self._search_page = page
        self._search_btn.configure(state="disabled")
        self._status_var.set(f"正在搜索第 {page} 页…")
        self._spawn(self._search_worker, keyword, page)

    def _search_worker(self, keyword: str, page: int) -> None:
        """后台线程：调用 ModelScope 搜索接口。"""
        try:
            result = search_models(keyword, page, _PAGE_SIZE, self._sort_key)
        except (ModelToolError, OSError) as exc:
            self._queue.put(("search_error", str(exc)))
        except Exception as exc:  # 后台线程兜底，避免界面失去反馈
            self._queue.put(("search_error", f"{type(exc).__name__}: {exc}"))
        else:
            self._queue.put(("search_done", result))

    def _on_model_selected(self, _event: Any = None) -> None:
        """左侧选中变化时加载详情。"""
        selection = self._models_tree.selection()
        if not selection:
            return
        values: Any = self._models_tree.item(selection[0], "values")
        repo = str(values[0]) if values else ""
        if repo and repo != self._current_repo:
            self._load_repo(repo)

    def _load_repo(self, repo: str) -> None:
        """加载指定仓库详情与文件清单（带世代号防止旧请求覆盖新选择）。"""
        self._detail_generation += 1
        generation = self._detail_generation
        self._current_repo = repo
        self._current_info = None
        self._current_files = ()
        self._download_btn.configure(state="disabled")
        self._title_var.set(repo)
        self._subtitle_var.set("正在加载详情…")
        for var in self._meta_vars.values():
            var.set("-")
        _guiwindow.clear_log(self._readme)
        self._files_tree.delete(*self._files_tree.get_children())
        self._files_total_var.set("")
        self._notebook.select(self._detail_tab)
        self._status_var.set(f"正在加载 {repo} …")
        self._spawn(self._detail_worker, repo, generation)

    def _detail_worker(self, repo: str, generation: int) -> None:
        """后台线程：拉取模型详情与 ModelScope 文件清单。"""
        try:
            info = fetch_model_info(repo)
        except (ModelToolError, OSError) as exc:
            self._queue.put(("detail_error", (generation, repo, str(exc))))
            return
        file_error = ""
        files: list[ModelFile] | None = None
        try:
            files = fetch_files(repo, "modelscope")
        except (ModelToolError, OSError) as exc:
            file_error = str(exc)
        except Exception as exc:  # 清单异常不影响详情展示
            file_error = f"{type(exc).__name__}: {exc}"
        self._queue.put(("detail_done", (generation, info, files, file_error)))

    # ------------------------------------------------------------------ #
    # 下载
    # ------------------------------------------------------------------ #
    def _browse_dest(self) -> None:
        """选择目标目录。"""
        from tkinter import filedialog

        initial = Path(self._dest_var.get().strip() or "models").expanduser()
        chosen = filedialog.askdirectory(initialdir=str(initial), title="选择模型保存目录")
        if chosen:
            self._dest_var.set(chosen)

    def _start_download(self) -> None:
        """校验参数后在后台执行下载。"""
        if self._downloading:
            return
        repo = self._current_repo
        if not repo:
            self._status_var.set("请先在左侧选择一个模型")
            return
        dest_text = self._dest_var.get().strip()
        if not dest_text:
            self._status_var.set("请填写目标目录")
            return
        try:
            workers = min(max(int(self._workers_var.get().strip()), 1), _MAX_WORKERS)
        except ValueError:
            self._status_var.set("并发数必须是整数")
            return
        source = _SOURCE_KEYS.get(self._source_var.get(), "modelscope")
        dest = Path(dest_text).expanduser()
        self._download_target = dest / repo
        self._downloading = True
        self._stop_event.clear()
        self._download_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._search_btn.configure(state="disabled")
        self._overall_bar.configure(maximum=1, value=0)
        self._file_bar.configure(maximum=1, value=0)
        self._progress_var.set("-")
        self._file_var.set("-")
        self._status_var.set(f"开始下载 {repo}")
        self._log(f"开始下载 {repo} -> {self._download_target}")
        _guiwindow.append_log(self._log_view, f"下载源: {source}，并发: {workers}")
        self._spawn(
            self._download_worker,
            repo,
            dest,
            source,
            self._include_var.get(),
            self._exclude_var.get(),
            workers,
            self._verify_var.get(),
        )

    def _download_worker(  # noqa: PLR0913, PLR0917
        self,
        repo: str,
        dest: Path,
        source: Literal["modelscope", "hf-mirror", "auto"],
        include: str,
        exclude: str,
        workers: int,
        verify: bool,
    ) -> None:
        """后台线程：选源 → 过滤 → 调下载核心（进度 / 取消通过回调接入）。"""
        try:
            self._queue.put(("log", f"获取 {repo} 的 {source} 文件清单…"))
            selection = select_source(repo, source)
            self._queue.put(("log", f"选用源 {selection.source}，仓库共 {len(selection.files)} 个文件"))
            selected = filter_files(list(selection.files), parse_patterns(include), parse_patterns(exclude))
            if not selected:
                raise ModelToolError("过滤后没有文件，请检查包含 / 排除掩码")
            target = dest / repo
            self._queue.put(("log", f"待下载 {len(selected)} 个文件，目标 {target}"))
            download_files(
                repo,
                selected,
                target,
                selection,
                workers=workers,
                verify=verify,
                reporter=lambda event: self._queue.put(("progress", event)),
                should_stop=self._stop_event.is_set,
            )
        except DownloadCancelled as exc:
            self._queue.put(("download_cancelled", str(exc)))
        except (ModelToolError, OSError, ValueError) as exc:
            self._queue.put(("download_error", str(exc)))
        except Exception as exc:  # 后台线程兜底
            self._queue.put(("download_error", f"{type(exc).__name__}: {exc}"))
        else:
            self._queue.put(("download_done", str(dest / repo)))

    def _stop_download(self) -> None:
        """请求停止下载（保留 .part 供下次续传）。"""
        if not self._downloading:
            return
        self._stop_event.set()
        self._stop_btn.configure(state="disabled")
        self._status_var.set("正在停止…")

    def _on_progress(self, event: ProgressEvent) -> None:
        """把下载核心的进度事件映射到两个进度条。"""
        if event.task == "总进度":
            self._overall_bar.configure(maximum=max(event.total, 1), value=event.completed)
            self._progress_var.set(f"{human_size(event.completed)} / {human_size(event.total)}")
            return
        self._file_bar.configure(maximum=max(event.total, 1), value=event.completed)
        if event.state == "start":
            self._file_var.set(event.task)
            self._log(f"开始: {event.task}")

    # ------------------------------------------------------------------ #
    # 本地管理
    # ------------------------------------------------------------------ #
    def _local_root(self) -> Path:
        """当前目标目录（相对路径按工作目录展开）。"""
        return Path(self._dest_var.get().strip() or "models").expanduser()

    def _refresh_local(self) -> None:
        """后台扫描目标目录下的本地模型。"""
        root = self._local_root()
        self._status_var.set(f"正在扫描 {root} …")
        self._spawn(self._local_worker, root)

    def _local_worker(self, root: Path) -> None:
        """后台线程：扫描本地模型目录。"""
        try:
            models = scan_local_models(root)
        except OSError as exc:
            self._queue.put(("local_error", str(exc)))
        else:
            self._queue.put(("local_done", models))

    def _open_selected_local(self) -> None:
        """打开选中模型的目录；未选中时打开目标根目录。"""
        selection = self._local_tree.selection()
        target = self._local_root()
        if selection:
            values = self._local_tree.item(selection[0], "values")
            if len(values) >= 5:
                target = Path(str(values[4]))
        if not target.is_dir():
            self._messagebox.showwarning("目录不存在", f"目录不存在:\n{target}")
            return
        try:
            open_path(target)
        except OSError as exc:
            self._messagebox.showerror("打开目录失败", str(exc))

    def _delete_selected_local(self) -> None:
        """经用户确认后删除选中模型目录。"""
        if self._downloading:
            self._messagebox.showwarning("下载进行中", "请先停止当前下载再删除模型")
            return
        selection = self._local_tree.selection()
        if not selection:
            self._status_var.set("请先在本地管理中选择要删除的模型")
            return
        values = self._local_tree.item(selection[0], "values")
        repo = str(values[0]) if values else ""
        model = next((item for item in self._local_models if item.repo == repo), None)
        if model is None:
            return
        confirmed = self._messagebox.askyesno(
            "确认删除模型",
            f"将从磁盘删除以下目录：\n\n{model.path}\n\n"
            f"大小：{human_size(model.size)}，文件数：{model.files}\n\n"
            "该操作不可撤销，确定删除？",
        )
        if not confirmed:
            return
        self._delete_btn.configure(state="disabled")
        self._status_var.set(f"正在删除 {model.repo} …")
        self._spawn(self._delete_worker, model.repo)

    def _delete_worker(self, repo: str) -> None:
        """后台线程：删除模型目录。"""
        try:
            removed = delete_local_model(self._local_root(), repo)
        except (ModelToolError, OSError) as exc:
            self._queue.put(("local_error", str(exc)))
        else:
            self._queue.put(("local_deleted", str(removed)))

    def _on_local_double_click(self, _event: Any = None) -> None:
        """双击本地模型加载其 ModelScope 详情。"""
        selection = self._local_tree.selection()
        if not selection:
            return
        values = self._local_tree.item(selection[0], "values")
        if values:
            self._keyword_var.set(str(values[0]))
            self._load_repo(str(values[0]))

    # ------------------------------------------------------------------ #
    # 事件循环
    # ------------------------------------------------------------------ #
    def _spawn(self, target: Any, *args: Any) -> None:
        """启动后台线程，并在结束时投递一个 ``job`` 事件以停止轮询。"""

        def runner() -> None:
            try:
                target(*args)
            finally:
                self._queue.put(("job", None))

        self._jobs += 1
        threading.Thread(target=runner, daemon=True).start()
        self._schedule_poll()

    def _schedule_poll(self) -> None:
        if not self._poll_scheduled:
            self._poll_scheduled = True
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll)

    def _poll(self) -> None:
        """主线程消费后台事件。"""
        self._poll_scheduled = False
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                self._dispatch(kind, payload)
        except queue.Empty:
            pass
        if self._jobs or self._downloading:
            self._schedule_poll()

    def _dispatch(self, kind: str, payload: Any) -> None:  # noqa: PLR0912 - 事件类型集中分发，拆散反而更难追踪
        """事件分发。"""
        if kind == "job":
            self._jobs = max(0, self._jobs - 1)
        elif kind == "log":
            self._log(str(payload))
        elif kind == "search_done":
            self._render_search(payload)
        elif kind == "search_error":
            self._search_btn.configure(state="normal")
            self._status_var.set("搜索失败")
            self._log(f"搜索失败: {payload}")
        elif kind == "detail_done":
            self._render_detail(*payload)
        elif kind == "detail_error":
            generation, repo, message = payload
            if generation == self._detail_generation:
                self._subtitle_var.set("详情加载失败")
                self._status_var.set(f"{repo} 详情加载失败")
                self._log(f"详情加载失败 {repo}: {message}")
                self._download_btn.configure(state="normal")
        elif kind == "progress":
            self._on_progress(payload)
        elif kind == "download_done":
            self._finish_download(f"下载完成: {payload}", ok=True)
        elif kind == "download_cancelled":
            self._finish_download(str(payload), ok=False)
        elif kind == "download_error":
            self._finish_download(f"下载失败: {payload}", ok=False)
        elif kind == "local_done":
            self._render_local(payload)
        elif kind == "local_deleted":
            self._delete_btn.configure(state="normal")
            self._status_var.set(f"已删除 {payload}")
            self._log(f"已删除 {payload}")
            self._refresh_local()
        elif kind == "local_error":
            self._delete_btn.configure(state="normal")
            self._status_var.set("本地管理失败")
            self._log(f"本地管理失败: {payload}")

    def _render_search(self, result: ModelSearch) -> None:
        """渲染搜索结果并自动加载第一条详情。"""
        self._search_btn.configure(state="normal")
        self._search_total = result.total
        self._search_page = result.page
        self._models_tree.delete(*self._models_tree.get_children())
        for item in result.items:
            self._models_tree.insert("", "end", values=model_row(item))
        pages = max(1, math.ceil(result.total / result.page_size)) if result.total else 1
        self._page_var.set(f"第 {result.page} / {pages} 页（共 {result.total:,} 条）")
        self._prev_btn.configure(state="normal" if result.page > 1 else "disabled")
        self._next_btn.configure(state="normal" if result.page < pages else "disabled")
        if result.items:
            first = self._models_tree.get_children()[0]
            self._models_tree.selection_set(first)
            self._models_tree.focus(first)
            self._status_var.set(f"找到 {result.total:,} 个模型")
        else:
            self._status_var.set("没有匹配的模型")

    def _render_detail(
        self,
        generation: int,
        info: ModelInfo,
        files: list[ModelFile] | None,
        file_error: str,
    ) -> None:
        """渲染模型详情与文件清单；过期世代直接丢弃。"""
        if generation != self._detail_generation:
            return
        self._current_info = info
        self._current_files = tuple(files or ())
        summary = info.summary
        self._title_var.set(summary.repo)
        self._subtitle_var.set(summary.chinese_name or info.description[:80] or " ")
        for name, value in detail_pairs(info):
            if name in self._meta_vars:
                self._meta_vars[name].set(value)
        _guiwindow.clear_log(self._readme)
        if info.readme:
            _guiwindow.append_log(self._readme, info.readme)
        else:
            _guiwindow.append_log(self._readme, "（该模型未提供 README）")
        self._files_tree.delete(*self._files_tree.get_children())
        if files:
            total = 0
            for file in files:
                total += file.size
                self._files_tree.insert("", "end", values=(file.path, human_size(file.size), file.sha256[:16] or "-"))
            self._files_total_var.set(f"共 {len(files)} 个文件，合计 {human_size(total)}（ModelScope 清单）")
        else:
            self._files_total_var.set(f"文件清单获取失败：{file_error}" if file_error else "文件清单为空")
        self._download_btn.configure(state="normal")
        self._status_var.set(f"已加载 {summary.repo}")

    def _render_local(self, models: list[LocalModel]) -> None:
        """渲染本地模型列表。"""
        self._local_models = models
        self._local_tree.delete(*self._local_tree.get_children())
        for item in models:
            self._local_tree.insert("", "end", values=local_row(item))
        total = sum(item.size for item in models)
        self._status_var.set(f"本地模型 {len(models)} 个，合计 {human_size(total)}")

    def _finish_download(self, message: str, *, ok: bool) -> None:
        """下载结束：恢复按钮、刷新本地列表并提示结果。"""
        self._downloading = False
        self._download_btn.configure(state="normal" if self._current_repo else "disabled")
        self._stop_btn.configure(state="disabled")
        self._search_btn.configure(state="normal")
        self._status_var.set(message)
        self._log(message)
        if ok:
            self._refresh_local()
        else:
            self._messagebox.showerror("下载未完成", message)

    # ------------------------------------------------------------------ #
    # 其他
    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        """向日志区追加一行。"""
        _guiwindow.append_log(self._log_view, message)

    def _about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            "模型下载器（ModelScope）\n\n"
            "搜索模型、查看 README / 文件清单，支持多源选优、断点续传、\n"
            "SHA256 校验与本地模型管理。",
        )

    def _save(self) -> None:
        """把当前窗口状态写回设置。"""
        try:
            workers = min(max(int(self._workers_var.get().strip()), 1), _MAX_WORKERS)
        except ValueError:
            workers = self._settings.workers
        save_settings(
            DownloadGuiSettings(
                geometry=self.root.winfo_geometry(),
                dest=self._dest_var.get().strip() or "models",
                source=_SOURCE_KEYS.get(self._source_var.get(), "modelscope"),
                workers=workers,
                verify=bool(self._verify_var.get()),
                include=self._include_var.get(),
                exclude=self._exclude_var.get(),
            ),
            self._settings_path,
        )

    def _close(self) -> None:
        """关闭窗口；下载中先确认，退出前保存设置。"""
        if self._downloading and not self._messagebox.askyesno(
            "下载进行中",
            "下载尚未完成，退出将停止下载（已下载部分保留为 .part）。确实退出？",
        ):
            return
        self._stop_event.set()
        self._save()
        self.root.destroy()


def run_gui() -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(DownloadWindow, "窗口版")


@fcmd.tool("llmdownw", help="打开模型下载器窗口（Tk，ModelScope）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 ModelScope 模型下载器窗口。"""
    run_gui()


@fcmd.main("llmdownw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
