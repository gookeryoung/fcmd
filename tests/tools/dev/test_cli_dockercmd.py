"""dockercmd 工具测试（普通 Python 工具模块）。

验证 ``fcmd dockercmd login``：
- 工具注册与子命令
- 默认仓库 / 自定义用户名 / 自定义仓库
- 用户名环境变量回退链（USERNAME/LOGNAME/USER/LNAME）
- tty 透传（fn 内 subprocess 不捕获输出）
- 失败退出码非零且不打印完成消息
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import fcmd as fx
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool

ensure_tools_discovered()


def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


def _clear_username_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("USERNAME", "LOGNAME", "USER", "LNAME"):
        monkeypatch.delenv(name, raising=False)


class TestDockercmdRegistration:
    """dockercmd 注册验证。"""

    def test_registered(self) -> None:
        assert "dockercmd" in _TOOL_REGISTRY
        assert fx.list_subcommands("dockercmd") == ["login"]


class TestDockercmdLogin:
    """``dockercmd login`` 执行语义。"""

    def test_login_success_with_env_username(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setenv("USERNAME", "envuser")
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.dockercmd.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login"]) == 0
        assert captured[0][0] == [
            "docker",
            "login",
            "--username",
            "envuser",
            "ccr.ccs.tencentyun.com",
        ]
        # tty 透传：不捕获输出
        assert "capture_output" not in captured[0][1]
        assert "已登录镜像仓库: ccr.ccs.tencentyun.com (用户: envuser)" in capsys.readouterr().out

    def test_login_env_fallback_chain(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _clear_username_env(monkeypatch)
        monkeypatch.setenv("USER", "posixuser")
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.dockercmd.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login"]) == 0
        assert captured[0][0][3] == "posixuser"

    def test_login_env_all_missing_keeps_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _clear_username_env(monkeypatch)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.dockercmd.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login"]) == 0
        assert captured[0][0][3] == ""

    def test_login_explicit_username_skips_env(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _clear_username_env(monkeypatch)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.dockercmd.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login", "--username", "admin"]) == 0
        assert captured[0][0][3] == "admin"
        assert "已登录镜像仓库" in capsys.readouterr().out

    def test_login_custom_registry(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.dockercmd.subprocess.run", _fake_run_factory(captured))
        assert (
            run_tool(
                "dockercmd",
                ["login", "--username", "admin", "--registry", "registry.example.com"],
            )
            == 0
        )
        assert captured[0][0][4] == "registry.example.com"

    def test_login_failure_exit_nonzero_without_message(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr(
            "fcmd.tools.dev.dockercmd.subprocess.run",
            _fake_run_factory(captured, returncode=1),
        )
        assert run_tool("dockercmd", ["login", "--username", "admin"]) == 1
        assert "已登录" not in capsys.readouterr().out

    def test_login_dry_run_skips_execution(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.dockercmd.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login", "--username", "admin", "--dry-run"]) == 0
        assert captured == []
        assert "已登录" not in capsys.readouterr().out
