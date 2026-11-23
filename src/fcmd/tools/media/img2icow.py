"""img2icow - 图像转 ICO 转换器（Qt GUI）。

交互参考 ``bitool_gui.img2ico``：选择单张图像或整个目录，填写逗号分隔的
图标尺寸与质量档位，一键生成多尺寸 Windows ICO。输入同时支持 SVG 与常见
位图（PNG/JPG/GIF/BMP/WebP/TIFF），图像按原始宽高比缩放并居中放入透明
方形画布；目录输入默认输出到 ``ico_output/``，单文件缺省输出到同目录。

设置（上次输入/输出路径、尺寸、质量、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/img2icow.json``，下次启动自动恢复。

示例
----
    fcmd img2icow         # 打开图像转 ICO 转换器窗口
    img2icow              # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow
from fcmd.infras import media

__all__ = [
    "DEFAULT_SIZES_TEXT",
    "QUALITY_CHOICES",
    "Img2IcoSettings",
    "config_path",
    "load_settings",
    "normalize_quality",
    "parse_sizes_text",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

_DESCRIPTION = (
    "图像转 ICO 转换器窗口版（Qt）：支持 SVG/PNG/JPG/GIF/BMP/WebP/TIFF 单文件或目录批量转换，"
    "自定义多尺寸与质量档位，自动保持宽高比并居中透明填充；"
    "设置持久化到 ~/.fcmd/ui/img2icow.json"
)

DEFAULT_SIZES_TEXT = "16,24,32,48,64,128,256"
QUALITY_CHOICES: tuple[str, ...] = ("low", "medium", "high")
DEFAULT_QUALITY = "high"
DEFAULT_GEOMETRY = "880x640+260+160"
MIN_WIDTH = 760
MIN_HEIGHT = 560


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/img2icow.json``。"""
    return _guiconfig.config_path("img2icow")


@dataclass
class Img2IcoSettings:
    """图像转 ICO 转换器的持久化设置。"""

    input_path: str = ""
    output_path: str = ""
    sizes: str = DEFAULT_SIZES_TEXT
    quality: str = DEFAULT_QUALITY
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def parse_sizes_text(text: str) -> list[int]:
    """解析逗号/空格分隔的尺寸文本（复用 CLI 的尺寸解析规则）。

    Raises
    ------
    ValueError
        未填写尺寸、包含非整数或非正数。
    """
    from fcmd.infras.media import parse_size_tokens

    if not text.strip():
        raise ValueError("请输入图标尺寸，例如 16,32,48,128,256")
    sizes = parse_size_tokens([text])
    if not sizes:
        raise ValueError("请输入图标尺寸，例如 16,32,48,128,256")
    return sizes


def normalize_quality(name: str) -> str:
    """把质量档位规范为 ``low`` / ``medium`` / ``high``（忽略大小写与空白）。"""
    return media.normalize_quality(name)


def load_settings(path: Path | None = None) -> Img2IcoSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return Img2IcoSettings()

    settings = Img2IcoSettings()
    input_path = raw.get("input_path")
    output_path = raw.get("output_path")
    sizes = raw.get("sizes")
    quality = raw.get("quality")
    geometry = raw.get("geometry")
    if isinstance(input_path, str):
        settings.input_path = input_path
    if isinstance(output_path, str):
        settings.output_path = output_path
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(sizes, str) and sizes.strip():
        # 校验通过才保留，非法尺寸文本回退默认值
        with contextlib.suppress(ValueError):
            parse_sizes_text(sizes)
            settings.sizes = sizes
    if isinstance(quality, str):
        with contextlib.suppress(ValueError):
            settings.quality = normalize_quality(quality)
    return settings


def save_settings(settings: Img2IcoSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "input_path": settings.input_path,
        "output_path": settings.output_path,
        "sizes": settings.sizes,
        "quality": settings.quality,
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
    """创建并运行 Qt 主窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.tools.media._img2icow_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        from fcmd.gui.compat import qt_import_failure

        raise SystemExit(qt_import_failure(exc)) from exc

    run_qt()


@fcmd.tool("img2icow", help="打开图像转 ICO 转换器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开图像转 ICO 转换器窗口。"""
    run_gui()


@fcmd.main("img2icow")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
