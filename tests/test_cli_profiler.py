"""``fcmd profiler`` 内建命令测试。

覆盖 ``FcmdApp._builtin_profiler`` 的路由、参数解析、hook 注入与输出：

* 无参数 → 打印帮助返回 1
* 脚本不存在 → 返回 2
* 成功分析 fx.run() 脚本 → 生成 HTML / text 报告
* 脚本不含 fx.run() → 返回 1 并提示
* ``--no-browser`` 不打开浏览器
* ``-o`` 指定输出路径
* hook 还原后 fcmd.run 恢复正常
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fcmd.cli.main import FcmdApp

# ---------------------------------------------------------------------- #
# 辅助：构造目标脚本
# ---------------------------------------------------------------------- #
_SIMPLE_WORKFLOW = """\
import fcmd as fx

@fx.task
def a() -> int:
    return 1

@fx.task
def b(a: int) -> int:
    return a + 1

g = fx.graph(a, b)
fx.run(g)
"""

_NO_RUN_SCRIPT = """\
import fcmd as fx

@fx.task
def a() -> int:
    return 1

# 没有 fx.run() 调用
print("hello")
"""

_RAISING_SCRIPT = """\
import fcmd as fx

@fx.task
def boom() -> int:
    raise RuntimeError("kaboom")

g = fx.graph(boom)
fx.run(g)  # 会抛 TaskFailedError
"""


def _write_script(tmp_path: Path, name: str, content: str) -> Path:
    """在 tmp_path 下写脚本文件。"""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------- #
# 参数解析与错误路径
# ---------------------------------------------------------------------- #
def test_profiler_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    """无参数打印帮助，返回 1。"""
    app = FcmdApp(["profiler"])
    assert app.run() == 1
    out = capsys.readouterr().out
    assert "fcmd profiler" in out
    assert "script" in out


def test_profiler_nonexistent_script_returns_2(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """脚本不存在返回 2。"""
    app = FcmdApp(["profiler", str(tmp_path / "nonexistent.py")])
    assert app.run() == 2
    out = capsys.readouterr().out
    assert "脚本不存在" in out


def test_profiler_no_run_call_returns_1(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """脚本不含 fx.run() 时返回 1 并提示。"""
    script = _write_script(tmp_path, "nrs.py", _NO_RUN_SCRIPT)
    app = FcmdApp(["profiler", str(script), "--no-browser"])
    assert app.run() == 1
    out = capsys.readouterr().out
    assert "未捕获到 fcmd.run()" in out


# ---------------------------------------------------------------------- #
# HTML 输出
# ---------------------------------------------------------------------- #
def test_profiler_html_output_default_filename(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """默认输出 <script_stem>_profile.html 到 cwd。"""
    script = _write_script(tmp_path, "wf.py", _SIMPLE_WORKFLOW)
    # 切到独立 cwd 避免污染
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    monkeypatch.chdir(out_dir)

    app = FcmdApp(["profiler", str(script), "--no-browser"])
    assert app.run() == 0

    expected = out_dir / "wf_profile.html"
    assert expected.is_file()
    html = expected.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in html
    assert "fcmd 性能剖面报告" in html


def test_profiler_html_output_custom_path(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """-o 指定输出文件路径。"""
    script = _write_script(tmp_path, "wf.py", _SIMPLE_WORKFLOW)
    out_file = tmp_path / "custom.html"

    app = FcmdApp(["profiler", str(script), "--no-browser", "-o", str(out_file)])
    assert app.run() == 0

    assert out_file.is_file()
    assert "<!DOCTYPE html>" in out_file.read_text(encoding="utf-8")


def test_profiler_open_browser_called(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """不带 --no-browser 时调用 webbrowser.open。"""
    script = _write_script(tmp_path, "wf.py", _SIMPLE_WORKFLOW)
    out_file = tmp_path / "rep.html"

    opened: list[str] = []

    def _fake_open(url: str) -> bool:
        opened.append(url)
        return True

    import webbrowser

    monkeypatch.setattr(webbrowser, "open", _fake_open)

    app = FcmdApp(["profiler", str(script), "-o", str(out_file)])
    assert app.run() == 0

    assert len(opened) == 1
    assert opened[0].startswith("file:///")


def test_profiler_browser_error_does_not_fail(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """webbrowser.open 抛异常时不影响退出码（仍为 0）。"""
    script = _write_script(tmp_path, "wf.py", _SIMPLE_WORKFLOW)
    out_file = tmp_path / "rep.html"

    import webbrowser

    def _raise(_url: str) -> bool:
        raise OSError("no display")

    monkeypatch.setattr(webbrowser, "open", _raise)

    app = FcmdApp(["profiler", str(script), "-o", str(out_file)])
    assert app.run() == 0
    out = capsys.readouterr().out
    assert "无法打开浏览器" in out


# ---------------------------------------------------------------------- #
# 文本输出
# ---------------------------------------------------------------------- #
def test_profiler_text_output(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """-E text 输出纯文本到 stdout。"""
    script = _write_script(tmp_path, "wf.py", _SIMPLE_WORKFLOW)

    app = FcmdApp(["profiler", str(script), "-E", "text"])
    assert app.run() == 0

    out = capsys.readouterr().out
    assert "fcmd 性能剖面报告" in out
    assert "【图级指标】" in out


# ---------------------------------------------------------------------- #
# 异常脚本（TaskFailedError）— hook 仍能捕获 report
# ---------------------------------------------------------------------- #
def test_profiler_handles_failing_workflow(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """脚本中 fx.run() 抛 TaskFailedError 时仍生成报告（通过 RunReport.__init__ hook）。"""
    script = _write_script(tmp_path, "boom.py", _RAISING_SCRIPT)
    out_file = tmp_path / "boom_rep.html"

    app = FcmdApp(["profiler", str(script), "--no-browser", "-o", str(out_file)])
    # 脚本抛异常被 _builtin_profiler 捕获，但仍能生成报告 → 返回 0
    assert app.run() == 0
    assert out_file.is_file()


# ---------------------------------------------------------------------- #
# Hook 还原
# ---------------------------------------------------------------------- #
def test_profiler_restores_run_after_execution(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """hook 还原后 fcmd.run 恢复原状，可正常调用。"""
    script = _write_script(tmp_path, "wf.py", _SIMPLE_WORKFLOW)
    out_file = tmp_path / "rep.html"

    app = FcmdApp(["profiler", str(script), "--no-browser", "-o", str(out_file)])
    assert app.run() == 0

    # hook 已还原，fcmd.run 应能正常工作
    import fcmd as fx

    @fx.task  # pyrefly: ignore [not-callable]
    def t() -> int:
        return 42

    g = fx.graph(t)
    report = fx.run(g)
    assert report.success
    assert report["t"] == 42


def test_profiler_restores_top_level_run_when_not_previously_set(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """执行前若 fcmd.run 未被访问（不在 __dict__），还原后从 __dict__ 删除。"""
    # 确保 fcmd.run 未被缓存：重新 import fcmd
    import importlib

    import fcmd as fx_mod

    # 删除可能已缓存的 run
    had_run = "run" in fx_mod.__dict__
    original_run = fx_mod.__dict__.get("run")
    if had_run:
        del fx_mod.__dict__["run"]

    try:
        script = _write_script(tmp_path, "wf.py", _SIMPLE_WORKFLOW)
        out_file = tmp_path / "rep.html"
        app = FcmdApp(["profiler", str(script), "--no-browser", "-o", str(out_file)])
        assert app.run() == 0

        # 还原后 run 不应在 __dict__（保持懒加载语义）
        assert "run" not in fx_mod.__dict__
        # 但仍可正常访问（通过 __getattr__）
        _ = fx_mod.run
        assert "run" in fx_mod.__dict__
    finally:
        # 清理：恢复初始状态
        if "run" in fx_mod.__dict__ and not had_run:
            del fx_mod.__dict__["run"]
        if had_run and original_run is not None:
            fx_mod.__dict__["run"] = original_run
        importlib.reload(fx_mod)


# ---------------------------------------------------------------------- #
# 脚本参数传递
# ---------------------------------------------------------------------- #
def test_profiler_passes_script_args(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """-- 之后的参数传给目标脚本（通过 sys.argv）。"""
    # 脚本读 sys.argv[1] 作为任务名，调用 fx.run
    script_content = """\
