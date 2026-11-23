"""wslmanw「迁移 SSH 文件」纯逻辑测试：扫描分类、探测解析、写入脚本与权限口径。"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from fcmd.apps.wslman import _sshfiles
from fcmd.apps.wslman._errors import WslmanError


def _encoded(name: str) -> str:
    return base64.b64encode(name.encode("utf-8")).decode("ascii")


def test_ssh_file_metadata_and_defaults() -> None:
    private = _sshfiles.SshFile("id_ed25519", 12, "private")
    assert private.mode == 0o600
    assert private.mode_text == "600"
    assert private.kind_label == "私钥"
    assert private.size_text == "12 B"
    assert private.checked_by_default is True
    assert _sshfiles.SshFile("id_ed25519.pub", 2048, "public").size_text == "2.0 KB"
    assert _sshfiles.SshFile("config", 3 * 1024 * 1024, "config").size_text == "3.0 MB"
    assert _sshfiles.SshFile("notes.txt", 1, "other").mode == 0o644
    assert _sshfiles.SshFile("notes.txt", 1, "other").checked_by_default is False
    assert _sshfiles.kind_label("unknown-kind") == "unknown-kind"


def test_remote_target_helpers() -> None:
    empty = _sshfiles.SshTarget()
    assert empty.exists is False
    assert empty.names() == ()
    assert empty.find("config") is None
    target = _sshfiles.SshTarget(
        user="me",
        home="/home/me",
        dir="/home/me/.ssh",
        dir_mode="700",
        files=(_sshfiles.RemoteFile("config", "644"), _sshfiles.RemoteFile("id_rsa", "600")),
    )
    assert target.exists is True
    assert target.names() == ("config", "id_rsa")
    assert target.find("config") == _sshfiles.RemoteFile("config", "644")
    assert target.find("missing") is None


def test_remote_source_helpers_describe_windows_and_wsl() -> None:
    """来源抽象：Windows 用本地目录，WSL 带实例名与实际读取路径。"""
    windows = _sshfiles.SshSource(_sshfiles.SOURCE_WINDOWS, "", r"C:\Users\me\.ssh")
    assert windows.is_wsl is False
    assert windows.label == "Windows 宿主"
    assert windows.display_path() == r"C:\Users\me\.ssh"
    assert _sshfiles.source_label(_sshfiles.SOURCE_WSL, "Debian") == "WSL 实例 · Debian"
    assert _sshfiles.source_label(_sshfiles.SOURCE_WSL) == "WSL 实例"

    remote = _sshfiles.SshSource(_sshfiles.SOURCE_WSL, "Debian", "/home/me/.ssh")
    assert remote.is_wsl is True
    assert remote.label == "WSL 实例 · Debian"
    assert remote.display_path() == "Debian:/home/me/.ssh"
    assert _sshfiles.source_file_path(remote, "id_ed25519") == "/home/me/.ssh/id_ed25519"
    with pytest.raises(WslmanError) as missing:
        _sshfiles.source_file_path(_sshfiles.SshSource(_sshfiles.SOURCE_WSL, "Debian"), "id_ed25519")
    assert "没有 ~/.ssh" in missing.value.message


def test_remote_read_argv_uses_cat_stream_and_keeps_path_argument() -> None:
    """跨实例读取走 ``cat`` 文件流，私钥内容只出现在 stdout。"""
    assert _sshfiles.remote_read_argv("/home/me/.ssh/id_ed25519") == (
        "cat",
        "--",
        "/home/me/.ssh/id_ed25519",
    )


def test_remote_files_classifies_names_and_warns_without_content() -> None:
    target = _sshfiles.SshTarget(
        user="me",
        home="/home/me",
        dir="/home/me/.ssh",
        files=(
            _sshfiles.RemoteFile("known_hosts", "644", 40),
            _sshfiles.RemoteFile("id_ed25519.pub", "644", 80),
            _sshfiles.RemoteFile("id_rsa", "600", 1200),
            _sshfiles.RemoteFile("weird.ppk", "600", 900),
            _sshfiles.RemoteFile("notes.txt", "644", 1),
        ),
    )
    files = _sshfiles.remote_files(target)
    assert [item.name for item in files] == ["id_rsa", "id_ed25519.pub", "known_hosts", "notes.txt", "weird.ppk"]
    assert [item.kind for item in files] == ["private", "public", "known_hosts", "other", "other"]
    assert files[0].size_text == "1.2 KB"
    assert "未知类型" in files[3].warning
    assert "PuTTY" in files[4].warning
    assert files[3].checked_by_default is False


def test_default_source_dir_is_home_dot_ssh() -> None:
    assert _sshfiles.default_source_dir() == Path.home() / ".ssh"


def test_classify_by_name_and_content() -> None:
    assert _sshfiles.classify("known_hosts", b"") == "known_hosts"
    assert _sshfiles.classify("known_hosts.old", b"") == "known_hosts"
    assert _sshfiles.classify("config", b"") == "config"
    assert _sshfiles.classify("id_ed25519.pub", b"ssh-ed25519 AAAA") == "public"
    assert _sshfiles.classify("mykey", b"-----BEGIN OPENSSH PRIVATE KEY-----\n") == "private"
    assert _sshfiles.classify("id_rsa", b"") == "private"
    assert _sshfiles.classify("notes.txt", b"hello") == "other"
    assert _sshfiles.looks_private("id_ed25519") is True
    assert _sshfiles.looks_private("id_ed25519.pub") is False


def test_scan_source_classifies_sorts_and_skips(tmp_path: Path) -> None:
    (tmp_path / "id_ed25519").write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc")
    (tmp_path / "id_ed25519.pub").write_bytes(b"ssh-ed25519 AAAA me")
    (tmp_path / "known_hosts").write_bytes(b"github.com ssh-ed25519 AAAA")
    (tmp_path / "config").write_text(
        "Host github.com\n    IdentityFile C:\\Users\\me\\.ssh\\id_ed25519\n", encoding="utf-8"
    )
    (tmp_path / "notes.txt").write_text("plain", encoding="utf-8")
    (tmp_path / "putty.ppk").write_text("PuTTY-User-Key-File", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    files = _sshfiles.scan_source(tmp_path)
    assert [item.name for item in files] == [
        "id_ed25519",
        "id_ed25519.pub",
        "known_hosts",
        "config",
        "notes.txt",
        "putty.ppk",
    ]
    by_name = {item.name: item for item in files}
    assert by_name["id_ed25519"].kind == "private"
    assert by_name["config"].warning.startswith("配置里可能有 Windows 路径")
    assert by_name["putty.ppk"].warning.startswith("PuTTY")
    assert by_name["notes.txt"].warning == "未知类型，确认后再迁移"
    assert by_name["known_hosts"].warning == ""


def test_scan_source_empty_private_key_warns(tmp_path: Path) -> None:
    (tmp_path / "id_ed25519").write_bytes(b"")
    (tmp_path / "config").write_text("Host *\n  ServerAliveInterval 30\n", encoding="utf-8")
    files = {item.name: item for item in _sshfiles.scan_source(tmp_path)}
    assert files["id_ed25519"].kind == "private"
    assert files["id_ed25519"].warning == "文件为空"
    assert files["config"].warning == ""


def test_scan_source_missing_and_unreadable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(WslmanError) as missing:
        _sshfiles.scan_source(tmp_path / "nope")
    assert missing.value.code == "NOT_FOUND"

    with pytest.raises(WslmanError) as unreadable:
        monkeypatch.setattr(_sshfiles.Path, "iterdir", lambda self: (_ for _ in ()).throw(PermissionError("denied")))
        _sshfiles.scan_source(tmp_path)
    assert unreadable.value.code == "RUNTIME_ERROR"


def test_scan_source_skips_unreadable_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "id_ed25519").write_bytes(b"PRIVATE KEY")
    (tmp_path / "locked").write_bytes(b"secret")
    real_open = Path.open

    def fake_open(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "locked":
            raise PermissionError("denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_sshfiles.Path, "open", fake_open)
    assert [item.name for item in _sshfiles.scan_source(tmp_path)] == ["id_ed25519"]


def test_read_source_success_and_failure(tmp_path: Path) -> None:
    path = tmp_path / "config"
    path.write_bytes(b"Host *")
    assert _sshfiles.read_source(path) == b"Host *"
    with pytest.raises(WslmanError) as missing:
        _sshfiles.read_source(tmp_path / "missing")
    assert missing.value.code == "RUNTIME_ERROR"


def test_probe_script_is_read_only_and_lists_metadata() -> None:
    script = _sshfiles.probe_script()
    assert script.startswith("set +e")
    assert "WSLSSH_DIRMODE" in script
    assert "WSLSSH_FILE=" in script
    assert 'name="${file##*/}"' in script
    assert "base64" in script
    assert "apt-get" not in script


def test_parse_probe_reads_fields_and_files() -> None:
    text = "\n".join(
        (
            "Welcome to Ubuntu",
            "WSLSSH_USER=me",
            "WSLSSH_HOME=/home/me",
            "WSLSSH_DIR=/home/me/.ssh",
            "WSLSSH_DIRMODE=700",
            f"WSLSSH_FILE={_encoded('id_ed25519')}|600|411",
            f"WSLSSH_FILE={_encoded('my keys')}|644|99",
            "WSLSSH_FILE=!!!|bad|not-a-number",
            "WSLSSH_FILE=|600|1",
            "ignored=1",
        )
    )
    target = _sshfiles.parse_probe(text)
    assert target.user == "me"
    assert target.home == "/home/me"
    assert target.dir == "/home/me/.ssh"
    assert target.dir_mode == "700"
    assert target.files[0] == _sshfiles.RemoteFile("id_ed25519", "600", 411)
    assert target.files[1] == _sshfiles.RemoteFile("my keys", "644", 99)
    assert target.files[2] == _sshfiles.RemoteFile("!!!", "bad", 0)
    assert target.files[3] == _sshfiles.RemoteFile("", "600", 1)


def test_parse_probe_empty() -> None:
    assert _sshfiles.parse_probe("") == _sshfiles.SshTarget()


def test_conflicts_keep_requested_order() -> None:
    target = _sshfiles.SshTarget(
        dir="/home/me/.ssh",
        files=(_sshfiles.RemoteFile("config", "600"), _sshfiles.RemoteFile("known_hosts", "644")),
    )
    assert _sshfiles.conflicts(["known_hosts", "id_ed25519", "config"], target) == ("known_hosts", "config")
    assert _sshfiles.conflicts([], target) == ()


def test_loose_modes_flags_only_private_keys() -> None:
    target = _sshfiles.SshTarget(
        dir="/home/me/.ssh",
        files=(
            _sshfiles.RemoteFile("id_rsa", "644"),
            _sshfiles.RemoteFile("id_ed25519", "600"),
            _sshfiles.RemoteFile("id_ecdsa", ""),
            _sshfiles.RemoteFile("config", "644"),
        ),
    )
    assert _sshfiles.loose_modes(target) == (_sshfiles.RemoteFile("id_rsa", "644"),)


def test_target_summary_covers_all_states() -> None:
    assert "尚未创建" in _sshfiles.target_summary(_sshfiles.SshTarget())
    empty = _sshfiles.SshTarget(user="me", dir="/home/me/.ssh", dir_mode="700")
    assert "为空目录" in _sshfiles.target_summary(empty)
    assert "目录权限 700" in _sshfiles.target_summary(empty)
    busy = _sshfiles.SshTarget(
        dir="/home/me/.ssh",
        files=(_sshfiles.RemoteFile("id_rsa", "644"), _sshfiles.RemoteFile("config", "600")),
    )
    summary = _sshfiles.target_summary(busy)
    assert "已有 2 个文件" in summary
    assert "先备份再覆盖" in summary
    assert "id_rsa（644）权限过宽" in summary


def test_backup_name_uses_timestamp() -> None:
    name = _sshfiles.backup_name(0)
    assert name.startswith(_sshfiles.BACKUP_PREFIX)
    assert re.fullmatch(r"fcmd-ssh-backup-\d{8}-\d{6}", name)


def test_migration_state_derives_from_scanned_files() -> None:
    files = (
        _sshfiles.SshFile("id_ed25519", 10, "private"),
        _sshfiles.SshFile("config", 5, "config"),
    )
    empty = _sshfiles.migration_state((), _sshfiles.SshTarget())
    assert (empty.label, empty.tone, empty.total) == ("无文件", "muted", 0)
    assert empty.done is False

    none = _sshfiles.migration_state(files, _sshfiles.SshTarget())
    assert (none.label, none.tone, none.matched, none.total) == ("未迁移", "muted", 0, 2)

    partial_target = _sshfiles.SshTarget(
        dir="/home/me/.ssh",
        files=(_sshfiles.RemoteFile("id_ed25519", "600", 10),),
    )
    partial = _sshfiles.migration_state(files, partial_target)
    assert (partial.label, partial.tone, partial.matched, partial.total) == ("部分迁移", "warning", 1, 2)
    assert "1/2" in partial.detail

    # 同名但大小不同，视为未到实例。
    wrong_size = _sshfiles.SshTarget(
        dir="/home/me/.ssh",
        files=(_sshfiles.RemoteFile("id_ed25519", "600", 11), _sshfiles.RemoteFile("config", "644", 5)),
    )
    assert _sshfiles.migration_state(files, wrong_size).label == "部分迁移"
    assert (
        _sshfiles.migration_state(
            files,
            _sshfiles.SshTarget(
                dir="/home/me/.ssh",
                files=(_sshfiles.RemoteFile("id_ed25519", "600", 11), _sshfiles.RemoteFile("config", "644", 6)),
            ),
        ).label
        == "未迁移"
    )

    done = _sshfiles.migration_state(
        files,
        _sshfiles.SshTarget(
            dir="/home/me/.ssh",
            files=(_sshfiles.RemoteFile("id_ed25519", "600", 10), _sshfiles.RemoteFile("config", "644", 5)),
        ),
    )
    assert (done.label, done.tone, done.matched, done.total) == (_sshfiles.DONE_LABEL, "success", 2, 2)
    assert done.done is True

    wrong_mode = _sshfiles.migration_state(
        files,
        _sshfiles.SshTarget(
            dir="/home/me/.ssh",
            files=(_sshfiles.RemoteFile("id_ed25519", "644", 10), _sshfiles.RemoteFile("config", "644", 5)),
        ),
    )
    assert (wrong_mode.label, wrong_mode.tone) == ("权限待修正", "warning")
    assert "id_ed25519" in wrong_mode.detail

    # 权限读不到（空串 / 非八进制）时不臆断权限错误，仍算已完成。
    unverifiable = _sshfiles.migration_state(
        files,
        _sshfiles.SshTarget(
            dir="/home/me/.ssh",
            files=(_sshfiles.RemoteFile("id_ed25519", "", 10), _sshfiles.RemoteFile("config", "bad", 5)),
        ),
    )
    assert unverifiable.label == _sshfiles.DONE_LABEL


def test_write_script_refuses_unconfirmed_overwrite_and_quotes_names() -> None:
    script = _sshfiles.write_script("my key", 0o600, overwrite=False, backup="fcmd-ssh-backup-1")
    assert '[ "0" -ne 1 ]' in script
    assert "exit 3" in script
    assert "target=\"$dir\"/'my key'" in script
    assert "chmod 600" in script
    assert 'cat > "$tmp"' in script
    assert "WSLSSH_WRITTEN=" in script


def test_write_script_backups_when_overwrite_confirmed() -> None:
    script = _sshfiles.write_script("id_ed25519", 0o600, overwrite=True, backup="fcmd-ssh-backup-1")
    assert '[ "1" -ne 1 ]' in script
    assert 'backup="$dir"/fcmd-ssh-backup-1' in script
    assert "已备份原有文件" in script
    assert "trap" in script
    assert "chmod 644" not in script


def test_parse_write_result_reads_last_marker() -> None:
    text = "已写入 ~/.ssh/id_ed25519（权限 600）\nWSLSSH_WRITTEN=600|me\nWSLSSH_WRITTEN=640|me\n"
    assert _sshfiles.parse_write_result(text) == ("640", "me")
    assert _sshfiles.parse_write_result("no marker") == ("", "")
