"""piptool 工具测试（普通 Python 工具模块）。

覆盖：
- 工具注册 / 别名校验
- ``i`` / ``up`` 纯 exec 子命令
- ``d`` 离线参数
- ``f`` 导出依赖
- ``u`` 通配符展开 + 受保护包过滤
- ``r`` 隐藏链 ``_uninstall`` → ``_install`` 的卸载/安装顺序
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import fcmd as fx
from fcmd.cli._discovery import _TOOL_ALIASES, ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool

ensure_tools_discovered()


def _fake_run(captured: list[Any], returncode: int = 0) -> Any:
    def run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return run


def _patch_tool_run(monkeypatch: pytest.MonkeyPatch, captured: list[Any], returncode: int = 0) -> None:
    monkeypatch.setattr("fcmd.tools.dev.piptool.subprocess.run", _fake_run(captured, returncode=returncode))


class TestPiptoolRegistration:
    """piptool 注册验证。"""

    def test_registered(self) -> None:
        assert "piptool" in _TOOL_REGISTRY

    def test_subcommands(self) -> None:
        assert set(fx.list_subcommands("piptool", include_hidden=True)) == {
            "i",
            "up",
            "d",
            "f",
            "u",
            "r",
            "_uninstall",
            "_install",
        }

    def test_alias(self) -> None:
        assert _TOOL_ALIASES["pipt"] == "piptool"

    def test_hidden_chain_flags(self) -> None:
        hidden = set(fx.list_subcommands("piptool", include_hidden=True)) - set(fx.list_subcommands("piptool"))
        assert hidden == {"_uninstall", "_install"}


class TestPiptoolInstall:
    """``i`` / ``up`` 纯 exec。"""

    def test_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run(captured))
        assert run_tool("piptool", ["i", "requests", "flask"]) == 0
        assert captured[0] == ["pip", "install", "requests", "flask"]

    def test_upgrade_pip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        monkeypatch.setattr("fcmd.core.execution.task_command.subprocess.run", _fake_run(captured))
        assert run_tool("piptool", ["up"]) == 0
        assert captured[0] == ["python", "-m", "pip", "install", "--upgrade", "pip"]


class TestPiptoolDownload:
    """``d`` 下载语义。"""

    def test_download_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["d", "requests"]) == 0
        assert captured[0] == ["pip", "download", "requests", "-d", "packages"]

    def test_download_offline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["d", "requests", "--offline"]) == 0
        assert captured[0] == [
            "pip",
            "download",
            "requests",
            "-d",
            "packages",
            "--no-index",
            "--find-links",
            ".",
        ]


class TestPiptoolFreeze:
    """``f`` 导出依赖。"""

    def test_freeze(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["f"]) == 0
        assert len(captured) == 1
        assert "pip freeze --exclude-editable > requirements.txt" in captured[0]
        assert "依赖已导出到 requirements.txt" in capsys.readouterr().out

    def test_freeze_failure_no_message(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured, returncode=1)
        assert run_tool("piptool", ["f"]) == 1
        assert "依赖已导出" not in capsys.readouterr().out


class TestPiptoolUninstall:
    """``u`` 通配符展开 + 受保护包过滤。"""

    def test_concrete_packages(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["u", "requests", "flask"]) == 0
        assert captured[0] == ["pip", "uninstall", "-y", "requests", "flask"]

    def test_wildcard_expand(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        monkeypatch.setattr(
            "fcmd.infras.dev.pip_installed_packages",
            lambda: ["requests", "requests-toolbelt", "flask"],
        )
        assert run_tool("piptool", ["u", "requests*"]) == 0
        assert captured[0] == [
            "pip",
            "uninstall",
            "-y",
            "requests",
            "requests-toolbelt",
        ]

    def test_wildcard_no_match_skips(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        monkeypatch.setattr("fcmd.infras.dev.pip_installed_packages", lambda: ["flask"])
        assert run_tool("piptool", ["u", "requests*"]) == 0
        assert captured == []

    def test_protected_package_skipped(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["u", "fcmd", "requests"]) == 0
        assert captured[0] == ["pip", "uninstall", "-y", "requests"]
        assert "跳过受保护的包: fcmd" in capsys.readouterr().out

    def test_all_protected_skips(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["u", "fcmd"]) == 0
        assert captured == []
        assert "跳过受保护的包: fcmd" in capsys.readouterr().out


class TestPiptoolReinstall:
    """``r`` 隐藏链卸载 → 安装。"""

    def test_uninstall_then_install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["r", "requests", "flask"]) == 0
        assert captured == [
            ["pip", "uninstall", "-y", "requests", "flask"],
            ["pip", "install", "requests", "flask"],
        ]

    def test_reinstall_offline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["r", "requests", "--offline"]) == 0
        assert captured == [
            ["pip", "uninstall", "-y", "requests"],
            [
                "pip",
                "install",
                "requests",
                "--no-index",
                "--find-links",
                ".",
            ],
        ]

    def test_reinstall_all_protected_skips(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: list[Any] = []
        _patch_tool_run(monkeypatch, captured)
        assert run_tool("piptool", ["r", "fcmd"]) == 0
        assert captured == []
        assert capsys.readouterr().out.count("跳过受保护的包: fcmd") == 1
