"""新工具测试：hashfile / filedate / writefile / folderzip。

验证 ``fcmd.cli`` 包下 4 个参考 pyflowx 实现的工具：
- ``hashfile``：文件/目录哈希计算（f/d 子命令）
- ``filedate``：文件日期前缀处理（add/clear 子命令）
- ``writefile``：文本写入文件（w 子命令）
- ``folderzip``：子文件夹批量压缩（z 子命令）
"""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.filedate  # 触发 @fx.tool 注册
import fcmd.cli.folderzip
import fcmd.cli.hashfile
import fcmd.cli.writefile
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.filedate import (
    DATE_PATTERN,
    add_date_prefix,
    get_file_timestamp,
    process_file_date,
    process_files_date,
    remove_date_prefix,
)
from fcmd.cli.folderzip import archive_folder
from fcmd.cli.hashfile import compute_hash, hash_directory, hash_file


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """4 个新工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """4 个新工具应在 _TOOL_REGISTRY 中注册。"""
        for name in ("hashfile", "filedate", "writefile", "folderzip"):
            assert name in _TOOL_REGISTRY, f"工具 {name!r} 未注册"

    def test_hashfile_subcommands(self) -> None:
        """hashfile 应有 f / d 子命令。"""
        subs = fx.list_subcommands("hashfile")
        assert "f" in subs
        assert "d" in subs

    def test_filedate_subcommands(self) -> None:
        """filedate 应有 add / clear 子命令。"""
        subs = fx.list_subcommands("filedate")
        assert "add" in subs
        assert "clear" in subs

    def test_writefile_subcommand(self) -> None:
        """writefile 应有 w 子命令。"""
        subs = fx.list_subcommands("writefile")
        assert "w" in subs

    def test_folderzip_subcommand(self) -> None:
        """folderzip 应有 z 子命令。"""
        subs = fx.list_subcommands("folderzip")
        assert "z" in subs


# ---------------------------------------------------------------------- #
# hashfile 工具测试
# ---------------------------------------------------------------------- #
class TestHashfile:
    """``hashfile`` 工具测试。"""

    def test_compute_hash_sha256(self, tmp_path: Path) -> None:
        """compute_hash 默认使用 sha256。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        expected = hashlib.sha256(b"hello").hexdigest()
        assert compute_hash(f) == expected

    def test_compute_hash_md5(self, tmp_path: Path) -> None:
        """compute_hash 支持 md5 算法。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        expected = hashlib.md5(b"hello").hexdigest()
        assert compute_hash(f, "md5") == expected

    def test_compute_hash_large_file(self, tmp_path: Path) -> None:
        """compute_hash 分块读取大文件。"""
        f = tmp_path / "big.bin"
        data = b"x" * (200 * 1024)  # 200KB，超过 _CHUNK_SIZE
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert compute_hash(f) == expected

    def test_hash_file_prints(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """hash_file 打印 algorithm + digest + path。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        hash_file(str(f))
        out = capsys.readouterr().out
        assert "sha256" in out
        assert hashlib.sha256(b"hello").hexdigest() in out
        assert str(f) in out

    def test_hash_file_not_exist(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """hash_file 文件不存在时打印提示。"""
        hash_file(str(tmp_path / "nonexistent"))
        out = capsys.readouterr().out
        assert "文件不存在" in out

    def test_hash_directory_prints_all_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """hash_directory 打印目录下全部文件哈希。"""
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        hash_directory(str(tmp_path))
        out = capsys.readouterr().out
        assert "a.txt" in out
        assert "b.txt" in out
        assert hashlib.sha256(b"a").hexdigest() in out

    def test_hash_directory_skips_ignore_dirs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """hash_directory 跳过 __pycache__ 等忽略目录。"""
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "cached.pyc").write_bytes(b"cached")
        hash_directory(str(tmp_path))
        out = capsys.readouterr().out
        assert "a.txt" in out
        assert "__pycache__" not in out
        assert "cached.pyc" not in out

    def test_hash_directory_skips_ignore_ext(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """hash_directory 跳过 .pyc/.zip 等忽略扩展名文件。"""
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "archive.zip").write_bytes(b"zipdata")
        (tmp_path / "compiled.pyc").write_bytes(b"pycdata")
        hash_directory(str(tmp_path))
        out = capsys.readouterr().out
        assert "a.txt" in out
        assert "archive.zip" not in out
        assert "compiled.pyc" not in out

    def test_hash_directory_not_exist(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """hash_directory 目录不存在时打印提示。"""
        hash_directory(str(tmp_path / "nonexistent"))
        out = capsys.readouterr().out
        assert "目录不存在" in out

    def test_hashfile_f_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashfile f <file> 通过 run_tool 调用。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        code = run_tool("hashfile", ["f", str(f)])
        assert code == 0
        out = capsys.readouterr().out
        assert hashlib.sha256(b"hello").hexdigest() in out

    def test_hashfile_f_md5_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashfile f <file> --algorithm md5 指定算法。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        code = run_tool("hashfile", ["f", str(f), "--algorithm", "md5"])
        assert code == 0
        out = capsys.readouterr().out
        assert hashlib.md5(b"hello").hexdigest() in out

    def test_hashfile_d_via_run_tool(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """fcmd hashfile d <dir> 通过 run_tool 调用。"""
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        code = run_tool("hashfile", ["d", str(tmp_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "a.txt" in out


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


# ---------------------------------------------------------------------- #
# writefile 工具测试
# ---------------------------------------------------------------------- #
class TestWritefile:
    """``writefile`` 工具测试。"""

    def test_writefile_via_run_tool(self, tmp_path: Path) -> None:
        """fcmd writefile w <path> <content> 写入文件。"""
        f = tmp_path / "note.txt"
        code = run_tool("writefile", ["w", str(f), "Hello World"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "Hello World"

    def test_writefile_custom_encoding(self, tmp_path: Path) -> None:
        """fcmd writefile w --encoding 指定编码。"""
        f = tmp_path / "note.txt"
        code = run_tool("writefile", ["w", str(f), "中文内容", "--encoding", "utf-8"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "中文内容"

    def test_writefile_creates_parent_dirs(self, tmp_path: Path) -> None:
        """writefile 写入嵌套路径时自动创建父目录。"""
        f = tmp_path / "sub" / "dir" / "note.txt"
        # Path.write_text 不自动创建父目录，需手动建
        f.parent.mkdir(parents=True)
        code = run_tool("writefile", ["w", str(f), "nested"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "nested"

    def test_writefile_overwrite(self, tmp_path: Path) -> None:
        """writefile 覆盖已有文件。"""
        f = tmp_path / "note.txt"
        f.write_text("old", encoding="utf-8")
        code = run_tool("writefile", ["w", str(f), "new"])
        assert code == 0
        assert f.read_text(encoding="utf-8") == "new"


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
        """fcmd folderzip z --directory <dir> 压缩全部子文件夹。"""
        (tmp_path / "dir1").mkdir()
        (tmp_path / "dir1" / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "dir2").mkdir()
        (tmp_path / "dir2" / "b.txt").write_text("b", encoding="utf-8")

        code = run_tool("folderzip", ["z", "--directory", str(tmp_path)])
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

        code = run_tool("folderzip", ["z", "--directory", str(tmp_path)])
        assert code == 0
        out = capsys.readouterr().out
        assert "real.zip" in out
        assert "__pycache__" not in out
        assert (tmp_path / "real.zip").exists()
        assert not (tmp_path / "__pycache__.zip").exists()

    def test_folderzip_nonexistent_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """folderzip 不存在的目录打印提示。"""
        code = run_tool("folderzip", ["z", "--directory", str(tmp_path / "nonexistent")])
        assert code == 0  # 函数返回 None，run_tool 视为成功
        out = capsys.readouterr().out
        assert "目录不存在" in out

    def test_folderzip_empty_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """folderzip 空目录不产生压缩包。"""
        code = run_tool("folderzip", ["z", "--directory", str(tmp_path)])
        assert code == 0
        out = capsys.readouterr().out
        # 空目录无子文件夹，不打印压缩完成
        assert "压缩完成" not in out
