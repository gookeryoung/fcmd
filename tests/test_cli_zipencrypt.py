"""zipencrypt 工具测试。

验证 ``fcmd.tools.archive.zipencrypt`` 模块（pyzipper AES 实现）：
- 工具注册与单命令结构
- ``_get_valid_entries`` 过滤逻辑（文件 + 非隐藏目录）
- ``_require_pyzipper`` 惰性导入与缺失降级
- ``_write_aes_zip`` 写后可读回、AES 加密位（flag_bits / compress_type=99）
- ``_make_archive`` 跳过/覆盖/失败清理
- ``zip_encrypt`` 端到端（通过 run_tool 调用）
"""

from __future__ import annotations

import builtins
import zipfile
from pathlib import Path
from typing import Any

import pytest
import pyzipper

from fcmd.core.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.tools.archive import zipencrypt
from fcmd.tools.archive.zipencrypt import (
    _get_valid_entries,
    _make_archive,
    _require_pyzipper,
    _write_aes_zip,
    zip_encrypt,
)

_PASSWORD = "secret"


# ============================================================================ #
# 测试辅助
# ============================================================================ #
def _make_dir_with_files(base: Path) -> tuple[Path, list[Path]]:
    """创建包含文件和子目录的测试目录，返回 (目录, 条目列表)。"""
    (base / "doc.txt").write_text("hello")
    (base / "config.json").write_text('{"a": 1}')
    sub = base / "project"
    sub.mkdir()
    (sub / "main.py").write_text("print('hi')")
    hidden_dir = base / ".hidden"
    hidden_dir.mkdir()
    (hidden_dir / "secret.txt").write_text("secret")
    cache_dir = base / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "mod.pyc").write_text("binary")
    return base, [base / "doc.txt", base / "config.json", base / "project"]


def _read_aes(path: Path, password: str = _PASSWORD, name: str | None = None) -> bytes:
    """用 pyzipper 读回 AES ZIP 内容（验证加密包可正常解密）。"""
    with pyzipper.AESZipFile(path) as zf:
        zf.setpassword(password.encode("utf-8"))
        return zf.read(name) if name is not None else b"".join(zf.read(n) for n in zf.namelist())


# ============================================================================ #
# 注册验证
# ============================================================================ #
class TestRegistration:
    """工具注册验证。"""

    def test_registered(self) -> None:
        """zipencrypt 应在 _TOOL_REGISTRY 中注册。"""
        assert "zipencrypt" in _TOOL_REGISTRY

    def test_single_command(self) -> None:
        """zipencrypt 是单命令工具（无子命令）。"""
        assert None in _TOOL_REGISTRY["zipencrypt"]
        assert len(_TOOL_REGISTRY["zipencrypt"]) == 1


# ============================================================================ #
# _get_valid_entries
# ============================================================================ #
class TestGetValidEntries:
    """_get_valid_entries 过滤逻辑测试。"""

    def test_returns_files_and_non_hidden_dirs(self, tmp_path: Path) -> None:
        """返回所有文件 + 非隐藏目录。"""
        _make_dir_with_files(tmp_path)
        entries = _get_valid_entries(tmp_path)
        names = sorted(e.name for e in entries)
        assert names == ["config.json", "doc.txt", "project"]

    def test_includes_dotfiles(self, tmp_path: Path) -> None:
        """文件不受 _SKIP_PREFIXES 限制（.env 等配置文件需加密）。"""
        (tmp_path / ".env").write_text("SECRET=abc")
        (tmp_path / "normal.txt").write_text("ok")
        entries = _get_valid_entries(tmp_path)
        names = sorted(e.name for e in entries)
        assert names == [".env", "normal.txt"]

    def test_empty_dir(self, tmp_path: Path) -> None:
        """空目录返回空列表。"""
        assert _get_valid_entries(tmp_path) == []


