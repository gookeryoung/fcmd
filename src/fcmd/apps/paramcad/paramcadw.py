"""paramcadw - 参数化建模与几何预览（Qt）。

界面只负责取数与绘制，几何采样全部来自 :mod:`fcmd.apps.paramcad._preview`，
因此没有显示器时也能单测纯逻辑部分；窗口复用 ``gui`` 的主题与设置持久化。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui.settings import SettingsStore

__all__ = ["CanvasMap", "Settings", "load_settings", "run_gui", "save_settings"]

_DESCRIPTION = "参数化建模预览（Qt）：二维截面、轴向剖面与密度 / 壁厚分布曲线；数据可导出为 dynacalc-model/1 模型描述"

DEFAULT_GEOMETRY = "1120x720"
MIN_WIDTH = 900
MIN_HEIGHT = 600
SETTINGS_NAME = "paramcad"


@dataclass(frozen=True, slots=True)
class Settings:
    """窗口设置：几何尺寸、上次打开的模型路径与分栏比例。"""

    geometry: str | None = None
    model_path: str = ""
    splitter: tuple[int, int] | None = None


def settings_path() -> Path:
    """配置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/paramcad.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> Settings:
    """读取设置；文件缺失或损坏时回落默认值。"""
    raw = SettingsStore("paramcadw", path or settings_path()).load()
    if raw is None:
        return Settings()
    model_path = raw.get("model_path")
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
        splitter=splitter,
    )


def save_settings(settings: Settings, path: Path | None = None) -> None:
    """写入设置；失败静默（关闭窗口不应因落盘失败中断）。"""
    SettingsStore("paramcadw", path or settings_path()).save(
        {
            "geometry": settings.geometry,
            "model_path": settings.model_path,
            "splitter": list(settings.splitter) if settings.splitter is not None else None,
        },
    )


@dataclass(frozen=True, slots=True)
class CanvasMap:
    """把毫米坐标映射到画布像素（等比缩放，居中留白）。"""

    width: float
    height: float
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    padding: float = 18.0

    def to_pixel(self, x: float, y: float) -> tuple[float, float]:
        """毫米坐标 → 画布像素（y 轴向上为正）。"""
        span_x = max(self.max_x - self.min_x, 1e-9)
        span_y = max(self.max_y - self.min_y, 1e-9)
        usable_width = max(self.width - 2 * self.padding, 1.0)
        usable_height = max(self.height - 2 * self.padding, 1.0)
        scale = min(usable_width / span_x, usable_height / span_y)
        center_x = self.width / 2.0
        center_y = self.height / 2.0
        mid_x = (self.min_x + self.max_x) / 2.0
        mid_y = (self.min_y + self.max_y) / 2.0
        return center_x + (x - mid_x) * scale, center_y - (y - mid_y) * scale

    def polyline(self, points: Sequence[tuple[float, float]]) -> list[float]:
        """折线像素坐标扁平列表（x0, y0, x1, y1, …），供绘制层消费。"""
        rows: list[float] = []
        for x, y in points:
            pixel_x, pixel_y = self.to_pixel(x, y)
            rows.extend((pixel_x, pixel_y))
        return rows


def run_gui(model: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.apps.paramcad._paramcadw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt(model)


@fcmd.tool("paramcadw", help="打开参数化建模预览窗口（Qt）", description=_DESCRIPTION)
def launch_cmd(model_file: Path | None = None) -> None:
    """打开预览窗口，可传入要直接加载的模型文件。

    Parameters
    ----------
    model_file:
        启动时加载的 ``dynacalc-model/1`` YAML；省略时恢复上次文件或打开空窗口
    """
    run_gui(model=model_file)


@fcmd.main("paramcadw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体


if __name__ == "__main__":  # pragma: no cover
    main()
