"""cmderw 归档写入纯逻辑：包内新增 / 删除 / 改名、压缩级别与密码。

所有写操作都重建成「同目录临时文件 → 写后完整性校验 → 原子替换」：任一
步骤失败只删除临时文件，原归档保持不变。可写范围与只读浏览一致，仅
``zip`` 与 ``tar`` 家族；单文件压缩流与 ``7z``/``rar`` 仍为只读。密码只对
``zip`` 生效（AES-256，依赖可选依赖 ``pyzipper``），传入密码写 ``tar``
会直接报错而不是静默忽略。
"""

from __future__ import annotations

import bz2
import contextlib
import copy
import gzip
import lzma
import os
import shutil
import tarfile
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

from fcmd.tools.archive.archivex import detect_format

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence
    from typing import Any

__all__ = [
    "DEFAULT_COMPRESSION_LEVEL",
    "MAX_COMPRESSION_LEVEL",
    "MIN_COMPRESSION_LEVEL",
    "ArchiveWriteOptions",
    "add_paths",
    "archive_editable",
    "archive_member_names",
    "create_archive_file",
    "normalize_level",
    "planned_additions",
    "remove_members",
    "rename_member",
    "verify_archive",
]

MIN_COMPRESSION_LEVEL = 0
MAX_COMPRESSION_LEVEL = 9
DEFAULT_COMPRESSION_LEVEL = 6

_TAR_SUFFIXES: tuple[str, ...] = (
    ".tar",
    ".tar.gz",
    ".tgz",
    ".tar.bz2",
    ".tbz",
    ".tbz2",
    ".tar.xz",
    ".txz",
)

_AES_COMPRESS_TYPE = 99
_ENCRYPTED_FLAG = 0x1


@dataclass(frozen=True)
class ArchiveWriteOptions:
    """写入选项：``level`` 为 0–9，``password`` 仅对 ``zip`` 生效。"""

    level: int = DEFAULT_COMPRESSION_LEVEL
    password: str = ""


def normalize_level(level: object) -> int:
    """把压缩级别夹到 0–9；非法类型回退默认值。"""
    if isinstance(level, bool) or not isinstance(level, int):
        return DEFAULT_COMPRESSION_LEVEL
    return max(MIN_COMPRESSION_LEVEL, min(MAX_COMPRESSION_LEVEL, level))


def _archive_format(path: Path) -> str | None:
    """复用 :func:`fcmd.tools.archive.archivex.detect_format`；不支持的格式返回 ``None``。"""
    try:
        return detect_format(path)
    except ValueError:
        return None


def _is_zip(path: Path) -> bool:
    return _archive_format(path) == "zip"


def _is_tar(path: Path) -> bool:
    return _archive_format(path) == "tar"


def archive_editable(path: Path) -> bool:
    """是否支持包内写入（zip 与 tar 家族）；单文件压缩流与 7z/rar 为只读。"""
    return _is_zip(path) or _is_tar(path)


def _compound_suffix(path: Path) -> str:
    """取完整复合扩展名（``.tar.gz``），供临时文件保持格式可识别。"""
    lower = path.name.lower()
    for suffix in (*_TAR_SUFFIXES, ".zip"):
        if lower.endswith(suffix):
            return suffix
    return path.suffix or ".tmp"


def _clean_name(name: str) -> str:
    """规范化成员名并拒绝绝对路径 / ``..`` 穿越 / 空路径。"""
    text = name.replace("\\", "/").strip("/")
    parts = PurePosixPath(text).parts
    if not text or text.startswith("/") or ".." in parts:
        raise ValueError(f"非法成员路径: {name}")
    return text


def _require_pyzipper() -> Any:
    """惰性导入 pyzipper；缺失时抛出可读的 ``ValueError``。"""
    try:
        import pyzipper
    except ImportError:
        raise ValueError("密码功能需要安装 pyzipper：pip install fcmd[archive]") from None
    return pyzipper


