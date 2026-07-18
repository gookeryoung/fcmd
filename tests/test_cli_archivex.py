"""archivex 工具测试。

验证 ``fcmd.cli.archivex`` 模块：
- 工具注册与两子命令结构（extract/list）
- ``detect_format`` 格式检测（7 种格式 + 不支持）
- ``extract_archive`` 解压（zip/tar/gz/bz2/xz 走 stdlib，7z/rar 走 mock）
- ``list_archive`` 列出内容（同上）
- CLI 子命令端到端（含 ``--output``、不存在文件、不支持格式）
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import tarfile
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest

from fcmd.apis.toolkit import list_subcommands, run_tool
from fcmd.cli.archivex import (
    _strip_compression_ext,
    detect_format,
    extract_archive,
    list_archive,
)


# ============================================================================ #
# 辅助函数
# ============================================================================ #
def _make_zip(path: Path, files: dict[str, str]) -> None:
    """创建 zip 归档。"""
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)


def _make_tar(path: Path, files: dict[str, str], mode: Literal["w", "w:gz", "w:bz2", "w:xz"] = "w") -> None:
    """创建 tar 归档（支持 w/w:gz/w:bz2/w:xz）。"""
    with tarfile.open(path, mode) as tf:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            data = content.encode("utf-8")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))


# ============================================================================ #
# 工具注册
# ============================================================================ #
class TestRegistration:
    """工具注册与子命令结构测试。"""

    def test_registered(self) -> None:
        """archivex 已注册到工具表。"""
        from fcmd.apis.toolkit import list_tools

        assert "archivex" in list_tools()

    def test_subcommands(self) -> None:
        """archivex 有 extract/list 两个子命令。"""
        subs = list_subcommands("archivex")
        assert set(subs) == {"extract", "list"}


# ============================================================================ #
# detect_format
# ============================================================================ #
class TestDetectFormat:
    """detect_format 格式检测测试。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("a.zip", "zip"),
            ("a.tar", "tar"),
            ("a.tar.gz", "tar"),
            ("a.tgz", "tar"),
            ("a.tar.bz2", "tar"),
            ("a.tbz", "tar"),
            ("a.tar.xz", "tar"),
            ("a.txz", "tar"),
            ("a.gz", "gz"),
            ("a.bz2", "bz2"),
            ("a.xz", "xz"),
            ("a.7z", "7z"),
            ("a.rar", "rar"),
            ("UPPER.ZIP", "zip"),
            ("Mixed.Tar.Gz", "tar"),
        ],
    )
    def test_formats(self, name: str, expected: str) -> None:
        """正确识别各格式扩展名（含大小写）。"""
        assert detect_format(Path(name)) == expected

    def test_unsupported(self) -> None:
        """不支持的扩展名抛 ValueError。"""
        with pytest.raises(ValueError, match="不支持的归档格式"):
            detect_format(Path("a.iso"))


# ============================================================================ #
# _strip_compression_ext
# ============================================================================ #
class TestStripExt:
    """_strip_compression_ext 去掉压缩扩展名测试。"""

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("file.txt.gz", "file.txt"),
            ("file.txt.bz2", "file.txt"),
            ("file.txt.xz", "file.txt"),
            ("FILE.TXT.GZ", "FILE.TXT"),
            ("noext.gz", "noext"),
            ("archive.tar.gz", "archive.tar"),  # 注意：只去掉 .gz
        ],
    )
    def test_strip(self, name: str, expected: str) -> None:
        assert _strip_compression_ext(name) == expected

    def test_no_match(self) -> None:
        """无压缩扩展名时原样返回。"""
        assert _strip_compression_ext("file.txt") == "file.txt"