# ============================================================================ #
# _require_pyzipper（惰性导入）
# ============================================================================ #
class TestRequirePyzipper:
    """pyzipper 惰性导入与缺失降级。"""

    def test_returns_module(self) -> None:
        """正常环境下返回 pyzipper 模块。"""
        assert _require_pyzipper() is pyzipper

    def test_missing_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pyzipper 不可导入时返回 None（不抛错）。"""
        monkeypatch.setattr(zipencrypt, "_pyzipper", None)
        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "pyzipper":
                raise ImportError("no pyzipper")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert _require_pyzipper() is None

    def test_missing_prints_install_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """缺库时 zip_encrypt 打印 extra 安装提示并返回。"""
        (tmp_path / "a.txt").write_text("x")
        monkeypatch.setattr(zipencrypt, "_require_pyzipper", lambda: None)
        zip_encrypt(str(tmp_path), _PASSWORD)
        out = capsys.readouterr().out
        assert "pip install fcmd[archive]" in out
        assert not (tmp_path / "a.zip").exists()


# ============================================================================ #
# _write_aes_zip
# ============================================================================ #
class TestWriteAesZip:
    """AES ZIP 写入：可读回 + 加密位。"""

    def test_file_read_back(self, tmp_path: Path) -> None:
        """写后可读回：单文件内容与 arcname 正确。"""
        src = tmp_path / "data.txt"
        src.write_text("content")
        dst = tmp_path / "data.zip"
        _write_aes_zip(pyzipper, src, dst, _PASSWORD)

        with pyzipper.AESZipFile(dst) as zf:
            assert zf.namelist() == ["data.txt"]
        assert _read_aes(dst, name="data.txt") == b"content"

    def test_dir_read_back(self, tmp_path: Path) -> None:
        """写后可读回：目录条目为相对路径。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("a")
        sub = src / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b")
        dst = tmp_path / "proj.zip"
        _write_aes_zip(pyzipper, src, dst, _PASSWORD)

        with pyzipper.AESZipFile(dst) as zf:
            assert sorted(zf.namelist()) == ["a.txt", "sub/b.txt"]
        assert _read_aes(dst, name="sub/b.txt") == b"b"

    def test_wrong_password_fails(self, tmp_path: Path) -> None:
        """错误密码无法解密（HMAC 校验失败）。"""
        src = tmp_path / "data.txt"
        src.write_text("content")
        dst = tmp_path / "data.zip"
        _write_aes_zip(pyzipper, src, dst, _PASSWORD)
        with pytest.raises(RuntimeError):
            _read_aes(dst, password="wrong", name="data.txt")

    def test_aes_encryption_flags(self, tmp_path: Path) -> None:
        """AES 加密位存在：encrypted 标志置位且压缩方法为 WinZip AES(99)。"""
        src = tmp_path / "data.txt"
        src.write_text("content")
        dst = tmp_path / "data.zip"
        _write_aes_zip(pyzipper, src, dst, _PASSWORD)

        with zipfile.ZipFile(dst) as zf:
            info = zf.getinfo("data.txt")
            assert info.flag_bits & 0x1  # 加密标志
            assert info.compress_type == 99  # WinZip AES
            with pytest.raises(RuntimeError, match="password"):
                zf.read("data.txt")

    def test_unicode_password(self, tmp_path: Path) -> None:
        """非 ASCII 密码按 UTF-8 编码后可用。"""
        src = tmp_path / "data.txt"
        src.write_text("content")
        dst = tmp_path / "data.zip"
        _write_aes_zip(pyzipper, src, dst, "密码123")
        assert _read_aes(dst, password="密码123", name="data.txt") == b"content"