def _zip_encrypted(path: Path) -> bool:
    with zipfile.ZipFile(path) as handle:
        return any(info.flag_bits & _ENCRYPTED_FLAG for info in handle.infolist())


@contextlib.contextmanager
def _zip_reader(path: Path, password: str) -> Iterator[Any]:
    """打开 zip 读取；有密码时优先用 pyzipper（兼容 AES 与 ZipCrypto）。"""
    if password:
        module = _require_pyzipper()
        handle = module.AESZipFile(path)
        handle.setpassword(password.encode("utf-8"))
    else:
        handle = zipfile.ZipFile(path)
    try:
        yield handle
    finally:
        handle.close()


@contextlib.contextmanager
def _zip_writer(path: Path, options: ArchiveWriteOptions) -> Iterator[Any]:
    """打开 zip 写入；有密码时用 pyzipper AES-256。"""
    level = normalize_level(options.level)
    if options.password:
        module = _require_pyzipper()
        handle = module.AESZipFile(
            path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=level,
            encryption=module.WZ_AES,
        )
        handle.setpassword(options.password.encode("utf-8"))
    else:
        compression = zipfile.ZIP_STORED if level == 0 else zipfile.ZIP_DEFLATED
        handle = zipfile.ZipFile(path, "w", compression=compression, compresslevel=level)
    try:
        yield handle
    finally:
        handle.close()


@contextlib.contextmanager
def _tar_writer(path: Path, archive_name: str, options: ArchiveWriteOptions) -> Iterator[tarfile.TarFile]:
    """按归档扩展名选择压缩后端并按级别写入（bz2 最小级别为 1）。"""
    level = normalize_level(options.level)
    lower = archive_name.lower()
    with path.open("wb") as raw:
        if lower.endswith((".tar.gz", ".tgz")):
            with (
                gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=level) as stream,
                tarfile.open(fileobj=stream, mode="w") as handle,
            ):
                yield handle
        elif lower.endswith((".tar.bz2", ".tbz", ".tbz2")):
            with (
                bz2.BZ2File(raw, "w", compresslevel=max(1, level)) as stream,
                tarfile.open(fileobj=stream, mode="w") as handle,
            ):
                yield handle
        elif lower.endswith((".tar.xz", ".txz")):
            with (
                lzma.LZMAFile(raw, "w", preset=level) as stream,
                tarfile.open(fileobj=stream, mode="w") as handle,
            ):
                yield handle
        else:
            with tarfile.open(fileobj=raw, mode="w") as handle:
                yield handle


def planned_additions(sources: Sequence[Path], prefix: str = "") -> dict[str, Path]:
    """把待加入的源文件 / 目录展开为 ``成员名 → 源路径``（目录递归展开）。"""
    base = _clean_name(prefix) if prefix.strip() else ""
    entries: dict[str, Path] = {}
    for raw_source in sources:
        source = Path(raw_source)
        if source.is_dir():
            top = f"{base}/{source.name}" if base else source.name
            for child in sorted(source.rglob("*")):
                if child.is_file():
                    name = _clean_name(f"{top}/{child.relative_to(source).as_posix()}")
                    entries[name] = child
        elif source.is_file():
            name = _clean_name(f"{base}/{source.name}" if base else source.name)
            entries[name] = source
        else:
            raise FileNotFoundError(f"源不存在: {source}")
    return entries


def archive_member_names(path: Path, *, password: str = "") -> list[str]:
    """列出可写归档的成员名（zip / tar）；用于写入前的冲突判断。"""
    if not path.is_file():
        raise FileNotFoundError(f"归档不存在: {path}")
    if _is_zip(path):
        with _zip_reader(path, password) as handle:
            return [info.filename for info in handle.infolist()]
    if _is_tar(path):
        with tarfile.open(path) as handle:
            return [info.name for info in handle.getmembers()]
    raise ValueError(f"该格式不支持写入: {path.name}")