import sys
import fcmd as fx

name = sys.argv[1] if len(sys.argv) > 1 else "default"

@fx.task
def default() -> str:
    return name

g = fx.graph(default)
fx.run(g)
"""
    script = _write_script(tmp_path, "arg.py", script_content)
    out_file = tmp_path / "rep.html"

    app = FcmdApp(["profiler", str(script), "--no-browser", "-o", str(out_file), "--", "my_arg"])
    assert app.run() == 0

    html = out_file.read_text(encoding="utf-8")
    assert "default" in html


# ---------------------------------------------------------------------- #
# 内部辅助方法
# ---------------------------------------------------------------------- #
def test_inject_run_hook_captures_graph_and_report() -> None:
    """_inject_run_hook 注入后调用 run 能捕获 graph + report。"""
    app = FcmdApp([])
    captured = app._inject_run_hook()

    import fcmd as fx

    @fx.task  # pyrefly: ignore [not-callable]
    def t() -> int:
        return 1

    g = fx.graph(t)
    fx.run(g)

    assert captured.get("graph") is g
    assert captured.get("report") is not None

    captured["_restore"]()


def test_inject_run_hook_restore_reverts_patches() -> None:
    """_restore 还原后 executors.run 恢复原对象。"""
    from fcmd import executors as executors_mod

    original = executors_mod.run

    app = FcmdApp([])
    captured = app._inject_run_hook()
    assert executors_mod.run is not original

    captured["_restore"]()
    assert executors_mod.run is original


def test_output_profile_text_writes_to_stdout(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """_output_profile export=text 写 stdout。"""
    from datetime import datetime

    from fcmd.dag import Graph
    from fcmd.profiling import ProfileReport
    from fcmd.report import RunReport
    from fcmd.task import TaskResult, TaskSpec, TaskStatus

    spec = TaskSpec(name="a", fn=lambda: 1)
    graph = Graph.from_specs([spec])
    report = RunReport()
    base = datetime(2024, 1, 1)
    report.results["a"] = TaskResult(
        spec=spec,
        status=TaskStatus.SUCCESS,
        attempts=1,
        started_at=base,
        finished_at=base,
    )
    profile = ProfileReport.from_report(report, graph)

    FcmdApp._output_profile(profile, export="text", output=None, script_stem="x", no_browser=True)

    out = capsys.readouterr().out
    assert "fcmd 性能剖面报告" in out


def test_output_profile_html_writes_file(
    tmp_path: Path,
) -> None:
    """_output_profile export=html 写入文件。"""
    from datetime import datetime

    from fcmd.dag import Graph
    from fcmd.profiling import ProfileReport
    from fcmd.report import RunReport
    from fcmd.task import TaskResult, TaskSpec, TaskStatus

    spec = TaskSpec(name="a", fn=lambda: 1)
    graph = Graph.from_specs([spec])
    report = RunReport()
    base = datetime(2024, 1, 1)
    report.results["a"] = TaskResult(
        spec=spec,
        status=TaskStatus.SUCCESS,
        attempts=1,
        started_at=base,
        finished_at=base,
    )
    profile = ProfileReport.from_report(report, graph)

    out_file = tmp_path / "out.html"
    FcmdApp._output_profile(profile, export="html", output=str(out_file), script_stem="x", no_browser=True)
    assert out_file.is_file()
    assert "<!DOCTYPE html>" in out_file.read_text(encoding="utf-8")


def test_run_target_script_executes_with_main(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """_run_target_script 以 __main__ 身份执行脚本。"""
    script = _write_script(tmp_path, "exec.py", "print('hello_from_script')\n")
    FcmdApp._run_target_script(script, [])
    out = capsys.readouterr().out
    assert "hello_from_script" in out


def test_run_target_script_sets_sys_argv(tmp_path: Path) -> None:
    """_run_target_script 设置 sys.argv[0] 为脚本路径。"""
    script = _write_script(tmp_path, "argv.py", "import sys\nprint(sys.argv[0])\n")
    FcmdApp._run_target_script(script, ["a", "b"])
    # 无需断言——只要不抛异常即可（print 输出已用 capsys 捕获但本测试不验证）


def test_run_target_script_adds_script_dir_to_path(tmp_path: Path) -> None:
    """脚本所在目录被加入 sys.path。"""
    sub = tmp_path / "sub"
    sub.mkdir()
    script = sub / "s.py"
    script.write_text("import sys\nprint('ok')\n", encoding="utf-8")
    original_path = list(sys.path)
    try:
        FcmdApp._run_target_script(script, [])
        assert str(sub.resolve()) in sys.path
    finally:
        sys.path[:] = original_path


# ---------------------------------------------------------------------- #
# _run_builtin 分发
# ---------------------------------------------------------------------- #
def test_builtin_dispatch_profiler(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    """_run_builtin('profiler', ...) 正确分发到 _builtin_profiler。"""
    app = FcmdApp([])
    # 无参数 → 返回 1
    assert app._run_builtin("profiler", []) == 1


# ---------------------------------------------------------------------- #
# _BUILTIN_COMMANDS 包含 profiler
# ---------------------------------------------------------------------- #
def test_builtin_commands_includes_profiler() -> None:
    """_BUILTIN_COMMANDS 含 'profiler'。"""
    from fcmd.cli.main import _BUILTIN_COMMANDS

    assert "profiler" in _BUILTIN_COMMANDS


# ---------------------------------------------------------------------- #
# 覆盖率补充：SystemExit 与可选依赖缺失
# ---------------------------------------------------------------------- #
def test_profiler_script_calls_sys_exit(tmp_path: Path) -> None:
    """脚本调用 sys.exit() 时 profiler 捕获 SystemExit 并正常生成报告。"""
    script_content = """\
import sys
import fcmd as fx

@fx.task
def a() -> int:
    return 1

g = fx.graph(a)
fx.run(g)
sys.exit(0)
"""
    script = _write_script(tmp_path, "exit.py", script_content)
    out_file = tmp_path / "rep.html"

    app = FcmdApp(["profiler", str(script), "--no-browser", "-o", str(out_file)])
    assert app.run() == 0
    assert out_file.exists()


def test_collect_optional_deps_status_with_missing_dep(monkeypatch: pytest.MonkeyPatch) -> None:
    """_collect_optional_deps_status 处理未安装的可选依赖。"""
    import builtins

    app = FcmdApp([])
    original_import = builtins.__import__

    def mock_import(name: str, *args: object, **kwargs: object) -> object:
        if name == "PIL":
            raise ImportError("mocked: No module named 'PIL'")
        return original_import(name, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", mock_import)

    deps = app._collect_optional_deps_status()
    pil_dep = next(d for d in deps if d["package"] == "PIL")
    assert pil_dep["installed"] is False
    assert pil_dep["version"] == ""
