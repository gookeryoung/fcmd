"""filescan 性能基准：可重复执行的扫描吞吐测量与防回归检查。

覆盖场景（语料在临时目录现造，不读写仓库）：

- ``small``：5000 个 4 KiB 文本文件（大量小文件）；
- ``large``：20 个 2 MiB 文本文件（少量大文件）；
- ``deep``：8 层深目录、每层 60 个文件；
- ``mixed``：2000 个白名单内文件 + 3000 个白名单外文件，对比「白名单跳过」与
  ``ext_whitelist=("*",)`` 全后缀（改前行为）的耗时，输出加速比；
- ``rules``：同一小文件语料下，1 条关键词规则 vs 7 条内置规则的组合表达式。

用法::

    uv run python tests/apps/filescan/bench_filescan.py            # 打印实测
    uv run python tests/apps/filescan/bench_filescan.py --check    # 与基线比对
    uv run python tests/apps/filescan/bench_filescan.py --json     # 机器可读

``--check`` 以 :data:`BASELINES`（本机基线，文件/秒 与 MiB/秒）为参照，低于
``--min-ratio``（默认 0.5）即判回归并返回非零退出码。该脚本不参与默认
``make test``，避免不同机器/CI 抖动；需要时用 ``make bench-filescan`` 单独跑。
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fcmd.apps.filescan._engine import DEFAULT_TEXT_EXTENSIONS, ScanOptions, format_size, scan_paths
from fcmd.apps.filescan._rules import MATCH_KEYWORD, Matcher, Rule, RuleSet, builtin_ruleset

KIB = 1024
MIB = 1024 * KIB

BASELINES: dict[str, tuple[float, float]] = {
    # 场景 -> (文件/秒, MiB/秒)；本机基线（Windows 11 / Python 3.13 / workers=4）
    # 2026-11-12 优化后重测：进程池调度（绕开 GIL）+ 读缓冲按实际大小申请，
    # 基线取实测值下浮约 10%，`--check` 再按 --min-ratio（默认 50%）判回归。
    "small/1rule": (5900.0, 23.50),
    "small/builtin": (7200.0, 28.50),
    "large/builtin": (20.00, 40.00),
    "large/w1": (7.00, 14.00),
    "large/w8": (27.00, 55.00),
    "deep/builtin": (3500.0, 7.00),
    "mixed/whitelist": (3400.0, 6.70),
}

DEFAULT_MIN_RATIO = 0.5
"""``--check`` 阈值：实测吞吐低于基线的该比例即判回归。"""

_EXTRA_LABELS = {
    "speedup_vs_all_ext": "白名单相比全后缀加速",
    "speedup_vs_w1": "相比单线程加速",
}

LINE_TEMPLATE = "INFO ordinary log line without secrets 0123456789abcdef\n"
PHONE_LINE = "联系方式 13812345678，邮箱 zhang@example.com\n"


@dataclass
class Corpus:
    """已生成的基准语料。"""

    root: Path
    expected_files: int
    expected_bytes: int


@dataclass
class CaseResult:
    """单个场景的测量结果。"""

    key: str
    files_scanned: int
    files_skipped_extension: int
    bytes_read: int
    elapsed: float
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def files_per_second(self) -> float:
        return self.files_scanned / self.elapsed if self.elapsed > 0 else 0.0

    @property
    def mib_per_second(self) -> float:
        return self.bytes_read / MIB / self.elapsed if self.elapsed > 0 else 0.0


def _fill_file(path: Path, size: int, *, secret: bool) -> int:
    """按 ``size`` 字节写入文本文件（末条命中可插入敏感样例）。"""
    lines: list[str] = []
    total = 0
    index = 0
    while total < size:
        line = PHONE_LINE if secret and index % 97 == 0 else LINE_TEMPLATE
        lines.append(line)
        total += len(line.encode("utf-8"))
        index += 1
    data = "".join(lines).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def _build_small(root: Path) -> Corpus:
    """5000 个 4 KiB 文件。"""
    count = 5000
    total = 0
    for index in range(count):
        total += _fill_file(root / f"log-{index:05d}.log", 4 * KIB, secret=index % 10 == 0)
    return Corpus(root, count, total)


def _build_large(root: Path) -> Corpus:
    """20 个 2 MiB 文件。"""
    count = 20
    total = sum(_fill_file(root / f"big-{index:02d}.txt", 2 * MIB, secret=True) for index in range(count))
    return Corpus(root, count, total)


def _build_deep(root: Path) -> Corpus:
    """8 层深目录，每层 60 个文件。"""
    depth = 8
    per_level = 60
    total = 0
    directory = root
    for level in range(depth):
        directory = directory / f"level-{level}"
        directory.mkdir(parents=True)
        for index in range(per_level):
            total += _fill_file(directory / f"row-{index:02d}.log", 2 * KIB, secret=index % 20 == 0)
    return Corpus(root, depth * per_level, total)


def _build_mixed(root: Path) -> Corpus:
    """2000 个白名单内文件 + 3000 个白名单外文件（内容同样含敏感样例）。"""
    inside = 2000
    outside = 3000
    total = 0
    for index in range(inside):
        total += _fill_file(root / f"keep-{index:04d}.log", 2 * KIB, secret=index % 10 == 0)
    for index in range(outside):
        _fill_file(root / f"skip-{index:04d}.dat", 2 * KIB, secret=index % 10 == 0)
    return Corpus(root, inside + outside, total)


def _single_rule_ruleset() -> RuleSet:
    """1 条关键词规则的规则集。"""
    rule = Rule(
        id="phone",
        name="手机号",
        matcher=Matcher(kind=MATCH_KEYWORD, text="13812345678", case_sensitive=True),
    )
    return RuleSet(name="基准-单规则", rules=(rule,))


def _run(root: Path, ruleset: RuleSet, options: ScanOptions) -> CaseResult:
    """执行一次扫描并汇总吞吐。"""
    started = time.perf_counter()
    result = scan_paths([root], ruleset, options)
    elapsed = time.perf_counter() - started
    stats = result.stats
    return CaseResult(
        key="",
        files_scanned=stats.files_scanned,
        files_skipped_extension=stats.files_skipped_extension,
        bytes_read=stats.bytes_read,
        elapsed=elapsed,
    )


def _expect(result: CaseResult, *, files: int, extension_skips: int = 0) -> None:
    """校验语料确实被完整遍历，避免基准因过滤条件写错而虚快。"""
    if result.files_scanned != files:
        raise RuntimeError(f"扫描文件数 {result.files_scanned} != 预期 {files}")
    if result.files_skipped_extension != extension_skips:
        raise RuntimeError(f"后缀跳过 {result.files_skipped_extension} != 预期 {extension_skips}")


def _format_row(result: CaseResult) -> str:
    return (
        f"{result.key:<18} 文件 {result.files_scanned:>6}  后缀跳过 {result.files_skipped_extension:>5}  "
        f"读取 {format_size(result.bytes_read):>9}  耗时 {result.elapsed:8.3f}s  "
        f"{result.files_per_second:8.1f} 文件/s  {result.mib_per_second:7.2f} MiB/s"
    )


def run_benchmark(base: Path, *, workers: int) -> list[CaseResult]:
    """生成全部语料并测量，返回每个场景的结果。"""
    options = ScanOptions(workers=workers)
    results: list[CaseResult] = []

    small = _build_small(base / "small")
    results.append(_with_key(_run(small.root, _single_rule_ruleset(), options), "small/1rule"))
    _expect(results[-1], files=5000)
    results.append(_with_key(_run(small.root, builtin_ruleset(), options), "small/builtin"))
    _expect(results[-1], files=5000)

    large = _build_large(base / "large")
    # 大文件场景的并发维度：单线程 / 4 / 8 并发，用来观察进程池是否绕开 GIL。
    four = _with_key(_run(large.root, builtin_ruleset(), options), "large/builtin")
    single = _with_key(_run(large.root, builtin_ruleset(), ScanOptions(workers=1)), "large/w1")
    wide = _with_key(_run(large.root, builtin_ruleset(), ScanOptions(workers=8)), "large/w8")
    for item in (four, single, wide):
        _expect(item, files=20)
    for item in (four, wide):
        item.extra["speedup_vs_w1"] = single.elapsed / item.elapsed if item.elapsed else 0.0
    results.extend((four, single, wide))

    deep = _build_deep(base / "deep")
    results.append(_with_key(_run(deep.root, builtin_ruleset(), options), "deep/builtin"))
    _expect(results[-1], files=480)

    mixed = _build_mixed(base / "mixed")
    whitelist = _with_key(_run(mixed.root, builtin_ruleset(), options), "mixed/whitelist")
    _expect(whitelist, files=2000, extension_skips=3000)
    results.append(whitelist)
    all_exts = _with_key(
        _run(mixed.root, builtin_ruleset(), ScanOptions(workers=workers, ext_whitelist=("*",))),
        "mixed/all-ext",
    )
    _expect(all_exts, files=5000)
    results.append(all_exts)
    for item in results:
        if item.key == "mixed/whitelist":
            item.extra["speedup_vs_all_ext"] = all_exts.elapsed / item.elapsed if item.elapsed > 0 else 0.0
    return results


def _with_key(result: CaseResult, key: str) -> CaseResult:
    result.key = key
    return result


def check_regressions(results: list[CaseResult], *, min_ratio: float) -> list[str]:
    """与基线比较吞吐，返回未达阈值的说明（空表示通过）。"""
    problems: list[str] = []
    for result in results:
        baseline = BASELINES.get(result.key)
        if baseline is None or baseline == (0.0, 0.0):
            continue
        base_files, base_mib = baseline
        if result.files_per_second < base_files * min_ratio or result.mib_per_second < base_mib * min_ratio:
            problems.append(
                f"{result.key}: 实测 {result.files_per_second:.1f} 文件/s / {result.mib_per_second:.2f} MiB/s "
                f"低于基线 {base_files:.1f} / {base_mib:.2f} 的 {min_ratio:.0%}"
            )
    return problems


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="filescan 性能基准")
    parser.add_argument("--check", action="store_true", help="与 BASELINES 比对，低于阈值时返回非零退出码")
    parser.add_argument("--min-ratio", type=float, default=DEFAULT_MIN_RATIO, help="回归阈值比例（默认 0.5）")
    parser.add_argument("--workers", type=int, default=4, help="扫描并发线程数（默认 4）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 便于留档")
    parser.add_argument("--keep", action="store_true", help="保留临时语料目录")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """运行基准；``--check`` 模式返回 0/1。"""
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    base = Path(tempfile.mkdtemp(prefix="filescan-bench-"))
    try:
        print(f"语料目录：{base}（workers={args.workers}，白名单 {len(DEFAULT_TEXT_EXTENSIONS)} 项）")
        started = time.perf_counter()
        results = run_benchmark(base, workers=max(1, args.workers))
        total = time.perf_counter() - started
    finally:
        if not args.keep:
            shutil.rmtree(base, ignore_errors=True)

    if args.json:
        print(json.dumps([_result_mapping(item) for item in results], ensure_ascii=False, indent=2))
    else:
        for result in results:
            print(_format_row(result))
            for key, value in result.extra.items():
                print(f"{'':<18} {_EXTRA_LABELS.get(key, key)} {value:.2f}x")
        print(f"总耗时 {total:.2f}s（含语料生成）")

    if not args.check:
        return 0
    problems = check_regressions(results, min_ratio=args.min_ratio)
    for problem in problems:
        print(f"[回归] {problem}")
    print("回归检查：通过" if not problems else f"回归检查：失败（{len(problems)} 项）")
    return 0 if not problems else 1


def _result_mapping(result: CaseResult) -> dict[str, Any]:
    return {
        "scenario": result.key,
        "files_scanned": result.files_scanned,
        "files_skipped_extension": result.files_skipped_extension,
        "bytes_read": result.bytes_read,
        "elapsed": round(result.elapsed, 4),
        "files_per_second": round(result.files_per_second, 1),
        "mib_per_second": round(result.mib_per_second, 2),
        **result.extra,
    }


if __name__ == "__main__":  # pragma: no cover - 独立脚本
    raise SystemExit(main())
