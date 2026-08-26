"""性能基线测量。

用标准库 ``time.perf_counter`` + ``subprocess`` 测量 fcmd 四项核心基线，
为后续性能优化提供可复现的对照数据：

- **cold start**: 干净子进程中 ``import fcmd`` 净耗时（目标 < 100ms）
- **tool discovery**: ``_ensure_tools_discovered()`` 首次扫描 ``fcmd.cli`` 包的耗时
- **DAG run**: 3 任务链 ``fx.graph`` + ``fx.run`` 端到端耗时
- **tool exec**: ``run_tool`` 单工具冷执行端到端耗时（含按需导入工具模块）

每项在独立子进程内测量，避免主进程 import 缓存污染；预热 1 次后取 6 次统计
（min/median/mean/stdev）。用 ``@pytest.mark.slow`` 标记，``make check`` 默认跳过。

直接运行 ``python tests/test_perf_baseline.py`` 生成基线报告。

回归断言阈值宽松（目标的 2-3 倍），仅防严重退化，不作精度门禁。
"""

from __future__ import annotations

import statistics
import subprocess
import sys
from pathlib import Path

import pytest

# 子进程内测量脚本：打印单次耗时（毫秒），便于主进程解析。
# 每段脚本须在末尾 print 一个浮点数。

_COLD_START_SCRIPT = (
    "import time\nt=time.perf_counter()\nimport fcmd  # noqa: F401\nprint((time.perf_counter()-t)*1000)\n"
)

_DISCOVERY_SCRIPT = (
    "import time\n"
    "import fcmd.cli.main as m\n"
    "t=time.perf_counter()\n"
    "m._ensure_tools_discovered()\n"
    "print((time.perf_counter()-t)*1000)\n"
)

_DAG_RUN_SCRIPT = (
    "import time\n"
    "import fcmd as fx\n"
    "@fx.task\n"
    "def extract() -> list[int]:\n"
    "    return [1, 2, 3]\n"
    "@fx.task\n"
    "def double(extract: list[int]) -> list[int]:\n"
    "    return [x * 2 for x in extract]\n"
    "@fx.task\n"
    "def total(double: list[int]) -> int:\n"
    "    return sum(double)\n"
    "t=time.perf_counter()\n"
    "g=fx.graph(extract, double, total)\n"
    "fx.run(g)\n"
    "print((time.perf_counter()-t)*1000)\n"
)

_TOOL_EXEC_SCRIPT = (
    "import time\n"
    "import fcmd.cli.conv.codetool  # noqa: F401  触发 @fcmd.tool 注册\n"
    "from fcmd.apis.toolkit import run_tool\n"
    "t=time.perf_counter()\n"
    "run_tool('codetool', ['base64', 'hello'])\n"
    "print((time.perf_counter()-t)*1000)\n"
)

# 回归断言阈值（毫秒）：目标值的 2-3 倍，仅防严重退化
_COLD_START_BUDGET_MS = 200.0
_DISCOVERY_BUDGET_MS = 500.0
_DAG_RUN_BUDGET_MS = 300.0
_TOOL_EXEC_BUDGET_MS = 300.0

# 测量次数：1 次预热（丢弃）+ 6 次统计
_WARMUP = 1
_RUNS = 6


def _measure(script: str, runs: int = _RUNS, warmup: int = _WARMUP) -> list[float]:
    """在独立子进程内重复执行脚本，返回耗时样本（毫秒）。

    Parameters
    ----------
    script:
        子进程内执行的 Python 代码，末尾须 ``print`` 一个浮点数（毫秒）
    runs:
        统计样本数（不含预热）
    warmup:
        预热次数（丢弃，消除页缓存/分支预测冷启动）

    Returns
    -------
    list[float]
        耗时样本列表（毫秒），长度等于 ``runs``
    """
    samples: list[float] = []
    for _ in range(warmup + runs):
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=True,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        # 子进程可能向 stdout 打印业务输出（如 run_tool 的工具日志），
        # 脚本末尾 print 的耗时始终在最后一行，取末行解析。
        last_line = result.stdout.strip().splitlines()[-1]
        samples.append(float(last_line))
    return samples[warmup:]


def _format_stats(name: str, samples: list[float], budget: float) -> str:
    """格式化单项基线统计为可读字符串。"""
    return (
        f"  {name:<14} min={min(samples):7.2f}ms  "
        f"median={statistics.median(samples):7.2f}ms  "
        f"mean={statistics.mean(samples):7.2f}ms  "
        f"stdev={statistics.stdev(samples):6.2f}ms  "
        f"budget={budget:.0f}ms"
    )


@pytest.mark.slow
def test_cold_start_baseline() -> None:
    """冷启动基线：``import fcmd`` 应在 200ms 内完成（目标 100ms）。"""
    samples = _measure(_COLD_START_SCRIPT)
    print("\n" + _format_stats("cold start", samples, _COLD_START_BUDGET_MS))
    assert min(samples) < _COLD_START_BUDGET_MS


@pytest.mark.slow
def test_tool_discovery_baseline() -> None:
    """工具发现基线：扫描 ``fcmd.cli`` 包应在 500ms 内完成。"""
    samples = _measure(_DISCOVERY_SCRIPT)
    print("\n" + _format_stats("discovery", samples, _DISCOVERY_BUDGET_MS))
    assert min(samples) < _DISCOVERY_BUDGET_MS


@pytest.mark.slow
def test_dag_run_baseline() -> None:
    """DAG 执行基线：3 任务链 ``graph`` + ``run`` 应在 300ms 内完成。"""
    samples = _measure(_DAG_RUN_SCRIPT)
    print("\n" + _format_stats("DAG run", samples, _DAG_RUN_BUDGET_MS))
    assert min(samples) < _DAG_RUN_BUDGET_MS


@pytest.mark.slow
def test_tool_exec_baseline() -> None:
    """工具执行基线：``run_tool`` 单工具冷执行应在 300ms 内完成。"""
    samples = _measure(_TOOL_EXEC_SCRIPT)
    print("\n" + _format_stats("tool exec", samples, _TOOL_EXEC_BUDGET_MS))
    assert min(samples) < _TOOL_EXEC_BUDGET_MS


@pytest.mark.slow
def test_perf_baseline_report() -> None:
    """汇总四项基线并打印报告（不断言，供人工查看与回归对比）。"""
    cold = _measure(_COLD_START_SCRIPT)
    disc = _measure(_DISCOVERY_SCRIPT)
    dag = _measure(_DAG_RUN_SCRIPT)
    tool = _measure(_TOOL_EXEC_SCRIPT)

    report = [
        "",
        "=" * 72,
        "fcmd 性能基线报告",
        "=" * 72,
        f"Python: {sys.version.split()[0]}  Platform: {sys.platform}",
        f"样本数: {_RUNS}（预热 {_WARMUP} 次已剔除）",
        "-" * 72,
        _format_stats("cold start", cold, _COLD_START_BUDGET_MS),
        _format_stats("discovery", disc, _DISCOVERY_BUDGET_MS),
        _format_stats("DAG run", dag, _DAG_RUN_BUDGET_MS),
        _format_stats("tool exec", tool, _TOOL_EXEC_BUDGET_MS),
        "-" * 72,
        "说明: min 为最优值（无干扰）；median 为典型值；budget 为回归断言上限。",
        "=" * 72,
        "",
    ]
    print("\n".join(report))


if __name__ == "__main__":
    # 直接运行：执行汇总报告（含四项测量）
    test_perf_baseline_report()
