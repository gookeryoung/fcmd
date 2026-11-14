"""locllmw - 本地 LLM 服务管理的 Tk 窗口版。

基于 :mod:`fcmd.apps.locllm.llmserve` 的核心逻辑，用侧边栏分页组织为
概览、模型、运行、对话、代理、画图、视频、音频、监测、设置十页：
后四类多模态页与对话 / 代理页当前为规划中占位，其余页面提供启动参数配置、
模型扫描与显存适配、服务启停、``/metrics`` 监控、系统负载 / GPU 面板、
性能测试与日志。启动与停止均在后台线程执行，界面不阻塞；系统负载在窗口
打开后即持续采样，服务未启动时同样可见。

示例
----
    fcmd locllmw
"""

from __future__ import annotations

import queue
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast, override

import fcmd
from fcmd.apps.locllm import _llmtune, llmbase, llmenv, llmmodel, llmserve
from fcmd.apps.locllm._llmgguf import GgufPrecision, detect_precision
from fcmd.apps.locllm._locllmw_bench import _LocllmWindowBench
from fcmd.apps.locllm._locllmw_chat import _LocllmWindowChat
from fcmd.apps.locllm._locllmw_hardware import HardwareSnapshot, snapshot_from_dict
from fcmd.apps.locllm._locllmw_monitor import (  # noqa: F401  门面 re-export：保留旧访问路径
    RATE_WINDOW_SECONDS,
    GpuSample,
    LoadSample,
    RateWindow,
    _busy_advice,
    _fit_advice,
    _fit_state,
    _format_duration,
    _format_gpu,
    _format_load,
    _LocllmWindowMonitor,
    _parse_gpu_rows,
    _parse_number,
    _probe_web_ui,
    _sample_gpu,
    _sample_load,
)
from fcmd.apps.locllm._locllmw_overview import _LocllmWindowOverview
from fcmd.apps.locllm._locllmw_ui import (
    _HINT_PLACEHOLDER,
    _MODEL_PLACEHOLDER,
    _QUANT_SUFFIX_RE,
    _LocllmWindowUI,
)
from fcmd.apps.locllm.llmbase import (
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    KV_SCALE,
    PROFILES,
    ModelEntry,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    format_command,
    http_base_url,
    human_size,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "LocllmSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
]

_DESCRIPTION = (
    "本地 LLM 服务窗口版（Tk，侧边栏分页：概览 / 模型 / 运行 / 对话 / 代理 / 画图 / 视频 / 音频 / 监测 / 设置）："
    "列出已配置的可选模型并内置能力标签（代码/推理/对话/视觉等）"
    "与最佳实践提示，按整卡与当前可用显存分别标注“硬件不足”（标红加删除线）与“需释放显存”（标橙）并给出建议，"
    "配置档位/端口/上下文等参数，预览启动计划，后台启动与停止 llama.cpp 服务，"
    "查看状态与日志，并在服务运行中实时采样 /metrics 展示平滑后的 token/s 与请求队列状态，"
    "同时展示 CPU / 内存 / 交换 / 服务进程等系统负载以及 GPU 利用率、显存、温度、功耗等关键指标"
    "（系统负载在服务未启动时也持续采样展示）；监测页的性能测试分区提供短对话 / 长输出 / 代码补全 / "
    "长上下文 8K / 长上下文 32K / 并发 4 路等典型场景，可调重复次数、超时与输出上限，"
    "后台批量实测延迟与 token 吞吐并按场景汇总聚合吞吐；窗口位置与大小记忆到 ~/.fcmd/ui/locllmw.json"
)

MIN_WIDTH = 820
MIN_HEIGHT = 640
DEFAULT_WIDTH = 900
DEFAULT_HEIGHT = 780


@dataclass
class LocllmSettings:
    """窗口持久化设置：geometry、硬件快照与性能实测记录。"""

    geometry: str | None = None
    hardware: dict[str, Any] | None = None
    measured: dict[str, dict[str, Any]] | None = None


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/locllmw.json``。"""
    return _guiconfig.config_path("locllmw")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> LocllmSettings:
    """读取设置；文件缺失、JSON 损坏或 geometry 非法时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = LocllmSettings()
    if raw is not None:
        geometry = raw.get("geometry")
        if isinstance(geometry, str) and valid_geometry(geometry):
            settings.geometry = geometry
        hardware = raw.get("hardware")
        if isinstance(hardware, dict):
            settings.hardware = hardware
        measured = raw.get("measured")
        if isinstance(measured, dict):
            settings.measured = {str(key): dict(value) for key, value in measured.items() if isinstance(value, dict)}
    return settings


