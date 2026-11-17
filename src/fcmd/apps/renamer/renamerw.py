"""renamerw - 批量重命名窗口版（Qt）。

上方「文件」表同时列出名称、新名与状态，下方编辑规则流水线（替换 / 重排 / 搬移
/ 插入 / 大小写 / 计数器），规则或文件变动后自动重算；确认后两阶段执行，可撤销
本次运行结果。规则可导出 / 导入 JSON；

窗口设置（文件列表、规则、递归与排序选项、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/renamerw.json``，下次启动自动恢复。

示例
----
    fcmd renamerw        # 打开批量重命名窗口
    renamerw             # 全局命令等价入口
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import fcmd
from fcmd.apps.renamer._core import (
    ANCHOR_AFTER,
    ANCHOR_BEFORE,
    ANCHOR_END,
    ANCHOR_INDEX,
    ANCHOR_START,
    FIELD_EXT,
    FIELD_STEM,
    SORT_KEYS,
    SORT_NAME,
    STATUS_CONFLICT,
    STATUS_INVALID,
    STATUS_OK,
    STATUS_UNCHANGED,
    RenameRule,
    RuleError,
    parse_rules,
    rules_to_json,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "ANCHOR_TEXT",
    "CASE_TEXT",
    "DEFAULT_GEOMETRY",
    "FIELD_TEXT",
    "KIND_HINT",
    "KIND_TEXT",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SORT_TEXT",
    "STATUS_TEXT",
    "RenamerSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "rule_to_row",
    "rules_from_text",
    "rules_to_text",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "批量重命名窗口版：目标文件/目录选择、规则流水线编辑（正则替换、捕获组重排、"
    "匹配文本搬移、插入、大小写、计数器）、勾选前预览与冲突/非法标记、两阶段执行与撤销、"
    "规则 JSON 导入导出；设置持久化到 ~/.fcmd/ui/renamerw.json"
)

DEFAULT_GEOMETRY = "1120x760+120+60"
MIN_WIDTH = 960
MIN_HEIGHT = 600

KIND_TEXT = {
    "replace": "替换",
    "reorder": "重排",
    "move": "搬移",
    "insert": "插入",
    "case": "大小写",
    "sequence": "计数器",
}
KIND_HINT = {
    "replace": "模板用 \\1 / \\g<name> 引用捕获组；第几个匹配 0 = 全部",
    "reorder": "模板用 {0}/{1}/{name} 引用捕获组",
    "move": "把匹配文本整体搬到锚点；第几个匹配 0 = 全部",
    "insert": "在锚点插入字面文本；锚点文本仅「锚点前/后」需要",
    "case": "转换名称或扩展名的大小写",
    "sequence": "按「计数器排序」生成序号；分隔符可空",
}
FIELD_TEXT = {FIELD_STEM: "名称", FIELD_EXT: "扩展名"}
ANCHOR_TEXT = {
    ANCHOR_START: "开头",
    ANCHOR_END: "结尾",
    ANCHOR_BEFORE: "锚点前",
    ANCHOR_AFTER: "锚点后",
    ANCHOR_INDEX: "指定位置",
}
CASE_TEXT = {
    "keep": "保持",
    "lower": "小写",
    "upper": "大写",
    "title": "每词首字母",
    "sentence": "仅首字母",
}
SORT_TEXT = {"name": "名称", "modified": "修改时间", "created": "创建时间"}
STATUS_TEXT = {
    STATUS_OK: "可执行",
    STATUS_UNCHANGED: "未变化",
    STATUS_INVALID: "非法",
    STATUS_CONFLICT: "冲突",
}


# ---------------------------------------------------------------------- #
# 纯逻辑（可单测）
# ---------------------------------------------------------------------- #
def rules_from_text(text: str) -> tuple[RenameRule, ...]:
    """解析规则 JSON 文本；空文本视为空规则集。"""
    if not text.strip():
        return ()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则 JSON 解析失败：{exc}") from exc
    return parse_rules(raw)


def rules_to_text(rules: list[RenameRule] | tuple[RenameRule, ...]) -> str:
    """把规则集格式化为 JSON 文本。"""
    return json.dumps(rules_to_json(rules), ensure_ascii=False, indent=2)


def rule_to_row(rule: RenameRule) -> tuple[str, str, str, str]:
    """把规则转成表格行：类型 / 作用域 / 规则参数 / 说明。"""
    kind = KIND_TEXT.get(rule.kind, rule.kind)
    scope = FIELD_TEXT.get(rule.field, rule.field)
    if rule.kind in ("replace", "reorder", "move"):
        params = f"{rule.pattern}  ⇒  {rule.replacement}" if rule.replacement else rule.pattern
    elif rule.kind == "insert":
        params = rule.text
    elif rule.kind == "case":
        params = CASE_TEXT.get(rule.mode, rule.mode)
    else:
        params = f"{rule.text}{rule.start}..（步长 {rule.step}，{rule.digits} 位）"
    return kind, scope, params, _rule_detail(rule)


def _rule_detail(rule: RenameRule) -> str:
    """生成规则的一行人类可读说明。"""
    if rule.kind == "replace":
        detail = "正则替换"
        if rule.occurrence:
            detail += f"，仅第 {rule.occurrence} 处"
        return detail + ("（忽略大小写）" if rule.ignore_case else "")
    if rule.kind == "reorder":
        return f"捕获组按模板重排：{rule.replacement}"
    if rule.kind == "move":
        detail = f"匹配文本搬到{ANCHOR_TEXT.get(rule.anchor, rule.anchor)}"
        if rule.anchor in (ANCHOR_BEFORE, ANCHOR_AFTER):
            detail += f"「{rule.anchor_text}」"
        elif rule.anchor == ANCHOR_INDEX:
            detail += f" {rule.position}"
        return detail
    if rule.kind == "insert":
        return f"在{ANCHOR_TEXT.get(rule.anchor, rule.anchor)}插入"
    if rule.kind == "case":
        return f"转为{CASE_TEXT.get(rule.mode, rule.mode)}"
    return f"计数器：{rule.start} 起步长 {rule.step}，{rule.digits} 位补零"


def config_path() -> Path:
    """返回设置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/renamerw.json``。"""
    return _guiconfig.config_path("renamerw")


valid_geometry = _guiconfig.valid_geometry


@dataclass
class RenamerSettings:
    """窗口与配置文件共用的设置。"""

    targets: list[str] = field(default_factory=list)
    rules_json: str = "[]"
    recursive: bool = False
    include_dirs: bool = False
    sort_key: str = SORT_NAME
    geometry: str | None = None


def settings_to_mapping(settings: RenamerSettings) -> dict[str, Any]:
    """把设置转成可写入 JSON 的字典。"""
    return {
        "targets": list(settings.targets),
        "rules_json": settings.rules_json,
        "recursive": settings.recursive,
        "include_dirs": settings.include_dirs,
        "sort_key": settings.sort_key,
        "geometry": settings.geometry,
    }


def settings_from_mapping(
    raw: Mapping[str, Any],
    base: RenamerSettings | None = None,
) -> RenamerSettings:
    """从字典解析设置；缺失 / 类型不符的字段保留默认值或 ``base``。"""
    settings = replace(base) if base is not None else RenamerSettings()
    targets = raw.get("targets")
    if isinstance(targets, list):
        settings.targets = [item for item in targets if isinstance(item, str)]
    rules_json = raw.get("rules_json")
    if isinstance(rules_json, str):
        try:
            rules_from_text(rules_json)
        except RuleError:
            settings.rules_json = "[]"
        else:
            settings.rules_json = rules_json
    for name in ("recursive", "include_dirs"):
        value = raw.get(name)
        if isinstance(value, bool):
            setattr(settings, name, value)
    sort_key = raw.get("sort_key")
    if isinstance(sort_key, str) and sort_key in SORT_KEYS:
        settings.sort_key = sort_key
    geometry = raw.get("geometry")
    settings.geometry = geometry if isinstance(geometry, str) and valid_geometry(geometry) else None
    return settings


def load_settings(path: Path | None = None) -> RenamerSettings:
    """读取设置；文件缺失、损坏或顶层非对象时返回默认设置。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return RenamerSettings()
    return settings_from_mapping(raw)


def save_settings(settings: RenamerSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界 / 异常时回落到默认位置并按屏幕夹紧。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# 界面入口
# ---------------------------------------------------------------------- #
def _label_value(label: str, labels: Mapping[str, str], default: str) -> str:
    """把界面标签（或值本身）转回枚举值；无法识别时返回 ``default``。"""
    for value, text in labels.items():
        if label in (text, value):
            return value
    return default


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.apps.renamer._renamerw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("renamerw", help="打开批量重命名窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开批量重命名窗口。"""
    run_gui()


@fcmd.main("renamerw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
