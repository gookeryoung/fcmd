"""filesyncw - 文件夹比较与同步（Tk GUI，参考 FreeFileSync）。

左右两个目录 + 比较 / 同步选项：按「时间与大小 / 仅大小 / 内容」比较，
结果表列出仅左、仅右、不同与相同项及计划动作；按「双向 / 镜像 / 更新」
同步，支持「预演」只统计不写盘。比较与同步在后台线程执行，逐项进度与
日志实时回到主线程；冲突项（修改时间相同但内容不同）自动跳过并标注。

菜单「帮助 → 载入配置 / 保存配置」可把当前左右目录与选项存成 JSON 复用；
窗口设置（目录、比较 / 同步模式、过滤、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/filesyncw.json``，下次启动自动恢复。

示例
----
    fcmd filesyncw        # 打开文件夹比较与同步窗口
    filesyncw             # 全局命令等价入口
"""

from __future__ import annotations

import queue
import threading
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.filesync._core import (
    ACTION_TEXT,
    CATEGORY_CONFLICT,
    CATEGORY_DIFFERENT,
    CATEGORY_EQUAL,
    CATEGORY_LEFT_ONLY,
    CATEGORY_RIGHT_ONLY,
    CATEGORY_TEXT,
    COMPARE_MODE_TEXT,
    COMPARE_MODES,
    SYNC_MODE_TEXT,
    SYNC_MODES,
    SYNC_TWO_WAY,
    CompareOptions,
    CompareResult,
    SyncAction,
    SyncPlan,
    SyncReport,
    build_sync_plan,
    compare_trees,
    execute_plan,
    format_size,
    format_sync_report,
    normalize_compare_mode,
    normalize_sync_mode,
    parse_filter_text,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "DEFAULT_GEOMETRY",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "FilesyncSettings",
    "config_path",
    "load_settings",
    "mode_label",
    "mode_value",
    "resolve_geometry",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "文件夹比较与同步窗口版（Tk）：左右目录对比（时间/大小/内容），结果表列出差异与计划动作，"
    "支持双向/镜像/更新同步与试运行，冲突项自动跳过；"
    "设置持久化到 ~/.fcmd/ui/filesyncw.json"
)

DEFAULT_GEOMETRY = "1120x780+140+70"
MIN_WIDTH = 900
MIN_HEIGHT = 620

COMPARE_CHOICES: tuple[str, ...] = tuple(COMPARE_MODE_TEXT[mode] for mode in COMPARE_MODES)
SYNC_CHOICES: tuple[str, ...] = tuple(SYNC_MODE_TEXT[mode] for mode in SYNC_MODES)


# ---------------------------------------------------------------------- #
# 配置持久化（纯逻辑，可单测）
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/filesyncw.json``。"""
    return _guiconfig.config_path("filesyncw")


valid_geometry = _guiconfig.valid_geometry


@dataclass
class FilesyncSettings:
    """窗口与配置文件共用的设置。"""

    left: str = ""
    right: str = ""
    compare_mode: str = COMPARE_MODES[0]
    sync_mode: str = SYNC_TWO_WAY
    include: str = ""
    exclude: str = ""
    skip_hidden: bool = False
    show_equal: bool = False
    geometry: str | None = None


def settings_to_mapping(settings: FilesyncSettings) -> dict[str, Any]:
    """把设置转成可写入 JSON 的字典。"""
    return {
        "left": settings.left,
        "right": settings.right,
        "compare_mode": settings.compare_mode,
        "sync_mode": settings.sync_mode,
        "include": settings.include,
        "exclude": settings.exclude,
        "skip_hidden": settings.skip_hidden,
        "show_equal": settings.show_equal,
        "geometry": settings.geometry,
    }


def settings_from_mapping(
    raw: Mapping[str, Any],
    base: FilesyncSettings | None = None,
) -> FilesyncSettings:
    """从字典解析设置；缺失或类型不符的字段保留默认值 / ``base`` 值。

    比较 / 同步模式未知时回退默认值，geometry 非法时置空。
    """
    settings = replace(base) if base is not None else FilesyncSettings()
    for name in ("left", "right", "include", "exclude"):
        value = raw.get(name)
        if isinstance(value, str):
            setattr(settings, name, value)
    compare_mode = raw.get("compare_mode")
    if isinstance(compare_mode, str):
        try:
            settings.compare_mode = normalize_compare_mode(compare_mode)
        except ValueError:
            settings.compare_mode = COMPARE_MODES[0]
    sync_mode = raw.get("sync_mode")
    if isinstance(sync_mode, str):
        try:
            settings.sync_mode = normalize_sync_mode(sync_mode)
        except ValueError:
            settings.sync_mode = SYNC_TWO_WAY
    for name in ("skip_hidden", "show_equal"):
        value = raw.get(name)
        if isinstance(value, bool):
            setattr(settings, name, value)
    geometry = raw.get("geometry")
    settings.geometry = geometry if isinstance(geometry, str) and valid_geometry(geometry) else None
    return settings


def load_settings(path: Path | None = None) -> FilesyncSettings:
    """读取设置文件；缺失、损坏或顶层非对象时返回默认设置。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return FilesyncSettings()
    return settings_from_mapping(raw)


