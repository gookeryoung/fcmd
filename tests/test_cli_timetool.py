"""timetool 工具测试。

验证 ``fcmd.cli.calc.timetool`` 模块：
- 工具注册与五子命令结构（now/parse/unix/fromunix/convert）
- ``now_utc``/``now_local``/``parse_time``/``format_time``/``to_unix``/``from_unix``/``convert_timezone``
- CLI 子命令端到端
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.calc.timetool import (
    convert_timezone,
    format_time,
    from_unix,
    now_local,
    now_utc,
    parse_time,
    to_unix,
)

# 框架日志关键词：verbose 模式下 _make_verbose_callback 打印的状态行含这些中文标记
_FRAMEWORK_MARKERS = ("开始执行", "成功", "失败", "跳过", "错误:")


def _extract_user_lines(out: str) -> list[str]:
    """从 run_tool 输出中提取用户数据行，过滤框架 verbose 状态行。"""
    return [line for line in out.splitlines() if line and not any(marker in line for marker in _FRAMEWORK_MARKERS)]


def _named_tz_available(name: str) -> bool:
    """检查命名时区在当前平台是否可用（Windows 默认无 tzdata 包时不可用）。"""
    if sys.version_info < (3, 9):
        return False
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(name)
        return True
    except Exception:  # KeyError/ZoneInfoNotFoundError/OSError 等
        return False


# 命名时区 Asia/Shanghai 可用性标记（Windows 默认无 tzdata 时不可用）
_SHANGHAI_AVAILABLE = _named_tz_available("Asia/Shanghai")
_skip_no_shanghai = pytest.mark.skipif(
    not _SHANGHAI_AVAILABLE,
    reason="当前平台无 Asia/Shanghai 时区数据（需 tzdata 包或非 Windows 系统）",
)


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """timetool 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "timetool" in list_tools()

    def test_subcommands(self) -> None:
        """timetool 有 now/parse/unix/fromunix/convert 五个子命令。"""
        subs = list_subcommands("timetool")
        assert set(subs) == {"now", "parse", "unix", "fromunix", "convert"}


# ============================================================================ #
# now_utc / now_local
# ============================================================================ #
class TestNow:
    """now_utc / now_local 测试。"""

    def test_now_utc_has_tzinfo(self) -> None:
        """now_utc 返回 aware datetime。"""
        dt = now_utc()
        assert dt.tzinfo is not None

    def test_now_utc_is_utc(self) -> None:
        """now_utc 的 tzinfo 为 UTC。"""
        dt = now_utc()
        # 与 UTC 偏移应为 0（aware datetime 的 utcoffset 必非 None）
        offset = dt.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0

    def test_now_local_is_naive(self) -> None:
        """now_local 返回 naive datetime。"""
        dt = now_local()
        assert dt.tzinfo is None

    def test_now_returns_recent_time(self) -> None:
        """now_local 返回接近当前的时间。"""
        before = datetime.now()
        dt = now_local()
        after = datetime.now()
        # 时间应在 [before, after] 区间内
        assert before <= dt <= after


# ============================================================================ #
# parse_time / format_time
# ============================================================================ #
class TestParseFormat:
    """parse_time / format_time 测试。"""

    def test_parse_default_format(self) -> None:
        """默认格式解析。"""
        dt = parse_time("2026-07-29 12:30:00")
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 29
        assert dt.hour == 12
        assert dt.minute == 30
        assert dt.second == 0

    def test_parse_custom_format(self) -> None:
        """自定义格式解析。"""
        dt = parse_time("2026/07/29", "%Y/%m/%d")
        assert dt.year == 2026
        assert dt.month == 7
        assert dt.day == 29

    def test_parse_invalid_raises(self) -> None:
        """格式不匹配抛 ValueError。"""
        with pytest.raises(ValueError):
            parse_time("not a time")

    def test_format_default(self) -> None:
        """默认格式化。"""
        dt = datetime(2026, 7, 29, 12, 30, 0)
        assert format_time(dt) == "2026-07-29 12:30:00"

    def test_format_custom(self) -> None:
        """自定义格式化。"""
        dt = datetime(2026, 7, 29, 12, 30, 0)
        assert format_time(dt, "%Y/%m/%d") == "2026/07/29"

    def test_round_trip(self) -> None:
        """解析 -> 格式化 -> 解析 保持一致。"""
        original = "2026-07-29 12:30:00"
        dt = parse_time(original)
        formatted = format_time(dt)
        assert formatted == original


