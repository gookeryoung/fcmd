"""piptool 工具测试。

验证 ``fcmd.cli.piptool`` 模块：
- 工具注册
- 辅助函数
- 命令构造
- CLI 调度
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.piptool
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.piptool import (
    _expand_wildcard_packages,
    _filter_protected_packages,
    _get_installed_packages,
    pip_download,
    pip_freeze,
    pip_install,
    pip_reinstall,
    pip_uninstall,
    pip_upgrade,
)
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助：创建 fake run_command 函数（避免 lambda ARG005）
# ============================================================================ #
def _fake_run(result: CommandResult) -> Any:
    """创建总是返回 ``result`` 的 fake ``run_command`` 函数。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        return result

    return run


def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``run_command`` 函数，返回成功结果。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        calls.append(cmd)
        return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

    return run


def _success_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回成功结果的 fake ``run_command`` 函数。"""
    return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """piptool 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """piptool 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("piptool",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_piptool_subcommands(self) -> None:
        """piptool 应有 i/u/r/d/up/f 子命令。"""
        subs = fx.list_subcommands("piptool")
        for name in ("i", "u", "r", "d", "up", "f"):
            assert name in subs, f"子命令 {name!r} 未注册"


# ============================================================================ #
# piptool 测试
# ============================================================================ #
class TestPiptoolHelpers:
    """piptool 辅助函数测试。"""

    def test_filter_protected_packages_keeps_safe(self) -> None:
        """_filter_protected_packages 保留非保护包。"""
        result = _filter_protected_packages(["requests", "flask"])
        assert result == ["requests", "flask"]

    def test_filter_protected_packages_removes_protected(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_filter_protected_packages 过滤受保护包并打印提示。"""
        result = _filter_protected_packages(["requests", "fcmd", "flask"])
        assert "fcmd" not in result
        assert "requests" in result
        assert "flask" in result
        out = capsys.readouterr().out
        assert "fcmd" in out

    def test_filter_protected_packages_case_insensitive(self, capsys: pytest.CaptureFixture[str]) -> None:
        """_filter_protected_packages 大小写不敏感。"""
        result = _filter_protected_packages(["FCMD", "Requests"])
        assert "FCMD" not in result
        assert "Requests" in result

    def test_get_installed_packages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_installed_packages 解析 pip list 输出。"""
        fake_result = CommandResult(
            cmd=["pip", "list"],
            returncode=0,
            stdout="requests==2.31.0\nflask==3.0.0\n",
            stderr="",
        )
        monkeypatch.setattr("fcmd.cli.piptool.run_command", _fake_run(fake_result))
        result = _get_installed_packages()
        assert "requests" in result
        assert "flask" in result

    def test_get_installed_packages_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_get_installed_packages 空输出返回空列表。"""
        fake_result = CommandResult(cmd=["pip", "list"], returncode=0, stdout="", stderr="")
        monkeypatch.setattr("fcmd.cli.piptool.run_command", _fake_run(fake_result))
        assert _get_installed_packages() == []

    def test_expand_wildcard_no_pattern(self) -> None:
        """_expand_wildcard_packages 无通配符时返回原列表。"""
        assert _expand_wildcard_packages("requests") == ["requests"]

    def test_expand_wildcard_with_pattern(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """_expand_wildcard_packages 展开通配符。"""
        monkeypatch.setattr(
            "fcmd.cli.piptool._get_installed_packages",
            lambda: ["requests", "flask", "django"],
        )
        result = _expand_wildcard_packages("f*")
        assert "flask" in result
        assert "requests" not in result


class TestPiptoolCommands:
    """piptool CLI 子命令测试。"""

    def test_pip_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_install 调用 pip install。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_install(["requests", "flask"])
        assert calls[0] == ["pip", "install", "requests", "flask"]
        out = capsys.readouterr().out
        assert "安装完成" in out

    def test_pip_uninstall_protected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_uninstall 跳过受保护包。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        monkeypatch.setattr("fcmd.cli.piptool._expand_wildcard_packages", lambda p: [p])
        pip_uninstall(["fcmd"])
        # 受保护包应跳过，不调用 pip uninstall
        assert not any("uninstall" in " ".join(c) for c in calls)
        out = capsys.readouterr().out
        assert "受保护" in out

    def test_pip_uninstall_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_uninstall 正常卸载。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        monkeypatch.setattr("fcmd.cli.piptool._expand_wildcard_packages", lambda p: [p])
        pip_uninstall(["requests"])
        assert calls[0] == ["pip", "uninstall", "-y", "requests"]

    def test_pip_reinstall_all_protected(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_reinstall 全是受保护包时跳过。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["fcmd"])
        assert not calls
        out = capsys.readouterr().out
        assert "受保护" in out

    def test_pip_reinstall_normal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_reinstall 正常重装。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["requests"])
        assert calls[0] == ["pip", "uninstall", "-y", "requests"]
        assert calls[1] == ["pip", "install", "requests"]

    def test_pip_reinstall_offline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_reinstall 离线模式添加 --no-index。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_reinstall(["requests"], offline=True)
        assert calls[1] == [
            "pip",
            "install",
            "--no-index",
            "--find-links",
            ".",
            "requests",
        ]

    def test_pip_download(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_download 下载到 packages 目录。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_download(["requests"])
        assert calls[0] == ["pip", "download", "requests", "-d", "packages"]

    def test_pip_download_offline(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """pip_download 离线模式。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_download(["requests"], offline=True)
        assert "--no-index" in calls[0]
        assert "--find-links" in calls[0]

    def test_pip_upgrade(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_upgrade 升级 pip。"""
        calls: list[list[str]] = []
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _recording_run(calls),
        )
        pip_upgrade()
        assert calls[0] == ["python", "-m", "pip", "install", "--upgrade", "pip"]
        out = capsys.readouterr().out
        assert "升级完成" in out

    def test_pip_freeze(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """pip_freeze 导出依赖到 requirements.txt。"""
        monkeypatch.chdir(tmp_path)
        fake_result = CommandResult(
            cmd=["pip", "freeze"],
            returncode=0,
            stdout="requests==2.31.0\nflask==3.0.0\n",
            stderr="",
        )
        monkeypatch.setattr("fcmd.cli.piptool.run_command", _fake_run(fake_result))
        pip_freeze()
        content = (tmp_path / "requirements.txt").read_text(encoding="utf-8")
        assert "requests==2.31.0" in content
        out = capsys.readouterr().out
        assert "requirements.txt" in out


class TestPiptoolRunTool:
    """piptool 通过 run_tool 集成测试。"""

    def test_pip_i_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd piptool i <packages> 通过 run_tool 调用。"""
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _success_run,
        )
        code = run_tool("piptool", ["i", "requests"])
        assert code == 0
        out = capsys.readouterr().out
        assert "安装完成" in out

    def test_pip_up_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd piptool up 通过 run_tool 调用。"""
        monkeypatch.setattr(
            "fcmd.cli.piptool.run_command",
            _success_run,
        )
        code = run_tool("piptool", ["up"])
        assert code == 0
        out = capsys.readouterr().out
        assert "升级完成" in out
