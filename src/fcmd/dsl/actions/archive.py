"""归档与压缩 DSL 动作：folderzip。

原模块 ``fcmd.cli.archive.folderzip`` 为纯 Python 实现（shutil / pathlib
标准库），无子进程调用。
"""

from __future__ import annotations

from pathlib import Path

from fcmd.dsl.actions import action

__all__: list[str] = []


# ============================================================================
# 公共函数
# ============================================================================


def archive_folder(folder: Path) -> None:
    """压缩单个文件夹为同名 zip。"""
    # 惰性导入：本模块被动作注册表在工具发现期导入，shutil 会连带
    # 加载压缩相关标准库，仅在实际压缩时才需要
    import shutil

    shutil.make_archive(
        str(folder.with_name(folder.name)),
        format="zip",
        base_dir=folder,
    )
    print(f"压缩完成: {folder.name}.zip")


# ============================================================================
# DSL 动作
# ============================================================================


@action(
    "folderzip",
    param_help={"directory": "目标目录（默认当前目录）"},
)
def folderzip(directory: str = ".") -> None:
    """压缩目录下的所有子文件夹为 zip。"""
    from fcmd.cli._common import IGNORE_DIRS, IGNORE_EXT

    dir_path = Path(directory)
    if not dir_path.exists():
        print(f"目录不存在: {dir_path}")
        return

    dirs: list[Path] = [
        e for e in dir_path.iterdir() if e.is_dir() and e.name not in IGNORE_DIRS and e.suffix not in IGNORE_EXT
    ]

    for child in dirs:
        archive_folder(child)
