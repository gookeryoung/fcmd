"""llmw 的窗口设置：geometry、硬件快照、性能实测与下载设置。

Qt 窗口（``_llmw_qt_window.py``）使用这一份设置模型，文件固定为
``${FCMD_HOME:-~/.fcmd}/ui/llmw.json``，保存窗口位置、硬件缓存、性能实测、
下载参数、按模型保存的启动参数、上次启动的量化精度、画图页设置与模型列表
更新时间。解析与校验都是纯逻辑，不依赖任何 GUI 工具包。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fcmd.apps.llm import _llmconfig
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _geometry

__all__ = [
    "DEFAULT_HEIGHT",
    "DEFAULT_WIDTH",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "LlmSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

MIN_WIDTH = 1000
MIN_HEIGHT = 600
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 880


@dataclass
class LlmSettings:
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
    models_updated_at: str | None = None
    chat_settings_open: bool = False
    chat_sidebar_width: int = 120
    chat_sidebar_collapsed: bool = False
    sidebar_collapsed: bool = False
    model_view: dict[str, Any] | None = None
    chat_history: list[dict[str, Any]] | None = None
    chat_active: int = 0
    paint: dict[str, Any] | None = None


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/llmw.json``。

    改名前的 ``ui/locllmw.json`` 由 :func:`_llmconfig.ui_settings_path` 一次性
    搬迁到新名字并删除旧文件，老用户的历史设置与 geometry 不丢。
    """
    return _llmconfig.ui_settings_path()


valid_geometry = _guiconfig.valid_geometry


def load_settings(path: Path | None = None) -> LlmSettings:  # noqa: PLR0912 - 逐字段校验并回退
    """读取设置；文件缺失、JSON 损坏或 geometry 非法时回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    settings = LlmSettings()
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
        models_updated_at = raw.get("models_updated_at")
        if isinstance(models_updated_at, str) and models_updated_at.strip():
            settings.models_updated_at = models_updated_at
        chat_settings_open = raw.get("chat_settings_open")
        if isinstance(chat_settings_open, bool):
            settings.chat_settings_open = chat_settings_open
        chat_sidebar_width = raw.get("chat_sidebar_width")
        if isinstance(chat_sidebar_width, int) and not isinstance(chat_sidebar_width, bool):
            # 旧版本的默认宽度（160 / 200 / 240）迁移到新的紧凑默认值，
            # 避免升级后每次打开仍是很宽的侧栏；用户显式拖动过的其它宽度保留。
            width = 120 if chat_sidebar_width in {160, 200, 240} else chat_sidebar_width
            settings.chat_sidebar_width = min(max(width, 120), 480)
        chat_sidebar_collapsed = raw.get("chat_sidebar_collapsed")
        if isinstance(chat_sidebar_collapsed, bool):
            settings.chat_sidebar_collapsed = chat_sidebar_collapsed
        sidebar_collapsed = raw.get("sidebar_collapsed")
        if isinstance(sidebar_collapsed, bool):
            settings.sidebar_collapsed = sidebar_collapsed
        model_view = raw.get("model_view")
        if isinstance(model_view, dict):
            settings.model_view = {
                str(key): value
                for key, value in model_view.items()
                if isinstance(value, (str, bool)) and (not isinstance(value, str) or value.strip() or key == "owner")
            }
        chat_history = raw.get("chat_history")
        if isinstance(chat_history, list):
            settings.chat_history = [dict(item) for item in chat_history if isinstance(item, dict)]
        chat_active = raw.get("chat_active")
        if isinstance(chat_active, int) and not isinstance(chat_active, bool) and chat_active >= 0:
            settings.chat_active = chat_active
        paint = raw.get("paint")
        if isinstance(paint, dict):
            settings.paint = paint
    return settings


def save_settings(settings: LlmSettings, path: Path | None = None) -> None:
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
            "models_updated_at": settings.models_updated_at,
            "chat_settings_open": settings.chat_settings_open,
            "chat_sidebar_width": settings.chat_sidebar_width,
            "chat_sidebar_collapsed": settings.chat_sidebar_collapsed,
            "sidebar_collapsed": settings.sidebar_collapsed,
            "model_view": settings.model_view,
            "chat_history": settings.chat_history,
            "chat_active": settings.chat_active,
            "paint": settings.paint,
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
