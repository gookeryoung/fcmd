"""CLI 启动性能基准：冷/热启动的可复现测量与防回归检查。

覆盖场景（每条命令都是独立子进程，测量墙钟时间）：

- ``env`` / ``env --json``：内建命令的最重路径；
- ``graph pymake tc`` 的 ``tree`` / ``mermaid`` / ``layers`` 三种渲染；
- ``fcmd stattool --help`` 与 ``stattool.exe --help``：同一工具的两条入口
  （后者走 console script 的 :func:`fcmd.cli._entry.run_named`）；
- ``--version`` 与 ``python -c pass``：只剩解释器 / CLI 导入的固定成本地板。

口径（改动前后必须一致，否则数字不可比）：

- 同一台机器、同样的输出重定向：stdout 与 stderr 均指向空设备；
- ``NO_COLOR=1``、``TERM=dumb``，避免彩色探测与终端宽度影响输出量；
- 热启动 ``warm``：默认跑 15 次，丢弃前 3 次（首次含文件系统缓存冷启动），
  其余 12 次取中位数；
- 冷启动 ``cold``：每次测量前把 ``PYTHONPYCACHEPREFIX`` 指向一个新建的空
  目录（空字节码缓存），默认跑 5 次、丢弃首 1 次，其余 4 次取中位数；
- 中位数相邻两次结果差异超过 20% 说明宿主抖动，建议重跑一次再采信；若整体
  同乘以约 2，属于宿主电源/调度状态切换（本机 ``python -c pass`` 会在 27ms 与
  55ms 之间跳变），不要跨会话直接比较绝对数，用同会话 A/B 或 ``--check``；
- 清单快路径落地后，``BASELINES`` 已更新为优化后的本机中位数（HEAD d0adf1d，
  与改动前同会话交错 A/B 口径一致）；改动前数字见提交信息与本文件 git 历史。

用法::

    uv run python tests/cli/bench_startup.py              # 打印实测
    uv run python tests/cli/bench_startup.py --check      # 与基线比对
    uv run python tests/cli/bench_startup.py --json       # 机器可读
    uv run python tests/cli/bench_startup.py --skip-cold  # 只测热启动

``--check`` 以 :data:`BASELINES`（本机中位数，毫秒）为参照，高于
``--max-ratio``（默认 1.5）即判回归并返回非零退出码。该脚本不参与默认
``make test``，避免不同机器/CI 抖动；需要时用 ``make bench-startup`` 单独跑。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]

WARM_RUNS = 15
"""热启动默认测量次数（含丢弃）。"""

WARM_DISCARD = 3
"""热启动默认丢弃次数（首次含代码页缓存/文件系统缓存冷启动）。"""

COLD_RUNS = 5
"""冷启动默认测量次数（含丢弃）。"""

COLD_DISCARD = 1
"""冷启动默认丢弃次数。"""

DEFAULT_MAX_RATIO = 1.5
"""``--check`` 阈值：实测中位数高于基线的该比例即判回归。"""

BASELINES: dict[tuple[str, str], float] = {
    # (场景, warm|cold) -> 中位数（毫秒）。本机基线：Windows 11 / Python 3.14，
    # 内建命令按需导入 + 清单严格校验单次遍历落地后按同一口径采集
    # （N=15/5，丢弃 3/1）；改动前后的同位置 A/B 数字见提交信息。
    ("env", "warm"): 291.2,
    ("env", "cold"): 704.9,
    ("env-json", "warm"): 276.3,
    ("env-json", "cold"): 657.5,
    ("graph-tree", "warm"): 285.7,
    ("graph-tree", "cold"): 733.4,
    ("graph-mermaid", "warm"): 285.4,
    ("graph-mermaid", "cold"): 740.6,
    ("graph-layers", "warm"): 286.1,
    ("graph-layers", "cold"): 737.9,
    ("tool-help", "warm"): 278.6,
    ("tool-help", "cold"): 727.8,
    ("tool-exe-help", "warm"): 272.1,
    ("tool-exe-help", "cold"): 712.1,
    ("version", "warm"): 113.6,
    ("version", "cold"): 233.5,
    ("python-floor", "warm"): 55.3,
    ("python-floor", "cold"): 71.0,
}

_MODE_LABELS = {"warm": "热启动", "cold": "冷启动"}


@dataclass(frozen=True)
class Case:
    """一个基准场景：名字 + 命令行（不含可执行文件）。"""

    key: str
    argv: tuple[str, ...]
    description: str
    require_output: bool = True


@dataclass(frozen=True)
class Measurement:
    """一个场景在一种模式下的全部采样。"""

    key: str
    mode: str
    samples: tuple[float, ...]
    discarded: int

    @property
    def kept(self) -> tuple[float, ...]:
        return self.samples[self.discarded :]

    @property
    def median(self) -> float:
        return statistics.median(self.kept)


def _console_script(name: str) -> str:
    """定位当前虚拟环境里的 console script（可执行文件）。"""
    suffix = ".exe" if os.name == "nt" else ""
    candidate = Path(sys.executable).with_name(f"{name}{suffix}")
    if candidate.is_file():
        return str(candidate)
    found = shutil.which(name)
    if found is not None:
        return found
    raise RuntimeError(f"找不到 console script {name!r}：先运行 uv sync 安装项目入口")


def build_cases() -> list[Case]:
    """构造全部基准场景（所有条目共用同一套口径）。"""
    fcmd = _console_script("fcmd")
    stattool_exe = _console_script("stattool")
    return [
        Case("env", (fcmd, "env"), "环境信息（人类可读）"),
        Case("env-json", (fcmd, "env", "--json"), "环境信息（--json）"),
        Case("graph-tree", (fcmd, "graph", "pymake", "tc", "--format=tree"), "graph tree"),
        Case("graph-mermaid", (fcmd, "graph", "pymake", "tc", "--format=mermaid"), "graph mermaid"),
        Case("graph-layers", (fcmd, "graph", "pymake", "tc", "--format=layers"), "graph layers"),
        Case("tool-help", (fcmd, "stattool", "--help"), "fcmd <tool> --help"),
        Case("tool-exe-help", (stattool_exe, "--help"), "<tool>.exe --help"),
        Case("version", (fcmd, "--version"), "固定成本地板（--version）"),
        Case("python-floor", (sys.executable, "-c", "pass"), "固定成本地板（解释器）", require_output=False),
    ]


def _base_env() -> dict[str, str]:
    """构造基准子进程环境：固定输出相关变量，清掉干扰字节码缓存的变量。"""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # 父进程若设了这两个变量，会分别让「热启动」变成冷启动、让「冷启动」失效
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    return env


def _run_once(argv: tuple[str, ...], env: dict[str, str]) -> float:
    """执行一次并返回毫秒墙钟时间；退出码非零直接报错（不吞失败）。"""
    started = time.perf_counter()
    proc = subprocess.run(
        argv,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    elapsed = (time.perf_counter() - started) * 1000
    if proc.returncode != 0:
        raise RuntimeError(f"基准命令退出码 {proc.returncode}：{subprocess.list2cmdline(argv)}")
    return elapsed


def _verify(case: Case, env: dict[str, str]) -> None:
    """采样前先确认命令成功且有输出（失败时保留 stderr 便于定位）。"""
    proc = subprocess.run(
        case.argv,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"场景 {case.key} 命令失败（退出码 {proc.returncode}）：{subprocess.list2cmdline(case.argv)}\n{proc.stderr}"
        )
    if case.require_output and not proc.stdout.strip():
        raise RuntimeError(f"场景 {case.key} 无标准输出：{subprocess.list2cmdline(case.argv)}")


def _measure(case: Case, mode: str, *, runs: int, discard: int, env: dict[str, str]) -> Measurement:
    """按模式采样：冷启动每次换一个空字节码缓存目录。"""
    if discard >= runs:
        raise ValueError(f"{case.key}/{mode}: 丢弃次数 {discard} 必须小于采样次数 {runs}")
    samples: list[float] = []
    for _ in range(runs):
        if mode == "cold":
            cache_dir = Path(tempfile.mkdtemp(prefix="fcmd-bench-pyc-"))
            run_env = dict(env)
            run_env["PYTHONPYCACHEPREFIX"] = str(cache_dir)
            try:
                samples.append(_run_once(case.argv, run_env))
            finally:
                shutil.rmtree(cache_dir, ignore_errors=True)
        else:
            samples.append(_run_once(case.argv, env))
    return Measurement(case.key, mode, tuple(samples), discard)


def run_benchmark(
    *,
    warm_runs: int,
    warm_discard: int,
    cold_runs: int,
    cold_discard: int,
    skip_cold: bool,
) -> list[Measurement]:
    """采集全部场景的冷/热启动采样。"""
    env = _base_env()
    results: list[Measurement] = []
    for case in build_cases():
        _verify(case, env)
        results.append(_measure(case, "warm", runs=warm_runs, discard=warm_discard, env=env))
        if not skip_cold:
            results.append(_measure(case, "cold", runs=cold_runs, discard=cold_discard, env=env))
    return results


def check_regressions(results: list[Measurement], *, max_ratio: float) -> list[str]:
    """与基线比较中位数，返回超出阈值的说明（空表示通过）。"""
    problems: list[str] = []
    for result in results:
        baseline = BASELINES.get((result.key, result.mode))
        if baseline is None or baseline <= 0:
            continue
        if result.median > baseline * max_ratio:
            problems.append(
                f"{result.key}/{result.mode}: 实测 {result.median:.1f}ms 高于基线 {baseline:.1f}ms 的 {max_ratio:.0%}"
            )
    return problems


def _format_measurement(result: Measurement) -> str:
    kept = result.kept
    label = _MODE_LABELS.get(result.mode, result.mode)
    return (
        f"{result.key:<14} {label}  中位数 {result.median:8.1f} ms  最小 {min(kept):8.1f}  "
        f"最大 {max(kept):8.1f}  N={len(result.samples)}（丢弃 {result.discarded}）"
    )


def _result_mapping(result: Measurement) -> dict[str, Any]:
    kept = result.kept
    return {
        "case": result.key,
        "mode": result.mode,
        "median_ms": round(result.median, 1),
        "min_ms": round(min(kept), 1),
        "max_ms": round(max(kept), 1),
        "samples_ms": [round(value, 1) for value in result.samples],
        "discarded": result.discarded,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CLI 启动性能基准（冷/热启动）")
    parser.add_argument("--check", action="store_true", help="与 BASELINES 比对，超出阈值时返回非零退出码")
    parser.add_argument("--max-ratio", type=float, default=DEFAULT_MAX_RATIO, help="回归阈值比例（默认 1.5）")
    parser.add_argument("--warm-runs", type=int, default=WARM_RUNS, help=f"热启动采样次数（默认 {WARM_RUNS}）")
    parser.add_argument("--warm-discard", type=int, default=WARM_DISCARD, help=f"热启动丢弃次数（默认 {WARM_DISCARD}）")
    parser.add_argument("--cold-runs", type=int, default=COLD_RUNS, help=f"冷启动采样次数（默认 {COLD_RUNS}）")
    parser.add_argument("--cold-discard", type=int, default=COLD_DISCARD, help=f"冷启动丢弃次数（默认 {COLD_DISCARD}）")
    parser.add_argument("--skip-cold", action="store_true", help="跳过冷启动（空字节码缓存）采样")
    parser.add_argument("--json", action="store_true", help="输出 JSON 便于留档")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """运行基准；``--check`` 模式返回 0/1。"""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    started = time.perf_counter()
    results = run_benchmark(
        warm_runs=args.warm_runs,
        warm_discard=args.warm_discard,
        cold_runs=args.cold_runs,
        cold_discard=args.cold_discard,
        skip_cold=args.skip_cold,
    )
    total = time.perf_counter() - started

    if args.json:
        print(json.dumps([_result_mapping(item) for item in results], ensure_ascii=False, indent=2))
    else:
        print(f"仓库：{_ROOT}  解释器：{sys.executable}")
        print(
            f"口径：NO_COLOR=1、stdout/stderr→空设备；"
            f"热启动 N={args.warm_runs} 丢弃 {args.warm_discard}；"
            f"冷启动 N={args.cold_runs} 丢弃 {args.cold_discard}\n"
        )
        for result in results:
            print(_format_measurement(result))
        print(f"\n总耗时 {total:.1f}s（含全部采样）")

    if not args.check:
        return 0
    problems = check_regressions(results, max_ratio=args.max_ratio)
    for problem in problems:
        print(f"[回归] {problem}")
    print("回归检查：通过" if not problems else f"回归检查：失败（{len(problems)} 项）")
    return 0 if not problems else 1


if __name__ == "__main__":  # pragma: no cover - 独立脚本
    raise SystemExit(main())
