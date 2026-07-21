"""filelevel 工具测试。

验证 ``fcmd.cli.filelevel`` 模块：
- 工具注册
- 标记移除
- 单文件等级处理
- 批量等级处理
- CLI 调度
"""

from __future__ import annotations

from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.filelevel
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.filelevel import (
    process_file_level,
    process_files_level,
    remove_marks,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestToolsRegistration:
    """filelevel 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """filelevel 应在 _TOOL_REGISTRY 中注册。"""
        for name in ("filelevel",):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_filelevel_subcommands(self) -> None:
        """filelevel 应有 set 子命令。"""
        subs = fx.list_subcommands("filelevel")
        assert "set" in subs


# ============================================================================ #
# filelevel 测试
# ============================================================================ #
class TestRemoveMarks:
    """remove_marks 函数测试。"""

    def test_remove_single_mark(self) -> None:
        """移除单个括号包裹的标记。"""
        assert remove_marks("file(PUB).txt", ["PUB"]) == "file.txt"

    def test_remove_multiple_marks(self) -> None:
        """移除多个标记。"""
        assert remove_marks("file(PUB)(NOR).txt", ["PUB", "NOR"]) == "file.txt"

    def test_remove_mark_with_different_brackets(self) -> None:
        """支持多种括号类型。"""
        assert remove_marks("file[PUB].txt", ["PUB"]) == "file.txt"
        assert remove_marks("file_PUB_.txt", ["PUB"]) == "file.txt"
        assert remove_marks("file【PUB】.txt", ["PUB"]) == "file.txt"

    def test_remove_mark_not_in_brackets(self) -> None:
        """裸标记（无括号包裹）不移除。"""
        assert remove_marks("filePUB.txt", ["PUB"]) == "filePUB.txt"

    def test_remove_mark_not_found(self) -> None:
        """标记不存在时原样返回。"""
        assert remove_marks("file.txt", ["PUB"]) == "file.txt"

    def test_remove_mark_at_boundary(self) -> None:
        """标记在边界时安全处理。"""
        # 标记在开头，左侧无括号
        assert remove_marks("PUB_file.txt", ["PUB"]) == "PUB_file.txt"


class TestProcessFileLevel:
    """process_file_level 函数测试。"""

    def test_set_level_1(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """设置等级 1 (PUB)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=1)
        out = capsys.readouterr().out
        assert "重命名" in out
        assert (tmp_path / "report(PUB).pdf").exists()
        assert not f.exists()

    def test_set_level_2(self, tmp_path: Path) -> None:
        """设置等级 2 (INT)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=2)
        assert (tmp_path / "report(INT).pdf").exists()

    def test_set_level_3(self, tmp_path: Path) -> None:
        """设置等级 3 (CON)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=3)
        assert (tmp_path / "report(CON).pdf").exists()

    def test_set_level_4(self, tmp_path: Path) -> None:
        """设置等级 4 (CLA)。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=4)
        assert (tmp_path / "report(CLA).pdf").exists()

    def test_clear_level(self, tmp_path: Path) -> None:
        """等级 0 清除已有标记。"""
        f = tmp_path / "report(PUB).pdf"
        f.write_text("content")
        process_file_level(f, level=0)
        assert (tmp_path / "report.pdf").exists()

    def test_replace_existing_level(self, tmp_path: Path) -> None:
        """已有等级时替换为新等级。"""
        f = tmp_path / "report(PUB).pdf"
        f.write_text("content")
        process_file_level(f, level=3)
        assert (tmp_path / "report(CON).pdf").exists()
        assert not (tmp_path / "report(PUB)(CON).pdf").exists()

    def test_invalid_level_high(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无效等级（过高）打印提示。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=99)
        out = capsys.readouterr().out
        assert "无效的等级" in out
        assert f.exists()  # 文件未被重命名

    def test_invalid_level_negative(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无效等级（负数）打印提示。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=-1)
        out = capsys.readouterr().out
        assert "无效的等级" in out

    def test_file_not_exists(self, capsys: pytest.CaptureFixture[str]) -> None:
        """文件不存在时打印提示。"""
        process_file_level(Path("nonexistent.pdf"), level=1)
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_no_change_when_already_correct(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """文件名无变化时不重命名。"""
        f = tmp_path / "report.pdf"
        f.write_text("content")
        process_file_level(f, level=0)
        out = capsys.readouterr().out
        assert "重命名" not in out
        assert f.exists()


class TestProcessFilesLevel:
    """process_files_level 批量处理测试。"""

    def test_batch_set_level(self, tmp_path: Path) -> None:
        """批量设置等级。"""
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_text("1")
        f2.write_text("2")
        process_files_level([f1, f2], level=2)
        assert (tmp_path / "a(INT).pdf").exists()
        assert (tmp_path / "b(INT).pdf").exists()

    def test_batch_clear_level(self, tmp_path: Path) -> None:
        """批量清除等级。"""
        f1 = tmp_path / "a(PUB).pdf"
        f2 = tmp_path / "b(CON).pdf"
        f1.write_text("1")
        f2.write_text("2")
        process_files_level([f1, f2], level=0)
        assert (tmp_path / "a.pdf").exists()
        assert (tmp_path / "b.pdf").exists()

    def test_filelevel_via_run_tool(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """fcmd filelevel set <files> --level 2 通过 run_tool 调用。"""
        monkeypatch.chdir(tmp_path)
        f = tmp_path / "doc.pdf"
        f.write_text("content")
        code = run_tool("filelevel", ["set", "doc.pdf", "--level", "2"])
        assert code == 0
        assert (tmp_path / "doc(INT).pdf").exists()


# ============================================================================ #
# filelevel 循环分支补充测试
# ============================================================================ #
class TestFilelevelBranches:
    """filelevel 循环分支补充测试。"""

    def test_process_files_level_empty_list(self) -> None:
        """process_files_level 空列表不报错。"""
        process_files_level([], level=1)  # 不抛异常即可

    def test_process_files_level_mixed_existence(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """process_files_level 部分文件不存在时跳过。"""
        f1 = tmp_path / "exists.pdf"
        f1.write_text("content")
        f2 = tmp_path / "missing.pdf"
        process_files_level([f1, f2], level=2)
        out = capsys.readouterr().out
        assert "文件不存在" in out
        assert (tmp_path / "exists(INT).pdf").exists()
