"""sshcopyid 工具测试。

验证 ``fcmd.cli.sshcopyid`` 模块：
- 工具注册
- ssh_copy_id 部署 SSH 公钥
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
import fcmd.cli.sshcopyid
from fcmd.apis.toolkit import _TOOL_REGISTRY
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助
# ============================================================================ #
def _fail_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回失败结果的 fake ``run_command`` 函数。"""
    return CommandResult(cmd=list(cmd), returncode=1, stdout="", stderr="error")


def _recording_run(calls: list[list[str]]) -> Any:
    """创建记录调用的 fake ``run_command`` 函数，返回成功结果。"""

    def run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
        calls.append(cmd)
        return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")

    return run


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """sshcopyid 工具注册验证。"""

    def test_all_tools_registered(self) -> None:
        """sshcopyid 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("sshcopyid",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_sshcopyid_single_command(self) -> None:
        """sshcopyid 是单命令工具。"""
        assert fx.list_subcommands("sshcopyid") == []


# ============================================================================ #
# sshcopyid 测试
# ============================================================================ #
class TestSshCopyId:
    """sshcopyid 工具测试。"""

    def test_pubkey_not_exists(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        """公钥文件不存在时打印提示。"""
        fcmd.cli.sshcopyid.ssh_copy_id(
            hostname="host", username="user", password="pass", keypath="/nonexistent/key.pub"
        )
        captured = capsys.readouterr()
        assert "公钥文件不存在" in captured.out

    def test_success_deploy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """成功部署公钥。"""
        key_file = tmp_path / "id_rsa.pub"
        key_file.write_text("ssh-rsa AAAAB3NzaC1yc2E test@example.com\n")

        calls: list[list[str]] = []
        monkeypatch.setattr("fcmd.cli.sshcopyid.run_command", _recording_run(calls))

        fcmd.cli.sshcopyid.ssh_copy_id(hostname="host", username="user", password="pass", keypath=str(key_file))
        captured = capsys.readouterr()
        assert "已部署" in captured.out
        assert len(calls) == 1
        assert "sshpass" in calls[0][0]

    def test_failed_deploy(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """部署失败时提示手动执行。"""
        key_file = tmp_path / "id_rsa.pub"
        key_file.write_text("ssh-rsa AAAAB3NzaC1yc2E test@example.com\n")
        monkeypatch.setattr("fcmd.cli.sshcopyid.run_command", _fail_run)

        fcmd.cli.sshcopyid.ssh_copy_id(hostname="host", username="user", password="pass", keypath=str(key_file))
        captured = capsys.readouterr()
        assert "手动执行" in captured.out