# ============================================================================ #
# extract_archive
# ============================================================================ #
class TestExtractArchive:
    """extract_archive 解压测试。"""

    def test_zip(self, tmp_path: Path) -> None:
        """解压 zip 归档。"""
        archive = tmp_path / "test.zip"
        _make_zip(archive, {"a.txt": "hello", "b.txt": "world"})
        out = tmp_path / "out"
        extract_archive(archive, out)
        assert (out / "a.txt").read_text() == "hello"
        assert (out / "b.txt").read_text() == "world"

    def test_tar(self, tmp_path: Path) -> None:
        """解压 tar 归档。"""
        archive = tmp_path / "test.tar"
        _make_tar(archive, {"a.txt": "hello"})
        out = tmp_path / "out"
        extract_archive(archive, out)
        assert (out / "a.txt").read_text() == "hello"

    def test_tar_gz(self, tmp_path: Path) -> None:
        """解压 tar.gz 归档。"""
        archive = tmp_path / "test.tar.gz"
        _make_tar(archive, {"a.txt": "hello"}, mode="w:gz")
        out = tmp_path / "out"
        extract_archive(archive, out)
        assert (out / "a.txt").read_text() == "hello"

    def test_gz_single(self, tmp_path: Path) -> None:
        """解压单文件 gz。"""
        archive = tmp_path / "file.txt.gz"
        with gzip.open(archive, "wb") as f:
            f.write(b"compressed content")
        out = tmp_path / "out"
        extract_archive(archive, out)
        assert (out / "file.txt").read_text() == "compressed content"

    def test_bz2_single(self, tmp_path: Path) -> None:
        """解压单文件 bz2。"""
        archive = tmp_path / "file.txt.bz2"
        with bz2.open(archive, "wb") as f:
            f.write(b"bz2 content")
        out = tmp_path / "out"
        extract_archive(archive, out)
        assert (out / "file.txt").read_text() == "bz2 content"

    def test_xz_single(self, tmp_path: Path) -> None:
        """解压单文件 xz。"""
        archive = tmp_path / "file.txt.xz"
        with lzma.open(archive, "wb") as f:
            f.write(b"xz content")
        out = tmp_path / "out"
        extract_archive(archive, out)
        assert (out / "file.txt").read_text() == "xz content"

    def test_unsupported_raises(self, tmp_path: Path) -> None:
        """不支持的格式抛 ValueError。"""
        archive = tmp_path / "a.iso"
        archive.write_bytes(b"data")
        with pytest.raises(ValueError, match="不支持的归档格式"):
            extract_archive(archive, tmp_path / "out")

    def test_7z_mocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """7z 解压走外部命令（mock subprocess.run + shutil.which）。"""
        archive = tmp_path / "a.7z"
        archive.write_bytes(b"fake 7z")
        out = tmp_path / "out"

        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda cmd: "/usr/bin/" + cmd)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("fcmd.cli.archivex.subprocess.run", fake_run)
        extract_archive(archive, out)
        assert calls and calls[0][0] == "7z"
        assert "x" in calls[0]

    def test_rar_mocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """rar 解压优先用 unrar。"""
        archive = tmp_path / "a.rar"
        archive.write_bytes(b"fake rar")
        out = tmp_path / "out"

        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda cmd: "/usr/bin/" + cmd)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        monkeypatch.setattr("fcmd.cli.archivex.subprocess.run", fake_run)
        extract_archive(archive, out)
        assert calls and calls[0][0] == "unrar"

    def test_external_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """外部命令未找到时抛 FileNotFoundError。"""
        archive = tmp_path / "a.7z"
        archive.write_bytes(b"fake")
        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda _: None)
        with pytest.raises(FileNotFoundError, match="未找到解压命令"):
            extract_archive(archive, tmp_path / "out")

    def test_external_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """外部命令失败时抛 RuntimeError。"""
        archive = tmp_path / "a.7z"
        archive.write_bytes(b"fake")
        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda cmd: "/usr/bin/" + cmd)

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=1, stdout="", stderr="corrupt archive")

        monkeypatch.setattr("fcmd.cli.archivex.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="解压失败"):
            extract_archive(archive, tmp_path / "out")


# ============================================================================ #
# list_archive
# ============================================================================ #
class TestListArchive:
    """list_archive 列出内容测试。"""

    def test_zip(self, tmp_path: Path) -> None:
        """列出 zip 内容。"""
        archive = tmp_path / "test.zip"
        _make_zip(archive, {"a.txt": "x", "b.txt": "y"})
        names = list_archive(archive)
        assert set(names) == {"a.txt", "b.txt"}

    def test_tar(self, tmp_path: Path) -> None:
        """列出 tar 内容。"""
        archive = tmp_path / "test.tar"
        _make_tar(archive, {"a.txt": "x"})
        names = list_archive(archive)
        assert "a.txt" in names

    def test_gz_single(self, tmp_path: Path) -> None:
        """列出单文件 gz（返回解压后文件名）。"""
        archive = tmp_path / "file.txt.gz"
        with gzip.open(archive, "wb") as f:
            f.write(b"x")
        names = list_archive(archive)
        assert names == ["file.txt"]

    def test_7z_mocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """7z 列出走外部命令。"""
        archive = tmp_path / "a.7z"
        archive.write_bytes(b"fake")
        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda cmd: "/usr/bin/" + cmd)

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=0, stdout="line1\nline2\n\n", stderr="")

        monkeypatch.setattr("fcmd.cli.archivex.subprocess.run", fake_run)
        names = list_archive(archive)
        assert names == ["line1", "line2"]

    def test_unsupported_raises(self, tmp_path: Path) -> None:
        """不支持的格式抛 ValueError。"""
        archive = tmp_path / "a.iso"
        archive.write_bytes(b"data")
        with pytest.raises(ValueError, match="不支持的归档格式"):
            list_archive(archive)

    def test_external_not_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """外部命令未找到时抛 FileNotFoundError。"""
        archive = tmp_path / "a.7z"
        archive.write_bytes(b"fake")
        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda _: None)
        with pytest.raises(FileNotFoundError, match="未找到解压命令"):
            list_archive(archive)

    def test_external_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """列出外部命令失败时抛 RuntimeError。"""
        archive = tmp_path / "a.7z"
        archive.write_bytes(b"fake")
        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda cmd: "/usr/bin/" + cmd)

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=1, stdout="", stderr="corrupt archive")

        monkeypatch.setattr("fcmd.cli.archivex.subprocess.run", fake_run)
        with pytest.raises(RuntimeError, match="列出失败"):
            list_archive(archive)

    def test_rar_mocked(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """rar 列出走 unrar 外部命令。"""
        archive = tmp_path / "a.rar"
        archive.write_bytes(b"fake rar")
        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda cmd: "/usr/bin/" + cmd)
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            calls.append(cmd)
            return SimpleNamespace(returncode=0, stdout="file1.txt\nfile2.txt\n", stderr="")

        monkeypatch.setattr("fcmd.cli.archivex.subprocess.run", fake_run)
        names = list_archive(archive)
        assert calls and calls[0][0] == "unrar"
        assert "l" in calls[0]
        assert names == ["file1.txt", "file2.txt"]


