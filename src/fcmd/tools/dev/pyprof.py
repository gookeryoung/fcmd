"""pyprof - 非侵入式 Python 代码性能分析工具。

参考 ``bitool_gui.pyprofiler`` 的交互形态：用标准库 :mod:`cProfile` 采集目标
脚本 / 模块的运行数据，转换为热点排行、调用关系、火焰图与模块聚合，
输出单文件自包含 HTML 报告（无外部 CDN、无模板依赖）。

``run`` 与 ``load`` 两个子命令覆盖「现场采集」和「离线分析已有数据」两种场景，
不改动目标代码，目标脚本的 ``sys.argv`` / ``sys.path`` 在执行后原样还原。

示例
----
    fcmd pyprof run app.py                     # 分析脚本并打开报告
    fcmd pyprof run app.py --no-open           # 只生成报告
    fcmd pyprof run -- app.py --level 4        # ``--`` 之后的参数传给脚本
    fcmd pyprof module json.tool data.json     # 以 python -m 方式分析模块
    fcmd pyprof load saved.prof                # 分析已有 cProfile 数据
"""

from __future__ import annotations

import ast
import cProfile
import functools
import io
import os
import pstats
import runpy
import sys
import sysconfig
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, cast

import fcmd
from fcmd.console import get_console
from fcmd.core.errors import FcmdError
from fcmd.tools.dev._pyprof_html import (
    _format_number,
    _format_time,
    _short_filename,
    render_html,
)

__all__ = [
    "CodeProfileReport",
    "FunctionStat",
    "ModuleStat",
    "ProfileLoader",
    "ProfileRunner",
    "analyze_profile",
    "load_profile",
    "profile_module",
    "run_profile",
]

# pstats 内部结构：函数键 = (文件名, 行号, 函数名)
_FuncKey = tuple[str, int, str]
_CallData = tuple[int, int, float, float]
_RawEntry = tuple[int, int, float, float, dict[_FuncKey, _CallData]]
_RawStats = dict[_FuncKey, _RawEntry]
_Callees = dict[_FuncKey, dict[_FuncKey, _CallData]]


# ============================================================================
# 数据模型
# ============================================================================


@dataclass(frozen=True)
class CallRef:
    """调用关系中的一端（调用者或被调用者）。

    属性
    ----
    filename:
        文件名。
    lineno:
        行号。
    func_name:
        函数名。
    ncalls:
        调用次数（``总次数/原始次数`` 或单值字符串）。
    tottime:
        自身耗时（秒）。
    cumtime:
        累计耗时（秒）。
    """

    filename: str
    lineno: int
    func_name: str
    ncalls: str
    tottime: float
    cumtime: float

    @property
    def display_name(self) -> str:
        """适合展示的函数标识（``文件名:行号 函数名``）。"""
        return f"{_short_filename(self.filename)}:{self.lineno} {self.func_name}"


@dataclass
class FunctionStat:
    """单个函数的性能统计。

    属性
    ----
    filename:
        文件名。
    lineno:
        行号。
    func_name:
        函数名。
    ncalls:
        调用次数。
    tottime:
        自身耗时（秒），不含子调用。
    cumtime:
        累计耗时（秒），含子调用。
    tottime_percall:
        单次调用的自身耗时。
    cumtime_percall:
        单次调用的累计耗时。
    origin:
        来源分类：``user`` / ``stdlib`` / ``third_party`` / ``builtin``。
    callers:
        调用者列表。
    callees:
        被调用者列表。
    """

    filename: str
    lineno: int
    func_name: str
    ncalls: str
    tottime: float
    cumtime: float
    tottime_percall: float
    cumtime_percall: float
    origin: str = ""
    callers: list[CallRef] = field(default_factory=list)
    callees: list[CallRef] = field(default_factory=list)

    @property
    def full_name(self) -> str:
        """完整标识名（``文件名:行号(函数名)``）。"""
        return f"{self.filename}:{self.lineno}({self.func_name})"

    @property
    def display_name(self) -> str:
        """适合展示的函数标识。"""
        return f"{_short_filename(self.filename)}:{self.lineno} {self.func_name}"