# ============================================================================ #
# to_unix / from_unix
# ============================================================================ #
class TestUnixConversion:
    """to_unix / from_unix 测试。"""

    def test_to_unix_known_value(self) -> None:
        """已知 Unix 时间戳转换（2026-07-29 12:30:00 UTC = 1785328200）。"""
        dt = datetime(2026, 7, 29, 12, 30, 0, tzinfo=timezone.utc)
        ts = to_unix(dt)
        assert ts == 1785328200

    def test_to_unix_naive_uses_local(self) -> None:
        """naive datetime 视为本地时间。"""
        dt = datetime(2026, 7, 29, 12, 30, 0)
        ts = to_unix(dt)
        # 反向转换得到本地时间
        converted = from_unix(ts)
        assert converted == dt

    def test_from_unix_round_trip(self) -> None:
        """from_unix -> to_unix 保持一致。"""
        original_ts = 1785328200
        dt = from_unix(original_ts)
        ts = to_unix(dt)
        assert ts == original_ts

    def test_from_unix_returns_naive(self) -> None:
        """from_unix 返回 naive datetime。"""
        dt = from_unix(0)
        assert dt.tzinfo is None


# ============================================================================ #
# convert_timezone
# ============================================================================ #
class TestConvertTimezone:
    """convert_timezone 测试。"""

    def test_utc_to_utc(self) -> None:
        """UTC 转 UTC 保持不变。"""
        dt = datetime(2026, 7, 29, 12, 30, 0, tzinfo=timezone.utc)
        converted = convert_timezone(dt, "UTC")
        offset = converted.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0
        assert converted.hour == 12

    @_skip_no_shanghai
    def test_aware_to_named_tz(self) -> None:
        """aware datetime 转命名时区保持时刻。"""
        dt = datetime(2026, 7, 29, 12, 30, 0, tzinfo=timezone.utc)
        converted = convert_timezone(dt, "Asia/Shanghai")
        # Asia/Shanghai 是 UTC+8
        assert converted.hour == 20

    def test_naive_treated_as_local(self) -> None:
        """naive datetime 视为本地时间。"""
        dt = datetime(2026, 7, 29, 12, 30, 0)
        converted = convert_timezone(dt, "UTC")
        # 转换后应带 UTC tzinfo
        assert converted.tzinfo is not None
        # 时刻保持（本地时间 12:30 对应某个 UTC 时刻）
        assert converted.utcoffset() is not None

    def test_invalid_tz_raises(self) -> None:
        """无效时区名抛 ValueError（_resolve_tz 将 KeyError 统一包装）。"""
        dt = datetime(2026, 7, 29, 12, 30, 0, tzinfo=timezone.utc)
        with pytest.raises(ValueError, match="无效或不可用的时区"):
            convert_timezone(dt, "Invalid/Zone")


