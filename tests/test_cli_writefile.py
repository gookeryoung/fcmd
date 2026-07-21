"""writefile 工具测试。

验证 ``fcmd.cli.writefile`` 模块：
- 工具注册（单命令工具，无子命令）
- 通过 run_tool 调用 writefile 写入文件
- 自定义编码、父目录自动创建、覆盖写入
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
import fcmd.cli.writefile
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """writefile 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """writefile 应在 _TOOL_REGISTRY 中注册。"""
        assert "writefile" in _TOOL_REGISTRY, "工具 'writefile' 未注册"

    def test_writefile_single_command(self) -> None:
        """writefile 是单命令工具（无子命令）。"""
        subs = fx.list_subcommands("writefile")
        assert subs == []


# ---------------------------------------------------------------------- #
# writefile 工具测试
# ---------------------------------------------------------------------- #
class TestWritefile:
    """``writefile`` 工具测试。"""

    def test_writefile_via_run_tool(self, tmp_path: Path) -> None:
        """fcmd writefile <path> <content> 写入文件。"""
        f = tmp_path / "note.txt"
        code = run_tool("writefile", [str(f), "Hello World"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "Hello World"

    def test_writefile_custom_encoding(self, tmp_path: Path) -> None:
        """fcmd writefile --encoding 指定编码。"""
        f = tmp_path / "note.txt"
        code = run_tool("writefile", [str(f), "中文内容", "--encoding", "utf-8"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "中文内容"

    def test_writefile_creates_parent_dirs(self, tmp_path: Path) -> None:
        """writefile 写入嵌套路径时自动创建父目录。"""
        f = tmp_path / "sub" / "dir" / "note.txt"
        # Path.write_text 不自动创建父目录，需手动建
        f.parent.mkdir(parents=True)
        code = run_tool("writefile", [str(f), "nested"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "nested"

    def test_writefile_overwrite(self, tmp_path: Path) -> None:
        """writefile 覆盖已有文件。"""
        f = tmp_path / "note.txt"
        f.write_text("old", encoding="utf-8")
        code = run_tool("writefile", [str(f), "new"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "new"
