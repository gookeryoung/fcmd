"""llmservew 的运行监控：GPU / 系统负载采样、格式化、速率平滑与适配建议。

从 :mod:`fcmd.apps.locllm.llmservew` 拆出；``llmservew`` 仍 re-export 这些纯函数与
``_ServeWindowMonitor`` / ``_ServeWindowUI`` 所需的常量，既有导入路径不变。
"""

from __future__ import annotations

import csv
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

from fcmd.apps.locllm import llmbase, llmmodel, llmserve
from fcmd.apps.locllm.llmbase import ServeError, ServeState, human_size

_FIT_OK = "可用"
_FIT_BUSY = "需释放显存"
_FIT_UNSUPPORTED = "硬件不足"
_FIT_RUNNING = "运行中"


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


class _ServeWindowMonitor:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """ServeWindow 的实时监控方法（mixin）。"""

    if TYPE_CHECKING:
        # 宿主 ServeWindow 提供的成员：仅用于类型检查，运行时由宿主类实现。
        root: Any
        _closing: bool
        _gpu_mib: int | None
        _gpu_free_mib: int | None
        _monitor_seq: int
        _monitor_state: ServeState | None
        _monitor_stop: threading.Event | None
        _monitor_thread: threading.Thread | None
        _monitor_results: Any
        _log: Any
        _metric_vars: Any
        _load_vars: Any
        _gpu_vars: Any
        _update_fit_states: Any

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
