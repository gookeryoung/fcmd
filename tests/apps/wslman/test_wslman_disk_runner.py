"""wslman 磁盘估算、执行器与错误类型测试。

执行器一律注入假实现或替换 ``subprocess.run``，不真的调用 ``wsl.exe``。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from fcmd.apps.wslman import _disk, _errors, _runner
from tests.apps.wslman import _fakes

# ---------------------------------------------------------------------- #
# format_bytes / volume_info
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("value", "estimated", "expected"),
    [
        (None, False, "未知"),
        (0, False, "0 B"),
        (512, False, "512 B"),
        (1536, False, "1.5 KiB"),
        (2 * 1024**3, True, "≈2.0 GiB"),
        (5 * 1024**4, False, "5.0 TiB"),
    ],
)
def test_format_bytes(value: int | None, estimated: bool, expected: str) -> None:
    assert _disk.format_bytes(value, estimated=estimated) == expected


def test_volume_info_existing_path(tmp_path: Path) -> None:
    info = _disk.volume_info(tmp_path)
    assert info.path == str(tmp_path)
    assert info.free_bytes is not None and info.free_bytes > 0
    assert info.total_bytes is not None
    assert info.error is None


def test_volume_info_walks_up_to_existing_parent(tmp_path: Path) -> None:
    info = _disk.volume_info(tmp_path / "missing" / "deep")
    assert info.path == str(tmp_path)
    assert info.free_bytes is not None


def test_volume_info_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def boom(_path: Path) -> Any:
        raise OSError("设备未就绪")

    monkeypatch.setattr(_disk.shutil, "disk_usage", boom)
    info = _disk.volume_info(tmp_path)
    assert info.free_bytes is None
    assert info.error is not None
    assert "设备未就绪" in info.error


def test_recommended_backup_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert _disk.recommended_backup_dir() == tmp_path / "wsl-backups"


# ---------------------------------------------------------------------- #
# distro_disk_bytes
# ---------------------------------------------------------------------- #


def test_disk_bytes_without_base_path() -> None:
    assert _disk.distro_disk_bytes(None, 2) == (None, False)


def test_disk_bytes_wsl2_vhdx(tmp_path: Path) -> None:
    vhdx = tmp_path / "ext4.vhdx"
    vhdx.write_bytes(b"x" * 100)
    assert _disk.distro_disk_bytes(str(tmp_path), 2) == (100, True)


def test_disk_bytes_stat_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class BadPath:
        def __init__(self, _value: str) -> None:
            pass

        def __truediv__(self, _name: str) -> BadPath:
            return self

        def is_file(self) -> bool:
            return True

        def stat(self) -> Any:
            raise OSError("无法读取")

    monkeypatch.setattr(_disk, "Path", BadPath)
    assert _disk.distro_disk_bytes("C:/fake", 2) == (None, False)


def test_disk_bytes_wsl1_rootfs(tmp_path: Path) -> None:
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "a").write_bytes(b"a" * 30)
    (rootfs / "b").write_bytes(b"b" * 70)
    assert _disk.distro_disk_bytes(str(tmp_path), 1) == (100, False)


def test_disk_bytes_rootfs_without_version(tmp_path: Path) -> None:
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "a").write_bytes(b"a" * 5)
    assert _disk.distro_disk_bytes(str(tmp_path), None) == (5, False)


def test_disk_bytes_wsl1_without_rootfs(tmp_path: Path) -> None:
    assert _disk.distro_disk_bytes(str(tmp_path), 1) == (None, False)


def test_disk_bytes_walk_budget_exceeded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    (rootfs / "a").write_bytes(b"a" * 3)
    ticks = iter([0.0, 100.0])
    monkeypatch.setattr(_disk.time, "monotonic", lambda: next(ticks))
    assert _disk.distro_disk_bytes(str(tmp_path), 1) == (3, True)


def test_disk_bytes_walk_oserror(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()

    def boom(_path: Path) -> Any:
        raise OSError("拒绝访问")

    monkeypatch.setattr(_disk.os, "scandir", boom)
    assert _disk.distro_disk_bytes(str(tmp_path), 1) == (None, True)


# ---------------------------------------------------------------------- #
# SubprocessRunner / probe
# ---------------------------------------------------------------------- #


def test_subprocess_runner_sets_utf8_and_returns_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen["args"] = args
        seen.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=b"ok\r\n", stderr=b"")

    monkeypatch.setattr(_runner.subprocess, "run", fake_run)
    result = _runner.SubprocessRunner().run(["wsl.exe", "--status"], timeout=5.0)
    assert seen["args"] == ["wsl.exe", "--status"]
    assert seen["check"] is False
    assert seen["capture_output"] is True
    assert seen["timeout"] == 5.0
    assert seen["env"]["WSL_UTF8"] == "1"
    assert result.argv == ("wsl.exe", "--status")
    assert result.returncode == 0
    assert result.stdout == b"ok\r\n"
    assert result.output_text == "ok"


def test_subprocess_runner_extra_env(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        seen.update(kwargs)
        return subprocess.CompletedProcess(args=args, returncode=1, stdout=b"", stderr=b"boom")

    monkeypatch.setattr(_runner.subprocess, "run", fake_run)
    result = _runner.SubprocessRunner().run(["wsl.exe"], env={"EXTRA": "1"})
    assert seen["env"]["EXTRA"] == "1"
    assert result.stderr == b"boom"


def test_probe_without_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runner, "find_wsl", lambda: None)
    caps = _runner.probe(_fakes.FakeRunner())
    assert caps.available is False
    assert caps.wsl_path is None
    assert caps.errors == ("未在 PATH 中找到 wsl.exe",)


def test_probe_detects_switches() -> None:
    runner = _fakes.FakeRunner(
        {
            ("--version",): _fakes.make_result(stdout=_fakes.utf16("WSL 版本: 2.3.26.0\n")),
            ("--help",): _fakes.make_result(stdout=b"--install --no-launch --import --version\n"),
        }
    )
    caps = _runner.probe(runner, wsl_path=_fakes.WSL)
    assert caps.available is True
    assert caps.version == "2.3.26.0"
    assert caps.has_install is True
    assert caps.has_install_no_launch is True
    assert caps.has_import_version is True
    assert caps.errors == ()


def test_probe_handles_command_failures() -> None:
    runner = _fakes.FakeRunner(
        {
            ("--version",): _fakes.make_result(returncode=1, stderr=b"failed"),
            ("--help",): OSError("无法启动"),
        }
    )
    caps = _runner.probe(runner, wsl_path=_fakes.WSL)
    assert caps.version is None
    assert caps.help_text == ""
    assert caps.has_install is False
    assert any("--version 返回非零" in error for error in caps.errors)
    assert any("--help" in error for error in caps.errors)


def test_find_wsl_prefers_exe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runner.shutil, "which", lambda name: _fakes.WSL if name == "wsl.exe" else None)
    assert _runner.find_wsl() == _fakes.WSL


def test_find_wsl_falls_back_to_wsl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runner.shutil, "which", lambda name: "/usr/bin/wsl" if name == "wsl" else None)
    assert _runner.find_wsl() == "/usr/bin/wsl"


def test_find_wsl_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runner.shutil, "which", lambda _name: None)
    assert _runner.find_wsl() is None


def test_is_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_runner.sys, "platform", "win32")
    assert _runner.is_windows() is True
    monkeypatch.setattr(_runner.sys, "platform", "linux")
    assert _runner.is_windows() is False


# ---------------------------------------------------------------------- #
# 错误类型
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (_errors.not_found, "NOT_FOUND"),
        (_errors.invalid_argument, "INVALID_ARGUMENT"),
        (_errors.usage_error, "USAGE"),
        (_errors.runtime_error, "RUNTIME_ERROR"),
        (_errors.check_failed, "CHECK_FAILED"),
    ],
)
def test_error_factories(factory: Any, code: str) -> None:
    error = factory("说明", hint="提示", field="field")
    assert isinstance(error, _errors.WslmanError)
    assert error.code == code
    assert str(error) == "说明"
    assert error.hint == "提示"
    assert error.field == "field"
