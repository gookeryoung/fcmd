"""llmservew - 推理服务生命周期管理的 Tk 窗口版。

基于 :mod:`fcmd.tools.llm.llmserve` 的核心逻辑，提供启动参数的图形化配置、
已配置模型列表（后台扫描 ``llmenv`` 登记的模型目录，并按文件名内置能力标签
与最佳实践提示）、启动计划预览、后台启动 / 停止、状态与日志查看、一键在默认浏览器
打开服务地址，以及运行监控（``/metrics`` 性能指标 + 系统负载）。启动与停止均在后台线程执行，界面不阻塞。
系统负载（CPU / 内存 / 交换 / GPU）在窗口打开后即持续采样，服务未启动时同样可见。
模型列表按整卡与当前可用显存双重评估：整卡放不下标红并加删除线为“硬件不足”，仅当前余量不足标橙为“需释放显存”，
运行中的模型标为“运行中”，并在提示区给出针对性建议；显存释放后自动重算。

示例
----
    fcmd llmservew
"""

from __future__ import annotations

import csv
import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import fcmd
from fcmd.console import print_error
from fcmd.tools.llm import llmbase, llmengine, llmenv, llmmodel, llmserve
from fcmd.tools.llm.llmbase import (
    DEFAULT_API_KEY,
    DEFAULT_HOST,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_PROFILE,
    DEFAULT_THREADS,
    KV_SCALE,
    PROFILES,
    EngineInfo,
    ModelEntry,
    ProfileName,
    ServeError,
    ServeState,
    StopResult,
    format_command,
    http_base_url,
    human_size,
)

__all__ = ["main"]

_DESCRIPTION = (
    "推理服务窗口版（Tk）：列出已配置的可选模型并内置能力标签（代码/推理/对话/视觉等）"
    "与最佳实践提示，按整卡与当前可用显存分别标注“硬件不足”（标红加删除线）与“需释放显存”（标橙）并给出建议，"
    "配置档位/端口/上下文等参数，预览启动计划，后台启动与停止 llama.cpp 服务，"
    "查看状态与日志，并在服务运行中实时采样 /metrics 展示平滑后的 token/s 与请求队列状态，"
    "同时展示 CPU / 内存 / 交换 / 服务进程等系统负载以及 GPU 利用率、显存、温度、功耗等关键指标"
    "（系统负载在服务未启动时也持续采样展示）"
)

#: 当前硬件放不下的模型行标记色（整卡显存也不够）
_UNFIT_COLOR = "#c62828"
#: 整卡够用但当前可用显存不足的模型行标记色（需释放显存）
_BUSY_COLOR = "#ef6c00"

_FIT_OK = "可用"
_FIT_BUSY = "需释放显存"
_FIT_UNSUPPORTED = "硬件不足"
_FIT_RUNNING = "运行中"

_MODEL_PLACEHOLDER = "未选择（按档位自动在模型目录中查找）"
_HINT_PLACEHOLDER = "选择模型后显示能力标签（代码/推理/对话/视觉/数学/重排/嵌入/通用）、硬件适配与最佳实践建议"
_QUANT_SUFFIX_RE = re.compile(r"[-_.](?:I?Q\d+(?:_[A-Za-z0-9]+)*|BF16|F16|F32)$", re.IGNORECASE)

#: token 速率平滑窗口（秒）：窗口内累计 token 数 ÷ 经过时间，避免逐秒差值剧烈跳变
RATE_WINDOW_SECONDS = 10.0
#: ``nvidia-smi --query-gpu`` 查询字段（顺序与解析逻辑对应）
_GPU_FIELDS = (
    "uuid",
    "index",
    "name",
    "utilization.gpu",
    "memory.used",
    "memory.total",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "fan.speed",
    "clocks.sm",
)


@dataclass(frozen=True, slots=True)
class GpuSample:
    """一次 GPU 采样：利用率 / 显存 / 温度 / 功耗 / 风扇 / 频率（显存 MiB、功耗 W、频率 MHz）。"""

    name: str
    index: str = ""
    utilization: float | None = None
    memory_used: float | None = None
    memory_total: float | None = None
    temperature: float | None = None
    power_draw: float | None = None
    power_limit: float | None = None
    fan: float | None = None
    clock_mhz: float | None = None
    process_memory: float | None = None


@dataclass(frozen=True, slots=True)
class LoadSample:
    """一次系统负载采样（CPU / 内存 / 交换 / 服务进程 / GPU）。"""

    cpu: float | None = None
    cpu_freq: float | None = None
    memory_used: int = 0
    memory_total: int = 0
    memory_percent: float = 0.0
    swap_used: int = 0
    swap_total: int = 0
    process_cpu: float | None = None
    process_rss: int | None = None
    process_threads: int | None = None
    process_uptime: float | None = None
    gpu: GpuSample | None = None


