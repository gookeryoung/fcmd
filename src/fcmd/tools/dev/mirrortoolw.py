"""mirrortoolw —— 镜像搜索与配置窗口版（Tk）。

复用 ``envdev mirror`` / ``mirrortool`` 的候选镜像表、实测下载吞吐探测与配置
写入实现：按服务（pip/conda/rustup/cargo/go/maven/npm）测速排序并一键应用，
或探测 MirrorZ 教育网镜像站点；结果可按镜像名/地址过滤。

设置（模式、服务、超时、来源、搜索词、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/mirrortoolw.json``，下次启动自动恢复。

示例
----
    fcmd mirrortoolw      # 打开镜像搜索与配置窗口
    mirrortoolw           # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
import io
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "DEFAULT_GEOMETRY",
    "DEFAULT_TIMEOUT",
    "MAX_TIMEOUT",
    "MIN_TIMEOUT",
    "MODE_SERVICE",
    "MODE_SITES",
    "SERVICE_LABELS",
    "MirrorResult",
    "MirrorToolSettings",
    "apply_service_mirror",
    "config_location",
    "config_path",
    "filter_results",
    "load_settings",
    "normalize_timeout",
    "probe_service",
    "probe_sites",
    "recommend_mirror",
    "resolve_geometry",
    "run_gui",
    "save_settings",
    "service_candidates",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "镜像搜索与配置窗口版（Tk）：按服务（pip/conda/rustup/cargo/go/maven/npm）"
    "实测下载吞吐排序并一键应用镜像，或探测 MirrorZ 教育网镜像站点；"
    "结果支持按镜像名/地址过滤，设置持久化到 ~/.fcmd/ui/mirrortoolw.json"
)

MODE_SERVICE = "service"
MODE_SITES = "sites"
MODES: tuple[str, ...] = (MODE_SERVICE, MODE_SITES)

SERVICE_LABELS: dict[str, str] = {
    "pip": "Python (pip/uv)",
    "conda": "Python (conda)",
    "rustup": "Rust (rustup)",
    "cargo": "Rust (cargo/crates.io)",
    "go": "Go (GOPROXY)",
    "maven": "Java (Maven)",
    "npm": "Node.js (npm/yarn/pnpm)",
}

SOURCE_LABELS: dict[str, str] = {
    "auto": "自动拉取 MirrorZ",
    "builtin": "内置站点列表",
}

DEFAULT_TIMEOUT = 3.0
MIN_TIMEOUT = 0.5
MAX_TIMEOUT = 30.0
DEFAULT_GEOMETRY = "1040x720+180+100"
MIN_WIDTH = 900
MIN_HEIGHT = 620

_ENV_ONLY_LOCATION = "环境变量（重开终端后生效）"


# ---------------------------------------------------------------------- #
# 结果模型
# ---------------------------------------------------------------------- #
@dataclass(frozen=True, slots=True)
class MirrorResult:
    """单条镜像 / 站点探测结果。"""

    name: str
    url: str
    ok: bool
    latency_ms: float
    throughput_kbps: float = 0.0
    reason: str = ""

    @property
    def status_text(self) -> str:
        """可读状态：可访问，或不可访问并附原因。"""
        if self.ok:
            return "可访问"
        return f"不可访问（{self.reason or '未知原因'}）"

    @property
    def latency_text(self) -> str:
        """可读延迟；不可达时用 ``—`` 占位。"""
        return f"{self.latency_ms:.0f} ms" if self.ok else "—"

    @property
    def throughput_text(self) -> str:
        """可读下载吞吐；站点探测与不可达项用 ``—`` 占位。"""
        if not self.ok or self.throughput_kbps <= 0:
            return "—"
        return f"{self.throughput_kbps:.0f} KB/s"


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/mirrortoolw.json``。"""
    return _guiconfig.config_path("mirrortoolw")


