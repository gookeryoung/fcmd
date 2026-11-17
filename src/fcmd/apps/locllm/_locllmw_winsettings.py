"""locllmw 的窗口设置：geometry、硬件快照、性能实测与下载设置。

Qt 窗口（``_locllmw_qt_window.py``）使用这一份设置模型，文件固定为
``${FCMD_HOME:-~/.fcmd}/ui/locllmw.json``，保存窗口位置、硬件缓存、性能实测、
下载参数、按模型保存的启动参数与上次启动的量化精度。解析与校验都是纯逻辑，
不依赖任何 GUI 工具包。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _geometry

__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "LocllmSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

MIN_WIDTH = 1000
MIN_HEIGHT = 800
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 880


@dataclass
class LocllmSettings:
    """窗口持久化设置：geometry、对话设置展开状态、硬件快照与性能实测记录。"""

    geometry: str | None = None
    hardware: dict[str, Any] | None = None
    measured: dict[str, dict[str, Any]] | None = None
    download: dict[str, Any] | None = None
    download_totals: dict[str, int] | None = None
    convert: dict[str, Any] | None = None
    run_params: dict[str, dict[str, Any]] | None = None
    quant_choice: dict[str, str] | None = None
    last_model: str | None = None
    chat_settings_open: bool = False
    sidebar_collapsed: bool = False


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/locllmw.json``。"""
    return _guiconfig.config_path("locllmw")


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> LocllmSettings:
    """读取设置；文件缺失、JSON 损坏或 geometry 非法时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = LocllmSettings()
    if raw is not None:
        geometry = raw.get("geometry")
        if isinstance(geometry, str) and valid_geometry(geometry):
            settings.geometry = geometry
        hardware = raw.get("hardware")
        if isinstance(hardware, dict):
            settings.hardware = hardware
        measured = raw.get("measured")
        if isinstance(measured, dict):
            settings.measured = {str(key): dict(value) for key, value in measured.items() if isinstance(value, dict)}
        download = raw.get("download")
        if isinstance(download, dict):
            settings.download = download
        download_totals = raw.get("download_totals")
        if isinstance(download_totals, dict):
            settings.download_totals = {
                str(key): int(value)
                for key, value in download_totals.items()
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
            }
        convert = raw.get("convert")
        if isinstance(convert, dict):
            settings.convert = convert
        run_params = raw.get("run_params")
        if isinstance(run_params, dict):
            settings.run_params = {
                str(key): dict(value) for key, value in run_params.items() if isinstance(value, dict)
            }
        quant_choice = raw.get("quant_choice")
        if isinstance(quant_choice, dict):
            settings.quant_choice = {
                str(key): value for key, value in quant_choice.items() if isinstance(value, str) and value.strip()
            }
        last_model = raw.get("last_model")
        if isinstance(last_model, str) and last_model.strip():
            settings.last_model = last_model
        chat_settings_open = raw.get("chat_settings_open")
        if isinstance(chat_settings_open, bool):
            settings.chat_settings_open = chat_settings_open
        sidebar_collapsed = raw.get("sidebar_collapsed")
        if isinstance(sidebar_collapsed, bool):
            settings.sidebar_collapsed = sidebar_collapsed
    return settings


def save_settings(settings: LocllmSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败静默忽略（不影响窗口关闭）。"""
    _guiconfig.write_settings(
        {
            "geometry": settings.geometry,
            "hardware": settings.hardware,
            "measured": settings.measured,
            "download": settings.download,
            "download_totals": settings.download_totals,
            "convert": settings.convert,
            "run_params": settings.run_params,
            "quant_choice": settings.quant_choice,
            "last_model": settings.last_model,
            "chat_settings_open": settings.chat_settings_open,
            "sidebar_collapsed": settings.sidebar_collapsed,
        },
        path or config_path(),
    )


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到窗口原默认尺寸（按屏幕夹紧）。"""
    return _geometry.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=f"{DEFAULT_WIDTH}x{DEFAULT_HEIGHT}",
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )
