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


# ============================================================================ #
# 批量重命名
# ============================================================================ #
class TestBuildNewName:
    def test_find_replace_is_literal(self) -> None:
        source = ops.RenameSource("IMG_001.png")
        assert ops.build_new_name(source, 1, ops.BulkRenameOptions(find="IMG", replace="photo")) == "photo_001.png"

    def test_find_treats_regex_chars_literally(self) -> None:
        source = ops.RenameSource("a.b.txt")
        options = ops.BulkRenameOptions(find=".", replace="_")
        assert ops.build_new_name(source, 1, options) == "a_b_txt"

    @pytest.mark.parametrize(
        ("case", "expected"),
        [
            ("keep", "My File.TXT"),
            ("lower", "my file.TXT"),
            ("upper", "MY FILE.TXT"),
            ("title", "My File.TXT"),
        ],
    )
    def test_case_applies_to_stem_only(self, case: str, expected: str) -> None:
        assert ops.build_new_name(ops.RenameSource("My File.TXT"), 1, ops.BulkRenameOptions(case=case)) == expected

    def test_unknown_case_mode(self) -> None:
        with pytest.raises(ValueError, match="大小写"):
            ops.build_new_name(ops.RenameSource("a.txt"), 1, ops.BulkRenameOptions(case="weird"))

    @pytest.mark.parametrize(
        ("extension", "expected"),
        [
            ("", "a.txt"),
            ("-", "a"),
            ("md", "a.md"),
            (".tar.gz", "a.tar.gz"),
        ],
    )
    def test_extension(self, extension: str, expected: str) -> None:
        assert ops.build_new_name(ops.RenameSource("a.txt"), 1, ops.BulkRenameOptions(extension=extension)) == expected

    def test_template_with_number(self) -> None:
        options = ops.BulkRenameOptions(template="{name}_{n}", digits=3)
        assert ops.build_new_name(ops.RenameSource("report.txt"), 7, options) == "report_007"

    def test_template_with_extension(self) -> None:
        options = ops.BulkRenameOptions(template="{n}-{name}.{ext}", digits=2, case="lower")
        assert ops.build_new_name(ops.RenameSource("Report.TXT"), 3, options) == "03-report.TXT"

    def test_template_without_extension(self) -> None:
        options = ops.BulkRenameOptions(template="{name}.bak")
        assert ops.build_new_name(ops.RenameSource("README"), 1, options) == "README.bak"

    def test_invalid_template(self) -> None:
        with pytest.raises(ValueError, match="模板无效"):
            ops.build_new_name(ops.RenameSource("a.txt"), 1, ops.BulkRenameOptions(template="{unknown}"))

    def test_directory_does_not_split_extension(self) -> None:
        options = ops.BulkRenameOptions(find=".", replace="_")
        source = ops.RenameSource("v1.2", is_dir=True)
        assert ops.build_new_name(source, 1, options) == "v1_2"

    def test_number_start_step_digits(self) -> None:
        options = ops.BulkRenameOptions(template="{name}-{n}", start=10, step=5, digits=2)
        assert ops.build_new_name(ops.RenameSource("a.txt"), 20, options) == "a-20"


class TestRenamePlan:
    def test_unchanged_entries(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(tmp_path, [ops.RenameSource("a.txt")], ops.BulkRenameOptions(find="zzz"))
        assert plan.items[0].status == "unchanged"
        assert plan.changes == ()
        assert plan.conflicts == ()

    def test_duplicate_targets_are_conflicts(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt"), ops.RenameSource("b.txt")],
            ops.BulkRenameOptions(template="same"),
        )
        assert [item.status for item in plan.items] == ["ok", "conflict"]
        assert "同名" in plan.items[1].note

    def test_existing_target_is_conflict(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(template="b.txt"),
        )
        assert plan.items[0].status == "conflict"
        assert plan.items[0].note == "目标已存在"

    def test_invalid_name_is_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(template="bad:name"),
        )
        assert plan.items[0].status == "conflict"
        assert "不能包含" in plan.items[0].note

    def test_case_only_rename_is_allowed(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(case="upper"),
        )
        assert plan.items[0].status == "ok"

    def test_apply_only_executes_ok_items(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt"), ops.RenameSource("b.txt")],
            ops.BulkRenameOptions(find=".txt", replace=".md"),
        )
        assert not plan.conflicts

        report = ops.apply_rename_plan(plan)

        assert report.errors == []
        assert (tmp_path / "a.md").read_text(encoding="utf-8") == "a"
        assert (tmp_path / "b.md").read_text(encoding="utf-8") == "b"
        assert not (tmp_path / "a.txt").exists()

    def test_undo_round_trip(self, tmp_path: Path) -> None:
        for name in ("a.txt", "b.txt"):
            (tmp_path / name).write_text(name, encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt"), ops.RenameSource("b.txt")],
            ops.BulkRenameOptions(template="{name}.bak"),
        )
        report = ops.apply_rename_plan(plan)
        assert len(report.completed) == 2

        undone = ops.undo_rename(report)

        assert undone.errors == []
        assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "a.txt"
        assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b.txt"
        assert not (tmp_path / "a.txt.bak").exists()

    def test_undo_reports_occupied_source(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        plan = ops.plan_bulk_rename(
            tmp_path,
            [ops.RenameSource("a.txt")],
            ops.BulkRenameOptions(find=".txt", replace=".md"),
        )
        report = ops.apply_rename_plan(plan)
        (tmp_path / "a.txt").write_text("new", encoding="utf-8")

        undone = ops.undo_rename(report)

        assert undone.completed == []
        assert "已被占用" in undone.errors[0][1]
        assert (tmp_path / "a.md").exists()
