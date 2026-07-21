"""dockercmd 工具测试。

验证 ``fcmd.cli.dockercmd`` 模块：
- 工具注册
- docker login 子命令（默认/自定义用户名/自定义仓库/失败分支）
"""

from __future__ import annotations

import pytest

import fcmd as fx
import fcmd.cli.dockercmd  # 触发 @fx.tool 注册
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.dockercmd import _DEFAULT_REGISTRY, docker_login
from fcmd.models import CommandResult


# ---------------------------------------------------------------------- #
# 测试辅助：构造 stub run_command
# ---------------------------------------------------------------------- #
def _make_result(returncode: int = 0, stdout: str = "", stderr: str = "") -> CommandResult:
    """构造 CommandResult 测试替身。"""
    return CommandResult(
        cmd=[],
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _stub_success(_cmd: list[str], **_kwargs: object) -> CommandResult:
    """始终成功的 run_command stub。"""
    return _make_result(returncode=0)


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """dockercmd 注册验证。"""

    def test_all_tools_registered(self) -> None:
        """dockercmd 应在 _TOOL_REGISTRY 中注册。"""
        assert "dockercmd" in _TOOL_REGISTRY

    def test_dockercmd_subcommands(self) -> None:
        """dockercmd 应有 login 子命令。"""
        subs = fx.list_subcommands("dockercmd")
        assert "login" in subs


# ---------------------------------------------------------------------- #
# dockercmd 工具测试
# ---------------------------------------------------------------------- #
class TestDockercmd:
    """``dockercmd`` 工具测试。"""

    def test_default_registry_is_tencent(self) -> None:
        """_DEFAULT_REGISTRY 为腾讯云镜像仓库。"""
        assert _DEFAULT_REGISTRY == "ccr.ccs.tencentyun.com"

    def test_login_success_with_default_username(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """docker_login 默认使用当前系统用户名登录腾讯云。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.dockercmd.run_command", fake_run)
        docker_login()
        out = capsys.readouterr().out
        assert "已登录镜像仓库" in out
        assert _DEFAULT_REGISTRY in out
        # 默认 registry 是腾讯云
        assert _DEFAULT_REGISTRY in captured["cmd"]
        # 默认 username = getpass.getuser()，应出现在 --username 后
        assert "--username" in captured["cmd"]
        idx = captured["cmd"].index("--username")
        assert captured["cmd"][idx + 1]  # 非空用户名

    def test_login_with_custom_username(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """docker_login 接受自定义 username。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.dockercmd.run_command", fake_run)
        docker_login(username="admin")
        out = capsys.readouterr().out
        assert "admin" in out
        idx = captured["cmd"].index("--username")
        assert captured["cmd"][idx + 1] == "admin"

    def test_login_with_custom_registry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """docker_login 接受自定义 registry。"""
        captured: dict[str, list[str]] = {}

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            captured["cmd"] = cmd
            return _make_result(returncode=0)

        monkeypatch.setattr("fcmd.cli.dockercmd.run_command", fake_run)
        docker_login(username="admin", registry="registry.example.com")
        out = capsys.readouterr().out
        assert "registry.example.com" in out
        assert "registry.example.com" in captured["cmd"]

    def test_login_failure_prints_message(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """docker_login 失败时打印失败消息。"""

        def fake_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
            return _make_result(returncode=1, stderr="Login Failed")

        monkeypatch.setattr("fcmd.cli.dockercmd.run_command", fake_run)
        docker_login(username="admin")
        out = capsys.readouterr().out
        assert "登录失败" in out

    def test_login_via_run_tool(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd dockercmd login 通过 run_tool 调用。"""
        monkeypatch.setattr("fcmd.cli.dockercmd.run_command", _stub_success)
        code = run_tool("dockercmd", ["login", "--username", "admin"])
        assert code == 0
        out = capsys.readouterr().out
        assert "已登录" in out
