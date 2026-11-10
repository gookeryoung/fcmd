"""``_cmderw_ops`` 纯逻辑测试：通配选择、快速过滤、目录统计与回收站。"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

import pytest

from fcmd.tools.system import _cmderw_ops as ops


# ============================================================================ #
# 掩码 / 过滤 / 反选
# ============================================================================ #
class TestMasks:
    def test_split_masks(self) -> None:
        """分号分隔、去空白、丢弃空段。"""
        assert ops.split_masks(" *.py ; ;*.txt ") == ("*.py", "*.txt")
        assert ops.split_masks("") == ()

    @pytest.mark.parametrize(
        ("name", "mask", "expected"),
        [
            ("a.py", "*.py", True),
            ("A.PY", "*.py", True),
            ("a.txt", "*.py", False),
            ("a.py", "*.py;*.txt", True),
            ("a.txt", "*.py;*.txt", True),
            ("a.md", "*.py;*.txt", False),
            ("ab", "a?", True),
            ("abc", "a?", False),
            ("any", "*", True),
        ],
    )
    def test_matches_mask(self, name: str, mask: str, expected: bool) -> None:
        assert ops.matches_mask(name, mask) is expected

    def test_matches_mask_empty(self) -> None:
        """空掩码不命中任何名称。"""
        assert ops.matches_mask("a.py", " ; ") is False

    def test_match_names_preserves_order(self) -> None:
        names = ["b.txt", "a.py", "c.txt"]
        assert ops.match_names(names, "*.txt") == ["b.txt", "c.txt"]

    def test_filter_names(self) -> None:
        """快速过滤是忽略大小写的子串匹配，空关键词返回全部。"""
        names = ["Readme.md", "report.txt", "data.bin"]
        assert ops.filter_names(names, "RE") == ["Readme.md", "report.txt"]
        assert ops.filter_names(names, "zzz") == []
        assert ops.filter_names(names, "  ") == names

    def test_invert_names(self) -> None:
        names = ["a", "b", "c"]
        assert ops.invert_names(names, ["b"]) == ["a", "c"]
        assert ops.invert_names(names, names) == []
        assert ops.invert_names(names, []) == names


# ============================================================================ #
# 目录统计
# ============================================================================ #
class TestDirectorySize:
    def test_file(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_bytes(b"12345")
        assert ops.directory_size(target) == (5, 1)

    def test_tree(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_bytes(b"123")
        nested = tmp_path / "sub"
        nested.mkdir()
        (nested / "b.txt").write_bytes(b"12345")
        assert ops.directory_size(tmp_path) == (8, 2)

    def test_skips_symlinks(self, tmp_path: Path) -> None:
        """符号链接文件与目录都不计入，避免跨卷环。"""
        target = tmp_path / "real.txt"
        target.write_bytes(b"1234")
        (tmp_path / "link.txt").symlink_to(target)
        directory = tmp_path / "tree"
        directory.mkdir()
        (directory / "inner.txt").write_bytes(b"12")
        (tmp_path / "link_dir").symlink_to(directory, target_is_directory=True)
        assert ops.directory_size(tmp_path) == (6, 2)

    def test_missing_path(self, tmp_path: Path) -> None:
        assert ops.directory_size(tmp_path / "missing") == (0, 0)


# ============================================================================ #
# 回收站
# ============================================================================ #
class TestTrash:
    def test_trash_root_uses_xdg_data_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        assert ops.trash_root() == tmp_path / "xdg" / "Trash"

    def test_trash_root_defaults_to_local_share(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("XDG_DATA_HOME", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        assert ops.trash_root() == tmp_path / ".local" / "share" / "Trash"

    def test_linux_file_writes_trashinfo(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        source = tmp_path / "a b.txt"
        source.write_text("x", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert not source.exists()
        assert target == tmp_path / "xdg" / "Trash" / "files" / "a b.txt"
        assert target.read_text(encoding="utf-8") == "x"
        info = (tmp_path / "xdg" / "Trash" / "info" / "a b.txt.trashinfo").read_text(encoding="utf-8")
        assert info.startswith("[Trash Info]\n")
        assert f"Path={quote(str(source.resolve()), safe='/')}" in info
        assert "DeletionDate=" in info

    def test_linux_directory_and_name_conflict(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        files_dir = tmp_path / "xdg" / "Trash" / "files"
        files_dir.mkdir(parents=True)
        (files_dir / "dir").mkdir()
        (files_dir / "dir" / "old.txt").write_text("old", encoding="utf-8")
        source = tmp_path / "dir"
        source.mkdir()
        (source / "new.txt").write_text("new", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert target == files_dir / "dir (2)"
        assert (target / "new.txt").read_text(encoding="utf-8") == "new"
        assert (files_dir / "dir" / "old.txt").read_text(encoding="utf-8") == "old"
        assert (tmp_path / "xdg" / "Trash" / "info" / "dir (2).trashinfo").is_file()

    def test_linux_trashinfo_conflict_forces_unique_name(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """files 下已无同名，但 info 记录占用时仍要换名，避免覆盖历史记录。"""
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
        info_dir = tmp_path / "xdg" / "Trash" / "info"
        info_dir.mkdir(parents=True)
        (info_dir / "a.txt.trashinfo").write_text("[Trash Info]\n", encoding="utf-8")
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert target.name == "a (2).txt"
        assert (info_dir / "a.txt.trashinfo").read_text(encoding="utf-8") == "[Trash Info]\n"

    def test_missing_source(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            ops.send_to_trash(tmp_path / "missing")

    def test_rejects_root(self) -> None:
        with pytest.raises(ValueError):
            ops.send_to_trash(Path(Path("/").anchor))

    def test_windows_dispatch(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """win32 分支交给 SHFileOperationW 封装，并返回原路径。"""
        monkeypatch.setattr(sys, "platform", "win32")
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")
        calls: list[Path] = []
        monkeypatch.setattr(ops, "_trash_windows", calls.append)

        assert ops.send_to_trash(source) == source
        assert calls == [source]

    def test_macos_moves_to_home_trash(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        monkeypatch.setenv("HOME", str(tmp_path))
        source = tmp_path / "a.txt"
        source.write_text("x", encoding="utf-8")

        target = ops.send_to_trash(source)

        assert target == tmp_path / ".Trash" / "a.txt"
        assert target.read_text(encoding="utf-8") == "x"

    def test_trash_paths_reports_progress_and_errors(self, tmp_path: Path) -> None:
        good = tmp_path / "good.txt"
        bad = tmp_path / "bad.txt"
        good.write_text("x", encoding="utf-8")
        bad.write_text("y", encoding="utf-8")
        events: list[tuple[int, int, str]] = []

        def mover(path: Path) -> Path:
            if path.name == "bad.txt":
                raise OSError("拒绝访问")
            return path

        def record(done: int, total: int, name: str) -> None:
            events.append((done, total, name))

        report = ops.trash_paths([good, bad], progress=record, mover=mover)

        assert report.completed == [good]
        assert report.errors == [(bad, "拒绝访问")]
        assert events == [(1, 2, "good.txt"), (2, 2, "bad.txt")]