def _in_scope(name: str, wanted: Sequence[str]) -> bool:
    cleaned = name.rstrip("/")
    return any(cleaned == item or cleaned.startswith(f"{item}/") for item in wanted)


def _zip_datetime(mtime: float) -> tuple[int, int, int, int, int, int]:
    try:
        fields = time.localtime(mtime)
    except (OSError, OverflowError, ValueError):
        return (1980, 1, 1, 0, 0, 0)
    if fields[0] < 1980:
        return (1980, 1, 1, 0, 0, 0)
    return (fields[0], fields[1], fields[2], fields[3], fields[4], fields[5])


def _zip_info_cls(handle: Any) -> type:
    """取写入句柄的 ZipInfo 子类（pyzipper 的自定义类或标准库类）。"""
    return getattr(handle, "zipinfo_cls", zipfile.ZipInfo)


def _copy_zip(
    source: Any,
    target: Any,
    *,
    name_for: Callable[[str], str | None],
    skip: Callable[[str], bool],
    level: int,
) -> list[str]:
    names: list[str] = []
    compress = zipfile.ZIP_STORED if level == 0 else zipfile.ZIP_DEFLATED
    for info in source.infolist():
        if skip(info.filename):
            continue
        new_name = name_for(info.filename)
        if new_name is None:
            continue
        if info.is_dir() and not new_name.endswith("/"):
            new_name += "/"
        target_info = _zip_info_cls(target)(new_name, date_time=info.date_time)
        target_info.external_attr = info.external_attr
        target_info.compress_type = compress
        if info.is_dir():
            target.writestr(target_info, b"")
        else:
            with source.open(info) as reader, target.open(target_info, "w") as writer:
                shutil.copyfileobj(reader, writer)
        names.append(new_name)
    return names


def _append_zip(target: Any, additions: dict[str, Path], *, level: int) -> list[str]:
    names: list[str] = []
    compress = zipfile.ZIP_STORED if level == 0 else zipfile.ZIP_DEFLATED
    for name, path in sorted(additions.items()):
        info = _zip_info_cls(target)(name, date_time=_zip_datetime(path.stat().st_mtime))
        info.compress_type = compress
        info.external_attr = 0o600 << 16
        with path.open("rb") as reader, target.open(info, "w") as writer:
            shutil.copyfileobj(reader, writer)
        names.append(name)
    return names


def _copy_tar(
    source: tarfile.TarFile,
    target: tarfile.TarFile,
    *,
    name_for: Callable[[str], str | None],
    skip: Callable[[str], bool],
) -> list[str]:
    names: list[str] = []
    for info in source.getmembers():
        if skip(info.name):
            continue
        new_name = name_for(info.name)
        if new_name is None:
            continue
        clone = copy.copy(info)
        clone.name = new_name
        if info.isfile():
            stream = source.extractfile(info)
            if stream is None:
                raise RuntimeError(f"无法读取成员: {info.name}")
            target.addfile(clone, stream)
        else:
            target.addfile(clone)
        names.append(new_name)
    return names


def _append_tar(target: tarfile.TarFile, additions: dict[str, Path]) -> list[str]:
    names: list[str] = []
    for name, path in sorted(additions.items()):
        info = target.gettarinfo(str(path), arcname=name)
        with path.open("rb") as stream:
            target.addfile(info, stream)
        names.append(name)
    return names


def _check_writable(archive: Path, options: ArchiveWriteOptions) -> None:
    if not archive_editable(archive):
        raise ValueError(f"该格式不支持写入（仅 zip 与 tar 家族）: {archive.name}")
    if options.password and not _is_zip(archive):
        raise ValueError("仅 zip 支持密码，tar 家族请去掉密码后重试")


def _check_readable(archive: Path, options: ArchiveWriteOptions) -> None:
    if _is_zip(archive) and not options.password and _zip_encrypted(archive):
        raise ValueError("归档已加密，请先在「归档写入选项」中填写密码")


