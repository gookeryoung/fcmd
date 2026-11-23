"""本地镜像包：tar / vhdx / 压缩包的解包与载荷识别。"""

from __future__ import annotations

import shutil
import tarfile
import zipfile
from pathlib import Path

import pytest

from fcmd.apps.wslman import _install
from fcmd.apps.wslman._errors import WslmanError


def _write_zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_direct_tar_wsl_and_vhdx_are_import_payloads(tmp_path: Path) -> None:
    for name in ("rootfs.tar", "rootfs.tar.gz", "custom.wsl"):
        source = tmp_path / name
        source.write_bytes(b"x")
        payload = _install.prepare_payload(source, tmp_path / "work")
        assert payload.kind == "tar"
        assert payload.path == source
        assert payload.cleanup is None
    vhdx = tmp_path / "ext4.vhdx"
    vhdx.write_bytes(b"x")
    payload = _install.prepare_payload(vhdx, tmp_path / "work")
    assert payload.kind == "vhdx"
    assert payload.path == vhdx


def test_zip_with_vhdx_extracts_and_finds_disk(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "Ubuntu.zip", {"Ubuntu/ext4.vhdx": b"disk"})
    payload = _install.prepare_payload(source, tmp_path / "work")
    assert payload.kind == "vhdx"
    assert payload.path.name == "ext4.vhdx"
    assert payload.cleanup is not None and payload.cleanup.is_dir()
    shutil.rmtree(payload.cleanup)


def test_zip_with_rootfs_tar_extracts_tar(tmp_path: Path) -> None:
    source = _write_zip(tmp_path / "Debian.zip", {"rootfs.tar": b"tar"})
    payload = _install.prepare_payload(source, tmp_path / "work")
    assert payload.kind == "tar"
    assert payload.path.name == "rootfs.tar"
    assert payload.cleanup is not None


def test_zip_with_only_rootfs_tree_is_packed_to_tar(tmp_path: Path) -> None:
    source = _write_zip(
        tmp_path / "Alpine.zip",
        {"rootfs/etc/os-release": b"NAME=Alpine\n", "rootfs/bin/sh": b"#!/bin/sh\n"},
    )
    payload = _install.prepare_payload(source, tmp_path / "work")
    assert payload.kind == "tar"
    assert payload.cleanup is not None
    try:
        with tarfile.open(payload.path) as archive:
            names = set(archive.getnames())
        assert "etc" in names and "bin" in names
    finally:
        shutil.rmtree(payload.cleanup)


def test_tar_xz_rootfs_is_extracted_and_repacked(tmp_path: Path) -> None:
    """bz2 / xz 压缩 tar 先解包；只有文件树时重新打成普通 tar 供 wsl --import。"""
    tree = tmp_path / "tree"
    (tree / "etc").mkdir(parents=True)
    (tree / "bin").mkdir()
    (tree / "etc" / "os-release").write_text("NAME=Arch", encoding="utf-8")
    (tree / "bin" / "sh").write_text("#!/bin/sh", encoding="utf-8")
    source = tmp_path / "Arch.tar.xz"
    with tarfile.open(source, "w:xz") as archive:
        archive.add(tree / "etc", arcname="etc")
        archive.add(tree / "bin", arcname="bin")
    payload = _install.prepare_payload(source, tmp_path / "work")
    assert payload.kind == "tar"
    assert payload.cleanup is not None
    try:
        with tarfile.open(payload.path) as archive:
            assert {"etc", "bin"} <= set(archive.getnames())
    finally:
        shutil.rmtree(payload.cleanup)


def test_external_archive_formats_use_same_payload_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """7z / rar 走外部解压：这里替身只验证后缀识别和后续载荷发现。"""
    for suffix in (".7z", ".rar"):
        source = tmp_path / f"image{suffix}"
        source.write_bytes(b"x")

        def fake_extract(_source: Path, output: Path) -> None:
            (output / "ext4.vhdx").write_bytes(b"disk")

        monkeypatch.setattr(_install, "extract_archive", fake_extract)
        payload = _install.prepare_payload(source, tmp_path / "work")
        assert payload.kind == "vhdx"
        assert payload.cleanup is not None
        shutil.rmtree(payload.cleanup)


def test_archive_without_payload_or_unknown_suffix_fails(tmp_path: Path) -> None:
    empty = _write_zip(tmp_path / "empty.zip", {"readme.txt": b"no image"})
    with pytest.raises(WslmanError) as excinfo:
        _install.prepare_payload(empty, tmp_path / "work")
    assert "没有找到可安装的镜像" in str(excinfo.value)
    unknown = tmp_path / "image.iso"
    unknown.write_bytes(b"x")
    with pytest.raises(WslmanError) as excinfo:
        _install.prepare_payload(unknown, tmp_path / "work")
    assert "不支持的镜像包格式" in str(excinfo.value)
