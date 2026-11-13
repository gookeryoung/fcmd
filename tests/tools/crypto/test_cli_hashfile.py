"""hashfile 工具测试。

验证 ``fcmd.tools.crypto.hashfile`` 工具与 ``fcmd.infras.hashing`` 实现：
- 工具注册（f/d 两子命令）
- f 子命令：单文件哈希、文件不存在提示、已知向量
- d 子命令：目录遍历、忽略目录/扩展名过滤
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from fcmd.core.toolkit import list_subcommands, list_tools, run_tool
from tests.helpers import run_and_capture as _run_and_capture


# ====================================================================== #
# 注册
# ====================================================================== #
class TestHashfileRegistration:
    """hashfile 注册与子命令结构。"""

    def test_registered(self) -> None:
        assert "hashfile" in list_tools()

    def test_subcommands(self) -> None:
        assert set(list_subcommands("hashfile")) == {"f", "d", "dupes"}


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
        # 目标不存在：NOT_FOUND（4）
        assert code == 4
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
        assert code == 4
        assert "目录不存在" in out

    def test_file_invalid_algorithm_keeps_exception(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """默认模式非法算法按 INVALID_INPUT 退出（3）。"""
        f = tmp_path / "a.txt"
        f.write_text("x", encoding="utf-8")
        assert run_tool("hashfile", ["f", str(f), "--algorithm", "nope"]) == 3
        capsys.readouterr()

    def test_dir_invalid_algorithm_keeps_exception(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """d 默认模式同样按 INVALID_INPUT 退出（3）。"""
        assert run_tool("hashfile", ["d", str(tmp_path), "--algorithm", "nope"]) == 3
        capsys.readouterr()


# ====================================================================== #
# JSON 机读输出
# ====================================================================== #
class TestHashfileJSON:
    """``--json`` 增量输出：默认人类可读行保持不变。"""

    def test_file_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """f --json 返回 algorithm/digest/path。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["f", str(f), "--json"], capsys)
        assert code == 0
        payload = json.loads(out)
        assert payload["schema"] == "fcmd-result/1"
        assert payload["data"]["algorithm"] == "sha256"
        assert payload["data"]["digest"] == hashlib.sha256(b"hello").hexdigest()
        assert Path(payload["data"]["path"]).name == "a.txt"

    def test_file_json_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """f --json 文件不存在：stdout 为空，stderr 为 FILE_NOT_FOUND 信封。"""
        f = tmp_path / "missing.txt"
        assert run_tool("hashfile", ["f", str(f), "--json"]) == 4
        captured = capsys.readouterr()
        assert captured.out == ""
        payload = json.loads(captured.err)
        assert payload["ok"] is False
        assert payload["code"] == "FILE_NOT_FOUND"
        assert payload["field"] == "path"

    def test_file_json_invalid_algorithm(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """f --json 非法算法：结构化错误 + 期望取值列表。"""
        f = tmp_path / "a.txt"
        f.write_text("hello", encoding="utf-8")
        assert run_tool("hashfile", ["f", str(f), "--algorithm", "nope", "--json"]) == 3
        captured = capsys.readouterr()
        assert captured.out == ""
        payload = json.loads(captured.err)
        assert payload["code"] == "INVALID_ALGORITHM"
        assert payload["field"] == "algorithm"
        assert "sha256" in payload["expected"]

    def test_dir_json(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """d --json 返回文件列表与总数。"""
        (tmp_path / "a.txt").write_text("x", encoding="utf-8")
        (tmp_path / "b.txt").write_text("y", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["d", str(tmp_path), "--json"], capsys)
        assert code == 0
        data = json.loads(out)["data"]
        assert data["total"] == 2
        assert {Path(item["path"]).name for item in data["files"]} == {"a.txt", "b.txt"}

    def test_dir_json_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """d --json 目录不存在：DIR_NOT_FOUND。"""
        assert run_tool("hashfile", ["d", str(tmp_path / "missing"), "--json"]) == 4
        payload = json.loads(capsys.readouterr().err)
        assert payload["code"] == "DIR_NOT_FOUND"
        assert payload["field"] == "directory"

    def test_dir_json_invalid_algorithm(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """d --json 非法算法：INVALID_ALGORITHM。"""
        assert run_tool("hashfile", ["d", str(tmp_path), "--algorithm", "nope", "--json"]) == 3
        assert json.loads(capsys.readouterr().err)["code"] == "INVALID_ALGORITHM"


# ====================================================================== #
# dupes 子命令 —— 只读重复文件查找
# ====================================================================== #
class TestHashfileDupes:
    """``hashfile dupes``：默认人读摘要，``--json`` 给全量结构。"""

    def test_human_groups(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """人类可读：组数、可回收量、保留项与重复项。"""
        (tmp_path / "a.txt").write_text("same", encoding="utf-8")
        (tmp_path / "b.txt").write_text("same", encoding="utf-8")
        (tmp_path / "c.txt").write_text("other", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["dupes", str(tmp_path)], capsys)
        assert code == 0
        assert "重复文件: 1 组" in out
        assert "保留:" in out
        assert "重复:" in out

    def test_human_no_dupes(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        (tmp_path / "a.txt").write_text("1", encoding="utf-8")
        (tmp_path / "b.txt").write_text("22", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["dupes", str(tmp_path)], capsys)
        assert code == 0
        assert "（无重复文件）" in out

    def test_human_limit_note(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``--limit`` 截断时给出总量脚注。"""
        (tmp_path / "big1.bin").write_bytes(b"x" * 100)
        (tmp_path / "big2.bin").write_bytes(b"x" * 100)
        (tmp_path / "small1.bin").write_bytes(b"y")
        (tmp_path / "small2.bin").write_bytes(b"y")
        code, out = _run_and_capture("hashfile", ["dupes", str(tmp_path), "--limit", "1"], capsys)
        assert code == 0
        assert "共 2 组" in out
        assert "已显示前 1 组" in out

    def test_min_size(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """默认跳过空文件，``--min-size 0`` 才包含。"""
        (tmp_path / "a.txt").write_bytes(b"")
        (tmp_path / "b.txt").write_bytes(b"")
        _, out = _run_and_capture("hashfile", ["dupes", str(tmp_path)], capsys)
        assert "（无重复文件）" in out
        _, out = _run_and_capture("hashfile", ["dupes", str(tmp_path), "--min-size", "0"], capsys)
        assert "重复文件: 1 组" in out

    def test_human_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        code, out = _run_and_capture("hashfile", ["dupes", str(tmp_path / "missing")], capsys)
        assert code == 4
        assert "目录不存在" in out

    def test_human_invalid_algorithm_keeps_exception(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """默认模式非法算法按 INVALID_INPUT 退出（3）。"""
        assert run_tool("hashfile", ["dupes", str(tmp_path), "--algorithm", "nope"]) == 3
        capsys.readouterr()

    def test_human_negative_min_size_keeps_exception(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert run_tool("hashfile", ["dupes", str(tmp_path), "--min-size", "-1"]) == 3
        capsys.readouterr()

    def test_json_payload(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``--json`` 给组明细与总量字段。"""
        (tmp_path / "a.txt").write_text("same", encoding="utf-8")
        (tmp_path / "b.txt").write_text("same", encoding="utf-8")
        (tmp_path / "c.txt").write_text("other", encoding="utf-8")
        code, out = _run_and_capture("hashfile", ["dupes", str(tmp_path), "--json"], capsys)
        assert code == 0
        payload = json.loads(out)
        assert payload["schema"] == "fcmd-result/1"
        assert payload["subcommand"] == "dupes"
        data = payload["data"]
        assert data["algorithm"] == "sha256"
        assert data["scanned"] == 3
        assert data["total_groups"] == 1
        assert data["reclaimable_bytes"] == 4
        assert data["returned"] == 1
        assert data["truncated"] is False
        group = data["groups"][0]
        assert group["size"] == 4
        assert group["wasted"] == 4
        assert len(group["paths"]) == 2

    def test_json_limit_truncated(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """``--limit`` 只截明细，总量仍是全量。"""
        (tmp_path / "big1.bin").write_bytes(b"x" * 100)
        (tmp_path / "big2.bin").write_bytes(b"x" * 100)
        (tmp_path / "small1.bin").write_bytes(b"y")
        (tmp_path / "small2.bin").write_bytes(b"y")
        _, out = _run_and_capture("hashfile", ["dupes", str(tmp_path), "--limit", "1", "--json"], capsys)
        data = json.loads(out)["data"]
        assert data["total_groups"] == 2
        assert data["returned"] == 1
        assert data["truncated"] is True

    def test_json_not_found(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert run_tool("hashfile", ["dupes", str(tmp_path / "missing"), "--json"]) == 4
        captured = capsys.readouterr()
        assert captured.out == ""
        payload = json.loads(captured.err)
        assert payload["code"] == "DIR_NOT_FOUND"
        assert payload["field"] == "directory"

    def test_json_invalid_algorithm(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert run_tool("hashfile", ["dupes", str(tmp_path), "--algorithm", "nope", "--json"]) == 3
        payload = json.loads(capsys.readouterr().err)
        assert payload["code"] == "INVALID_ALGORITHM"
        assert "sha256" in payload["expected"]

    def test_json_negative_min_size(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert run_tool("hashfile", ["dupes", str(tmp_path), "--min-size", "-1", "--json"]) == 3
        payload = json.loads(capsys.readouterr().err)
        assert payload["code"] == "INVALID_MIN_SIZE"
        assert payload["field"] == "min_size"
