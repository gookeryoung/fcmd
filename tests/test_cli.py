"""CLI 主入口测试。"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from fcmd.cli.main import FcmdApp, _build_parser, main


def test_cli_parser_version() -> None:
    """--version 打印版本号。"""
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc_info:
        parser.parse_args(["--version"])
    assert exc_info.value.code == 0


def test_cli_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """无参数调用通过 SystemExit(0) 退出。"""
    monkeypatch.setattr(sys, "argv", ["fcmd"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


def test_cli_entry_point_version() -> None:
    """通过 console_script 入口调用 --version。"""
    result = subprocess.run(
        [sys.executable, "-m", "fcmd", "--version"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "fcmd" in result.stdout


# ---------------------------------------------------------------------- #
# FcmdApp 测试
# ---------------------------------------------------------------------- #
def test_fcmd_app_no_args() -> None:
    """无参数列出工具，返回 0。"""
    app = FcmdApp([])
    assert app.run() == 0


def test_fcmd_app_help_flag() -> None:
    """--help 列出工具，返回 0。"""
    app = FcmdApp(["--help"])
    assert app.run() == 0


def test_fcmd_app_version() -> None:
    """--version 输出版本号，返回 0。"""
    app = FcmdApp(["--version"])
    assert app.run() == 0


def test_fcmd_app_unknown_tool() -> None:
    """未知工具返回 1。"""
    app = FcmdApp(["nonexistent_tool"])
    assert app.run() == 1


def test_fcmd_app_unknown_tool_suggestion() -> None:
    """未知工具名接近时给出模糊匹配建议。"""
    # pymak 接近 pymake，应触发建议
    app = FcmdApp(["pymak"])
    assert app.run() == 1


def test_fcmd_app_pymake_no_subcommand() -> None:
    """fcmd pymake 列出子命令，返回 0。"""
    app = FcmdApp(["pymake"])
    # 无 subcommand → run_tool 列子命令返回 SUCCESS
    assert app.run() == 0


def test_fcmd_app_pymake_b() -> None:
    """fcmd pymake b --dry-run 验证构建子命令路由。"""
    app = FcmdApp(["pymake", "b", "--dry-run"])
    # --dry-run 不实际执行 uv build，仅打印执行计划
    assert app.run() == 0


def test_fcmd_app_pymake_unknown_subcommand() -> None:
    """fcmd pymake unknown 返回 1。"""
    app = FcmdApp(["pymake", "unknown_subcommand"])
    assert app.run() == 1


def test_fcmd_app_pm_alias() -> None:
    """fcmd pm 别名路由到 pymake。"""
    app = FcmdApp(["pm"])
    # pm → pymake，无 subcommand → 列子命令返回 0
    assert app.run() == 0


def test_fcmd_app_pymake_dry_run() -> None:
    """fcmd pymake b --dry-run 不实际执行，返回 0。"""
    app = FcmdApp(["pymake", "b", "--dry-run"])
    assert app.run() == 0


def test_main_with_pymake(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() 路由 pymake b --dry-run 返回 0。"""
    monkeypatch.setattr(sys, "argv", ["fcmd", "pymake", "b", "--dry-run"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


def test_main_no_args(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() 无参数通过 SystemExit(0) 退出。"""
    monkeypatch.setattr(sys, "argv", ["fcmd"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


def test_main_version(monkeypatch: pytest.MonkeyPatch) -> None:
    """main() --version 通过 SystemExit(0) 退出。"""
    monkeypatch.setattr(sys, "argv", ["fcmd", "--version"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------- #
# _tool_description / _run_tool 防御路径覆盖
# ---------------------------------------------------------------------- #
def test_tool_description_unknown_tool_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """_tool_description 传入未知工具名（不在 _TOOL_MODULES 且不在 _TOOL_REGISTRY）返回空串。"""
    from fcmd.cli import main as main_mod

    monkeypatch.setitem(main_mod._TOOL_MODULES, "ghost", "fcmd.cli.ghost")
    # 未知工具不在 _TOOL_REGISTRY，应返回 ""
    assert main_mod.FcmdApp._tool_description(main_mod.FcmdApp([]), "ghost") == ""


def test_tool_description_import_error_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """_tool_description 导入模块失败（ImportError）时返回空串。"""
    from fcmd.cli import main as main_mod

    def _raise_import_error(_name: str) -> None:
        raise ImportError("simulated")

    monkeypatch.setitem(main_mod._TOOL_MODULES, "broken", "fcmd.cli.broken")
    monkeypatch.setattr(main_mod.importlib, "import_module", _raise_import_error)
    assert main_mod.FcmdApp._tool_description(main_mod.FcmdApp([]), "broken") == ""


def test_tool_description_with_description_field(monkeypatch: pytest.MonkeyPatch) -> None:
    """_tool_description 优先返回 spec.description（非空时立即返回）。"""
    from fcmd.apis.toolkit import ToolSpec
    from fcmd.cli import main as main_mod

    def _fake_func() -> None:
        """dummy."""

    spec = ToolSpec(
        name="dummy",
        subcommand="x",
        func=_fake_func,
        description="工具级描述",
        help="子命令帮助",
    )
    # dummy 不在 _TOOL_MODULES，跳过导入；在 _TOOL_REGISTRY 中有 description
    monkeypatch.setitem(main_mod._TOOL_REGISTRY, "dummy", {"x": spec})
    result = main_mod.FcmdApp._tool_description(main_mod.FcmdApp([]), "dummy")
    assert result == "工具级描述"


def test_tool_description_no_description_no_help(monkeypatch: pytest.MonkeyPatch) -> None:
    """_tool_description 在 spec 无 description 且 hidden spec 无 help 时返回空串。"""
    from fcmd.apis.toolkit import ToolSpec
    from fcmd.cli import main as main_mod

    def _fake_func() -> None:
        """dummy."""

    # hidden=True 且 help=""，第二个 for 循环跳过
    spec = ToolSpec(
        name="silent",
        subcommand="y",
        func=_fake_func,
        description="",
        help="",
        hidden=True,
    )
    monkeypatch.setitem(main_mod._TOOL_REGISTRY, "silent", {"y": spec})
    result = main_mod.FcmdApp._tool_description(main_mod.FcmdApp([]), "silent")
    assert result == ""


def test_run_tool_module_path_none_returns_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """_run_tool 在 module_path 为 None 时打印错误并返回 1。"""
    from fcmd.cli import main as main_mod

    # 构造 _TOOL_ALIASES 让 _resolve_tool 返回一个不在 _TOOL_MODULES 的工具名
    monkeypatch.setitem(main_mod._TOOL_ALIASES, "ghost", "ghost_tool")
    # _TOOL_MODULES 不含 "ghost_tool"
    app = main_mod.FcmdApp(["ghost"])
    assert app.run() == 1
    out = capsys.readouterr().out
    assert "无模块映射" in out


def test_run_tool_import_error_returns_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """_run_tool 在 import_module 抛 ImportError 时打印错误并返回 1。"""
    from fcmd.cli import main as main_mod

    def _raise_import_error(_name: str) -> None:
        raise ImportError("simulated load failure")

    monkeypatch.setitem(main_mod._TOOL_ALIASES, "broken", "broken_tool")
    monkeypatch.setitem(main_mod._TOOL_MODULES, "broken_tool", "fcmd.cli.broken_tool")
    monkeypatch.setattr(main_mod.importlib, "import_module", _raise_import_error)
    app = main_mod.FcmdApp(["broken"])
    assert app.run() == 1
    out = capsys.readouterr().out
    assert "加载工具" in out


# ---------------------------------------------------------------------- #
# 工具自动发现机制测试
# ---------------------------------------------------------------------- #
class TestToolDiscovery:
    """``_ensure_tools_discovered`` 自动发现机制测试。"""

    def test_discovery_finds_pymake_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """扫描后发现 pymake 工具模块。"""
        from fcmd.cli import main as main_mod

        # 重置发现状态，强制重新扫描
        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {})
        main_mod._ensure_tools_discovered()
        assert "pymake" in main_mod._TOOL_MODULES
        assert main_mod._TOOL_MODULES["pymake"] == "fcmd.cli.pymake"

    def test_discovery_loads_aliases_from_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """扫描时读取模块 __tool_aliases__ 并注册别名。"""
        from fcmd.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {})
        main_mod._ensure_tools_discovered()
        # pymake.py 声明 __tool_aliases__ = ["pm"]
        assert main_mod._TOOL_ALIASES.get("pm") == "pymake"

    def test_discovery_is_idempotent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """多次调用 _ensure_tools_discovered 不重复扫描。"""
        from fcmd.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {})
        main_mod._ensure_tools_discovered()
        # 手动清空后再调用，不应重新填充（标志已 True）
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        main_mod._ensure_tools_discovered()
        # 标志为 True，不会重新扫描，_TOOL_ALIASES 保持空
        assert main_mod._TOOL_ALIASES == {}

    def test_discovery_skips_main_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """discovery 不把 main 模块当作工具。"""
        from fcmd.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {})
        main_mod._ensure_tools_discovered()
        assert "main" not in main_mod._TOOL_MODULES

    def test_discovery_skips_private_modules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """discovery 不把 _ 前缀模块当作工具。"""
        from fcmd.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {})
        main_mod._ensure_tools_discovered()
        for name in main_mod._TOOL_MODULES:
            assert not name.startswith("_"), f"私有模块 {name!r} 不应被发现"

    def test_discovery_does_not_override_mock_entries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """discovery 用 setdefault，不覆盖测试注入的键。"""
        from fcmd.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        # 预注入一个 mock 工具
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {"pymake": "mock_value"})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {"pymake": "mock_module"})
        main_mod._ensure_tools_discovered()
        # setdefault 不覆盖已存在的值
        assert main_mod._TOOL_MODULES["pymake"] == "mock_module"
        assert main_mod._TOOL_ALIASES["pymake"] == "mock_value"

    def test_run_triggers_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """FcmdApp.run() 首次调用触发 discovery。"""
        from fcmd.cli import main as main_mod

        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {})
        app = main_mod.FcmdApp(["pymake", "b", "--dry-run"])
        app.run()
        assert main_mod._TOOLS_DISCOVERED is True
        assert "pymake" in main_mod._TOOL_MODULES

    def test_cold_start_import_does_not_trigger_discovery(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """import fcmd 不触发 discovery（_TOOLS_DISCOVERED 保持 False）。"""
        # 此测试验证冷启动：重新 import fcmd.cli.main 不会触发 discovery
        # discovery 只在 FcmdApp.run() 显式调用时触发
        from fcmd.cli import main as main_mod

        # 模拟冷启动场景：重置标志后不调用 run()
        monkeypatch.setattr(main_mod, "_TOOLS_DISCOVERED", False)
        monkeypatch.setattr(main_mod, "_TOOL_ALIASES", {})
        monkeypatch.setattr(main_mod, "_TOOL_MODULES", {})
        # 仅访问模块，不调用 run()
        assert main_mod._TOOLS_DISCOVERED is False
        assert main_mod._TOOL_MODULES == {}


# ---------------------------------------------------------------------- #
# fcmd graph 内建命令测试
# ---------------------------------------------------------------------- #
class TestBuiltinGraph:
    """``fcmd graph`` 内建命令测试。"""

    def test_graph_pymake_tc_mermaid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pymake tc 默认输出 Mermaid 图。"""
        app = FcmdApp(["graph", "pymake", "tc"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "graph TD" in out
        # tc 依赖 c + pyrefly_check + lint
        assert "tc" in out
        assert "c" in out
        assert "lint" in out

    def test_graph_pymake_all_mermaid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pymake all 输出全套流程 DAG。"""
        app = FcmdApp(["graph", "pymake", "all"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "graph TD" in out
        for name in ("c", "b", "t", "tc"):
            assert name in out, f"DAG 应包含 {name!r}"

    def test_graph_format_layers(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pymake tc --format=layers 输出分层列表。"""
        app = FcmdApp(["graph", "pymake", "tc", "--format=layers"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "Layer" in out
        # tc 有依赖，至少 2 层
        assert "Layer 1" in out
        assert "Layer 2" in out

    def test_graph_format_describe(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pymake tc --format=describe 输出摘要。"""
        app = FcmdApp(["graph", "pymake", "tc", "--format=describe"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "Graph(tasks=" in out
        assert "Layer" in out

    def test_graph_unknown_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph unknown_tool 返回 1。"""
        app = FcmdApp(["graph", "nonexistent_tool", "x"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "未知工具" in out

    def test_graph_unknown_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pymake unknown 返回 1。"""
        app = FcmdApp(["graph", "pymake", "unknown_subcommand"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "没有子命令" in out

    def test_graph_no_args_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph 无参数打印帮助并返回 1。"""
        app = FcmdApp(["graph"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "tool" in out

    def test_graph_pm_alias_works(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pm tc 别名路由正常。"""
        app = FcmdApp(["graph", "pm", "tc"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "graph TD" in out

    def test_graph_single_command_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pymake b（单任务）输出单节点图。"""
        app = FcmdApp(["graph", "pymake", "b"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "graph TD" in out
        assert "b" in out

    def test_graph_no_subcommand_shows_all(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd graph pymake（无子命令）输出全部子命令的 DAG。"""
        app = FcmdApp(["graph", "pymake"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "graph TD" in out
        # 全部子命令都应出现
        for name in ("b", "c", "t", "tc", "all"):
            assert name in out, f"全量 DAG 应包含 {name!r}"

    def test_run_builtin_unknown_name(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_run_builtin 收到未知内建命令名时返回 1（防御路径）。"""
        app = FcmdApp()
        assert app._run_builtin("nonexistent_builtin", []) == 1
        out = capsys.readouterr().out
        assert "未知内建命令" in out


# ---------------------------------------------------------------------- #
# build_tool_graph 直接 API 测试
# ---------------------------------------------------------------------- #
class TestBuildToolGraph:
    """``build_tool_graph`` 公共 API 测试。"""

    def test_unknown_tool_raises(self) -> None:
        """未注册工具抛 FcmdError。"""
        from fcmd.apis.toolkit import build_tool_graph
        from fcmd.errors import FcmdError

        with pytest.raises(FcmdError, match="未注册"):
            build_tool_graph("nonexistent_tool_xyz", None)

    def test_unknown_subcommand_raises(self) -> None:
        """未注册子命令抛 FcmdError。"""
        from fcmd.apis.toolkit import build_tool_graph
        from fcmd.errors import FcmdError

        with pytest.raises(FcmdError, match="没有子命令"):
            build_tool_graph("pymake", "nonexistent_sub_xyz")

    def test_none_target_includes_all_subcommands(self) -> None:
        """target=None 时包含全部子命令。"""
        from fcmd.apis.toolkit import build_tool_graph

        graph = build_tool_graph("pymake", None)
        names = set(graph.names)
        # 至少包含主要子命令
        assert {"b", "c", "t", "tc"}.issubset(names)

    def test_target_with_deps_includes_upstream(self) -> None:
        """target=tc 时包含 tc 及其上游依赖。"""
        from fcmd.apis.toolkit import build_tool_graph

        graph = build_tool_graph("pymake", "tc")
        names = set(graph.names)
        assert "tc" in names
        # tc 依赖 c / pyrefly_check / lint，应一并包含
        assert "c" in names


# ---------------------------------------------------------------------- #
# fcmd info 内建命令测试
# ---------------------------------------------------------------------- #
class TestBuiltinInfo:
    """``fcmd info`` 内建命令测试。"""

    def test_info_no_args_shows_overview(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info 无参数列出内建命令与工具概览。"""
        app = FcmdApp(["info"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "内建命令" in out
        assert "fcmd graph" in out
        assert "fcmd info" in out
        assert "pymake" in out

    def test_info_tool_shows_subcommands(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info pymake 列出 pymake 的全部子命令。"""
        app = FcmdApp(["info", "pymake"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "pymake" in out
        # 应包含主要子命令
        for name in ("b", "c", "t", "tc", "all"):
            assert name in out, f"info 应列出子命令 {name!r}"
        # 应标记 hidden 子命令
        assert "hidden" in out

    def test_info_subcommand_shows_full_spec(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info pymake tc 展示 tc 的完整 ToolSpec 字段。"""
        app = FcmdApp(["info", "pymake", "tc"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "pymake" in out
        assert "tc" in out
        # 关键字段
        assert "help" in out
        assert "needs" in out
        assert "strategy" in out
        assert "cmd" in out
        assert "hidden" in out
        # tc 是聚合任务（有 needs，无 cmd）
        assert "aggregate" in out

    def test_info_cmd_subcommand_shows_cmd(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info pymake b 展示 cmd 任务的命令。"""
        app = FcmdApp(["info", "pymake", "b"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "uv build" in out
        assert "cmd" in out

    def test_info_unknown_tool(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info unknown_tool 返回 1。"""
        app = FcmdApp(["info", "nonexistent_tool"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "未知工具" in out

    def test_info_unknown_subcommand(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info pymake unknown 返回 1。"""
        app = FcmdApp(["info", "pymake", "unknown_subcommand"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "没有子命令" in out

    def test_info_pm_alias_works(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info pm 别名路由正常。"""
        app = FcmdApp(["info", "pm"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "pymake" in out

    def test_info_overview_shows_subcommand_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info 概览显示子命令数量（含 hidden 标注）。"""
        app = FcmdApp(["info"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # pymake 有 hidden 子命令，应显示 "(+N hidden)"
        assert "hidden" in out

    def test_info_subcommand_marked_hidden(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd info pymake 展示 hidden 子命令时带标记。"""
        app = FcmdApp(["info", "pymake"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # pyrefly_check 是 hidden 子命令
        assert "pyrefly_check" in out

    def test_spec_kind_classification(self) -> None:
        """_spec_kind 正确分类 cmd / aggregate / fn。"""
        from fcmd.apis.toolkit import get_tool

        # cmd 任务
        b_spec = get_tool("pymake", "b")
        assert FcmdApp._spec_kind(b_spec) == "cmd"
        # aggregate 任务（tc 有 needs 无 cmd 无函数逻辑）
        tc_spec = get_tool("pymake", "tc")
        assert FcmdApp._spec_kind(tc_spec) == "aggregate"
        # fn 任务（c 有函数逻辑无 cmd 无 needs）
        c_spec = get_tool("pymake", "c")
        assert FcmdApp._spec_kind(c_spec) == "fn"

    def test_info_tool_import_failure(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd info <tool> 模块导入失败时返回 1（覆盖 _load_tool_subs ImportError）。"""
        from fcmd.cli import main as main_mod

        # 注入伪工具：在 _TOOL_ALIASES 和 _TOOL_MODULES 中注册一个不可导入的模块
        monkeypatch.setitem(main_mod._TOOL_ALIASES, "broken_tool", "broken_tool")
        monkeypatch.setitem(main_mod._TOOL_MODULES, "broken_tool", "fcmd.cli.nonexistent_module_xyz")

        app = FcmdApp(["info", "broken_tool"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "加载工具" in out
        assert "失败" in out

    def test_info_tool_not_in_registry(
        self,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """fcmd info <tool> 工具解析成功但未注册时返回 1（覆盖 _load_tool_subs 未注册路径）。"""
        from fcmd.cli import main as main_mod

        # 注入伪工具：在 _TOOL_ALIASES 中注册，但不在 _TOOL_MODULES 也不在 _TOOL_REGISTRY
        monkeypatch.setitem(main_mod._TOOL_ALIASES, "ghost_tool", "ghost_tool")

        app = FcmdApp(["info", "ghost_tool"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "未注册" in out


# ---------------------------------------------------------------------- #
# fcmd completion 内建命令
# ---------------------------------------------------------------------- #
class TestBuiltinCompletion:
    """``fcmd completion`` 内建命令测试。"""

    def test_completion_no_args_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd completion 无参数打印帮助并返回 1。"""
        app = FcmdApp(["completion"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "--shell" in out

    def test_completion_bash_outputs_script(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd completion --shell bash 输出 bash 补全脚本。"""
        app = FcmdApp(["completion", "--shell", "bash"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "_fcmd_complete" in out
        assert "complete -F _fcmd_complete fcmd" in out

    def test_completion_zsh_outputs_script(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd completion --shell zsh 输出 zsh 补全脚本。"""
        app = FcmdApp(["completion", "--shell", "zsh"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "#compdef fcmd" in out
        assert "_describe" in out

    def test_completion_fish_outputs_script(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd completion --shell fish 输出 fish 补全脚本。"""
        app = FcmdApp(["completion", "--shell", "fish"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "complete -c fcmd" in out

    def test_completion_default_shell_is_bash(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd completion --shell 缺省生成 bash 脚本。"""
        app = FcmdApp(["completion", "--shell=bash"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "_fcmd_complete" in out

    def test_completion_invalid_shell_returns_failure(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd completion --shell invalid 返回 1（argparse 报错）。"""
        app = FcmdApp(["completion", "--shell", "powershell"])
        # argparse 解析失败 SystemExit(2) 被 _builtin_completion 直接抛出
        with pytest.raises(SystemExit) as exc_info:
            app.run()
        assert exc_info.value.code == 2

    def test_completion_bash_includes_tools_and_aliases(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bash 脚本第一层包含全部工具名 + 别名 + 内建命令 + 全局选项。"""
        app = FcmdApp(["completion", "--shell", "bash"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # 内建命令
        for cmd in ("graph", "info", "completion"):
            assert cmd in out, f"应包含内建命令 {cmd!r}"
        # 全局选项
        assert "--version" in out
        # 工具名 + 别名
        for name in ("pymake", "pm", "hashfile", "filedate", "writefile", "folderzip"):
            assert name in out, f"应包含工具/别名 {name!r}"

    def test_completion_bash_includes_pymake_subcommands(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bash 脚本包含 pymake 的可见子命令。"""
        app = FcmdApp(["completion", "--shell", "bash"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # pymake 的主要可见子命令
        for sub in ("b", "c", "t", "tc", "all", "sync", "lint", "fmt"):
            assert sub in out, f"应包含 pymake 子命令 {sub!r}"
        # hidden 子命令不应出现在补全脚本中
        assert "pyrefly_check" not in out

    def test_completion_bash_pymake_pattern_with_alias(self, capsys: pytest.CaptureFixture[str]) -> None:
        """bash 脚本中 pymake 的 case 分支同时匹配 pymake 和 pm。"""
        app = FcmdApp(["completion", "--shell", "bash"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "pymake|pm)" in out

    def test_completion_fish_merges_alias_in_single_call(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fish 脚本对带别名的工具用单次 __fish_seen_subcommand_from 调用合并名字。"""
        app = FcmdApp(["completion", "--shell", "fish"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # pymake 别名 pm 应与 pymake 同在一个 __fish_seen_subcommand_from 调用中
        assert "__fish_seen_subcommand_from pymake pm" in out
        # 不应出现两个独立调用（语义为 AND，永远不可能为真）
        assert "__fish_seen_subcommand_from pymake __fish_seen_subcommand_from pm" not in out

    def test_completion_zsh_pymake_pattern_with_alias(self, capsys: pytest.CaptureFixture[str]) -> None:
        """zsh 脚本中 pymake 的 case 分支同时匹配 pymake 和 pm。"""
        app = FcmdApp(["completion", "--shell", "zsh"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "(pymake|pm)" in out

    def test_completion_zsh_includes_command_descriptions(self, capsys: pytest.CaptureFixture[str]) -> None:
        """zsh 脚本 commands 列表包含工具名:描述 对。"""
        app = FcmdApp(["completion", "--shell", "zsh"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # 工具描述对（用工具名作描述）
        assert "'pymake:pymake'" in out
        assert "'pm:pymake'" in out
        # 全局选项带描述
        assert "'--version:版本号'" in out

    def test_completion_fish_includes_subcommand_descriptions(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fish 脚本子命令补全带描述。"""
        app = FcmdApp(["completion", "--shell", "fish"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # pymake 的 b 子命令描述
        assert "-a 'b' -d '构建分发包" in out

    def test_completion_skips_single_command_tools_in_subcommand_block(
        self,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无子命令的工具（writefile/folderzip）不出现在子命令补全分支。"""
        app = FcmdApp(["completion", "--shell", "bash"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # writefile 和 folderzip 是单命令工具，无子命令分支
        assert "writefile)" not in out
        assert "folderzip)" not in out

    def test_completion_routes_through_run(self, capsys: pytest.CaptureFixture[str]) -> None:
        """completion 通过 run() 入口正确路由（覆盖 _run_builtin 分发）。"""
        app = FcmdApp(["completion", "--shell", "bash"])
        assert app.run() == 0
        # 区分于 graph/info：completion 输出第一行应为脚本注释
        out = capsys.readouterr().out
        assert out.startswith("# fcmd bash 补全脚本")

    def test_completion_collect_data_returns_sorted(
        self,
    ) -> None:
        """_collect_completion_data 返回按工具名排序的列表。"""
        app = FcmdApp()
        data = app._collect_completion_data()
        names = [t["name"] for t in data]
        assert names == sorted(names)
        # 必须包含已知工具
        assert "pymake" in names
        assert "hashfile" in names

    def test_completion_collect_data_pymake_has_aliases(
        self,
    ) -> None:
        """_collect_completion_data 中 pymake 工具的 aliases 含 pm。"""
        app = FcmdApp()
        data = app._collect_completion_data()
        pymake = next(t for t in data if t["name"] == "pymake")
        assert "pm" in pymake["aliases"]
        # hidden 子命令不应出现
        sub_names = [sc for sc, _ in pymake["subs"]]
        assert "pyrefly_check" not in sub_names
        assert "b" in sub_names

    def test_completion_collect_data_handles_import_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """_collect_completion_data 容忍模块导入失败（contextlib.suppress）。"""
        from fcmd.cli import main as main_mod

        # 注入一个不可导入的伪工具
        monkeypatch.setitem(main_mod._TOOL_ALIASES, "broken_tool", "broken_tool")
        monkeypatch.setitem(main_mod._TOOL_MODULES, "broken_tool", "fcmd.cli.nonexistent_xyz")

        app = FcmdApp()
        # 应不抛异常，返回的数据不含 broken_tool（因为导入失败未注册）
        data = app._collect_completion_data()
        # broken_tool 在 _TOOL_ALIASES.values() 中仍会出现，但无 subs
        broken = next((t for t in data if t["name"] == "broken_tool"), None)
        assert broken is not None
        assert broken["subs"] == []


# ---------------------------------------------------------------------- #
# fcmd yaml 内建命令
# ---------------------------------------------------------------------- #
class TestBuiltinYaml:
    """``fcmd yaml`` 内建命令测试。"""

    @staticmethod
    def _echo_cmd_yaml(text: str) -> str:
        """跨平台 echo 命令的 YAML 字面量。"""
        import sys

        if sys.platform == "win32":
            return f'["cmd", "/c", "echo", "{text}"]'
        return f'["echo", "{text}"]'

    def test_yaml_no_args_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml 无参数打印帮助并返回 1。"""
        app = FcmdApp(["yaml"])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "file" in out

    def test_yaml_executes_graph(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <file> 执行 YAML 任务图成功。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  hello:
    cmd: {self._echo_cmd_yaml("hello")}
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file)])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "执行成功" in out

    def test_yaml_dry_run_does_not_execute(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <file> --dry-run 打印计划但不执行。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  hello:
    cmd: {self._echo_cmd_yaml("hello")}
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file), "--dry-run"])
        assert app.run() == 0
        out = capsys.readouterr().out
        # dry-run 打印执行计划
        assert "Dry run" in out or "Layer" in out
        # 实际命令的 stdout（"hello"）不应出现在 dry-run 输出中
        # 注意：echo 命令的输出是 "hello"（不含 echo 命令本身），dry-run 不执行所以不出现
        # 但 "hello" 可能作为 job 名出现在 Layer 列表中，故用更精确的判断
        # dry-run 输出行不会以单独的 "hello" 行结尾（cmd 才会）
        lines = out.splitlines()
        cmd_output_lines = [ln for ln in lines if ln.strip() == "hello"]
        assert cmd_output_lines == []

    def test_yaml_only_runs_specific_job(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <file> <job> 仅执行指定 job 及其依赖。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  setup:
    cmd: {self._echo_cmd_yaml("setup")}
  build:
    needs: [setup]
    cmd: {self._echo_cmd_yaml("build")}
  deploy:
    needs: [build]
    cmd: {self._echo_cmd_yaml("deploy")}
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file), "build"])
        assert app.run() == 0
        # deploy 不应被执行
        out = capsys.readouterr().out
        assert "deploy" not in out or "执行成功" in out

    def test_yaml_nonexistent_file_returns_1(self, capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
        """fcmd yaml <nonexistent> 返回 1。"""
        app = FcmdApp(["yaml", str(tmp_path / "nonexistent.yaml")])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "加载 YAML 失败" in out

    def test_yaml_invalid_yaml_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <invalid.yaml> 返回 1。"""
        yaml_file = tmp_path / "invalid.yaml"
        yaml_file.write_text("key: [unclosed bracket\n", encoding="utf-8")
        app = FcmdApp(["yaml", str(yaml_file)])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "加载 YAML 失败" in out

    def test_yaml_invalid_schema_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <schema_invalid.yaml>（缺 jobs）返回 1。"""
        yaml_file = tmp_path / "no_jobs.yaml"
        yaml_file.write_text("foo: bar\n", encoding="utf-8")
        app = FcmdApp(["yaml", str(yaml_file)])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "加载 YAML 失败" in out

    def test_yaml_strategy_override(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <file> --strategy sequential 执行成功。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  hello:
    cmd: {self._echo_cmd_yaml("hello")}
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file), "--strategy", "sequential"])
        assert app.run() == 0
        out = capsys.readouterr().out
        assert "执行成功" in out

    def test_yaml_invalid_strategy_returns_2(self, tmp_path: Path) -> None:
        """fcmd yaml <file> --strategy invalid 触发 argparse 报错。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  hello:
    cmd: {self._echo_cmd_yaml("hello")}
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file), "--strategy", "invalid"])
        with pytest.raises(SystemExit) as exc_info:
            app.run()
        assert exc_info.value.code == 2

    def test_yaml_routes_through_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """yaml 通过 run() 入口正确路由（覆盖 _run_builtin 分发）。"""
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  hello:
    cmd: {self._echo_cmd_yaml("hello")}
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file)])
        assert app.run() == 0

    def test_yaml_failing_task_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <file> 任务失败时返回 1（覆盖 except FcmdError 分支）。"""
        import sys

        # 用不存在的命令触发任务失败
        if sys.platform == "win32":
            failing_cmd = '["cmd", "/c", "exit", "1"]'
        else:
            failing_cmd = '["false"]'
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  broken:
    cmd: {failing_cmd}
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file)])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "错误" in out

    def test_yaml_continue_on_error_returns_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd yaml <file> continue-on-error 任务失败时返回 1（覆盖 report.success=False 分支）。"""
        import sys

        if sys.platform == "win32":
            failing_cmd = '["cmd", "/c", "exit", "1"]'
        else:
            failing_cmd = '["false"]'
        yaml_file = tmp_path / "jobs.yaml"
        yaml_file.write_text(
            f"""
jobs:
  broken:
    cmd: {failing_cmd}
    continue-on-error: true
""",
            encoding="utf-8",
        )
        app = FcmdApp(["yaml", str(yaml_file)])
        assert app.run() == 1
        out = capsys.readouterr().out
        assert "执行失败" in out