@dataclass
class TimeSlice:
    """时间分布切片（用于概览页柱状图）。

    属性
    ----
    display_name:
        展示名。
    tottime:
        自身耗时（秒）。
    cumulative:
        累计耗时（秒）。
    percentage:
        占总自身耗时的百分比。
    origin:
        来源分类。
    """

    display_name: str
    tottime: float
    cumulative: float
    percentage: float
    origin: str = ""


@dataclass
class FlameNode:
    """火焰图节点（自底向上的调用树）。

    属性
    ----
    name:
        函数名。
    filename:
        文件名。
    lineno:
        行号。
    value:
        该调用路径上的累计耗时。
    self_time:
        该节点自身的耗时。
    origin:
        来源分类。
    children:
        子节点列表。
    """

    name: str
    filename: str
    lineno: int
    value: float
    self_time: float
    origin: str = ""
    children: list[FlameNode] = field(default_factory=list)


@dataclass
class ModuleStat:
    """按文件聚合的模块级统计。

    属性
    ----
    filename:
        文件名。
    display_name:
        展示名（普通文件取 basename）。
     total_tottime:
        模块内函数自身耗时之和。
    total_cumtime:
        模块内函数累计耗时的最大值。
    call_count:
        模块内函数调用次数之和。
    func_count:
        函数数量。
    origin:
        来源分类。
    functions:
        模块内函数（按自身耗时降序，最多保留 10 个）。
    """

    filename: str
    display_name: str
    total_tottime: float
    total_cumtime: float
    call_count: int
    func_count: int
    origin: str = ""
    functions: list[FunctionStat] = field(default_factory=list)


@dataclass
class CodeProfileReport:
    """完整的代码性能分析报告。

    属性
    ----
    target_name:
        分析目标（脚本路径或 ``-m 模块名``）。
    generated_at:
        生成时间。
    total_calls:
        总调用次数。
    primitive_calls:
        原始调用次数。
    total_time:
        所有函数自身耗时之和（秒）。
    unique_functions:
        唯一函数数。
    function_stats:
        全部函数统计（按 ``sort_by`` 排序）。
    hotspots:
        热点函数（按自身耗时降序的 Top-N）。
    time_distribution:
        时间分布切片。
    flame_roots:
        火焰图根节点。
    module_stats:
        模块级统计。
    sort_by:
        排序字段。
    """

    target_name: str
    generated_at: str
    total_calls: int
    primitive_calls: int
    total_time: float
    unique_functions: int
    function_stats: list[FunctionStat]
    hotspots: list[FunctionStat]
    time_distribution: list[TimeSlice]
    flame_roots: list[FlameNode]
    module_stats: list[ModuleStat]
    sort_by: str


# ============================================================================
# 数据采集与加载
# ============================================================================