def _write_zip(  # noqa: PLR0913 - 写入参数由重写流程逐项透传
    target_path: Path,
    options: ArchiveWriteOptions,
    *,
    source: Path | None,
    name_for: Callable[[str], str | None],
    skip: Callable[[str], bool],
    additions: dict[str, Path],
) -> list[str]:
    level = normalize_level(options.level)
    names: list[str] = []
    with _zip_writer(target_path, options) as handle:
        if source is not None:
            with _zip_reader(source, options.password) as reader:
                names.extend(_copy_zip(reader, handle, name_for=name_for, skip=skip, level=level))
        names.extend(_append_zip(handle, additions, level=level))
    return names


def _write_tar(  # noqa: PLR0913 - 写入参数由重写流程逐项透传
    target_path: Path,
    archive_name: str,
    options: ArchiveWriteOptions,
    *,
    source: Path | None,
    name_for: Callable[[str], str | None],
    skip: Callable[[str], bool],
    additions: dict[str, Path],
) -> list[str]:
    names: list[str] = []
    with _tar_writer(target_path, archive_name, options) as handle:
        if source is not None:
            with tarfile.open(source) as reader:
                names.extend(_copy_tar(reader, handle, name_for=name_for, skip=skip))
        names.extend(_append_tar(handle, additions))
    return names


def _perform_write(  # noqa: PLR0913 - 写入参数由调用方显式传入
    archive: Path,
    *,
    options: ArchiveWriteOptions,
    source: Path | None,
    name_for: Callable[[str], str | None],
    skip: Callable[[str], bool],
    additions: dict[str, Path],
) -> list[str]:
    """写临时文件、校验后原子替换；失败清理临时文件且不动原归档。"""
    archive.parent.mkdir(parents=True, exist_ok=True)
    # 临时文件保留归档扩展名，校验函数据此识别格式（如 .tar.gz / .zip）。
    handle, temp_name = tempfile.mkstemp(
        prefix=f".{archive.name}.",
        suffix=_compound_suffix(archive),
        dir=archive.parent,
    )
    os.close(handle)
    temp = Path(temp_name)
    try:
        if _is_zip(archive):
            names = _write_zip(
                temp,
                options,
                source=source,
                name_for=name_for,
                skip=skip,
                additions=additions,
            )
        else:
            names = _write_tar(
                temp,
                archive.name,
                options,
                source=source,
                name_for=name_for,
                skip=skip,
                additions=additions,
            )
        verify_archive(temp, password=options.password, expected_names=names)
        temp.replace(archive)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return names


def _identity(name: str) -> str:
    return name


def add_paths(
    archive: Path,
    sources: Sequence[Path],
    prefix: str = "",
    *,
    options: ArchiveWriteOptions | None = None,
    replace: bool = False,
) -> int:
    """把文件 / 目录加入归档的 ``prefix`` 目录下，返回新增文件数。

    ``replace`` 为假时，若成员名已存在则抛 ``FileExistsError``（由界面询问
    是否覆盖），保证不会静默覆盖包内已有内容。
    """
    if not archive.is_file():
        raise FileNotFoundError(f"归档不存在: {archive}")
    active = options or ArchiveWriteOptions()
    _check_writable(archive, active)
    _check_readable(archive, active)
    additions = planned_additions(sources, prefix)
    if not additions:
        raise ValueError("没有可加入的文件")
    existing = set(archive_member_names(archive, password=active.password))
    conflicts = sorted(name for name in additions if name in existing)
    if conflicts and not replace:
        raise FileExistsError("成员已存在: " + "、".join(conflicts[:5]))
    additions = {name: path for name, path in additions.items() if name not in existing or replace}
    if archive.is_file():
        _perform_write(
            archive,
            options=active,
            source=archive,
            name_for=_identity,
            skip=lambda name: name in additions,
            additions=additions,
        )
        return len(additions)
    raise FileNotFoundError(f"归档不存在: {archive}")


