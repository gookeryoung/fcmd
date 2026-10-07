"""hashfile 工具测试（DSL 内建动作声明 commands/hashfile.toml）。

验证 ``fcmd hashfile`` 的 DSL action 迁移语义：
- 工具注册（多子命令 DSL 工具，内置声明）
- 声明契约：__dsl_action__ 标记、无 cmd（fn 任务形态）
- f 子命令：单文件哈希、文件不存在提示、已知向量
- d 子命令：目录遍历、忽略目录/扩展名过滤
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fcmd.apis._tool_args import ToolSpec
from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli._discovery import ensure_tools_discovered

ensure_tools_discovered()


def _run_and_capture(name: str, argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    code = run_tool(name, argv)
    raw = capsys.readouterr().out
    # 过滤掉控制台帧（以 > 或 OK/FAILED 开头的行），保留动作 print 输出
    lines = [
        ln
        for ln in raw.splitlines()
        if not ln.startswith("> ") and not ln.startswith("OK ") and not ln.startswith("FAILED ")
    ]
    return code, "\n".join(lines)


# ====================================================================== #
# 注册与声明契约
# ====================================================================== #
class TestHashfileRegistration:
    """hashfile 经内置 DSL（action 原语）注册。"""

    def test_subcommands(self) -> None:
        assert set(_TOOL_REGISTRY["hashfile"]) == {"f", "d"}

    @pytest.mark.parametrize("sub, action", [("f", "hashfile_f"), ("d", "hashfile_d")])
    def test_action_contract(self, sub: str, action: str) -> None:
        spec: ToolSpec = _TOOL_REGISTRY["hashfile"][sub]
        assert spec.cmd is None
        assert getattr(spec.func, "__dsl_action__", None) == action


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
        # 文件不存在不视为失败（动作内 print 提示后 return）
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
