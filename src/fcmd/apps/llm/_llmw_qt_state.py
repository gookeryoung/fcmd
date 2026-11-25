"""llmw（Qt）的共享状态：模型扫描、硬件快照、服务与跨页信号。

Qt 窗口把「跨页共享的数据」放在这里，页面只订阅信号、不互相引用：

- :attr:`LlmState.modelsChanged`：后台扫描完成，模型清单 / 精度 / 显存可用量更新；
- :attr:`LlmState.hardwareChanged`：硬件检测完成（含失败原因）；
- :attr:`LlmState.servicesChanged`：运行中的服务列表变化（启动 / 停止后刷新）；
- :attr:`LlmState.selectionChanged`：当前选中模型变化，运行页据此回填服务名；
- :attr:`LlmState.statusText` / :attr:`logLine`：状态栏与服务日志窗口；
- :attr:`LlmState.navigateRequested`：跨页跳转（「去设置」这类入口）；
- :attr:`LlmState.sampleCapacityChanged`：采样得到的显存余量变化
  （模型列表的「需释放显存」标注随之刷新）；实时性能由概览页自己采样渲染。
- :attr:`LlmState.recommendationApplied`：概览页把推荐配置送入运行页。

后台扫描走 :func:`fcmd.gui.workers.submit`，结果通过信号回到 GUI 线程，
因此扫描期间窗口仍然可操作；同一时刻只跑一份扫描，重复请求直接忽略。
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

from fcmd.apps.llm import llmbase, llmengine, llmenv, llmmodel, llmserve
from fcmd.apps.llm._llmgguf import GgufPrecision
from fcmd.apps.llm._llmw_hardware import (
    HardwareSnapshot,
    detect_hardware,
    snapshot_from_dict,
    snapshot_to_dict,
)
from fcmd.apps.llm._llmw_ui import (
    _detect_precisions,
    _scan_dir,
    _ScanOutcome,
    default_port_busy,
    entries_with_running_models,
)
from fcmd.apps.llm._llmw_winsettings import (
    LlmSettings,
    load_settings,
    save_settings,
)
from fcmd.apps.llm.llmbase import (
    DEFAULT_PORT,
    ModelEntry,
    ServeError,
    ServeState,
)
from fcmd.gui.logdialog import LogChannelMixin
from fcmd.gui.workers import submit

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "SERVICE_WATCH_MS",
    "LlmState",
    "gpu_usage",
    "scan_models",
    "service_snapshot",
]

SERVICE_WATCH_MS = 3000
"""服务看护间隔（毫秒）：外部启停 / 进程意外退出后各页最迟这么久刷新一次。"""


def gpu_usage(runner: llmbase.Runner = llmbase.run_command) -> tuple[int, int] | None:
    """查询 GPU 已用 / 总显存（MiB）；``nvidia-smi`` 不可用时返回 ``None``。"""
    result = runner(["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"])
    if result.returncode != 0 or not result.stdout.strip():
        return None
    try:
        used, total = (int(float(item)) for item in result.stdout.strip().splitlines()[0].split(","))
    except ValueError:
        return None
    return used, total


def scan_models(port: int | None = None) -> _ScanOutcome:
    """扫描全部模型目录并收集引擎 / GPU / 默认端口状态（阻塞，供线程池调用）。"""
    roots = llmenv.models_dirs()
    # 界面要的是「现在」的目录内容：先清掉进程内扫描缓存再走一遍。
    llmmodel.clear_scan_cache()
    entries: list[ModelEntry] = []
    issues: list[str] = []
    seen: set[str] = set()
    for root in roots:
        found, issue = _scan_dir(root)
        if issue:
            issues.append(issue)
        for entry in found:
            key = llmenv.path_key(entry.path)
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    issue = "；".join(issues) if issues else None
    try:
        states = llmenv.list_states()
    except (OSError, ServeError):
        states = []
    with contextlib.suppress(OSError):
        entries = entries_with_running_models(entries, states)
    if not entries and issue is None:
        issue = "模型目录中未找到 GGUF 文件"
    gpu = llmengine.detect_gpu()
    gpu_mib = gpu.memory_mib if gpu is not None else None
    usage = gpu_usage()
    free_mib = max(usage[1] - usage[0], 0) if usage is not None else None
    try:
        engine_missing = not llmengine.list_engines()
    except OSError:
        engine_missing = True
    return _ScanOutcome(
        entries=entries,
        gpu_mib=gpu_mib,
        free_mib=free_mib,
        precisions=_detect_precisions(entries),
        issue=issue,
        engine_missing=engine_missing,
        port_busy=default_port_busy(states, port),
        models_root=roots[0] if roots else llmenv.models_dir(),
    )


def service_snapshot() -> tuple[list[ServeState], list[ServeState]]:
    """返回（全部已登记服务, 进程仍存活的服务）。"""
    try:
        states = llmenv.list_states()
    except ServeError:
        return [], []
    return states, [state for state in states if llmserve.process_alive(state.pid, state.create_time)]


class LlmState(LogChannelMixin, QObject):  # pragma: no cover - 依赖 Qt 事件循环
    """跨页共享状态：模型 / 硬件 / 服务 / 选中项，以及状态栏与日志通道。"""

    modelsChanged = Signal(object)
    hardwareChanged = Signal(object)
    hardwareFailed = Signal(str)
    servicesChanged = Signal(list)
    selectionChanged = Signal(object, bool)
    logLine = Signal(str)
    statusText = Signal(str)
    pageStatus = Signal(str, str)
    sampleCapacityChanged = Signal()
    recommendationApplied = Signal(object)
    busyChanged = Signal(bool)
    navigateRequested = Signal(str)
    serveModelRequested = Signal(object)
    paintModelRequested = Signal(object)
    loadingTextChanged = Signal(str)
    modelsUpdatedAtChanged = Signal(str)

    def __init__(self, settings_path: Path | None = None) -> None:
        super().__init__()
        self.settings_path = settings_path
        self.settings: LlmSettings = load_settings(settings_path)
        self.models_updated_at: str | None = self.settings.models_updated_at
        self.hardware: HardwareSnapshot | None = (
            snapshot_from_dict(self.settings.hardware) if self.settings.hardware else None
        )
        self.measured: dict[str, dict[str, Any]] = dict(self.settings.measured or {})
        self.models: list[ModelEntry] = []
        self.models_root: Path | None = None
        self.precisions: dict[str, GgufPrecision] = {}
        self.selected_model: Path | None = None
        self.gpu_mib: int | None = None
        self.gpu_free_mib: int | None = None
        self.running_paths: set[str] = set()
        self.states: list[ServeState] = []
        self.alive_states: list[ServeState] = []
        self._services_signature_cached: tuple[Any, ...] | None = None
        self.busy = False
        self.closing = False
        self._init_log_channel()
        self.page_statuses: dict[str, str] = {}
        self._loading_tasks: dict[str, str] = {}
        self.scanning = False
        self.detecting = False

    # ------------------------------------------------------------------ #
    # 通道
    # ------------------------------------------------------------------ #
    def status(self, text: str) -> None:
        """更新状态栏（落在当前页面）。"""
        self.statusText.emit(text)

    def page_status(self, page: str, text: str) -> None:
        """更新指定页面的状态栏文字；页面不在前台时先记下，切回时再显示。"""
        self.page_statuses[page] = text
        self.pageStatus.emit(page, text)

    def navigate(self, key: str) -> None:
        """请求窗口切页。"""
        self.navigateRequested.emit(key)

    def request_serve_model(self, path: Path) -> None:
        """请求窗口切到运行页并启动指定模型（模型页快捷启动用）。"""
        self.serveModelRequested.emit(path)

    def request_paint_model(self, path: Path) -> None:
        """请求窗口切到运行页「画图」并启动 sd.cpp（扩散模型快捷启动用）。"""
        self.paintModelRequested.emit(path)

    def begin_loading(self, key: str, text: str) -> None:
        """登记一项加载任务并显示最近开始的说明；同一说明供状态栏右侧渲染。"""
        self._loading_tasks[key] = text
        self.loadingTextChanged.emit(text)

    def end_loading(self, key: str) -> None:
        """结束一项加载任务；还有其它任务时回退到上一条说明，否则收起。"""
        if key not in self._loading_tasks:
            return
        self._loading_tasks.pop(key, None)
        if self._loading_tasks:
            self.loadingTextChanged.emit(next(reversed(self._loading_tasks.values())))
        else:
            self.loadingTextChanged.emit("")

    def mark_models_updated(self) -> None:
        """记录模型列表最近一次成功更新的时间（ISO），落盘并通知窗口。"""
        from fcmd.apps.llm.llmbase import now_iso

        stamp = now_iso()
        self.models_updated_at = stamp
        self.settings.models_updated_at = stamp
        self.persist()
        self.modelsUpdatedAtChanged.emit(stamp)

    def remember_quant_choice(self, path: Path) -> None:
        """记住用户启动过的量化精度（按仓库保存），供模型页快捷启动默认选中。

        只记录量化模型；写入内存设置，由调用方决定何时 ``persist``。
        """
        from fcmd.apps.llm import _llmconfig, _llmw_models

        repo = ""
        for root in llmenv.models_dirs():
            repo = _llmw_models.repo_for_model_path(path, root)
            if repo:
                break
        label = _llmw_models.quant_precision_label(path)
        if not repo or not label or repo.casefold() == _llmconfig.PRODUCTS_DIRNAME:
            return
        choices = dict(self.settings.quant_choice or {})
        choices[repo.casefold()] = label
        self.settings.quant_choice = choices

    def set_busy(self, busy: bool) -> None:
        """标记「启动 / 停止进行中」，窗口据此禁用动作按钮。"""
        self.busy = busy
        self.busyChanged.emit(busy)

    # ------------------------------------------------------------------ #
    # 服务与选中模型
    # ------------------------------------------------------------------ #
    def refresh_services(self) -> list[ServeState]:
        """读取服务状态文件并刷新运行中服务列表（同步，毫秒级）。"""
        states, alive = service_snapshot()
        self._apply_services(states, alive, force=True)
        return self.alive_states

    def watch_services(self) -> None:
        """周期性核对服务快照：外部启停 / 进程退出后各页及时更新。

        只有快照与上次不同（服务集合、pid、端口 / 模型路径发生变化，或有进程
        悄无声息地退出）才发 :attr:`servicesChanged`：否则每秒采样都会重建
        「运行中的模型」行 / 重填运行页下拉。采样不跟模型推理抢网络，只读几十字节
        的状态文件 + 一次进程存活检查，开销可忽略。
        """
        if self.closing:
            return
        states, alive = service_snapshot()
        self._apply_services(states, alive)

    def _apply_services(
        self,
        states: list[ServeState],
        alive: list[ServeState],
        *,
        force: bool = False,
    ) -> None:
        """写回服务快照，快照变化（或显式刷新）时通知各页。"""
        signature = self._services_signature(states, alive)
        self.states = states
        self.alive_states = alive
        self.running_paths = {llmenv.path_key(Path(state.model)) for state in alive}
        if force or signature != self._services_signature_cached:
            self._services_signature_cached = signature
            self.servicesChanged.emit(self.alive_states)

    @staticmethod
    def _services_signature(states: list[ServeState], alive: list[ServeState]) -> tuple[Any, ...]:
        """服务快照指纹：登记项元数据 + 存活项名称，任一变化都算列表变了。"""
        return (
            tuple((state.name, state.pid, state.create_time, state.host, state.port, state.model) for state in states),
            tuple(state.name for state in alive),
        )

    def select_model(self, path: Path, *, update_name: bool = True) -> None:
        """记录选中的模型并通知各页（运行页可据 ``update_name`` 回填服务名）。"""
        self.selected_model = path
        self.selectionChanged.emit(path, update_name)

    def state_for_model(self, path: Path) -> ServeState | None:
        """返回模型对应的运行中服务（按路径匹配）。"""
        key = llmenv.path_key(path)
        for state in self.alive_states:
            if llmenv.path_key(Path(state.model)) == key:
                return state
        return None

    def is_running(self, path: Path) -> bool:
        """模型是否已被运行中的服务加载。"""
        return llmenv.path_key(path) in self.running_paths

    # ------------------------------------------------------------------ #
    # 后台扫描
    # ------------------------------------------------------------------ #
    def refresh_models(self, port: int | None = None) -> None:
        """后台扫描模型目录；已有扫描在跑时忽略重复请求。"""
        if self.scanning or self.busy:
            return
        self.scanning = True
        probe = port if port is not None else DEFAULT_PORT
        submit(
            scan_models,
            probe,
            on_done=self._apply_scan,
            on_error=self._scan_failed,
            on_finished=self._scan_finished,
        )

    def _apply_scan(self, outcome: _ScanOutcome) -> None:
        self.models = list(outcome.entries)
        self.models_root = outcome.models_root
        self.precisions = dict(outcome.precisions)
        self.gpu_mib = outcome.gpu_mib
        self.gpu_free_mib = outcome.free_mib
        self.modelsChanged.emit(outcome)

    def _scan_failed(self, message: str) -> None:
        self.log(f"扫描模型目录失败：{message}")
        self.status("扫描失败：" + message)

    def _scan_finished(self) -> None:
        self.scanning = False

    def detect_hardware(self, on_done: Callable[[], None] | None = None) -> None:
        """后台检测硬件（CPU / 内存 / GPU / 磁盘），完成后写回设置。"""
        if self.detecting:
            return
        self.detecting = True

        def _work() -> HardwareSnapshot:
            return detect_hardware(models_root=llmenv.models_dir())

        def _done(snapshot: HardwareSnapshot) -> None:
            self.detecting = False
            self.hardware = snapshot
            self.hardwareChanged.emit(snapshot)
            self.persist()
            if on_done is not None:
                on_done()

        def _failed(message: str) -> None:
            self.detecting = False
            self.hardwareFailed.emit(message)

        submit(_work, on_done=_done, on_error=_failed)

    # ------------------------------------------------------------------ #
    # 设置落盘
    # ------------------------------------------------------------------ #
    def persist(self, geometry: str | None = None, *, download: dict[str, Any] | None = None) -> None:
        """把硬件快照、实测记录、对话设置与窗口位置写回配置文件。"""
        self.settings.geometry = geometry if geometry is not None else self.settings.geometry
        if download is not None:
            self.settings.download = download
        self.settings.hardware = snapshot_to_dict(self.hardware) if self.hardware is not None else None
        self.settings.measured = self.measured
        save_settings(self.settings, self.settings_path)

    def record_benchmark(self, model_path: str, result: Any) -> None:
        """记录性能测试结果；后续推荐优先使用实测解码速度。"""
        from fcmd.apps.llm.llmbase import now_iso

        if not model_path or getattr(result, "error", None) is not None or getattr(result, "cancelled", False):
            return
        key = llmenv.path_key(Path(model_path))
        record = dict(self.measured.get(key, {}))
        scenario_key = result.scenario.key
        if scenario_key in ("short", "generate"):
            record["decode_tps"] = float(result.mean_tps)
        if scenario_key == "concurrent4":
            record["aggregate_tps"] = float(result.aggregate_tps)
        if not record:
            return
        record["scenario"] = scenario_key
        record["at"] = now_iso()
        self.measured[key] = record
        self.persist()
