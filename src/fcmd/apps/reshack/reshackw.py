"""reshackw - Windows PE 资源查看与修改（Qt GUI）。

侧边栏分页窗口（与 wslmanw 同构）：「资源」页平铺资源行卡（类型筛选 +
行内展开文本 / 十六进制 / 图像预览，行内直接导出 / 替换）；「文件」页放
文件信息、最近打开与全部导出。解析与写回复用 :mod:`fcmd.apps.reshack.reshack`，
需安装 ``pip install fcmd[pe]``。任务日志不占分页，从「帮助 → 任务日志…」
以非模态窗口打开。

设置（上次文件、最近打开、侧边栏折叠、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/reshackw.json``，下次启动自动恢复。

示例
----
    fcmd reshackw         # 打开 PE 资源编辑器窗口
    reshackw              # 全局命令等价入口
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fcmd
from fcmd.apps.reshack._core import (
    FIT_MAX_UPSCALE,
    HEX_PREVIEW_LIMIT,
    MAX_ZOOM,
    MIN_ZOOM,
    TEXT_PREVIEW_LIMIT,
    ZOOM_STEP,
    clamp_zoom,
    copy_image_to_clipboard,
    decode_text,
    default_export_name,
    fit_zoom,
    format_hex_preview,
    group_resources,
    image_to_dib,
    is_text_resource,
    load_preview_image,
    zoom_in,
    zoom_out,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow

__all__ = [
    "FIT_MAX_UPSCALE",
    "HEX_PREVIEW_LIMIT",
    "MAX_ZOOM",
    "MIN_ZOOM",
    "TEXT_PREVIEW_LIMIT",
    "ZOOM_STEP",
    "ReshackSettings",
    "add_recent_file",
    "clamp_zoom",
    "config_path",
    "copy_image_to_clipboard",
    "decode_text",
    "default_export_name",
    "fit_zoom",
    "format_hex_preview",
    "group_resources",
    "image_to_dib",
    "is_text_resource",
    "load_preview_image",
    "load_settings",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
    "zoom_in",
    "zoom_out",
]

_DESCRIPTION = (
    "PE 资源编辑器窗口版（Qt）：侧边栏分页（资源 / 文件），资源行卡平铺展示，"
    "行内展开文本/十六进制/图像预览（图像可缩放/适应窗口，右键复制到剪贴板或另存为 PNG/ICO/BMP）、"
    "单资源导出（Icon/Bitmap 自动补 ICO/BMP 头）与替换（自动备份）、全部导出；"
    "任务日志从「帮助 → 任务日志…」打开；设置持久化到 ~/.fcmd/ui/reshackw.json，依赖 fcmd[pe]"
)

DEFAULT_GEOMETRY = "1080x720+180+120"
MIN_WIDTH = 900
MIN_HEIGHT = 600
RECENT_LIMIT = 10


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/reshackw.json``。"""
    return _guiconfig.config_path("reshackw")


@dataclass
class ReshackSettings:
    """PE 资源编辑器的持久化设置。"""

    file_path: str = ""
    recent_files: list[str] = field(default_factory=list)
    sidebar_collapsed: bool = False
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def add_recent_file(files: list[str], path: Path, limit: int = RECENT_LIMIT) -> list[str]:
    """把路径插入最近列表头部，去重并截断到 ``limit`` 条。"""
    entry = str(path)
    remaining = [item for item in files if item != entry]
    return [entry, *remaining][:limit]


def load_settings(path: Path | None = None) -> ReshackSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return ReshackSettings()

    settings = ReshackSettings()
    file_path = raw.get("file_path")
    recent = raw.get("recent_files")
    geometry = raw.get("geometry")
    if isinstance(file_path, str):
        settings.file_path = file_path
    if isinstance(recent, list):
        settings.recent_files = [item for item in recent if isinstance(item, str)][:RECENT_LIMIT]
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if raw.get("sidebar_collapsed") is True:
        settings.sidebar_collapsed = True
    return settings


def save_settings(settings: ReshackSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "file_path": settings.file_path,
        "recent_files": settings.recent_files,
        "sidebar_collapsed": settings.sidebar_collapsed,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# GUI 入口
# ---------------------------------------------------------------------- #
def run_gui(settings_path: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.qt.app`` 统一处理）。"""
    try:
        from fcmd.apps.reshack._reshackw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt(settings_path)


@fcmd.tool("reshackw", help="打开 PE 资源编辑器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PE 资源编辑器窗口。"""
    run_gui()


@fcmd.main("reshackw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
