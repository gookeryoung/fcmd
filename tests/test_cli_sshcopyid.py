"""sshcopyid 工具测试（DSL 声明 commands/sshcopyid.toml）。

验证 ``fcmd sshcopyid`` 的 DSL 迁移语义：
- 工具注册（单命令 DSL 工具）与 when/message/fail_message 函数属性契约
- env 插值传递 SSHPASS（密码经环境变量，不进命令行 argv）
- {keypath:content} 文件内容插值嵌入远端脚本（去首尾空白）
- when 路径探针：公钥缺失时跳过执行且不打印完成消息
- 失败打印 fail_message 提示手动执行且退出码非零（行为变化：原版恒 exit 0）
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()  # 幂等：注册内置 DSL 命令（含 sshcopyid）


# ---------------------------------------------------------------------- #
# 测试辅助
# ---------------------------------------------------------------------- #
def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    """构造捕获 cmd 与 kwargs 的 subprocess.run 替身。"""

    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


def _write_key(tmp_path: Path, content: str = "ssh-rsa AAAAB3NzaC1yc2E test@example.com\n") -> Path:
    """写入测试公钥文件。"""
    key_file = tmp_path / "id_rsa.pub"
    key_file.write_text(content, encoding="utf-8")
    return key_file


# ---------------------------------------------------------------------- #
# 注册与声明契约
# ---------------------------------------------------------------------- #
class TestSshcopyidRegistration:
    """sshcopyid 经内置 DSL 注册。"""

    def test_registered_as_dsl(self) -> None:
        """sshcopyid 注册为内置 DSL 单命令工具。"""
        assert "sshcopyid" in _TOOL_REGISTRY
        assert fx.list_subcommands("sshcopyid") == []

    def test_decl_contract(self) -> None:
        """when 探针 / 完成 / 失败消息经函数属性注入。"""
        spec = _TOOL_REGISTRY["sshcopyid"][None]
        when = getattr(spec.func, "__dsl_when__", None)
        assert when is not None and when.path == "{keypath}" and when.expect == "exists"
        assert getattr(spec.func, "__dsl_message__", "") == "SSH 密钥已部署到 {username}@{hostname}:{port}"
        assert (
            getattr(spec.func, "__dsl_fail_message__", "")
            == "部署失败，可手动执行: ssh-copy-id -p {port} {username}@{hostname}"
        )
        assert spec.env == {"SSHPASS": "{password}"}


# ---------------------------------------------------------------------- #
# 执行语义
# ---------------------------------------------------------------------- #
class TestSshcopyidRun:
    """``fcmd sshcopyid`` 执行语义。"""

    def test_success_deploys_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """成功部署：密码走 SSHPASS 环境变量，公钥内容嵌入远端脚本，打印完成消息。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        code = run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file)])
        assert code == 0
        cmd = captured[0][0]
        assert cmd[:2] == ["sshpass", "-e"]
        assert "pass" not in cmd  # 密码不进 argv
        assert cmd[3] == "-p" and cmd[4] == "22"
        assert cmd[-2] == "user@host"
        # 公钥内容插值进远端脚本（去首尾空白、按 key body 判重）
        script = cmd[-1]
        assert "ssh-rsa AAAAB3NzaC1yc2E test@example.com" in script
        assert "grep -qF" in script and "awk '{print $2}'" in script
        # SSHPASS 经 env 传递
        assert captured[0][1]["env"]["SSHPASS"] == "pass"
        assert "SSH 密钥已部署到 user@host:22" in capsys.readouterr().out

    def test_content_stripped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """公钥文件首尾空白被去除（echo 追加不带入换行导致的引号断裂）。"""
        key_file = _write_key(tmp_path, content="  ssh-rsa AAAAB3 key\n\n")
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file)]) == 0
        assert "echo 'ssh-rsa AAAAB3 key' >> authorized_keys" in captured[0][0][-1]

    def test_custom_port_and_timeout(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--port / --timeout 插值进命令行。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert (
            run_tool(
                "sshcopyid", ["host", "user", "pass", "--port", "2222", "--timeout", "5", "--keypath", str(key_file)]
            )
            == 0
        )
        cmd = captured[0][0]
        assert cmd[4] == "2222"
        assert "ConnectTimeout=5" in cmd
        assert "SSH 密钥已部署到 user@host:2222" in capsys.readouterr().out

    def test_missing_key_skipped(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """公钥缺失：when 探针跳过执行，退出码 0，不打印完成/失败消息。"""
        missing = tmp_path / "no-such-key.pub"
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(missing)]) == 0
        assert captured == []
        out = capsys.readouterr().out
        assert "已部署" not in out
        assert "手动执行" not in out

    def test_failure_prints_fail_message(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """部署失败：退出码 1，打印手动执行提示（行为变化：原版恒 exit 0）。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured, returncode=1))
        assert run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file)]) == 1
        out = capsys.readouterr().out
        assert "部署失败，可手动执行: ssh-copy-id -p 22 user@host" in out
        assert "SSH 密钥已部署" not in out

    def test_dry_run_skips_execution(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """--dry-run 不执行命令、不打印消息。"""
        key_file = _write_key(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.engine.task_command.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host", "user", "pass", "--keypath", str(key_file), "--dry-run"]) == 0
        assert captured == []
        assert "已部署" not in capsys.readouterr().out
