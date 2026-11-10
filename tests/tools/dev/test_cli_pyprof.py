"""pyprof 工具测试。

覆盖 ``fcmd.tools.dev.pyprof`` 的完整链路：
- 工具注册与子命令
- cProfile 采集（脚本 / 模块）与离线加载
- 分析引擎（热点、时间分布、火焰图、模块聚合）
- HTML 报告渲染与转义
- CLI 调度与端到端执行
"""

from __future__ import annotations

import cProfile
import io
import json
import pstats
from collections.abc import Callable
from pathlib import Path

import pytest

import fcmd as fx
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.errors import FcmdError
from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.dev import pyprof
from fcmd.tools.dev._pyprof_html import (
    _esc,
    _flame_json,
    _flame_to_dict,
    _origin_badge,
    _render_call_table,
    _render_cards,
    _render_distribution,
    _render_relation_panels,
)
from fcmd.tools.dev.pyprof import (
    CallRef,
    CodeProfileReport,
    FlameNode,
    FunctionStat,
    ModuleStat,
    ProfileLoader,
    ProfileRunner,
    TimeSlice,
    _build_flame_graph,
    _build_module_stats,
    _build_time_distribution,
    _classify_origin,
    _extract_call_data,
    _format_calls,
    _format_number,
    _format_time,
    _parse_ncalls,
    _short_filename,
    _sort_functions,
    _truncate,
    _write_report,
    analyze_profile,
    load_profile,
    profile_module,
    render_html,
    run_profile,
)
from tests.conftest import CmdResult

ensure_tools_discovered()


# ============================================================================ #
# 采集辅助
# ============================================================================ #


def _fib(n: int) -> int:
    """测试用递归函数。"""
    return n if n < 2 else _fib(n - 1) + _fib(n - 2)


def _workload() -> int:
    """测试用工作负载。"""
    return sum(_fib(i) for i in range(10))


def _stats_for(func: Callable[[], object]) -> pstats.Stats:
    """对函数执行 cProfile 并返回统计数据。"""
    profile = cProfile.Profile()
    profile.enable()
    func()
    profile.disable()
    return pstats.Stats(profile, stream=io.StringIO())


@pytest.fixture(scope="module")
def report() -> CodeProfileReport:
    """基于真实采集数据的分析报告（模块级复用）。"""
    return analyze_profile(_stats_for(_workload), top_n=5, target_name="workload")


def _make_report(**overrides: object) -> CodeProfileReport:
    """构造最小可渲染报告，便于测试边界分支。"""
    defaults: dict[str, object] = {
        "target_name": "demo.py",
        "generated_at": "2026-01-01 00:00:00",
        "total_calls": 10,
        "primitive_calls": 8,
        "total_time": 1.0,
        "unique_functions": 1,
        "function_stats": [],
        "hotspots": [],
        "time_distribution": [],
        "flame_roots": [],
        "module_stats": [],
        "sort_by": "tottime",
    }
    defaults.update(overrides)
    return CodeProfileReport(**defaults)  # type: ignore[arg-type]


# ============================================================================ #
# 注册验证
# ============================================================================ #


class TestRegistration:
    """工具注册与子命令验证。"""

    def test_tool_registered(self) -> None:
        """pyprof 应注册到工具注册表。"""
        assert "pyprof" in _TOOL_REGISTRY

    def test_subcommands(self) -> None:
        """应提供 run / module / load 三个子命令。"""
        subs = fx.list_subcommands("pyprof")
        assert {"run", "module", "load"} <= set(subs)


# ============================================================================ #
# 格式化与分类辅助
# ============================================================================ #


