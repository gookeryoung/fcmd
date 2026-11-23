"""filesyncw - 文件夹比较与同步（Qt GUI 入口与设置）。

左右两个目录 + 比较 / 同步选项：按「时间与大小 / 仅大小 / 内容」比较，
结果表列出仅左、仅右、不同与相同项及计划动作；按「双向 / 镜像 / 更新」
同步，支持「预演」只统计不写盘。界面在
:mod:`fcmd.apps.filesync._filesyncw_qt`，本模块只负责设置读写、模式标签
转换与 geometry 计算，CI 无显示环境也能单测。

菜单「帮助 → 载入配置 / 保存配置」可把当前左右目录与选项存成 JSON 复用；
窗口设置（目录、比较 / 同步模式、过滤、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/filesyncw.json``，下次启动自动恢复。

示例
----
    fcmd filesyncw        # 打开文件夹比较与同步窗口
    filesyncw             # 全局命令等价入口
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.filesync._core import (
    COMPARE_MODE_TEXT,
    COMPARE_MODES,
    SYNC_MODE_TEXT,
    SYNC_MODES,
    SYNC_TWO_WAY,
    normalize_compare_mode,
    normalize_sync_mode,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow
from fcmd.gui.settings import SettingsStore

__all__ = [
    "DEFAULT_GEOMETRY",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "FilesyncSettings",
    "config_path",
    "load_settings",
    "mode_label",
    "mode_value",
    "resolve_geometry",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "valid_geometry",
]

_DESCRIPTION = (
    "文件夹比较与同步窗口版（Qt）：左右目录对比（时间/大小/内容），结果表列出差异与计划动作，"
    "支持双向/镜像/更新同步与试运行，冲突项自动跳过；"
    "设置持久化到 ~/.fcmd/ui/filesyncw.json"
)

DEFAULT_GEOMETRY = "1120x780+140+70"
MIN_WIDTH = 900
MIN_HEIGHT = 620

COMPARE_CHOICES: tuple[str, ...] = tuple(COMPARE_MODE_TEXT[mode] for mode in COMPARE_MODES)
SYNC_CHOICES: tuple[str, ...] = tuple(SYNC_MODE_TEXT[mode] for mode in SYNC_MODES)


# ---------------------------------------------------------------------- #
# 配置持久化（纯逻辑，可单测）
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/filesyncw.json``。"""
    return _guiconfig.config_path("filesyncw")


valid_geometry = _guiconfig.valid_geometry


@dataclass
class FilesyncSettings:
    """窗口与配置文件共用的设置。"""

    left: str = ""
    right: str = ""
    compare_mode: str = COMPARE_MODES[0]
    sync_mode: str = SYNC_TWO_WAY
    include: str = ""
    exclude: str = ""
    skip_hidden: bool = False
    show_equal: bool = False
    geometry: str | None = None


def settings_to_mapping(settings: FilesyncSettings) -> dict[str, Any]:
    """把设置转成可写入 JSON 的字典。"""
    return {
        "left": settings.left,
        "right": settings.right,
        "compare_mode": settings.compare_mode,
        "sync_mode": settings.sync_mode,
        "include": settings.include,
        "exclude": settings.exclude,
        "skip_hidden": settings.skip_hidden,
        "show_equal": settings.show_equal,
        "geometry": settings.geometry,
    }


def settings_from_mapping(
    raw: Mapping[str, Any],
    base: FilesyncSettings | None = None,
) -> FilesyncSettings:
    """从字典解析设置；缺失或类型不符的字段保留默认值 / ``base`` 值。

    比较 / 同步模式未知时回退默认值，geometry 非法时置空。
    """
    settings = replace(base) if base is not None else FilesyncSettings()
    for name in ("left", "right", "include", "exclude"):
        value = raw.get(name)
        if isinstance(value, str):
            setattr(settings, name, value)
    compare_mode = raw.get("compare_mode")
    if isinstance(compare_mode, str):
        try:
            settings.compare_mode = normalize_compare_mode(compare_mode)
        except ValueError:
            settings.compare_mode = COMPARE_MODES[0]
    sync_mode = raw.get("sync_mode")
    if isinstance(sync_mode, str):
        try:
            settings.sync_mode = normalize_sync_mode(sync_mode)
        except ValueError:
            settings.sync_mode = SYNC_TWO_WAY
    for name in ("skip_hidden", "show_equal"):
        value = raw.get(name)
        if isinstance(value, bool):
            setattr(settings, name, value)
    geometry = raw.get("geometry")
    settings.geometry = geometry if isinstance(geometry, str) and valid_geometry(geometry) else None
    return settings


def load_settings(path: Path | None = None) -> FilesyncSettings:
    """读取设置文件；缺失、损坏或顶层非对象时返回默认设置。"""
    raw = SettingsStore("filesyncw", path).load()
    if raw is None:
        return FilesyncSettings()
    return settings_from_mapping(raw)


def save_settings(settings: FilesyncSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    SettingsStore("filesyncw", path).save(settings_to_mapping(settings))


def mode_label(value: str, labels: Mapping[str, str]) -> str:
    """把模式值转成界面展示文本；未知值回退首个标签。"""
    if value in labels:
        return labels[value]
    return next(iter(labels.values()))


def mode_value(label: str, labels: Mapping[str, str], default: str) -> str:
    """把界面文本（或模式值）转回模式值；无法识别时返回 ``default``。"""
    for value, text in labels.items():
        if label in (text, value):
            return value
    return default


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界 / 异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def run_gui(settings_path: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.apps.filesync._filesyncw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt(settings_path)


@fcmd.tool("filesyncw", help="打开文件夹比较与同步窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开文件夹比较与同步窗口。"""
    run_gui()


@fcmd.main("filesyncw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