class ProfileRunner:
    """非侵入式 cProfile 采集引擎。

    通过 :class:`cProfile.Profile` 包裹 :mod:`runpy` 运行目标脚本 / 模块，
    目标代码无需任何改动；目标抛出的异常不会中断报告生成。
    """

    @staticmethod
    def _validate_script(script: Path) -> None:
        """校验脚本可被分析。

        参数
        ----
        script:
            目标脚本路径。

        异常
        ----
        FcmdError
            文件不存在、不是 ``.py`` 文件、无法读取或存在语法错误。
        """
        if not script.exists():
            msg = f"脚本文件不存在: {script}"
            raise FcmdError(msg)
        if not script.is_file():
            msg = f"不是文件: {script}"
            raise FcmdError(msg)
        if script.suffix.lower() != ".py":
            msg = f"文件扩展名不是 .py: {script}"
            raise FcmdError(msg)
        try:
            source = script.read_text(encoding="utf-8")
        except PermissionError as exc:
            msg = f"文件无读取权限: {script}"
            raise FcmdError(msg) from exc
        except UnicodeDecodeError as exc:
            msg = f"文件编码错误，无法使用 UTF-8 读取: {script}"
            raise FcmdError(msg) from exc
        try:
            ast.parse(source, filename=str(script))
        except SyntaxError as exc:
            msg = f"Python 语法错误: {exc.msg}（{script} 第 {exc.lineno} 行）"
            raise FcmdError(msg) from exc

    @staticmethod
    def _profile(action: Callable[[], None]) -> pstats.Stats:
        """执行 ``action`` 并返回 cProfile 统计。

        ``action`` 为无参可调用对象；目标抛出的 ``SystemExit`` 视为正常退出，
        其他异常打印警告后继续产出已采集的数据。
        """
        profile = cProfile.Profile()
        profile.enable()
        try:
            action()
        except SystemExit:
            pass
        except Exception as exc:  # 用户脚本可抛任意异常，宽捕获保证报告可用
            get_console().print(f"[yellow]警告:[/yellow] 目标执行出错，将基于已采集数据生成报告: {exc}")
        finally:
            profile.disable()
        return pstats.Stats(profile, stream=io.StringIO())

    @staticmethod
    def run_script(script: Path, args: list[str] | None = None) -> pstats.Stats:
        """运行并分析 Python 脚本。

        参数
        ----
        script:
            目标脚本路径。
        args:
            传给目标脚本的命令行参数（不含脚本路径本身）。

        返回
        ----
        pstats.Stats
            cProfile 统计数据。
        """
        script = script.resolve()
        ProfileRunner._validate_script(script)
        argv = list(args or [])
        orig_argv = sys.argv[:]
        orig_path = sys.path[:]

        def action() -> None:
            sys.argv = [str(script), *argv]
            script_dir = str(script.parent)
            if script_dir not in sys.path:
                sys.path.insert(0, script_dir)
            runpy.run_path(str(script), run_name="__main__")

        try:
            return ProfileRunner._profile(action)
        finally:
            sys.argv = orig_argv
            sys.path = orig_path

    @staticmethod
    def run_module(module: str, args: list[str] | None = None) -> pstats.Stats:
        """运行并分析 Python 模块（类似 ``python -m module``）。

        参数
        ----
        module:
            模块名，如 ``json.tool``。
        args:
            传给目标模块的命令行参数。

        返回
        ----
        pstats.Stats
            cProfile 统计数据。
        """
        argv = list(args or [])
        orig_argv = sys.argv[:]

        def action() -> None:
            sys.argv = [module, *argv]
            runpy.run_module(module, run_name="__main__", alter_sys=True)

        try:
            return ProfileRunner._profile(action)
        finally:
            sys.argv = orig_argv


class ProfileLoader:
    """加载已有的 cProfile 数据文件。"""

    @staticmethod
    def load(file_path: Path) -> pstats.Stats:
        """加载 ``.prof`` / ``.pstats`` 文件。

        参数
        ----
        file_path:
            cProfile 数据文件路径。

        异常
        ----
        FcmdError
            文件不存在或内容无法识别。
        """
        file_path = file_path.resolve()
        if not file_path.is_file():
            msg = f"文件不存在: {file_path}"
            raise FcmdError(msg)
        try:
            stats = pstats.Stats(str(file_path), stream=io.StringIO())
        except (EOFError, TypeError, ValueError, AttributeError, OSError) as exc:
            msg = f"无法加载性能数据文件: {file_path}"
            raise FcmdError(msg) from exc
        if not isinstance(getattr(stats, "stats", None), dict):
            msg = f"无法加载性能数据文件: {file_path}"
            raise FcmdError(msg)
        return stats


# ============================================================================
# 分析引擎
# ============================================================================


