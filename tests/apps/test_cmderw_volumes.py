"""卷 / 挂载点枚举与剩余空间格式化测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.cmderw import _cmderw_volumes as volumes


class TestFormatBytes:
    def test_bytes_and_units(self) -> None:
        assert volumes.format_bytes(512) == "512 B"
        assert volumes.format_bytes(1024) == "1 KB"
        assert volumes.format_bytes(1536) == "1.5 KB"
        assert volumes.format_bytes(5 * 1024**3) == "5 GB"

    def test_petabyte_fallback(self) -> None:
        assert volumes.format_bytes(1024**6) == "1024 PB"


class TestVolumeFor:
    def test_reads_usage(self, tmp_path: Path) -> None:
        volume = volumes.volume_for(tmp_path, usage=lambda _path: (100, 40, 60))
        assert volume == volumes.Volume(mount=tmp_path, total=100, free=60)

    def test_oserror_returns_none(self, tmp_path: Path) -> None:
        def broken(_path: Path) -> tuple[int, int, int]:
            raise OSError("denied")

        assert volumes.volume_for(tmp_path, usage=broken) is None

    def test_label_contains_free_and_total(self, tmp_path: Path) -> None:
        volume = volumes.Volume(mount=tmp_path, total=2048, free=1024)
        assert volume.label == f"{tmp_path}  —  1 KB 可用 / 2 KB"


class TestWindowsDriveRoots:
    def test_bitmask(self) -> None:
        assert volumes.windows_drive_roots(0) == []
        assert volumes.windows_drive_roots(0b101) == [Path("A:\\"), Path("C:\\")]

    def test_uses_win32_api_when_mask_omitted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ctypes
        from types import SimpleNamespace

        kernel32 = SimpleNamespace(GetLogicalDrives=lambda: 0b10)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
        assert volumes.windows_drive_roots() == [Path("B:\\")]


class TestParseMounts:
    def test_skips_pseudo_filesystems(self) -> None:
        text = "\n".join(
            [
                "proc /proc proc rw 0 0",
                "sysfs /sys sysfs rw 0 0",
                "tmpfs /run tmpfs rw 0 0",
                "/dev/sda1 / ext4 rw 0 0",
                "/dev/sdb1 /mnt/data ext4 rw 0 0",
            ]
        )
        assert volumes.parse_mounts(text) == [Path("/"), Path("/mnt/data")]

    def test_includes_drvfs_and_skips_short_lines(self) -> None:
        text = "broken\nC: /mnt/c drvfs rw 0 0\nD: /mnt/d drvfs rw 0 0"
        assert volumes.parse_mounts(text) == [Path("/mnt/c"), Path("/mnt/d")]

    def test_decodes_escapes(self) -> None:
        assert volumes.parse_mounts("/dev/sdc1 /mnt/my\\040disk ext4 rw 0 0") == [Path("/mnt/my disk")]

    def test_dedupes_same_mount(self) -> None:
        text = "/dev/sda1 / ext4 rw 0 0\n/dev/sda1 / ext4 rw 0 0"
        assert volumes.parse_mounts(text) == [Path("/")]


class TestLinuxMountPoints:
    def test_reads_given_contents(self) -> None:
        assert volumes.linux_mount_points("/dev/sda1 / ext4 rw 0 0") == [Path("/")]

    def test_read_failure_returns_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def broken(*_args: object, **_kwargs: object) -> str:
            raise OSError("missing")

        monkeypatch.setattr(Path, "read_text", broken)
        assert volumes.linux_mount_points() == []


class TestEnumerateVolumes:
    def test_linux_keeps_accessible_mounts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(volumes, "linux_mount_points", lambda: [Path("/"), Path("/missing")])
        monkeypatch.setattr(
            volumes,
            "volume_for",
            lambda mount: volumes.Volume(mount, 10, 5) if mount == Path("/") else None,
        )
        assert volumes.enumerate_volumes(platform="linux") == [volumes.Volume(Path("/"), 10, 5)]

    def test_windows_uses_drive_roots(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(volumes, "windows_drive_roots", lambda: [Path("C:\\"), Path("C:\\")])
        monkeypatch.setattr(volumes, "volume_for", lambda mount: volumes.Volume(mount, 20, 10))
        assert volumes.enumerate_volumes(platform="win32") == [volumes.Volume(Path("C:\\"), 20, 10)]

    def test_empty_result(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(volumes, "linux_mount_points", lambda: [])
        assert volumes.enumerate_volumes(platform="linux") == []
