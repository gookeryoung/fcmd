"""``fcmd.infras.fs.level``（由 ``infras/fs.py`` 拆分）。

文件名等级标记（PUB/NOR/INT/CON/CLA）的映射、括号口径与本地配置持久化。
默认值仍与旧版本一致，但等级映射和括号字符会从
``${FCMD_HOME:-~/.fcmd}/filelevel.json`` 读取；用户修改该文件（或用
``fcmd filelevel config``）后，下一次 ``set`` 立即生效，不需要改代码。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from fcmd.infras.jsonio import read_json_file, write_json_atomic
from fcmd.shared.paths import fcmd_home

__all__ = [
    "CONFIG_SCHEMA",
    "DEFAULT_BRACKETS",
    "DEFAULT_LEVELS",
    "FileLevelConfig",
    "config_path",
    "config_to_mapping",
    "default_config",
    "load_filelevel_config",
    "save_filelevel_config",
    "set_file_level",
    "update_brackets",
    "update_levels",
]

CONFIG_SCHEMA = "filelevel-config/1"
"""配置文件 schema 标识；仅用于提示，不匹配时仍尽量按字段读取。"""

DEFAULT_LEVELS: dict[str, str] = {"0": "", "1": "PUB,NOR", "2": "INT", "3": "CON", "4": "CLA"}
"""等级 → 标记；一个等级可写多个标记（逗号分隔），添加时取第一个，清除时全部移除。"""

DEFAULT_BRACKETS: dict[str, str] = {"left": " ([_(【-", "right": " )]_）】"}
"""允许包裹等级标记的左右括号字符集；不在集合里的裸标记不会被清除。"""


@dataclass(frozen=True, slots=True)
class FileLevelConfig:
    """filelevel 的可持久化配置。"""

    levels: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_LEVELS))
    brackets: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_BRACKETS))

    @property
    def max_level(self) -> int:
        """当前配置支持的最大等级（含 0）。"""
        return max((int(key) for key in self.levels), default=0)

    def mark_for(self, level: int) -> str:
        """某个等级添加时使用的标记；未配置时返回空串。"""
        return str(self.levels.get(str(level), "")).split(",")[0].strip()

    def removable_marks(self) -> tuple[str, ...]:
        """清等级时需要移除的全部标记（按配置顺序去重）。"""
        marks: list[str] = []
        for value in self.levels.values():
            for mark in str(value).split(","):
                name = mark.strip()
                if name and name not in marks:
                    marks.append(name)
        return tuple(marks)

    @property
    def left_brackets(self) -> str:
        return str(self.brackets.get("left") or DEFAULT_BRACKETS["left"])

    @property
    def right_brackets(self) -> str:
        return str(self.brackets.get("right") or DEFAULT_BRACKETS["right"])


def default_config() -> FileLevelConfig:
    """内置默认配置（未创建配置文件时使用）。"""
    return FileLevelConfig(dict(DEFAULT_LEVELS), dict(DEFAULT_BRACKETS))


def config_path() -> Path:
    """配置文件路径：``${FCMD_HOME:-~/.fcmd}/filelevel.json``。"""
    return fcmd_home() / "filelevel.json"


def config_to_mapping(config: FileLevelConfig) -> dict[str, Any]:
    """序列化为 JSON 友好的映射（键按等级数值排序）。"""
    levels = {key: config.levels[key] for key in sorted(config.levels, key=int)}
    return {
        "schema": CONFIG_SCHEMA,
        "levels": levels,
        "brackets": {"left": config.left_brackets, "right": config.right_brackets},
    }


def _normalize_levels(raw: object) -> tuple[dict[str, str], tuple[str, ...]]:  # noqa: PLR0911
    """校验等级映射；非法 / 缺级 / 空标记时整份回落默认值。"""
    if not isinstance(raw, Mapping):
        return dict(DEFAULT_LEVELS), ("levels 不是对象，已使用内置默认等级",)
    values: dict[int, str] = {}
    for key, value in raw.items():
        try:
            level = int(str(key).strip())
        except (TypeError, ValueError):
            return dict(DEFAULT_LEVELS), (f"等级键不是整数：{key!r}，已使用内置默认等级",)
        if level < 0 or not isinstance(value, str):
            return dict(DEFAULT_LEVELS), (f"等级 {key!r} 的配置非法，已使用内置默认等级",)
        if level == 0:
            continue
        if not any(part.strip() for part in value.split(",")):
            return dict(DEFAULT_LEVELS), (f"等级 {level} 没有标记，已使用内置默认等级",)
        values[level] = value.strip()
    if not values:
        return dict(DEFAULT_LEVELS), ("没有配置任何等级，已使用内置默认等级",)
    maximum = max(values)
    missing = [level for level in range(1, maximum + 1) if level not in values]
    if missing:
        return dict(DEFAULT_LEVELS), (f"等级不连续（缺少 {missing}），已使用内置默认等级",)
    return {"0": "", **{str(level): values[level] for level in range(1, maximum + 1)}}, ()


def _normalize_brackets(raw: object) -> tuple[dict[str, str], tuple[str, ...]]:
    if not isinstance(raw, Mapping):
        return dict(DEFAULT_BRACKETS), ("brackets 不是对象，已使用内置默认括号",)
    left = raw.get("left")
    right = raw.get("right")
    if not isinstance(left, str) or not left or not isinstance(right, str) or not right:
        return dict(DEFAULT_BRACKETS), ("brackets 需要非空的 left / right 字符串，已使用内置默认括号",)
    return {"left": left, "right": right}, ()


def load_filelevel_config(path: Path | None = None) -> tuple[FileLevelConfig, tuple[str, ...]]:
    """读取本地配置；文件缺失时返回默认值，坏字段逐项回落并给出提示。"""
    target = path or config_path()
    if not target.exists():
        return default_config(), ()
    try:
        raw = read_json_file(target)
    except (OSError, ValueError) as exc:
        return default_config(), (f"配置文件读取失败（{exc}），已使用内置默认值：{target}",)
    if not isinstance(raw, Mapping):
        return default_config(), (f"配置文件不是 JSON 对象（{target}），已使用内置默认值",)
    warnings: list[str] = []
    schema = raw.get("schema")
    if schema is not None and schema != CONFIG_SCHEMA:
        warnings.append(f"配置 schema 为 {schema!r}（期望 {CONFIG_SCHEMA}），仍按字段读取")
    levels, level_warnings = _normalize_levels(raw.get("levels"))
    brackets, bracket_warnings = _normalize_brackets(raw.get("brackets"))
    warnings.extend(level_warnings)
    warnings.extend(bracket_warnings)
    return FileLevelConfig(levels, brackets), tuple(warnings)


def save_filelevel_config(config: FileLevelConfig, path: Path | None = None) -> Path:
    """原子写入配置文件，返回实际写入路径。"""
    target = path or config_path()
    write_json_atomic(target, config_to_mapping(config), sort_keys=False)
    return target


def update_levels(config: FileLevelConfig, overrides: Sequence[str]) -> FileLevelConfig:
    """按 ``N=NAME`` 修改已有等级；不允许新增 / 删除等级或写入空标记。"""
    levels = dict(config.levels)
    for item in overrides:
        key, separator, name = str(item).partition("=")
        if not separator:
            raise ValueError(f"等级格式应为 N=NAME，收到：{item!r}")
        try:
            level = int(key.strip())
        except ValueError as exc:
            raise ValueError(f"等级编号必须是整数：{item!r}") from exc
        if level <= 0 or str(level) not in levels:
            raise ValueError(f"等级 {level} 未定义；只能修改已有等级 {sorted(int(k) for k in levels if k != '0')}")
        mark = name.strip()
        if not mark:
            raise ValueError(f"等级 {level} 的标记不能为空")
        levels[str(level)] = mark
    return replace(config, levels=levels)


def update_brackets(config: FileLevelConfig, *, left: str = "", right: str = "") -> FileLevelConfig:
    """修改左右括号字符集；空串表示保持原值。"""
    brackets = dict(config.brackets)
    if left:
        brackets["left"] = left
    if right:
        brackets["right"] = right
    return replace(config, brackets=brackets)


def _remove_level_marks(stem: str, marks: list[str], *, left: str, right: str) -> str:
    """从文件名主干中移除所有被括号包裹的标记（裸字符串形式保留）。"""
    for mark in marks:
        pos = 0
        while True:
            pos = stem.find(mark, pos)
            if pos == -1:
                break
            b, e = pos - 1, pos + len(mark)
            if b >= 0 and e < len(stem) and stem[b] in left and stem[e] in right:
                stem = stem[:b] + stem[e + 1 :]
            else:
                pos = e
    return stem


def _process_file_level(filepath: Path, level: int, config: FileLevelConfig) -> str | None:
    """单文件等级标记处理：先清除所有已有标记，再按 ``level`` 添加新标记。"""
    filestem = filepath.stem
    original_stem = filestem
    filestem = _remove_level_marks(
        filestem,
        list(config.removable_marks()),
        left=config.left_brackets,
        right=config.right_brackets,
    )
    for digit in map(str, range(1, 10)):
        filestem = _remove_level_marks(filestem, [digit], left=config.left_brackets, right=config.right_brackets)
    if level > 0:
        levelstr = config.mark_for(level)
        if levelstr:
            filestem = f"{filestem}({levelstr})"
    if filestem != original_stem:
        new_path = filepath.with_name(filestem + filepath.suffix)
        filepath.rename(new_path)
        return f"重命名: {filepath} -> {new_path}"
    return None


def set_file_level(
    files: list[Path],
    level: int = 0,
    *,
    config: FileLevelConfig | None = None,
) -> list[str]:
    """为文件名添加或清除等级标记，返回过程反馈。

    ``config`` 缺省时读取 ``${FCMD_HOME}/filelevel.json``；等级映射与括号都来自
    该配置，因此用户改配置后无需重启或改代码。
    """
    active = config if config is not None else load_filelevel_config()[0]
    if level < 0 or level > active.max_level:
        raise ValueError(f"无效的等级 {level}，必须在 0 和 {active.max_level} 之间")
    if level > 0 and not active.mark_for(level):
        raise ValueError(f"等级 {level} 没有配置标记，请检查 {config_path()}")
    messages: list[str] = []
    for filepath in files:
        if not filepath.exists():
            messages.append(f"文件不存在: {filepath}")
            continue
        message = _process_file_level(filepath, level, active)
        if message is not None:
            messages.append(message)
    return messages