def create_archive_file(
    archive: Path,
    sources: Sequence[Path],
    *,
    options: ArchiveWriteOptions | None = None,
) -> int:
    """用给定源创建 zip / tar 归档（覆盖同名文件），返回写入文件数。"""
    active = options or ArchiveWriteOptions()
    _check_writable(archive, active)
    additions = planned_additions(sources)
    if not additions:
        raise ValueError("没有可打包的文件")
    _perform_write(
        archive,
        options=active,
        source=None,
        name_for=_identity,
        skip=lambda _name: False,
        additions=additions,
    )
    return len(additions)


def remove_members(
    archive: Path,
    names: Sequence[str],
    *,
    options: ArchiveWriteOptions | None = None,
) -> int:
    """删除成员（目录按前缀整棵删除），返回删除的成员数。"""
    if not archive.is_file():
        raise FileNotFoundError(f"归档不存在: {archive}")
    active = options or ArchiveWriteOptions()
    _check_writable(archive, active)
    _check_readable(archive, active)
    wanted = [_clean_name(name) for name in names if name.strip()]
    if not wanted:
        return 0
    existing = archive_member_names(archive, password=active.password)
    removed = [name for name in existing if _in_scope(name, wanted)]
    if not removed:
        return 0
    _perform_write(
        archive,
        options=active,
        source=archive,
        name_for=_identity,
        skip=lambda name: _in_scope(name, wanted),
        additions={},
    )
    return len(removed)


def rename_member(
    archive: Path,
    old: str,
    new: str,
    *,
    options: ArchiveWriteOptions | None = None,
) -> str:
    """重命名单个成员；目录按前缀改整棵子树，返回新成员名。"""
    if not archive.is_file():
        raise FileNotFoundError(f"归档不存在: {archive}")
    active = options or ArchiveWriteOptions()
    _check_writable(archive, active)
    _check_readable(archive, active)
    old_name = _clean_name(old)
    new_name = _clean_name(new)
    if old_name == new_name:
        raise ValueError("新旧名称相同")
    existing = archive_member_names(archive, password=active.password)
    targets = [name for name in existing if _in_scope(name, [old_name])]
    if not targets:
        raise FileNotFoundError(f"成员不存在: {old_name}")
    if any(_in_scope(name, [new_name]) for name in existing if not _in_scope(name, [old_name])):
        raise FileExistsError(f"成员已存在: {new_name}")

    def renamed(name: str) -> str:
        if name == old_name:
            return new_name
        if name.startswith(f"{old_name}/"):
            return f"{new_name}{name[len(old_name) :]}"
        return name

    _perform_write(
        archive,
        options=active,
        source=archive,
        name_for=renamed,
        skip=lambda _name: False,
        additions={},
    )
    return new_name


def verify_archive(
    path: Path,
    *,
    password: str = "",
    expected_names: Sequence[str] | None = None,
) -> None:
    """校验归档可完整读取：逐成员解压校验 CRC，并比对成员名单。"""
    if _is_zip(path):
        with _zip_reader(path, password) as handle:
            bad = handle.testzip()
            if bad is not None:
                raise RuntimeError(f"归档校验失败（CRC 错误）: {bad}")
            names = [info.filename for info in handle.infolist()]
    elif _is_tar(path):
        names: list[str] = []
        with tarfile.open(path) as handle:
            for info in handle.getmembers():
                names.append(info.name)
                if not info.isfile():
                    continue
                stream = handle.extractfile(info)
                if stream is None:
                    raise RuntimeError(f"归档校验失败（无法读取）: {info.name}")
                while True:
                    chunk = stream.read(1 << 20)
                    if not chunk:
                        break
    else:
        raise ValueError(f"该格式不支持校验: {path.name}")
    if expected_names is not None and sorted(names) != sorted(expected_names):
        raise RuntimeError("归档校验失败（成员列表不一致）")
