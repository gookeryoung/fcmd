"""infras.dev 纯逻辑测试：pip 包展开/过滤与 Windows SSH 客户端探测。

探测函数带 ``lru_cache`` 且依赖 ``subprocess`` / ``Path.is_file``，这里用临时
目录造真实候选文件、用假 ``subprocess.run`` 模拟 ``where`` 与版本串，覆盖
OpenSSH / plink 的命中、跳过与异常分支。
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from fcmd.infras import dev

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _clear_probe_caches() -> Any:
    """探测函数带 lru_cache，用例前后清空避免互相污染。"""
    for func in (dev.resolve_windows_openssh, dev.resolve_windows_plink, dev.resolve_windows_ssh_client):
        func.cache_clear()
    yield
    for func in (dev.resolve_windows_openssh, dev.resolve_windows_plink, dev.resolve_windows_ssh_client):
        func.cache_clear()


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


class TestPipInstalledPackages:
    """``pip list --format=freeze`` 解析。"""

    def test_parses_freeze_output(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            calls.append(cmd)
            return _completed(stdout="requests==2.31.0\nflask==3.0.1\n\nnoise line\n")

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.pip_installed_packages() == ["requests", "flask"]
        assert calls == [["pip", "list", "--format=freeze"]]

    def test_raises_with_exit_code_and_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev.subprocess, "run", lambda *_args, **_kwargs: _completed(2, stderr=" boom "))
        with pytest.raises(RuntimeError, match="pip list 失败（退出码 2）: boom"):
            dev.pip_installed_packages()

    def test_expand_reuses_single_pip_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []

        def fake_list() -> list[str]:
            calls.append(True)
            return ["requests", "flask", "requests-toolbelt"]

        monkeypatch.setattr(dev, "pip_installed_packages", fake_list)
        assert dev.expand_packages(["req*", "fla*"]) == ["requests", "requests-toolbelt", "flask"]
        assert calls == [True]


class TestResolveWindowsOpenssh:
    """OpenSSH ssh.exe 探测。"""

    def test_prefers_standard_install_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
        ssh = _touch(tmp_path / "System32" / "OpenSSH" / "ssh.exe")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            if cmd[0] == "where":
                return _completed(returncode=1)
            return _completed(stderr="OpenSSH_for_Windows_9.5p1, LibreSSL 3.8.2")

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_openssh() == str(ssh)

    def test_scans_where_results(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path / "empty"))
        ssh = _touch(tmp_path / "bin" / "ssh.exe")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            if cmd[0] == "where":
                return _completed(stdout=f"  \n{ssh}\nnot-an-exe\n")
            return _completed(stderr="OpenSSH_8.9p1 Ubuntu-3ubuntu0.10")

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_openssh() == str(ssh)

    def test_skips_putty_version_string(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
        _touch(tmp_path / "System32" / "OpenSSH" / "ssh.exe")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            if cmd[0] == "where":
                return _completed(returncode=1)
            return _completed(stderr="PuTTY Release 0.80 (OpenSSH compatible)")

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_openssh() is None

    def test_skips_missing_candidate_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
        monkeypatch.setattr(dev.subprocess, "run", lambda *_args, **_kwargs: _completed(stdout="C:\\gone\\ssh.exe\n"))
        assert dev.resolve_windows_openssh() is None

    def test_survives_where_and_version_failures(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setenv("SYSTEMROOT", str(tmp_path))
        _touch(tmp_path / "System32" / "OpenSSH" / "ssh.exe")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            if cmd[0] == "where":
                raise FileNotFoundError("where 不存在")
            raise subprocess.TimeoutExpired(cmd, timeout=3)

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_openssh() is None


class TestResolveWindowsPlink:
    """plink.exe 探测。"""

    def test_returns_path_when_version_is_putty(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        plink = _touch(tmp_path / "PuTTY" / "plink.exe")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            if cmd[0] == "where":
                return _completed(stdout=f"{plink}\n")
            return _completed(stderr="plink: Release 0.80 (PuTTY)")

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_plink() == str(plink)

    def test_ignores_non_putty_version(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        plink = _touch(tmp_path / "plink.exe")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            if cmd[0] == "where":
                return _completed(stdout=f"{plink}\n")
            return _completed(stderr="OpenSSH_9.0")

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_plink() is None

    def test_survives_where_and_version_failures(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        _touch(tmp_path / "plink.exe")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            if cmd[0] == "where":
                return _completed(stdout=f"{tmp_path / 'plink.exe'}\n")
            raise subprocess.TimeoutExpired(cmd, timeout=3)

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_plink() is None

    def test_where_nonzero_exit_yields_no_candidate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(dev.subprocess, "run", lambda *_args, **_kwargs: _completed(returncode=1))
        assert dev.resolve_windows_plink() is None

    def test_where_output_filters_blank_and_non_exe_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        missing = r"C:\gone\plink.exe"
        monkeypatch.setattr(
            dev.subprocess,
            "run",
            lambda *_args, **_kwargs: _completed(stdout=f"\nnot-an-exe\n{missing}\n"),
        )
        assert dev.resolve_windows_plink() is None

    def test_where_missing_binary_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")

        def fake_run(cmd: list[str], **_kwargs: Any) -> SimpleNamespace:
            raise FileNotFoundError("where 不存在")

        monkeypatch.setattr(dev.subprocess, "run", fake_run)
        assert dev.resolve_windows_plink() is None


class TestResolveWindowsSshClient:
    """plink 优先、OpenSSH 兜底的组合选择。"""

    def test_prefers_plink(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev, "resolve_windows_plink", lambda: r"C:\Tools\plink.exe")
        monkeypatch.setattr(dev, "resolve_windows_openssh", lambda: r"C:\Windows\ssh.exe")
        assert dev.resolve_windows_ssh_client() == ("plink", r"C:\Tools\plink.exe")

    def test_falls_back_to_openssh(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev, "resolve_windows_plink", lambda: None)
        monkeypatch.setattr(dev, "resolve_windows_openssh", lambda: r"C:\Windows\ssh.exe")
        assert dev.resolve_windows_ssh_client() == ("openssh", r"C:\Windows\ssh.exe")

    def test_returns_none_when_both_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(dev, "resolve_windows_plink", lambda: None)
        monkeypatch.setattr(dev, "resolve_windows_openssh", lambda: None)
        assert dev.resolve_windows_ssh_client() is None


class TestRewriteSshCmd:
    """命令模板改写。"""

    def test_ignores_non_ssh_prefix(self) -> None:
        assert dev._rewrite_openssh_cmd_as_plink("scp a b", "plink.exe") == "scp a b"

    def test_keeps_non_string_commands(self) -> None:
        command = ("ssh", "root@host")
        assert dev.rewrite_ssh_cmd(command, platform="win32") is command
