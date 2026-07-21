"""folderback 工具测试。

验证 ``fcmd.cli.folderback`` 模块：
- 工具注册
- 旧备份清理
- 目录压缩
- CLI 调度
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.folderback
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.folderback import backup_folder, remove_old_backups, zip_target


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """folderback 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """folderback 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("folderback",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_folderback_single_command(self) -> None:
        """folderback 是单命令工具。"""
        assert fx.list_subcommands("folderback") == []


# ============================================================================ #
# folderback 测试
# ============================================================================ #
class TestFolderback:
    """folderback 工具测试。"""

    def test_remove_old_backups_no_files(self, tmp_path: Path) -> None:
        """remove_old_backups 无备份文件时不操作。"""
        remove_old_backups("test", tmp_path, max_zip=3)
        # 无异常即通过

    def test_remove_old_backups_under_limit(self, tmp_path: Path) -> None:
        """remove_old_backups 备份数不超过限制时不删除。"""
        for i in range(3):
            (tmp_path / f"test_2023010{i}_120000.zip").write_text("", encoding="utf-8")
        remove_old_backups("test", tmp_path, max_zip=5)
        zips = list(tmp_path.glob("*.zip"))
        assert len(zips) == 3

    def test_remove_old_backups_over_limit(self, tmp_path: Path) -> None:
        """remove_old_backups 超出限制时删除最旧的。"""
        for i in range(5):
            (tmp_path / f"test_2023010{i}_120000.zip").write_text("", encoding="utf-8")
        remove_old_backups("test", tmp_path, max_zip=2)
        zips = sorted(tmp_path.glob("*.zip"))
        assert len(zips) == 2
        # 保留最新的两个（03 和 04）
        names = [z.name for z in zips]
        assert "test_20230103_120000.zip" in names
        assert "test_20230104_120000.zip" in names

    def test_zip_target_creates_zip(self, tmp_path: Path) -> None:
        """zip_target 创建 zip 文件。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        (src / "subdir").mkdir()
        (src / "subdir" / "b.txt").write_text("world", encoding="utf-8")
        dst = tmp_path / "backup"
        dst.mkdir()

        zip_target(src, dst, max_zip=5)

        zips = list(dst.glob("*.zip"))
        assert len(zips) == 1
        # 验证 zip 内容
        with zipfile.ZipFile(zips[0]) as zf:
            names = zf.namelist()
            assert any("a.txt" in n for n in names)
            assert any("b.txt" in n for n in names)

    def test_backup_folder_src_not_exist(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """backup_folder 源不存在时打印提示。"""
        backup_folder(src=str(tmp_path / "nonexistent"), dst=str(tmp_path / "backup"))
        out = capsys.readouterr().out
        assert "不存在" in out

    def test_backup_folder_creates_dst(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """backup_folder 自动创建目标目录。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        dst = tmp_path / "backup"

        backup_folder(src=str(src), dst=str(dst), max_zip=3)

        out = capsys.readouterr().out
        assert "创建目标文件夹" in out
        assert "备份完成" in out
        zips = list(dst.glob("*.zip"))
        assert len(zips) == 1

    def test_backup_folder_default_src(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """backup_folder 默认备份当前目录。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        monkeypatch.chdir(src)
        dst = tmp_path / "backup"

        backup_folder(dst=str(dst), max_zip=3)

        out = capsys.readouterr().out
        assert "备份完成" in out

    def test_backup_folder_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd folderback 通过 run_tool 调用。"""
        src = tmp_path / "project"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        dst = tmp_path / "backup"

        code = run_tool("folderback", ["--src", str(src), "--dst", str(dst), "--max-zip", "3"])
        assert code == 0
        out = capsys.readouterr().out
        assert "备份完成" in out
