"""wslmanw「迁移配置文件」的 WSL 适配测试：Windows 专用值不原样搬进实例。"""

from __future__ import annotations

import pytest

from fcmd.apps.wslman import _configadapt


def test_has_windows_path_detects_drives_unc_and_exe() -> None:
    assert _configadapt.has_windows_path(r"C:\Users\me\.ssh\id_ed25519")
    assert _configadapt.has_windows_path("C:/Program Files/OpenSSH/ssh.exe")
    assert _configadapt.has_windows_path(r"\\server\share\key")
    assert _configadapt.has_windows_path(r"\\.\pipe\openssh-ssh-agent")
    assert _configadapt.has_windows_path("cmd.exe /c echo hi")
    assert _configadapt.has_windows_path("powershell -Command x")
    assert not _configadapt.has_windows_path("~/.ssh/id_ed25519")
    assert not _configadapt.has_windows_path("/usr/bin/ssh")


def test_normalize_newlines_turns_crlf_and_cr_into_lf() -> None:
    assert _configadapt.normalize_newlines(b"a\r\nb\rc\n") == b"a\nb\nc\n"


def test_git_config_adapts_windows_ssh_and_helper() -> None:
    data = (
        b"[user]\r\n"
        b"\tname = me\r\n"
        b"[core]\r\n"
        b"\tsshCommand = C:/Program Files/OpenSSH/ssh.exe\r\n"
        b"\texcludesFile = C:\\\\Users\\\\me\\\\.gitignore_global\r\n"
        b"[credential]\r\n"
        b"\thelper = manager\r\n"
        b"[include]\r\n"
        b"\tpath = C:/Users/me/extra.gitconfig\r\n"
        b"[alias]\r\n"
        b"\twincmd = !cmd.exe /c echo hi\r\n"
    )
    result = _configadapt.adapt("git", ".gitconfig", data)
    text = result.data.decode("utf-8")
    assert "\r" not in text
    assert "sshCommand = ssh" in text
    assert "sshCommand = C:/Program Files/OpenSSH/ssh.exe" not in text
    assert "excludesFile = ~/.gitignore_global" in text
    assert "# fcmd: 已注释 Windows 专用配置：helper = manager" in text
    assert "# fcmd: 已注释 Windows 专用配置：path = C:/Users/me/extra.gitconfig" in text
    assert "# fcmd: 已注释 Windows 专用配置：wincmd = !cmd.exe /c echo hi" in text
    notes = "；".join(result.notes)
    assert "CRLF" in notes
    assert "core.sshCommand" in notes
    assert "credential.helper" in notes
    assert result.changed is True


def test_git_config_safe_values_stay_untouched() -> None:
    data = b"[user]\n\tname = me\n[core]\n\tsshCommand = ssh -i ~/.ssh/work\n"
    result = _configadapt.adapt("git", ".gitconfig", data)
    assert result.data == data
    assert result.notes == ()
    assert result.changed is False


def test_ssh_config_rewrites_identity_and_drops_windows_agent() -> None:
    data = (
        b"Host github.com\r\n"
        b"  HostName github.com\r\n"
        b"  IdentityFile C:\\Users\\me\\.ssh\\github\r\n"
        b"  IdentityAgent \\\\.\\pipe\\openssh-ssh-agent\r\n"
        b"  ProxyCommand C:/Windows/System32/ssh.exe -W %h:%p\r\n"
        b"  UserKnownHostsFile C:/Users/me/.ssh/known_hosts\r\n"
        b"  Include C:/Users/me/.ssh/extra.conf\r\n"
    )
    result = _configadapt.adapt("ssh", ".ssh/config", data)
    text = result.data.decode("utf-8")
    assert "Host github.com" in text
    assert "IdentityFile ~/.ssh/github" in text
    assert "IdentityAgent" not in text.split("\n")[0]
    assert "# fcmd: 已注释 Windows 专用配置：IdentityAgent" in text
    assert "# fcmd: 已注释 Windows 专用配置：ProxyCommand" in text
    assert "UserKnownHostsFile ~/.ssh/known_hosts" in text
    assert "# fcmd: 已注释 Windows 专用配置：Include" in text
    assert "  ProxyCommand C:/Windows/System32/ssh.exe" not in text
    assert "  Include C:" not in text
    assert result.changed is True


def test_ssh_config_safe_values_stay_untouched() -> None:
    data = b"Host work\n  IdentityFile ~/.ssh/id_ed25519\n  HostName example.com\n"
    result = _configadapt.adapt("ssh", ".ssh/config", data)
    assert result.data == data
    assert result.notes == ()


def test_shell_config_only_normalizes_newlines() -> None:
    result = _configadapt.adapt("shell", ".bashrc", b"export A=1\r\nexport B=2\r\n")
    assert result.data == b"export A=1\nexport B=2\n"
    assert len(result.notes) == 1
    assert "CRLF" in result.notes[0]


def test_non_config_files_are_not_rewritten() -> None:
    data = b"-----BEGIN OPENSSH PRIVATE KEY-----\r\nabc"
    result = _configadapt.adapt("ssh", ".ssh/id_ed25519", data)
    assert result.data == data
    assert result.notes == ()


def test_adapt_notes_only_returns_messages() -> None:
    notes = _configadapt.adapt_notes(
        "git", ".gitconfig", b"[core]\r\n\tsshCommand = C:/Program Files/OpenSSH/ssh.exe\r\n"
    )
    assert any("core.sshCommand" in note for note in notes)
    assert any("CRLF" in note for note in notes)


@pytest.mark.parametrize("group", ["ssh", "git", "shell"])
def test_adapt_is_idempotent(group: str) -> None:
    data = b"[core]\n\tsshCommand = C:/Program Files/OpenSSH/ssh.exe\n"
    first = _configadapt.adapt(group, ".gitconfig", data)
    second = _configadapt.adapt(group, ".gitconfig", first.data)
    assert second.data == first.data
