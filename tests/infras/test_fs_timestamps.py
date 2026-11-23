"""``infras.fs.timestamps`` 纯逻辑测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.infras.fs.timestamps import TimestampError, file_stamps, format_epoch, parse_timestamp


class TestParseTimestamp:
    """时间戳文本解析。"""

    def test_unix_seconds(self) -> None:
        """``@`` 前缀按 Unix 秒解析。"""
        assert parse_timestamp("@1700000000") == 1700000000.0
        assert parse_timestamp(" @1.5 ") == 1.5

    def test_invalid_unix(self) -> None:
        """``@`` 后非数字报错。"""
        with pytest.raises(TimestampError, match="Unix"):
            parse_timestamp("@abc")

    def test_empty(self) -> None:
        """空文本报错。"""
        with pytest.raises(TimestampError, match="不能为空"):
            parse_timestamp("   ")

    def test_iso_date(self) -> None:
        """纯日期按当天 00:00:00 解析。"""
        parsed = parse_timestamp("2025-01-02")
        assert parsed == parse_timestamp("2025-01-02 00:00:00")

    def test_iso_utc_z(self) -> None:
        """``Z`` 后缀按 UTC 解析。"""
        assert parse_timestamp("2025-01-02T03:04:05Z") == 1735787045.0

    def test_iso_offset(self) -> None:
        """显式偏移按偏移解析。"""
        assert parse_timestamp("2025-01-02T11:04:05+08:00") == 1735787045.0

    def test_invalid_iso(self) -> None:
        """非法 ISO 文本报错。"""
        with pytest.raises(TimestampError, match="ISO 8601"):
            parse_timestamp("2025-13-40")


class TestFormatEpoch:
    """Unix 秒展开。"""

    def test_fields(self) -> None:
        """返回整数秒 + 本地时间 + UTC 文本。"""
        stamp = format_epoch(1735787045)
        assert stamp["unix"] == 1735787045
        assert stamp["utc"] == "2025-01-02T03:04:05Z"
        assert isinstance(stamp["local"], str)


class TestFileStamps:
    """文件三时间戳。"""

    def test_file_stamps(self, tmp_path: Path) -> None:
        """mtime/atime/ctime 都存在。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        stamps = file_stamps(target)
        assert set(stamps) == {"mtime", "atime", "ctime"}
        for value in stamps.values():
            assert set(value) == {"unix", "local", "utc"}