@functools.lru_cache(maxsize=1)
def _stdlib_path() -> str | None:
    """标准库路径（缓存，normcase 便于跨平台前缀比较）。"""
    path = sysconfig.get_path("stdlib")
    return os.path.normcase(os.path.realpath(path)) if path else None


@functools.lru_cache(maxsize=1)
def _site_packages_path() -> str | None:
    """site-packages 路径（缓存）。"""
    path = sysconfig.get_path("purelib")
    return os.path.normcase(os.path.realpath(path)) if path else None


def _classify_origin(filename: str) -> str:
    """按文件路径把函数归类为 user / stdlib / third_party / builtin。"""
    if filename.startswith(("<", "{", "~")):
        return "builtin"
    try:
        real = os.path.normcase(os.path.realpath(filename))
    except (ValueError, OSError):
        return "builtin"
    stdlib = _stdlib_path()
    if stdlib and real.startswith(stdlib):
        return "stdlib"
    site = _site_packages_path()
    if site and real.startswith(site):
        return "third_party"
    lowered = real.lower()
    if "site-packages" in lowered or "dist-packages" in lowered:
        return "third_party"
    return "user"


def _format_calls(ncalls: int, primitive: int) -> str:
    """格式化调用次数：递归调用显示为 ``总次数/原始次数``。"""
    if ncalls != primitive:
        return f"{ncalls}/{primitive}"
    return str(ncalls)


def _parse_ncalls(ncalls: str) -> int:
    """把 ``100/20`` 形式的调用次数解析为整数（用于排序）。"""
    return int(ncalls.split("/", maxsplit=1)[0])


def _extract_call_data(data: object) -> tuple[int, int, float, float]:
    """兼容解析 pstats 调用关系数据为 ``(nc, cc, tt, ct)``。"""
    if isinstance(data, tuple) and len(data) >= 4:
        nc = data[0] if isinstance(data[0], int) else 0
        cc = data[1] if isinstance(data[1], int) else 0
        tt = float(data[2]) if isinstance(data[2], (int, float)) else 0.0
        ct = float(data[3]) if isinstance(data[3], (int, float)) else 0.0
        return nc, cc, tt, ct
    nc = data if isinstance(data, int) else 0
    return nc, nc, 0.0, 0.0


def _to_call_ref(key: _FuncKey, data: object) -> CallRef:
    """把 ``(文件名, 行号, 函数名)`` + 调用数据转为 :class:`CallRef`。"""
    filename, lineno, func_name = key
    nc, cc, tt, ct = _extract_call_data(data)
    return CallRef(
        filename=filename,
        lineno=lineno,
        func_name=func_name,
        ncalls=_format_calls(nc, cc),
        tottime=tt,
        cumtime=ct,
    )


def _build_function_stats(raw: _RawStats) -> dict[_FuncKey, FunctionStat]:
    """把 pstats 原始数据转换为 :class:`FunctionStat` 映射。"""
    result: dict[_FuncKey, FunctionStat] = {}
    for key, entry in raw.items():
        cc, nc, tt, ct = (int(entry[0]), int(entry[1]), float(entry[2]), float(entry[3]))
        callers = entry[4] if len(entry) > 4 and isinstance(entry[4], dict) else {}
        filename, lineno, func_name = key
        result[key] = FunctionStat(
            filename=filename,
            lineno=lineno,
            func_name=func_name,
            ncalls=_format_calls(nc, cc),
            tottime=tt,
            cumtime=ct,
            tottime_percall=tt / nc if nc > 0 else 0.0,
            cumtime_percall=ct / cc if cc > 0 else 0.0,
            origin=_classify_origin(filename),
            callers=[_to_call_ref(k, v) for k, v in callers.items()],
        )
    return result


