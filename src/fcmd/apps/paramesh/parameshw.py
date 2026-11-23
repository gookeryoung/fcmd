"""parameshw - 二维轴对称网格预览（Qt）。

界面只负责取数与绘制，网格生成、质量统计与线框变换全部位于
:mod:`fcmd.apps.paramesh._mesh` / ``_quality`` / ``_preview``，因此没有显示器
时也能单测纯逻辑部分。窗口复用 ``fcmd.gui`` 的主题与设置持久化。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.paramesh._config import LEVELS, MeshConfig
from fcmd.gui import config as _guiconfig
from fcmd.gui.settings import SettingsStore

__all__ = ["Settings", "load_settings", "run_gui", "save_settings"]

_DESCRIPTION = "二维轴对称网格预览（Qt）：按区域着色线框、局部放大、质量摘要与三档网格包导出"

DEFAULT_GEOMETRY = "1180x720"
MIN_WIDTH = 900
MIN_HEIGHT = 600
SETTINGS_NAME = "paramesh"
MIN_ZOOM = 0.1
MAX_ZOOM = 80.0


@dataclass(frozen=True, slots=True)
class Settings:
    """窗口设置：几何尺寸、上次模型 / 配置路径与当前档位。"""

    geometry: str | None = None
    model_path: str = ""
    config_path: str = ""
    level: str = "coarse"
    splitter: tuple[int, int] | None = None


def settings_path() -> Path:
    """配置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/paramesh.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> Settings:
    """读取设置；文件缺失或损坏时回落默认值。"""
    raw = SettingsStore("parameshw", path or settings_path()).load()
    if raw is None:
        return Settings()
    model_path = raw.get("model_path")
    config_path = raw.get("config_path")
    level = raw.get("level")
    splitter_raw = raw.get("splitter")
    splitter: tuple[int, int] | None = None
    if (
        isinstance(splitter_raw, list)
        and len(splitter_raw) == 2
        and all(isinstance(value, int) and not isinstance(value, bool) for value in splitter_raw)
    ):
        splitter = (splitter_raw[0], splitter_raw[1])
    return Settings(
        geometry=_guiconfig.valid_geometry(raw.get("geometry")),
        model_path=model_path if isinstance(model_path, str) else "",
        config_path=config_path if isinstance(config_path, str) else "",
        level=level if isinstance(level, str) and level in LEVELS else "coarse",
        splitter=splitter,
    )


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """写入设置；失败静默（关闭窗口不应因落盘失败中断）。"""
    SettingsStore("parameshw", path or settings_path()).save(
        {
            "geometry": settings.geometry,
            "model_path": settings.model_path,
            "config_path": settings.config_path,
            "level": settings.level,
            "splitter": list(settings.splitter) if settings.splitter is not None else None,
        },
    )


def region_color(region: str, palette: Any) -> str:
    """区域着色：炸药 / 间隙 / 管壁各一色；未知区域回落次要前景色。"""
    return {
        "propellant": palette.warning,
        "gap": palette.accent,
        "wall": palette.border_strong,
    }.get(region, palette.muted)


def _cli_default_config() -> MeshConfig:
    """默认配置；示例注入前作为占位（缺少反应区尺度，生成时会明确报错）。"""
    return MeshConfig()


def run_gui(model: Path | None = None, config: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.apps.paramesh._parameshw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        from fcmd.gui.compat import qt_import_failure

        raise SystemExit(qt_import_failure(exc)) from exc

    run_qt(model, config)


@fcmd.tool("parameshw", help="打开二维轴对称网格预览窗口（Qt）", description=_DESCRIPTION)
def launch_cmd(model_file: Path | None = None, config_file: Path | None = None) -> None:
    """打开预览窗口，可传入要直接加载的模型与网格配置。

    Parameters
    ----------
    model_file:
        启动时加载的 ``dynacalc-model/1`` YAML；省略时恢复上次文件或打开空窗口
    config_file:
        ``paramesh/1`` 网格配置；省略时使用上次配置或默认值
    """
    run_gui(model=model_file, config=config_file)


@fcmd.main("parameshw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体


if __name__ == "__main__":  # pragma: no cover
    main()