# ============================================================================ #
# CLI 子命令端到端
# ============================================================================ #
class TestCLISubcommands:
    """CLI 子命令端到端测试（通过 run_tool 调用）。"""

    def test_extract_zip_default_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """extract 默认输出到 archive stem 目录。"""
        archive = tmp_path / "data.zip"
        _make_zip(archive, {"a.txt": "hello"})
        code = run_tool("archivex", ["extract", str(archive)])
        assert code == 0
        out_dir = tmp_path / "data"
        assert (out_dir / "a.txt").read_text() == "hello"
        captured = capsys.readouterr()
        assert "解压完成" in captured.out

    def test_extract_with_output(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """extract --output 指定输出目录。"""
        archive = tmp_path / "data.zip"
        _make_zip(archive, {"a.txt": "hello"})
        out_dir = tmp_path / "custom"
        code = run_tool("archivex", ["extract", str(archive), "--output", str(out_dir)])
        assert code == 0
        assert (out_dir / "a.txt").read_text() == "hello"

    def test_extract_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """extract 归档不存在时提示。"""
        code = run_tool("archivex", ["extract", str(tmp_path / "no.zip")])
        assert code == 0
        captured = capsys.readouterr()
        assert "归档文件不存在" in captured.out

    def test_extract_unsupported(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """extract 不支持的格式时提示。"""
        archive = tmp_path / "a.iso"
        archive.write_bytes(b"data")
        code = run_tool("archivex", ["extract", str(archive)])
        assert code == 0
        captured = capsys.readouterr()
        assert "不支持的归档格式" in captured.out

    def test_list_zip(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """list 列出 zip 内容。"""
        archive = tmp_path / "data.zip"
        _make_zip(archive, {"a.txt": "x", "b.txt": "y"})
        code = run_tool("archivex", ["list", str(archive)])
        assert code == 0
        captured = capsys.readouterr()
        assert "a.txt" in captured.out
        assert "b.txt" in captured.out
        assert "2 项" in captured.out

    def test_list_nonexistent(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """list 归档不存在时提示。"""
        code = run_tool("archivex", ["list", str(tmp_path / "no.zip")])
        assert code == 0
        captured = capsys.readouterr()
        assert "归档文件不存在" in captured.out

    def test_list_unsupported(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """list 不支持的格式时提示。"""
        archive = tmp_path / "a.iso"
        archive.write_bytes(b"data")
        code = run_tool("archivex", ["list", str(archive)])
        assert code == 0
        captured = capsys.readouterr()
        assert "不支持的归档格式" in captured.out

    def test_extract_external_failure_handled(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """extract 外部命令失败时 CLI 层捕获并提示。"""
        archive = tmp_path / "a.7z"
        archive.write_bytes(b"fake")
        monkeypatch.setattr("fcmd.cli.archivex.shutil.which", lambda cmd: "/usr/bin/" + cmd)

        def fake_run(cmd: list[str], **kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(returncode=1, stdout="", stderr="corrupt")

        monkeypatch.setattr("fcmd.cli.archivex.subprocess.run", fake_run)
        code = run_tool("archivex", ["extract", str(archive)])
        assert code == 0
        captured = capsys.readouterr()
        assert "解压失败" in captured.out

    def test_list_empty_zip(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """list 空归档时提示（空归档）。"""
        archive = tmp_path / "empty.zip"
        _make_zip(archive, {})
        code = run_tool("archivex", ["list", str(archive)])
        assert code == 0
        captured = capsys.readouterr()
        assert "（空归档）" in captured.out
