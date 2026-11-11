"""llmservew 的性能测试页：典型场景定义、后台执行与结果汇总。

纯逻辑部分（场景表、单次 / 并发执行、结果指标、被测服务解析、结果行格式化）
不依赖 Tk，可直接单测；``_ServeWindowBench`` 只负责把执行结果经队列回传给
主线程并刷新界面。

示例
----
窗口「性能测试」页选择场景后点击“开始测试”，对运行中的服务批量实测延迟与
token 吞吐；也支持在窗口关闭时干净地停止后台测试。
"""

from __future__ import annotations

import queue
import statistics
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fcmd.tools.llm import llmbench, llmenv, llmserve
from fcmd.tools.llm.llmbase import Endpoint, ServeError, ServeState

__all__ = [
    "SCENARIOS",
    "BenchSample",
    "BenchScenario",
    "ScenarioResult",
    "endpoint_from_states",
    "format_result_row",
    "run_scenario",
    "scenario_by_key",
]

#: 结果表列标题（与 :func:`format_result_row` 的返回顺序一致）
RESULT_COLUMNS = ("场景", "请求", "输入 tok", "输出 tok", "墙钟", "聚合 tok/s", "单请求 tok/s", "状态")


@dataclass(frozen=True, slots=True)
class BenchScenario:
    """一个典型测试场景。

    ``prompt_tokens`` 为 0 时使用短提示词；``concurrency`` 大于 1 时每轮并行
    发起多个请求，用于测聚合吞吐。
    """

    key: str
    label: str
    detail: str
    prompt_tokens: int
    max_tokens: int
    concurrency: int = 1


#: 典型场景：覆盖单请求延迟、持续生成、代码补全、长上下文与并发吞吐
SCENARIOS: tuple[BenchScenario, ...] = (
    BenchScenario("short", "短对话", "默认短提示 + 128 输出，测单请求往返延迟", 0, 128),
    BenchScenario("generate", "长输出", "短提示 + 512 输出，测持续生成速度", 0, 512),
    BenchScenario("code", "代码补全", "约 1K 输入 + 256 输出，模拟代码补全", 1024, 256),
    BenchScenario("long8k", "长上下文 8K", "约 8K 输入，模拟长文档问答", 8192, 128),
    BenchScenario("long32k", "长上下文 32K", "约 32K 输入，模拟长会话", 32768, 128),
    BenchScenario("concurrent4", "并发吞吐 4 路", "4 路短请求并行，测聚合吞吐", 0, 128, concurrency=4),
)


def scenario_by_key(key: str) -> BenchScenario | None:
    """按 key 查找场景；未命中返回 ``None``。"""
    return next((scenario for scenario in SCENARIOS if scenario.key == key), None)


@dataclass(frozen=True, slots=True)
class BenchSample:
    """单个请求的实测结果（延迟秒数、输入 / 输出 tokens、输出吞吐）。"""

    latency_s: float
    prompt_tokens: int
    completion_tokens: int
    tokens_per_s: float


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    """一个场景的执行结果（可能包含多轮 / 多路请求）。"""

    scenario: BenchScenario
    samples: tuple[BenchSample, ...] = ()
    wall_s: float = 0.0
    error: str | None = None
    cancelled: bool = False

    @property
    def requests(self) -> int:
        """已完成的请求数。"""
        return len(self.samples)

    @property
    def total_prompt(self) -> int:
        """所有请求的输入 token 合计。"""
        return sum(sample.prompt_tokens for sample in self.samples)

    @property
    def total_completion(self) -> int:
        """所有请求的输出 token 合计。"""
        return sum(sample.completion_tokens for sample in self.samples)

    @property
    def aggregate_tps(self) -> float:
        """聚合吞吐：输出 token 合计 ÷ 实际墙钟（并发场景下体现总吞吐）。"""
        return self.total_completion / max(self.wall_s, 1e-6)

    @property
    def mean_tps(self) -> float:
        """单请求输出吞吐均值（优先用服务端 timings 的 decode 速率）。"""
        if not self.samples:
            return 0.0
        return statistics.fmean(sample.tokens_per_s for sample in self.samples)


