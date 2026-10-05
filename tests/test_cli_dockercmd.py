"""dockercmd 工具测试（DSL 声明 commands/dockercmd.toml）。

验证 ``fcmd dockercmd login`` 的 DSL 迁移语义：
- 工具注册与子命令（tty 声明 → ToolSpec.passthrough）
- 默认仓库 / 自定义用户名 / 自定义仓库
- default_env 环境变量回退链（USERNAME/LOGNAME/USER/LNAME）
- 失败退出码非零且不打印完成消息（行为变化：原 Python 版失败打印
  「登录失败」且恒 exit 0，DSL 走引擎失败链路非零退出）
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

import fcmd as fx
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 dockercmd）


# ---------------------------------------------------------------------- #
# 测试辅助
# ---------------------------------------------------------------------- #
def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    """构造捕获 cmd 与 kwargs 的 subprocess.run 替身。"""

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


def _clear_username_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """清除用户名回退链的全部环境变量（CI/tox 环境变量不可控）。"""
    for name in ("USERNAME", "LOGNAME", "USER", "LNAME"):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestDockercmdRegistration:
    """dockercmd 经内置 DSL 注册。"""

    def test_registered_as_dsl(self) -> None:
        """dockercmd 注册为内置 DSL 工具。"""
        assert "dockercmd" in _TOOL_REGISTRY
        assert fx.list_subcommands("dockercmd") == ["login"]

    def test_tty_maps_to_passthrough(self) -> None:
        """tty = true 声明映射 ToolSpec.passthrough；default_env 注入函数属性。"""
        spec = _TOOL_REGISTRY["dockercmd"]["login"]
        assert spec.passthrough is True
        env_map = getattr(spec.func, "__dsl_param_env__", {})
        assert env_map == {"username": (("USERNAME", "LOGNAME", "USER", "LNAME"), "")}


# ---------------------------------------------------------------------- #
# login 执行语义
# ---------------------------------------------------------------------- #
class TestDockercmdLogin:
    """``dockercmd login`` 执行语义。"""

    def test_login_success_with_env_username(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """username 缺省回退 USERNAME，message 插值取解析后的值。"""
        monkeypatch.setenv("USERNAME", "envuser")
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        code = run_tool("dockercmd", ["login"])
        assert code == 0
        assert captured[0][0] == ["docker", "login", "--username", "envuser", "ccr.ccs.tencentyun.com"]
        assert "已登录镜像仓库: ccr.ccs.tencentyun.com (用户: envuser)" in capsys.readouterr().out

    def test_login_env_fallback_chain(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """回退链顺序：USERNAME 未设时依次 LOGNAME → USER。"""
        _clear_username_env(monkeypatch)
        monkeypatch.setenv("USER", "posixuser")
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login"]) == 0
        assert captured[0][0][3] == "posixuser"

    def test_login_env_all_missing_keeps_default(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """回退链全空时保持声明默认值（空串透传给 docker）。"""
        _clear_username_env(monkeypatch)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login"]) == 0
        assert captured[0][0][3] == ""

    def test_login_explicit_username_skips_env(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """显式 --username 优先，不查环境变量链。"""
        _clear_username_env(monkeypatch)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login", "--username", "admin"]) == 0
        assert captured[0][0][3] == "admin"
        assert "已登录镜像仓库" in capsys.readouterr().out

    def test_login_custom_registry(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """--registry 覆盖默认腾讯云仓库。"""
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login", "--username", "admin", "--registry", "registry.example.com"]) == 0
        assert captured[0][0][4] == "registry.example.com"

    def test_login_failure_exit_nonzero_without_message(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """docker 失败 → 退出码 1，不打印完成消息（行为变化：原版恒 exit 0）。"""
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured, returncode=1))
        assert run_tool("dockercmd", ["login", "--username", "admin"]) == 1
        assert "已登录" not in capsys.readouterr().out

    def test_login_dry_run_skips_execution(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--dry-run 不执行命令、不打印完成消息。"""
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("dockercmd", ["login", "--username", "admin", "--dry-run"]) == 0
        assert captured == []
        assert "已登录" not in capsys.readouterr().out
