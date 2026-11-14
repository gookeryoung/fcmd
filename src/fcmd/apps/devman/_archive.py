"""zip 安全解压：拒绝绝对路径、``..`` 与符号链接成员。"""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

from fcmd.apps.devman._errors import runtime_error

__all__ = ["extract_zip", "single_root"]


def _safe_member(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    member = PurePosixPath(normalized)
    if not normalized or member.is_absolute():
        raise runtime_error(f"压缩包内含绝对路径成员：{name!r}", code="DOWNLOAD_FAILED")
    if any(part == ".." for part in member.parts):
        raise runtime_error(f"压缩包内含越界成员：{name!r}", code="DOWNLOAD_FAILED")
    if member.parts and ":" in member.parts[0]:
        raise runtime_error(f"压缩包内含盘符成员：{name!r}", code="DOWNLOAD_FAILED")
    return member


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    # external_attr 高 16 位是 Unix mode；0o120000 为符号链接。
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def extract_zip(archive: Path, dest: Path) -> None:
    """把 ``archive`` 解到 ``dest``（已存在的同名文件会被覆盖）。"""
    try:
        dest.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for info in bundle.infolist():
                member = _safe_member(info.filename)
                if _is_symlink(info):
                    raise runtime_error(f"压缩包内含符号链接：{info.filename!r}", code="DOWNLOAD_FAILED")
                target = dest.joinpath(*member.parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(info) as source, target.open("wb") as sink:
                    while chunk := source.read(1024 * 1024):
                        sink.write(chunk)
    except zipfile.BadZipFile as exc:
        raise runtime_error(f"压缩包损坏：{archive.name}：{exc}", code="DOWNLOAD_FAILED") from exc
    except OSError as exc:
        raise runtime_error(f"解压 {archive.name} 失败：{exc}", code="DOWNLOAD_FAILED") from exc


def single_root(directory: Path) -> Path | None:
    """若目录下只有一个顶层目录（且没有别的文件），返回它；否则 ``None``。"""
    try:
        entries = list(directory.iterdir())
    except OSError:
        return None
    if len(entries) != 1 or not entries[0].is_dir():
        return None
    return entries[0]
