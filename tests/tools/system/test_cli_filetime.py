"""``filetime`` 工具测试：读取/设置文件时间戳与确认闸门。"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool


class TestRegistration:
    """filetime 注册为多子命令工具。"""

    def test_registered(self) -> None:
        """filetime 已注册且含 get/set 子命令。"""
        assert "filetime" in list_tools()
        assert set(list_subcommands("filetime")) == {"get", "set"}


class TestGet:
    """``filetime get`` 读取语义。"""

    def test_get_human(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """人读输出含 mtime/atime/ctime 三行。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["get", str(target)]) == 0
        out = capsys.readouterr().out
        assert str(target) in out
        for key in ("mtime", "atime", "ctime"):
            assert key in out

    def test_get_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 输出 fcmd-result/1 信封与三个时间戳字段。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["get", str(target), "--json"]) == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["schema"] == "fcmd-result/1"
        assert doc["tool"] == "filetime"
        assert doc["subcommand"] == "get"
        item = doc["data"]["files"][0]
        assert item["path"] == str(target)
        for key in ("mtime", "atime", "ctime"):
            assert set(item[key]) == {"unix", "local", "utc"}

    def test_get_multiple_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """多个文件各自输出一段。"""
        first = tmp_path / "a.txt"
        second = tmp_path / "b.txt"
        first.write_text("x", encoding="utf-8")
        second.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["get", str(first), str(second)]) == 0
        out = capsys.readouterr().out
        assert str(first) in out
        assert str(second) in out

    def test_get_missing_file(self, tmp_path: Path) -> None:
        """缺失文件按 FILE_NOT_FOUND 退出 4。"""
        assert run_tool("filetime", ["get", str(tmp_path / "nope.txt")]) == 4

    def test_get_missing_file_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """缺失文件在 --json 下写 fcmd-error/1 信封。"""
        assert run_tool("filetime", ["get", str(tmp_path / "nope.txt"), "--json"]) == 4
        payload = json.loads(capsys.readouterr().err)
        assert payload["schema"] == "fcmd-error/1"
        assert payload["code"] == "FILE_NOT_FOUND"
        assert payload["exit_code"] == 4


class TestSet:
    """``filetime set`` 写入语义与确认闸门。"""

    def test_set_requires_confirm(self, tmp_path: Path) -> None:
        """未加 --confirm 时拒绝执行，退出 2。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        before = target.stat().st_mtime
        assert run_tool("filetime", ["set", str(target), "--mtime", "@1700000000"]) == 2
        assert target.stat().st_mtime == pytest.approx(before)

    def test_set_requires_confirm_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 下拒绝执行时写 CONFIRM_REQUIRED 错误信封。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["set", str(target), "--mtime", "@1700000000", "--json"]) == 2
        payload = json.loads(capsys.readouterr().err)
        assert payload["code"] == "CONFIRM_REQUIRED"
        assert payload["exit_code"] == 2

    def test_set_with_confirm(self, tmp_path: Path) -> None:
        """--confirm 后 mtime 被改写为指定 Unix 秒。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["set", str(target), "--mtime", "@1700000000", "--confirm"]) == 0
        assert int(target.stat().st_mtime) == 1700000000

    def test_set_atime(self, tmp_path: Path) -> None:
        """--atime 可与 mtime 分开指定。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        argv = ["set", str(target), "--mtime", "@1700000000", "--atime", "@1600000000", "--confirm"]
        assert run_tool("filetime", argv) == 0
        stat = target.stat()
        assert int(stat.st_mtime) == 1700000000
        assert int(stat.st_atime) == 1600000000

    def test_set_iso_with_z(self, tmp_path: Path) -> None:
        """ISO 8601 带 Z 按 UTC 解释。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["set", str(target), "--mtime", "2025-01-02T03:04:05Z", "--confirm"]) == 0
        assert int(target.stat().st_mtime) == 1735787045

    def test_set_iso_local(self, tmp_path: Path) -> None:
        """无时区 ISO 文本按本机时区解释。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        text = "2025-01-02 03:04:05"
        expected = int(datetime.fromisoformat(text).timestamp())
        assert run_tool("filetime", ["set", str(target), "--mtime", text, "--confirm"]) == 0
        assert int(target.stat().st_mtime) == expected

    def test_set_dry_run_skips_write(self, tmp_path: Path) -> None:
        """--dry-run 不要求确认，也不写 mtime。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        before = target.stat().st_mtime
        assert run_tool("filetime", ["set", str(target), "--mtime", "@1700000000", "--dry-run"]) == 0
        assert target.stat().st_mtime == pytest.approx(before)

    def test_set_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """--json 输出写入后的时间戳。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["set", str(target), "--mtime", "@1700000000", "--confirm", "--json"]) == 0
        doc = json.loads(capsys.readouterr().out)
        assert doc["subcommand"] == "set"
        assert doc["data"]["mtime"]["unix"] == 1700000000

    def test_set_invalid_mtime(self, tmp_path: Path) -> None:
        """非法 mtime 按 INVALID_ARGUMENT 退出 3。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["set", str(target), "--mtime", "not-a-time", "--confirm"]) == 3

    def test_set_empty_mtime(self, tmp_path: Path) -> None:
        """空 mtime 按 INVALID_ARGUMENT 退出 3。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        assert run_tool("filetime", ["set", str(target), "--mtime", "", "--confirm"]) == 3

    def test_set_invalid_atime(self, tmp_path: Path) -> None:
        """非法 atime 单独报错，mtime 不落盘。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")
        before = target.stat().st_mtime
        argv = ["set", str(target), "--mtime", "@1700000000", "--atime", "bad", "--confirm"]
        assert run_tool("filetime", argv) == 3
        assert target.stat().st_mtime == pytest.approx(before)

    def test_set_missing_file(self, tmp_path: Path) -> None:
        """缺失文件退出 4。"""
        assert run_tool("filetime", ["set", str(tmp_path / "nope.txt"), "--mtime", "@1", "--confirm"]) == 4

    def test_set_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """utime 抛 OSError 时按 RUNTIME_ERROR 退出 1。"""
        target = tmp_path / "a.txt"
        target.write_text("x", encoding="utf-8")

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("locked")

        monkeypatch.setattr(os, "utime", _boom)
        assert run_tool("filetime", ["set", str(target), "--mtime", "@1700000000", "--confirm"]) == 1
