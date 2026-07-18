"""archivex 工具测试。

验证 ``fcmd.cli.archivex`` 模块：
- 工具注册与三子命令结构（extract/list/create）
- ``detect_format`` 格式检测（7 种格式 + 不支持）
- ``extract_archive`` 解压（zip/tar/gz/bz2/xz 走 stdlib，7z/rar 走 mock）
- ``list_archive`` 列出内容（同上）
- ``create_archive`` 创建归档（zip/tar/gz/bz2/xz + 目录/文件模式 + 忽略规则）
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
    create_archive,
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
        """archivex 有 extract/list/create 三个子命令。"""
        subs = list_subcommands("archivex")
        assert set(subs) == {"extract", "list", "create"}


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
# create_archive
# ============================================================================ #
class TestCreateArchive:
    """create_archive 创建归档测试。"""

    def test_create_zip_directory(self, tmp_path: Path) -> None:
        """目录打包为 zip 并往返验证。"""
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("hello", encoding="utf-8")
        (src / "sub" / "b.txt").write_text("world", encoding="utf-8")
        output = tmp_path / "out.zip"
        create_archive(src, output)
        assert output.is_file()
        # 往返验证：解压后内容一致
        extract_dir = tmp_path / "extracted"
        extract_archive(output, extract_dir)
        assert (extract_dir / "a.txt").read_text(encoding="utf-8") == "hello"
        assert (extract_dir / "sub" / "b.txt").read_text(encoding="utf-8") == "world"

    def test_create_tar_gz_directory(self, tmp_path: Path) -> None:
        """目录打包为 tar.gz 并往返验证。"""
        src = tmp_path / "src"
        (src / "sub").mkdir(parents=True)
        (src / "a.txt").write_text("hello", encoding="utf-8")
        (src / "sub" / "b.txt").write_text("world", encoding="utf-8")
        output = tmp_path / "out.tar.gz"
        create_archive(src, output)
        assert output.is_file()
        # tar 归档内文件名正确（POSIX 分隔符）
        with tarfile.open(output, "r:gz") as tf:
            names = tf.getnames()
        assert "a.txt" in names
        assert "sub/b.txt" in names

    def test_create_tar_bz2_directory(self, tmp_path: Path) -> None:
        """目录打包为 tar.bz2。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.tar.bz2"
        create_archive(src, output)
        assert output.is_file()
        with tarfile.open(output, "r:bz2") as tf:
            assert "a.txt" in tf.getnames()

    def test_create_tar_xz_directory(self, tmp_path: Path) -> None:
        """目录打包为 tar.xz。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.tar.xz"
        create_archive(src, output)
        assert output.is_file()
        with tarfile.open(output, "r:xz") as tf:
            assert "a.txt" in tf.getnames()

    def test_create_tar_plain_directory(self, tmp_path: Path) -> None:
        """目录打包为 .tar（无压缩）。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.tar"
        create_archive(src, output)
        assert output.is_file()
        with tarfile.open(output, "r:") as tf:
            assert "a.txt" in tf.getnames()

    def test_create_tgz_short_ext(self, tmp_path: Path) -> None:
        """`.tgz` 短扩展名等价于 `.tar.gz`。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.tgz"
        create_archive(src, output)
        with tarfile.open(output, "r:gz") as tf:
            assert "a.txt" in tf.getnames()

    def test_create_zip_single_file(self, tmp_path: Path) -> None:
        """单文件打包为 zip。"""
        src = tmp_path / "data.txt"
        src.write_text("hello", encoding="utf-8")
        output = tmp_path / "out.zip"
        create_archive(src, output)
        with zipfile.ZipFile(output) as zf:
            assert zf.namelist() == ["data.txt"]
            assert zf.read("data.txt").decode("utf-8") == "hello"

    def test_create_tar_single_file(self, tmp_path: Path) -> None:
        """单文件打包为 tar.gz。"""
        src = tmp_path / "data.txt"
        src.write_text("hello", encoding="utf-8")
        output = tmp_path / "out.tar.gz"
        create_archive(src, output)
        with tarfile.open(output, "r:gz") as tf:
            assert tf.getnames() == ["data.txt"]

    def test_create_gz_single_file(self, tmp_path: Path) -> None:
        """单文件压缩为 .gz 并往返验证。"""
        src = tmp_path / "data.txt"
        src.write_text("hello", encoding="utf-8")
        output = tmp_path / "data.txt.gz"
        create_archive(src, output)
        # 解压回原始内容
        extract_dir = tmp_path / "extracted"
        extract_archive(output, extract_dir)
        assert (extract_dir / "data.txt").read_text(encoding="utf-8") == "hello"

    def test_create_bz2_single_file(self, tmp_path: Path) -> None:
        """单文件压缩为 .bz2。"""
        src = tmp_path / "data.txt"
        src.write_text("hello", encoding="utf-8")
        output = tmp_path / "data.txt.bz2"
        create_archive(src, output)
        with bz2.open(output, "rb") as f:
            assert f.read().decode("utf-8") == "hello"

    def test_create_xz_single_file(self, tmp_path: Path) -> None:
        """单文件压缩为 .xz。"""
        src = tmp_path / "data.txt"
        src.write_text("hello", encoding="utf-8")
        output = tmp_path / "data.txt.xz"
        create_archive(src, output)
        with lzma.open(output, "rb") as f:
            assert f.read().decode("utf-8") == "hello"

    def test_create_directory_to_single_file_format_raises(self, tmp_path: Path) -> None:
        """目录压缩为单文件格式（gz/bz2/xz）抛 ValueError。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        for ext in (".gz", ".bz2", ".xz"):
            output = tmp_path / f"out{ext}"
            with pytest.raises(ValueError, match="目录无法压缩为单文件格式"):
                create_archive(src, output)

    def test_create_7z_unsupported(self, tmp_path: Path) -> None:
        """7z 格式不支持创建。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.7z"
        with pytest.raises(ValueError, match="create 不支持格式 7z"):
            create_archive(src, output)

    def test_create_rar_unsupported(self, tmp_path: Path) -> None:
        """rar 格式不支持创建。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.rar"
        with pytest.raises(ValueError, match="create 不支持格式 rar"):
            create_archive(src, output)

    def test_create_unsupported_ext_raises(self, tmp_path: Path) -> None:
        """不支持的扩展名抛 ValueError（detect_format）。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.xyz"
        with pytest.raises(ValueError, match="不支持的归档格式"):
            create_archive(src, output)

    def test_create_source_not_found(self, tmp_path: Path) -> None:
        """源不存在抛 FileNotFoundError。"""
        output = tmp_path / "out.zip"
        with pytest.raises(FileNotFoundError, match="源路径不存在"):
            create_archive(tmp_path / "missing", output)

    def test_create_creates_parent_dir(self, tmp_path: Path) -> None:
        """输出路径的父目录不存在时自动创建。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "deep" / "nested" / "out.zip"
        create_archive(src, output)
        assert output.is_file()

    def test_create_applies_ignore_dirs(self, tmp_path: Path) -> None:
        """默认跳过 _common.IGNORE_DIRS 中的目录。"""
        src = tmp_path / "src"
        (src / ".git").mkdir(parents=True)
        (src / "a.txt").write_text("keep", encoding="utf-8")
        (src / ".git" / "config").write_text("skip", encoding="utf-8")
        output = tmp_path / "out.zip"
        create_archive(src, output)
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "a.txt" in names
        assert all(".git" not in n for n in names)

    def test_create_applies_ignore_ext(self, tmp_path: Path) -> None:
        """默认跳过 _common.IGNORE_EXT 中的扩展名（如 .pyc）。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("keep", encoding="utf-8")
        (src / "b.pyc").write_bytes(b"\x00\x00pyc")
        output = tmp_path / "out.zip"
        create_archive(src, output)
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "a.txt" in names
        assert "b.pyc" not in names

    def test_create_applies_ignore_dirs_glob(self, tmp_path: Path) -> None:
        """通配模式 ``*.egg-info`` 命中目录被跳过。"""
        src = tmp_path / "src"
        (src / "fcmd.egg-info").mkdir(parents=True)
        (src / "a.txt").write_text("keep", encoding="utf-8")
        (src / "fcmd.egg-info" / "PKG-INFO").write_text("skip", encoding="utf-8")
        output = tmp_path / "out.zip"
        create_archive(src, output)
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "a.txt" in names
        assert all("egg-info" not in n for n in names)

    def test_create_custom_ignore_dirs(self, tmp_path: Path) -> None:
        """自定义 ignore_dirs 集合。"""
        src = tmp_path / "src"
        (src / "skipme").mkdir(parents=True)
        (src / "a.txt").write_text("keep", encoding="utf-8")
        (src / "skipme" / "b.txt").write_text("skip", encoding="utf-8")
        output = tmp_path / "out.zip"
        create_archive(src, output, ignore_dirs={"skipme"})
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "a.txt" in names
        assert all("skipme" not in n for n in names)

    def test_create_custom_ignore_ext(self, tmp_path: Path) -> None:
        """自定义 ignore_ext 集合。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("keep", encoding="utf-8")
        (src / "b.log").write_text("skip", encoding="utf-8")
        output = tmp_path / "out.zip"
        create_archive(src, output, ignore_ext={".log"})
        with zipfile.ZipFile(output) as zf:
            names = zf.namelist()
        assert "a.txt" in names
        assert "b.log" not in names

    def test_create_empty_directory_zip(self, tmp_path: Path) -> None:
        """空目录打包为 zip（无文件）。"""
        src = tmp_path / "src"
        src.mkdir()
        output = tmp_path / "out.zip"
        create_archive(src, output)
        with zipfile.ZipFile(output) as zf:
            assert zf.namelist() == []


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

    def test_create_zip_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """create 子命令打包目录为 zip。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("hello", encoding="utf-8")
        output = tmp_path / "out.zip"
        code = run_tool("archivex", ["create", str(src), str(output)])
        assert code == 0
        captured = capsys.readouterr()
        assert "创建完成" in captured.out
        assert output.is_file()
        with zipfile.ZipFile(output) as zf:
            assert "a.txt" in zf.namelist()

    def test_create_tar_gz_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """create 子命令打包目录为 tar.gz。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.tar.gz"
        code = run_tool("archivex", ["create", str(src), str(output)])
        assert code == 0
        captured = capsys.readouterr()
        assert "创建完成" in captured.out
        assert output.is_file()

    def test_create_gz_single_file_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """create 子命令压缩单文件为 .gz。"""
        src = tmp_path / "data.txt"
        src.write_text("hello", encoding="utf-8")
        output = tmp_path / "data.txt.gz"
        code = run_tool("archivex", ["create", str(src), str(output)])
        assert code == 0
        captured = capsys.readouterr()
        assert "创建完成" in captured.out
        assert output.is_file()

    def test_create_source_not_found_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """create 源不存在时提示。"""
        output = tmp_path / "out.zip"
        code = run_tool("archivex", ["create", str(tmp_path / "missing"), str(output)])
        assert code == 0
        captured = capsys.readouterr()
        assert "源路径不存在" in captured.out

    def test_create_unsupported_format_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """create 不支持的格式时提示。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.7z"
        code = run_tool("archivex", ["create", str(src), str(output)])
        assert code == 0
        captured = capsys.readouterr()
        assert "create 不支持格式 7z" in captured.out

    def test_create_directory_to_gz_cli(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """create 目录压缩为 .gz 时提示（需用 tar.gz）。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("data", encoding="utf-8")
        output = tmp_path / "out.gz"
        code = run_tool("archivex", ["create", str(src), str(output)])
        assert code == 0
        captured = capsys.readouterr()
        assert "目录无法压缩为单文件格式" in captured.out
