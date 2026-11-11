"""归档域基础设施：目录筛选与 zip 压缩（纯函数，无打印、无 CLI）。

工具层（``fcmd.tools.archive.folderzip``）只负责参数适配与输出，
压缩逻辑集中在本模块，便于测试与复用。
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["archive_folder", "collect_folders"]


def collect_folders(root: Path) -> list[Path]:
    """收集 root 下需要压缩的子目录（跳过忽略目录与忽略后缀）。"""
    from fcmd.infras.filefilter import DEFAULT_IGNORE_EXTS, DEFAULT_IGNORE_SPEC, should_skip_parts

    return [
        entry
        for entry in root.iterdir()
        if entry.is_dir()
        and not should_skip_parts(entry.parts, DEFAULT_IGNORE_SPEC)
        and entry.suffix not in DEFAULT_IGNORE_EXTS
    ]


def archive_folder(folder: Path) -> Path:
    """压缩单个文件夹为同名 zip，返回压缩包路径。"""
    # 惰性导入：仅实际压缩时才加载压缩相关标准库
    import shutil

    archive_path = folder.with_name(f"{folder.name}.zip")
    shutil.make_archive(
        str(folder.with_name(folder.name)),
        format="zip",
        base_dir=folder,
    )
    return archive_path
