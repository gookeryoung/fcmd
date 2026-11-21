"""Qt 图标库：内置线性图标，按主题色渲染成 ``QIcon``。

图标路径取自 `Lucide <https://lucide.dev>`_（ISC License）的 24×24 线性图标，
只保留项目实际用到的少量符号，避免为一个箭头引入完整图标包与运行时依赖：

- ``chevron-left`` / ``chevron-right``：侧边栏折叠 / 展开按钮；
- ``chevron-down`` / ``chevron-up``：列表行详情展开 / 收起；
- ``chevrons-left`` / ``chevrons-right``：更紧凑的双箭头（预留）；
- ``panel-left-close`` / ``panel-left-open``：带面板边框的折叠 / 展开（预留）。

配色跟随 ``QApplication`` 调色板的 ``WindowText``（:func:`default_color`），
``fcmd.gui.qt.theme.apply_theme`` 已把语义色写进同一份调色板，因此深浅主题下
都能保持对比度；调用方也可以显式传 ``color`` 覆盖。

模块本身不在导入时加载 Qt：:func:`svg` 是纯函数（CI 无 PySide6 可单测），
:func:`pixmap` / :func:`icon` 才延迟导入 ``QtSvg`` / ``QtGui``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon, QPixmap

__all__ = [
    "DEFAULT_COLOR",
    "ICON_BODIES",
    "ICON_SIZE",
    "default_color",
    "icon",
    "pixmap",
    "svg",
]

ICON_SIZE = 16
"""图标默认逻辑尺寸（像素）；比正文行高略小，避免行卡被图标撑高。"""

DEFAULT_COLOR = "#c8c8c8"
"""没有 ``QApplication`` 时的兜底色（测试 / 早期构造），与深色主题正文接近。"""

ICON_BODIES: dict[str, str] = {
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevrons-left": '<path d="m11 17-5-5 5-5"/><path d="m18 17-5-5 5-5"/>',
    "chevrons-right": '<path d="m6 17 5-5-5-5"/><path d="m13 17 5-5-5-5"/>',
    "panel-left-close": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="m16 15-3-3 3-3"/>',
    "panel-left-open": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/><path d="m14 9 3 3-3 3"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08'
        "a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74"
        "l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25"
        "a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25"
        "a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08"
        "a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38"
        'a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
}
"""图标名 → Lucide 图形片段（24×24 viewBox，描边由 :func:`svg` 统一补齐）。"""

_SVG_TEMPLATE = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
    'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round">{body}</svg>'
)


def default_color() -> str:
    """当前应用调色板的正文色；没有 ``QApplication`` 时用 :data:`DEFAULT_COLOR`。"""
    try:
        from PySide6.QtGui import QPalette
        from PySide6.QtWidgets import QApplication
    except ImportError:  # pragma: no cover - 无 Qt 环境
        return DEFAULT_COLOR
    app = QApplication.instance()
    if app is None:
        return DEFAULT_COLOR
    return app.palette().color(QPalette.ColorRole.WindowText).name()


def svg(name: str, *, color: str | None = None) -> str:
    """返回指定图标的完整 SVG 文本（纯函数，便于无 Qt 单测）。"""
    body = ICON_BODIES.get(name)
    if body is None:
        known = "、".join(sorted(ICON_BODIES))
        raise KeyError(f"未知图标 {name!r}；可选：{known}")
    return _SVG_TEMPLATE.format(color=color or default_color(), body=body)


def pixmap(name: str, *, size: int = ICON_SIZE, color: str | None = None) -> QPixmap:
    """把图标渲染成透明底 ``QPixmap``；按屏幕缩放比生成高 DPI 位图。"""
    from PySide6.QtCore import QByteArray, QRectF, Qt
    from PySide6.QtGui import QPainter, QPixmap
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(QByteArray(svg(name, color=color).encode("utf-8")))
    ratio = 1.0
    try:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            screen = app.primaryScreen()
            if screen is not None:
                ratio = max(1.0, float(screen.devicePixelRatio()))
    except ImportError:  # pragma: no cover - 无 QtWidgets
        ratio = 1.0
    canvas = QPixmap(round(size * ratio), round(size * ratio))
    canvas.setDevicePixelRatio(ratio)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    try:
        # 必须显式给目标矩形：无参 render() 按 SVG 自身 24×24 尺寸绘制，
        # 小图标（16px）只会画进左上角一块，看起来像被裁掉 / 偏移。
        renderer.render(painter, QRectF(0, 0, size, size))
    finally:
        painter.end()
    return canvas


def icon(name: str, *, size: int = ICON_SIZE, color: str | None = None) -> QIcon:
    """构造 ``QIcon``；``name`` 必须是 :data:`ICON_BODIES` 里登记过的图标。"""
    from PySide6.QtGui import QIcon

    return QIcon(pixmap(name, size=size, color=color))
