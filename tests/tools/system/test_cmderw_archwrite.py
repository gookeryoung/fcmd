"""归档写入纯逻辑测试：新增 / 删除 / 改名、压缩级别、密码与原子性。"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path
from typing import Literal

import pytest

from fcmd.tools.system import _cmderw_archwrite as archwrite
from fcmd.tools.system._cmderw_archwrite import ArchiveWriteOptions


def make_zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as handle:
        for name, data in members.items():
            handle.writestr(name, data)
    return path


def make_tar(path: Path, members: dict[str, bytes], mode: Literal["w", "w:gz", "w:bz2", "w:xz"] = "w") -> Path:
    import io

    with tarfile.open(path, mode) as handle:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            handle.addfile(info, io.BytesIO(data))
    return path


def text_file(path: Path, data: str = "hello") -> Path:
    path.write_text(data, encoding="utf-8")
    return path


class TestNormalizeLevel:
    def test_clamps_range(self) -> None:
        assert archwrite.normalize_level(0) == 0
        assert archwrite.normalize_level(9) == 9
        assert archwrite.normalize_level(-3) == 0
        assert archwrite.normalize_level(99) == 9

    def test_invalid_type_falls_back(self) -> None:
        assert archwrite.normalize_level("9") == archwrite.DEFAULT_COMPRESSION_LEVEL
        assert archwrite.normalize_level(True) == archwrite.DEFAULT_COMPRESSION_LEVEL


class TestEditable:
    def test_zip_and_tar_families(self) -> None:
        for name in ("a.zip", "a.tar", "a.tar.gz", "a.tgz", "a.tar.bz2", "a.tbz2", "a.tar.xz", "a.txz"):
            assert archwrite.archive_editable(Path(name))

    def test_readonly_formats(self) -> None:
        for name in ("a.gz", "a.bz2", "a.xz", "a.7z", "a.rar"):
            assert not archwrite.archive_editable(Path(name))


class TestPlannedAdditions:
    def test_single_file_and_prefix(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "note.txt")
        planned = archwrite.planned_additions([source], "docs")
        assert planned == {"docs/note.txt": source}

    def test_directory_is_expanded(self, tmp_path: Path) -> None:
        folder = tmp_path / "folder"
        nested = folder / "sub"
        nested.mkdir(parents=True)
        text_file(folder / "a.txt")
        text_file(nested / "b.txt")
        planned = archwrite.planned_additions([folder])
        assert sorted(planned) == ["folder/a.txt", "folder/sub/b.txt"]

    def test_missing_source(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="源不存在"):
            archwrite.planned_additions([tmp_path / "missing"])

    def test_unsafe_prefix(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "a.txt")
        with pytest.raises(ValueError, match="非法成员路径"):
            archwrite.planned_additions([source], "../evil")


class TestArchiveMemberNames:
    def test_zip_and_tar(self, tmp_path: Path) -> None:
        zipped = make_zip(tmp_path / "a.zip", {"a.txt": b"a"})
        tared = make_tar(tmp_path / "a.tar", {"a.txt": b"a"})
        assert archwrite.archive_member_names(zipped) == ["a.txt"]
        assert archwrite.archive_member_names(tared) == ["a.txt"]

    def test_unsupported_format(self, tmp_path: Path) -> None:
        path = tmp_path / "a.gz"
        path.write_bytes(b"x")
        with pytest.raises(ValueError, match="不支持写入"):
            archwrite.archive_member_names(path)

    def test_missing_archive(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="归档不存在"):
            archwrite.archive_member_names(tmp_path / "missing.zip")


class TestAddPaths:
    def test_add_file(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"keep.txt": b"keep"})
        source = text_file(tmp_path / "new.txt", "new")
        assert archwrite.add_paths(archive, [source]) == 1
        assert archwrite.archive_member_names(archive) == ["keep.txt", "new.txt"]
        with zipfile.ZipFile(archive) as handle:
            assert handle.read("new.txt") == b"new"
            assert handle.read("keep.txt") == b"keep"

    def test_add_directory_with_prefix(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"keep.txt": b"keep"})
        folder = tmp_path / "folder"
        folder.mkdir()
        text_file(folder / "a.txt", "a")
        assert archwrite.add_paths(archive, [folder], "docs") == 1
        assert "docs/folder/a.txt" in archwrite.archive_member_names(archive)

    def test_conflict_requires_replace(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"dup.txt": b"old"})
        other = tmp_path / "other"
        other.mkdir()
        source = text_file(other / "dup.txt", "new")
        with pytest.raises(FileExistsError, match=r"dup\.txt"):
            archwrite.add_paths(archive, [source])
        assert archwrite.add_paths(archive, [source], replace=True) == 1
        with zipfile.ZipFile(archive) as handle:
            assert handle.read("dup.txt") == b"new"

    def test_tar_add_and_remove(self, tmp_path: Path) -> None:
        archive = make_tar(tmp_path / "a.tar.gz", {"old.txt": b"old"}, mode="w:gz")
        source = text_file(tmp_path / "new.txt", "new")
        assert archwrite.add_paths(archive, [source]) == 1
        assert "new.txt" in archwrite.archive_member_names(archive)
        assert archwrite.remove_members(archive, ["new.txt"]) == 1
        assert archwrite.archive_member_names(archive) == ["old.txt"]

    def test_missing_archive(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "new.txt")
        with pytest.raises(FileNotFoundError, match="归档不存在"):
            archwrite.add_paths(tmp_path / "missing.zip", [source])

    def test_empty_sources_raises(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {})
        with pytest.raises(ValueError, match="没有可加入"):
            archwrite.add_paths(archive, [])


class TestRemoveMembers:
    def test_remove_file_and_subtree(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"a.txt": b"a", "dir/b.txt": b"b", "dir/sub/c.txt": b"c"})
        assert archwrite.remove_members(archive, ["dir"]) == 2
        assert archwrite.archive_member_names(archive) == ["a.txt"]

    def test_unknown_names_removes_nothing(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"a.txt": b"a"})
        assert archwrite.remove_members(archive, ["nope.txt"]) == 0
        assert archwrite.remove_members(archive, []) == 0

    def test_tar_remove(self, tmp_path: Path) -> None:
        archive = make_tar(tmp_path / "a.tar", {"a.txt": b"a", "b.txt": b"b"})
        assert archwrite.remove_members(archive, ["a.txt"]) == 1
        assert archwrite.archive_member_names(archive) == ["b.txt"]


class TestRenameMember:
    def test_rename_file(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"a.txt": b"a"})
        assert archwrite.rename_member(archive, "a.txt", "b.txt") == "b.txt"
        assert archwrite.archive_member_names(archive) == ["b.txt"]
        with zipfile.ZipFile(archive) as handle:
            assert handle.read("b.txt") == b"a"

    def test_rename_directory_moves_subtree(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"dir/a.txt": b"a", "dir/sub/b.txt": b"b", "keep.txt": b"k"})
        archwrite.rename_member(archive, "dir", "moved")
        assert sorted(archwrite.archive_member_names(archive)) == ["keep.txt", "moved/a.txt", "moved/sub/b.txt"]

    def test_rename_conflict(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"a.txt": b"a", "b.txt": b"b"})
        with pytest.raises(FileExistsError, match=r"b\.txt"):
            archwrite.rename_member(archive, "a.txt", "b.txt")

    def test_rename_missing_or_same(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"a.txt": b"a"})
        with pytest.raises(FileNotFoundError, match="成员不存在"):
            archwrite.rename_member(archive, "nope.txt", "b.txt")
        with pytest.raises(ValueError, match="新旧名称相同"):
            archwrite.rename_member(archive, "a.txt", "a.txt")

    def test_tar_rename(self, tmp_path: Path) -> None:
        archive = make_tar(tmp_path / "a.tar", {"a.txt": b"a"})
        archwrite.rename_member(archive, "a.txt", "b.txt")
        assert archwrite.archive_member_names(archive) == ["b.txt"]


class TestCreateArchiveFile:
    def test_zip_level_zero_stores(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "data.txt", "x" * 1024)
        archive = tmp_path / "out.zip"
        assert archwrite.create_archive_file(archive, [source], options=ArchiveWriteOptions(level=0)) == 1
        with zipfile.ZipFile(archive) as handle:
            assert handle.infolist()[0].compress_type == zipfile.ZIP_STORED

    def test_tar_gz_level_affects_size(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "data.txt", "abc" * 10000)
        low = tmp_path / "low.tar.gz"
        high = tmp_path / "high.tar.gz"
        archwrite.create_archive_file(low, [source], options=ArchiveWriteOptions(level=0))
        archwrite.create_archive_file(high, [source], options=ArchiveWriteOptions(level=9))
        assert high.stat().st_size <= low.stat().st_size
        assert archwrite.archive_member_names(high) == ["data.txt"]

    def test_tar_xz_and_bz2(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "data.txt")
        for name in ("out.tar.xz", "out.tar.bz2"):
            archive = tmp_path / name
            archwrite.create_archive_file(archive, [source])
            assert archwrite.archive_member_names(archive) == ["data.txt"]

    def test_empty_sources(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="没有可打包"):
            archwrite.create_archive_file(tmp_path / "out.zip", [])

    def test_readonly_target(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "data.txt")
        with pytest.raises(ValueError, match="不支持写入"):
            archwrite.create_archive_file(tmp_path / "out.gz", [source])


class TestVerifyArchive:
    def test_valid_archives(self, tmp_path: Path) -> None:
        zipped = make_zip(tmp_path / "a.zip", {"a.txt": b"a"})
        tared = make_tar(tmp_path / "a.tar.gz", {"a.txt": b"a"}, mode="w:gz")
        archwrite.verify_archive(zipped, expected_names=["a.txt"])
        archwrite.verify_archive(tared, expected_names=["a.txt"])

    def test_expected_mismatch(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"a.txt": b"a"})
        with pytest.raises(RuntimeError, match="成员列表不一致"):
            archwrite.verify_archive(archive, expected_names=["b.txt"])

    def test_unsupported(self, tmp_path: Path) -> None:
        path = tmp_path / "a.gz"
        path.write_bytes(b"x")
        with pytest.raises(ValueError, match="不支持校验"):
            archwrite.verify_archive(path)


class TestAtomicity:
    def test_verify_failure_keeps_original(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        archive = make_zip(tmp_path / "a.zip", {"keep.txt": b"keep"})
        original = archive.read_bytes()
        source = text_file(tmp_path / "new.txt", "new")

        def boom(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("校验失败")

        monkeypatch.setattr(archwrite, "verify_archive", boom)
        with pytest.raises(RuntimeError, match="校验失败"):
            archwrite.add_paths(archive, [source])
        assert archive.read_bytes() == original
        assert not list(tmp_path.glob(".*.zip"))

    def test_missing_source_leaves_archive_intact(self, tmp_path: Path) -> None:
        archive = make_zip(tmp_path / "a.zip", {"keep.txt": b"keep"})
        original = archive.read_bytes()
        with pytest.raises(FileNotFoundError):
            archwrite.add_paths(archive, [tmp_path / "missing"])
        assert archive.read_bytes() == original


class TestPassword:
    def test_zip_password_roundtrip(self, tmp_path: Path) -> None:
        pyzipper = pytest.importorskip("pyzipper")
        source = text_file(tmp_path / "data.txt", "secret-data")
        archive = tmp_path / "secure.zip"
        options = ArchiveWriteOptions(password="pw")
        assert archwrite.create_archive_file(archive, [source], options=options) == 1
        archwrite.verify_archive(archive, password="pw", expected_names=["data.txt"])
        with pyzipper.AESZipFile(archive) as handle:
            handle.setpassword(b"pw")
            assert handle.read("data.txt") == b"secret-data"

    def test_encrypted_rewrite_requires_password(self, tmp_path: Path) -> None:
        pytest.importorskip("pyzipper")
        source = text_file(tmp_path / "data.txt", "secret")
        archive = tmp_path / "secure.zip"
        archwrite.create_archive_file(archive, [source], options=ArchiveWriteOptions(password="pw"))
        extra = text_file(tmp_path / "extra.txt", "extra")
        with pytest.raises(ValueError, match="已加密"):
            archwrite.add_paths(archive, [extra])
        assert archwrite.add_paths(archive, [extra], options=ArchiveWriteOptions(password="pw")) == 1
        archwrite.verify_archive(archive, password="pw")

    def test_tar_rejects_password(self, tmp_path: Path) -> None:
        source = text_file(tmp_path / "data.txt")
        with pytest.raises(ValueError, match="仅 zip 支持密码"):
            archwrite.create_archive_file(
                tmp_path / "out.tar.gz",
                [source],
                options=ArchiveWriteOptions(password="pw"),
            )
