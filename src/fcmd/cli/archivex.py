"""archivex - 归档解压工具。

支持多格式归档文件解压或列出内容：zip/tar/gz/bz2/xz 走标准库，
7z/rar 走外部 7z/unrar 命令。

示例
----
    fcmd archivex extract archive.zip                  # 解压到 archive/ 目录
    fcmd archivex extract archive.tar.gz --output out  # 解压到 out/ 目录
    fcmd archivex list archive.7z                      # 列出归档内容
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import fcmd

__all__ = [
    "detect_format",
    "extract_archive",
    "list_archive",
]

# 归档扩展名（按优先级匹配，长扩展名在前避免 .tar.gz 被截为 .gz）
_ARCHIVE_EXTS: tuple[str, ...] = (
    ".tar.gz",
    ".tar.bz2",
    ".tar.xz",
    ".tgz",
    ".tbz",
    ".tbz2",
    ".txz",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",
    ".7z",
    ".rar",
)

# tar 归档扩展名集合
_TAR_EXTS: frozenset[str] = frozenset(
    {
        ".tar",
        ".tar.gz",
        ".tgz",
        ".tar.bz2",
        ".tbz",
        ".tbz2",
        ".tar.xz",
        ".txz",
    }
)


def detect_format(filepath: Path) -> str:  # noqa: PLR0911
    """根据文件扩展名检测归档格式。

    Parameters
    ----------
    filepath:
        归档文件路径

    Returns
    -------
    str
        格式标识：``"zip"``/``"tar"``/``"gz"``/``"bz2"``/``"xz"``/``"7z"``/``"rar"``

    Raises
    ------
    ValueError
        不支持的扩展名
    """
    name = filepath.name.lower()
    for ext in _ARCHIVE_EXTS:
        if name.endswith(ext):
            if ext == ".zip":
                return "zip"
            elif ext in _TAR_EXTS:
                return "tar"
            elif ext == ".gz":
                return "gz"
            elif ext == ".bz2":
                return "bz2"
            elif ext == ".xz":
                return "xz"
            elif ext == ".7z":
                return "7z"
            else:  # ext == ".rar"
                return "rar"
    raise ValueError(f"不支持的归档格式: {filepath.name}")


def _strip_compression_ext(name: str) -> str:
    """去掉单文件压缩扩展名（.gz/.bz2/.xz）。"""
    for ext in (".gz", ".bz2", ".xz"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name


def _extract_single(filepath: Path, output: Path, opener: Any) -> None:
    """解压单文件压缩（gz/bz2/xz，不含 tar）。

    Parameters
    ----------
    filepath:
        压缩文件路径
    output:
        输出目录
    opener:
        解压打开函数（gzip.open/bz2.open/lzma.open）
    """
    target = output / _strip_compression_ext(filepath.name)
    with opener(filepath, "rb") as src, target.open("wb") as dst:
        shutil.copyfileobj(src, dst)


def _extract_with_external(filepath: Path, output: Path, tool: str) -> None:
    """调用外部命令解压（7z/unrar）。

    Parameters
    ----------
    filepath:
        归档文件路径
    output:
        输出目录
    tool:
        外部命令名（``"7z"`` 或 ``"unrar"``）

    Raises
    ------
    FileNotFoundError
        外部命令未找到
    RuntimeError
        外部命令执行失败
    """
    if not shutil.which(tool):
        raise FileNotFoundError(f"未找到解压命令: {tool}（请安装 7-Zip 或 WinRAR）")
    if tool == "7z":
        cmd: list[str] = [tool, "x", str(filepath), f"-o{output}", "-y"]
    else:  # unrar
        cmd = [tool, "x", str(filepath), str(output).rstrip("/\\") + "/", "-y"]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{tool} 解压失败: {result.stderr.strip()}")


def extract_archive(filepath: Path, output: Path) -> None:
    """解压归档文件到指定目录。

    Parameters
    ----------
    filepath:
        归档文件路径
    output:
        输出目录（不存在时自动创建）

    Raises
    ------
    ValueError
        不支持的归档格式
    FileNotFoundError
        外部解压命令未找到
    RuntimeError
        外部命令执行失败
    """
    fmt = detect_format(filepath)
    output.mkdir(parents=True, exist_ok=True)

    if fmt == "zip":
        with zipfile.ZipFile(filepath) as zf:
            zf.extractall(output)
    elif fmt == "tar":
        with tarfile.open(filepath) as tf:
            # Python 3.12+ 要求 filter 参数（PEP 706），低版本默认无过滤
            if sys.version_info >= (3, 12):  # pragma: no cover
                tf.extractall(output, filter="data")
            else:
                tf.extractall(output)
    elif fmt == "gz":
        _extract_single(filepath, output, gzip.open)
    elif fmt == "bz2":
        _extract_single(filepath, output, bz2.open)
    elif fmt == "xz":
        _extract_single(filepath, output, lzma.open)
    elif fmt == "7z":
        _extract_with_external(filepath, output, "7z")
    elif fmt == "rar":
        tool = "unrar" if shutil.which("unrar") else "7z"
        _extract_with_external(filepath, output, tool)
    else:  # pragma: no cover
        raise RuntimeError(f"未处理的格式: {fmt}")


def _list_with_external(filepath: Path, tool: str) -> list[str]:
    """调用外部命令列出归档内容，返回原始输出行。

    7z/unrar 输出格式复杂（含表头/分隔线/统计），直接返回全部非空行，
    由 CLI 层原样打印供用户查看。

    Parameters
    ----------
    filepath:
        归档文件路径
    tool:
        外部命令名

    Returns
    -------
    list[str]
        输出行列表

    Raises
    ------
    FileNotFoundError
        外部命令未找到
    RuntimeError
        外部命令执行失败
    """
    if not shutil.which(tool):
        raise FileNotFoundError(f"未找到解压命令: {tool}（请安装 7-Zip 或 WinRAR）")
    cmd = [tool, "l", str(filepath)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{tool} 列出失败: {result.stderr.strip()}")
    return [line for line in result.stdout.splitlines() if line.strip()]


def list_archive(filepath: Path) -> list[str]:
    """列出归档文件内容。

    标准库格式（zip/tar/gz/bz2/xz）返回文件名列表；
    外部格式（7z/rar）返回外部命令的原始输出行。

    Parameters
    ----------
    filepath:
        归档文件路径

    Returns
    -------
    list[str]
        内容列表

    Raises
    ------
    ValueError
        不支持的归档格式
    FileNotFoundError
        外部解压命令未找到
    RuntimeError
        外部命令执行失败
    """
    fmt = detect_format(filepath)

    if fmt == "zip":
        with zipfile.ZipFile(filepath) as zf:
            return zf.namelist()
    if fmt == "tar":
        with tarfile.open(filepath) as tf:
            return tf.getnames()
    if fmt in ("gz", "bz2", "xz"):
        return [_strip_compression_ext(filepath.name)]
    if fmt == "7z":
        return _list_with_external(filepath, "7z")
    if fmt == "rar":
        tool = "unrar" if shutil.which("unrar") else "7z"
        return _list_with_external(filepath, tool)
    raise RuntimeError(f"未处理的格式: {fmt}")  # pragma: no cover


@fcmd.tool("archivex", subcommand="extract", help="解压归档文件")
def archivex_extract(archive: Path, output: str = "") -> None:
    """解压归档文件到指定目录。

    默认输出目录为归档文件同目录下以归档主干名命名的子目录。

    Parameters
    ----------
    archive:
        归档文件路径
    output:
        输出目录（默认: 归档同目录的 ``<stem>/`` 子目录）
    """
    if not archive.exists():
        print(f"归档文件不存在: {archive}")
        return
    out_path = Path(output) if output else archive.parent / archive.stem

    try:
        extract_archive(archive, out_path)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(str(e))
        return
    print(f"解压完成: {archive} -> {out_path}")


@fcmd.tool("archivex", subcommand="list", help="列出归档文件内容")
def archivex_list(archive: Path) -> None:
    """列出归档文件内容。

    Parameters
    ----------
    archive:
        归档文件路径
    """
    if not archive.exists():
        print(f"归档文件不存在: {archive}")
        return
    try:
        names = list_archive(archive)
    except (ValueError, FileNotFoundError, RuntimeError) as e:
        print(str(e))
        return
    if not names:
        print("（空归档）")
        return
    print(f"归档内容 ({len(names)} 项):")
    for name in names:
        print(f"  {name}")