def _sort_functions(funcs: list[FunctionStat], sort_by: str) -> None:
    """按指定字段就地降序排序。"""
    if sort_by == "cumtime":
        funcs.sort(key=lambda f: f.cumtime, reverse=True)
    elif sort_by == "calls":
        funcs.sort(key=lambda f: _parse_ncalls(f.ncalls), reverse=True)
    else:
        funcs.sort(key=lambda f: f.tottime, reverse=True)


def _build_time_distribution(funcs: list[FunctionStat], total_time: float, max_items: int = 15) -> list[TimeSlice]:
    """构建概览页的时间分布（Top-N + 其他）。"""
    if total_time <= 0:
        return []
    slices: list[TimeSlice] = []
    shown = 0.0
    for func in sorted(funcs, key=lambda f: f.tottime, reverse=True)[:max_items]:
        if func.tottime <= 0:
            continue
        slices.append(
            TimeSlice(
                display_name=_truncate(func.display_name, 46),
                tottime=func.tottime,
                cumulative=func.cumtime,
                percentage=func.tottime / total_time * 100,
                origin=func.origin,
            )
        )
        shown += func.tottime
    other = total_time - shown
    if other > 0 and slices:
        slices.append(
            TimeSlice(
                display_name="其他函数",
                tottime=other,
                cumulative=0.0,
                percentage=other / total_time * 100,
                origin="builtin",
            )
        )
    return slices


def _truncate(text: str, max_len: int) -> str:
    """按长度截断并追加省略号。"""
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


class _FlameBuilder:
    """火焰图构建器：沿调用关系递归展开，带节点预算与环路保护。"""

    def __init__(
        self,
        raw: _RawStats,
        callees: _Callees,
        max_nodes: int = 20000,
        max_depth: int = 40,
    ) -> None:
        self.raw = raw
        self.callees = callees
        self.remaining = max_nodes
        self.max_depth = max_depth

    def build(self, key: _FuncKey, depth: int, path: frozenset[_FuncKey]) -> FlameNode | None:
        """递归构建单个节点及其子节点。"""
        if key in path or depth > self.max_depth or self.remaining <= 0:
            return None
        entry = self.raw.get(key)
        if entry is None:
            return None
        self.remaining -= 1
        filename, lineno, func_name = key
        node = FlameNode(
            name=func_name,
            filename=filename,
            lineno=lineno,
            value=float(entry[3]),
            self_time=float(entry[2]),
            origin=_classify_origin(filename),
        )
        for callee_key, data in self.callees.get(key, {}).items():
            _nc, _cc, c_ct, _c_tt = _extract_call_data(data)
            child = self._build_child(callee_key, depth + 1, path | {key}, c_ct)
            if child is not None and child.value > 0:
                node.children.append(child)
        node.children.sort(key=lambda c: c.value, reverse=True)
        return node

    def _build_child(
        self,
        key: _FuncKey,
        depth: int,
        path: frozenset[_FuncKey],
        value: float,
    ) -> FlameNode | None:
        """构建子节点，并用父节点视角的累计耗时覆盖节点宽度。"""
        child = self.build(key, depth, path)
        if child is not None:
            child.value = value
        return child


def _build_flame_graph(raw: _RawStats, callees: _Callees, total_time: float) -> list[FlameNode]:
    """构建火焰图根节点列表（最多 10 个根）。"""
    if not raw:
        return []
    called = {key for targets in callees.values() for key in targets}
    roots = set(raw) - called
    if not roots:
        roots = set(sorted(raw, key=lambda k: float(raw[k][3]), reverse=True)[:3])
    builder = _FlameBuilder(raw, callees)
    nodes = [node for key in roots if (node := builder.build(key, 0, frozenset())) is not None and node.value > 0]
    if total_time > 0:
        threshold = total_time * 0.001
        nodes = [node for node in nodes if node.value >= threshold]
    nodes.sort(key=lambda n: n.value, reverse=True)
    return nodes[:10]


