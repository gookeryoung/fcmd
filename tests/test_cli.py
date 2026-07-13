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
    """fcmd pymake b 执行构建子命令。"""
    app = FcmdApp(["pymake", "b"])
    # python --version 必定成功
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
    """main() 路由 pymake b 返回 0。"""
    monkeypatch.setattr(sys, "argv", ["fcmd", "pymake", "b"])
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
