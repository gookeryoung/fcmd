"""时间戳与属性修改纯逻辑测试。"""

from __future__ import annotations

import stat
from datetime import datetime
from pathlib import Path

import pytest

from fcmd.tools.system import _cmderw_attrs as attrs


class TestParseTimestamp:
    def test_formats(self) -> None:
        expected = datetime(2026, 1, 2, 3, 4, 5).timestamp()
        assert attrs.parse_timestamp("2026-01-02 03:04:05") == expected
        assert attrs.parse_timestamp("2026-01-02 03:04") == datetime(2026, 1, 2, 3, 4).timestamp()
        assert attrs.parse_timestamp("2026-01-02") == datetime(2026, 1, 2).timestamp()
        assert attrs.parse_timestamp("  2026-01-02  ") == datetime(2026, 1, 2).timestamp()

    def test_empty_is_none(self) -> None:
        assert attrs.parse_timestamp("  ") is None

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            attrs.parse_timestamp("2026/01/02")


class TestFormatTimestamp:
    def test_roundtrip(self) -> None:
        stamp = datetime(2026, 1, 2, 3, 4, 5).timestamp()
        assert attrs.format_timestamp(stamp) == "2026-01-02 03:04:05"

    def test_invalid_returns_empty(self) -> None:
        assert attrs.format_timestamp(0) == ""


class TestReadAttributes:
    def test_posix_defaults(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert attrs.read_attributes(target, platform="linux") == attrs.FileAttributes(False, False)

    def test_posix_dotfile_is_hidden(self, tmp_path: Path) -> None:
        target = tmp_path / ".hidden"
        target.write_text("x", encoding="utf-8")
        assert attrs.read_attributes(target, platform="linux").hidden

    def test_posix_without_write_bits_is_readonly(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        target.chmod(stat.S_IRUSR | stat.S_IRGRP)
        assert attrs.read_attributes(target, platform="linux").readonly

    def test_windows_bits_are_decoded(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert attrs.read_attributes(target, platform="win32", reader=lambda _path: 0x3) == attrs.FileAttributes(
            True, True
        )
        assert attrs.read_attributes(target, platform="win32", reader=lambda _path: 0x0) == attrs.FileAttributes(
            False, False
        )


class TestWindowsAttributeIo:
    def test_read_attributes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ctypes
        from types import SimpleNamespace

        kernel32 = SimpleNamespace(GetFileAttributesW=lambda _path: 0x2)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
        assert attrs.windows_attributes(Path("C:/a.txt")) == 0x2

    def test_read_invalid_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ctypes
        from types import SimpleNamespace

        kernel32 = SimpleNamespace(GetFileAttributesW=lambda _path: 0xFFFFFFFF)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
        with pytest.raises(OSError, match="无法读取属性"):
            attrs.windows_attributes(Path("C:/a.txt"))

    def test_write_attributes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ctypes
        from types import SimpleNamespace

        calls: list[tuple[str, int]] = []
        kernel32 = SimpleNamespace(SetFileAttributesW=lambda path, value: calls.append((path, value)) or 1)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
        attrs.write_windows_attributes(Path("C:/a.txt"), 0x3)
        assert calls == [(str(Path("C:/a.txt")), 0x3)]

    def test_write_failure_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import ctypes
        from types import SimpleNamespace

        kernel32 = SimpleNamespace(SetFileAttributesW=lambda _path, _value: 0)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)
        with pytest.raises(OSError, match="无法写入属性"):
            attrs.write_windows_attributes(Path("C:/a.txt"), 0x3)


class TestSetHidden:
    def test_toggle_on_and_off(self) -> None:
        written: list[tuple[Path, int]] = []
        current = {0x1}

        def reader(_path: Path) -> int:
            return next(iter(current))

        def writer(_path: Path, value: int) -> None:
            written.append((Path("C:/a.txt"), value))
            current.clear()
            current.add(value)

        attrs.set_hidden(Path("C:/a.txt"), True, reader=reader, writer=writer)
        assert written[-1][1] == 0x3
        attrs.set_hidden(Path("C:/a.txt"), False, reader=reader, writer=writer)
        assert written[-1][1] == 0x1

    def test_no_write_when_unchanged(self) -> None:
        written: list[int] = []
        attrs.set_hidden(
            Path("C:/a.txt"),
            False,
            reader=lambda _path: 0x1,
            writer=lambda _path, value: written.append(value),
        )
        assert written == []


class TestApplyAttributes:
    def test_sets_both_timestamps(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        report = attrs.apply_attributes(
            [target],
            attrs.AttributeChange(modified=1_600_000_000.0, accessed=1_500_000_000.0),
            platform="linux",
        )
        assert report.ok and report.updated == 1
        stamp = target.stat()
        assert stamp.st_mtime == pytest.approx(1_600_000_000.0)
        assert stamp.st_atime == pytest.approx(1_500_000_000.0)

    def test_partial_timestamp_keeps_other(self, tmp_path: Path) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        before = target.stat()
        attrs.apply_attributes([target], attrs.AttributeChange(accessed=1_500_000_000.0), platform="linux")
        after = target.stat()
        assert after.st_atime == pytest.approx(1_500_000_000.0)
        assert after.st_mtime == pytest.approx(before.st_mtime)

    def test_windows_readonly_and_hidden(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        hidden_calls: list[bool] = []
        monkeypatch.setattr(attrs, "set_hidden", lambda _path, hidden: hidden_calls.append(hidden))
        report = attrs.apply_attributes(
            [target],
            attrs.AttributeChange(readonly=True, hidden=True),
            platform="win32",
        )
        assert report.ok
        assert not target.stat().st_mode & stat.S_IWRITE
        assert hidden_calls == [True]

        attrs.apply_attributes([target], attrs.AttributeChange(readonly=False), platform="win32")
        assert target.stat().st_mode & stat.S_IWRITE

        attrs.apply_attributes([target], attrs.AttributeChange(hidden=False), platform="win32")
        assert hidden_calls == [True, False]

    def test_failures_are_collected(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.txt"
        report = attrs.apply_attributes([missing], attrs.AttributeChange(modified=1.0), platform="linux")
        assert not report.ok
        assert report.updated == 0
        assert report.failed[0][0] == missing

    def test_inactive_change_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="没有需要修改的属性"):
            attrs.apply_attributes([tmp_path], attrs.AttributeChange())


class TestReportMessage:
    def test_success(self) -> None:
        assert attrs.AttributeReport(updated=3).message() == "已更新 3 项属性"

    def test_failures_are_listed(self) -> None:
        report = attrs.AttributeReport(updated=1, failed=[(Path("a.txt"), "denied")])
        assert report.message() == "已更新 1 项，失败 1 项：a.txt"

    def test_many_failures_are_summarized(self) -> None:
        failed = [(Path(f"f{index}.txt"), "denied") for index in range(7)]
        report = attrs.AttributeReport(failed=failed)
        assert report.message() == "已更新 0 项，失败 7 项：f0.txt，f1.txt，f2.txt，f3.txt，f4.txt 等 7 项"