def _parse_number(text: str) -> float | None:
    """解析 nvidia-smi 数值字段；``N/A`` 等非数值返回 ``None``。"""
    raw = text.strip()
    if not raw or raw.upper() in {"N/A", "[N/A]", "[NOT SUPPORTED]"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_gpu_rows(text: str) -> list[tuple[str, GpuSample]]:
    """解析 ``--query-gpu`` 的 CSV 输出为（GPU UUID, 采样）列表。"""
    rows: list[tuple[str, GpuSample]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = [field.strip() for field in next(csv.reader([line]), [])]
        if len(fields) < len(_GPU_FIELDS):
            continue
        rows.append(
            (
                fields[0],
                GpuSample(
                    name=fields[2],
                    index=fields[1],
                    utilization=_parse_number(fields[3]),
                    memory_used=_parse_number(fields[4]),
                    memory_total=_parse_number(fields[5]),
                    temperature=_parse_number(fields[6]),
                    power_draw=_parse_number(fields[7]),
                    power_limit=_parse_number(fields[8]),
                    fan=_parse_number(fields[9]),
                    clock_mhz=_parse_number(fields[10]),
                ),
            )
        )
    return rows


def _gpu_process_memory(pid: int, runner: llmbase.Runner = llmbase.run_command) -> tuple[str | None, float | None]:
    """查询服务进程占用的 GPU UUID 与显存（MiB）；进程未使用 GPU 时返回 ``(None, None)``。"""
    result = runner(["nvidia-smi", "--query-compute-apps=pid,gpu_uuid,used_memory", "--format=csv,noheader,nounits"])
    if result.returncode != 0:
        return None, None
    for line in result.stdout.splitlines():
        fields = [field.strip() for field in next(csv.reader([line]), [])]
        if len(fields) >= 3 and fields[0] == str(pid):
            return fields[1], _parse_number(fields[2])
    return None, None


def _sample_gpu(pid: int, runner: llmbase.Runner = llmbase.run_command) -> GpuSample | None:
    """采样 GPU 关键指标；无 NVIDIA GPU 或 nvidia-smi 不可用时返回 ``None``。"""
    result = runner(["nvidia-smi", f"--query-gpu={','.join(_GPU_FIELDS)}", "--format=csv,noheader,nounits"])
    if result.returncode != 0:
        return None
    rows = _parse_gpu_rows(result.stdout)
    if not rows:
        return None
    uuid, process_memory = _gpu_process_memory(pid, runner)
    chosen = next((sample for row_uuid, sample in rows if row_uuid == uuid), None)
    if chosen is None:
        chosen = max(rows, key=lambda item: item[1].memory_used or 0.0)[1]
    if process_memory is None:
        return chosen
    return replace(chosen, process_memory=process_memory)


def _sample_load(pid: int, process: Any | None) -> LoadSample:
    """采样系统负载：CPU 使用率 / 频率、内存、交换、服务进程与 GPU。"""
    import psutil

    cpu = float(psutil.cpu_percent())
    try:
        freq = psutil.cpu_freq()
    except (NotImplementedError, AttributeError):
        freq = None
    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()
    process_cpu = process_rss = process_threads = process_uptime = None
    if process is not None:
        try:
            process_cpu = float(process.cpu_percent())
            process_rss = int(process.memory_info().rss)
            process_threads = int(process.num_threads())
            process_uptime = max(time.time() - float(process.create_time()), 0.0)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return LoadSample(
        cpu=cpu,
        cpu_freq=float(freq.current) if freq is not None else None,
        memory_used=int(memory.used),
        memory_total=int(memory.total),
        memory_percent=float(memory.percent),
        swap_used=int(swap.used),
        swap_total=int(swap.total),
        process_cpu=process_cpu,
        process_rss=process_rss,
        process_threads=process_threads,
        process_uptime=process_uptime,
        gpu=_sample_gpu(pid),
    )


def _format_duration(seconds: float) -> str:
    """把秒数格式化为 ``1h 02m 03s`` / ``2m 03s`` / ``45s``。"""
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _format_load(sample: LoadSample) -> dict[str, str]:
    """把系统负载采样格式化为面板显示文本。"""
    memory = (
        f"{human_size(sample.memory_used)} / {human_size(sample.memory_total)}（{sample.memory_percent:.1f} %）"
        if sample.memory_total
        else "-"
    )
    swap = (
        f"{human_size(sample.swap_used)} / {human_size(sample.swap_total)}"
        f"（{sample.swap_used / sample.swap_total * 100:.1f} %）"
        if sample.swap_total
        else "-"
    )
    return {
        "cpu": "-" if sample.cpu is None else f"{sample.cpu:.1f} %",
        "cpu_freq": "-" if sample.cpu_freq is None else f"{sample.cpu_freq:.0f} MHz",
        "memory": memory,
        "swap": swap,
        "proc_cpu": "-" if sample.process_cpu is None else f"{sample.process_cpu:.1f} %",
        "proc_mem": "-" if sample.process_rss is None else human_size(sample.process_rss),
        "proc_threads": "-" if sample.process_threads is None else str(sample.process_threads),
        "uptime": "-" if sample.process_uptime is None else _format_duration(sample.process_uptime),
    }


def _format_gpu(gpu: GpuSample | None) -> dict[str, str]:
    """把 GPU 采样格式化为面板显示文本（无 GPU 时给出提示）。"""
    if gpu is None:
        return {
            "gpu_name": "未检测到 NVIDIA GPU（nvidia-smi 不可用）",
            "gpu_util": "-",
            "gpu_vram": "-",
            "gpu_proc_vram": "-",
            "gpu_temp": "-",
            "gpu_power": "-",
            "gpu_fan": "-",
            "gpu_clock": "-",
        }
    title = f"{gpu.name}（GPU {gpu.index}）" if gpu.index else gpu.name
    vram = "-"
    if gpu.memory_used is not None and gpu.memory_total:
        used = human_size(int(gpu.memory_used * 1024 * 1024))
        total = human_size(int(gpu.memory_total * 1024 * 1024))
        vram = f"{used} / {total}（{gpu.memory_used / gpu.memory_total * 100:.1f} %）"
    power = "-"
    if gpu.power_draw is not None:
        power = f"{gpu.power_draw:.0f} W"
        if gpu.power_limit is not None:
            power = f"{gpu.power_draw:.0f} / {gpu.power_limit:.0f} W"
    return {
        "gpu_name": title,
        "gpu_util": "-" if gpu.utilization is None else f"{gpu.utilization:.0f} %",
        "gpu_vram": vram,
        "gpu_proc_vram": "-" if gpu.process_memory is None else human_size(int(gpu.process_memory * 1024 * 1024)),
        "gpu_temp": "-" if gpu.temperature is None else f"{gpu.temperature:.0f} °C",
        "gpu_power": power,
        "gpu_fan": "-" if gpu.fan is None else f"{gpu.fan:.0f} %",
        "gpu_clock": "-" if gpu.clock_mhz is None else f"{gpu.clock_mhz:.0f} MHz",
    }


def _probe_web_ui(base_url: str, api_key: str | None = None, *, timeout: float = 3.0) -> int | None:
    """探测服务根路径状态码；连接失败返回 ``None``，HTTP 错误返回其状态码。

    llama.cpp 的 Web UI 资源是 gzip 内嵌的，缺少 ``Accept-Encoding: gzip`` 会返回 415。
    """
    from urllib.error import HTTPError
    from urllib.request import Request, urlopen

    headers = {"Accept-Encoding": "gzip"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with urlopen(Request(base_url + "/", headers=headers), timeout=timeout) as resp:
            return int(getattr(resp, "status", 200))
    except HTTPError as exc:
        return int(exc.code)
    except OSError:
        return None


def _fit_advice(fit: llmmodel.VramFit, context: int, kv_type: str) -> str:
    """按显存缺口给出降配建议（纯文本，供界面与日志复用）。"""
    if fit.max_context >= 4096:
        kv_hint = "" if kv_type == "q8_0" else "，或把 KV 缓存切到 q8_0"
        return f"把上下文从 {context} 降到 {fit.max_context // 1024}K 以内{kv_hint}"
    return "该文件仅权重就超出可用显存：改用更小量化（Q4_K_M/Q3_K_M）或更小参数量的模型；若显存被其他服务占用，先停止"


def _busy_advice(fit: llmmodel.VramFit, context: int, kv_type: str) -> str:
    """整卡够用但当前余量不足时的建议：优先释放显存，其次降配。"""
    if fit.max_context >= 4096:
        kv_hint = "" if kv_type == "q8_0" else "，或把 KV 缓存切到 q8_0"
        return f"先停止占用显存的其他服务 / 实例；若仍需并行，把上下文从 {context} 降到 {fit.max_context // 1024}K 以内{kv_hint}"
    return "先停止占用显存的其他服务 / 实例；释放后仍不足时换用更小量化（Q4_K_M/Q3_K_M）的文件"


def _fit_state(total: llmmodel.VramFit | None, free: llmmodel.VramFit | None) -> str:
    """区分两类不可用：整卡放不下（硬件不足）与仅当前余量放不下（需释放显存）。"""
    if total is None:
        return "-"
    if not total.ok:
        return _FIT_UNSUPPORTED
    if free is not None and not free.ok:
        return _FIT_BUSY
    return _FIT_OK


class RateWindow:
    """按时间窗口平滑 token 速率。

    参考 DeepSeek Harness 的口径：用窗口内累计 token 数 ÷ 经过时间计算平均速率，
    而不是显示两次采样间的瞬时差值，避免 1s 抖动导致数字剧烈跳变。
    """

    def __init__(self, window: float = RATE_WINDOW_SECONDS) -> None:
        self._window = window
        self._samples: deque[tuple[float, float, float]] = deque()
        self._gen_rate = 0.0
        self._prompt_rate = 0.0

    def update(self, now: float, predicted: float, prompt: float) -> tuple[float, float]:
        """记录一次采样并返回平滑后的（生成, 提示）token/s。"""
        if self._samples and (predicted < self._samples[-1][1] or prompt < self._samples[-1][2]):
            self._samples.clear()
            self._gen_rate = self._prompt_rate = 0.0
        self._samples.append((now, predicted, prompt))
        cutoff = now - self._window
        while len(self._samples) > 2 and self._samples[0][0] < cutoff:
            self._samples.popleft()
        first_time, first_predicted, first_prompt = self._samples[0]
        span = now - first_time
        if span >= 1.0:
            self._gen_rate = max(predicted - first_predicted, 0.0) / span
            self._prompt_rate = max(prompt - first_prompt, 0.0) / span
        return self._gen_rate, self._prompt_rate


class ServeWindow:
    """Tk 服务窗口：参数表单 + 模型列表 + 计划预览 + 后台启停。"""

    def __init__(self) -> None:
        import tkinter as tk
        from tkinter import filedialog, font, messagebox, ttk

        self._tk = tk
        self._filedialog = filedialog
        self._font = font
        self._messagebox = messagebox
        self._ttk = ttk
        self.root = tk.Tk()
        self.root.title("推理服务管理")
        self.root.geometry("900x780")
        self.root.minsize(820, 640)

        self._models: list[ModelEntry] = []
        self._selected_model: Path | None = None
        self._syncing_selection = False
        self._gpu_mib: int | None = None
        self._gpu_free_mib: int | None = None
        self._running_paths: set[str] = set()
        self._busy = False
        self._closing = False

        self._scan_thread: threading.Thread | None = None
        self._scan_results: queue.Queue[tuple[list[ModelEntry], dict[str, str], int | None, int | None]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._messages: queue.Queue[tuple[str, Any]] | None = None

        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop: threading.Event | None = None
        self._monitor_state: ServeState | None = None
        self._monitor_results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._monitor_seq = 0

        self._name_var = tk.StringVar(value=self._default_service_name())
        self._profile_var = tk.StringVar(value=DEFAULT_PROFILE)
        self._host_var = tk.StringVar(value=DEFAULT_HOST)
        self._port_var = tk.StringVar(value=str(DEFAULT_PORT))
        self._ctx_var = tk.StringVar(value="")
        self._kv_var = tk.StringVar(value="")
        self._ubatch_var = tk.StringVar(value="")
        self._alias_var = tk.StringVar(value="")
        self._api_key_var = tk.StringVar(value=DEFAULT_API_KEY)
        self._threads_var = tk.StringVar(value=str(DEFAULT_THREADS))
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
        self._refresh_loaded()
        self._refresh_models()
        self._start_system_monitor()

    # ------------------------------------------------------------------ #
    # 界面
    # ------------------------------------------------------------------ #
    def _build_widgets(self) -> None:
        ttk = self._ttk
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, textvariable=self._status_var).pack(side="bottom", fill="x", pady=(4, 0))
        self._build_actions(outer)
        self._notebook = ttk.Notebook(outer)
        self._notebook.pack(fill="both", expand=True)
        start_page = ttk.Frame(self._notebook, padding=10)
        run_page = ttk.Frame(self._notebook, padding=10)
        self._notebook.add(start_page, text="启动配置")
        self._notebook.add(run_page, text="运行监控")
        self._build_params(start_page)
        self._build_env(start_page)
        self._build_models(start_page)
        self._build_loaded(run_page)
        self._build_metrics(run_page)
        self._build_load(run_page)
        self._build_gpu(run_page)
        self._build_log(run_page)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    @staticmethod
    def _service_names() -> list[str]:
        """已登记的服务名列表（供服务名下拉框展示）。"""
        try:
            return [state.name for state in llmenv.list_states()]
        except ServeError:
            return []

    @classmethod
    def _default_service_name(cls) -> str:
        """唯一运行中的服务优先作为默认服务名，避免与实际服务不一致。"""
        try:
            states = llmenv.list_states()
        except ServeError:
            return DEFAULT_NAME
        alive = [state.name for state in states if llmserve.process_alive(state.pid, state.create_time)]
        return alive[0] if len(alive) == 1 else DEFAULT_NAME

    def _refresh_service_names(self) -> None:
        """按最新状态文件刷新服务名下拉选项。"""
        self._name_box.configure(values=self._service_names())

    def _build_params(self, parent: Any) -> None:
        ttk = self._ttk
        params = ttk.LabelFrame(parent, text="启动参数", padding=8)
        params.pack(fill="x")
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)
        self._name_box = ttk.Combobox(
            params,
            textvariable=self._name_var,
            values=self._service_names(),
        )
        self._name_box.bind("<<ComboboxSelected>>", self._on_service_selected)
        self._profile_box = ttk.Combobox(
            params,
            textvariable=self._profile_var,
            values=tuple(PROFILES),
            state="readonly",
        )
        self._profile_box.bind("<<ComboboxSelected>>", self._on_profile_changed)
        self._pair_row(
            params,
            0,
            "服务名",
            self._name_box,
            "档位",
            self._profile_box,
        )
        self._pair_row(
            params,
            1,
            "监听地址",
            ttk.Entry(params, textvariable=self._host_var),
            "端口",
            ttk.Entry(params, textvariable=self._port_var),
        )
        self._pair_row(
            params,
            2,
            "线程数",
            ttk.Entry(params, textvariable=self._threads_var),
            "",
            ttk.Label(params, text=""),
        )

        advanced, body = self._collapsible(parent, "高级参数（留空回落档位默认）", open_state=False)
        advanced.pack(fill="x", pady=(8, 0))
        body.columnconfigure(1, weight=1)
        body.columnconfigure(3, weight=1)
        self._pair_row(
            body,
            0,
            "上下文",
            ttk.Entry(body, textvariable=self._ctx_var),
            "KV",
            ttk.Combobox(body, textvariable=self._kv_var, values=("", *KV_SCALE), state="readonly"),
        )
        self._pair_row(
            body,
            1,
            "ubatch",
            ttk.Entry(body, textvariable=self._ubatch_var),
            "模型别名（留空自动）",
            ttk.Entry(body, textvariable=self._alias_var),
        )
        self._pair_row(
            body,
            2,
            "API Key",
            ttk.Entry(body, textvariable=self._api_key_var),
            "等待就绪（秒）",
            ttk.Entry(body, textvariable=self._wait_var),
        )

    def _pair_row(  # noqa: PLR0913, PLR0917
        self,
        target: Any,
        row: int,
        label: str,
        widget: Any,
        right_label: str,
        right_widget: Any,
    ) -> None:
        """在目标 Frame 的第 ``row`` 行放置两组“标签 + 输入控件”。"""
        ttk = self._ttk
        ttk.Label(target, text=label).grid(row=row, column=0, sticky="w", padx=(0, 6), pady=3)
        widget.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=3)
        if right_label:
            ttk.Label(target, text=right_label).grid(row=row, column=2, sticky="w", padx=(0, 6), pady=3)
        right_widget.grid(row=row, column=3, sticky="ew", pady=3)

    def _collapsible(self, parent: Any, title: str, *, open_state: bool = True) -> Any:
        """创建可折叠分区，返回（外层容器, 内容 Frame）。"""
        ttk = self._ttk
        container = ttk.Frame(parent)
        header = ttk.Frame(container)
        header.pack(fill="x")
        body = ttk.Frame(container, padding=(6, 4, 0, 0))
        state = {"open": open_state}

        def render() -> None:
            if state["open"]:
                body.pack(fill="x")
            else:
                body.pack_forget()
            button.configure(text=("▼ " if state["open"] else "▶ ") + title)

        def flip() -> None:
            state["open"] = not state["open"]
            render()

        button = ttk.Button(header, text="", style="Toolbutton", command=flip)
        button.pack(fill="x", anchor="w")
        render()
        return container, body

    def _build_models(self, parent: Any) -> None:
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="已配置模型（llmenv 登记的模型目录）", padding=8)
        box.pack(fill="both", expand=True, pady=(8, 0))

        row = ttk.Frame(box)
        row.pack(side="bottom", fill="x", pady=(6, 0))
        ttk.Entry(row, textvariable=self._model_var, state="readonly").pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        self._model_btn = ttk.Button(row, text="刷新", command=self._refresh_models)
        self._model_btn.pack(side="left")
        self._browse_btn = ttk.Button(row, text="浏览…", command=self._select_file)
        self._browse_btn.pack(side="left", padx=(6, 0))
        self._clear_btn = ttk.Button(row, text="清除选择", command=self._clear_selection)
        self._clear_btn.pack(side="left", padx=(6, 0))

        tree_frame = ttk.Frame(box)
        tree_frame.pack(fill="both", expand=True)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=("model", "size", "profiles", "tags", "fit"),
            show="headings",
            selectmode="browse",
            height=4,
        )
        self._tree.heading("model", text="模型")
        self._tree.heading("size", text="大小")
        self._tree.heading("profiles", text="匹配档位")
        self._tree.heading("tags", text="标签")
        self._tree.heading("fit", text="硬件")
        self._tree.column("model", width=290, anchor="w")
        self._tree.column("size", width=90, anchor="e", stretch=False)
        self._tree.column("profiles", width=130, anchor="w", stretch=False)
        self._tree.column("tags", width=100, anchor="w", stretch=False)
        self._tree.column("fit", width=90, anchor="w", stretch=False)
        self._strike_font = self._font.nametofont("TkDefaultFont").copy()
        self._strike_font.configure(overstrike=True)
        self._tree.tag_configure("unfit", foreground=_UNFIT_COLOR, font=self._strike_font)
        self._tree.tag_configure("busy", foreground=_BUSY_COLOR)
        yscroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=yscroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self._tree.bind("<<TreeviewSelect>>", self._on_model_selected)

        self._hint_var = self._tk.StringVar(value=_HINT_PLACEHOLDER)
        ttk.Label(box, textvariable=self._hint_var, wraplength=780, justify="left").pack(
            side="bottom", fill="x", pady=(6, 0)
        )

    def _build_env(self, parent: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        box, body = self._collapsible(parent, "环境信息（引擎 / 版本 / GPU）", open_state=False)
        box.pack(side="bottom", fill="x", pady=(8, 0))
        body.columnconfigure(1, weight=1)
        for row, (key, label) in enumerate((("engine", "引擎"), ("version", "版本"), ("gpu", "GPU"))):
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="nw", padx=(0, 8))
            var = tk.StringVar(value="检测中…")
            ttk.Label(body, textvariable=var, wraplength=760, justify="left").grid(row=row, column=1, sticky="w")
            self._info_vars[key] = var

    def _build_loaded(self, parent: Any) -> None:
        """已加载模型：列出运行中的服务及其模型、档位与地址。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="已加载模型（运行中的服务）", padding=6)
        box.pack(fill="x")
        self._loaded_tree = ttk.Treeview(
            box,
            columns=("name", "model", "profile", "address"),
            show="headings",
            height=3,
            selectmode="browse",
        )
        self._loaded_tree.heading("name", text="服务")
        self._loaded_tree.heading("model", text="已加载模型")
        self._loaded_tree.heading("profile", text="档位 / ctx")
        self._loaded_tree.heading("address", text="地址")
        self._loaded_tree.column("name", width=90, stretch=False)
        self._loaded_tree.column("model", width=360, anchor="w")
        self._loaded_tree.column("profile", width=140, anchor="w", stretch=False)
        self._loaded_tree.column("address", width=170, anchor="w", stretch=False)
        self._loaded_tree.pack(fill="x")
        self._loaded_var = self._tk.StringVar(value="无运行中的服务")
        ttk.Label(box, textvariable=self._loaded_var).pack(fill="x", pady=(4, 0))

    def _refresh_loaded(self) -> None:
        """刷新“已加载模型”列表（只统计进程仍存活的服务）。"""
        for item in self._loaded_tree.get_children():
            self._loaded_tree.delete(item)
        alive = [state for state in llmenv.list_states() if llmserve.process_alive(state.pid, state.create_time)]
        self._running_paths = {llmenv.path_key(Path(state.model)) for state in alive}
        for state in alive:
            self._loaded_tree.insert(
                "",
                "end",
                values=(
                    state.name,
                    Path(state.model).name,
                    f"{state.profile} / ctx {state.context}",
                    state.base_url,
                ),
            )
        self._loaded_var.set("" if alive else "无运行中的服务")

    def _build_metrics(self, parent: Any) -> None:
        """实时性能面板：显示 /metrics 计算的 token/s 与请求队列状态。"""
        ttk = self._ttk
        box = ttk.LabelFrame(
            parent,
            text=f"实时性能（服务运行中每 1s 采样 /metrics，速率按 {RATE_WINDOW_SECONDS:.0f}s 窗口平滑）",
            padding=8,
        )
        box.pack(fill="x")
        for column in range(4):
            box.columnconfigure(column, weight=1)
        items = (
            ("生成速率", "gen_rate"),
            ("提示速率", "prompt_rate"),
            ("处理中请求", "processing"),
            ("排队请求", "deferred"),
            ("KV 使用率", "kv"),
            ("累计生成 tokens", "predicted"),
            ("累计提示 tokens", "prompt"),
            ("采样状态", "monitor"),
        )
        for index, (label, key) in enumerate(items):
            row, column = divmod(index, 4)
            cell = ttk.Frame(box)
            cell.grid(row=row, column=column, sticky="w", padx=(0, 16), pady=2)
            ttk.Label(cell, text=label + ":").pack(side="left")
            ttk.Label(cell, textvariable=self._metric_vars[key], font=("", 9, "bold")).pack(side="left", padx=(4, 0))

    def _build_load(self, parent: Any) -> None:
        """系统负载面板：CPU / 频率 / 内存 / 交换与服务进程关键指标。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="系统负载（每 1s 采样；服务运行时额外显示服务进程指标）", padding=8)
        box.pack(fill="x", pady=(8, 0))
        items = (
            ("CPU", "cpu"),
            ("频率", "cpu_freq"),
            ("内存", "memory"),
            ("交换", "swap"),
            ("服务 CPU", "proc_cpu"),
            ("服务内存", "proc_mem"),
            ("线程", "proc_threads"),
            ("运行时长", "uptime"),
        )
        self._metric_grid(box, items, self._load_vars)

    def _build_gpu(self, parent: Any) -> None:
        """GPU 负载面板：利用率 / 显存 / 温度 / 功耗 / 风扇 / 频率。"""
        ttk = self._ttk
        box = ttk.LabelFrame(parent, text="GPU 负载（nvidia-smi，每 1s 采样；服务运行时额外显示服务显存）", padding=8)
        box.pack(fill="x", pady=(8, 0))
        for column in range(4):
            box.columnconfigure(column, weight=1)
        ttk.Label(box, text="设备:").grid(row=0, column=0, sticky="w", padx=(0, 6), pady=2)
        ttk.Label(
            box,
            textvariable=self._gpu_vars["gpu_name"],
            font=("", 9, "bold"),
            wraplength=700,
            justify="left",
        ).grid(row=0, column=1, columnspan=3, sticky="w", pady=2)
        items = (
            ("使用率", "gpu_util"),
            ("显存", "gpu_vram"),
            ("服务显存", "gpu_proc_vram"),
            ("温度", "gpu_temp"),
            ("功耗", "gpu_power"),
            ("风扇", "gpu_fan"),
            ("核心频率", "gpu_clock"),
        )
        self._metric_grid(box, items, self._gpu_vars, start_row=1)

    def _metric_grid(
        self,
        box: Any,
        items: tuple[tuple[str, str], ...],
        variables: dict[str, Any],
        *,
        columns: int = 4,
        start_row: int = 0,
    ) -> None:
        """把“标签 + 数值”按网格排入面板，供系统负载与 GPU 面板复用。"""
        ttk = self._ttk
        for column in range(columns):
            box.columnconfigure(column, weight=1)
        for index, (label, key) in enumerate(items):
            row, column = divmod(index, columns)
            cell = ttk.Frame(box)
            cell.grid(row=start_row + row, column=column, sticky="w", padx=(0, 16), pady=2)
            ttk.Label(cell, text=label + ":").pack(side="left")
            ttk.Label(cell, textvariable=variables[key], font=("", 9, "bold")).pack(side="left", padx=(4, 0))

    def _build_actions(self, outer: Any) -> None:
        ttk = self._ttk
        actions = ttk.Frame(outer)
        actions.pack(side="bottom", fill="x", pady=(8, 0))
        self._preview_btn = ttk.Button(actions, text="预览计划", command=self._preview)
        self._preview_btn.pack(side="left")
        self._start_btn = ttk.Button(actions, text="启动服务", command=self._start)
        self._start_btn.pack(side="left", padx=(6, 0))
        self._stop_btn = ttk.Button(actions, text="停止服务", command=self._stop)
        self._stop_btn.pack(side="left", padx=(6, 0))
        self._status_btn = ttk.Button(actions, text="刷新状态", command=self._refresh_status)
        self._status_btn.pack(side="left", padx=(6, 0))
        self._logs_btn = ttk.Button(actions, text="查看日志", command=self._show_logs)
        self._logs_btn.pack(side="left", padx=(6, 0))
        self._browser_btn = ttk.Button(actions, text="打开浏览器", command=self._open_browser)
        self._browser_btn.pack(side="left", padx=(6, 0))
        self._clear_log_btn = ttk.Button(actions, text="清空日志", command=self._clear_log)
        self._clear_log_btn.pack(side="left", padx=(6, 0))
        self._progress = ttk.Progressbar(actions, mode="indeterminate", length=200)
        self._progress.pack(side="right")

    def _build_log(self, parent: Any) -> None:
        tk = self._tk
        ttk = self._ttk
        log_frame = ttk.LabelFrame(parent, text="输出日志", padding=6)
        log_frame.pack(fill="both", expand=True, pady=(8, 0))
        scrollbar = ttk.Scrollbar(log_frame)
        scrollbar.pack(side="right", fill="y")
        self._log_text = tk.Text(log_frame, wrap="none", state="disabled", yscrollcommand=scrollbar.set, height=10)
        self._log_text.pack(side="left", fill="both", expand=True)
        scrollbar.configure(command=self._log_text.yview)

    # ------------------------------------------------------------------ #
    # 模型选择
    # ------------------------------------------------------------------ #
    def _on_model_selected(self, _event: object | None = None) -> None:
        if self._syncing_selection:
            return
        selection = self._tree.selection()
        if not selection:
            return
        index = int(selection[0])
        if 0 <= index < len(self._models):
            self._select_model(self._models[index].path)

    def _on_profile_changed(self, _event: object | None = None) -> None:
        """切换档位后按新的上下文 / KV 重算模型列表的硬件适配标注。"""
        self._render_models()
        if self._selected_model is not None:
            self._show_model_hint(self._selected_model)

    def _select_file(self) -> None:
        path = self._filedialog.askopenfilename(
            title="选择 GGUF 模型",
            filetypes=[("GGUF 文件", "*.gguf"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._tree.selection_remove(*self._tree.selection())
        self._select_model(Path(path).resolve())

    def _select_model(self, path: Path, *, update_name: bool = True) -> None:
        self._selected_model = path
        self._model_var.set(str(path))
        if update_name:
            self._name_var.set(self._service_name_from_model(path))
        self._show_model_hint(path)
        self._log(f"已选择模型: {path}")

    @classmethod
    def _service_name_from_model(cls, path: Path) -> str:
        """按模型文件名生成服务名：去掉量化后缀，把非法字符转为 ``-``。"""
        stem = _QUANT_SUFFIX_RE.sub("", path.stem)
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-._")
        return cleaned or DEFAULT_NAME

    @staticmethod
    def _load_state_safe(name: str) -> ServeState | None:
        """按服务名读取状态；名称非法或状态缺失时返回 ``None``。"""
        if not name:
            return None
        try:
            return llmenv.load_state(name, missing_ok=True)
        except ServeError:
            return None

    def _on_service_selected(self, _event: object | None = None) -> None:
        """选中服务名后同步定位其模型，便于停止已加载的模型。"""
        name = self._name_var.get().strip()
        state = self._load_state_safe(name)
        if state is None:
            return
        path = Path(state.model)
        self._select_model(path, update_name=False)
        self._sync_tree_selection(path)

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
        hint = f"标签: {label} | {advice}\n接入: {base}/v1，model={alias}"
        note = self._hardware_note(path)
        if note:
            hint += f"\n{note}"
            self._log(f"  {note}")
        self._hint_var.set(hint)
        self._log(f"  标签: {label}")
        self._log(f"  建议: {advice}")

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

    def _fits(self, size: int) -> tuple[llmmodel.VramFit | None, llmmodel.VramFit | None]:
        """返回（整卡能力, 当前可用）两套显存估算；未检测到 GPU 时均为 ``None``。"""
        if self._gpu_mib is None:
            return None, None
        _, context, kv_type = self._profile_context()
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
        detail = f"{name} 档（ctx {context} / kv {kv_type}）"
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

    def _clear_selection(self) -> None:
        self._tree.selection_remove(*self._tree.selection())
        self._selected_model = None
        self._model_var.set(_MODEL_PLACEHOLDER)
        self._hint_var.set(_HINT_PLACEHOLDER)

    # ------------------------------------------------------------------ #
    # 扫描模型与引擎信息
    # ------------------------------------------------------------------ #
    def _refresh_models(self) -> None:
        """后台扫描已配置模型目录并探测引擎 / GPU 信息，避免阻塞窗口。"""
        if self._busy:
            return
        if self._scan_thread is not None and self._scan_thread.is_alive():
            return
        self._model_btn.configure(state="disabled")
        self._model_var.set("正在扫描模型目录…")
        for var in self._info_vars.values():
            var.set("检测中…")
        self._scan_thread = threading.Thread(target=self._scan_models, daemon=True)
        self._scan_thread.start()
        self.root.after(200, self._poll_scan)

    def _scan_models(self) -> None:
        """后台线程：扫描模型目录并收集引擎 / GPU 信息。"""
        entries: list[ModelEntry] = []
        try:
            roots = [root for root in llmenv.models_roots() if root.is_dir()]
            if roots:
                entries = llmmodel.scan_models(roots)
            entries = self._with_running_models(entries)
        except OSError:
            entries = []
        info, gpu_mib = self._collect_env_info()
        usage = self._gpu_usage()
        free_mib = max(usage[1] - usage[0], 0) if usage is not None else None
        self._scan_results.put((entries, info, gpu_mib, free_mib))

    @staticmethod
    def _with_running_models(entries: list[ModelEntry]) -> list[ModelEntry]:
        """把运行中服务的模型并入清单，便于停止已加载模型时在列表中找到。"""
        seen = {llmenv.path_key(entry.path) for entry in entries}
        for state in llmenv.list_states():
            path = Path(state.model)
            key = llmenv.path_key(path)
            if key in seen or not llmserve.process_alive(state.pid, state.create_time):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            seen.add(key)
            entries.append(
                ModelEntry(
                    root=path.parent,
                    path=path,
                    size=size,
                    profiles=tuple(name for name, prof in PROFILES.items() if prof.quant.lower() in path.name.lower()),
                    tags=llmmodel.detect_model_tags(path.name),
                )
            )
        entries.sort(key=lambda entry: (-entry.size, str(entry.path)))
        return entries

    def _collect_env_info(self) -> tuple[dict[str, str], int | None]:
        """收集当前引擎 tag / 可执行文件、版本与显存（MiB）。"""
        info: dict[str, str] = {"engine": "", "version": "", "gpu": ""}
        try:
            engine = llmengine.resolve_engine()
        except llmbase.ServeError as exc:
            info["engine"] = f"未找到（{exc}）"
        else:
            info["engine"] = f"{engine.tag} · {engine.exe}"
            info["version"] = self._probe_version(engine)
        gpu = llmengine.detect_gpu()
        info["gpu"] = f"{gpu.name}（{gpu.memory_mib} MiB）" if gpu else "未检测到 NVIDIA GPU"
        return info, gpu.memory_mib if gpu is not None else None

    @staticmethod
    def _probe_version(engine: EngineInfo) -> str:
        """执行 ``llama-server --version`` 取首行版本信息。"""
        result = llmengine.run_with_engine(llmbase.run_command, [str(engine.exe), "--version"], engine)
        output = (result.stdout or result.stderr).strip()
        if result.returncode != 0:
            detail = output.splitlines()[0] if output else f"退出码 {llmbase.format_returncode(result.returncode)}"
            return f"获取失败（{detail}）"
        return output.splitlines()[0] if output else "未知"

    def _poll_scan(self) -> None:
        try:
            entries, info, gpu_mib, free_mib = self._scan_results.get_nowait()
        except queue.Empty:
            if self._scan_thread is not None and self._scan_thread.is_alive():
                self.root.after(200, self._poll_scan)
            return
        self._apply_models(entries, info, gpu_mib, free_mib)

    def _apply_models(
        self,
        entries: list[ModelEntry],
        info: dict[str, str],
        gpu_mib: int | None,
        free_mib: int | None,
    ) -> None:
        self._models = entries
        self._gpu_mib = gpu_mib
        self._gpu_free_mib = free_mib
        if self._selected_model is None:
            state = self._load_state_safe(self._name_var.get().strip())
            if state is not None and llmserve.process_alive(state.pid, state.create_time):
                self._select_model(Path(state.model), update_name=False)
        self._render_models()
        self._model_var.set(str(self._selected_model) if self._selected_model else _MODEL_PLACEHOLDER)
        if self._selected_model is not None:
            self._sync_tree_selection(self._selected_model)
        for key, var in self._info_vars.items():
            var.set(info.get(key) or "-")
        self._model_btn.configure(state="normal")
        if not entries:
            self._log("未发现已配置的 GGUF：用 fcmd llmenv add 登记模型目录，或直接“浏览…”选择模型文件")

    def _row_state(self, entry: ModelEntry) -> str:
        """单个模型的硬件状态：运行中 > 硬件不足 > 需释放显存 > 可用。"""
        if llmenv.path_key(entry.path) in self._running_paths:
            return _FIT_RUNNING
        return _fit_state(*self._fits(entry.size))

    @staticmethod
    def _state_tag(state: str) -> tuple[str, ...]:
        """状态对应的 Treeview tag（用于行着色）。"""
        return {_FIT_UNSUPPORTED: ("unfit",), _FIT_BUSY: ("busy",)}.get(state, ())

    def _render_models(self) -> None:
        """重建模型列表：硬件不足标红，仅当前余量不足标橙，并显示对应状态。"""
        self._tree.delete(*self._tree.get_children())
        for index, entry in enumerate(self._models):
            state = self._row_state(entry)
            self._tree.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    entry.relative,
                    human_size(entry.size),
                    ", ".join(entry.profiles) or "-",
                    " · ".join(entry.tags) or "-",
                    state,
                ),
                tags=self._state_tag(state),
            )
        self._show_basis_hint()

    def _update_fit_states(self) -> None:
        """按最新可用显存原地更新各行状态（不重建列表，保留选中项）。"""
        for index, entry in enumerate(self._models):
            iid = str(index)
            if not self._tree.exists(iid):
                continue
            state = self._row_state(entry)
            values = self._tree.item(iid, "values")
            if values and values[-1] != state:
                updated = (*values[:-1], state)
                self._tree.item(iid, values=updated, tags=self._state_tag(state))
        self._show_basis_hint()

    def _show_basis_hint(self) -> None:
        """未选中模型时在提示区展示硬件评估口径。"""
        if self._selected_model is not None:
            return
        available = self._gpu_free_mib if self._gpu_free_mib is not None else self._gpu_mib
        if available is None:
            self._hint_var.set("未检测到可用 NVIDIA GPU，无法评估显存适配（CPU 推理会很慢）")
            return
        _, context, kv_type = self._profile_context()
        total = f"{self._gpu_mib} MiB" if self._gpu_mib is not None else "-"
        self._hint_var.set(
            f"硬件评估口径：整卡 {total}，当前可用 {available} MiB，上下文 {context}，KV {kv_type}；"
            "红色=硬件不足（清理显存也放不下），橙色=需释放显存（整卡够用），选择模型可查看具体建议"
        )

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
            alias=self._alias_var.get().strip() or None,
            api_key=self._api_key_var.get().strip() or DEFAULT_API_KEY,
            threads=self._int_field(self._threads_var, "线程数"),
        )

    @staticmethod
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
        self._log(f"  监听: {http_base_url(plan.host, plan.port)}")
        tags = llmmodel.detect_model_tags(plan.model.name)
        self._log(f"  标签: {' · '.join(tags) or '未识别'}")
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

    def _stop(self) -> None:
        if self._busy:
            self._log("已有任务正在进行，请等待完成")
            return
        try:
            name = self._model_name()
        except ServeError as exc:
            self._messagebox.showerror("参数错误", str(exc))
            return
        if llmenv.load_state(name, missing_ok=True) is None:
            alive = [s.name for s in llmenv.list_states() if llmserve.process_alive(s.pid, s.create_time)]
            hint = f"；当前运行中: {', '.join(alive)}，请在下拉框中选择" if alive else ""
            self._status_var.set(f"{name} · 未启动")
            self._log(f"服务 {name} 未启动，无需停止{hint}")
            self._refresh_service_names()
            self._start_system_monitor()
            return
        self._stop_monitor("停止中…")
        self._begin_task(self._stop_work, (name,))

    def _begin_task(self, target: Any, args: tuple[Any, ...]) -> None:
        """启动后台线程执行目标函数，结果通过队列回传主线程。"""
        self._notebook.select(1)
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

    def _stop_work(self, name: str) -> None:
        assert self._messages is not None
        status = self._messages
        status.put(("log", f"正在停止服务 {name}…"))
        try:
            result = llmserve.stop_server(name)
        except ServeError as exc:
            status.put(("error", f"停止失败: {exc}"))
        else:
            status.put(("stopped", result))
        finally:
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
                    self._show_stopped(cast(StopResult, payload))
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

    def _show_stopped(self, result: StopResult) -> None:
        self._stop_monitor("已停止")
        self._refresh_loaded()
        self._refresh_service_names()
        if result.was_running:
            self._status_var.set(f"{result.state.name} · 已停止")
            self._log(f"已停止服务 {result.state.name}（pid={result.state.pid}）")
        else:
            self._status_var.set(f"{result.state.name} · 未运行（已清理状态）")
            self._log(f"服务 {result.state.name} 未在运行，已清理残留状态")
        self._start_system_monitor()

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

    def _show_logs(self) -> None:
        self._notebook.select(1)
        try:
            name = self._model_name()
            content = llmserve.read_log(name, 50)
        except ServeError as exc:
            self._messagebox.showerror("日志不可用", str(exc))
            return
        self._log(f"日志尾部（{name}）:")
        self._log(content)

    # ------------------------------------------------------------------ #
    # 实时性能监控（/metrics）
    # ------------------------------------------------------------------ #
    def _start_monitor(self, state: ServeState) -> None:
        """启动 ``/metrics`` 采样线程；同一服务已在监控时忽略。"""
        current = self._monitor_state
        if (
            self._monitor_thread is not None
            and self._monitor_thread.is_alive()
            and current is not None
            and (current.host, current.port) == (state.host, state.port)
        ):
            self._metric_vars["monitor"].set("监控中")
            return
        self._stop_monitor("重新连接监控…")
        stop = threading.Event()
        self._monitor_stop = stop
        self._monitor_state = state
        self._monitor_seq += 1
        seq = self._monitor_seq
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(state, stop), daemon=True)
        self._monitor_thread.start()
        self._metric_vars["monitor"].set("监控中")
        self._log(f"已开始实时性能采样: {state.base_url}/metrics")
        self.root.after(300, lambda: self._poll_metrics(seq))

    def _start_system_monitor(self) -> None:
        """服务未运行时仅采样系统负载，保证窗口打开即可看到 CPU / 内存 / GPU 使用情况。"""
        if self._closing:
            return
        if self._monitor_thread is not None and self._monitor_thread.is_alive():
            return
        stop = threading.Event()
        self._monitor_stop = stop
        self._monitor_state = None
        self._monitor_seq += 1
        seq = self._monitor_seq
        self._monitor_thread = threading.Thread(target=self._monitor_loop, args=(None, stop), daemon=True)
        self._monitor_thread.start()
        self._metric_vars["monitor"].set("服务未运行（仅采样系统负载）")
        self.root.after(300, lambda: self._poll_metrics(seq))

    def _stop_monitor(self, reason: str = "未监控") -> None:
        """停止采样线程并更新状态（重复调用安全）。"""
        if self._monitor_stop is not None:
            self._monitor_stop.set()
        thread = self._monitor_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.5)
        self._monitor_stop = None
        self._monitor_thread = None
        self._monitor_state = None
        self._monitor_seq += 1
        self._metric_vars["monitor"].set(reason)
        for var in (*self._load_vars.values(), *self._gpu_vars.values()):
            var.set("-")

    def _monitor_loop(self, state: ServeState | None, stop: threading.Event) -> None:
        """后台线程：每秒采样系统负载；服务运行时额外采样 ``/metrics``。"""
        window = RateWindow()
        process = self._attach_process(state.pid) if state is not None else None
        pid = state.pid if state is not None else 0
        while not stop.is_set():
            started = time.monotonic()
            self._monitor_results.put(("load", _sample_load(pid, process)))
            if state is None:
                self._monitor_results.put(("idle", None))
            else:
                try:
                    metrics = llmserve.fetch_metrics(state.host, state.port, state.api_key)
                except ServeError as exc:
                    self._monitor_results.put(("error", str(exc)))
                else:
                    gen_rate, prompt_rate = window.update(started, metrics.predicted_tokens, metrics.prompt_tokens)
                    self._monitor_results.put(("metrics", (metrics, gen_rate, prompt_rate)))
            stop.wait(max(0.2, 1.0 - (time.monotonic() - started)))

    @staticmethod
    def _attach_process(pid: int) -> Any | None:
        """绑定服务进程用于 CPU / 内存 / 运行时长采样；进程已退出时返回 ``None``。"""
        import psutil

        try:
            return psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def _poll_metrics(self, seq: int) -> None:
        """主线程刷新性能面板（按序号丢弃已停止监控的旧回调）。"""
        if self._closing or seq != self._monitor_seq:
            return
        try:
            while True:
                kind, payload = self._monitor_results.get_nowait()
                if kind == "metrics":
                    metrics, gen_rate, prompt_rate = cast(tuple[llmserve.ServeMetrics, float, float], payload)
                    self._apply_metrics(metrics, gen_rate, prompt_rate)
                elif kind == "load":
                    self._apply_load(cast(LoadSample, payload))
                elif kind == "idle":
                    self._apply_idle()
                else:
                    self._metric_vars["monitor"].set("采样失败: " + str(payload)[:60])
        except queue.Empty:
            pass
        self.root.after(500, lambda: self._poll_metrics(seq))

    def _apply_metrics(self, metrics: llmserve.ServeMetrics, gen_rate: float, prompt_rate: float) -> None:
        self._metric_vars["gen_rate"].set(f"{gen_rate:.1f} tok/s")
        self._metric_vars["prompt_rate"].set(f"{prompt_rate:.1f} tok/s")
        self._metric_vars["processing"].set(f"{metrics.processing:.0f}")
        self._metric_vars["deferred"].set(f"{metrics.deferred:.0f}")
        self._metric_vars["kv"].set(f"{metrics.kv_usage * 100:.1f} %")
        self._metric_vars["predicted"].set(f"{metrics.predicted_tokens:.0f}")
        self._metric_vars["prompt"].set(f"{metrics.prompt_tokens:.0f}")
        self._metric_vars["monitor"].set("监控中")

    def _apply_idle(self) -> None:
        """服务未运行：清空服务相关指标，仅保留系统负载采样。"""
        for key in ("gen_rate", "prompt_rate", "processing", "deferred", "kv", "predicted", "prompt"):
            self._metric_vars[key].set("-")
        self._metric_vars["monitor"].set("服务未运行（仅采样系统负载）")

    def _apply_load(self, sample: LoadSample) -> None:
        """刷新系统负载与 GPU 面板，并同步模型列表的显存适配状态。"""
        for key, value in _format_load(sample).items():
            self._load_vars[key].set(value)
        for key, value in _format_gpu(sample.gpu).items():
            self._gpu_vars[key].set(value)
        self._sync_free_vram(sample.gpu)

    def _sync_free_vram(self, gpu: GpuSample | None) -> None:
        """用实时 GPU 采样更新可用显存，服务停止释放显存后自动恢复为“可用”。"""
        if gpu is None or gpu.memory_used is None or gpu.memory_total is None:
            return
        free_mib = int(max(gpu.memory_total - gpu.memory_used, 0))
        if self._gpu_free_mib is not None and abs(free_mib - self._gpu_free_mib) < 256:
            return
        self._gpu_free_mib = free_mib
        if self._gpu_mib is None:
            self._gpu_mib = int(gpu.memory_total)
        self._update_fit_states()

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

    def _close(self) -> None:
        running = self._thread is not None and self._thread.is_alive()
        if running and not self._messagebox.askyesno("任务进行中", "启动 / 停止任务仍在执行，确实退出？"):
            return
        self._closing = True
        self._stop_monitor()
        self.root.destroy()

    # ------------------------------------------------------------------ #
    # 日志
    # ------------------------------------------------------------------ #
    def _log(self, message: str) -> None:
        self._log_text.configure(state="normal")
        self._log_text.insert("end", message + "\n")
        self._log_text.see("end")
        self._log_text.configure(state="disabled")

    def _clear_log(self) -> None:
        """清空输出日志区域。"""
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")


def run_gui() -> None:
    """创建并运行 Tk 主窗口。"""
    try:
        window = ServeWindow()
    except ImportError as exc:
        print_error(f"tkinter 不可用，无法启动窗口版: {exc}")
        return
    window.root.mainloop()


@fcmd.tool("llmservew", help="打开推理服务窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开推理服务窗口。"""
    run_gui()


@fcmd.main("llmservew")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
