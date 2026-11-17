"""img2pdfw - 图片转 PDF 转换器（Qt GUI）。

交互参考 ``bitool_gui.img2pdf``：选择图片文件或目录，设置页面尺寸、DPI
与 EXIF 方向校正，一键合并为 PDF。目录输入按文件名顺序排版并默认输出
``<目录名>.pdf``，单文件输入默认输出同名 PDF。转换在后台线程执行，进度
与日志实时回传界面；设置（上次输入/输出、页面尺寸、DPI、窗口位置）保存
在 ``${FCMD_HOME:-~/.fcmd}/ui/img2pdfw.json``，下次启动自动恢复。

示例
----
    fcmd img2pdfw        # 打开图片转 PDF 转换器窗口
    img2pdfw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import window as _guiwindow

__all__ = [
    "DEFAULT_DPI",
    "DEFAULT_GEOMETRY",
    "DEFAULT_PAGE_SIZE",
    "PAGE_SIZE_OPTIONS",
    "Img2PdfSettings",
    "config_path",
    "default_output_path",
    "load_settings",
    "parse_dpi",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

__version__ = "0.1.0"

_DESCRIPTION = (
    "图片转 PDF 转换器窗口版（Qt）：支持图片文件或目录批量、页面尺寸（A3/A4/A5/B5/letter/legal）"
    "与 DPI 设置、EXIF 方向校正；设置持久化到 ~/.fcmd/ui/img2pdfw.json"
)

PAGE_SIZE_OPTIONS: tuple[str, ...] = ("auto", "A4", "A3", "A5", "B5", "letter", "legal")
DEFAULT_PAGE_SIZE = "auto"
DEFAULT_DPI = 150
DEFAULT_GEOMETRY = "900x680+240+140"
MIN_WIDTH = 780
MIN_HEIGHT = 580


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/img2pdfw.json``。"""
    return _guiconfig.config_path("img2pdfw")


@dataclass
class Img2PdfSettings:
    """图片转 PDF 转换器的持久化设置。"""

    input_path: str = ""
    output_path: str = ""
    page_size: str = DEFAULT_PAGE_SIZE
    dpi: int = DEFAULT_DPI
    normalize: bool = True
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def normalize_page(value: str) -> str:
    """校验并规范化页面尺寸（复用 CLI 的取值规则）。

    Raises
    ------
    ValueError
        取值不在支持列表中。
    """
    from fcmd.tools.pdf.img2pdf import normalize_page_size

    return normalize_page_size(value)


def format_page_size(value: str) -> str:
    """把规范化页面键转成界面显示值（``A4`` / ``auto`` / ``letter``）。"""
    key = normalize_page(value)
    return key if key in {"auto", "letter", "legal"} else key.upper()


def parse_dpi(value: int | str) -> int:
    """把界面输入解析为合法 DPI（36-1200）。

    Raises
    ------
    ValueError
        非整数或超出范围。
    """
    from fcmd.tools.pdf.img2pdf import normalize_dpi

    try:
        dpi = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"DPI 须为整数: {value!r}") from exc
    return normalize_dpi(dpi)


def default_output_path(input_path: Path) -> Path:
    """按输入类型给出缺省输出：目录 → ``<目录名>.pdf``，文件 → 同名 ``.pdf``。"""
    if input_path.is_dir():
        return input_path / f"{input_path.name}.pdf"
    return input_path.with_suffix(".pdf")


def load_settings(path: Path | None = None) -> Img2PdfSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return Img2PdfSettings()

    settings = Img2PdfSettings()
    input_path = raw.get("input_path")
    output_path = raw.get("output_path")
    page_size = raw.get("page_size")
    dpi = raw.get("dpi")
    normalize = raw.get("normalize")
    geometry = raw.get("geometry")
    if isinstance(input_path, str):
        settings.input_path = input_path
    if isinstance(output_path, str):
        settings.output_path = output_path
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(page_size, str):
        with contextlib.suppress(ValueError):
            settings.page_size = normalize_page(page_size)
    if isinstance(dpi, int) and not isinstance(dpi, bool):
        with contextlib.suppress(ValueError):
            settings.dpi = parse_dpi(dpi)
    if isinstance(normalize, bool):
        settings.normalize = normalize
    return settings


def save_settings(settings: Img2PdfSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "input_path": settings.input_path,
        "output_path": settings.output_path,
        "page_size": settings.page_size,
        "dpi": settings.dpi,
        "normalize": settings.normalize,
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


def run_gui() -> None:
    """创建并运行 Qt 主窗口（高 DPI 与主题由 ``gui.qt.app`` 统一处理）。"""
    try:
        from fcmd.tools.pdf._img2pdfw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt()


@fcmd.tool("img2pdfw", help="打开图片转 PDF 转换器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开图片转 PDF 转换器窗口。"""
    run_gui()


@fcmd.main("img2pdfw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
