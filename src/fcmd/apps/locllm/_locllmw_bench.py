"""locllmw 的性能测试页：典型场景定义、后台执行与结果汇总。

纯逻辑部分（场景表、单次 / 并发执行、结果指标、被测服务解析、结果行格式化）
不依赖任何 GUI 工具包，可直接单测；Qt 监测页只负责把执行结果经信号回传给
主线程并刷新界面。

示例
----
窗口「性能测试」页选择场景后点击“开始测试”，对运行中的服务批量实测延迟与
token 吞吐；也支持在窗口关闭时干净地停止后台测试。
"""

from __future__ import annotations

import csv
import io
import statistics
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from fcmd.apps.locllm import llmbench, llmserve
from fcmd.apps.locllm.llmbase import Endpoint, ServeError, ServeState

__all__ = [
    "BENCH_CHECKED",
    "BENCH_UNCHECKED",
    "SCENARIOS",
    "BenchSample",
    "BenchScenario",
    "ScenarioResult",
    "checked_scenarios",
    "endpoint_from_states",
    "format_result_csv",
    "format_result_row",
    "run_scenario",
    "scenario_by_key",
]

#: 结果表列标题（与 :func:`format_result_row` 的返回顺序一致）
RESULT_COLUMNS = ("场景", "请求", "输入 tok", "输出 tok", "墙钟", "聚合 tok/s", "单请求 tok/s", "状态")

#: 场景表勾选列的两种标记（点击切换）
BENCH_CHECKED = "☑"
BENCH_UNCHECKED = "☐"


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


def checked_scenarios(rows: Sequence[tuple[str, ...]]) -> list[BenchScenario]:
    """按场景表各行的勾选标记取场景。

    ``rows`` 与 :data:`SCENARIOS` 同序，每行首列为 :data:`BENCH_CHECKED` 时视为勾选。
    """
    return [scenario for scenario, values in zip(SCENARIOS, rows, strict=True) if values and values[0] == BENCH_CHECKED]


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


def format_result_csv(rows: Sequence[Sequence[Any]]) -> str:
    """把结果表行导出为 CSV 文本（首行为 :data:`RESULT_COLUMNS` 表头）。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(RESULT_COLUMNS)
    writer.writerows(rows)
    return buffer.getvalue()


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

    ``request`` 默认用 :func:`fcmd.apps.locllm.llmbench.bench`，测试时可注入替身
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
