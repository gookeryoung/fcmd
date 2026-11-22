"""Qt 图标库：内置线性图标，按主题色渲染成 ``QIcon``。

图标路径取自 `Lucide <https://lucide.dev>`_（ISC License）的 24×24 线性图标，
只保留项目实际用到的少量符号，避免为一个箭头引入完整图标包与运行时依赖：

- ``chevron-left`` / ``chevron-right``：侧边栏折叠 / 展开按钮；
- ``chevron-down`` / ``chevron-up``：列表行详情展开 / 收起；
- ``chevrons-left`` / ``chevrons-right``：更紧凑的双箭头（预留）；
- ``panel-left-close`` / ``panel-left-open``：带面板边框的折叠 / 展开（预留）。

除侧边栏页面图标外，还内置 ``refresh`` / ``download`` / ``upload`` / ``save`` /
``folder`` / ``search`` / ``trash`` / ``send`` / ``convert`` / ``export`` / ``disc``
 等按钮动作图标；:func:`icon_for_text` 按按钮文案推断图标，
``fcmd.gui.qt.widgets.button`` 默认调用它。

语义区分：``export`` 是「文件 + 向下箭头」（导出到文件）、``download`` 是「托盘 + 向下
箭头」（从网上下载）、``disc`` 是磁盘／盘位（WSL 镜像这类磁盘映像），
``images`` 是相册（图片）。三者不要互相借用，否则按钮 / 页面图标会表意错位。

配色跟随 ``QApplication`` 调色板的 ``WindowText``（:func:`default_color`），
``fcmd.gui.qt.theme.apply_theme`` 已把语义色写进同一份调色板，因此深浅主题下
都能保持对比度；调用方也可以显式传 ``color`` 覆盖。

模块本身不在导入时加载 Qt：:func:`svg` 是纯函数（CI 无 PySide6 可单测），
:func:`pixmap` / :func:`icon` 才延迟导入 ``QtSvg`` / ``QtGui``。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from PySide6.QtGui import QIcon, QPixmap

__all__ = [
    "BUTTON_ICON_KEYWORDS",
    "DEFAULT_COLOR",
    "ICON_BODIES",
    "ICON_SIZE",
    "default_color",
    "icon",
    "icon_for_text",
    "pixmap",
    "refresh_icons",
    "set_icon",
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
    # 页面 / 侧边栏
    "overview": (
        '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/>'
        '<rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>'
    ),
    "models": (
        '<path d="M11 21.73a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16V8a2 2 0 0 0-1-1.73l-7-4'
        'a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73z"/>'
        '<path d="M12 22V12"/><path d="m3.29 7 8.71 5 8.71-5"/><path d="m7.5 4.27 9 5.15"/>'
    ),
    "run": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "chat": '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "paint": (
        '<path d="m9.06 11.9 8.07-8.06a2.85 2.85 0 1 1 4.03 4.03l-8.06 8.08"/>'
        '<path d="M7.07 14.94c-1.66 0-3 1.35-3 3.02 0 1.33-2.5 1.52-2 2.02 1.08 1.1 2.49 2.02 4 2.02'
        ' 2.2 0 4-1.8 4-4.04a3.01 3.01 0 0 0-3-3.02z"/>'
    ),
    "images": (
        '<rect width="18" height="18" x="3" y="3" rx="2" ry="2"/><circle cx="9" cy="9" r="2"/>'
        '<path d="m21 15-3.09-3.09a2 2 0 0 0-2.83 0L6 21"/>'
    ),
    "distros": (
        '<path d="m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91'
        'a2 2 0 0 0 1.66 0l8.58-3.9a1 1 0 0 0 0-1.83Z"/>'
        '<path d="m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65"/>'
        '<path d="m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65"/>'
    ),
    "log": (
        '<path d="M15 12h-5"/><path d="M15 8h-5"/><path d="M19 17V5a2 2 0 0 0-2-2H4"/>'
        '<path d="M8 21h12a2 2 0 0 0 2-2v-1a1 1 0 0 0-1-1H11a1 1 0 0 0-1 1v1a2 2 0 1 1-4 0V5'
        'a2 2 0 1 0-4 0v2a1 1 0 0 0 1 1h3"/>'
    ),
    # 通用动作
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "refresh": ('<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'),
    "trash": (
        '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
        '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>'
    ),
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/><line x1="12" x2="12" y1="15" y2="3"/>'
    ),
    "upload": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="17 8 12 3 7 8"/><line x1="12" x2="12" y1="3" y2="15"/>'
    ),
    "save": (
        '<path d="M15.2 3a2 2 0 0 1 1.4.6l3.8 3.8a2 2 0 0 1 .6 1.4V19a2 2 0 0 1-2 2H5'
        'a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"/>'
        '<path d="M17 21v-7a1 1 0 0 0-1-1H8a1 1 0 0 0-1 1v7"/><path d="M7 3v4a1 1 0 0 0 1 1h7"/>'
    ),
    "export": (
        # Lucide ``file-down``：完整的文件轮廓 + 向下箭头（导出 = 落盘）。
        # 旧路径轮廓开口、只画出一条折线，渲染出来是个残缺的文件形。
        '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/>'
        '<path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
        '<path d="M12 18v-6"/><path d="m9 15 3 3 3-3"/>'
    ),
    "disc": (
        # Lucide ``hard-drive``：磁盘/盘位。给「镜像」这类磁盘映像页面用，
        # 不用照片图标（``images``）以免语义错位。
        '<line x1="22" x2="2" y1="12" y2="12"/>'
        '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89'
        'A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
        '<line x1="6" x2="6.01" y1="16" y2="16"/><line x1="10" x2="10.01" y1="16" y2="16"/>'
    ),
    "folder": (
        '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9'
        'A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'
    ),
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "scan": (
        '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/>'
        '<path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'
    ),
    "stop": '<rect width="18" height="18" x="3" y="3" rx="2"/>',
    "send": (
        '<path d="M14.54 21.69a.5.5 0 0 0 .94-.02l6.5-19a.5.5 0 0 0-.64-.64l-19 6.5'
        'a.5.5 0 0 0-.02.94l7.93 3.18a2 2 0 0 1 1.11 1.11z"/>'
        '<path d="m21.85 2.15-10.94 10.94"/>'
    ),
    "copy": (
        '<rect width="14" height="14" x="8" y="8" rx="2" ry="2"/>'
        '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
    ),
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "reset": ('<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/>'),
    "convert": (
        '<path d="M9.94 15.5A2 2 0 0 0 8.5 14.06l-6.14-1.58a.5.5 0 0 1 0-.96L8.5 9.94'
        "A2 2 0 0 0 9.94 8.5l1.58-6.14a.5.5 0 0 1 .96 0l1.58 6.14A2 2 0 0 0 15.5 9.94"
        "l6.14 1.58a.5.5 0 0 1 0 .96L15.5 14.06a2 2 0 0 0-1.44 1.44l-1.58 6.14"
        'a.5.5 0 0 1-.96 0z"/>'
    ),
}
"""图标名 → Lucide 图形片段（24×24 viewBox，描边由 :func:`svg` 统一补齐）。"""

BUTTON_ICON_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("新对话", "plus"),
    ("重新检测", "refresh"),
    ("重新扫描", "refresh"),
    ("重新加载", "refresh"),
    ("关闭全部", "stop"),
    ("刷新", "refresh"),
    ("保存", "save"),
    ("删除", "trash"),
    ("清空", "trash"),
    ("下载", "download"),
    ("导入", "upload"),
    ("导出", "export"),
    ("镜像", "disc"),
    ("打开", "folder"),
    ("浏览", "folder"),
    ("选择", "folder"),
    ("目录", "folder"),
    ("启动", "run"),
    ("运行", "run"),
    ("停止", "stop"),
    ("发送", "send"),
    ("搜索", "search"),
    ("扫描", "scan"),
    ("检测", "scan"),
    ("新建", "plus"),
    ("添加", "plus"),
    ("复制", "copy"),
    ("转换", "convert"),
    ("量化", "convert"),
    ("重置", "reset"),
    ("确定", "check"),
    ("已启动", "check"),
    ("已转换", "check"),
    ("应用", "check"),
)
"""按钮文案 → 图标名；按关键词长度降序匹配，``button()`` 用它自动挂图标。"""


def icon_for_text(text: str) -> str | None:
    """按按钮文案推断图标名；没有命中时返回 ``None``（保持纯文字按钮）。"""
    for keyword, name in sorted(BUTTON_ICON_KEYWORDS, key=lambda item: len(item[0]), reverse=True):
        if keyword in text:
            return name
    return None


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


ICON_NAME_PROPERTY = "fcmdIconName"
ICON_SIZE_PROPERTY = "fcmdIconSize"
ICON_TONE_PROPERTY = "fcmdIconTone"


def tone_color(tone: str) -> str:
    """主题语义色 → 十六进制颜色；未知 / 读取失败时回落正文色。

    ``tone`` 取 :class:`fcmd.gui.theme.Palette` 的字段名（``text`` / ``muted`` /
    ``accent`` / ``on_accent`` / ``danger`` 等），保证图标与按钮文字的配色一致：
    主操作按钮用 ``on_accent``，危险按钮用 ``danger``，次要按钮用 ``muted``。
    """
    try:
        from fcmd.gui.qt.theme import active_theme

        palette = active_theme().palette
    except Exception:  # pragma: no cover - 无 Qt / 主题未初始化
        return default_color()
    return str(getattr(palette, tone, palette.text))


def set_icon(widget: Any, name: str, *, size: int = ICON_SIZE, tone: str = "text") -> None:
    """给控件设置公共图标，并记住图标名 / 尺寸 / 语义色供主题切换时重绘。

    图标是位图，主题切换后必须重新生成；``tone`` 决定它与哪类文字配色一致
    （主按钮 ``on_accent``、危险按钮 ``danger``、次要按钮 ``muted``）。
    """
    from PySide6.QtCore import QSize

    widget.setIcon(icon(name, size=size, color=tone_color(tone)))
    widget.setIconSize(QSize(size, size))
    widget.setProperty(ICON_NAME_PROPERTY, name)
    widget.setProperty(ICON_SIZE_PROPERTY, int(size))
    widget.setProperty(ICON_TONE_PROPERTY, tone)


def refresh_icons() -> None:
    """按当前应用调色板重绘所有通过 :func:`set_icon` 挂上的图标。"""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for widget in app.allWidgets():
        name = widget.property(ICON_NAME_PROPERTY)
        if not isinstance(name, str) or not name:
            continue
        size = widget.property(ICON_SIZE_PROPERTY)
        tone = widget.property(ICON_TONE_PROPERTY)
        set_icon(
            widget,
            name,
            size=int(size) if isinstance(size, int) and size > 0 else ICON_SIZE,
            tone=tone if isinstance(tone, str) and tone else "text",
        )
