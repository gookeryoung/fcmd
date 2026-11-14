"""zip 解压的安全边界测试。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fcmd.apps.devman import _archive
from fcmd.apps.devman._errors import DevmanError


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as bundle:
        for name, payload in members.items():
            bundle.writestr(name, payload)
    return path


def test_extract_zip_roundtrip(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {"root/node.exe": b"MZ", "root/lib/x.txt": b"x"})
    dest = tmp_path / "out"
    _archive.extract_zip(archive, dest)
    assert (dest / "root" / "node.exe").read_bytes() == b"MZ"
    assert (dest / "root" / "lib" / "x.txt").read_bytes() == b"x"


def test_extract_zip_handles_directory_members(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(zipfile.ZipInfo("root/empty/"), b"")
        bundle.writestr("root/a.txt", b"a")
    dest = tmp_path / "out"
    _archive.extract_zip(archive, dest)
    assert (dest / "root" / "empty").is_dir()


def test_safe_member_rejects_absolute_member() -> None:
    with pytest.raises(DevmanError) as info:
        _archive._safe_member("/etc/passwd")
    assert info.value.code == "DOWNLOAD_FAILED"


def test_safe_member_rejects_parent_and_drive() -> None:
    with pytest.raises(DevmanError):
        _archive._safe_member("../evil.txt")
    with pytest.raises(DevmanError):
        _archive._safe_member("C:/Windows/evil.dll")
    with pytest.raises(DevmanError):
        _archive._safe_member("")


def test_extract_zip_rejects_symlink_member(tmp_path: Path) -> None:
    archive = tmp_path / "a.zip"
    info = zipfile.ZipInfo("root/link")
    info.external_attr = 0o120000 << 16
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(info, b"target")
    with pytest.raises(DevmanError) as exc:
        _archive.extract_zip(archive, tmp_path / "out")
    assert "符号链接" in str(exc.value)


def test_extract_zip_reports_bad_archive(tmp_path: Path) -> None:
    archive = tmp_path / "broken.zip"
    archive.write_bytes(b"not a zip")
    with pytest.raises(DevmanError) as info:
        _archive.extract_zip(archive, tmp_path / "out")
    assert info.value.code == "DOWNLOAD_FAILED"


def test_extract_zip_reports_unwritable_destination(tmp_path: Path) -> None:
    archive = _zip(tmp_path / "a.zip", {"root/a.txt": b"a"})
    dest = tmp_path / "dest-file"
    dest.write_bytes(b"x")
    with pytest.raises(DevmanError) as info:
        _archive.extract_zip(archive, dest)
    assert "解压" in str(info.value)


def test_single_root_variants(tmp_path: Path) -> None:
    none_dir = tmp_path / "none"
    none_dir.mkdir()
    assert _archive.single_root(none_dir) is None

    only_file = tmp_path / "only-file"
    only_file.mkdir()
    (only_file / "a.txt").write_text("a", encoding="utf-8")
    assert _archive.single_root(only_file) is None

    two_dirs = tmp_path / "two"
    (two_dirs / "a").mkdir(parents=True)
    (two_dirs / "b").mkdir()
    assert _archive.single_root(two_dirs) is None

    one_dir = tmp_path / "one"
    nested = one_dir / "root"
    nested.mkdir(parents=True)
    assert _archive.single_root(one_dir) == nested
    assert _archive.single_root(tmp_path / "missing") is None