def save_settings(settings: FilesyncSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def mode_label(value: str, labels: Mapping[str, str]) -> str:
    """把模式值转成界面展示文本；未知值回退首个标签。"""
    if value in labels:
        return labels[value]
    return next(iter(labels.values()))


def mode_value(label: str, labels: Mapping[str, str], default: str) -> str:
    """把界面文本（或模式值）转回模式值；无法识别时返回 ``default``。"""
    for value, text in labels.items():
        if label in (text, value):
            return value
    return default


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界 / 异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class FilesyncWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 文件夹比较与同步主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self._tk = tk
        self._ttk = ttk
        self._filedialog = filedialog
        self._messagebox = messagebox
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._running = False
        self._result: CompareResult | None = None
        self._plan: SyncPlan | None = None
        self._action_buttons: list[Any] = []

        self.root, self._theme = _guiwindow.create_root(
            title=f"filesync - 文件夹比较与同步 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._left_var = tk.StringVar(value=self._settings.left)
        self._right_var = tk.StringVar(value=self._settings.right)
        self._compare_var = tk.StringVar(value=mode_label(self._settings.compare_mode, COMPARE_MODE_TEXT))
        self._sync_var = tk.StringVar(value=mode_label(self._settings.sync_mode, SYNC_MODE_TEXT))
        self._include_var = tk.StringVar(value=self._settings.include)
        self._exclude_var = tk.StringVar(value=self._settings.exclude)
        self._skip_hidden_var = tk.BooleanVar(value=self._settings.skip_hidden)
        self._show_equal_var = tk.BooleanVar(value=self._settings.show_equal)
        self._dry_run_var = tk.BooleanVar(value=True)
        self._status_var = tk.StringVar(value="就绪")

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        _guiwindow.build_menu(
            self.root,
            on_close=self.close,
            on_about=self._show_about,
            help_items=(("载入配置…", self._load_profile), ("保存配置…", self._save_profile)),
        )

    # ------------------------------------------------------------------ #
    # 界面构建
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        folders = ttk.LabelFrame(self.root, text="文件夹对", padding=space.sm)
        folders.grid(row=0, column=0, sticky="ew", padx=space.md, pady=(space.md, space.xs))
        folders.columnconfigure(1, weight=1)
        ttk.Label(folders, text="左侧：", width=7, anchor="w").grid(row=0, column=0, sticky="w")
        ttk.Entry(folders, textvariable=self._left_var).grid(
            row=0, column=1, sticky="ew", padx=(0, space.sm), pady=space.xxs
        )
        ttk.Button(folders, text="浏览…", command=lambda: self._browse(self._left_var)).grid(
            row=0, column=2, sticky="e", pady=space.xxs
        )
        ttk.Label(folders, text="右侧：", width=7, anchor="w").grid(row=1, column=0, sticky="w")
        ttk.Entry(folders, textvariable=self._right_var).grid(
            row=1, column=1, sticky="ew", padx=(0, space.sm), pady=space.xxs
        )
        ttk.Button(folders, text="浏览…", command=lambda: self._browse(self._right_var)).grid(
            row=1, column=2, sticky="e", pady=space.xxs
        )
        ttk.Button(folders, text="交换左右", command=self._swap).grid(row=0, column=3, rowspan=2, padx=(space.sm, 0))

        options = ttk.LabelFrame(self.root, text="比较与同步", padding=space.sm)
        options.grid(row=1, column=0, sticky="ew", padx=space.md, pady=space.xs)
        options.columnconfigure(6, weight=1)
        ttk.Label(options, text="比较：").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            options,
            state="readonly",
            width=12,
            values=list(COMPARE_CHOICES),
            textvariable=self._compare_var,
        ).grid(row=0, column=1, sticky="w", padx=(0, space.md))
        ttk.Label(options, text="同步：").grid(row=0, column=2, sticky="w")
        sync_box = ttk.Combobox(
            options,
            state="readonly",
            width=16,
            values=list(SYNC_CHOICES),
            textvariable=self._sync_var,
        )
        sync_box.grid(row=0, column=3, sticky="w", padx=(0, space.md))
        sync_box.bind("<<ComboboxSelected>>", self._on_sync_mode_change)
        ttk.Checkbutton(options, text="跳过隐藏文件", variable=self._skip_hidden_var).grid(
            row=0, column=4, sticky="w", padx=(0, space.md)
        )
        ttk.Checkbutton(
            options,
            text="显示相同项",
            variable=self._show_equal_var,
            command=self._render_result,
        ).grid(row=0, column=5, sticky="w")

        ttk.Label(options, text="包含：").grid(row=1, column=0, sticky="w", pady=(space.xxs, 0))
        ttk.Entry(options, textvariable=self._include_var).grid(
            row=1, column=1, columnspan=3, sticky="ew", padx=(0, space.md), pady=(space.xxs, 0)
        )
        ttk.Label(options, text="排除：").grid(row=1, column=4, sticky="w", pady=(space.xxs, 0))
        ttk.Entry(options, textvariable=self._exclude_var).grid(
            row=1, column=5, columnspan=2, sticky="ew", pady=(space.xxs, 0)
        )

        actions = ttk.Frame(options)
        actions.grid(row=2, column=0, columnspan=7, sticky="ew", pady=(space.sm, 0))
        actions.columnconfigure(3, weight=1)
        compare_button = ttk.Button(actions, text="比较", command=self._start_compare, style="Accent.TButton")
        compare_button.grid(row=0, column=0, padx=(0, space.sm))
        sync_button = ttk.Button(actions, text="同步", command=self._start_sync)
        sync_button.grid(row=0, column=1, padx=(0, space.md))
        self._action_buttons.extend([compare_button, sync_button])
        ttk.Checkbutton(actions, text="预演（不写入）", variable=self._dry_run_var).grid(row=0, column=2, sticky="w")

        paned = ttk.Panedwindow(self.root, orient="vertical")
        paned.grid(row=2, column=0, sticky="nsew", padx=space.md, pady=space.xs)

        results = ttk.LabelFrame(paned, text="比较结果", padding=space.sm)
        results.rowconfigure(0, weight=1)
        results.columnconfigure(0, weight=1)
        self._tree = ttk.Treeview(
            results,
            columns=("state", "path", "action", "left_size", "right_size"),
            show="headings",
            selectmode="browse",
        )
        headings = (
            ("state", "状态", 90, "w"),
            ("path", "相对路径", 460, "w"),
            ("action", "计划动作", 130, "w"),
            ("left_size", "左侧大小", 110, "e"),
            ("right_size", "右侧大小", 110, "e"),
        )
        for column, text, width, anchor in headings:
            self._tree.heading(column, text=text)
            self._tree.column(column, width=width, anchor=anchor, stretch=column == "path")
        palette = self._theme.palette
        self._tree.tag_configure(CATEGORY_LEFT_ONLY, foreground=palette.accent)
        self._tree.tag_configure(CATEGORY_RIGHT_ONLY, foreground=palette.warning)
        self._tree.tag_configure(CATEGORY_DIFFERENT, foreground=palette.danger)
        self._tree.tag_configure(CATEGORY_EQUAL, foreground=palette.muted)
        self._tree.tag_configure(CATEGORY_CONFLICT, foreground=palette.danger)
        yscroll = ttk.Scrollbar(results, orient="vertical", command=self._tree.yview)
        xscroll = ttk.Scrollbar(results, orient="horizontal", command=self._tree.xview)
        self._tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        paned.add(results, weight=3)

        log_frame = ttk.LabelFrame(paned, text="日志", padding=space.sm)
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self._log = _guiwindow.create_log(log_frame, height=8, grid=True)
        paned.add(log_frame, weight=1)

        ttk.Label(self.root, textvariable=self._status_var, anchor="w").grid(
            row=3, column=0, sticky="ew", padx=space.md, pady=(0, space.sm)
        )
        _guiwindow.append_log(self._log, "选择左右目录后点击「比较」，确认结果与计划再执行「同步」。", timestamp=True)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _browse(self, var: Any) -> None:
        """选择目录并写回输入框。"""
        initial = var.get().strip()
        directory = self._filedialog.askdirectory(initialdir=initial or None, parent=self.root)
        if directory:
            var.set(directory)

    def _swap(self) -> None:
        """交换左右目录。"""
        left = self._left_var.get()
        self._left_var.set(self._right_var.get())
        self._right_var.set(left)

    def _on_sync_mode_change(self, _event: Any = None) -> None:
        """切换同步模式后，用缓存结果即时重算计划动作列。"""
        if self._result is None:
            return
        self._plan = build_sync_plan(self._result, self._sync_mode())
        self._render_result()

    def _compare_mode(self) -> str:
        return mode_value(self._compare_var.get(), COMPARE_MODE_TEXT, COMPARE_MODES[0])

    def _sync_mode(self) -> str:
        return mode_value(self._sync_var.get(), SYNC_MODE_TEXT, SYNC_TWO_WAY)

    def _collect_options(self) -> CompareOptions:
        """把界面选项转为比较选项。"""
        return CompareOptions(
            mode=self._compare_mode(),
            include=parse_filter_text(self._include_var.get()),
            exclude=parse_filter_text(self._exclude_var.get()),
            skip_hidden=bool(self._skip_hidden_var.get()),
        )

    def _validate(self) -> tuple[Path, Path, CompareOptions] | None:
        """校验左右目录；无效时弹窗并返回 ``None``。"""
        left = Path(self._left_var.get().strip()).expanduser()
        right = Path(self._right_var.get().strip()).expanduser()
        if not left.is_dir() or not right.is_dir():
            self._messagebox.showerror("路径无效", "请选择存在的左侧和右侧文件夹。", parent=self.root)
            return None
        if left.resolve() == right.resolve():
            self._messagebox.showerror("路径无效", "左右目录不能相同。", parent=self.root)
            return None
        return left, right, self._collect_options()

    def _begin(self, message: str, *, clear: bool = True) -> None:
        """进入运行态：禁用按钮、准备新队列并写日志。"""
        self._running = True
        self._queue = queue.Queue()
        self._set_buttons(False)
        self._status_var.set(message)
        if clear:
            _guiwindow.clear_log(self._log)
        _guiwindow.append_log(self._log, message, timestamp=True)

    def _finish(self, message: str) -> None:
        """退出运行态并恢复按钮。"""
        self._running = False
        self._status_var.set(message)
        self._set_buttons(True)

    def _set_buttons(self, enabled: bool) -> None:
        for button in self._action_buttons:
            button.configure(state="normal" if enabled else "disabled")

    # ------------------------------------------------------------------ #
    # 后台任务
    # ------------------------------------------------------------------ #
    def _start_compare(self) -> None:
        if self._running:
            return
        target = self._validate()
        if target is None:
            return
        left, right, options = target
        self._begin("正在比较…")
        queue_obj = self._queue
        mode = self._sync_mode()

        def task() -> None:
            try:
                result = compare_trees(left, right, options)
                plan = build_sync_plan(result, mode)
            except (OSError, ValueError) as exc:
                queue_obj.put(("error", str(exc)))
                return
            queue_obj.put(("compare_done", (result, plan)))

        self._worker = _guiwindow.start_worker(self.root, task, poll=self._poll)

    def _start_sync(self) -> None:
        if self._running:
            return
        target = self._validate()
        if target is None:
            return
        left, right, options = target
        mode = self._sync_mode()
        dry_run = bool(self._dry_run_var.get())
        if not dry_run:
            label = mode_label(mode, SYNC_MODE_TEXT)
            confirmed = self._messagebox.askyesno(
                "确认同步",
                f"将按「{label}」模式同步：\n{left}\n↔ {right}\n\n可能覆盖或删除文件，是否继续？",
                parent=self.root,
            )
            if not confirmed:
                return
        self._begin("正在同步…")
        queue_obj = self._queue

        def task() -> None:
            try:
                result = compare_trees(left, right, options)
                plan = build_sync_plan(result, mode)
                report = execute_plan(
                    plan,
                    dry_run=dry_run,
                    progress=lambda done, total, action: queue_obj.put(("progress", (done, total, action))),
                )
            except (OSError, ValueError) as exc:
                queue_obj.put(("error", str(exc)))
                return
            queue_obj.put(("sync_done", (result, plan, report)))

        self._worker = _guiwindow.start_worker(self.root, task, poll=self._poll)

    def _poll(self) -> None:
        """主线程消费后台事件；任务结束后停止续排。"""
        while True:
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "progress":
                self._on_progress(payload)
            elif kind == "compare_done":
                self._on_compare_done(payload)
            elif kind == "sync_done":
                self._on_sync_done(payload)
            elif kind == "error":
                self._on_error(str(payload))
        if self._running:
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll)

    def _on_progress(self, payload: tuple[int, int, SyncAction]) -> None:
        done, total, action = payload
        _guiwindow.append_log(
            self._log,
            f"[{done}/{total}] {action.action_text} {action.relative}",
            timestamp=True,
        )

    def _on_compare_done(self, payload: tuple[CompareResult, SyncPlan]) -> None:
        result, plan = payload
        self._result = result
        self._plan = plan
        self._render_result()
        _guiwindow.append_log(
            self._log,
            f"比较完成：{self._summary(result, plan)}",
            timestamp=True,
        )
        self._finish("比较完成")

    def _on_sync_done(self, payload: tuple[CompareResult, SyncPlan, SyncReport]) -> None:
        result, plan, report = payload
        self._result = result
        self._plan = plan
        self._render_result()
        _guiwindow.append_log(self._log, format_sync_report(report), timestamp=True)
        self._finish("同步完成（有失败）" if report.errors else "同步完成")

    def _on_error(self, message: str) -> None:
        _guiwindow.append_log(self._log, f"错误：{message}", timestamp=True)
        self._finish("操作失败")
        self._messagebox.showerror("操作失败", message, parent=self.root)

    # ------------------------------------------------------------------ #
    # 结果渲染
    # ------------------------------------------------------------------ #
    def _render_result(self) -> None:
        """按缓存结果刷新表格（「显示相同项」切换时复用）。"""
        for item_id in self._tree.get_children():
            self._tree.delete(item_id)
        if self._result is None:
            return
        actions = {action.relative: action for action in self._plan.actions} if self._plan else {}
        conflicts = {item.relative for item in self._plan.conflicts} if self._plan else set()
        show_equal = bool(self._show_equal_var.get())
        for item in self._result.items:
            if item.category == CATEGORY_EQUAL and not show_equal:
                continue
            action = actions.get(item.relative)
            if action is not None:
                action_text = action.action_text
            elif item.relative in conflicts:
                action_text = "冲突（跳过）"
            else:
                action_text = ACTION_TEXT["none"]
            tag = CATEGORY_CONFLICT if item.relative in conflicts else item.category
            self._tree.insert(
                "",
                "end",
                values=(
                    CATEGORY_TEXT[item.category],
                    item.relative,
                    action_text,
                    format_size(item.left.size) if item.left is not None else "-",
                    format_size(item.right.size) if item.right is not None else "-",
                ),
                tags=(tag,),
            )

    @staticmethod
    def _summary(result: CompareResult, plan: SyncPlan) -> str:
        """生成一行概要：差异数量 + 计划动作数。"""
        counts = result.counts()
        return (
            f"共 {len(result.items)} 项：仅左 {counts[CATEGORY_LEFT_ONLY]}，"
            f"仅右 {counts[CATEGORY_RIGHT_ONLY]}，不同 {counts[CATEGORY_DIFFERENT]}，"
            f"相同 {counts[CATEGORY_EQUAL]}；计划复制 {plan.copy_count}，"
            f"删除 {plan.delete_count}，冲突 {len(plan.conflicts)}"
        )

    # ------------------------------------------------------------------ #
    # 配置文件与关于
    # ------------------------------------------------------------------ #
    def _load_profile(self) -> None:
        """从 JSON 文件载入左右目录与选项。"""
        selected = self._filedialog.askopenfilename(
            parent=self.root,
            title="载入配置",
            filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
        )
        if not selected:
            return
        raw = _guiconfig.read_settings(Path(selected))
        if raw is None:
            self._messagebox.showerror("载入配置", "配置文件无法解析。", parent=self.root)
            return
        settings = settings_from_mapping(raw)
        self._left_var.set(settings.left)
        self._right_var.set(settings.right)
        self._compare_var.set(mode_label(settings.compare_mode, COMPARE_MODE_TEXT))
        self._sync_var.set(mode_label(settings.sync_mode, SYNC_MODE_TEXT))
        self._include_var.set(settings.include)
        self._exclude_var.set(settings.exclude)
        self._skip_hidden_var.set(settings.skip_hidden)
        self._show_equal_var.set(settings.show_equal)
        _guiwindow.append_log(self._log, f"已载入配置：{selected}", timestamp=True)

    def _save_profile(self) -> None:
        """把当前左右目录与选项保存到 JSON 文件。"""
        selected = self._filedialog.asksaveasfilename(
            parent=self.root,
            title="保存配置",
            defaultextension=".json",
            initialfile="filesync.json",
            filetypes=(("JSON 配置", "*.json"), ("所有文件", "*.*")),
        )
        if not selected:
            return
        save_settings(self._current_settings(), Path(selected))
        _guiwindow.append_log(self._log, f"已保存配置：{selected}", timestamp=True)

    def _show_about(self) -> None:
        self._messagebox.showinfo(
            "关于",
            f"filesync v{__version__}\n文件夹比较与同步（参考 FreeFileSync）\n"
            "比较：时间与大小 / 仅大小 / 内容\n同步：双向 / 镜像 / 更新（支持预演）",
            parent=self.root,
        )

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def _current_settings(self) -> FilesyncSettings:
        """收集当前界面状态（geometry 由调用方决定是否写入）。"""
        return FilesyncSettings(
            left=self._left_var.get(),
            right=self._right_var.get(),
            compare_mode=self._compare_mode(),
            sync_mode=self._sync_mode(),
            include=self._include_var.get(),
            exclude=self._exclude_var.get(),
            skip_hidden=bool(self._skip_hidden_var.get()),
            show_equal=bool(self._show_equal_var.get()),
            geometry=self._settings.geometry,
        )

    def close(self) -> None:
        """保存设置并退出窗口；运行中先确认。"""
        if self._running and not self._messagebox.askyesno("退出", "任务正在运行，确定退出？", parent=self.root):
            return
        settings = self._current_settings()
        settings.geometry = self.root.winfo_geometry()
        save_settings(settings, self._settings_path)
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: FilesyncWindow(settings_path), "文件夹比较与同步")


@fcmd.tool("filesyncw", help="打开文件夹比较与同步窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开文件夹比较与同步窗口。"""
    run_gui()


@fcmd.main("filesyncw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
