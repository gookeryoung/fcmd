"""hashfile 工具测试。

验证 ``fcmd.cli.hashfile`` 模块：
- 工具注册与子命令结构
- compute_hash 哈希计算
- hash_file / hash_directory 文件与目录哈希
- 通过 run_tool 调用 f / d 子命令
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import fcmd as fx
import fcmd.cli.hashfile
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.hashfile import compute_hash, hash_directory, hash_file


# ---------------------------------------------------------------------- #
# 注册验证
# ---------------------------------------------------------------------- #
class TestToolsRegistration:
    """hashfile 工具的注册验证。"""

    def test_all_tools_registered(self) -> None:
        """hashfile 应在 _TOOL_REGISTRY 中注册。"""
        assert "hashfile" in _TOOL_REGISTRY, "工具 'hashfile' 未注册"

    def test_hashfile_subcommands(self) -> None:
        """hashfile 应有 f / d 子命令。"""
        subs = fx.list_subcommands("hashfile")
        assert "f" in subs
        assert "d" in subs


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
