"""pdfsplitw - PDF 分割 / 页面提取窗口版（Qt 前端入口与设置）。

选择 PDF 后按三种模式之一分割：按份数平均分割、按每份页数切分、按页码范围
提取。窗口实现在 :mod:`fcmd.tools.pdf._pdfsplitw_qt`，本模块保留设置持久化
与参数校验；输入 / 输出路径、模式与参数保存在
``${FCMD_HOME:-~/.fcmd}/ui/pdfsplitw.json``。

示例
----
    fcmd pdfsplitw        # 打开 PDF 分割窗口
    pdfsplitw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import window as _guiwindow

__all__ = [
    "DEFAULT_GEOMETRY",
    "MODES",
    "MODE_LABELS",
    "PdfSplitSettings",
    "config_path",
    "default_output_path",
    "load_settings",
    "normalize_mode",
    "parse_positive_int",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "PDF 分割窗口版（Qt）：按份数 / 每份页数 / 页码范围三种模式分割或提取页面，"
    "进度与日志实时回传；设置持久化到 ~/.fcmd/ui/pdfsplitw.json"
)

DEFAULT_GEOMETRY = "880x700+240+130"
MIN_WIDTH = 780
MIN_HEIGHT = 580

MODES: tuple[str, ...] = ("parts", "size", "range")
MODE_LABELS: dict[str, str] = {
    "parts": "按份数",
    "size": "每份页数",
    "range": "页码范围",
}


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/pdfsplitw.json``。"""
    return _guiconfig.config_path("pdfsplitw")


@dataclass
class PdfSplitSettings:
    """PDF 分割窗口的持久化设置。"""

    input_path: str = ""
    output_dir: str = ""
    output_path: str = ""
    mode: str = "parts"
    parts: int = 2
    size: int = 1
    pages: str = ""
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def normalize_mode(value: str) -> str:
    """校验模式标识（``parts`` / ``size`` / ``range``）。

    Raises
    ------
    ValueError
        模式不在支持范围内。
    """
    key = value.strip().lower()
    if key not in MODES:
        raise ValueError(f"无效的分割模式: {value!r}（支持 {', '.join(MODES)}）")
    return key


def parse_positive_int(value: int | str, label: str) -> int:
    """把界面输入解析为正整数。

    Raises
    ------
    ValueError
        非整数或小于 1。
    """
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label}须为整数: {value!r}") from exc
    if number < 1:
        raise ValueError(f"{label}须为正整数: {number}")
    return number


def default_output_path(input_path: Path) -> Path:
    """页码范围模式的缺省输出：``<原名>_extracted.pdf``。"""
    return input_path.with_name(f"{input_path.stem}_extracted.pdf")


def load_settings(path: Path | None = None) -> PdfSplitSettings:
    """读取设置文件；缺失、损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return PdfSplitSettings()

    settings = PdfSplitSettings()
    input_path = raw.get("input_path")
    output_dir = raw.get("output_dir")
    output_path = raw.get("output_path")
    mode = raw.get("mode")
    parts = raw.get("parts")
    size = raw.get("size")
    pages = raw.get("pages")
    geometry = raw.get("geometry")
    if isinstance(input_path, str):
        settings.input_path = input_path
    if isinstance(output_dir, str):
        settings.output_dir = output_dir
    if isinstance(output_path, str):
        settings.output_path = output_path
    if isinstance(mode, str):
        with contextlib.suppress(ValueError):
            settings.mode = normalize_mode(mode)
    if isinstance(parts, int) and not isinstance(parts, bool) and parts >= 1:
        settings.parts = parts
    if isinstance(size, int) and not isinstance(size, bool) and size >= 1:
        settings.size = size
    if isinstance(pages, str):
        settings.pages = pages
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    return settings


def save_settings(settings: PdfSplitSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "input_path": settings.input_path,
        "output_dir": settings.output_dir,
        "output_path": settings.output_path,
        "mode": settings.mode,
        "parts": settings.parts,
        "size": settings.size,
        "pages": settings.pages,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认 geometry（按屏幕夹紧）。"""
    return _guiwindow.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


# ---------------------------------------------------------------------- #
# 入口
# ---------------------------------------------------------------------- #
def run_gui() -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.qt.app`` 统一处理）。"""
    try:
        from fcmd.tools.pdf._pdfsplitw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt()


@fcmd.tool("pdfsplitw", help="打开 PDF 分割窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PDF 分割窗口。"""
    run_gui()


@fcmd.main("pdfsplitw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