@dataclass
class MirrorToolSettings:
    """镜像搜索与配置窗口的持久化设置。"""

    mode: str = MODE_SERVICE
    service: str = "pip"
    timeout: float = DEFAULT_TIMEOUT
    source: str = "auto"
    search: str = ""
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def normalize_timeout(value: object) -> float:
    """校验并规范化超时秒数（``0.5``–``30``），非法时抛出 ``ValueError``。"""
    message = f"超时必须是 {MIN_TIMEOUT:g}–{MAX_TIMEOUT:g} 秒之间的数字"
    if isinstance(value, bool):
        raise ValueError(message)
    if isinstance(value, (int, float)):
        timeout = float(value)
    elif isinstance(value, str):
        try:
            timeout = float(value.strip())
        except ValueError as exc:
            raise ValueError(message) from exc
    else:
        raise ValueError(message)
    if not MIN_TIMEOUT <= timeout <= MAX_TIMEOUT:
        raise ValueError(message)
    return timeout


def load_settings(path: Path | None = None) -> MirrorToolSettings:
    """读取设置文件；缺失、损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return MirrorToolSettings()

    settings = MirrorToolSettings()
    mode = raw.get("mode")
    service = raw.get("service")
    source = raw.get("source")
    search = raw.get("search")
    geometry = raw.get("geometry")
    if isinstance(mode, str) and mode in MODES:
        settings.mode = mode
    if isinstance(service, str) and service in SERVICE_LABELS:
        settings.service = service
    if isinstance(source, str) and source in SOURCE_LABELS:
        settings.source = source
    if isinstance(search, str):
        settings.search = search
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if "timeout" in raw:
        with contextlib.suppress(ValueError):
            settings.timeout = normalize_timeout(raw["timeout"])
    return settings


def save_settings(settings: MirrorToolSettings, path: Path | None = None) -> None:
    """保存设置；写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "mode": settings.mode,
        "service": settings.service,
        "timeout": settings.timeout,
        "source": settings.source,
        "search": settings.search,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# 镜像候选与探测（与 CLI 共用同一份候选表与实现）
# ---------------------------------------------------------------------- #
def service_candidates(service: str) -> dict[str, str]:
    """返回指定服务的 ``{镜像名: 测速地址}`` 候选表副本。

    Raises
    ------
    ValueError
        服务名不在 ``pip`` / ``conda`` / ``rustup`` / ``cargo`` / ``go`` /
        ``maven`` / ``npm`` 中。
    """
    from fcmd.tools.dev._envdev_mirrors import _MIRROR_SERVICE_CANDIDATES

    if service not in _MIRROR_SERVICE_CANDIDATES:
        raise ValueError(f"未知服务: {service}")
    return dict(_MIRROR_SERVICE_CANDIDATES[service])


def probe_service(service: str, timeout: float = DEFAULT_TIMEOUT) -> list[MirrorResult]:
    """探测单个服务的全部候选镜像，返回按实测吞吐与延迟排序的结果。"""
    from fcmd.infras.net import probe_mirrors
    from fcmd.tools.dev.envdev_core import _MIRROR_DEFAULT_UA, _MIRROR_USER_AGENTS

    candidates = service_candidates(service)
    name_by_url = {url: name for name, url in candidates.items()}
    probes = probe_mirrors(
        list(candidates.values()),
        timeout=timeout,
        user_agent=_MIRROR_USER_AGENTS.get(service, _MIRROR_DEFAULT_UA),
        allow_html=service == "pip",
    )
    return [
        MirrorResult(
            name=name_by_url[probe.url],
            url=probe.url,
            ok=probe.ok,
            latency_ms=probe.latency_ms,
            throughput_kbps=probe.throughput_kbps,
            reason=probe.reason,
        )
        for probe in probes
        if probe.url in name_by_url
    ]