def _short(text: str, limit: int = 48) -> str:
    """截断过长的错误信息，避免撑破结果表单元格。"""
    cleaned = " ".join(text.split())
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def format_result_row(result: ScenarioResult) -> tuple[str, ...]:
    """把一个场景结果格式化为结果表的一行（顺序与 :data:`RESULT_COLUMNS` 一致）。"""
    shared = (
        result.scenario.label,
        str(result.requests),
        str(result.total_prompt),
        str(result.total_completion),
        f"{result.wall_s:.2f} s",
    )
    if result.error is not None:
        return (*shared, "-", "-", "失败: " + _short(result.error))
    status = "已取消" if result.cancelled else "完成"
    return (*shared, f"{result.aggregate_tps:.1f}", f"{result.mean_tps:.1f}", status)


def endpoint_from_states(
    states: Sequence[ServeState],
    name: str = "",
    *,
    alive: Callable[[ServeState], bool] | None = None,
) -> Endpoint:
    """从已登记服务中挑选被测服务：同名运行中 → 唯一运行中 → 明确报错。"""
    check = alive or (lambda state: llmserve.process_alive(state.pid, state.create_time))
    running = [state for state in states if check(state)]
    if name:
        for state in running:
            if state.name == name:
                return state.endpoint
        if any(state.name == name for state in states):
            raise ServeError(f"服务 {name!r} 未在运行，请先在「启动配置」页启动")
    if len(running) == 1:
        return running[0].endpoint
    if not running:
        raise ServeError("没有运行中的服务：请先在「启动配置」页启动服务")
    names = ", ".join(state.name for state in running)
    raise ServeError(f"多个服务在运行，请在「启动配置」页选择服务名：{names}")