class TestHelpers:
    """格式化 / 分类 / 解析辅助函数。"""

    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0.0, "0 s"),
            (-1.0, "0 s"),
            (1.5, "1.500 s"),
            (0.0123, "12.300 ms"),
            (1.5e-6, "1.500 µs"),
            (1e-9, "1 ns"),
        ],
    )
    def test_format_time(self, seconds: float, expected: str) -> None:
        """时间格式化应覆盖 s/ms/µs/ns 与零值。"""
        assert _format_time(seconds) == expected

    def test_format_calls(self) -> None:
        """调用次数在递归时显示 ``总/原始``。"""
        assert _format_calls(3, 3) == "3"
        assert _format_calls(9, 3) == "9/3"

    def test_parse_ncalls(self) -> None:
        """排序用调用次数应取斜杠前整数。"""
        assert _parse_ncalls("100/20") == 100
        assert _parse_ncalls("7") == 7

    def test_format_number(self) -> None:
        """整数应带千分位。"""
        assert _format_number(1234567) == "1,234,567"

    def test_short_filename(self) -> None:
        """内置伪文件保留原样，普通路径取 basename。"""
        assert _short_filename("<string>") == "<string>"
        assert _short_filename(r"C:\proj\demo.py") == "demo.py"

    def test_truncate(self) -> None:
        """超长文本应截断并追加省略号。"""
        assert _truncate("short", 10) == "short"
        assert _truncate("abcdefghijklmn", 10) == "abcdefg..."

    def test_escape(self) -> None:
        """HTML 转义应处理特殊字符。"""
        assert _esc("<a & b>") == "&lt;a &amp; b&gt;"

    def test_origin_badge(self) -> None:
        """来源徽章应带语义化 CSS 类。"""
        assert "origin-user" in _origin_badge("user")
        assert "用户代码" in _origin_badge("user")

    def test_extract_call_data(self) -> None:
        """调用数据兼容四元组与单独计数。"""
        assert _extract_call_data((1, 2, 0.5, 1.5)) == (1, 2, 0.5, 1.5)
        assert _extract_call_data(4) == (4, 4, 0.0, 0.0)
        assert _extract_call_data("bad") == (0, 0, 0.0, 0.0)
        assert _extract_call_data((1, "x", "y", None)) == (1, 0, 0.0, 0.0)

    def test_classify_origin(self) -> None:
        """来源分类应覆盖内置 / 标准库 / 第三方 / 用户代码。"""
        assert _classify_origin("<frozen importlib._bootstrap>") == "builtin"
        assert _classify_origin("~") == "builtin"
        assert _classify_origin(str(Path(str(pyprof.__file__)).resolve())) == "user"
        assert _classify_origin(r"C:\venv\Lib\site-packages\demo\mod.py") == "third_party"

    def test_classify_origin_stdlib(self) -> None:
        """标准库文件应归类为 stdlib（通过 sysconfig 路径前缀）。"""
        import os

        stdlib_file = os.__file__
        if stdlib_file and "site-packages" not in stdlib_file:
            assert _classify_origin(stdlib_file) == "stdlib"

    def test_sort_functions(self) -> None:
        """三种排序字段均应降序。"""
        funcs = [
            FunctionStat("f.py", 1, "a", "2", 0.5, 0.2, 0.25, 0.1),
            FunctionStat("f.py", 2, "b", "10", 0.1, 0.9, 0.01, 0.09),
        ]
        _sort_functions(funcs, "cumtime")
        assert [f.func_name for f in funcs] == ["b", "a"]
        _sort_functions(funcs, "calls")
        assert [f.func_name for f in funcs] == ["b", "a"]
        _sort_functions(funcs, "tottime")
        assert [f.func_name for f in funcs] == ["a", "b"]

    def test_function_names(self) -> None:
        """函数标识名应包含文件、行号与函数名。"""
        func = FunctionStat("a.py", 3, "f", "1", 0.1, 0.2, 0.1, 0.2)
        assert func.full_name == "a.py:3(f)"
        assert func.display_name == "a.py:3 f"

    def test_classify_origin_realpath_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """realpath 失败时降级为内置分类。"""

        def boom(path: str) -> str:
            raise ValueError(path)

        monkeypatch.setattr(pyprof.os.path, "realpath", boom)
        assert _classify_origin("broken/path.py") == "builtin"


# ============================================================================ #
# 采集与加载
# ============================================================================ #


