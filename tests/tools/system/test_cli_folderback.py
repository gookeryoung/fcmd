"""folderback 工具测试。

验证 ``fcmd folderback``：
- 工具注册（单命令）
- zip 备份、时间戳命名、目标目录自动创建、旧备份清理
- 源目录缺失 → 任务失败汇总 + 退出码 1
- dry-run 不执行
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool


class TestRegistration:
    """folderback 注册为单命令工具。"""

    def test_registered(self) -> None:
        """folderback 已注册且无子命令。"""
        assert "folderback" in list_tools()
        assert list_subcommands("folderback") == []


class TestFolderbackRun:
    """``fcmd folderback`` 执行语义。"""

    def test_backup_creates_zip(self, tmp_path: Path) -> None:
        """备份源目录生成 zip（含源目录文件）。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        dst = tmp_path / "backup"
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst)]) == 0
        zips = list(dst.glob("proj_*.zip"))
        assert len(zips) == 1
        with zipfile.ZipFile(zips[0]) as zf:
            assert len(zf.namelist()) >= 1

    def test_backup_creates_missing_dst(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """目标目录缺失时自动创建并打印提示。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("x", encoding="utf-8")
        dst = tmp_path / "new_backup"
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst)]) == 0
        assert dst.is_dir()
        assert "创建目标文件夹" in capsys.readouterr().out

    def test_backup_missing_src_fails(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """源目录缺失 → 任务失败汇总 + 退出码 1。"""
        assert run_tool("folderback", ["--src", str(tmp_path / "nope"), "--dst", str(tmp_path / "b")]) == 1
        assert "失败" in capsys.readouterr().out

    def test_old_backups_cleaned(self, tmp_path: Path) -> None:
        """旧备份清理：超出 max_zip 时删除最旧的。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("x", encoding="utf-8")
        dst = tmp_path / "backup"
        dst.mkdir()
        # 预置 3 个旧备份（文件名尾部时间戳决定新旧排序）
        for date in ("20260101_000000", "20260102_000000", "20260103_000000"):
            (dst / f"proj_{date}.zip").write_bytes(b"PK\x05\x06")
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst), "--max-zip", "2"]) == 0
        names = {p.name for p in dst.glob("*.zip")}
        assert len(names) == 2
        assert "proj_20260101_000000.zip" not in names
        assert "proj_20260102_000000.zip" not in names

    def test_dry_run_skips_execution(self, tmp_path: Path) -> None:
        """--dry-run 打印计划不执行：不创建备份。"""
        src = tmp_path / "proj"
        src.mkdir()
        dst = tmp_path / "backup"
        assert run_tool("folderback", ["--src", str(src), "--dst", str(dst), "--dry-run"]) == 0
        assert not dst.exists()