def save_settings(settings: LocllmSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略（不影响窗口关闭）。"""
    _guiconfig.write_settings(
        {
            "geometry": settings.geometry,
            "hardware": settings.hardware,
            "measured": settings.measured,
        },
        path or config_path(),
    )


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到窗口原默认尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


class LocllmWindow(
    _LocllmWindowUI,
    _LocllmWindowOverview,
    _LocllmWindowChat,
    _LocllmWindowMonitor,
    _LocllmWindowBench,
):  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """Tk 服务窗口：侧边栏分页 + 模型列表 + 计划预览 + 后台启停 + 监控与性能测试。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, font, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._font = font
        self._messagebox = messagebox
        self._ttk = ttk
        self._settings_path = settings_path
        self._settings = load_settings(settings_path)
        self._hardware: HardwareSnapshot | None = (
            snapshot_from_dict(self._settings.hardware) if self._settings.hardware else None
        )
        self._measured: dict[str, dict[str, Any]] = dict(self._settings.measured or {})
        self._hardware_thread: threading.Thread | None = None
        self._hardware_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._hardware_detecting = False
        self.root, self._theme = _guiwindow.create_root(
            title="本地 LLM 服务",
            saved_geometry=self._settings.geometry,
            default_geometry=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._close,
        )

        self._models: list[ModelEntry] = []
        self._precisions: dict[str, GgufPrecision] = {}
        self._selected_model: Path | None = None
        self._syncing_selection = False
        self._gpu_mib: int | None = None
        self._gpu_free_mib: int | None = None
        self._running_paths: set[str] = set()
        self._busy = False
        self._closing = False

        self._scan_thread: threading.Thread | None = None
        self._scan_results: queue.Queue[
            tuple[list[ModelEntry], dict[str, str], int | None, int | None, dict[str, GgufPrecision]]
        ] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] | None = None

        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop: threading.Event | None = None
        self._monitor_state: ServeState | None = None
        self._monitor_results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._monitor_seq = 0

        self._bench_thread: threading.Thread | None = None
        self._bench_stop: threading.Event | None = None
        self._bench_results: queue.Queue[tuple[str, Any]] | None = None
        self._bench_seq = 0
        self._bench_running = False
        self._bench_completed = 0
        self._bench_total = 0
        self._bench_model_path = ""
        self._bench_repeat_var = tk.StringVar(value="1")
        self._bench_timeout_var = tk.StringVar(value="180")
        self._bench_tokens_var = tk.StringVar(value="")
        self._bench_status_var = tk.StringVar(value="选择场景后点击“开始测试”")

        self._name_var = tk.StringVar(value=self._default_service_name())
        self._profile_var = tk.StringVar(value=DEFAULT_PROFILE)
        self._host_var = tk.StringVar(value=DEFAULT_HOST)
        self._port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self._ctx_var = tk.StringVar(value="")
        self._kv_var = tk.StringVar(value="")
        self._ubatch_var = tk.StringVar(value="")
        self._parallel_var = tk.StringVar(value="")
        self._threads_http_var = tk.StringVar(value="")
        self._alias_var = tk.StringVar(value="")
        self._api_key_var = tk.StringVar(value=DEFAULT_API_KEY)
        self._threads_var = tk.StringVar(value=str(DEFAULT_THREADS))
        self._tune_var = tk.StringVar(value=_llmtune.TUNE_LABELS["auto"])
        self._tune_kv_auto = False
        self._tune_ubatch_auto = False
        self._tune_parallel_auto = False
        self._tune_threads_http_auto = False
        self._syncing_tune = False
        self._kv_var.trace_add("write", self._on_kv_field_edited)
        self._ubatch_var.trace_add("write", self._on_ubatch_field_edited)
        self._parallel_var.trace_add("write", self._on_parallel_field_edited)
        self._threads_http_var.trace_add("write", self._on_threads_http_field_edited)
        self._wait_var = tk.StringVar(value="180")
        self._model_var = tk.StringVar(value=_MODEL_PLACEHOLDER)
        self._status_var = tk.StringVar(value="状态：未查询")
        self._info_vars: dict[str, tk.StringVar] = {}
        self._metric_vars: dict[str, tk.StringVar] = {
            "gen_rate": tk.StringVar(value="0.0 tok/s"),
            "prompt_rate": tk.StringVar(value="0.0 tok/s"),
            "processing": tk.StringVar(value="0"),
            "deferred": tk.StringVar(value="0"),
            "kv": tk.StringVar(value="0.0 %"),
            "predicted": tk.StringVar(value="0"),
            "prompt": tk.StringVar(value="0"),
            "monitor": tk.StringVar(value="未监控"),
        }
        self._load_vars: dict[str, tk.StringVar] = {
            "cpu": tk.StringVar(value="-"),
            "cpu_freq": tk.StringVar(value="-"),
            "memory": tk.StringVar(value="-"),
            "swap": tk.StringVar(value="-"),
            "proc_cpu": tk.StringVar(value="-"),
            "proc_mem": tk.StringVar(value="-"),
            "proc_threads": tk.StringVar(value="-"),
            "uptime": tk.StringVar(value="-"),
        }
        self._gpu_vars: dict[str, tk.StringVar] = {
            "gpu_name": tk.StringVar(value="检测中…"),
            "gpu_util": tk.StringVar(value="-"),
            "gpu_vram": tk.StringVar(value="-"),
            "gpu_proc_vram": tk.StringVar(value="-"),
            "gpu_temp": tk.StringVar(value="-"),
            "gpu_power": tk.StringVar(value="-"),
            "gpu_fan": tk.StringVar(value="-"),
            "gpu_clock": tk.StringVar(value="-"),
        }

        self._build_widgets()
        _guitheme.install_theme_switcher(self.root)
        self._init_overview()
        self._refresh_loaded()
        self._refresh_models()
        self._start_system_monitor()

    # ------------------------------------------------------------------ #
    # 模型选择
    # ------------------------------------------------------------------ #
    @override
    def _on_model_selected(self, _event: object | None = None) -> None:
        if self._syncing_selection:
            return
        selection = self._tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._models):
            self._select_model(self._models[index].path)

    @override
    def _on_profile_changed(self, _event: object | None = None) -> None:
        """切换档位后按新的上下文 / KV 重算模型列表的硬件适配标注。"""
        self._render_models()
        if self._selected_model is not None:
            self._show_model_hint(self._selected_model)

    @override
    def _select_file(self) -> None:
        path = self._filedialog.askopenfilename(
            title="选择 GGUF 模型",
            filetypes=[("GGUF 文件", "*.gguf"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._tree.selection_remove(*self._tree.selection())
        self._select_model(Path(path).resolve())

    @override
    def _select_model(self, path: Path, *, update_name: bool = True) -> None:
        self._selected_model = path
        self._model_var.set(str(path))
        if update_name:
            self._name_var.set(self._service_name_from_model(path))
        self._show_model_hint(path)
        self._refresh_hardware_view()
        self._log(f"已选择模型: {path}")

    @classmethod
    def _service_name_from_model(cls, path: Path) -> str:
        """按模型文件名生成服务名：去掉量化后缀，把非法字符转为 ``-``。"""
        stem = _QUANT_SUFFIX_RE.sub("", path.stem)
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
        return cleaned or DEFAULT_NAME

    @staticmethod
    @override
    def _load_state_safe(name: str) -> ServeState | None:
        """按服务名读取状态；名称非法或状态缺失时返回 ``None``。"""
        if not name:
            return None
        try:
            return llmenv.load_state(name, missing_ok=True)
        except ServeError:
            return None

    @override
    def _on_service_selected(self, _event: object | None = None) -> None:
        """选中服务名后同步定位其模型，便于停止已加载的模型。"""
        name = self._name_var.get().strip()
        state = self._load_state_safe(name)
        if state is None:
            return
        path = Path(state.model)
        self._select_model(path, update_name=False)
        self._sync_tree_selection(path)

    @override
    def _sync_tree_selection(self, path: Path) -> None:
        """在模型树中选中指定路径的条目（不触发服务名联动）。"""
        key = llmenv.path_key(path)
        self._syncing_selection = True
        try:
            for index, entry in enumerate(self._models):
                if llmenv.path_key(entry.path) == key:
                    self._tree.selection_set(str(index))
                    self._tree.see(str(index))
                    return
        finally:
            self._syncing_selection = False

    def _precision_for(self, path: Path) -> GgufPrecision:
        """取模型精度识别结果（优先用后台扫描缓存，未命中时现场读取头部）。"""
        key = llmenv.path_key(path)
        precision = self._precisions.get(key)
        if precision is None:
            precision = detect_precision(path)
            self._precisions[key] = precision
        return precision

    def _show_model_hint(self, path: Path) -> None:
        """按文件名内置标签展示推荐档位、最佳实践、硬件适配与客户端接入方式。"""
        tags = llmmodel.detect_model_tags(path.name)
        label = " · ".join(tags) or "未识别"
        advice = llmmodel.model_advice(tags)
        try:
            port = self._int_field(self._port_var, "端口")
        except ServeError:
            port = DEFAULT_PORT
        base = http_base_url(self._host_var.get().strip() or DEFAULT_HOST, port)
        alias = self._alias_var.get().strip() or path.stem
        precision = self._precision_for(path)
        hint = f"标签: {label} | {advice}\n精度: {precision.detail}\n接入: {base}/v1，model={alias}"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        tuning = self._tuning_for(size)
        hint += f"\n调优: {tuning.label} · {tuning.detail}\n{self._effective_tuning_text(tuning)}"
        note = self._hardware_note(path)
        if note:
            hint += f"\n{note}"
            self._log(f"  {note}")
        self._hint_var.set(hint)
        self._log(f"  标签: {label}")
        self._log(f"  精度: {precision.detail}")
        self._log(f"  建议: {advice}")
        self._log(f"  调优: {tuning.label} · {tuning.detail}")

    @override
    def _profile_context(self) -> tuple[str, int, str]:
        """当前表单下的档位名、上下文与 KV 类型（输入无效时回退到档位默认值）。"""
        name = self._profile_var.get().strip() or DEFAULT_PROFILE
        profile = PROFILES.get(name, PROFILES[DEFAULT_PROFILE])
        try:
            context = self._optional_int_field(self._ctx_var, "上下文") or profile.context
        except ServeError:
            context = profile.context
        kv_type = self._kv_var.get().strip() or profile.kv_type
        return name, context, kv_type if kv_type in KV_SCALE else profile.kv_type

    @override
    def _fits(self, size: int) -> tuple[llmmodel.VramFit | None, llmmodel.VramFit | None]:
        """返回（整卡能力, 当前可用）两套显存估算；未检测到 GPU 时均为 ``None``。"""
        if self._gpu_mib is None:
            return None, None
        _, context, kv_type = self._profile_context()
        tuning = self._tuning_for(size)
        kv_type = tuning.kv_type or kv_type
        total = llmmodel.fit_vram(size, self._gpu_mib * 1024 * 1024, context, kv_type)
        free = None
        if self._gpu_free_mib is not None:
            free = llmmodel.fit_vram(size, self._gpu_free_mib * 1024 * 1024, context, kv_type)
        return total, free

    def _fits_for(self, path: Path) -> tuple[llmmodel.VramFit | None, llmmodel.VramFit | None]:
        """按文件大小估算整卡 / 当前可用两套适配情况；文件不可读时均为 ``None``。"""
        try:
            size = path.stat().st_size
        except OSError:
            return None, None
        return self._fits(size)

    def _hardware_note(self, path: Path) -> str:
        """生成硬件适配说明，区分“硬件不足”与“需释放显存”两类情况。"""
        if llmenv.path_key(path) in self._running_paths:
            return "硬件: 该模型已在运行中的服务里加载"
        if self._gpu_mib is None:
            return "硬件: 未检测到可用 NVIDIA GPU，只能 CPU 推理（速度很慢，显存适配无法判断）"
        total, free = self._fits_for(path)
        if total is None:
            return ""
        name, context, kv_type = self._profile_context()
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        tuning = self._tuning_for(size)
        kv_type = tuning.kv_type or kv_type
        detail = f"{name} 档（ctx {context} / kv {kv_type} / {tuning.label}）"
        if not total.ok:
            return (
                f"硬件不足（已标红）: {detail}预计需 {human_size(total.needed)}，整卡显存仅 "
                f"{human_size(total.available)}，清理显存也无法运行；建议{_fit_advice(total, context, kv_type)}"
            )
        if free is not None and not free.ok:
            return (
                f"需释放显存（已标橙）: {detail}预计需 {human_size(total.needed)}，整卡 "
                f"{human_size(total.available)} 够用，但当前可用仅 {human_size(free.available)}；"
                f"建议{_busy_advice(free, context, kv_type)}"
            )
        available = free.available if free is not None else total.available
        return f"硬件: {detail}预计占 {human_size(total.needed)} / 可用显存 {human_size(available)}"

    @override
    def _clear_selection(self) -> None:
        self._tree.selection_remove(*self._tree.selection())
        self._selected_model = None
        self._model_var.set(_MODEL_PLACEHOLDER)
        self._hint_var.set(_HINT_PLACEHOLDER)

    # ------------------------------------------------------------------ #
    # 参数解析
    # ------------------------------------------------------------------ #
    @staticmethod
    def _int_field(var: Any, label: str) -> int:
        text = var.get().strip()
        try:
            return int(text)
        except ValueError as exc:
            raise ServeError(f"{label} 必须是整数: {text!r}") from exc

    @staticmethod
    def _optional_int_field(var: Any, label: str) -> int | None:
        text = var.get().strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise ServeError(f"{label} 必须是整数: {text!r}") from exc

    def _model_name(self) -> str:
        name = self._name_var.get().strip()
        if not name:
            raise ServeError("服务名不能为空")
        return llmbase.validate_name(name)

    def _tune_name(self) -> str:
        """当前下拉框对应的调优策略名（未知标签回落 auto）。"""
        return _llmtune.LABEL_TO_TUNE.get(self._tune_var.get().strip(), "auto")

    def _on_kv_field_edited(self, *_args: object) -> None:
        if not self._syncing_tune:
            self._tune_kv_auto = False

    def _on_ubatch_field_edited(self, *_args: object) -> None:
        if not self._syncing_tune:
            self._tune_ubatch_auto = False

    def _on_parallel_field_edited(self, *_args: object) -> None:
        if not self._syncing_tune:
            self._tune_parallel_auto = False

    def _on_threads_http_field_edited(self, *_args: object) -> None:
        if not self._syncing_tune:
            self._tune_threads_http_auto = False

    def _tuning_for(self, size: int) -> llmbase.Tuning:
        """按当前档位 / 上下文 / GPU 解析调优策略。"""
        _, context, _ = self._profile_context()
        return _llmtune.resolve(self._tune_name(), context=context, model_bytes=size, gpu_mib=self._gpu_mib)

    def _apply_tuning_fields(self, tuning: llmbase.Tuning) -> None:
        """把策略值同步到高级参数输入框，保留用户手填的非策略值。"""
        synced = _llmtune.sync_advanced_fields(
            tuning,
            kv_value=self._kv_var.get().strip(),
            ubatch_value=self._ubatch_var.get().strip(),
            parallel_value=self._parallel_var.get().strip(),
            threads_http_value=self._threads_http_var.get().strip(),
            kv_auto=self._tune_kv_auto,
            ubatch_auto=self._tune_ubatch_auto,
            parallel_auto=self._tune_parallel_auto,
            threads_http_auto=self._tune_threads_http_auto,
        )
        self._tune_kv_auto = synced.kv_auto
        self._tune_ubatch_auto = synced.ubatch_auto
        self._tune_parallel_auto = synced.parallel_auto
        self._tune_threads_http_auto = synced.threads_http_auto
        self._syncing_tune = True
        try:
            self._kv_var.set(synced.kv_value)
            self._ubatch_var.set(synced.ubatch_value)
            self._parallel_var.set(synced.parallel_value)
            self._threads_http_var.set(synced.threads_http_value)
        finally:
            self._syncing_tune = False

    def _effective_tuning_text(self, tuning: llmbase.Tuning) -> str:
        """提示区展示的生效高级参数（输入框 > 策略 > 档位）。"""
        _, _, profile_kv = self._profile_context()
        profile = llmbase.PROFILES.get(self._profile_var.get().strip())
        kv_type = self._kv_var.get().strip() or tuning.kv_type or profile_kv
        ubatch = self._ubatch_var.get().strip() or str(tuning.ubatch or (profile.ubatch if profile else ""))
        parallel = self._parallel_var.get().strip() or str(tuning.parallel)
        threads_http = self._threads_http_var.get().strip() or str(tuning.threads_http or "-")
        return (
            f"生效: b{tuning.batch} / ub {ubatch} / np {parallel} / th {threads_http} / "
            f"cr {tuning.cache_reuse} / kv {kv_type} / fa {tuning.flash_attn}"
        )

    @override
    def _on_tune_changed(self, _event: object | None = None) -> None:
        """切换调优策略后同步高级参数，并刷新硬件适配列与说明。"""
        size = 0
        if self._selected_model is not None:
            try:
                size = self._selected_model.stat().st_size
            except OSError:
                size = 0
        tuning = self._tuning_for(size)
        self._apply_tuning_fields(tuning)
        self._update_fit_states()
        if self._selected_model is not None:
            self._show_model_hint(self._selected_model)
            return
        self._hint_var.set(f"调优: {tuning.label} · {tuning.detail}\n{self._effective_tuning_text(tuning)}")

    def _build_plan(self) -> llmbase.LaunchPlan:
        """按表单内容构造启动计划（参数错误时抛 :class:`ServeError`）。"""
        profile = self._profile_var.get().strip()
        return llmserve.make_plan(
            self._model_name(),
            cast(ProfileName, profile),
            model=self._selected_model,
            port=self._int_field(self._port_var, "端口"),
            host=self._host_var.get().strip() or DEFAULT_HOST,
            ctx=self._optional_int_field(self._ctx_var, "上下文"),
            kv_type=self._kv_var.get().strip() or None,
            ubatch=self._optional_int_field(self._ubatch_var, "ubatch"),
            parallel=self._optional_int_field(self._parallel_var, "并发槽"),
            threads_http=self._optional_int_field(self._threads_http_var, "HTTP 线程数"),
            alias=self._alias_var.get().strip() or None,
            api_key=self._api_key_var.get().strip() or DEFAULT_API_KEY,
            threads=self._int_field(self._threads_var, "线程数"),
            tune=self._tune_name(),
            gpu_mib=self._gpu_mib,
        )

    @staticmethod
    @override
    def _gpu_usage() -> tuple[int, int] | None:
        """查询 GPU 已用 / 总显存（MiB）；nvidia-smi 不可用时返回 ``None``。"""
        result = llmbase.run_command(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"]
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        try:
            used, total = (int(float(item)) for item in result.stdout.strip().splitlines()[0].split(","))
        except ValueError:
            return None
        return used, total

    def _memory_guard(self, plan: llmbase.LaunchPlan) -> None:
        """启动前检查显存 / 内存余量，区分硬件上限与显存被占用两类不足。"""
        estimate = llmmodel.plan_vram(plan)
        usage = self._gpu_usage()
        if usage is not None:
            used, total = usage
            self._log(f"显存占用: {used} / {total} MiB")
            if estimate > total * 1024 * 1024:
                raise ServeError(
                    f"整卡显存不足（硬件限制），清理显存也无法运行：预计需要 {human_size(estimate)}，"
                    f"整卡仅 {human_size(total * 1024 * 1024)}。"
                    "请改用更小量化或更小参数量的模型，或减小上下文"
                )
            available = max(total - used, 0) * 1024 * 1024
            if estimate > available:
                raise ServeError(
                    f"当前可用显存不足（整卡 {total} MiB 够用，可能被其他服务占用）："
                    f"预计需要 {human_size(estimate)}，当前可用 {human_size(available)}。"
                    "请先停止其他服务 / 实例，或改用更小档位、减小上下文"
                )
            self._log(f"资源检查通过：预计占用 {human_size(estimate)} / 显存可用 {human_size(available)}")
            return
        import psutil

        available = int(psutil.virtual_memory().available)
        if estimate > available:
            raise ServeError(
                f"可用内存不足：预计需要 {human_size(estimate)}，当前可用 {human_size(available)}。"
                "请改用更小档位、减小上下文，或先停止其他服务"
            )
        self._log(f"资源检查通过：预计占用 {human_size(estimate)} / 可用内存 {human_size(available)}")

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    @override
    def _preview(self) -> None:
        try:
            plan = self._build_plan()
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        estimate = llmmodel.plan_vram(plan)
        self._log("启动计划:")
        self._log(f"  服务名: {plan.name}")
        self._log(f"  引擎: {plan.engine.tag} · {plan.engine.exe}")
        self._log(f"  模型: {plan.model}")
        self._log(
            f"  档位: {plan.profile.name} / {plan.profile.quant} / kv {plan.effective_kv_type} / "
            f"ctx {plan.context} / ub {plan.effective_ubatch}"
        )
        self._log(
            f"  调优: {plan.tuning.label} / b{plan.tuning.batch} / np{plan.effective_parallel} / "
            f"th{plan.effective_threads_http or '-'} / cr{plan.tuning.cache_reuse} / fa {plan.tuning.flash_attn}"
        )
        self._log(f"  说明: {plan.tuning.detail}")
        self._log(f"  监听: {http_base_url(plan.host, plan.port)}")
        tags = llmmodel.detect_model_tags(plan.model.name)
        self._log(f"  标签: {' · '.join(tags) or '未识别'}")
        self._log(f"  精度: {self._precision_for(plan.model).detail}")
        self._log(f"  建议: {llmmodel.model_advice(tags)}")
        self._log(f"  显存估算: {human_size(estimate)}")
        self._log("  命令: " + format_command(plan.argv()))
        total, free = self._fits_for(plan.model)
        if total is not None and not total.ok:
            self._log(
                f"  警告: 估算 {human_size(estimate)} 超过整卡显存 {human_size(total.available)}，无法运行；"
                f"建议{_fit_advice(total, plan.context, plan.effective_kv_type)}"
            )
        elif free is not None and not free.ok:
            self._log(
                f"  警告: 估算 {human_size(estimate)} 超过当前可用显存 {human_size(free.available)}，可能 OOM；"
                f"建议{_busy_advice(free, plan.context, plan.effective_kv_type)}"
            )

    @override
    def _start(self) -> None:
        if self._busy:
            self._log("已有任务正在进行，请等待完成")
            return
        try:
            plan = self._build_plan()
            wait_s = self._int_field(self._wait_var, "等待就绪秒数")
            if wait_s <= 0:
                raise ServeError(f"等待就绪秒数必须为正整数: {wait_s}")
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        try:
            self._memory_guard(plan)
        except ServeError as exc:
            self._log("已取消启动：" + str(exc))
            self._messagebox.showerror("资源不足", str(exc))
            return
        self._stop_monitor("启动中…")
        self._begin_task(self._start_work, (plan, wait_s))

    @override
    def _stop(self) -> None:
        """停止所有已登记的服务，无需先选择服务名。"""
        if self._busy:
            self._log("已有任务正在进行，请等待完成")
            return
        try:
            names = tuple(state.name for state in llmenv.list_states())
        except ServeError as exc:
            self._messagebox.showerror("停止失败", str(exc))
            return
        if not names:
            self._status_var.set("无已登记服务")
            self._log("没有已登记的服务，无需停止")
            self._refresh_service_names()
            self._start_system_monitor()
            return
        self._stop_monitor("停止中…")
        self._begin_task(self._stop_work, (names,))

    def _begin_task(self, target: Any, args: tuple[Any, ...]) -> None:
        """启动后台线程执行目标函数，结果通过队列回传主线程。"""
        self._nav.select("monitor")
        self._messages = queue.Queue()
        self._thread = threading.Thread(target=target, args=args, daemon=True)
        self._set_busy(True)
        self._thread.start()
        self.root.after(100, self._poll_task)

    def _start_work(self, plan: llmbase.LaunchPlan, wait_s: int) -> None:
        assert self._messages is not None
        status = self._messages
        status.put(("log", f"正在启动 {plan.name}，等待 /health（最多 {wait_s}s）…"))
        try:
            state = llmserve.start_server(plan, wait_s=float(wait_s))
        except ServeError as exc:
            status.put(("error", f"启动失败: {exc}"))
        else:
            status.put(("state", state))
        finally:
            status.put(("done", None))

    def _stop_work(self, names: tuple[str, ...]) -> None:
        """后台线程：依次停止所有服务，收集成功结果与失败原因。"""
        assert self._messages is not None
        status = self._messages
        results: list[StopResult] = []
        failures: list[str] = []
        for name in names:
            status.put(("log", f"正在停止服务 {name}…"))
            try:
                results.append(llmserve.stop_server(name))
            except ServeError as exc:
                failures.append(f"{name}: {exc}")
        status.put(("stopped", (results, failures)))
        status.put(("done", None))

    def _poll_task(self) -> None:
        """主线程轮询队列，刷新日志与状态。"""
        assert self._messages is not None
        try:
            while True:
                kind, payload = self._messages.get_nowait()
                if kind == "log":
                    self._log(str(payload))
                elif kind == "error":
                    self._log(str(payload))
                    self._messagebox.showerror("错误", str(payload))
                elif kind == "state":
                    self._show_state(cast(ServeState, payload))
                elif kind == "stopped":
                    results, failures = cast(tuple[list[StopResult], list[str]], payload)
                    self._show_stopped(results, failures)
                elif kind == "done":
                    self._set_busy(False)
                    self._start_system_monitor()
                    # 启动 / 停止都会改变显存占用，任务结束后重新采样，避免沿用旧余量
                    self._refresh_models()
        except queue.Empty:
            if self._thread is not None and self._thread.is_alive():
                self.root.after(100, self._poll_task)

    def _show_state(self, state: ServeState) -> None:
        self._status_var.set(f"{state.name} · 运行中 · {state.base_url} · pid {state.pid}")
        self._refresh_service_names()
        self._log(f"服务已就绪: {state.base_url}（OpenAI: {state.base_url}/v1）")
        self._log(f"  模型: {state.model}")
        self._log(f"  档位: {state.profile} / ctx {state.context}")
        self._log(f"  日志: {state.log}")
        self._start_monitor(state)
        self._refresh_loaded()
        self._messagebox.showinfo("启动成功", f"服务 {state.name} 已就绪\n{state.base_url}")

    def _show_stopped(self, results: list[StopResult], failures: list[str]) -> None:
        """汇总本次“停止所有服务”的结果并刷新界面状态。"""
        self._stop_monitor("已停止")
        self._refresh_loaded()
        self._refresh_service_names()
        stopped = [result.state.name for result in results if result.was_running]
        cleaned = [result.state.name for result in results if not result.was_running]
        for name in stopped:
            self._log(f"已停止服务 {name}")
        if cleaned:
            self._log(f"服务未在运行，已清理残留状态: {', '.join(cleaned)}")
        for failure in failures:
            self._log(f"停止失败: {failure}")
        if failures:
            self._status_var.set(f"停止完成 · {len(stopped)} 个已停止 · {len(failures)} 个失败")
            self._messagebox.showerror("停止失败", "\n".join(failures))
        elif stopped:
            self._status_var.set(f"已停止 {len(stopped)} 个服务")
        else:
            self._status_var.set("无运行中的服务（已清理残留状态）")
        self._start_system_monitor()

    @override
    def _refresh_status(self) -> None:
        self._refresh_loaded()
        self._refresh_service_names()
        try:
            name = self._model_name()
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        state = llmenv.load_state(name, missing_ok=True)
        if state is None:
            self._status_var.set(f"{name} · 未启动")
            self._log(f"服务 {name} 未启动（尚无状态文件，持续显示系统负载）")
            self._stop_monitor("服务未运行")
            self._start_system_monitor()
            return
        alive = llmserve.process_alive(state.pid, state.create_time)
        self._status_var.set(f"{state.name} · {'运行中' if alive else '已退出'} · {state.base_url} · pid {state.pid}")
        self._log(f"服务 {state.name}: {'运行中' if alive else '已退出'}")
        self._log(f"  地址: {state.base_url}")
        self._log(f"  模型: {state.model}")
        self._log(f"  档位: {state.profile} / ctx {state.context}")
        self._log(f"  启动时间: {state.started_at}")
        self._log(f"  日志: {state.log}")
        if alive:
            self._start_monitor(state)
        else:
            self._stop_monitor("服务未运行")
            self._start_system_monitor()

    @override
    def _open_browser(self) -> None:
        """在默认浏览器中打开服务地址（优先用运行中服务的实际地址，否则用表单配置）。"""
        import webbrowser

        try:
            name = self._model_name()
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        state = llmenv.load_state(name, missing_ok=True)
        engine = state.engine if state is not None else "（未登记）"
        if state is not None and llmserve.process_alive(state.pid, state.create_time):
            url = state.base_url
            api_key = state.api_key
        else:
            try:
                url = http_base_url(
                    self._host_var.get().strip() or DEFAULT_HOST,
                    self._int_field(self._port_var, "端口"),
                )
            except ServeError as exc:
                self._messagebox.showerror("参数错误", str(exc))
                return
            api_key = self._api_key_var.get().strip() or DEFAULT_API_KEY

        status = _probe_web_ui(url, api_key)
        if status == 404:
            message = (
                f"当前服务的引擎未内置 Web UI（GET / 返回 404）: {engine}\n"
                "LM Studio 版 llama.cpp 会移除 Web UI；请切换到带 Web UI 的官方构建后重启服务：\n"
                "  fcmd llmengine use <tag>（如 b12212）\n"
                f"也可直接用 OpenAI 兼容客户端访问 {url}/v1"
            )
            self._log(message)
            self._messagebox.showerror("无 Web UI", message)
            return
        if status is None:
            message = f"服务未就绪，无法打开 Web UI: {url}"
            self._log(message)
            self._messagebox.showerror("打开失败", message)
            return
        if status != 200:
            message = f"服务返回 HTTP {status}，Web UI 不可用: {url}"
            self._log(message)
            self._messagebox.showerror("打开失败", message)
            return
        if webbrowser.open(url):
            self._log(f"已在浏览器中打开: {url}")
        else:
            self._log(f"无法打开浏览器，请手动访问: {url}")
            self._messagebox.showerror("打开失败", f"无法打开浏览器，请手动访问: {url}")

    @override
    def _show_logs(self) -> None:
        self._nav.select("monitor")
        try:
            name = self._model_name()
            content = llmserve.read_log(name, 50)
        except ServeError as exc:
            self._messagebox.showerror("日志不可用", str(exc))
            return
        self._log(f"日志尾部（{name}）:")
        self._log(content)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for button in (self._preview_btn, self._start_btn, self._stop_btn, self._status_btn, self._logs_btn):
            button.configure(state=state)
        self._model_btn.configure(state=state)
        self._browse_btn.configure(state=state)
        self._clear_btn.configure(state=state)
        if busy:
            self._progress.start(12)
        else:
            self._progress.stop()

    @override
    def _persist_settings(self) -> None:
        """把 geometry、硬件快照与实测记录写入配置。"""
        save_settings(
            LocllmSettings(
                geometry=self.root.winfo_geometry(),
                hardware=self._hardware_dict(),
                measured=self._measured,
            ),
            self._settings_path,
        )

    def _close(self) -> None:
        running = self._thread is not None and self._thread.is_alive()
        if running and not self._messagebox.askyesno("任务进行中", "启动 / 停止任务仍在执行，确实退出？"):
            return
        self._closing = True
        self._close_chat()
        self._stop_monitor()
        self._close_bench()
        self._persist_settings()
        self.root.destroy()

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    @override
    def _log(self, message: str) -> None:
        _guiwindow.append_log(self._log_text, message)

    @override
    def _clear_log(self) -> None:
        """清空输出日志区域。"""
        _guiwindow.clear_log(self._log_text)


def run_gui() -> None:
    """创建并运行 Tk 主窗口（先声明 DPI 感知，再创建 Tk）。"""
    _guiwindow.run_window(LocllmWindow, "本地 LLM")


@fcmd.tool("locllmw", help="打开本地 LLM 服务窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开本地 LLM 服务窗口。"""
    run_gui()


@fcmd.main("locllmw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
