"""wslmanw 文件管理纯逻辑：路径换算、目录读取与上传下载 / 增删改。"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from fcmd.apps.wslman import _files
from fcmd.apps.wslman._errors import WslmanError


def test_unc_candidates_prefer_wsl_localhost() -> None:
    assert _files.unc_candidates("Ubuntu-24.04") == (
        r"\\wsl.localhost\Ubuntu-24.04",
        r"\\wsl$\Ubuntu-24.04",
    )


def test_pick_unc_root_uses_first_accessible() -> None:
    seen: list[str] = []

    def probe(path: str) -> bool:
        seen.append(path)
        return True

    root = _files.pick_unc_root("Ubuntu", probe=probe)
    assert root == r"\\wsl.localhost\Ubuntu"
    assert seen == [r"\\wsl.localhost\Ubuntu"]


def test_pick_unc_root_falls_back_to_legacy_host() -> None:
    root = _files.pick_unc_root("Ubuntu", probe=lambda path: path.startswith(r"\\wsl$"))
    assert root == r"\\wsl$\Ubuntu"


def test_pick_unc_root_skips_probe_errors_and_reports_hint() -> None:
    def probe(path: str) -> bool:
        raise OSError("拒绝访问")

    with pytest.raises(WslmanError) as info:
        _files.pick_unc_root("Ubuntu", probe=probe)
    assert "无法访问 Ubuntu" in str(info.value)
    assert "wsl -d Ubuntu --exec true" in (info.value.hint or "")


def test_normalize_path_variants() -> None:
    home = "/home/me"
    assert _files.normalize_path("", home) == home
    assert _files.normalize_path("~", home) == home
    assert _files.normalize_path("~/project", home) == "/home/me/project"
    assert _files.normalize_path("project", home) == "/home/me/project"
    assert _files.normalize_path("/a/./b/../c", home) == "/a/c"
    assert _files.normalize_path("//a//b/", home) == "/a/b"
    assert _files.normalize_path("..", home) == "/home"
    assert _files.normalize_path(r"\tmp\a", home) == "/tmp/a"


def test_normalize_path_rejects_nul() -> None:
    with pytest.raises(WslmanError):
        _files.normalize_path("/tmp/a\0b", "/root")


def test_parent_and_join_path() -> None:
    assert _files.parent_path("/", "/root") == "/"
    assert _files.parent_path("/root", "/root") == "/"
    assert _files.parent_path("~", "/root") == "/"
    assert _files.join_path("/root", "a b") == "/root/a b"
    assert _files.join_path("~", "a") == "/a"


def test_display_path_uses_home_alias() -> None:
    home = "/home/me"
    assert _files.display_path(home, home) == "~"
    assert _files.display_path("/home/me/project", home) == "~/project"
    assert _files.display_path("/var/log", home) == "/var/log"
    assert _files.display_path("/var/log", "") == "/var/log"
    assert _files.display_path("/", "/") == "~"


def test_posix_unc_mapping_round_trip() -> None:
    root = r"\\wsl.localhost\Ubuntu"
    local = _files.to_local(root, "/home/me/a b")
    assert str(local) == r"\\wsl.localhost\Ubuntu\home\me\a b"
    assert _files.to_posix(root, local) == "/home/me/a b"
    assert _files.to_posix(root, _files.to_local(root, "/")) == "/"
    assert _files.to_posix(root, Path(r"C:\other")) == "/"


def test_list_entries_sorts_folders_first(tmp_path: Path) -> None:
    (tmp_path / "zeta.txt").write_text("12345", encoding="utf-8")
    (tmp_path / "Alpha.txt").write_text("a", encoding="utf-8")
    (tmp_path / "folder").mkdir()
    stamp = 1_700_000_000
    for path in tmp_path.iterdir():
        path.touch()
        os.utime(path, (stamp, stamp))

    entries = _files.list_entries(tmp_path)
    assert [entry.name for entry in entries] == ["folder", "Alpha.txt", "zeta.txt"]
    folder, alpha, zeta = entries
    assert folder.is_dir is True
    assert _files.type_text(folder) == "文件夹"
    assert _files.size_text(folder) == "—"
    assert _files.type_text(alpha) == "文件"
    assert _files.size_text(alpha) == "1 B"
    assert _files.size_text(zeta) == "5 B"
    expected = time.strftime("%Y-%m-%d %H:%M", time.localtime(stamp))
    assert _files.format_mtime(alpha) == expected


def test_size_and_mtime_unknown() -> None:
    entry = _files.FileEntry("x", False, 0, 0.0)
    assert _files.size_text(entry) == "0 B"
    assert _files.format_mtime(entry) == "—"


def test_list_entries_reports_unreadable_directory(tmp_path: Path) -> None:
    with pytest.raises(WslmanError) as info:
        _files.list_entries(tmp_path / "missing")
    assert "无法读取目录" in str(info.value)


def test_check_name_rules() -> None:
    assert _files.check_name("  report.txt  ") == "report.txt"
    for bad in ("", "  ", ".", "..", "a/b", r"a\b", "a\0b", "x" * 256):
        with pytest.raises(WslmanError):
            _files.check_name(bad)


def test_create_dir_and_duplicate(tmp_path: Path) -> None:
    created = _files.create_dir(tmp_path, " new ")
    assert created == tmp_path / "new"
    with pytest.raises(WslmanError) as info:
        _files.create_dir(tmp_path, "new")
    assert "已存在同名" in str(info.value)


def test_create_dir_maps_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(self: Path, *args: object, **kwargs: object) -> None:
        raise OSError("磁盘只读")

    monkeypatch.setattr(Path, "mkdir", boom)
    with pytest.raises(WslmanError) as info:
        _files.create_dir(tmp_path, "new")
    assert "无法新建文件夹" in str(info.value)


def test_rename_entry_and_conflict(tmp_path: Path) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    assert _files.rename_entry(source, "a.txt") == source
    renamed = _files.rename_entry(source, "c.txt")
    assert renamed.name == "c.txt"
    assert not source.exists()
    with pytest.raises(WslmanError) as info:
        _files.rename_entry(renamed, "b.txt")
    assert "已存在同名" in str(info.value)


def test_rename_entry_maps_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")

    def boom(self: Path, target: Path) -> Path:
        raise OSError("被占用")

    monkeypatch.setattr(Path, "rename", boom)
    with pytest.raises(WslmanError) as info:
        _files.rename_entry(source, "b.txt")
    assert "无法重命名" in str(info.value)


def test_delete_entry_file_and_tree(tmp_path: Path) -> None:
    root = tmp_path
    file_path = root / "a.txt"
    file_path.write_text("a", encoding="utf-8")
    _files.delete_entry(file_path, root)
    assert not file_path.exists()

    folder = root / "folder"
    folder.mkdir()
    (folder / "nested.txt").write_text("x", encoding="utf-8")
    _files.delete_entry(folder, root)
    assert not folder.exists()


def test_delete_entry_refuses_root(tmp_path: Path) -> None:
    with pytest.raises(WslmanError) as info:
        _files.delete_entry(tmp_path, tmp_path)
    assert "不能删除实例根目录" in str(info.value)


def test_delete_entry_unlinks_symlink(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    link = tmp_path / "link"
    link.write_text("x", encoding="utf-8")
    monkeypatch.setattr(Path, "is_symlink", lambda self: True)
    _files.delete_entry(link, tmp_path)
    assert not link.exists()


def test_delete_entry_maps_os_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()

    def boom(path: Path) -> None:
        raise OSError("权限不足")

    monkeypatch.setattr("fcmd.apps.wslman._files.shutil.rmtree", boom)
    with pytest.raises(WslmanError) as info:
        _files.delete_entry(folder, tmp_path)
    assert "无法删除" in str(info.value)


def test_upload_files_copies_and_guards_collision(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    origin = source / "a.txt"
    origin.write_text("hello", encoding="utf-8")
    target_dir = tmp_path / "target"
    target_dir.mkdir()

    copied = _files.upload_files([origin], target_dir)
    assert len(copied) == 1
    assert (target_dir / "a.txt").read_text(encoding="utf-8") == "hello"

    with pytest.raises(WslmanError) as info:
        _files.upload_files([origin], target_dir)
    assert "目标已存在" in str(info.value)

    overwritten = _files.upload_files([origin], target_dir, overwrite=True)
    assert len(overwritten) == 1
    assert _files.upload_files([], target_dir) == ()


def test_upload_files_missing_source_and_copy_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(WslmanError) as info:
        _files.upload_files([tmp_path / "missing.txt"], tmp_path)
    assert "源文件不存在" in str(info.value)

    origin = tmp_path / "a.txt"
    origin.write_text("a", encoding="utf-8")
    target_dir = tmp_path / "target"
    target_dir.mkdir()

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("网络中断")

    monkeypatch.setattr("fcmd.apps.wslman._files.shutil.copy2", boom)
    with pytest.raises(WslmanError) as info:
        _files.upload_files([origin], target_dir)
    assert "上传失败" in str(info.value)


def test_download_file_copies_and_guards(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text("hello", encoding="utf-8")
    target = tmp_path / "out" / "saved.txt"
    target.parent.mkdir()

    assert _files.download_file(source, target) == target
    assert target.read_text(encoding="utf-8") == "hello"

    with pytest.raises(WslmanError) as info:
        _files.download_file(source, target)
    assert "目标文件已存在" in str(info.value)

    assert _files.download_file(source, target, overwrite=True) == target


def test_download_file_rejects_folder_and_missing(tmp_path: Path) -> None:
    folder = tmp_path / "folder"
    folder.mkdir()
    with pytest.raises(WslmanError) as info:
        _files.download_file(folder, tmp_path / "x")
    assert "是文件夹" in str(info.value)

    with pytest.raises(WslmanError) as info:
        _files.download_file(tmp_path / "missing", tmp_path / "x")
    assert "文件不存在" in str(info.value)


def test_download_file_maps_copy_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "a.txt"
    source.write_text("a", encoding="utf-8")

    def boom(*args: object, **kwargs: object) -> None:
        raise OSError("磁盘已满")

    monkeypatch.setattr("fcmd.apps.wslman._files.shutil.copy2", boom)
    with pytest.raises(WslmanError) as info:
        _files.download_file(source, tmp_path / "b.txt")
    assert "下载失败" in str(info.value)


def test_parse_home_ignores_banners() -> None:
    text = "\n".join(
        (
            "Welcome to Ubuntu",
            "WSLFILES_USER=me",
            "noise=1",
            "WSLFILES_HOME=/home/me",
        )
    )
    assert _files.parse_home(text) == ("me", "/home/me")
    assert _files.parse_home("WSLFILES_HOME=home/me") == ("", "/home/me")
    assert _files.parse_home("WSLFILES_HOME=") == ("", "")
    assert _files.parse_home("") == ("", "")


def test_home_probe_script_has_markers() -> None:
    script = _files.home_probe_script()
    assert "WSLFILES_USER=" in script
    assert "WSLFILES_HOME=" in script
    assert "$HOME" in script


def test_default_unc_probe_uses_filesystem(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未注入 probe 时用真实目录判断；探测抛错按不可访问处理。"""
    monkeypatch.setattr(Path, "is_dir", lambda self: True)
    assert _files.pick_unc_root("Ubuntu") == r"\\wsl.localhost\Ubuntu"

    def boom(self: Path) -> bool:
        raise OSError("拒绝访问")

    monkeypatch.setattr(Path, "is_dir", boom)
    with pytest.raises(WslmanError):
        _files.pick_unc_root("Ubuntu")


def test_absolute_prepends_root() -> None:
    assert _files._absolute("relative") == "/relative"
    assert _files._absolute(".") == "/"
    assert _files._absolute("") == "/"


def test_list_entries_keeps_unreadable_items(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """元数据读不到也列出该项（大小 / 时间未知），不因为单个坏项丢掉整目录。"""
    target = tmp_path / "broken"
    target.write_text("x", encoding="utf-8")
    original_stat = Path.stat

    def broken_stat(self: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if self.name == "broken":
            raise OSError("gone")
        return original_stat(self, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", broken_stat)
    entries = _files.list_entries(tmp_path)
    assert entries[0].name == "broken"
    assert entries[0].size == 0
    assert _files.format_mtime(entries[0]) == "—"

    monkeypatch.setattr(Path, "stat", original_stat)
    original_is_dir = Path.is_dir

    def broken_is_dir(self: Path) -> bool:
        if self.name == "broken":
            raise OSError("denied")
        return original_is_dir(self)

    monkeypatch.setattr(Path, "is_dir", broken_is_dir)
    entries = _files.list_entries(tmp_path)
    assert entries[0].is_dir is False
    assert entries[0].size == 1
