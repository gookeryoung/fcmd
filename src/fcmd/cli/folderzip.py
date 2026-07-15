"""folderzip - 文件夹压缩工具。

压缩当前目录下的所有子文件夹为 zip 文件。跳过常见忽略目录与已有压缩包。

示例
----
    fcmd folderzip                          # 压缩当前目录下全部子文件夹
    fcmd folderzip --directory /path/to/dir
"""

from __future__ import annotations

import shutil
from pathlib import Path

import fcmd

__all__ = [
    "archive_folder",
    "zip_folders",
]

# ============================================================================
# 公共函数
# ============================================================================


def archive_folder(folder: Path) -> None:
    """压缩单个文件夹为同名 zip。

    Parameters
    ----------
    folder:
        待压缩文件夹路径
    """
    shutil.make_archive(
        str(folder.with_name(folder.name)),
        format="zip",
        base_dir=folder,
    )
    print(f"压缩完成: {folder.name}.zip")


# ============================================================================
# CLI 子命令
# ============================================================================


@fcmd.tool("folderzip", subcommand="z", help="压缩当前目录下所有子文件夹为 zip")
def zip_folders(directory: str = ".") -> None:
    """压缩目录下的所有子文件夹为 zip。

    跳过 ``.git``/``__pycache__`` 等忽略目录与 ``.zip``/``.rar`` 等压缩包文件。

    Parameters
    ----------
    directory:
        目标目录（默认当前目录）
    """
    from fcmd.cli._common import IGNORE_DIRS, IGNORE_EXT

    dir_path = Path(directory)
    if not dir_path.exists():
        print(f"目录不存在: {dir_path}")
        return

    dirs: list[Path] = [
        e for e in dir_path.iterdir() if e.is_dir() and e.name not in IGNORE_DIRS and e.suffix not in IGNORE_EXT
    ]

    for dir_path in dirs:
        archive_folder(dir_path)
