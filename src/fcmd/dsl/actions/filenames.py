"""文件名批量操作动作：日期前缀/正则重命名/插入/大小写/等级标记。"""

from __future__ import annotations

import re
from pathlib import Path

from fcmd.dsl.actions import action

__all__: list[str] = []


# ---------------------------------------------------------------------- #
# 日期前缀（dateprefix_add/dateprefix_clear）
# ---------------------------------------------------------------------- #
def _file_timestamp(filepath: Path) -> str:
    """获取文件时间戳（取修改时间与创建时间的较大值），``YYYYMMDD`` 格式。"""
    import time  # 延迟导入：time 仅日期前缀/备份场景需要（冷启动导入税）

    stat = filepath.stat()
    return time.strftime("%Y%m%d", time.localtime(max(stat.st_mtime, stat.st_ctime)))


# 日期前缀正则：匹配 19xx/20xx 开头的 YYYYMMDD（允许分隔符 -_#.~）
_DATE_PATTERN = re.compile(r"(20|19)\d{2}[-_#.~]?((0[1-9])|(1[012]))[-_#.~]?((0[1-9])|([12]\d)|(3[01]))[-_#.~]?")
_DATE_SEP = "_"


def _strip_date_prefix(filepath: Path) -> Path:
    """移除文件名主干中的日期前缀（无前缀时打印提示返回原路径）。"""
    stem = filepath.stem
    new_stem = _DATE_PATTERN.sub("", stem)
    if new_stem == stem:
        print(f"{filepath} 无日期前缀")
        return filepath
    new_path = filepath.with_name(new_stem + filepath.suffix)
    filepath.rename(new_path)
    return new_path


@action("dateprefix_add", param_help={"files": "文件路径列表"})
def _dateprefix_add(files: list[Path]) -> None:
    """为文件名添加/更新日期前缀（基于文件修改/创建时间，先清后加避免重复）。"""
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            stripped = _strip_date_prefix(filepath)
            timestamp = _file_timestamp(stripped)
            target = stripped.with_name(f"{timestamp}{_DATE_SEP}{stripped.stem}{stripped.suffix}")
            stripped.rename(target)


@action("dateprefix_clear", param_help={"files": "文件路径列表"})
def _dateprefix_clear(files: list[Path]) -> None:
    """清除文件名中的日期前缀（缺失文件与点文件跳过）。"""
    for filepath in files:
        if filepath.exists() and not filepath.name.startswith("."):
            _strip_date_prefix(filepath)


# ---------------------------------------------------------------------- #
# 重命名原语（filerename_replace/filerename_insert/filerename_case）
# ---------------------------------------------------------------------- #
def _safe_rename(filepath: Path, new_stem: str, preview: bool) -> bool:
    """安全重命名文件名主干（保留扩展名），返回是否执行。

    目标名与原名相同时静默跳过；目标已存在且非同一文件时跳过并提示
    （大小写不敏感文件系统上仅大小写不同视为同一文件允许重命名）。
    """
    if new_stem == filepath.stem:
        return False
    target = filepath.with_name(new_stem + filepath.suffix)
    if target.exists() and target.resolve() != filepath.resolve():
        print(f"跳过（目标已存在）: {filepath.name} -> {target.name}")
        return False
    if preview:
        print(f"[预览] {filepath.name} -> {target.name}")
    else:
        filepath.rename(target)
        print(f"重命名: {filepath.name} -> {target.name}")
    return True


@action(
    "filerename_replace",
    param_help={
        "files": "待重命名的文件列表",
        "pattern": "正则表达式（Python re 语法，支持反向引用）",
        "replacement": "替换字符串（默认空字符串，即删除匹配部分）",
        "preview": "仅预览不实际执行",
    },
)
def _filerename_replace(files: list[Path], pattern: str, replacement: str = "", preview: bool = False) -> None:
    """正则替换文件名主干中的匹配部分（保留扩展名，仅匹配时执行）。"""
    compiled = re.compile(pattern)  # 语法错误 → 任务失败汇总 + 退出码 1
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        if not compiled.search(filepath.stem):
            continue
        _safe_rename(filepath, compiled.sub(replacement, filepath.stem), preview)


@action(
    "filerename_insert",
    param_help={
        "files": "待重命名的文件列表",
        "text": "待插入文本",
        "position": "插入位置（0=开头，负数从末尾计算，超出范围自动截断）",
        "preview": "仅预览不实际执行",
    },
)
def _filerename_insert(files: list[Path], text: str, position: int = 0, preview: bool = False) -> None:
    """在文件名主干指定位置插入文本（保留扩展名）。"""
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        if not text:
            continue
        stem = filepath.stem
        pos = max(0, min(position, len(stem)))
        _safe_rename(filepath, stem[:pos] + text + stem[pos:], preview)


@action(
    "filerename_case",
    param_help={
        "files": "待重命名的文件列表",
        "mode": "转换模式：lower / upper / title（默认 lower）",
        "preview": "仅预览不实际执行",
    },
)
def _filerename_case(files: list[Path], mode: str = "lower", preview: bool = False) -> None:
    """转换文件名主干大小写（保留扩展名）。"""
    mode_map = {"lower": str.lower, "upper": str.upper, "title": str.title}
    if mode not in mode_map:
        raise ValueError(f"不支持的大小写模式: {mode}（可选: lower/upper/title）")
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        _safe_rename(filepath, mode_map[mode](filepath.stem), preview)


# ---------------------------------------------------------------------- #
# 等级标记（filelevel_set）
# ---------------------------------------------------------------------- #
# 等级标记映射：0 表示清除等级，1-4 对应不同等级标记
_LEVELS: dict[str, str] = {"0": "", "1": "PUB,NOR", "2": "INT", "3": "CON", "4": "CLA"}
# 左右括号集合：标记两侧的括号字符（用于识别并整体移除）
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


def _process_file_level(filepath: Path, level: int) -> None:
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
        print(f"重命名: {filepath} -> {new_path}")


@action(
    "filelevel_set",
    param_help={
        "files": "文件路径列表",
        "level": "文件等级（0-4），0 用于清除等级",
    },
)
def _filelevel_set(files: list[Path], level: int = 0) -> None:
    """为文件名添加或清除等级标记（PUB/NOR/INT/CON/CLA）。"""
    if not (0 <= level < len(_LEVELS)):
        raise ValueError(f"无效的等级 {level}，必须在 0 和 {len(_LEVELS) - 1} 之间")
    for filepath in files:
        if not filepath.exists():
            print(f"文件不存在: {filepath}")
            continue
        _process_file_level(filepath, level)