def _build_module_stats(funcs: list[FunctionStat]) -> list[ModuleStat]:
    """按文件聚合函数统计。"""
    groups: dict[str, list[FunctionStat]] = defaultdict(list)
    for func in funcs:
        groups[func.filename].append(func)
    modules: list[ModuleStat] = []
    for filename, items in groups.items():
        ordered = sorted(items, key=lambda f: f.tottime, reverse=True)
        modules.append(
            ModuleStat(
                filename=filename,
                display_name=_short_filename(filename),
                total_tottime=sum(f.tottime for f in items),
                total_cumtime=max((f.cumtime for f in items), default=0.0),
                call_count=sum(_parse_ncalls(f.ncalls) for f in items),
                func_count=len(items),
                origin=items[0].origin,
                functions=ordered[:10],
            )
        )
    modules.sort(key=lambda m: m.total_tottime, reverse=True)
    return modules


def analyze_profile(
    stats: pstats.Stats,
    *,
    top_n: int = 50,
    sort_by: str = "tottime",
    target_name: str = "",
) -> CodeProfileReport:
    """把 pstats 数据转换为 :class:`CodeProfileReport`。

    参数
    ----
    stats:
        cProfile 统计数据。
    top_n:
        热点排行数量。
    sort_by:
        函数列表排序字段（``tottime`` / ``cumtime`` / ``calls``）。
    target_name:
        分析目标名称。
    """
    raw = cast("_RawStats", getattr(stats, "stats", {}))
    stats.calc_callees()
    callees = cast("_Callees", getattr(stats, "all_callees", {}))

    total_calls = 0
    primitive_calls = 0
    total_time = 0.0
    for entry in raw.values():
        total_calls += int(entry[1])
        primitive_calls += int(entry[0])
        total_time += float(entry[2])

    func_map = _build_function_stats(raw)
    for key, func in func_map.items():
        func.callees = [_to_call_ref(k, v) for k, v in callees.get(key, {}).items()]
        func.callees.sort(key=lambda c: c.cumtime, reverse=True)
        func.callers.sort(key=lambda c: c.cumtime, reverse=True)

    functions = list(func_map.values())
    _sort_functions(functions, sort_by)
    hotspots = sorted(functions, key=lambda f: f.tottime, reverse=True)[: max(top_n, 0)]

    return CodeProfileReport(
        target_name=target_name,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_calls=total_calls,
        primitive_calls=primitive_calls,
        total_time=total_time,
        unique_functions=len(functions),
        function_stats=functions,
        hotspots=hotspots,
        time_distribution=_build_time_distribution(functions, total_time),
        flame_roots=_build_flame_graph(raw, callees, total_time),
        module_stats=_build_module_stats(functions),
        sort_by=sort_by,
    )


# ============================================================================
# CLI
# ============================================================================


def _write_report(report: CodeProfileReport, output: Path, no_open: bool) -> Path:
    """写入 HTML 报告并按需打开浏览器。"""
    out_path = output.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_html(report), encoding="utf-8")
    print(f"HTML 报告已生成: {out_path}")
    if not no_open:
        import webbrowser

        try:
            webbrowser.open(out_path.as_uri())
        except Exception as exc:  # 打开浏览器失败不影响报告本身
            get_console().print(f"[yellow]警告:[/yellow] 无法打开浏览器: {exc}")
    return out_path


def _print_summary(report: CodeProfileReport) -> None:
    """在终端打印关键指标与 Top-3 热点。"""
    print(
        f"总耗时 {_format_time(report.total_time)} · 调用 {_format_number(report.total_calls)} 次 · "
        f"函数 {report.unique_functions} 个 · 模块 {len(report.module_stats)} 个"
    )
    for index, func in enumerate(report.hotspots[:3], start=1):
        share = func.tottime / report.total_time * 100 if report.total_time else 0.0
        print(f"  #{index} {func.display_name}  自身 {_format_time(func.tottime)} ({share:.1f}%)")


