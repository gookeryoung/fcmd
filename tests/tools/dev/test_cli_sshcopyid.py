"""sshcopyid 工具测试（普通 Python 工具模块 + infras/dev 纯逻辑）。

覆盖：
- 工具注册 / 参数解析
- 公钥不存在时直接返回（SKIPPED 可观察行为，退出码 0）
- Linux：sshpass -e + SSHPASS 环境变量 + 公钥文件重定向
- Windows：plink / OpenSSH 客户端探测与命令重写
- 失败提示与成功提示
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import fcmd as fx
from fcmd.cli._discovery import ensure_tools_discovered
from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.infras import dev as dev_infra

ensure_tools_discovered()


def _fake_run_factory(captured: list[tuple[Any, dict[str, Any]]], returncode: int = 0):
    def fake_run(cmd: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    return fake_run


def _key_file(tmp_path: Path) -> Path:
    key = tmp_path / "id_rsa.pub"
    key.write_text("ssh-rsa AAAA test\n", encoding="utf-8")
    return key


class TestSshcopyidRegistration:
    """sshcopyid 注册验证。"""

    def test_registered_flat_tool(self) -> None:
        assert "sshcopyid" in _TOOL_REGISTRY
        assert fx.list_subcommands("sshcopyid") == []


class TestSshcopyidMissingKey:
    """公钥缺失行为。"""

    def test_missing_keypath_returns_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.sshcopyid.subprocess.run", _fake_run_factory(captured))
        key = tmp_path / "missing.pub"
        assert (
            run_tool(
                "sshcopyid",
                ["host.example.com", "root", "pass", "--keypath", str(key)],
            )
            == 0
        )
        assert captured == []
        out = capsys.readouterr().out
        assert "部署" not in out


class TestSshcopyidLinux:
    """Linux 分支：sshpass。"""

    def test_linux_uses_sshpass_env(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        key = _key_file(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.sshcopyid.subprocess.run", _fake_run_factory(captured))
        assert (
            run_tool(
                "sshcopyid",
                [
                    "host.example.com",
                    "root",
                    "secret",
                    "--port",
                    "2222",
                    "--keypath",
                    str(key),
                    "--timeout",
                    "15",
                ],
            )
            == 0
        )
        cmd, kwargs = captured[0]
        assert "sshpass -e ssh -p 2222" in cmd
        assert "ConnectTimeout=15" in cmd
        assert "root@host.example.com" in cmd
        assert str(key) in cmd
        assert kwargs["shell"] is True
        assert kwargs["env"]["SSHPASS"] == "secret"
        assert kwargs["capture_output"] is True
        assert "SSH 密钥已部署到 root@host.example.com:2222" in capsys.readouterr().out

    def test_linux_expands_tilde_keypath(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        key = tmp_path / ".ssh" / "id_rsa.pub"
        key.parent.mkdir()
        key.write_text("ssh-rsa AAAA test\n", encoding="utf-8")
        monkeypatch.setenv("HOME", str(tmp_path))
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.sshcopyid.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host.example.com", "root", "secret"]) == 0
        assert str(key) in captured[0][0]

    def test_linux_failure_prints_fallback(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        key = _key_file(tmp_path)
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr(
            "fcmd.tools.dev.sshcopyid.subprocess.run",
            _fake_run_factory(captured, returncode=1),
        )
        assert (
            run_tool(
                "sshcopyid",
                ["host.example.com", "root", "secret", "--port", "2222", "--keypath", str(key)],
            )
            == 1
        )
        out = capsys.readouterr().out
        assert "部署失败，可手动执行: ssh-copy-id -p 2222 root@host.example.com" in out
        assert "SSH 密钥已部署" not in out


class TestSshcopyidWindows:
    """Windows 分支：客户端探测与命令重写。"""

    def test_windows_plink_rewrite(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        key = _key_file(tmp_path)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            dev_infra,
            "resolve_windows_ssh_client",
            lambda: ("plink", r"C:\Tools\plink.exe"),
        )
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.sshcopyid.subprocess.run", _fake_run_factory(captured))
        assert (
            run_tool(
                "sshcopyid",
                ["host.example.com", "root", "secret", "--port", "2222", "--keypath", str(key)],
            )
            == 0
        )
        cmd, kwargs = captured[0]
        assert cmd.startswith(r"C:\Tools\plink.exe -ssh -pw secret -batch")
        assert " -P 2222" in cmd
        assert kwargs["shell"] is True
        # Windows 分支不捕获输出（tty 透传）
        assert "capture_output" not in kwargs
        assert "SSH 密钥已部署到 root@host.example.com:2222" in capsys.readouterr().out

    def test_windows_openssh_path_prefix(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        key = _key_file(tmp_path)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(
            dev_infra,
            "resolve_windows_ssh_client",
            lambda: ("openssh", r"C:\Windows\System32\OpenSSH\ssh.exe"),
        )
        captured: list[tuple[Any, dict[str, Any]]] = []
        monkeypatch.setattr("fcmd.tools.dev.sshcopyid.subprocess.run", _fake_run_factory(captured))
        assert run_tool("sshcopyid", ["host.example.com", "root", "secret", "--keypath", str(key)]) == 0
        assert captured[0][0].startswith(r"C:\Windows\System32\OpenSSH\ssh.exe -p 22")


class TestWindowsSshDetection:
    """infras/dev 中的探测与改写纯逻辑。"""

    def test_non_windows_detection_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        dev_infra.resolve_windows_openssh.cache_clear()
        dev_infra.resolve_windows_plink.cache_clear()
        dev_infra.resolve_windows_ssh_client.cache_clear()
        try:
            assert dev_infra.resolve_windows_openssh() is None
            assert dev_infra.resolve_windows_plink() is None
            assert dev_infra.resolve_windows_ssh_client() is None
        finally:
            dev_infra.resolve_windows_openssh.cache_clear()
            dev_infra.resolve_windows_plink.cache_clear()
            dev_infra.resolve_windows_ssh_client.cache_clear()

    def test_rewrite_plink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev_infra, "resolve_windows_ssh_client", lambda: ("plink", "plink.exe"))
        rewritten = dev_infra.rewrite_ssh_cmd(
            "ssh -p {port} -o StrictHostKeyChecking=no root@host < key", platform="win32"
        )
        assert rewritten == "plink.exe -ssh -pw {password} -batch -P {port} root@host < key"

    def test_rewrite_openssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev_infra, "resolve_windows_ssh_client", lambda: ("openssh", r"C:\ssh.exe"))
        rewritten = dev_infra.rewrite_ssh_cmd("ssh -p {port} root@host", platform="win32")
        assert rewritten == r"C:\ssh.exe -p {port} root@host"

    def test_rewrite_no_client_keeps_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev_infra, "resolve_windows_ssh_client", lambda: None)
        assert dev_infra.rewrite_ssh_cmd("ssh -p {port} root@host", platform="win32") == "ssh -p {port} root@host"

    def test_rewrite_ignores_non_win32(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert dev_infra.rewrite_ssh_cmd("ssh -p {port} root@host", platform="linux") == "ssh -p {port} root@host"


class TestExpandAndFilter:
    """infras/dev 的 pip 展开/过滤纯逻辑。"""

    def test_expand_without_wildcard_does_not_probe_pip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom() -> list[str]:
            raise AssertionError("不应调用 pip list")

        monkeypatch.setattr(dev_infra, "pip_installed_packages", boom)
        assert dev_infra.expand_packages(["requests", "flask"]) == ["requests", "flask"]

    def test_expand_wildcard_is_case_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            dev_infra,
            "pip_installed_packages",
            lambda: ["Requests", "requests-toolbelt", "flask"],
        )
        assert dev_infra.expand_packages(["requests*"]) == ["Requests", "requests-toolbelt"]

    def test_filter_protected(self) -> None:
        kept, skipped = dev_infra.filter_protected(["fcmd", "requests", "FCMD"])
        assert kept == ["requests"]
        assert skipped == ["fcmd", "FCMD"]
