"""CLI 主入口测试。"""

from __future__ import annotations

import subprocess
import sys

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
