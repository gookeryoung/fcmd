"""归档与压缩 DSL 动作：folderzip。

业务实现统一在 :mod:`fcmd.tools.archive.archivebase`；本模块只保留
``@action`` 声明与参数转接。原模块 ``fcmd.tools.archive.folderzip`` 为纯
Python 实现（shutil / pathlib 标准库），无子进程调用。
"""

from __future__ import annotations

from pathlib import Path

from fcmd.dsl.actions import action
from fcmd.tools.archive import archivebase

__all__: list[str] = []


@action(
    "folderzip",
    param_help={"directory": "目标目录（默认当前目录）"},
)
def folderzip(directory: str = ".") -> None:
    """压缩目录下的所有子文件夹为 zip。"""
    dir_path = Path(directory)
    if not dir_path.exists():
        print(f"目录不存在: {dir_path}")
        return

    for child in archivebase.collect_folders(dir_path):
        archivebase.archive_folder(child)
