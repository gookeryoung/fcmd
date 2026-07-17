"""zipencrypt 工具测试。

验证 ``fcmd.cli.zipencrypt`` 模块：
- 工具注册与单命令结构
- ``_get_valid_entries`` 过滤逻辑（文件 + 非隐藏目录）
- ``_detect_encrypt_tool`` 工具检测（7z/zip/rar/none）
- ``_build_encrypt_cmd`` 命令构造（各工具参数差异）
- ``_create_unencrypted_zip`` zipfile 回退
- ``_make_archive`` 加密/跳过/覆盖/失败路径
- ``zip_encrypt`` 端到端（通过 run_tool 调用）
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from fcmd.apis.toolkit import _TOOL_REGISTRY, run_tool
from fcmd.cli.zipencrypt import (
    _build_encrypt_cmd,
    _create_unencrypted_zip,
    _detect_encrypt_tool,
    _get_valid_entries,
    _make_archive,
    zip_encrypt,
)
from fcmd.models import CommandResult


# ============================================================================ #
# 测试辅助
# ============================================================================ #
def _success_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回成功结果的 fake ``run_command``。"""
    return CommandResult(cmd=list(cmd), returncode=0, stdout="", stderr="")


def _fail_run(cmd: list[str], *, capture: bool = False, check: bool = False) -> CommandResult:
    """总是返回失败结果的 fake ``run_command``。"""
    return CommandResult(cmd=list(cmd), returncode=1, stdout="", stderr="encryption error")


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
# _detect_encrypt_tool
# ============================================================================ #
class TestDetectEncryptTool:
    """_detect_encrypt_tool 工具检测测试。"""

    def test_detect_7z(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """7z 可用时优先返回 7z。"""
        monkeypatch.setattr("fcmd.cli.zipencrypt.shutil.which", lambda cmd: "/usr/bin/7z" if cmd == "7z" else None)
        assert _detect_encrypt_tool() == "7z"

    def test_detect_zip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """仅 zip 可用时返回 zip。"""
        monkeypatch.setattr(
            "fcmd.cli.zipencrypt.shutil.which",
            lambda cmd: "/usr/bin/zip" if cmd == "zip" else None,
        )
        assert _detect_encrypt_tool() == "zip"

    def test_detect_rar(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """仅 rar 可用时返回 rar。"""
        monkeypatch.setattr(
            "fcmd.cli.zipencrypt.shutil.which",
            lambda cmd: "/usr/bin/rar" if cmd == "rar" else None,
        )
        assert _detect_encrypt_tool() == "rar"

    def test_detect_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """无任何工具时返回 None。"""
        monkeypatch.setattr("fcmd.cli.zipencrypt.shutil.which", lambda _: None)
        assert _detect_encrypt_tool() is None

    def test_priority_7z_over_zip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """7z 和 zip 都可用时优先 7z。"""
        monkeypatch.setattr(
            "fcmd.cli.zipencrypt.shutil.which",
            lambda cmd: f"/usr/bin/{cmd}" if cmd in ("7z", "zip") else None,
        )
        assert _detect_encrypt_tool() == "7z"


# ============================================================================ #
# _build_encrypt_cmd
# ============================================================================ #
class TestBuildEncryptCmd:
    """_build_encrypt_cmd 命令构造测试。"""

    def test_7z_cmd(self, tmp_path: Path) -> None:
        """7z 命令包含 AES256 加密参数。"""
        src = tmp_path / "file.txt"
        dst = tmp_path / "file.zip"
        cmd = _build_encrypt_cmd(src, dst, "secret", "7z")
        assert cmd[0] == "7z"
        assert cmd[1] == "a"
        assert "-psecret" in cmd
        assert "-mem=AES256" in cmd
        assert str(dst) in cmd
        assert str(src) in cmd

    def test_zip_cmd(self, tmp_path: Path) -> None:
        """zip 命令使用 -P 传密码。"""
        src = tmp_path / "file.txt"
        dst = tmp_path / "file.zip"
        cmd = _build_encrypt_cmd(src, dst, "pw", "zip")
        assert cmd[0] == "zip"
        assert "-r" in cmd
        assert "-Ppw" in cmd

    def test_rar_cmd(self, tmp_path: Path) -> None:
        """rar 命令使用 -p 传密码与 -m5 压缩级别。"""
        src = tmp_path / "file.txt"
        dst = tmp_path / "file.zip"
        cmd = _build_encrypt_cmd(src, dst, "pw", "rar")
        assert cmd[0] == "rar"
        assert "-ppw" in cmd
        assert "-m5" in cmd


# ============================================================================ #
# _create_unencrypted_zip
# ============================================================================ #
class TestCreateUnencryptedZip:
    """_create_unencrypted_zip zipfile 回退测试。"""

    def test_file_to_zip(self, tmp_path: Path) -> None:
        """单文件写入 ZIP，arcname 仅为文件名。"""
        src = tmp_path / "data.txt"
        src.write_text("content")
        dst = tmp_path / "data.zip"
        _create_unencrypted_zip(src, dst)
        assert dst.exists()
        with zipfile.ZipFile(dst) as zf:
            assert zf.namelist() == ["data.txt"]
            assert zf.read("data.txt").decode() == "content"

    def test_dir_to_zip(self, tmp_path: Path) -> None:
        """目录写入 ZIP，arcname 为相对路径。"""
        src = tmp_path / "proj"
        src.mkdir()
        (src / "a.txt").write_text("a")
        sub = src / "sub"
        sub.mkdir()
        (sub / "b.txt").write_text("b")
        dst = tmp_path / "proj.zip"
        _create_unencrypted_zip(src, dst)
        assert dst.exists()
        with zipfile.ZipFile(dst) as zf:
            names = sorted(zf.namelist())
            assert names == ["a.txt", "sub/b.txt"]


# ============================================================================ #
# _make_archive
# ============================================================================ #
class TestMakeArchive:
    """_make_archive 加密/跳过/覆盖/失败路径测试。"""

    def test_encrypt_file_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """使用外部工具成功加密文件。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        monkeypatch.setattr("fcmd.cli.zipencrypt.run_command", _success_run)

        result = _make_archive(src, "pw", "7z", replace=False)
        assert result is True
        captured = capsys.readouterr()
        assert "完成" in captured.out

    def test_skip_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """目标 ZIP 已存在且 replace=False 时跳过。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        (tmp_path / "doc.zip").write_text("existing")
        monkeypatch.setattr("fcmd.cli.zipencrypt.run_command", _success_run)

        result = _make_archive(src, "pw", "7z", replace=False)
        assert result is False
        captured = capsys.readouterr()
        assert "跳过" in captured.out

    def test_replace_existing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """目标 ZIP 已存在且 replace=True 时覆盖。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        existing = tmp_path / "doc.zip"
        existing.write_text("old")
        monkeypatch.setattr("fcmd.cli.zipencrypt.run_command", _success_run)

        result = _make_archive(src, "pw", "7z", replace=True)
        assert result is True
        captured = capsys.readouterr()
        assert "覆盖" in captured.out

    def test_encrypt_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """外部工具返回失败码时报告失败。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        monkeypatch.setattr("fcmd.cli.zipencrypt.run_command", _fail_run)

        result = _make_archive(src, "pw", "7z", replace=False)
        assert result is False
        captured = capsys.readouterr()
        assert "加密失败" in captured.out

    def test_unencrypted_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """tool=None 时使用 zipfile 回退（无加密）。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")
        # 不 mock run_command，确保 tool=None 路径不调用它

        result = _make_archive(src, "pw", tool=None, replace=False)
        assert result is True
        assert (tmp_path / "doc.zip").exists()
        with zipfile.ZipFile(tmp_path / "doc.zip") as zf:
            assert zf.read("doc.txt").decode() == "hello"

    def test_unencrypted_fallback_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """zipfile 回退抛 OSError 时报告失败。"""
        src = tmp_path / "doc.txt"
        src.write_text("hello")

        def _raise_oserror(filepath: Path, target_path: Path) -> None:
            raise OSError("disk full")

        monkeypatch.setattr("fcmd.cli.zipencrypt._create_unencrypted_zip", _raise_oserror)

        result = _make_archive(src, "pw", tool=None, replace=False)
        assert result is False
        captured = capsys.readouterr()
        assert "加密失败" in captured.out
        assert "disk full" in captured.out


# ============================================================================ #
# zip_encrypt（函数直接调用）
# ============================================================================ #
class TestZipEncryptFunction:
    """zip_encrypt 函数级测试（含边界条件）。"""

    def test_empty_password(self, capsys: pytest.CaptureFixture[str]) -> None:
        """空密码时提示并返回。"""
        zip_encrypt(".", "")
        captured = capsys.readouterr()
        assert "密码不能为空" in captured.out

    def test_nonexistent_dir(self, capsys: pytest.CaptureFixture[str]) -> None:
        """目录不存在时提示并返回。"""
        zip_encrypt("/nonexistent/path/xyz", "pw")
        captured = capsys.readouterr()
        assert "目录不存在" in captured.out

    def test_not_a_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """路径是文件而非目录时提示并返回。"""
        f = tmp_path / "file.txt"
        f.write_text("ok")
        zip_encrypt(str(f), "pw")
        captured = capsys.readouterr()
        assert "不是目录" in captured.out

    def test_empty_dir(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """空目录提示未找到目标文件。"""
        zip_encrypt(str(tmp_path), "pw")
        captured = capsys.readouterr()
        assert "未找到目标文件" in captured.out

    def test_full_run_with_mock(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """mock 7z 可用 + run_command 成功，验证完整流程。"""
        _make_dir_with_files(tmp_path)
        monkeypatch.setattr("fcmd.cli.zipencrypt.shutil.which", lambda cmd: "/usr/bin/7z" if cmd == "7z" else None)
        monkeypatch.setattr("fcmd.cli.zipencrypt.run_command", _success_run)

        zip_encrypt(str(tmp_path), "secret", replace=False)
        captured = capsys.readouterr()
        assert "使用 7z" in captured.out
        assert "开始加密 3 个" in captured.out
        assert "3/3 成功" in captured.out

    def test_full_run_unencrypted(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """无外部工具时回退到 zipfile。"""
        _make_dir_with_files(tmp_path)
        monkeypatch.setattr("fcmd.cli.zipencrypt.shutil.which", lambda _: None)

        zip_encrypt(str(tmp_path), "secret", replace=False)
        captured = capsys.readouterr()
        assert "无加密 zipfile" in captured.out
        # 验证实际生成了 zip 文件
        assert (tmp_path / "doc.zip").exists()
        assert (tmp_path / "config.zip").exists()
        assert (tmp_path / "project.zip").exists()


# ============================================================================ #
# run_tool 端到端
# ============================================================================ #
class TestRunToolEndToEnd:
    """通过 run_tool 调用 zipencrypt 的端到端测试。"""

    def test_run_tool_success(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """run_tool 调用 zipencrypt 成功执行。"""
        (tmp_path / "a.txt").write_text("content")
        monkeypatch.setattr("fcmd.cli.zipencrypt.shutil.which", lambda _: None)

        code = run_tool("zipencrypt", [str(tmp_path), "mypw"])
        assert code == 0
        captured = capsys.readouterr()
        assert "1/1 成功" in captured.out
        assert (tmp_path / "a.zip").exists()

    def test_run_tool_with_replace_flag(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--replace 标志透传到函数。"""
        (tmp_path / "a.txt").write_text("content")
        (tmp_path / "a.zip").write_text("old zip")
        monkeypatch.setattr("fcmd.cli.zipencrypt.shutil.which", lambda _: None)

        code = run_tool("zipencrypt", [str(tmp_path), "mypw", "--replace"])
        assert code == 0
        # 验证旧 zip 被覆盖（新 zip 是有效的 zipfile）
        with zipfile.ZipFile(tmp_path / "a.zip") as zf:
            assert zf.read("a.txt").decode() == "content"
