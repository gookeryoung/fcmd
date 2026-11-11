"""folderback - 备份文件夹（zip 压缩 + 旧备份清理）。"""

from __future__ import annotations

import fcmd as fx
from fcmd.infras.system import backup_folder

__all__ = ["folderback"]


@fx.tool(
    "folderback",
    help="备份文件夹",
    param_help={
        "src": "源文件夹路径（默认: 当前目录）",
        "dst": "目标文件夹路径（默认: ./backup）",
        "max_zip": "最大备份数量（默认: 5，超出时删除最旧的）",
    },
)
def folderback(src: str = ".", dst: str = "./backup", max_zip: int = 5) -> None:
    """备份文件夹到指定目录（zip 压缩），自动清理旧备份。"""
    for message in backup_folder(src, dst, max_zip):
        print(message)