def _request_sample(
    send: Callable[..., Any],
    endpoint: Endpoint,
    scenario: BenchScenario,
    max_tokens: int,
    timeout: float,
) -> BenchSample:
    """发起一次请求并转换为 :class:`BenchSample`。"""
    result = send(
        endpoint,
        prompt_tokens=scenario.prompt_tokens or None,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    return BenchSample(
        latency_s=float(result.latency_s),
        prompt_tokens=int(result.prompt_tokens),
        completion_tokens=int(result.completion_tokens),
        tokens_per_s=float(result.tokens_per_s),
    )


def _run_round(
    send: Callable[..., Any],
    endpoint: Endpoint,
    scenario: BenchScenario,
    max_tokens: int,
    timeout: float,
) -> list[BenchSample]:
    """执行一轮请求；``concurrency`` 大于 1 时并行发起。"""
    count = max(1, scenario.concurrency)
    if count == 1:
        return [_request_sample(send, endpoint, scenario, max_tokens, timeout)]
    with ThreadPoolExecutor(max_workers=count) as pool:
        futures = [pool.submit(_request_sample, send, endpoint, scenario, max_tokens, timeout) for _ in range(count)]
        return [future.result() for future in futures]


def run_scenario(  # noqa: PLR0913 - 测试参数与依赖注入成组出现，拆开反而更易误用
    endpoint: Endpoint,
    scenario: BenchScenario,
    *,
    repeat: int = 1,
    max_tokens: int | None = None,
    timeout: float = 180.0,
    cancel: threading.Event | None = None,
    request: Callable[..., Any] | None = None,
) -> ScenarioResult:
    """执行一个场景的重复 / 并发请求，返回聚合结果。

    ``request`` 默认用 :func:`fcmd.tools.llm.llmbench.bench`，测试时可注入替身
    避免真实网络；请求失败记录在 :attr:`ScenarioResult.error` 中，不向外抛出。
    """
    send = request or llmbench.bench
    output_limit = max_tokens or scenario.max_tokens
    rounds = max(1, repeat)
    samples: list[BenchSample] = []
    error: str | None = None
    cancelled = False
    started = time.monotonic()
    for _ in range(rounds):
        if cancel is not None and cancel.is_set():
            cancelled = True
            break
        try:
            samples.extend(_run_round(send, endpoint, scenario, output_limit, timeout))
        except ServeError as exc:
            error = str(exc)
            break
    wall_s = max(time.monotonic() - started, 1e-6)
    return ScenarioResult(scenario, tuple(samples), wall_s, error, cancelled)


class _ServeWindowBench:  # pragma: no cover - Tk 窗口不纳入单元覆盖（CI 无显示环境）
    """ServeWindow 的性能测试页逻辑（mixin）。"""

    if TYPE_CHECKING:
        # 宿主 ServeWindow / _ServeWindowUI 提供的成员：仅用于类型检查。
        root: Any
        _name_var: Any
        _messagebox: Any
        _log: Any
        _bench_tree: Any
        _bench_result_tree: Any
        _bench_repeat_var: Any
        _bench_timeout_var: Any
        _bench_tokens_var: Any
        _bench_status_var: Any
        _bench_progress: Any
        _bench_start_btn: Any
        _bench_stop_btn: Any
        _bench_clear_btn: Any
        _bench_thread: threading.Thread | None
        _bench_stop: threading.Event | None
        _bench_results: queue.Queue[tuple[str, Any]] | None
        _bench_seq: int
        _bench_running: bool
        _bench_completed: int
        _bench_total: int

    # ------------------------------------------------------------------ #
    # 参数与场景
    # ------------------------------------------------------------------ #
    def _selected_scenarios(self) -> list[BenchScenario]:
        """取当前选中的场景（按场景表顺序）。"""
        keys = self._bench_tree.selection()
        if not keys:
            raise ServeError("请至少选择一个测试场景")
        return [SCENARIOS[int(iid)] for iid in sorted(keys, key=int)]

    def _bench_endpoint(self) -> Endpoint:
        """定位被测服务：优先表单中的服务名，其次唯一运行中的服务。"""
        state_name = self._name_var.get().strip()
        return endpoint_from_states(llmenv.list_states(), state_name)

    def _bench_settings(self) -> tuple[int, float, int | None]:
        """解析重复次数、超时与输出上限（非法输入抛 :class:`ServeError`）。"""
        try:
            repeat = int(self._bench_repeat_var.get().strip() or "1")
            timeout = float(self._bench_timeout_var.get().strip() or "180")
        except ValueError as exc:
            raise ServeError("重复次数必须是整数，超时必须是数字") from exc
        if repeat < 1:
            raise ServeError(f"重复次数必须 ≥ 1: {repeat}")
        if timeout <= 0:
            raise ServeError(f"请求超时必须大于 0: {timeout}")
        text = self._bench_tokens_var.get().strip()
        if not text:
            return repeat, timeout, None
        try:
            tokens = int(text)
        except ValueError as exc:
            raise ServeError(f"输出上限必须是整数: {text!r}") from exc
        if tokens < 1:
            raise ServeError(f"输出上限必须 ≥ 1: {tokens}")
        return repeat, timeout, tokens

    # ------------------------------------------------------------------ #
    # 执行与界面刷新
    # ------------------------------------------------------------------ #
    def _start_bench(self) -> None:
        """校验参数并在后台线程执行选中场景。"""
        if self._bench_running:
            self._log("性能测试仍在进行，请等待完成或先点击“停止”")
            return
        try:
            scenarios = self._selected_scenarios()
            endpoint = self._bench_endpoint()
            repeat, timeout, max_tokens = self._bench_settings()
        except ServeError as exc:
            self._messagebox.showerror("无法开始性能测试", str(exc))
            return
        stop = threading.Event()
        self._bench_stop = stop
        self._bench_results = queue.Queue()
        self._bench_running = True
        self._bench_completed = 0
        self._bench_total = len(scenarios)
        self._bench_seq += 1
        seq = self._bench_seq
        self._bench_result_tree.delete(*self._bench_result_tree.get_children())
        self._bench_status_var.set(f"测试中 0/{len(scenarios)}…")
        self._bench_progress.start(12)
        self._set_bench_busy(True)
        self._bench_thread = threading.Thread(
            target=self._bench_work,
            args=(endpoint, scenarios, repeat, max_tokens, timeout, stop),
            daemon=True,
        )
        self._bench_thread.start()
        self._log(
            f"开始性能测试：{len(scenarios)} 个场景 × 重复 {repeat} 次，"
            f"目标 {endpoint.base_url}（model={endpoint.model}）"
        )
        self.root.after(200, lambda: self._poll_bench(seq))

    def _bench_work(  # noqa: PLR0913, PLR0917 - 线程入口参数与测试配置一一对应
        self,
        endpoint: Endpoint,
        scenarios: Sequence[BenchScenario],
        repeat: int,
        max_tokens: int | None,
        timeout: float,
        stop: threading.Event,
    ) -> None:
        """后台线程：逐场景执行，结果经队列回传主线程。"""
        results = self._bench_results
        assert results is not None
        total = len(scenarios)
        for index, scenario in enumerate(scenarios, 1):
            if stop.is_set():
                break
            results.put(("progress", (index, total, scenario.label)))
            try:
                result = run_scenario(
                    endpoint,
                    scenario,
                    repeat=repeat,
                    max_tokens=max_tokens,
                    timeout=timeout,
                    cancel=stop,
                )
            except Exception as exc:  # 边界兜底：任何异常都要回传，避免界面卡在“测试中”
                result = ScenarioResult(scenario, error=str(exc), cancelled=stop.is_set())
            results.put(("result", result))
            if result.error is not None:
                # 请求失败通常意味着服务 / 显存已不可用，继续跑后续场景意义不大
                break
        results.put(("done", None))

    def _poll_bench(self, seq: int) -> None:
        """主线程轮询后台结果（按序号丢弃已停止测试的旧回调）。"""
        results = self._bench_results
        if results is None or seq != self._bench_seq:
            return
        finished = False
        try:
            while True:
                kind, payload = results.get_nowait()
                if kind == "progress":
                    index, total, label = payload
                    self._bench_status_var.set(f"测试中 {index}/{total}：{label}…")
                elif kind == "result":
                    self._apply_bench_result(payload)
                elif kind == "done":
                    finished = True
        except queue.Empty:
            pass
        if finished:
            self._finish_bench()
            return
        self.root.after(200, lambda: self._poll_bench(seq))

    def _apply_bench_result(self, result: ScenarioResult) -> None:
        """把单个场景结果写入结果表并同步状态与日志。"""
        self._bench_completed += 1
        tags: tuple[str, ...] = ()
        if result.error is not None:
            tags = ("error",)
        elif result.cancelled:
            tags = ("cancelled",)
        self._bench_result_tree.insert("", "end", values=format_result_row(result), tags=tags)
        self._bench_status_var.set(f"已完成 {self._bench_completed}/{self._bench_total}")
        if result.error is not None:
            self._log(f"性能测试失败（{result.scenario.label}）：{result.error}")
        else:
            self._log(
                f"性能测试 {result.scenario.label}: {result.requests} 请求 · "
                f"输出 {result.total_completion} tok · 墙钟 {result.wall_s:.2f}s · "
                f"聚合 {result.aggregate_tps:.1f} tok/s · 单请求 {result.mean_tps:.1f} tok/s"
                + ("（已取消）" if result.cancelled else "")
            )

    def _finish_bench(self) -> None:
        """测试结束：停止进度条、恢复按钮并更新状态栏。"""
        stopped = self._bench_stop is not None and self._bench_stop.is_set()
        self._bench_running = False
        self._bench_stop = None
        self._bench_results = None
        self._bench_progress.stop()
        self._set_bench_busy(False)
        if stopped:
            self._bench_status_var.set(f"已停止（完成 {self._bench_completed}/{self._bench_total}）")
            self._log("性能测试已停止")
        else:
            self._bench_status_var.set(f"测试完成（{self._bench_completed}/{self._bench_total}）")

    def _stop_bench(self) -> None:
        """请求停止：当前请求完成后不再发起新的场景 / 轮次。"""
        if not self._bench_running or self._bench_stop is None:
            return
        self._bench_stop.set()
        self._bench_status_var.set("正在停止（等待当前请求结束）…")
        self._log("已请求停止性能测试（当前请求结束后停止）")

    def _clear_bench(self) -> None:
        """清空结果表（测试进行中保留，避免隐藏正在写入的结果）。"""
        if self._bench_running:
            self._log("性能测试进行中，结束后再清空结果")
            return
        self._bench_result_tree.delete(*self._bench_result_tree.get_children())
        self._bench_status_var.set("已清空结果")

    def _set_bench_busy(self, busy: bool) -> None:
        """切换测试按钮可用状态。"""
        state = "disabled" if busy else "normal"
        self._bench_start_btn.configure(state=state)
        self._bench_clear_btn.configure(state=state)
        self._bench_stop_btn.configure(state="normal" if busy else "disabled")

    def _close_bench(self) -> None:
        """关窗时停止后台测试并等待线程退出。"""
        if self._bench_stop is not None:
            self._bench_stop.set()
        thread = self._bench_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.5)
        self._bench_stop = None
        self._bench_results = None