# ============================================================================ #
# _make_archive
# ============================================================================ #
class TestMakeArchive:
    """_make_archive 跳过/覆盖/失败清理。"""

    def test_encrypt_file_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """成功加密文件并回显完成。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")

        assert _make_archive(src, _PASSWORD, pyzipper, replace=False) is True
        assert "完成" in capsys.readouterr().out
        assert _read_aes(tmp_path / "doc.zip", name="doc.txt") == b"hello"

    def test_skip_existing_keeps_original(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """目标 ZIP 已存在且 replace=False 时跳过，原文件不被改写。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        existing = tmp_path / "doc.zip"
        existing.write_text("existing")

        assert _make_archive(src, _PASSWORD, pyzipper, replace=False) is False
        assert "跳过" in capsys.readouterr().out
        assert existing.read_text() == "existing"

    def test_replace_existing(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """目标 ZIP 已存在且 replace=True 时覆盖为有效 AES 包。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        (tmp_path / "doc.zip").write_text("old")

        assert _make_archive(src, _PASSWORD, pyzipper, replace=True) is True
        assert "覆盖" in capsys.readouterr().out
        assert _read_aes(tmp_path / "doc.zip", name="doc.txt") == b"hello"

    def test_failure_cleans_partial_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """写入抛 OSError 时报告失败并清理半成品文件。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        target = tmp_path / "doc.zip"

        def _broken(module: Any, filepath: Path, target_path: Path, password: str) -> None:
            target_path.write_text("partial")
            raise OSError("disk full")

        monkeypatch.setattr(zipencrypt, "_write_aes_zip", _broken)

        assert _make_archive(src, _PASSWORD, pyzipper, replace=False) is False
        out = capsys.readouterr().out
        assert "加密失败" in out
        assert "disk full" in out
        assert not target.exists()


# ============================================================================ #
# zip_encrypt（函数直接调用）
# ============================================================================ #
class TestZipEncryptFunction:
    """zip_encrypt 函数级测试（含边界条件）。"""

    def test_empty_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        """空密码时提示并返回。"""
        zip_encrypt(".", "")
        assert "密码不能为空" in capsys.readouterr().out

    def test_nonexistent_dir(self, capsys: pytest.CaptureFixture[str]) -> None:
        """目录不存在时提示并返回。"""
        zip_encrypt("/nonexistent/path/xyz", _PASSWORD)
        assert "目录不存在" in capsys.readouterr().out

    def test_not_a_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """路径是文件而非目录时提示并返回。"""
        f = tmp_path / "file.txt"
        f.write_text("ok")
        zip_encrypt(str(f), _PASSWORD)
        assert "不是目录" in capsys.readouterr().out

    def test_empty_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """空目录提示未找到目标文件。"""
        zip_encrypt(str(tmp_path), _PASSWORD)
        assert "未找到目标文件" in capsys.readouterr().out

    def test_full_run(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """完整流程：3 个条目全部生成 AES 加密 ZIP。"""
        _make_dir_with_files(tmp_path)

        zip_encrypt(str(tmp_path), _PASSWORD, replace=False)
        out = capsys.readouterr().out
        assert "AES-256" in out
        assert "开始加密 3 个" in out
        assert "3/3 成功" in out

        assert _read_aes(tmp_path / "doc.zip", name="doc.txt") == b"hello"
        assert _read_aes(tmp_path / "config.zip", name="config.json") == b'{"a": 1}'
        assert _read_aes(tmp_path / "project.zip", name="main.py") == b"print('hi')"

        # 隐藏目录与 __pycache__ 不进包
        with pyzipper.AESZipFile(tmp_path / "project.zip") as zf:
            assert zf.namelist() == ["main.py"]


# ============================================================================ #
# run_tool 端到端
# ============================================================================ #
class TestRunToolEndToEnd:
    """通过 run_tool 调用 zipencrypt 的端到端测试。"""

    def test_run_tool_success(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """run_tool 调用 zipencrypt 成功执行并生成 AES 包。"""
        (tmp_path / "a.txt").write_text("content")

        code = run_tool("zipencrypt", [str(tmp_path), _PASSWORD])
        assert code == 0
        assert "1/1 成功" in capsys.readouterr().out
        assert _read_aes(tmp_path / "a.zip", name="a.txt") == b"content"

    def test_run_tool_with_replace_flag(self, tmp_path: Path) -> None:
        """--replace 标志透传到函数：旧 ZIP 被覆盖为有效 AES 包。"""
        (tmp_path / "a.txt").write_text("content")
        (tmp_path / "a.zip").write_text("old zip")

        code = run_tool("zipencrypt", [str(tmp_path), _PASSWORD, "--replace"])
        assert code == 0
        assert _read_aes(tmp_path / "a.zip", name="a.txt") == b"content"

    def test_run_tool_skips_existing_without_replace(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """默认不覆盖已存在 ZIP。"""
        (tmp_path / "a.txt").write_text("content")
        (tmp_path / "a.zip").write_text("old zip")

        code = run_tool("zipencrypt", [str(tmp_path), _PASSWORD])
        assert code == 0
        out = capsys.readouterr().out
        assert "已存在，跳过" in out
        assert "0/1 成功" in out
        assert (tmp_path / "a.zip").read_text() == "old zip"
