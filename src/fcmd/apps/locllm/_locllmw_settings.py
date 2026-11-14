"""locllmw 设置页：目录 / 引擎 / 界面三页配置。

所有目录解析都委托给 :mod:`fcmd.apps.locllm._llmconfig`，页面不保存私有默认值；
修改直接写入权威 ``config.json``。「引擎」页等价承载原独立引擎窗口的能力：
引擎根目录、已安装引擎列表与默认切换、在线下载、离线包检查 / 安装、运行体检，
全部调用 :mod:`fcmd.apps.locllm.llmengine`（与 ``fcmd llmengine`` 同一套逻辑）。
"""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.locllm import _llmconfig, llmengine
from fcmd.apps.locllm.llmbase import DEFAULT_ENGINE_TAG, Check, EngineInfo, EngineVariant, ServeError, human_size
from fcmd.gui import config as _guiconfig
from fcmd.gui import window as _guiwindow

__all__ = [
    "EngineStatus",
    "SettingsPage",
    "apply_path",
    "check_engine_tag",
    "directory_rows",
    "engine_rows",
    "engine_status",
    "inspect_offline",
    "install_offline",
    "install_online",
    "installed_engines",
    "migration_note",
    "select_engine",
    "ui_rows",
]

_ENV = "环境变量"
_CONFIG = "配置"
_DEFAULT = "默认"
_AUTO = "自动保存"

_VARIANTS = ("cuda", "cpu")
_OFFLINE_PLACEHOLDER = "尚未选择离线包（zip 文件或包含 zip 的目录）"
_KIND_LABELS = {
    "engine": "引擎",
    "cudart": "CUDA 运行库",
    "source": "源码包",
    "unknown": "未知",
    "invalid": "无效",
}

#: 设置页可编辑的目录项：配置键 → (行标题, 选择对话框标题)
_EDITABLE: dict[str, tuple[str, str]] = {
    "models_dir": ("模型目录", "选择模型目录（仅支持单一目录）"),
    "engines_root": ("引擎根目录", "选择 llama.cpp 引擎根目录"),
}

TaskFunc = Callable[[Callable[[str, int, int | None], None]], tuple[str, str | None]]


# ---------------------------------------------------------------------- #
# 纯逻辑：目录 / 引擎 / 界面
# ---------------------------------------------------------------------- #
def _source(env_name: str, config_key: str, home: Path | None = None) -> str:
    """来源标注：环境变量 > 配置 > 默认（显式 home 时忽略环境变量）。"""
    if home is None and os.environ.get(env_name, "").strip():
        return _ENV
    value = _llmconfig.get_option(config_key, home)
    if isinstance(value, str) and value.strip():
        return _CONFIG
    return _DEFAULT


