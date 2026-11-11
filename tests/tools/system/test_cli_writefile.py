"""writefile 工具测试。

验证 ``fcmd writefile``：
- 工具注册（单命令）
- utf-8/自定义编码写入、覆盖写入
- 父目录缺失 → 任务失败汇总 + 退出码 1
- dry-run 不执行
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool


class TestRegistration:
    """writefile 注册为单命令工具。"""

    def test_registered(self) -> None:
        """writefile 已注册且无子命令。"""
        assert "writefile" in list_tools()
        assert list_subcommands("writefile") == []


class TestWritefileRun:
    """``fcmd writefile`` 执行语义。"""

    def test_run_writes_utf8(self, tmp_path: Path) -> None:
        """fcmd writefile <path> <content> 默认 utf-8 写入。"""
        f = tmp_path / "note.txt"
        assert run_tool("writefile", [str(f), "Hello World"]) == 0
        assert f.read_text(encoding="utf-8") == "Hello World"

    def test_run_custom_encoding(self, tmp_path: Path) -> None:
        """--encoding 指定编码。"""
        f = tmp_path / "note.txt"
        assert run_tool("writefile", [str(f), "中文内容", "--encoding", "gbk"]) == 0
        assert f.read_text(encoding="gbk") == "中文内容"

    def test_run_overwrites_existing(self, tmp_path: Path) -> None:
        """覆盖已有文件。"""
        f = tmp_path / "note.txt"
        f.write_text("old", encoding="utf-8")
        assert run_tool("writefile", [str(f), "new"]) == 0
        assert f.read_text(encoding="utf-8") == "new"

    def test_failure_exit_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """父目录缺失 → 任务失败汇总 + 退出码 1。"""
        f = tmp_path / "missing_dir" / "note.txt"
        assert run_tool("writefile", [str(f), "x"]) == 1
        out = capsys.readouterr().out
        assert "失败" in out

    def test_dry_run_skips_execution(self, tmp_path: Path) -> None:
        """--dry-run 打印计划不执行：文件不创建。"""
        f = tmp_path / "dry.txt"
        assert run_tool("writefile", [str(f), "x", "--dry-run"]) == 0
        assert not f.exists()