def probe_sites(timeout: float = DEFAULT_TIMEOUT, source: str = "auto") -> tuple[list[MirrorResult], str]:
    """探测教育网镜像站点，返回 ``(按速度排序的站点, 列表来源说明)``。"""
    from fcmd.infras.net import check_urls
    from fcmd.tools.dev._envdev_mirrors import _CERNET_MIRROR_SITES, fetch_mirrorz_sites

    sites = dict(_CERNET_MIRROR_SITES)
    origin = "help.mirrors.cernet.edu.cn 内置列表"
    if source == "auto":
        fetched = fetch_mirrorz_sites(timeout)
        if fetched is None:
            origin = "MirrorZ 拉取失败，已回退内置列表"
        else:
            sites = fetched
            origin = "mirrors.cernet.edu.cn 动态列表"

    name_by_url = {url: name for name, url in sites.items()}
    results = check_urls(list(sites.values()), timeout=timeout)
    rows = [
        MirrorResult(name=name_by_url[url], url=url, ok=ok, latency_ms=latency)
        for url, ok, latency in results
        if url in name_by_url
    ]
    return rows, origin


def recommend_mirror(results: list[MirrorResult]) -> str | None:
    """返回排序结果中第一个可达镜像名；全部不可达返回 ``None``。"""
    return next((row.name for row in results if row.ok), None)


def filter_results(results: list[MirrorResult], keyword: str) -> list[MirrorResult]:
    """按镜像名或地址做大小写不敏感过滤；关键词为空时返回原列表副本。"""
    text = keyword.strip().lower()
    if not text:
        return list(results)
    return [row for row in results if text in row.name.lower() or text in row.url.lower()]


def apply_service_mirror(service: str, mirror: str) -> str:
    """把镜像写入对应服务配置，返回被捕获的命令输出。

    Raises
    ------
    ValueError
        服务或镜像名不在候选表中。
    """
    from fcmd.tools.dev._envdev_mirrors import _MIRROR_SERVICE_APPLY

    candidates = service_candidates(service)
    if mirror not in candidates:
        raise ValueError(f"未知 {service} 镜像: {mirror}")
    applier = _MIRROR_SERVICE_APPLY[service]
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        applier(mirror)
    return buffer.getvalue().strip()


def config_location(service: str) -> str:
    """返回服务配置位置：配置文件路径；仅环境变量的服务返回提示文案。"""
    if service not in SERVICE_LABELS:
        raise ValueError(f"未知服务: {service}")
    from fcmd.tools.dev.envdev_core import pip_config_path

    if service == "pip":
        return str(pip_config_path())
    if service == "conda":
        return str(Path.home() / ".condarc")
    if service == "cargo":
        return str(Path.home() / ".cargo" / "config.toml")
    if service == "maven":
        return str(Path.home() / ".m2" / "settings.xml")
    return _ENV_ONLY_LOCATION


