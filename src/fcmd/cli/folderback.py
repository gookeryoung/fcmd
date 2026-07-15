"""folderback - 文件夹备份工具。

备份当前文件夹到指定目录，自动清理旧备份。

示例
----
    fcmd folderback                              # 备份当前目录到 ./backup
    fcmd folderback --src ./project --dst ./archive  # 指定源与目标
    fcmd folderback --max-zip 3                  # 最多保留 3 个备份
"""

from __future__ import annotations

import time
import zipfile
from pathlib import Path

import fcmd

__all__ = [
    "backup_folder",
    "remove_old_backups",
    "zip_target",
]


def remove_old_backups(src_stem: str, dst: Path, max_zip: int) -> None:
    """递归删除旧的备份 zip 文件，保留最新的 ``max_zip`` 个。

    Parameters
    ----------
    src_stem:
        源文件夹名（用于过滤匹配的备份文件）
    dst:
        备份目录
    max_zip:
        最大备份数量
    """
    while True:
        zip_paths = [fp for fp in dst.rglob("*.zip") if src_stem in str(fp)]
        zip_files = sorted(zip_paths, key=lambda fn: str(fn)[-19:-4])
        if len(zip_files) <= max_zip:
            return
        zip_files[0].unlink()


def zip_target(src: Path, dst: Path, max_zip: int) -> None:
    """将单个文件或文件夹压缩为 zip 文件。

    Parameters
    ----------
    src:
        源路径
    dst:
        目标目录
    max_zip:
        最大备份数量
    """
    files = [str(f) for f in src.rglob("*")]
    timestamp = time.strftime("_%Y%m%d_%H%M%S")
    target_path = dst / (src.stem + timestamp + ".zip")

    with zipfile.ZipFile(target_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for file in files:
            zip_file.write(file, arcname=file.replace(str(src.parent), ""))

    remove_old_backups(src.stem, dst, max_zip)
    print(f"备份完成: {target_path}")


@fcmd.tool("folderback", help="备份文件夹")
def backup_folder(src: str = ".", dst: str = "./backup", max_zip: int = 5) -> None:
    """备份文件夹到指定目录，自动清理旧备份。

    Parameters
    ----------
    src:
        源文件夹路径（默认: 当前目录）
    dst:
        目标文件夹路径（默认: ``./backup``）
    max_zip:
        最大备份数量（默认: 5，超出时删除最旧的）
    """
    src_path = Path(src)
    dst_path = Path(dst)

    if not src_path.exists():
        print(f"源文件夹不存在: {src_path}")
        return

    if not dst_path.exists():
        dst_path.mkdir(parents=True, exist_ok=True)
        print(f"创建目标文件夹: {dst_path}")

    zip_target(src_path, dst_path, max_zip)