def _analyze_and_report(  # noqa: PLR0913, PLR0917
    stats: pstats.Stats,
    target_name: str,
    output: Path,
    top: int,
    sort_by: str,
    no_open: bool,
) -> None:
    """分析统计数据并输出报告。"""
    report = analyze_profile(stats, top_n=top, sort_by=sort_by, target_name=target_name)
    _print_summary(report)
    _write_report(report, output, no_open)


@fcmd.tool(
    "pyprof",
    subcommand="run",
    help="运行并分析 Python 脚本，生成 HTML 性能报告",
    description="非侵入式 Python 代码性能分析（cProfile 采集 + HTML 报告）",
)
def run_profile(
    target: list[str],
    output: Path = Path("pyprofile_report.html"),
    top: int = 50,
    sort_by: Literal["tottime", "cumtime", "calls"] = "tottime",
    no_open: bool = False,
) -> None:
    """运行目标脚本并生成性能报告。

    参数
    ----
    target:
        ``脚本路径 [脚本参数...]``；第一个元素是脚本，其余透传给目标脚本。
    output:
        HTML 报告输出路径（默认当前目录 ``pyprofile_report.html``）。
    top:
        热点排行显示的条目数。
    sort_by:
        函数列表排序字段（tottime/cumtime/calls）。
    no_open:
        生成后不自动打开浏览器。

    示例::

        fcmd pyprof run app.py
        fcmd pyprof run -- app.py --level 4
    """
    script = Path(target[0])
    stats = ProfileRunner.run_script(script, target[1:])
    _analyze_and_report(stats, str(script), output, top, sort_by, no_open)


@fcmd.tool(
    "pyprof",
    subcommand="module",
    help="以 python -m 方式运行模块并分析，生成 HTML 性能报告",
    description="非侵入式 Python 代码性能分析（cProfile 采集 + HTML 报告）",
)
def profile_module(
    target: list[str],
    output: Path = Path("pyprofile_report.html"),
    top: int = 50,
    sort_by: Literal["tottime", "cumtime", "calls"] = "tottime",
    no_open: bool = False,
) -> None:
    """以模块方式运行并生成性能报告。

    参数
    ----
    target:
        ``模块名 [模块参数...]``；第一个元素是模块名（如 ``json.tool``），其余透传。
    output:
        HTML 报告输出路径（默认当前目录 ``pyprofile_report.html``）。
    top:
        热点排行显示的条目数。
    sort_by:
        函数列表排序字段（tottime/cumtime/calls）。
    no_open:
        生成后不自动打开浏览器。

    示例::

        fcmd pyprof module json.tool data.json
        fcmd pyprof module -- mypkg.cli --verbose
    """
    module = target[0]
    stats = ProfileRunner.run_module(module, target[1:])
    _analyze_and_report(stats, f"-m {module}", output, top, sort_by, no_open)


@fcmd.tool(
    "pyprof",
    subcommand="load",
    help="分析已有 .prof/.pstats 文件，生成 HTML 性能报告",
    description="非侵入式 Python 代码性能分析（cProfile 采集 + HTML 报告）",
)
def load_profile(
    profile_file: Path,
    output: Path = Path("pyprofile_report.html"),
    top: int = 50,
    sort_by: Literal["tottime", "cumtime", "calls"] = "tottime",
    no_open: bool = False,
) -> None:
    """加载已有 cProfile 数据并生成性能报告。

    参数
    ----
    profile_file:
        ``.prof`` / ``.pstats`` 文件路径。
    output:
        HTML 报告输出路径（默认当前目录 ``pyprofile_report.html``）。
    top:
        热点排行显示的条目数。
    sort_by:
        函数列表排序字段（tottime/cumtime/calls）。
    no_open:
        生成后不自动打开浏览器。
    """
    stats = ProfileLoader.load(profile_file)
    _analyze_and_report(stats, str(profile_file), output, top, sort_by, no_open)


@fcmd.main("pyprof")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":
    main()
