"""filedate 工具测试。

验证 ``fcmd.cli.filedate`` 模块：
- 工具注册与子命令结构
- DATE_PATTERN 日期前缀正则
- get_file_timestamp / add_date_prefix / remove_date_prefix
- process_file_date / process_files_date 批量处理
- 通过 run_tool 调用 add / clear 子命令
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.filedate  # 触发 @fx.tool 注册
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.filedate import (
    DATE_PATTERN,
    add_date_prefix,
    get_file_timestamp,
    process_file_date,
    process_files_date,
    remove_date_prefix,
)


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """filedate 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """filedate 应在 _TOOL_REGISTRY 中注册。"""
        assert "filedate" in _TOOL_REGISTRY, "工具 'filedate' 未注册"

    def test_filedate_subcommands(self) -> None:
        """filedate 应有 add / clear 子命令。"""
        subs = fx.list_subcommands("filedate")
        assert "add" in subs
        assert "clear" in subs


# ---------------------------------------------------------------------- #
# filedate 工具测试
# ---------------------------------------------------------------------- #
class TestFiledate:
    """``filedate`` 工具测试。"""

    def test_date_pattern_matches_yyyymmdd(self) -> None:
        """DATE_PATTERN 匹配 YYYYMMDD 格式。"""
        assert DATE_PATTERN.match("20260715_report.pdf")
        assert DATE_PATTERN.match("2026-07-15_report.pdf")
        assert not DATE_PATTERN.match("report.pdf")

    def test_get_file_timestamp_format(self, tmp_path: Path) -> None:
        """get_file_timestamp 返回 YYYYMMDD 格式。"""
        f = tmp_path / "a.txt"
        f.write_text("a", encoding="utf-8")
        ts = get_file_timestamp(f)
        assert len(ts) == 8
        assert ts.isdigit()
        assert ts.startswith(("19", "20"))

    def test_add_date_prefix(self, tmp_path: Path) -> None:
        """add_date_prefix 为文件名添加日期前缀。"""
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        new_path = add_date_prefix(f)
        assert new_path != f
        assert DATE_PATTERN.match(new_path.name)
        assert new_path.name.endswith("report.pdf")
        assert new_path.exists()
        assert not f.exists()

    def test_remove_date_prefix(self, tmp_path: Path) -> None:
        """remove_date_prefix 移除文件名中的日期前缀。"""
        f = tmp_path / "20260715_report.pdf"
        f.write_text("x", encoding="utf-8")
        new_path = remove_date_prefix(f)
        assert new_path.name == "report.pdf"
        assert new_path.exists()
        assert not f.exists()

    def test_remove_date_prefix_no_match(self, tmp_path: Path) -> None:
        """remove_date_prefix 无前缀时返回原路径。"""
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        new_path = remove_date_prefix(f)
        assert new_path == f

    def test_process_file_date_add_then_clear_roundtrip(self, tmp_path: Path) -> None:
        """add 后再 clear 应恢复原文件名。"""
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        original_name = f.name
        process_file_date(f, clear=False)
        # add 后文件名应有前缀
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name != original_name
        assert DATE_PATTERN.match(files[0].name)
        # clear 后应恢复
        process_file_date(files[0], clear=True)
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == original_name

    def test_filedate_add_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd filedate add <file> 通过 run_tool 调用。"""
        f = tmp_path / "report.pdf"
        f.write_text("x", encoding="utf-8")
        code = run_tool("filedate", ["add", str(f)])
        assert code == 0
        # 文件应被重命名（带日期前缀）
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert DATE_PATTERN.match(files[0].name)

    def test_filedate_clear_via_run_tool(self, tmp_path: Path) -> None:
        """fcmd filedate clear <file> 通过 run_tool 调用。"""
        f = tmp_path / "20260715_report.pdf"
        f.write_text("x", encoding="utf-8")
        code = run_tool("filedate", ["clear", str(f)])
        assert code == 0
        files = list(tmp_path.iterdir())
        assert len(files) == 1
        assert files[0].name == "report.pdf"

    def test_process_files_date_skips_nonexistent_and_hidden(self, tmp_path: Path) -> None:
        """process_files_date 跳过不存在文件与点开头隐藏文件。"""
        real = tmp_path / "real.pdf"
        real.write_text("x", encoding="utf-8")
        hidden = tmp_path / ".hidden.pdf"
        hidden.write_text("x", encoding="utf-8")
        nonexistent = tmp_path / "no_such_file.pdf"
        # 传入混合列表，只有 real.pdf 应被处理
        process_files_date([real, hidden, nonexistent], clear=False)
        files = {p.name for p in tmp_path.iterdir()}
        # real.pdf 应被重命名为带日期前缀
        assert any(DATE_PATTERN.match(n) for n in files)
        # .hidden.pdf 应保持不变
        assert ".hidden.pdf" in files
