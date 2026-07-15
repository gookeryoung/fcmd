"""filelevel - 文件等级重命名工具。

为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA）。

示例
----
    fcmd filelevel set report.pdf              # 清除等级标记
    fcmd filelevel set report.pdf --level 2    # 设置为 INT 等级
    fcmd filelevel set a.pdf b.pdf --level 3   # 批量设置 CON 等级
"""

from __future__ import annotations

from pathlib import Path

import fcmd

__all__ = [
    "BRACKETS",
    "LEVELS",
    "process_file_level",
    "process_files_level",
    "remove_marks",
]

# ============================================================================
# 配置
# ============================================================================

# 等级标记映射：0 表示清除等级，1-4 对应不同等级标记
LEVELS: dict[str, str] = {
    "0": "",
    "1": "PUB,NOR",
    "2": "INT",
    "3": "CON",
    "4": "CLA",
}

# 左右括号集合：标记两侧的括号字符（用于识别并整体移除）
BRACKETS: tuple[str, str] = (" ([_(【-", " )]_）】")


# ============================================================================
# 公共函数
# ============================================================================


def remove_marks(stem: str, marks: list[str]) -> str:
    """从文件名主干中移除所有标记。

    仅移除被括号包裹的标记（如 ``file(PUB).pdf`` -> ``file.pdf``），
    保留裸字符串形式的标记。

    Parameters
    ----------
    stem:
        文件名主干（不含扩展名）
    marks:
        待移除的标记列表

    Returns
    -------
    str
        处理后的文件名主干
    """
    left_brackets, right_brackets = BRACKETS
    for mark in marks:
        pos = 0
        while True:
            pos = stem.find(mark, pos)
            if pos == -1:
                break
            b, e = pos - 1, pos + len(mark)
            if b >= 0 and e < len(stem) and stem[b] in left_brackets and stem[e] in right_brackets:
                stem = stem[:b] + stem[e + 1 :]
            else:
                pos = e
    return stem


def process_file_level(filepath: Path, level: int = 0) -> None:
    """处理单个文件的等级标记。

    先清除所有已有等级标记与数字标记，再根据 ``level`` 添加新标记。
    ``level=0`` 表示仅清除等级。

    Parameters
    ----------
    filepath:
        文件路径
    level:
        文件等级（0-4），0 用于清除等级
    """
    level = int(level)
    if not (0 <= level < len(LEVELS)):
        print(f"无效的等级 {level}，必须在 0 和 {len(LEVELS) - 1} 之间")
        return

    if not filepath.exists():
        print(f"文件不存在: {filepath}")
        return

    filestem = filepath.stem
    original_stem = filestem

    # 清除所有等级标记
    for level_names in LEVELS.values():
        if level_names:
            filestem = remove_marks(filestem, level_names.split(","))

    # 清除数字标记（1-9）
    for digit in map(str, range(1, 10)):
        filestem = remove_marks(filestem, [digit])

    # 添加新等级标记
    if level > 0:
        levelstr = LEVELS.get(str(level), "").split(",")[0]
        if levelstr:
            filestem = f"{filestem}({levelstr})"

    if filestem != original_stem:
        new_path = filepath.with_name(filestem + filepath.suffix)
        filepath.rename(new_path)
        print(f"重命名: {filepath} -> {new_path}")


@fcmd.tool("filelevel", subcommand="set", help="设置文件等级")
def process_files_level(files: list[Path], level: int = 0) -> None:
    """批量处理文件等级标记。

    Parameters
    ----------
    files:
        文件路径列表
    level:
        文件等级（0-4），0 用于清除等级
    """
    for target in files:
        process_file_level(target, level)
