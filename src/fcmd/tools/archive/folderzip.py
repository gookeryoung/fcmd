"""folderzip - 把目录下所有子文件夹压缩为 zip。

示例
----
    fcmd folderzip                    # 压缩当前目录下所有子文件夹
    fcmd folderzip --directory /data  # 压缩指定目录下所有子文件夹
"""

from __future__ import annotations

from pathlib import Path

import fcmd as fx
from fcmd.infras.archive import archive_folder, collect_folders

__all__ = ["folderzip"]


@fx.tool(
    "folderzip",
    subcommand=None,
    help="压缩当前目录下所有子文件夹为 zip",
    param_help={"directory": "目标目录（默认当前目录）"},
)
def folderzip(directory: str = ".") -> None:
    """压缩目录下的所有子文件夹为 zip。"""
    dir_path = Path(directory)
    if not dir_path.exists():
        print(f"目录不存在: {dir_path}")
        return

    for child in collect_folders(dir_path):
        archive_folder(child)
        print(f"压缩完成: {child.name}.zip")


@fx.main("folderzip")
def main() -> None:
    pass  # pragma: no cover - @fx.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