# ============================================================================ #
# CLI 子命令测试
# ============================================================================ #
class TestTimetoolCLI:
    """``timetool`` 通过 ``run_tool`` 调用测试。"""

    def test_now_default(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd timetool now 打印当前时间。"""
        code = run_tool("timetool", ["now"])
        assert code == 0
        out = capsys.readouterr().out
        # 输出应包含年份
        assert "2026" in out or "2025" in out  # 容错跨年

    def test_now_utc(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--utc 打印 UTC 时间。"""
        code = run_tool("timetool", ["now", "--utc"])
        assert code == 0
        out = capsys.readouterr().out
        # 输出非空
        assert out.strip()

    def test_now_custom_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--format 自定义格式。"""
        code = run_tool("timetool", ["now", "--format", "%Y"])
        assert code == 0
        out = capsys.readouterr().out
        # 输出应仅为 4 位年份
        lines = _extract_user_lines(out)
        assert any(line.isdigit() and len(line) == 4 for line in lines)

    def test_parse_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd timetool parse <time> 打印解析结果。"""
        code = run_tool("timetool", ["parse", "2026-07-29 12:30:00"])
        assert code == 0
        out = capsys.readouterr().out
        assert "2026-07-29 12:30:00" in out

    def test_parse_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """格式不匹配打印错误。"""
        code = run_tool("timetool", ["parse", "not a time"])
        assert code == 0
        out = capsys.readouterr().out
        assert "解析失败" in out

    def test_parse_custom_format(self, capsys: pytest.CaptureFixture[str]) -> None:
        """--format 自定义解析格式。"""
        code = run_tool("timetool", ["parse", "2026/07/29", "--format", "%Y/%m/%d"])
        assert code == 0
        out = capsys.readouterr().out
        assert "2026/07/29" in out

    def test_unix_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd timetool unix <time> 打印时间戳。"""
        code = run_tool("timetool", ["unix", "2026-07-29 12:30:00"])
        assert code == 0
        out = capsys.readouterr().out
        # 输出应包含数字
        lines = _extract_user_lines(out)
        assert any(any(c.isdigit() for c in line) for line in lines)

    def test_unix_invalid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """格式不匹配打印错误。"""
        code = run_tool("timetool", ["unix", "not a time"])
        assert code == 0
        out = capsys.readouterr().out
        assert "解析失败" in out

    def test_fromunix_valid(self, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd timetool fromunix <ts> 打印时间。"""
        code = run_tool("timetool", ["fromunix", "1785328200"])
        assert code == 0
        out = capsys.readouterr().out
        # 输出应包含年份
        assert "2026" in out or "2025" in out

    @_skip_no_shanghai
    def test_convert_utc_to_shanghai(self, capsys: pytest.CaptureFixture[str]) -> None:
        """convert <time> Asia/Shanghai 转换时区。"""
        code = run_tool(
            "timetool",
            ["convert", "12:30:00", "Asia/Shanghai", "--format", "%H:%M:%S"],
        )
        assert code == 0
        out = capsys.readouterr().out
        # 输出应包含时间字符串
        assert ":" in out

    @_skip_no_shanghai
    def test_convert_with_from_tz(self, capsys: pytest.CaptureFixture[str]) -> None:
        """convert <time> Asia/Shanghai --from-tz UTC 指定源时区。"""
        code = run_tool(
            "timetool",
            [
                "convert",
                "2026-07-29 12:30:00",
                "Asia/Shanghai",
                "--from-tz",
                "UTC",
                "--format",
                "%Y-%m-%d %H:%M:%S",
            ],
        )
        assert code == 0
        out = capsys.readouterr().out
        # UTC 12:30 -> Shanghai 20:30
        assert "20:30:00" in out

    def test_convert_invalid_tz(self, capsys: pytest.CaptureFixture[str]) -> None:
        """无效时区打印错误。"""
        code = run_tool(
            "timetool",
            ["convert", "12:30:00", "Invalid/Zone", "--format", "%H:%M:%S"],
        )
        assert code == 0
        out = capsys.readouterr().out
        # 错误信息或无效时区
        assert "Invalid" in out or "无效" in out or "不支持" in out

    def test_convert_invalid_time(self, capsys: pytest.CaptureFixture[str]) -> None:
        """时间格式不匹配打印错误。"""
        code = run_tool(
            "timetool",
            ["convert", "not a time", "UTC", "--format", "%H:%M:%S"],
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "解析失败" in out

    def test_convert_utc_to_utc(self, capsys: pytest.CaptureFixture[str]) -> None:
        """convert <time> UTC 转 UTC（跨平台可用）。"""
        code = run_tool(
            "timetool",
            ["convert", "12:30:00", "UTC", "--from-tz", "UTC", "--format", "%H:%M:%S"],
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "12:30:00" in out

    def test_convert_invalid_from_tz(self, capsys: pytest.CaptureFixture[str]) -> None:
        """convert --from-tz 无效时区名打印错误。"""
        code = run_tool(
            "timetool",
            ["convert", "12:30:00", "UTC", "--from-tz", "Invalid/Zone", "--format", "%H:%M:%S"],
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "无效或不可用的时区" in out
