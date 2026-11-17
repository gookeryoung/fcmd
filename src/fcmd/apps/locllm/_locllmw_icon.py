"""locllmw 图标资源：``assets/icons`` 下的多尺寸 PNG 与 ``.ico``。

图标同时用于窗口标题栏 / 任务栏、侧边栏标题与「关于」对话框。多尺寸 PNG
覆盖 Qt 在各平台、各缩放比下的取图需求；Windows 任务栏与快捷方式用
``locllmw.ico``（内含同样的尺寸）。资源路径相对本模块，打包进 wheel 后
仍可直接定位。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon

__all__ = ["ICON_DIR", "PNG_SIZES", "ico_path", "png_path", "window_icon"]

ICON_DIR = Path(__file__).with_name("assets") / "icons"

PNG_SIZES: tuple[int, ...] = (16, 24, 32, 48, 64, 128, 256, 512)
"""内置 PNG 尺寸；``assets/icons/locllmw-<size>.png``。"""


def png_path(size: int) -> Path:
    """返回指定尺寸的 PNG 路径（不保证存在）。"""
    return ICON_DIR / f"locllmw-{size}.png"


def ico_path() -> Path:
    """返回多尺寸 ``.ico`` 路径（Windows 任务栏 / Tk 回退窗口使用）。"""
    return ICON_DIR / "locllmw.ico"


def window_icon() -> QIcon:  # pragma: no cover - 需要 QtGui
    """构造窗口图标：按尺寸加入 PNG，再补一个多尺寸 ``.ico``。

    PNG 内置在 QtGui（跨平台可用）；ICO 需要 Qt 的 ico 插件，若加载失败
    （例如裁剪过的 Qt 安装）仍可退化为 PNG 集合，不影响窗口图标显示。
    """
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    icon = QIcon()
    for size in PNG_SIZES:
        path = png_path(size)
        if path.is_file():
            icon.addFile(str(path), QSize(size, size))
    ico = ico_path()
    if ico.is_file():
        icon.addFile(str(ico))
    return icon
