"""归档域公共实现：folderzip（无工具注册副作用）。

供 ``fcmd.dsl.actions.toolcmd.archive`` 的 ``@action`` 声明复用；目录筛选与单目录
压缩实现只在本模块保留一份。原实现位于 DSL 动作层，迁入后命令行为不变。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["archive_folder", "collect_folders"]


def collect_folders(root: Path) -> list[Path]:
    """收集 root 下需要压缩的子目录（跳过忽略目录与忽略后缀）。"""
    from fcmd.core.filefilter import DEFAULT_IGNORE_EXTS, DEFAULT_IGNORE_SPEC, should_skip_parts

    return [
        entry
        for entry in root.iterdir()
        if entry.is_dir()
        and not should_skip_parts(entry.parts, DEFAULT_IGNORE_SPEC)
        and entry.suffix not in DEFAULT_IGNORE_EXTS
    ]


def archive_folder(folder: Path) -> None:
    """压缩单个文件夹为同名 zip。"""
    # 惰性导入：本模块在工具发现期被动作注册表导入，shutil 会连带
    # 加载压缩相关标准库，仅在实际压缩时才需要
    import shutil

    shutil.make_archive(
        str(folder.with_name(folder.name)),
        format="zip",
        base_dir=folder,
    )
    print(f"压缩完成: {folder.name}.zip")