class TestProfileRunner:
    """脚本 / 模块采集与校验。"""

    def test_validate_missing(self, tmp_path: Path) -> None:
        """不存在的脚本应报错。"""
        with pytest.raises(FcmdError, match="不存在"):
            ProfileRunner._validate_script(tmp_path / "missing.py")

    def test_validate_directory(self, tmp_path: Path) -> None:
        """目录不是可分析脚本。"""
        with pytest.raises(FcmdError, match="不是文件"):
            ProfileRunner._validate_script(tmp_path)

    def test_validate_non_python(self, tmp_path: Path) -> None:
        """非 .py 文件应报错。"""
        target = tmp_path / "demo.txt"
        target.write_text("print(1)", encoding="utf-8")
        with pytest.raises(FcmdError, match="扩展名"):
            ProfileRunner._validate_script(target)

    def test_validate_syntax_error(self, tmp_path: Path) -> None:
        """语法错误应给出带行号的提示。"""
        target = tmp_path / "broken.py"
        target.write_text("def oops(:\n", encoding="utf-8")
        with pytest.raises(FcmdError, match="语法错误"):
            ProfileRunner._validate_script(target)

    def test_validate_encoding_error(self, tmp_path: Path) -> None:
        """非 UTF-8 文件应报编码错误。"""
        target = tmp_path / "latin.py"
        target.write_bytes(b"print('\xff\xfe')\n")
        with pytest.raises(FcmdError, match="编码"):
            ProfileRunner._validate_script(target)

    def test_validate_permission_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """无读取权限时应报权限错误。"""
        target = tmp_path / "app.py"
        target.write_text("x = 1\n", encoding="utf-8")

        def deny(self: Path, encoding: str | None = None) -> str:
            raise PermissionError(str(self))

        monkeypatch.setattr(Path, "read_text", deny)
        with pytest.raises(FcmdError, match="权限"):
            ProfileRunner._validate_script(target)

    def test_run_script_collects_and_forwards_args(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """脚本应采集到数据且参数被正确转发。"""
        script = tmp_path / "app.py"
        script.write_text("import sys\nprint('ARGS', sys.argv[1:])\n_ = sum(range(1000))\n", encoding="utf-8")
        stats = ProfileRunner.run_script(script, ["alpha", "--level=4"])
        assert len(getattr(stats, "stats", {})) > 0
        assert "ARGS ['alpha', '--level=4']" in capsys.readouterr().out

    def test_run_script_restores_sys(self, tmp_path: Path) -> None:
        """执行后 sys.argv / sys.path 应恢复。"""
        import sys

        script = tmp_path / "noop.py"
        script.write_text("x = 1\n", encoding="utf-8")
        argv_before = sys.argv[:]
        path_before = sys.path[:]
        ProfileRunner.run_script(script)
        assert sys.argv == argv_before
        assert sys.path == path_before

    def test_run_script_handles_system_exit(self, tmp_path: Path) -> None:
        """SystemExit 视为正常退出，不抛异常。"""
        script = tmp_path / "exit.py"
        script.write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
        stats = ProfileRunner.run_script(script)
        assert isinstance(stats, pstats.Stats)

    def test_run_script_handles_exception(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """脚本异常应转为警告并继续产出统计。"""
        script = tmp_path / "boom.py"
        script.write_text("raise RuntimeError('boom')\n", encoding="utf-8")
        stats = ProfileRunner.run_script(script)
        assert isinstance(stats, pstats.Stats)
        assert "警告" in capsys.readouterr().out

    def test_run_script_with_sibling_import(self, tmp_path: Path) -> None:
        """脚本目录应加入 sys.path，可导入同级模块。"""
        (tmp_path / "helper.py").write_text("VALUE = 42\n", encoding="utf-8")
        script = tmp_path / "main.py"
        script.write_text("import helper\nassert helper.VALUE == 42\n", encoding="utf-8")
        stats = ProfileRunner.run_script(script)
        assert len(getattr(stats, "stats", {})) > 0

    def test_run_module(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """模块方式运行应输出参数并采集数据。"""
        module = tmp_path / "pydemo_mod.py"
        module.write_text("import sys\nprint('MODULE', sys.argv[1:])\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        stats = ProfileRunner.run_module("pydemo_mod", ["beta"])
        assert len(getattr(stats, "stats", {})) > 0
        assert "MODULE ['beta']" in capsys.readouterr().out

    def test_run_module_missing_warns(self, capsys: pytest.CaptureFixture[str]) -> None:
        """不存在的模块应转为警告并保留统计对象。"""
        stats = ProfileRunner.run_module("definitely_missing_pyprof_module")
        assert isinstance(stats, pstats.Stats)
        assert "警告" in capsys.readouterr().out


class TestProfileLoader:
    """离线 .prof 文件加载。"""

    def _dump(self, tmp_path: Path) -> Path:
        profile_file = tmp_path / "demo.prof"
        cProfile.runctx("sum(range(2000))", {}, {}, str(profile_file))
        return profile_file

    def test_load_ok(self, tmp_path: Path) -> None:
        """合法数据文件应能加载。"""
        stats = ProfileLoader.load(self._dump(tmp_path))
        assert len(getattr(stats, "stats", {})) > 0

    def test_load_missing(self, tmp_path: Path) -> None:
        """缺失文件应报错。"""
        with pytest.raises(FcmdError, match="不存在"):
            ProfileLoader.load(tmp_path / "missing.prof")

    def test_load_invalid(self, tmp_path: Path) -> None:
        """非法内容应报错。"""
        target = tmp_path / "broken.prof"
        target.write_text("not a profile", encoding="utf-8")
        with pytest.raises(FcmdError, match="无法加载"):
            ProfileLoader.load(target)

    def test_load_without_stats(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """加载结果缺少 stats 字段时应报错。"""
        target = tmp_path / "empty.prof"
        target.write_text("placeholder", encoding="utf-8")

        class FakeStats:
            def __init__(self, *args: object, **kwargs: object) -> None:
                self.stats: dict[str, object] | None = None

        monkeypatch.setattr(pyprof.pstats, "Stats", FakeStats)
        with pytest.raises(FcmdError, match="无法加载"):
            ProfileLoader.load(target)


# ============================================================================ #
# 分析引擎
# ============================================================================ #


class TestAnalyzer:
    """分析引擎各维度输出。"""

    def test_totals(self, report: CodeProfileReport) -> None:
        """总调用与耗时指标应自洽。"""
        assert report.total_time > 0
        assert report.total_calls > 0
        assert report.primitive_calls > 0
        assert report.unique_functions == len(report.function_stats)

    def test_hotspots_sorted_and_limited(self, report: CodeProfileReport) -> None:
        """热点按自身耗时降序且不超过 top_n。"""
        times = [f.tottime for f in report.hotspots]
        assert times == sorted(times, reverse=True)
        assert len(report.hotspots) <= 5

    def test_origins_and_relations(self, report: CodeProfileReport) -> None:
        """函数应带来源分类，调用关系可解析。"""
        assert all(f.origin for f in report.function_stats)
        assert any(f.callers for f in report.function_stats)
        assert any(f.callees for f in report.function_stats)
        for ref in report.hotspots[0].callees:
            assert isinstance(ref, CallRef)

    def test_time_distribution(self, report: CodeProfileReport) -> None:
        """时间分布占比之和应约等于 100%。"""
        assert report.time_distribution
        total = sum(item.percentage for item in report.time_distribution)
        assert total == pytest.approx(100.0, abs=0.01)
        assert all(isinstance(item, TimeSlice) for item in report.time_distribution)

    def test_flame_graph(self, report: CodeProfileReport) -> None:
        """火焰图应包含根节点与子节点，且值为正。"""
        assert report.flame_roots
        assert all(node.value > 0 for node in report.flame_roots)
        roots = sorted(report.flame_roots, key=lambda n: n.value, reverse=True)
        assert roots == report.flame_roots

        def walk(node: FlameNode) -> int:
            return 1 + sum(walk(child) for child in node.children)

        assert sum(walk(root) for root in report.flame_roots) > 1

    def test_module_stats(self, report: CodeProfileReport) -> None:
        """模块聚合应按自身耗时降序且字段自洽。"""
        assert report.module_stats
        totals = [m.total_tottime for m in report.module_stats]
        assert totals == sorted(totals, reverse=True)
        module = report.module_stats[0]
        assert isinstance(module, ModuleStat)
        assert module.func_count >= 1
        assert len(module.functions) <= 10
        assert module.display_name

    def test_sort_by_cumtime(self) -> None:
        """按累计耗时排序生效。"""
        result = analyze_profile(_stats_for(_workload), sort_by="cumtime")
        values = [f.cumtime for f in result.function_stats]
        assert values == sorted(values, reverse=True)

    def test_sort_by_calls(self) -> None:
        """按调用次数排序生效。"""
        result = analyze_profile(_stats_for(_workload), sort_by="calls")
        values = [_parse_ncalls(f.ncalls) for f in result.function_stats]
        assert values == sorted(values, reverse=True)

    def test_empty_helpers(self) -> None:
        """空输入的辅助函数应返回空结构。"""
        assert _build_flame_graph({}, {}, 0.0) == []
        assert _build_time_distribution([], 0.0) == []
        assert _build_module_stats([]) == []
        zero = [FunctionStat("f.py", 1, "a", "1", 0.0, 0.0, 0.0, 0.0)]
        assert _build_time_distribution(zero, 1.0) == []

    def test_flame_cycle_fallback(self) -> None:
        """只有自调用时回退到根节点且环路被截断。"""
        key = ("/tmp/a.py", 1, "loop")
        raw = {key: (1, 1, 0.5, 1.0, {})}
        callees = {key: {key: (1, 1, 0.0, 0.5)}}
        roots = _build_flame_graph(raw, callees, 1.0)
        assert len(roots) == 1
        assert roots[0].name == "loop"
        assert roots[0].children == []

    def test_flame_missing_callee(self) -> None:
        """被调用者不在统计数据中时应跳过。"""
        key = ("/tmp/a.py", 1, "caller")
        missing = ("/tmp/b.py", 2, "missing")
        raw = {key: (1, 1, 0.5, 1.0, {})}
        callees = {key: {missing: (1, 1, 0.0, 0.5)}}
        roots = _build_flame_graph(raw, callees, 1.0)
        assert roots[0].children == []


# ============================================================================ #
# HTML 渲染
# ============================================================================ #


class TestRenderer:
    """HTML 报告渲染。"""

    def test_render_html_contains_sections(self, report: CodeProfileReport) -> None:
        """报告应包含标签页、指标、火焰图数据与模块表。"""
        html_text = render_html(report)
        for marker in (
            "pyprof 性能分析报告",
            'id="tabs"',
            "热点排行",
            "火焰图",
            "模块聚合",
            "var FLAME = ",
            report.generated_at,
        ):
            assert marker in html_text
        assert "workload" in html_text

    def test_render_html_escapes(self) -> None:
        """函数名与文件名中的 HTML 特殊字符应被转义。"""
        func = FunctionStat("<script>.py", 1, "<img>", "3", 1.0, 2.0, 0.3, 0.6, origin="user")
        html_text = render_html(_make_report(hotspots=[func], total_time=2.0))
        assert "&lt;img&gt;" in html_text
        assert "<img>" not in html_text

    def test_render_empty_report(self) -> None:
        """空报告也应渲染出占位内容。"""
        html_text = render_html(_make_report(total_time=0.0))
        assert "无函数数据" in html_text
        assert "无模块数据" in html_text
        assert "无耗时数据" in html_text

    def test_render_cards(self, report: CodeProfileReport) -> None:
        """指标卡片应包含关键数字。"""
        cards = _render_cards(report)
        assert "总耗时" in cards
        assert "总调用次数" in cards

    def test_render_distribution_empty(self) -> None:
        """无分布数据时显示空状态。"""
        assert "无耗时数据" in _render_distribution(_make_report())

    def test_render_call_table_empty(self) -> None:
        """无调用关系时显示占位文案。"""
        assert "无调用者" in _render_call_table([], "无调用者（根函数）")

    def test_render_relation_panels(self) -> None:
        """调用关系面板应为每个热点生成 template。"""
        func = FunctionStat("a.py", 1, "f", "1", 1.0, 1.0, 1.0, 1.0)
        panels = _render_relation_panels(_make_report(hotspots=[func]))
        assert 'id="rel-1"' in panels
        assert "无调用者" in panels
        assert "无被调用者" in panels

    def test_flame_json(self) -> None:
        """火焰图 JSON 应可解析且转义 script 闭合标签。"""
        node = FlameNode("f", "a.py", 1, 1.0, 0.5, "user", [FlameNode("g", "a.py", 2, 0.5, 0.5, "user")])
        payload = _flame_json([node])
        assert "</script>" not in payload
        parsed = json.loads(payload)
        assert parsed[0]["n"] == "f"
        children = _flame_to_dict(node)["c"]
        assert isinstance(children, list)
        assert children[0]["n"] == "g"

    def test_write_report_opens_browser(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认应调用浏览器打开报告。"""
        opened: list[str] = []
        monkeypatch.setattr("webbrowser.open", opened.append)
        out = _write_report(_make_report(), tmp_path / "r.html", no_open=False)
        assert out.is_file()
        assert opened and opened[0].startswith("file:")

    def test_write_report_browser_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """浏览器打开失败只警告，不影响报告生成。"""

        def boom(uri: str) -> bool:
            raise RuntimeError("no browser")

        monkeypatch.setattr("webbrowser.open", boom)
        out = _write_report(_make_report(), tmp_path / "r.html", no_open=False)
        assert out.is_file()
        assert "警告" in capsys.readouterr().out


# ============================================================================ #
# CLI
# ============================================================================ #


class TestCli:
    """CLI 函数级测试。"""

    def test_run_script_writes_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """脚本模式应生成报告并打印摘要。"""
        monkeypatch.chdir(tmp_path)
        script = tmp_path / "app.py"
        script.write_text("sum(range(5000))\n", encoding="utf-8")
        run_profile(target=[str(script), "x"], output=Path("report.html"), no_open=True, top=3)
        out = capsys.readouterr().out
        assert "HTML 报告已生成" in out
        assert (tmp_path / "report.html").is_file()

    def test_run_module_writes_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """模块模式应生成报告。"""
        monkeypatch.chdir(tmp_path)
        module = tmp_path / "pydemo_cli_mod.py"
        module.write_text("value = sum(range(100))\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(tmp_path))
        profile_module(target=["pydemo_cli_mod"], output=Path("mod.html"), no_open=True)
        assert (tmp_path / "mod.html").is_file()
        assert "HTML 报告已生成" in capsys.readouterr().out

    def test_load_writes_report(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """离线模式应生成报告。"""
        monkeypatch.chdir(tmp_path)
        profile_file = tmp_path / "demo.prof"
        cProfile.runctx("sum(range(1000))", {}, {}, str(profile_file))
        load_profile(profile_file, output=Path("loaded.html"), no_open=True)
        assert (tmp_path / "loaded.html").is_file()
        assert "HTML 报告已生成" in capsys.readouterr().out


class TestCliE2E:
    """子进程端到端测试。"""

    def test_run_script(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """``fcmd pyprof run`` 应产出报告文件。"""
        script = tmp_path / "app.py"
        script.write_text("_ = sum(range(2000))\n", encoding="utf-8")
        result = fcmd("pyprof", "run", "app.py", "--no-open", "--output", "report.html")
        assert "HTML 报告已生成" in result.stdout
        assert (tmp_path / "report.html").is_file()

    def test_run_after_double_dash(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """``--`` 之后的参数应传给目标脚本。"""
        script = tmp_path / "app.py"
        script.write_text("import sys\nprint('PASSTHROUGH', sys.argv[1:])\n", encoding="utf-8")
        result = fcmd("pyprof", "run", "--no-open", "--", "app.py", "--level=4")
        assert "PASSTHROUGH ['--level=4']" in result.stdout

    def test_module(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """``fcmd pyprof module`` 应能分析模块。"""
        (tmp_path / "pydemo_e2e_mod.py").write_text("value = sum(range(300))\n", encoding="utf-8")
        result = fcmd("pyprof", "module", "pydemo_e2e_mod", "--no-open", "--output", "mod.html")
        assert "HTML 报告已生成" in result.stdout
        assert (tmp_path / "mod.html").is_file()

    def test_load(self, fcmd: Callable[..., CmdResult], tmp_path: Path) -> None:
        """``fcmd pyprof load`` 应能分析已有 .prof。"""
        cProfile.runctx("sum(range(500))", {}, {}, str(tmp_path / "demo.prof"))
        result = fcmd("pyprof", "load", "demo.prof", "--no-open", "--output", "loaded.html")
        assert "HTML 报告已生成" in result.stdout
        assert (tmp_path / "loaded.html").is_file()

    def test_missing_target(self, fcmd: Callable[..., CmdResult]) -> None:
        """缺少目标时应以非零码退出。"""
        result = fcmd("pyprof", "run", "--no-open", expect=1)
        assert "required" in result.stderr
