"""folderzip 工具测试。

验证 ``fcmd.cli.folderzip`` 模块：
- 工具注册（单命令工具，无子命令）
- archive_folder 单文件夹压缩
- 通过 run_tool 调用 folderzip --directory 批量压缩
- 跳过忽略目录、空目录与不存在目录处理
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.folderzip
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.folderzip import archive_folder


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """folderzip 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """folderzip 应在 _TOOL_REGISTRY 中注册。"""
        assert "folderzip" in _TOOL_REGISTRY, "工具 'folderzip' 未注册"

    def test_folderzip_single_command(self) -> None:
        """folderzip 是单命令工具（无子命令）。"""
        subs = fx.list_subcommands("folderzip")
        assert subs == []


# ---------------------------------------------------------------------- #
# folderzip 工具测试
# ---------------------------------------------------------------------- #
class TestFolderzip:
    """``folderzip`` 工具测试。"""

    def test_archive_folder_creates_zip(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """archive_folder 为单个文件夹创建 zip。"""
        src = tmp_path / "mydir"
        src.mkdir()
        (src / "a.txt").write_text("a", encoding="utf-8")
        (src / "sub").mkdir()
        (src / "sub" / "b.txt").write_text("b", encoding="utf-8")

        archive_folder(src)
        out = capsys.readouterr().out
        assert "压缩完成" in out
        assert "mydir.zip" in out

        zip_path = tmp_path / "mydir.zip"
        assert zip_path.exists()
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            assert any("a.txt" in n for n in names)
            assert any("b.txt" in n for n in names)

    def test_folderzip_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd folderzip --directory <dir> 压缩全部子文件夹。"""
        (tmp_path / "dir1").mkdir()
        (tmp_path / "dir1" / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "dir2").mkdir()
        (tmp_path / "dir2" / "b.txt").write_text("b", encoding="utf-8")

        code = run_tool("folderzip", ["--directory", str(tmp_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "dir1.zip" in out
        assert "dir2.zip" in out
        assert (tmp_path / "dir1.zip").exists()
        assert (tmp_path / "dir2.zip").exists()

    def test_folderzip_skips_ignore_dirs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """folderzip 跳过 __pycache__ 等忽略目录。"""
        (tmp_path / "real").mkdir()
        (tmp_path / "real" / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "c.pyc").write_bytes(b"c")

        code = run_tool("folderzip", ["--directory", str(tmp_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "real.zip" in out
        assert "__pycache__" not in out
        assert (tmp_path / "real.zip").exists()
        assert not (tmp_path / "__pycache__.zip").exists()

    def test_folderzip_nonexistent_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """folderzip 不存在的目录打印提示。"""
        code = run_tool("folderzip", ["--directory", str(tmp_path / "nonexistent")])
        assert code == 0  # 函数返回 None，run_tool 视为成功
        out = capsys.readouterr().out
        assert "目录不存在" in out

    def test_folderzip_empty_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """folderzip 空目录不产生压缩包。"""
        code = run_tool("folderzip", ["--directory", str(tmp_path)])
        assert code == 0
        out = capsys.readouterr().out
        # 空目录无子文件夹，不打印压缩完成
        assert "压缩完成" not in out
