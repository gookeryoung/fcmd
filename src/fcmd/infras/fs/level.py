"""``fcmd.infras.fs.level``（由 ``infras/fs.py`` 拆分）。"""

from __future__ import annotations

from pathlib import Path

__all__ = ["set_file_level"]


_LEVELS: dict[str, str] = {"0": "", "1": "PUB,NOR", "2": "INT", "3": "CON", "4": "CLA"}


_LEVEL_BRACKETS: tuple[str, str] = (" ([_(【-", " )]_）】")


def _remove_level_marks(stem: str, marks: list[str]) -> str:
    """从文件名主干中移除所有被括号包裹的标记（裸字符串形式保留）。"""
    left_brackets, right_brackets = _LEVEL_BRACKETS
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


def _process_file_level(filepath: Path, level: int) -> str | None:
    """单文件等级标记处理：先清除所有已有标记，再按 ``level`` 添加新标记。"""
    filestem = filepath.stem
    original_stem = filestem
    for level_names in _LEVELS.values():
        if level_names:
            filestem = _remove_level_marks(filestem, level_names.split(","))
    for digit in map(str, range(1, 10)):
        filestem = _remove_level_marks(filestem, [digit])
    if level > 0:
        levelstr = _LEVELS[str(level)].split(",")[0]
        if levelstr:
            filestem = f"{filestem}({levelstr})"
    if filestem != original_stem:
        new_path = filepath.with_name(filestem + filepath.suffix)
        filepath.rename(new_path)
        return f"重命名: {filepath} -> {new_path}"
    return None


def set_file_level(files: list[Path], level: int = 0) -> list[str]:
    """为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA），返回过程反馈。"""
    if not (0 <= level < len(_LEVELS)):
        raise ValueError(f"无效的等级 {level}，必须在 0 和 {len(_LEVELS) - 1} 之间")
    messages: list[str] = []
    for filepath in files:
        if not filepath.exists():
            messages.append(f"文件不存在: {filepath}")
            continue
        message = _process_file_level(filepath, level)
        if message is not None:
            messages.append(message)
    return messages