def directory_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回「目录」页 ``(项目, 路径, 来源)``：只有模型目录。"""
    return [
        ("模型目录", str(_llmconfig.models_dir(home=home)), _source("FCMD_MODELS", "models_dir", home)),
    ]


def engine_rows(home: Path | None = None) -> list[tuple[str, str, str]]:
    """返回「引擎」页根目录 ``(项目, 路径, 来源)``。"""
    return [("引擎根目录", str(_llmconfig.engines_root(home)), _source("FCMD_ENGINES", "engines_root", home))]


def ui_rows() -> list[tuple[str, str, str]]:
    """返回「界面」页：窗口配置文件与已保存的窗口几何（只读）。"""
    path = _guiconfig.config_path("locllmw")
    settings = _guiconfig.read_settings(path) or {}
    geometry = settings.get("geometry")
    geometry_text = geometry if isinstance(geometry, str) and geometry.strip() else "未保存（使用默认尺寸）"
    return [
        ("窗口配置文件", str(path), _AUTO),
        ("窗口位置与大小", geometry_text, _AUTO),
    ]


def migration_note(home: Path | None = None) -> str:
    """旧配置迁移提示：多余模型目录 + 已忽略的旧工作目录；无记录时返回空串。"""
    notes: list[str] = []
    extras = _llmconfig.models_dir_extra(home)
    if extras:
        notes.append("旧配置中的其他模型目录已记录但不再扫描：" + "、".join(extras))
    ignored = _llmconfig.ignored_work_dir(home)
    if ignored:
        notes.append(f"旧工作目录已忽略（下载与转换产物统一使用模型目录）：{ignored}")
    return "\n".join(notes)


def apply_path(key: str, value: str | None, home: Path | None = None) -> Path:
    """写入设置页目录项并返回当前生效路径；``value`` 为 ``None`` 表示恢复默认。"""
    if key == "models_dir":
        return _llmconfig.set_models_dir(Path(value) if value else None, home)
    if key == "engines_root":
        _llmconfig.set_option("engines_root", value or None, home)
        return _llmconfig.engines_root(home)
    raise ValueError(f"未知设置项: {key}")


@dataclass(frozen=True)
class EngineStatus:
    """引擎页摘要：根目录、当前引擎描述、已安装数量。"""

    root: Path
    current: str
    installed: int


def installed_engines(home: Path | None = None) -> list[EngineInfo]:
    """已安装引擎（按 tag 降序，当前引擎 ``source`` 为 ``current``）。"""
    return llmengine.list_engines(home)


def engine_status(home: Path | None = None) -> EngineStatus:
    """读取引擎页摘要；当前引擎无法解析时给出原因文案。"""
    infos = installed_engines(home)
    try:
        current = llmengine.resolve_engine(home=home)
    except ServeError as exc:
        text = f"未找到（{exc}）"
    else:
        text = f"{current.tag} · {current.exe}"
    return EngineStatus(_llmconfig.engines_root(home), text, len(infos))


def select_engine(tag: str, home: Path | None = None) -> EngineInfo:
    """把 ``tag`` 设为默认引擎（复用 ``fcmd llmengine use`` 的实现）。"""
    return llmengine.use_engine(tag, home)


def check_engine_tag(tag: str | None, home: Path | None = None) -> Check:
    """解析并体检引擎，返回 ``llama-server --version`` 的结果。"""
    return llmengine.check_engine(llmengine.resolve_engine(tag, home))


def install_online(
    tag: str,
    variant: EngineVariant = "cuda",
    home: Path | None = None,
    on_progress: llmengine.ProgressCallback | None = None,
) -> EngineInfo:
    """在线安装引擎；没有默认引擎时自动把新装的设为默认。"""
    info = llmengine.install_engine(tag, variant=variant, home=home, on_progress=on_progress)
    _ensure_current(info.tag, home)
    return info


def inspect_offline(source: Path, *, deep: bool = False) -> list[llmengine.ZipInspection]:
    """检查离线包（zip 或包含 zip 的目录）。"""
    return llmengine.inspect_zips([source], deep=deep)


def install_offline(
    tag: str,
    source: Path,
    home: Path | None = None,
    *,
    infer_tag: bool = False,
) -> tuple[EngineInfo, str]:
    """安装离线包或登记已编译引擎目录，返回 ``(引擎信息, 实际使用的 tag)``。

    ``source`` 为目录时优先安装目录内全部 zip，没有 zip 则按已编译引擎目录登记；
    为 zip 时直接解压安装。``infer_tag`` 为真且包内能推断出 tag 时用它替换入参。
    """
    target = tag
    if infer_tag:
        inferred = next((item.tag for item in inspect_offline(source) if item.tag), None)
        if inferred:
            target = inferred
    if source.is_dir():
        zips = sorted(item for item in source.glob("*.zip") if item.is_file())
        info = (
            llmengine.install_engine(target, archives=zips, home=home)
            if zips
            else llmengine.install_engine(target, path=source, home=home)
        )
    else:
        info = llmengine.install_engine(target, archives=[source], home=home)
    _ensure_current(target, home)
    return info, target


def _ensure_current(tag: str, home: Path | None = None) -> None:
    """没有默认引擎时把刚安装的引擎设为默认。"""
    if not any(item.source == "current" for item in installed_engines(home)):
        llmengine.use_engine(tag, home)


def format_inspection(item: llmengine.ZipInspection) -> str:
    """把单个离线包检查结果格式化为一行正文 + 附注。"""
    state = "可用" if item.usable else "不可安装"
    lines = [
        f"{item.path.name}: {_KIND_LABELS[item.kind]} / {state} / TAG={item.tag or '-'} / "
        f"变体={item.variant or '-'} / llama-server={item.server or '-'} / {human_size(item.total_size)}"
    ]
    lines.extend(f"  · {note}" for note in item.notes)
    lines.extend(f"  ! {problem}" for problem in item.problems)
    return "\n".join(lines)


# ---------------------------------------------------------------------- #
# 设置页
# ---------------------------------------------------------------------- #
class SettingsPage:  # pragma: no cover - Tk 界面不纳入单元覆盖（CI 无显示环境）
    """设置页容器：分页承载目录 / 引擎 / 界面，共用同一份权威配置。"""

    def __init__(
        self,
        parent: Any,
        *,
        tk: Any,
        ttk: Any,
        theme: Any,
        on_config_changed: Any = None,
    ) -> None:
        self._tk = tk
        self._ttk = ttk
        self._theme = theme
        self._on_config_changed = on_config_changed
        self._root = parent.winfo_toplevel()
        self._status_var = tk.StringVar(value="")
        self._vars: dict[str, Any] = {}
        self._migration_var = tk.StringVar(value="")
        self._ui_vars: dict[str, Any] = {}
        self._groups: dict[str, Any] = {}
        self._engine_info_vars: dict[str, Any] = {}
        self._engine_buttons: list[Any] = []
        self._engine_thread: threading.Thread | None = None
        self._engine_messages: queue.Queue[tuple[str, Any]] | None = None
        self._engine_running = False
        self._build_tabs(parent)
        ttk.Label(parent, textvariable=self._status_var, style="Hint.TLabel").pack(fill="x", pady=(theme.spacing.xs, 0))
        self.refresh()

    # ------------------------------------------------------------------ #
    # 构建
    # ------------------------------------------------------------------ #
    def _build_tabs(self, parent: Any) -> None:
        """三个分页：目录 / 引擎 / 界面（原三组字段与行为不变）。"""
        self._notebook = self._ttk.Notebook(parent)
        self._notebook.pack(fill="both", expand=True)
        directory, engine, ui = (self._ttk.Frame(self._notebook) for _ in range(3))
        self._notebook.add(directory, text="目录")
        self._notebook.add(engine, text="引擎")
        self._notebook.add(ui, text="界面")
        self._groups.update({"目录": directory, "引擎": engine, "界面": ui})
        self._build_directory(directory)
        self._build_engine(engine)
        self._build_ui(ui)

    def _group(self, parent: Any, title: str) -> Any:
        frame = self._ttk.LabelFrame(parent, text=title, padding=self._theme.spacing.sm)
        frame.pack(fill="x", pady=(0, self._theme.spacing.sm))
        frame.columnconfigure(1, weight=1)
        return frame

    def _editable_row(self, group: Any, key: str, row: int) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        label, dialog_title = _EDITABLE[key]
        ttk.Label(group, text=label).grid(row=row, column=0, sticky="w", padx=(0, space.sm))
        var = self._tk.StringVar()
        ttk.Entry(group, textvariable=var, state="readonly").grid(row=row, column=1, sticky="we")
        ttk.Button(group, text="选择…", command=lambda: self._choose(key, dialog_title)).grid(
            row=row, column=2, padx=(space.sm, 0)
        )
        ttk.Button(group, text="恢复默认", command=lambda: self._apply(key, None)).grid(
            row=row, column=3, padx=(space.xs, 0)
        )
        self._vars[key] = var

    def _build_directory(self, parent: Any) -> None:
        group = self._group(parent, "目录")
        self._editable_row(group, "models_dir", 0)
        self._ttk.Label(
            group,
            text="模型目录只支持单一目录；下载的模型直接写入该目录，转换等产物写入其中的 "
            f"{_llmconfig.PRODUCTS_DIRNAME} 子目录。",
        ).grid(row=1, column=0, columnspan=4, sticky="w", pady=(self._theme.spacing.xs, 0))
        self._ttk.Label(group, textvariable=self._migration_var, style="Hint.TLabel", justify="left").grid(
            row=2, column=0, columnspan=4, sticky="w"
        )

    def _build_engine(self, parent: Any) -> None:
        """引擎页：根目录、已安装引擎、在线下载、离线包、日志与进度。"""
        root_group = self._group(parent, "引擎根目录")
        self._editable_row(root_group, "engines_root", 0)
        self._ttk.Label(root_group, text="llama.cpp 引擎根目录；留空使用内置默认。").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(self._theme.spacing.xs, 0)
        )
        self._build_engine_list(parent)
        self._build_online(parent)
        self._build_offline(parent)
        self._build_engine_log(parent)

    def _build_engine_list(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="已安装引擎（双击设为默认）", padding=space.sm)
        box.pack(fill="x", pady=(0, space.sm))
        self._engine_tree = ttk.Treeview(
            box, columns=("tag", "current", "exe"), show="headings", selectmode="browse", height=5
        )
        for column, title, width, stretch in (
            ("tag", "TAG", 110, False),
            ("current", "当前", 50, False),
            ("exe", "可执行文件", 620, True),
        ):
            self._engine_tree.heading(column, text=title)
            self._engine_tree.column(column, width=width, anchor="w", stretch=stretch)
        self._engine_tree.bind("<Double-1>", lambda _event: self._use_selected())
        scrollbar = ttk.Scrollbar(box, orient="vertical", command=self._engine_tree.yview)
        self._engine_tree.configure(yscrollcommand=scrollbar.set)
        self._engine_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        info = ttk.Frame(parent)
        info.pack(fill="x", pady=(0, space.sm))
        info.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("current", "当前引擎"), ("installed", "已安装"))):
            ttk.Label(info, text=label).grid(row=row, column=0, sticky="w", padx=(0, space.sm))
            var = self._tk.StringVar(value="检测中…")
            ttk.Label(info, textvariable=var, justify="left").grid(row=row, column=1, sticky="we")
            self._engine_info_vars[key] = var

        actions = ttk.Frame(parent)
        actions.pack(fill="x", pady=(0, space.sm))
        for text, command in (
            ("刷新", self.refresh_engine),
            ("设为默认", self._use_selected),
            ("检测版本", self._check_selected),
        ):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side="left", padx=(0, space.sm))
            self._engine_buttons.append(button)
        self._engine_progress = ttk.Progressbar(actions, mode="determinate", maximum=100, length=180)
        self._engine_progress.pack(side="right")

    def _build_online(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="在线下载（GitHub release）", padding=space.sm)
        box.pack(fill="x", pady=(0, space.sm))
        box.columnconfigure(1, weight=1)
        self._tag_var = self._tk.StringVar(value=DEFAULT_ENGINE_TAG)
        self._variant_var = self._tk.StringVar(value="cuda")
        self._tag_edited = False
        ttk.Label(box, text="TAG").grid(row=0, column=0, sticky="w", padx=(0, space.sm), pady=space.xxs)
        entry = ttk.Entry(box, textvariable=self._tag_var)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, space.md), pady=space.xxs)
        entry.bind("<KeyRelease>", self._mark_tag_edited)
        ttk.Label(box, text="变体").grid(row=0, column=2, sticky="w", padx=(0, space.sm), pady=space.xxs)
        ttk.Combobox(box, textvariable=self._variant_var, values=list(_VARIANTS), state="readonly", width=8).grid(
            row=0, column=3, sticky="w", pady=space.xxs
        )
        button = ttk.Button(box, text="在线安装", command=self._online_install, style="Accent.TButton")
        button.grid(row=0, column=4, padx=(space.md, 0), pady=space.xxs)
        self._engine_buttons.append(button)
        ttk.Label(box, text="cuda 变体自动下载引擎包 + cudart 运行库").grid(row=1, column=1, columnspan=4, sticky="w")

    def _build_offline(self, parent: Any) -> None:
        ttk = self._ttk
        space = self._theme.spacing
        box = ttk.LabelFrame(parent, text="离线包（zip）", padding=space.sm)
        box.pack(fill="x", pady=(0, space.sm))
        box.columnconfigure(0, weight=1)
        self._offline_var = self._tk.StringVar(value=_OFFLINE_PLACEHOLDER)
        self._deep_var = self._tk.BooleanVar(value=False)
        row = ttk.Frame(box)
        row.grid(row=0, column=0, sticky="ew")
        row.columnconfigure(0, weight=1)
        ttk.Entry(row, textvariable=self._offline_var, state="readonly").grid(row=0, column=0, sticky="ew")
        zip_btn = ttk.Button(row, text="选择 zip…", command=self._select_zip)
        zip_btn.grid(row=0, column=1, padx=(space.sm, 0))
        dir_btn = ttk.Button(row, text="选择目录…", command=self._select_dir)
        dir_btn.grid(row=0, column=2, padx=(space.sm, 0))

        actions = ttk.Frame(box)
        actions.grid(row=1, column=0, sticky="ew", pady=(space.sm, 0))
        ttk.Checkbutton(actions, text="深度 CRC 校验（大包较慢）", variable=self._deep_var).pack(side="left")
        install_btn = ttk.Button(actions, text="安装离线包", command=self._offline_install)
        install_btn.pack(side="right")
        check_btn = ttk.Button(actions, text="检查", command=self._check_offline)
        check_btn.pack(side="right", padx=(0, space.sm))
        self._engine_buttons.extend([zip_btn, dir_btn, install_btn, check_btn])

    def _build_engine_log(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="输出日志", padding=self._theme.spacing.sm)
        box.pack(fill="both", expand=True)
        scrollbar = ttk.Scrollbar(box)
        scrollbar.pack(side="right", fill="y")
        self._log_text = self._tk.Text(box, wrap="word", state="disabled", yscrollcommand=scrollbar.set, height=6)
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

    def _build_ui(self, parent: Any) -> None:
        group = self._group(parent, "界面")
        for row, (label, _value, _source) in enumerate(ui_rows()):
            self._ttk.Label(group, text=label).grid(row=row, column=0, sticky="w", padx=(0, self._theme.spacing.sm))
            var = self._tk.StringVar()
            self._ttk.Entry(group, textvariable=var, state="readonly").grid(row=row, column=1, sticky="we")
            self._ui_vars[label] = var
        self._ttk.Label(group, text="窗口位置与大小在关闭时自动写入，无需手动编辑。", style="Hint.TLabel").grid(
            row=len(self._ui_vars), column=0, columnspan=2, sticky="w", pady=(self._theme.spacing.xs, 0)
        )

    # ------------------------------------------------------------------ #
    # 目录 / 界面行为
    # ------------------------------------------------------------------ #
    def _notify(self, message: str) -> None:
        self._status_var.set(message)

    def _choose(self, key: str, title: str) -> None:
        from tkinter import filedialog

        label = _EDITABLE[key][0]
        chosen = filedialog.askdirectory(parent=self._groups["目录"], title=title, initialdir=str(self._current(key)))
        if not chosen:
            return
        self._apply(key, chosen, label=label)

    def _current(self, key: str) -> Path:
        if key == "models_dir":
            return _llmconfig.models_dir()
        return _llmconfig.engines_root()

    def _apply(self, key: str, value: str | None, *, label: str | None = None) -> None:
        label = label or _EDITABLE[key][0]
        try:
            resolved = apply_path(key, value)
        except (OSError, ValueError) as exc:
            self._notify(f"{label} 保存失败：{exc}")
            return
        self._notify(f"{label} 已保存，当前生效：{resolved}")
        self.refresh()
        if key == "engines_root":
            self.refresh_engine()
        if self._on_config_changed is not None:
            self._on_config_changed()

    def refresh(self) -> None:
        """从权威配置重新读取三页显示值。"""
        values = {label: path for label, path, _source in (*directory_rows(), *engine_rows())}
        for key, (label, _title) in _EDITABLE.items():
            self._vars[key].set(values.get(label, ""))
        for label, value, _source_text in ui_rows():
            var = self._ui_vars.get(label)
            if var is not None:
                var.set(value)
        self._migration_var.set(migration_note())
        self.refresh_engine()

    # ------------------------------------------------------------------ #
    # 引擎：列表 / 切换 / 体检
    # ------------------------------------------------------------------ #
    def refresh_engine(self) -> None:
        """刷新已安装引擎列表与当前引擎摘要。"""
        status = engine_status()
        for item in self._engine_tree.get_children():
            self._engine_tree.delete(item)
        for info in installed_engines():
            self._engine_tree.insert(
                "",
                "end",
                iid=info.tag,
                values=(info.tag, "*" if info.source == "current" else "", str(info.exe)),
            )
        self._engine_info_vars["current"].set(status.current)
        self._engine_info_vars["installed"].set(f"{status.installed} 个")
        self._vars["engines_root"].set(str(status.root))

    def _selected_tag(self) -> str | None:
        selection = self._engine_tree.selection()
        if not selection:
            from tkinter import messagebox

            messagebox.showinfo("未选择引擎", "请先在列表中选择一个引擎", parent=self._root)
            return None
        return selection[0]

    def _use_selected(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            return

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            info = select_engine(tag)
            return f"默认引擎: {info.tag} -> {info.exe}", None

        self._run_task(f"切换默认引擎 {tag}", job)

    def _check_selected(self) -> None:
        tag = self._selected_tag()
        if tag is None:
            return

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            check: Check = check_engine_tag(tag)
            return f"{check.status} {check.name}: {check.detail}", None

        self._run_task(f"检测引擎 {tag}", job, notify=False)

    def _mark_tag_edited(self, _event: object | None = None) -> None:
        self._tag_edited = True

    # ------------------------------------------------------------------ #
    # 引擎：安装
    # ------------------------------------------------------------------ #
    def _select_zip(self) -> None:
        from tkinter import filedialog

        path = filedialog.askopenfilename(
            parent=self._root,
            title="选择 llama.cpp 离线包",
            filetypes=[("zip 压缩包", "*.zip"), ("所有文件", "*.*")],
        )
        if path:
            self._offline_var.set(path)

    def _select_dir(self) -> None:
        from tkinter import filedialog

        path = filedialog.askdirectory(parent=self._root, title="选择包含 zip 离线包或已编译引擎的目录")
        if path:
            self._offline_var.set(path)

    def _offline_source(self) -> Path | None:
        from tkinter import messagebox

        raw = self._offline_var.get()
        if raw == _OFFLINE_PLACEHOLDER:
            messagebox.showwarning("缺少离线包", "请选择 zip 文件或包含 zip 的目录", parent=self._root)
            return None
        path = Path(raw).expanduser()
        if not path.exists():
            messagebox.showwarning("路径不存在", str(path), parent=self._root)
            return None
        return path

    def _tag_or_warn(self) -> str | None:
        from tkinter import messagebox

        tag = self._tag_var.get().strip()
        if not tag:
            messagebox.showwarning("缺少 TAG", "请填写引擎 TAG（如 b12211）", parent=self._root)
            return None
        return tag

    def _online_install(self) -> None:
        tag = self._tag_or_warn()
        if tag is None:
            return
        variant = self._variant_var.get()
        from tkinter import messagebox

        if variant not in _VARIANTS:
            messagebox.showwarning("变体无效", f"请选择 {', '.join(_VARIANTS)}", parent=self._root)
            return
        typed: EngineVariant = "cuda" if variant == "cuda" else "cpu"

        def job(progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            info = install_online(tag, typed, on_progress=progress)
            return f"引擎就绪: {info.tag} -> {info.exe}", None

        self._run_task(f"在线安装 {tag}（{variant}）", job)

    def _offline_install(self) -> None:
        source = self._offline_source()
        if source is None:
            return
        tag = self._tag_or_warn()
        if tag is None:
            return
        infer = not self._tag_edited

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            info, used = install_offline(tag, source, infer_tag=infer)
            return f"引擎就绪: {info.tag} -> {info.exe}", used

        self._run_task(f"安装离线包 {source.name}", job)

    def _check_offline(self) -> None:
        source = self._offline_source()
        if source is None:
            return
        deep = self._deep_var.get()

        def job(_progress: llmengine.ProgressCallback) -> tuple[str, str | None]:
            inspections = inspect_offline(source, deep=deep)
            if not inspections:
                return "没有找到 zip 压缩包", None
            inferred = next((item.tag for item in inspections if item.tag), None)
            return "\n".join(format_inspection(item) for item in inspections), inferred

        self._run_task("检查离线包", job, notify=False)

    # ------------------------------------------------------------------ #
    # 引擎：后台任务与反馈
    # ------------------------------------------------------------------ #
    def _run_task(self, label: str, func: TaskFunc, notify: bool = True) -> None:
        """后台线程执行任务，队列回传日志 / 进度 / 结果，完成后刷新列表。"""
        if self._engine_running:
            self._log("已有任务正在进行，请等待完成")
            return
        self._engine_running = True
        self._set_engine_buttons(False)
        self._engine_progress.configure(mode="determinate")
        self._engine_progress["value"] = 0
        self._status_var.set(label)
        self._log(f"== {label} ==")
        messages: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._engine_messages = messages

        def progress(name: str, done: int, total: int | None) -> None:
            messages.put(("progress", (name, done, total)))

        def work() -> None:
            try:
                summary, tag = func(progress)
            except (ServeError, OSError, ValueError) as exc:
                messages.put(("done", (False, str(exc), None, notify)))
            except Exception as exc:  # 后台线程兜底，避免界面失去反馈
                messages.put(("done", (False, f"{type(exc).__name__}: {exc}", None, notify)))
            else:
                messages.put(("done", (True, summary, tag, notify)))

        self._engine_thread = threading.Thread(target=work, daemon=True)
        self._engine_thread.start()
        self._root.after(100, self._poll)

    def _poll(self) -> None:
        assert self._engine_messages is not None
        try:
            while True:
                kind, payload = self._engine_messages.get_nowait()
                if kind == "progress":
                    self._apply_progress(*payload)
                elif kind == "done":
                    self._finish(*payload)
        except queue.Empty:
            pass
        if self._engine_thread is not None and self._engine_thread.is_alive():
            self._root.after(100, self._poll)

    def _apply_progress(self, name: str, done: int, total: int | None) -> None:
        if total:
            if str(self._engine_progress["mode"]) != "determinate":
                self._engine_progress.stop()
                self._engine_progress.configure(mode="determinate")
            self._engine_progress["value"] = min(100.0, done * 100.0 / total)
            self._status_var.set(f"{name}: {human_size(done)} / {human_size(total)}")
        else:
            if str(self._engine_progress["mode"]) != "indeterminate":
                self._engine_progress.configure(mode="indeterminate")
                self._engine_progress.start(12)
            self._status_var.set(f"{name}: {human_size(done)}")

    def _finish(self, ok: bool, message: str, tag: str | None, notify: bool) -> None:
        self._engine_running = False
        self._engine_progress.stop()
        self._engine_progress.configure(mode="determinate")
        self._engine_progress["value"] = 100 if ok else 0
        self._status_var.set("完成" if ok else "失败")
        self._set_engine_buttons(True)
        if tag:
            self._tag_var.set(tag)
        if message:
            self._log(message)
        self.refresh_engine()
        if not notify:
            return
        from tkinter import messagebox

        if ok:
            messagebox.showinfo("完成", message or "任务已完成", parent=self._root)
        else:
            messagebox.showerror("失败", message or "任务失败", parent=self._root)

    def _set_engine_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._engine_buttons:
            button.configure(state=state)

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        _guiwindow.append_log(self._log_text, message)