# ---------------------------------------------------------------------- #
# Tk 界面
# ---------------------------------------------------------------------- #
class MirrorToolWindow:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 镜像搜索与配置主窗口。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._queue: queue.Queue[tuple[Any, ...]] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._rows: list[MirrorResult] = []
        self._row_names: dict[str, str] = {}
        self._last_service: str = ""

        self.root, self._theme = _guiwindow.create_root(
            title=f"镜像搜索与配置 v{__version__}",
            saved_geometry=self._settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self.close,
        )

        self._mode_var = tk.StringVar(value=self._settings.mode)
        self._service_var = tk.StringVar(value=SERVICE_LABELS[self._settings.service])
        self._timeout_var = tk.StringVar(value=f"{self._settings.timeout:g}")
        self._source_var = tk.StringVar(value=SOURCE_LABELS[self._settings.source])
        self._search_var = tk.StringVar(value=self._settings.search)
        self._status_var = tk.StringVar(value="就绪")
        self._config_var = tk.StringVar(value="")

        self._build_widgets()
        self._sync_mode()
        self._sync_config_hint()
        _guitheme.install_theme_switcher(self.root)

    def _build_widgets(self) -> None:
        ttk = self._ttk
        spacing = self._theme.spacing
        palette = self._theme.palette
        self._build_menu()

        controls = ttk.LabelFrame(self.root, text="测速目标", padding=spacing.sm)
        controls.pack(fill="x", padx=spacing.md, pady=(spacing.md, spacing.xs))

        ttk.Label(controls, text="模式：", anchor="w").grid(row=0, column=0, sticky="w")
        mode_frame = ttk.Frame(controls)
        mode_frame.grid(row=0, column=1, sticky="w", padx=(0, spacing.md))
        self._mode_service_radio = ttk.Radiobutton(
            mode_frame, text="服务镜像", value=MODE_SERVICE, variable=self._mode_var, command=self._on_mode_change
        )
        self._mode_service_radio.pack(side="left")
        self._mode_sites_radio = ttk.Radiobutton(
            mode_frame, text="教育网站点", value=MODE_SITES, variable=self._mode_var, command=self._on_mode_change
        )
        self._mode_sites_radio.pack(side="left", padx=(spacing.sm, 0))

        ttk.Label(controls, text="服务：", anchor="w").grid(row=0, column=2, sticky="w")
        self._service_combo = ttk.Combobox(
            controls,
            textvariable=self._service_var,
            values=list(SERVICE_LABELS.values()),
            state="readonly",
            width=26,
        )
        self._service_combo.grid(row=0, column=3, sticky="ew", padx=(0, spacing.md))
        self._service_combo.bind("<<ComboboxSelected>>", self._on_service_change)

        ttk.Label(controls, text="超时(秒)：", anchor="w").grid(row=0, column=4, sticky="w")
        self._timeout_spin = ttk.Spinbox(
            controls, from_=MIN_TIMEOUT, to=MAX_TIMEOUT, increment=0.5, textvariable=self._timeout_var, width=6
        )
        self._timeout_spin.grid(row=0, column=5, sticky="w")

        ttk.Label(controls, text="来源：", anchor="w").grid(row=1, column=0, sticky="w", pady=(spacing.xs, 0))
        self._source_combo = ttk.Combobox(
            controls,
            textvariable=self._source_var,
            values=list(SOURCE_LABELS.values()),
            state="readonly",
            width=20,
        )
        self._source_combo.grid(row=1, column=1, sticky="w", padx=(0, spacing.md), pady=(spacing.xs, 0))

        ttk.Label(controls, text="搜索：", anchor="w").grid(row=1, column=2, sticky="w", pady=(spacing.xs, 0))
        search_entry = ttk.Entry(controls, textvariable=self._search_var)
        search_entry.grid(row=1, column=3, columnspan=3, sticky="ew", pady=(spacing.xs, 0))
        search_entry.bind("<KeyRelease>", lambda _event: self._render_rows())

        ttk.Label(controls, textvariable=self._config_var, foreground=palette.muted, anchor="w").grid(
            row=2, column=0, columnspan=6, sticky="ew", pady=(spacing.sm, 0)
        )
        controls.columnconfigure(3, weight=1)
        controls.columnconfigure(5, weight=0)

        actions = ttk.Frame(self.root)
        actions.pack(fill="x", padx=spacing.md, pady=(spacing.xs, 0))
        self._probe_button = ttk.Button(
            actions,
            text="开始测速",
            command=self._start_probe,
            style="Accent.TButton",
            padding=(spacing.lg, spacing.xs),
        )
        self._probe_button.pack(side="right")
        self._apply_recommended_button = ttk.Button(actions, text="应用推荐", command=self._apply_recommended)
        self._apply_recommended_button.pack(side="right", padx=(0, spacing.sm))
        self._apply_selected_button = ttk.Button(actions, text="应用选中", command=self._apply_selected)
        self._apply_selected_button.pack(side="right", padx=(0, spacing.sm))
        ttk.Label(actions, textvariable=self._status_var, anchor="w").pack(side="left", fill="x", expand=True)

        result_frame = ttk.LabelFrame(self.root, text="探测结果", padding=spacing.sm)
        result_frame.pack(fill="both", expand=True, padx=spacing.md, pady=(spacing.sm, spacing.xs))
        columns = ("rank", "name", "url", "status", "latency", "throughput")
        self._tree: Any = ttk.Treeview(result_frame, columns=columns, show="headings", height=10)
        headings = {
            "rank": "排名",
            "name": "镜像",
            "url": "地址",
            "status": "状态",
            "latency": "延迟",
            "throughput": "吞吐",
        }
        widths = {"rank": 50, "name": 120, "url": 420, "status": 150, "latency": 90, "throughput": 110}
        for column in columns:
            self._tree.heading(column, text=headings[column])
            self._tree.column(column, width=widths[column], anchor="w", stretch=column == "url")
        self._tree.tag_configure("recommended", foreground=palette.success)
        self._tree.tag_configure("unreachable", foreground=palette.muted)
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scrollbar.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._progress = ttk.Progressbar(self.root, mode="indeterminate")
        self._progress.pack(fill="x", padx=spacing.md, pady=(0, spacing.xs))

        log_frame = ttk.LabelFrame(self.root, text="日志", padding=spacing.sm)
        log_frame.pack(fill="both", expand=True, padx=spacing.md, pady=(spacing.xs, spacing.md))
        self._log_text = _guiwindow.create_log(log_frame, height=8)
        self._append_log("选择服务镜像或教育网站点，点击「开始测速」；测速完成后可应用推荐镜像。")

    def _build_menu(self) -> None:
        _guiwindow.build_menu(self.root, on_close=self.close, on_about=self._show_about)

    # ------------------------------------------------------------------ #
    # 交互
    # ------------------------------------------------------------------ #
    def _show_about(self) -> None:
        """关于对话框。"""
        self._messagebox.showinfo(
            "关于",
            f"镜像搜索与配置 v{__version__}\n\n"
            "按服务实测下载吞吐排序并一键应用镜像\n"
            "支持 pip/conda/rustup/cargo/go/maven/npm\n"
            "另含 MirrorZ 教育网镜像站点探测",
        )

    def _selected_service(self) -> str:
        """把服务下拉框的显示名映射回服务 key；无匹配时返回空串。"""
        label = self._service_var.get()
        return next((name for name, text in SERVICE_LABELS.items() if text == label), "")

    def _selected_source(self) -> str:
        """把来源下拉框的显示名映射回 ``auto`` / ``builtin``。"""
        label = self._source_var.get()
        return next((name for name, text in SOURCE_LABELS.items() if text == label), "auto")

    def _on_mode_change(self) -> None:
        self._last_service = ""
        self._sync_mode()
        self._sync_config_hint()

    def _on_service_change(self, _event: Any = None) -> None:
        self._last_service = ""
        self._sync_config_hint()

    def _sync_mode(self) -> None:
        """按当前模式切换服务/来源控件与结果表列。"""
        service_mode = self._mode_var.get() == MODE_SERVICE
        self._service_combo.configure(state="readonly" if service_mode else "disabled")
        self._source_combo.configure(state="disabled" if service_mode else "readonly")
        self._apply_selected_button.configure(state="normal" if service_mode else "disabled")
        self._apply_recommended_button.configure(state="normal" if service_mode else "disabled")
        columns = (
            ("rank", "name", "url", "status", "latency", "throughput")
            if service_mode
            else ("rank", "name", "url", "status", "latency")
        )
        self._tree.configure(displaycolumns=columns)
        self._tree.heading("name", text="镜像" if service_mode else "站点")

    def _sync_config_hint(self) -> None:
        """更新「配置位置」提示：站点模式只读，服务模式显示落盘位置。"""
        if self._mode_var.get() != MODE_SERVICE:
            self._config_var.set("配置位置：教育网站点探测为只读，不修改任何配置")
            return
        service = self._selected_service()
        if not service:
            self._config_var.set("")
            return
        self._config_var.set(f"配置位置：{config_location(service)}")

    def _append_log(self, message: str) -> None:
        """向只读日志区追加带时间戳的一行并滚动到底部。"""
        _guiwindow.append_log(self._log_text, message, timestamp=True)

    def _clear_log(self) -> None:
        """清空日志区。"""
        _guiwindow.clear_log(self._log_text)

    def _set_busy(self, busy: bool) -> None:
        """探测 / 应用期间禁用操作与目标控件，避免重复提交或中途切换目标。"""
        state = "disabled" if busy else "normal"
        for button in (self._probe_button, self._apply_selected_button, self._apply_recommended_button):
            button.configure(state=state)
        switchable = (
            self._mode_service_radio,
            self._mode_sites_radio,
            self._service_combo,
            self._timeout_spin,
            self._source_combo,
        )
        for widget in switchable:
            widget.configure(state=state)
        if not busy:
            self._sync_mode()

    # ------------------------------------------------------------------ #
    # 探测（后台线程 + 队列回传，避免阻塞 Tk 主循环）
    # ------------------------------------------------------------------ #
    def _start_probe(self) -> None:
        """校验参数并启动后台测速线程。"""
        if self._worker is not None and self._worker.is_alive():
            return
        try:
            timeout = normalize_timeout(self._timeout_var.get())
        except ValueError as exc:
            self._append_log(f"错误：{exc}")
            return

        mode = self._mode_var.get()
        service = self._selected_service()
        if mode == MODE_SERVICE and not service:
            self._append_log("错误：请选择服务")
            return
        source = self._selected_source()

        self._clear_log()
        self._rows = []
        self._render_rows()
        self._set_busy(True)
        self._progress.start(12)
        if mode == MODE_SERVICE:
            self._status_var.set(f"正在测速 {SERVICE_LABELS[service]}…")
            self._append_log(f"测速服务：{SERVICE_LABELS[service]}；超时 {timeout:g} 秒")
            self._append_log(f"配置位置：{config_location(service)}")
        else:
            self._status_var.set("正在探测教育网镜像站点…")
            self._append_log(f"探测教育网镜像站点；超时 {timeout:g} 秒；来源 {self._source_var.get()}")

        self._worker = _guiwindow.start_worker(
            self.root, self._run_probe, (mode, service, timeout, source), self._poll_queue
        )

    def _run_probe(self, mode: str, service: str, timeout: float, source: str) -> None:
        """后台线程：执行探测并把结果写入队列。"""
        try:
            if mode == MODE_SERVICE:
                rows = probe_service(service, timeout)
                self._queue.put(("service", service, rows))
            else:
                rows, origin = probe_sites(timeout, source)
                self._queue.put(("sites", origin, rows))
        except Exception as exc:  # 后台线程需统一回传给界面
            self._queue.put(("error", str(exc)))

    def _render_rows(self) -> None:
        """按搜索词过滤并重绘结果表，标记推荐与不可达行。"""
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._row_names = {}
        rows = filter_results(self._rows, self._search_var.get())
        recommended = recommend_mirror(self._rows)
        for index, row in enumerate(rows, start=1):
            iid = self._tree.insert(
                "",
                "end",
                values=(
                    index,
                    row.name,
                    row.url,
                    row.status_text,
                    row.latency_text,
                    row.throughput_text,
                ),
            )
            self._row_names[iid] = row.name
            if not row.ok:
                self._tree.item(iid, tags=("unreachable",))
            elif row.name == recommended:
                self._tree.item(iid, tags=("recommended",))

    def _poll_queue(self) -> None:
        """在主线程消费后台事件，更新结果表、日志与状态。"""
        while True:
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            kind = event[0]
            if kind == "service":
                _, service, rows = event
                self._last_service = service
                self._finish_busy(rows, f"{SERVICE_LABELS[service]} 共 {len(rows)} 个候选镜像")
            elif kind == "sites":
                _, origin, rows = event
                self._last_service = ""
                self._finish_busy(rows, f"教育网站点（{origin}）共 {len(rows)} 个")
            elif kind == "applied":
                _, service, mirror, text = event
                self._progress.stop()
                self._set_busy(False)
                self._worker = None
                for line in text.splitlines():
                    if line.strip():
                        self._append_log(line.strip())
                self._append_log(f"已应用 {SERVICE_LABELS[service]} 镜像: {mirror}")
                self._status_var.set(f"已应用 {mirror}")
                return
            elif kind == "error":
                _, message = event
                self._finish_error(message)
                return

        if self._worker is not None:
            self.root.after(_guiwindow.WORKER_INTERVAL_MS, self._poll_queue)

    def _finish_busy(self, rows: list[MirrorResult], summary: str) -> None:
        """探测完成：停进度、恢复按钮、渲染结果并输出推荐。"""
        self._rows = rows
        self._progress.stop()
        self._set_busy(False)
        self._worker = None
        self._render_rows()
        reachable = sum(1 for row in rows if row.ok)
        self._append_log(f"{summary}，可达 {reachable} 个")
        best = recommend_mirror(rows)
        if best is None:
            self._append_log("全部不可达，请检查网络或更换来源后重试")
            self._status_var.set("全部不可达")
            return
        self._append_log(f"推荐镜像: {best}（按实测吞吐与延迟排序）")
        self._status_var.set(f"完成：推荐 {best}")

    def _finish_error(self, message: str) -> None:
        """探测 / 应用失败：停进度、恢复按钮并提示。"""
        self._progress.stop()
        self._set_busy(False)
        self._worker = None
        self._append_log(f"错误：{message}")
        self._status_var.set("操作失败")
        self._messagebox.showerror("操作失败", message)

    # ------------------------------------------------------------------ #
    # 应用配置
    # ------------------------------------------------------------------ #
    def _apply_selected(self) -> None:
        """把结果表中选中的镜像写入当前服务配置。"""
        if not self._check_applicable():
            return
        selection = self._tree.selection()
        mirror = self._row_names.get(selection[0]) if selection else None
        if mirror is None:
            self._append_log("请先在结果表中选择一个镜像")
            return
        self._start_apply(self._last_service, mirror)

    def _apply_recommended(self) -> None:
        """把本次测速推荐的镜像写入当前服务配置。"""
        if not self._check_applicable():
            return
        best = recommend_mirror(self._rows)
        if best is None:
            self._append_log("没有可达镜像可应用，请先测速或更换来源")
            return
        self._start_apply(self._last_service, best)

    def _check_applicable(self) -> bool:
        """检查当前状态是否允许应用配置（服务模式 + 已有测速结果）。"""
        if self._mode_var.get() != MODE_SERVICE:
            self._append_log("教育网站点探测为只读，不支持应用配置")
            return False
        if not self._last_service:
            self._append_log("请先完成一次服务镜像测速")
            return False
        return True

    def _start_apply(self, service: str, mirror: str) -> None:
        """启动后台应用线程（写入环境变量 / 配置文件可能较慢）。"""
        if self._worker is not None and self._worker.is_alive():
            return
        self._set_busy(True)
        self._progress.start(12)
        self._status_var.set(f"正在应用 {mirror}…")
        self._append_log(f"开始应用 {SERVICE_LABELS[service]} 镜像: {mirror}")
        self._worker = _guiwindow.start_worker(self.root, self._run_apply, (service, mirror), self._poll_queue)

    def _run_apply(self, service: str, mirror: str) -> None:
        """后台线程：写入服务配置并把输出写入队列。"""
        try:
            text = apply_service_mirror(service, mirror)
        except Exception as exc:  # 后台线程需统一回传给界面
            self._queue.put(("error", str(exc)))
            return
        self._queue.put(("applied", service, mirror, text))

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def close(self) -> None:
        """保存设置并退出窗口（后台线程为 daemon，随进程结束）。"""
        try:
            timeout = normalize_timeout(self._timeout_var.get())
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        save_settings(
            MirrorToolSettings(
                mode=self._mode_var.get(),
                service=self._selected_service() or "pip",
                timeout=timeout,
                source=self._selected_source(),
                search=self._search_var.get(),
                geometry=self.root.winfo_geometry(),
            ),
            self._settings_path,
        )
        self.root.destroy()


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(lambda: MirrorToolWindow(settings_path), "镜像搜索与配置")


@fcmd.tool("mirrortoolw", help="打开镜像搜索与配置窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开镜像搜索与配置窗口。"""
    run_gui()


@fcmd.main("mirrortoolw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
