"""wslmanw「迁移配置文件」纯逻辑测试：项目分类、扫描、探测、写入脚本与状态口径。"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from fcmd.apps.wslman import _configfiles
from fcmd.apps.wslman._errors import WslmanError


def _encoded(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def test_groups_cover_ssh_git_shell_and_managed_paths() -> None:
    keys = [group.key for group in _configfiles.GROUPS]
    assert keys == ["ssh", "git", "shell"]
    assert _configfiles.group_by_key("git") is not None
    assert _configfiles.group_by_key("missing") is None
    assert _configfiles.group_for_path(".ssh/id_ed25519") is not None
    assert _configfiles.group_for_path(".gitconfig").key == "git"  # type: ignore[union-attr]
    assert _configfiles.group_for_path(".config/git/config").key == "git"  # type: ignore[union-attr]
    assert _configfiles.group_for_path(".bashrc").key == "shell"  # type: ignore[union-attr]
    assert _configfiles.group_for_path(".ssh-backup/id") is None
    assert _configfiles.group_for_path("notes.txt") is None
    assert _configfiles.target_dir_mode(_configfiles.group_by_key("ssh")) == 0o700  # type: ignore[arg-type]
    assert _configfiles.target_dir_mode(_configfiles.group_by_key("git")) == 0o755  # type: ignore[arg-type]


def test_config_file_metadata_modes_and_labels() -> None:
    private = _configfiles.ConfigFile(".ssh/id_ed25519", 12, "ssh", "private")
    assert private.name == "id_ed25519"
    assert private.mode == 0o600
    assert private.mode_text == "600"
    assert private.kind_label == "私钥"
    assert private.size_text == "12 B"
    assert private.checked_by_default is True
    assert private.group_label == "SSH 文件"
    assert private.display == ".ssh/id_ed25519  ·  私钥  ·  12 B"
    assert _configfiles.ConfigFile(".ssh/id_ed25519.pub", 2048, "ssh", "public").size_text == "2.0 KB"
    assert _configfiles.ConfigFile(".gitconfig", 3 * 1024 * 1024, "git", "config").mode == 0o644
    assert _configfiles.ConfigFile(".gitconfig", 1, "git", "config").group_label == "Git 配置"
    assert _configfiles.ConfigFile("notes.txt", 1, "git", "other").checked_by_default is False
    assert _configfiles.ConfigFile(".gitconfig.bak", 1, "git", "config").checked_by_default is False


def test_default_source_dir_is_windows_home() -> None:
    assert _configfiles.default_source_dir() == Path.home()


def test_sources_describe_windows_and_wsl_and_build_paths() -> None:
    windows = _configfiles.ConfigSource(_configfiles.SOURCE_WINDOWS, "", r"C:\Users\me")
    assert windows.is_wsl is False
    assert windows.label == "Windows 宿主"
    assert windows.display_path() == r"C:\Users\me"
    assert _configfiles.source_label(_configfiles.SOURCE_WSL, "Debian") == "WSL 实例 · Debian"
    assert _configfiles.source_label(_configfiles.SOURCE_WSL) == "WSL 实例"

    remote = _configfiles.ConfigSource(_configfiles.SOURCE_WSL, "Debian", "/home/me")
    assert remote.is_wsl is True
    assert remote.label == "WSL 实例 · Debian"
    assert remote.display_path() == "Debian:/home/me"
    assert _configfiles.source_file_path(remote, ".ssh/id_ed25519") == "/home/me/.ssh/id_ed25519"
    with pytest.raises(WslmanError) as missing:
        _configfiles.source_file_path(_configfiles.ConfigSource(_configfiles.SOURCE_WSL, "Debian"), "x")
    assert "家目录" in missing.value.message
    assert _configfiles.remote_read_argv("/home/me/.gitconfig") == ("cat", "--", "/home/me/.gitconfig")


def test_classify_by_group_and_name() -> None:
    ssh = _configfiles.group_by_key("ssh")
    git = _configfiles.group_by_key("git")
    shell = _configfiles.group_by_key("shell")
    assert ssh is not None and git is not None and shell is not None
    assert _configfiles.classify(ssh, ".ssh/known_hosts", b"") == "known_hosts"
    assert _configfiles.classify(ssh, ".ssh/known_hosts.old", b"") == "other"
    assert _configfiles.classify(ssh, ".ssh/id_ed25519.pub", b"ssh-ed25519 AAAA") == "public"
    assert _configfiles.classify(ssh, ".ssh/mykey", b"-----BEGIN OPENSSH PRIVATE KEY-----\n") == "private"
    assert _configfiles.classify(ssh, ".ssh/id_rsa", b"") == "private"
    assert _configfiles.classify(ssh, ".ssh/notes.txt", b"hello") == "other"
    assert _configfiles.classify(git, ".gitconfig", b"[user]") == "config"
    assert _configfiles.classify(shell, ".bashrc", b"export A=1") == "config"
    assert _configfiles.classify(git, ".gitconfig.bak", b"") == "other"
    assert _configfiles.is_migratable_name(".ssh/id_ed25519") is True
    assert _configfiles.is_migratable_name(".ssh/id_rsa.bak") is False
    assert _configfiles.is_migratable_name(".DS_Store") is False


def test_scan_host_classifies_sorts_and_skips(tmp_path: Path) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_ed25519").write_bytes(b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc")
    (ssh_dir / "id_ed25519.pub").write_bytes(b"ssh-ed25519 AAAA me")
    (ssh_dir / "known_hosts").write_bytes(b"github.com ssh-ed25519 AAAA")
    (ssh_dir / "config").write_text(
        "Host github.com\n    IdentityFile C:\\Users\\me\\.ssh\\id_ed25519\n", encoding="utf-8"
    )
    (ssh_dir / "notes.txt").write_text("plain", encoding="utf-8")
    (ssh_dir / "putty.ppk").write_text("PuTTY-User-Key-File", encoding="utf-8")
    (ssh_dir / "nested").mkdir()
    (tmp_path / ".gitconfig").write_text("[user]\n\tname = me\n", encoding="utf-8")
    (tmp_path / ".bashrc").write_bytes(b"export A=1\n")

    files = _configfiles.scan_host(tmp_path)
    assert [item.path for item in files] == [
        ".ssh/id_ed25519",
        ".ssh/id_ed25519.pub",
        ".ssh/known_hosts",
        ".ssh/config",
        ".ssh/notes.txt",
        ".ssh/putty.ppk",
        ".gitconfig",
        ".bashrc",
    ]
    by_path = {item.path: item for item in files}
    assert by_path[".ssh/id_ed25519"].kind == "private"
    assert by_path[".ssh/config"].warning.startswith("迁移时会自动适配：")
    assert "IdentityFile" in by_path[".ssh/config"].warning
    assert by_path[".ssh/putty.ppk"].warning.startswith("PuTTY")
    assert by_path[".ssh/notes.txt"].warning == "未知类型，确认后再迁移"
    assert by_path[".ssh/known_hosts"].warning == ""
    assert by_path[".gitconfig"].group == "git"
    assert "Shell" in by_path[".bashrc"].warning


def test_scan_host_warns_when_windows_only_config_will_be_adapted(tmp_path: Path) -> None:
    """Windows 专用 sshCommand / CRLF 要在扫描阶段就说明迁移会自动适配。"""
    (tmp_path / ".gitconfig").write_text(
        "[core]\r\n\tsshCommand = C:/Program Files/OpenSSH/ssh.exe\n", encoding="utf-8"
    )
    files = _configfiles.scan_host(tmp_path)
    assert len(files) == 1
    warning = files[0].warning
    assert warning.startswith("迁移时会自动适配：")
    assert "core.sshCommand" in warning
    assert "CRLF" in warning
    # 仍然默认勾选：适配由迁移流程负责，不需要用户先手工改 Windows 侧文件。
    assert files[0].checked_by_default is True


def test_config_size_mismatch_is_ignored_after_adaptation() -> None:
    """配置文件写入前会被适配，大小本来就该变化；只有 SSH 密钥按大小比对。"""
    target = _configfiles.ConfigTarget(
        files=(
            _configfiles.RemoteFile(".gitconfig", "644", 10),
            _configfiles.RemoteFile(".ssh/id_ed25519", "600", 5),
        )
    )
    files = (
        _configfiles.ConfigFile(".gitconfig", 99, "git", "config"),
        _configfiles.ConfigFile(".ssh/id_ed25519", 10, "ssh", "private"),
    )
    state = _configfiles.migration_state(files, target)
    assert (state.label, state.matched, state.total) == ("部分迁移", 1, 2)


def test_scan_host_missing_directory_and_empty(tmp_path: Path) -> None:
    with pytest.raises(WslmanError) as missing:
        _configfiles.scan_host(tmp_path / "nope")
    assert missing.value.code == "NOT_FOUND"
    # 家目录存在但没有任何受管配置时返回空列表，不报错。
    assert _configfiles.scan_host(tmp_path) == ()


def test_scan_host_skips_unreadable_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_ed25519").write_bytes(b"PRIVATE KEY")
    (ssh_dir / "locked").write_bytes(b"secret")
    real_open = Path.open

    def fake_open(self: Path, *args: object, **kwargs: object) -> object:
        if self.name == "locked":
            raise PermissionError("denied")
        return real_open(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(_configfiles.Path, "open", fake_open)
    assert [item.path for item in _configfiles.scan_host(tmp_path)] == [".ssh/id_ed25519"]


def test_scan_host_reports_unreadable_ssh_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".ssh").mkdir()
    monkeypatch.setattr(_configfiles.Path, "iterdir", lambda self: (_ for _ in ()).throw(PermissionError("denied")))
    with pytest.raises(WslmanError) as unreadable:
        _configfiles.scan_host(tmp_path)
    assert unreadable.value.code == "RUNTIME_ERROR"


def test_scan_target_filters_managed_paths_and_classifies() -> None:
    target = _configfiles.ConfigTarget(
        user="me",
        home="/home/me",
        files=(
            _configfiles.RemoteFile(".ssh/known_hosts", "644", 40),
            _configfiles.RemoteFile(".ssh/id_rsa", "600", 1200),
            _configfiles.RemoteFile(".gitconfig", "644", 30),
            _configfiles.RemoteFile("notes.txt", "644", 1),
            _configfiles.RemoteFile(".ssh-backup/id_rsa", "600", 1),
        ),
        dirs={".ssh": "700"},
    )
    files = _configfiles.scan_target(target)
    assert [item.path for item in files] == [".ssh/id_rsa", ".ssh/known_hosts", ".gitconfig"]
    assert [item.kind for item in files] == ["private", "known_hosts", "config"]


def test_read_source_success_and_failure(tmp_path: Path) -> None:
    path = tmp_path / ".gitconfig"
    path.write_bytes(b"[user]")
    assert _configfiles.read_source(path) == b"[user]"
    with pytest.raises(WslmanError) as missing:
        _configfiles.read_source(tmp_path / "missing")
    assert missing.value.code == "RUNTIME_ERROR"


def test_probe_script_is_read_only_and_lists_managed_paths() -> None:
    script = _configfiles.probe_script()
    assert script.startswith("set +e")
    assert "WSLCFG_HOME" in script
    assert "WSLCFG_DIR=" in script
    assert "WSLCFG_FILE=" in script
    assert "emit_file .gitconfig" in script
    assert "base64" in script
    assert "apt-get" not in script


def test_parse_probe_reads_fields_files_and_dirs() -> None:
    text = "\n".join(
        (
            "Welcome to Ubuntu",
            "WSLCFG_USER=me",
            "WSLCFG_HOME=/home/me",
            f"WSLCFG_DIR={_encoded('.ssh')}|700",
            f"WSLCFG_FILE={_encoded('.ssh/id_ed25519')}|600|411",
            f"WSLCFG_FILE={_encoded('.gitconfig')}|644|99",
            "WSLCFG_FILE=!!!|bad|not-a-number",
            "WSLCFG_FILE=|600|1",
            "ignored=1",
        )
    )
    target = _configfiles.parse_probe(text)
    assert target.user == "me"
    assert target.home == "/home/me"
    assert target.dirs == {".ssh": "700"}
    assert target.files[0] == _configfiles.RemoteFile(".ssh/id_ed25519", "600", 411)
    assert target.files[1] == _configfiles.RemoteFile(".gitconfig", "644", 99)
    assert target.files[2] == _configfiles.RemoteFile("!!!", "bad", 0)
    assert target.files[3] == _configfiles.RemoteFile("", "600", 1)
    assert target.exists is True
    assert _configfiles.parse_probe("") == _configfiles.ConfigTarget()


def test_target_helpers_find_and_dir_mode() -> None:
    empty = _configfiles.ConfigTarget()
    assert empty.exists is False
    assert empty.paths() == ()
    assert empty.find(".gitconfig") is None
    target = _configfiles.ConfigTarget(
        home="/home/me",
        files=(_configfiles.RemoteFile(".gitconfig", "644"),),
        dirs={".ssh": "700"},
    )
    assert target.exists is True
    assert target.paths() == (".gitconfig",)
    assert target.find(".gitconfig") == _configfiles.RemoteFile(".gitconfig", "644")
    assert target.dir_mode(".ssh") == "700"
    assert target.dir_mode(".config") == ""


def test_conflicts_keep_requested_order() -> None:
    target = _configfiles.ConfigTarget(
        files=(
            _configfiles.RemoteFile(".gitconfig", "600"),
            _configfiles.RemoteFile(".ssh/known_hosts", "644"),
        )
    )
    assert _configfiles.conflicts([".ssh/known_hosts", ".gitconfig", ".bashrc"], target) == (
        ".ssh/known_hosts",
        ".gitconfig",
    )
    assert _configfiles.conflicts([], target) == ()


def test_loose_modes_flags_only_ssh_private_keys() -> None:
    target = _configfiles.ConfigTarget(
        files=(
            _configfiles.RemoteFile(".ssh/id_rsa", "644"),
            _configfiles.RemoteFile(".ssh/id_ed25519", "600"),
            _configfiles.RemoteFile(".ssh/id_ecdsa", ""),
            _configfiles.RemoteFile(".gitconfig", "644"),
        )
    )
    assert _configfiles.loose_modes(target) == (_configfiles.RemoteFile(".ssh/id_rsa", "644"),)


def test_target_summary_covers_all_states() -> None:
    assert "尚未找到" in _configfiles.target_summary(_configfiles.ConfigTarget())
    only_dir = _configfiles.ConfigTarget(home="/home/me", dirs={".ssh": "700"})
    assert "~/.ssh 目录权限 700" in _configfiles.target_summary(only_dir)
    assert "还没有配置文件" in _configfiles.target_summary(only_dir)
    busy = _configfiles.ConfigTarget(
        files=(
            _configfiles.RemoteFile(".ssh/id_rsa", "644"),
            _configfiles.RemoteFile(".gitconfig", "600"),
        )
    )
    summary = _configfiles.target_summary(busy)
    assert "已有 2 个文件" in summary
    assert "先备份再覆盖" in summary
    assert ".ssh/id_rsa（644）权限过宽" in summary


def test_group_progress_states_and_helpers() -> None:
    files = (
        _configfiles.ConfigFile(".ssh/id_ed25519", 10, "ssh", "private"),
        _configfiles.ConfigFile(".ssh/known_hosts", 5, "ssh", "known_hosts"),
        _configfiles.ConfigFile(".gitconfig", 3, "git", "config"),
        _configfiles.ConfigFile(".ssh/id_ed25519.bak", 99, "ssh", "other"),
    )
    target = _configfiles.ConfigTarget(
        files=(
            _configfiles.RemoteFile(".ssh/id_ed25519", "600", 10),
            _configfiles.RemoteFile(".gitconfig", "644", 3),
        )
    )
    progress = _configfiles.group_progress(files, target)
    assert progress["ssh"].matched == 1 and progress["ssh"].total == 2
    assert progress["git"].matched == 1 and progress["git"].total == 1
    assert progress["shell"].total == 0
    assert progress["shell"].tone == "muted" and progress["shell"].label == "无文件"
    assert progress["git"].tone == "success"
    assert progress["ssh"].tone == "warning"

    states = {state.key: state for state in _configfiles.group_states(files, target)}
    assert states["ssh"].label == "1/2"
    assert states["git"].label == "1/1"
    assert states["shell"].tone == "muted"
    assert "Windows 宿主没有该项目" in states["shell"].detail
    assert set(_configfiles.groups_with_files(files)) == {"ssh", "git"}
    assert _configfiles.groups_with_files(files)["git"] == (files[2],)

    text = _configfiles.group_counts_text(progress)
    assert text == "ssh:1/2;git:1/1;shell:0/0"
    restored = _configfiles.group_stats(text)
    assert restored["ssh"] == _configfiles.GroupProgress("ssh", 1, 2)
    assert _configfiles.group_stats("bad")["bad"] == _configfiles.GroupProgress("bad")
    assert _configfiles.group_stats("") == {}


def test_migration_state_derives_from_scanned_files() -> None:
    files = (
        _configfiles.ConfigFile(".ssh/id_ed25519", 10, "ssh", "private"),
        _configfiles.ConfigFile(".gitconfig", 5, "git", "config"),
    )
    empty = _configfiles.migration_state((), _configfiles.ConfigTarget())
    assert (empty.label, empty.tone, empty.total) == ("无文件", "muted", 0)
    assert empty.done is False

    none = _configfiles.migration_state(files, _configfiles.ConfigTarget())
    assert (none.label, none.tone, none.matched, none.total) == ("未迁移", "muted", 0, 2)

    partial_target = _configfiles.ConfigTarget(
        files=(_configfiles.RemoteFile(".ssh/id_ed25519", "600", 10),),
    )
    partial = _configfiles.migration_state(files, partial_target)
    assert (partial.label, partial.tone, partial.matched, partial.total) == ("部分迁移", "warning", 1, 2)
    assert "1/2" in partial.detail

    # SSH 密钥同名但大小不同，视为未到实例；配置文件适配后大小会变，只看是否存在。
    wrong_size = _configfiles.ConfigTarget(
        files=(
            _configfiles.RemoteFile(".ssh/id_ed25519", "600", 11),
            _configfiles.RemoteFile(".gitconfig", "644", 5),
        )
    )
    assert _configfiles.migration_state(files, wrong_size).label == "部分迁移"
    keys_only = (files[0], _configfiles.ConfigFile(".ssh/known_hosts", 6, "ssh", "known_hosts"))
    assert (
        _configfiles.migration_state(
            keys_only,
            _configfiles.ConfigTarget(
                files=(
                    _configfiles.RemoteFile(".ssh/id_ed25519", "600", 11),
                    _configfiles.RemoteFile(".ssh/known_hosts", "644", 7),
                )
            ),
        ).label
        == "未迁移"
    )

    done = _configfiles.migration_state(
        files,
        _configfiles.ConfigTarget(
            files=(
                _configfiles.RemoteFile(".ssh/id_ed25519", "600", 10),
                _configfiles.RemoteFile(".gitconfig", "644", 5),
            )
        ),
    )
    assert (done.label, done.tone, done.matched, done.total) == (_configfiles.DONE_LABEL, "success", 2, 2)
    assert done.done is True

    wrong_mode = _configfiles.migration_state(
        files,
        _configfiles.ConfigTarget(
            files=(
                _configfiles.RemoteFile(".ssh/id_ed25519", "644", 10),
                _configfiles.RemoteFile(".gitconfig", "644", 5),
            )
        ),
    )
    assert (wrong_mode.label, wrong_mode.tone) == ("权限待修正", "warning")
    assert ".ssh/id_ed25519" in wrong_mode.detail

    # 权限读不到（空串 / 非八进制）时不臆断权限错误，仍算已完成。
    unverifiable = _configfiles.migration_state(
        files,
        _configfiles.ConfigTarget(
            files=(
                _configfiles.RemoteFile(".ssh/id_ed25519", "", 10),
                _configfiles.RemoteFile(".gitconfig", "bad", 5),
            )
        ),
    )
    assert unverifiable.label == _configfiles.DONE_LABEL


def test_backup_and_temp_files_do_not_count_as_partial_migration() -> None:
    """有效文件全部迁移后就是完成：备份 / 未知杂项默认跳过，不参与完成度。"""
    files = (
        _configfiles.ConfigFile(".ssh/id_ed25519", 10, "ssh", "private"),
        _configfiles.ConfigFile(".gitconfig", 5, "git", "config"),
        _configfiles.ConfigFile(".ssh/id_ed25519.bak", 99, "ssh", "other", "备份 / 临时文件，默认不迁移"),
        _configfiles.ConfigFile(".ssh/known_hosts.old", 20, "ssh", "other", "备份 / 临时文件，默认不迁移"),
    )
    target = _configfiles.ConfigTarget(
        files=(
            _configfiles.RemoteFile(".ssh/id_ed25519", "600", 10),
            _configfiles.RemoteFile(".gitconfig", "644", 5),
        )
    )
    state = _configfiles.migration_state(files, target)
    assert (state.label, state.tone, state.matched, state.total) == (_configfiles.DONE_LABEL, "success", 2, 2)
    assert "已忽略 2 个" in state.detail

    only_skipped = tuple(item for item in files if item.kind == "other")
    skipped = _configfiles.migration_state(only_skipped, _configfiles.ConfigTarget())
    assert skipped.label == "无有效文件"
    assert skipped.total == 0


def test_backup_root_keeps_ssh_layout_and_uses_config_prefix() -> None:
    assert re.fullmatch(r"\.ssh/fcmd-ssh-backup-\d{8}-\d{6}", _configfiles.backup_root("ssh", 0))
    assert re.fullmatch(r"fcmd-config-backup-\d{8}-\d{6}", _configfiles.backup_root("git", 0))


def test_write_script_creates_parents_and_refuses_unconfirmed_overwrite() -> None:
    script = _configfiles.write_script(
        ".config/git/config", 0o644, overwrite=False, backup="fcmd-config-backup-1", dir_mode=0o755
    )
    assert '[ "0" -ne 1 ]' in script
    assert "exit 3" in script
    assert 'target="$home"/.config/git/config' in script
    assert 'chmod 755 "$dir"' in script
    assert "chmod 644" in script
    assert 'cat > "$tmp"' in script
    assert "WSLCFG_WRITTEN=" in script
    assert "rm -f" in script or "trap" in script


def test_write_script_backups_when_overwrite_confirmed_and_quotes_names() -> None:
    script = _configfiles.write_script(
        ".ssh/my key", 0o600, overwrite=True, backup=".ssh/fcmd-ssh-backup-1", dir_mode=0o700
    )
    assert '[ "1" -ne 1 ]' in script
    assert "backup=\"$home\"/.ssh/fcmd-ssh-backup-1/'my key'" in script
    assert "已备份原有文件" in script
    assert "chmod 700" in script


def test_write_script_rejects_paths_outside_managed_home() -> None:
    for path in ("/etc/passwd", "../outside", "notes.txt"):
        with pytest.raises(WslmanError) as bad:
            _configfiles.write_script(path, 0o644, overwrite=False, backup="x")
        assert bad.value.code == "RUNTIME_ERROR"


def test_initialize_script_moves_managed_files_and_parses_result() -> None:
    """初始化配置把文件移进备份目录，并回传 (路径, 备份) 供生成还原记录。"""
    script = _configfiles.initialize_script(
        [
            (".ssh/id_ed25519", ".ssh/fcmd-ssh-backup-1"),
            (".gitconfig", "fcmd-config-backup-1"),
        ]
    )
    assert script.startswith("set -euo pipefail")
    assert "move_one .ssh/id_ed25519 .ssh/fcmd-ssh-backup-1" in script
    assert "move_one .gitconfig fcmd-config-backup-1" in script
    assert 'backup_rel="${rel#.ssh/}"' in script
    assert "WSLCFG_INIT=" in script
    assert "rm -f" not in script.split("move_one")[1]  # 只移动，不直接删除

    path = ".ssh/id_ed25519"
    backup = ".ssh/fcmd-ssh-backup-1/id_ed25519"
    text = (
        "WSLCFG_INIT="
        + base64.b64encode(path.encode()).decode()
        + "|"
        + base64.b64encode(backup.encode()).decode()
        + "\nignored\n"
    )
    assert _configfiles.parse_init_result(text) == ((path, backup),)
    assert _configfiles.parse_init_result("") == ()


def test_restore_record_summary_mentions_operation() -> None:
    record = _configfiles.RestoreRecord(
        "2026-01-01 12:00:00",
        "初始化配置",
        (_configfiles.RestoreEntry(".gitconfig", "fcmd-config-backup-1/.gitconfig", 0),),
    )
    assert "初始化配置" in record.summary
    assert "备份 1" in record.summary


def test_parse_write_result_reads_last_marker() -> None:
    text = "已写入 ~/.ssh/id_ed25519（权限 600）\nWSLCFG_WRITTEN=600|me\nWSLCFG_WRITTEN=640|me\n"
    assert _configfiles.parse_write_result(text) == ("640", "me")
    assert _configfiles.parse_write_result("no marker") == ("", "")


def test_build_snapshot_carries_state_counts_source_and_groups() -> None:
    """扫描结果能压成可持久化的状态：标签 / 计数 / 来源 / 项目进度 / 实例侧摘要。"""
    files = (
        _configfiles.ConfigFile(".ssh/id_ed25519", 10, "ssh", "private"),
        _configfiles.ConfigFile(".gitconfig", 5, "git", "config"),
    )
    target = _configfiles.ConfigTarget(
        home="/home/me",
        files=(_configfiles.RemoteFile(".gitconfig", "644", 5),),
        dirs={".ssh": "700"},
    )
    source = _configfiles.ConfigSource(_configfiles.SOURCE_WINDOWS, "", r"C:\Users\me")
    snapshot = _configfiles.build_snapshot(files, target, source, stamp=1700000000.0)
    assert snapshot.state.label == "部分迁移"
    assert snapshot.source_label == "Windows 宿主"
    assert snapshot.source_count == 2
    assert snapshot.target_count == 1
    assert snapshot.summary == "Windows 宿主 2 个文件 · 实例内 1 个文件"
    assert snapshot.collapsed_hint == "Windows 宿主 2 个文件"
    assert snapshot.target_summary.startswith("~/.ssh 目录权限 700")
    assert snapshot.group_counts == "ssh:0/1;git:1/1;shell:0/0"
    assert snapshot.counts["git"].tone == "success"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", snapshot.scanned_at)


def test_snapshot_mapping_roundtrip_and_bad_rows() -> None:
    """状态缓存可无损往返；缺标签 / 非映射 / 坏计数按「没有缓存」处理。"""
    snapshot = _configfiles.build_snapshot(
        (_configfiles.ConfigFile(".ssh/id_ed25519", 10, "ssh", "private"),),
        _configfiles.ConfigTarget(
            home="/home/me",
            files=(_configfiles.RemoteFile(".ssh/id_ed25519", "600", 10),),
            dirs={".ssh": "700"},
        ),
        _configfiles.ConfigSource(_configfiles.SOURCE_WSL, "Debian", "/home/me"),
        stamp=1700000000.0,
    )
    mapping = _configfiles.snapshot_to_mapping(snapshot)
    assert set(mapping) == set(_configfiles._SNAPSHOT_FIELDS)
    assert all(isinstance(value, str) for value in mapping.values())
    assert _configfiles.snapshot_from_mapping(mapping) == snapshot

    assert _configfiles.snapshot_from_mapping(None) is None
    assert _configfiles.snapshot_from_mapping("nope") is None
    assert _configfiles.snapshot_from_mapping({}) is None
    assert _configfiles.snapshot_from_mapping({"label": "  "}) is None
    # 坏计数当成 0，坏色调回退 muted，不影响回显。
    restored = _configfiles.snapshot_from_mapping(
        {"label": "已完成迁移", "tone": "", "matched": "x", "total": "-3", "group_counts": 5}
    )
    assert restored is not None
    assert (restored.state.tone, restored.state.matched, restored.state.total) == ("muted", 0, 0)
    assert restored.group_counts == "5"
    only_counts = _configfiles.snapshot_from_mapping({"label": "已完成迁移", "group_counts": 5})
    assert only_counts is not None
    assert only_counts.group_counts == "5"
