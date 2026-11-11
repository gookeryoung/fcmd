"""hashfile 工具测试。

验证 ``fcmd.tools.crypto.hashfile`` 工具与 ``fcmd.infras.hashing`` 实现：
- 工具注册（f/d 两子命令）
- f 子命令：单文件哈希、文件不存在提示、已知向量
- d 子命令：目录遍历、忽略目录/扩展名过滤
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools
from tests.helpers import run_and_capture as _run_and_capture


# ====================================================================== #
# 注册
# ====================================================================== #
class TestHashfileRegistration:
    """hashfile 注册与子命令结构。"""

    def test_registered(self) -> None:
        assert "hashfile" in list_tools()

    def test_subcommands(self) -> None:
        assert set(list_subcommands("hashfile")) == {"f", "d"}


# ====================================================================== #
# f 子命令 —— 单文件哈希
# ====================================================================== #
class TestHashfileFile:
    """hashfile f 单文件哈希。"""

    def test_sha256_default(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """默认 sha256，输出格式 algorithm  digest  path。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["f", str(f)], capsys)
        assert code == 0
        expected = hashlib.sha256(b"hello").hexdigest()
        assert f"sha256  {expected}  {f}" == out.strip()

    def test_md5_explicit(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["f", str(f), "--algorithm", "md5"], capsys)
        assert code == 0
        expected = hashlib.md5(b"hello").hexdigest()
        assert f"md5  {expected}  {f}" == out.strip()

    def test_file_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        f = tmp_path / "nonexistent.txt"
        code, out = _run_and_capture("hashfile", ["f", str(f)], capsys)
        # 文件不存在不视为失败（工具内 print 提示后 return）
        assert code == 0
        assert "文件不存在" in out

    def test_large_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """1MB 文件分块读取无内存压力。"""
        f = tmp_path / "big.bin"
        f.write_bytes(b"x" * (1024 * 1024))
        code, out = _run_and_capture("hashfile", ["f", str(f)], capsys)
        assert code == 0
        expected = hashlib.sha256(b"x" * (1024 * 1024)).hexdigest()
        assert expected in out


# ====================================================================== #
# d 子命令 —— 目录遍历
# ====================================================================== #
class TestHashfileDir:
    """hashfile d 目录哈希。"""

    def test_iterates_all_files(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("y", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "c.txt").write_text("z", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["d", str(tmp_path)], capsys)
        assert code == 0
        assert "a.txt" in out
        assert "b.txt" in out
        assert "c.txt" in out

    def test_skips_ignore_dirs(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "good.txt").write_text("ok", encoding="utf-8")
        for dname in (".git", "__pycache__"):
            d = tmp_path / dname
            d.mkdir()
            (d / "secret.txt").write_text("skip me", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["d", str(tmp_path)], capsys)
        assert code == 0
        assert "secret.txt" not in out
        assert "good.txt" in out

    def test_directory_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("hashfile", ["d", str(tmp_path / "missing")], capsys)
        assert code == 0
        assert "目录不存在" in out
