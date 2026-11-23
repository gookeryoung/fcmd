"""``fcmd.gui.icons`` 的测试：SVG 生成、未知图标报错与 Qt 渲染。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

from fcmd.gui import icons
from fcmd.gui import style as _qt_theme
from fcmd.gui import theme as _guitheme
from fcmd.gui.widgets import button


def test_svg_uses_current_color_and_known_body() -> None:
    text = icons.svg("chevron-down", color="#123456")
    assert text.startswith("<svg")
    assert 'stroke="#123456"' in text
    assert 'd="m6 9 6 6 6-6"' in text
    # 未传 color 时用默认色（无 QApplication 时为 DEFAULT_COLOR，有调色板时为正文色）。
    assert "stroke=" in icons.svg("chevron-right")


def test_unknown_icon_lists_known_names() -> None:
    with pytest.raises(KeyError) as excinfo:
        icons.svg("no-such-icon")
    message = str(excinfo.value)
    assert "no-such-icon" in message
    assert "chevron-down" in message


def test_pixmap_and_icon_render() -> None:
    QApplication.instance() or QApplication([])
    image = icons.pixmap("chevron-down", size=16)
    assert not image.isNull()
    assert image.width() >= 16
    symbol = icons.icon("chevron-left")
    assert not symbol.isNull()
    assert not symbol.pixmap(16, 16).isNull()


def test_icon_pixels_are_centered_and_not_clipped() -> None:
    """回归：SVG 必须按目标矩形缩放；无参 render() 会把 24×24 画进左上角被裁掉。"""
    QApplication.instance() or QApplication([])
    image = icons.pixmap("chevron-down", size=64, color="#ffffff").toImage()
    xs: list[int] = []
    ys: list[int] = []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 0:
                xs.append(x)
                ys.append(y)
    assert xs and ys
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    assert right - left > image.width() * 0.4
    assert bottom - top > image.height() * 0.2
    assert abs((left + right) / 2 - image.width() / 2) < image.width() * 0.1
    assert abs((top + bottom) / 2 - image.height() / 2) < image.height() * 0.1


def test_icon_for_text_maps_common_actions() -> None:
    """按钮文案到公共图标的映射：常见动作自动命中，未知文案保持纯文字。"""
    assert icons.icon_for_text("刷新") == "refresh"
    assert icons.icon_for_text("重新检测") == "refresh"
    assert icons.icon_for_text("开始转换") == "convert"
    assert icons.icon_for_text("＋ 新对话") == "plus"
    assert icons.icon_for_text("启动服务") == "run"
    assert icons.icon_for_text("导出") == "export"
    assert icons.icon_for_text("导出报告…") == "export"
    # 「下载选中镜像」优先命中的是下载；只有镜像语境才用磁盘图标
    assert icons.icon_for_text("下载选中镜像") == "download"
    assert icons.icon_for_text("安装本地镜像…") == "disc"
    # 实例管理补充的动作图标：重启沿用修复的 reset，关闭全部用 power，
    # 修复注册改用扳手。
    assert icons.icon_for_text("重启") == "reset"
    assert icons.icon_for_text("修复注册") == "wrench"
    assert icons.icon_for_text("配置") == "settings"
    assert icons.icon_for_text("打开终端") == "terminal"
    assert icons.icon_for_text("重命名") == "pencil"
    assert icons.icon_for_text("迁移所选文件") == "transfer"
    assert icons.icon_for_text("取消") == "x"
    assert icons.icon_for_text("关闭") == "x"
    assert icons.icon_for_text("关闭全部") == "power"
    assert icons.icon_for_text("关于") is None


def test_distro_icon_matches_common_distributions_with_generic_fallback() -> None:
    """发行版图标：常见发行版按名称命中，识别不出的回落通用图标。"""
    assert icons.distro_icon("Ubuntu-24.04") == "distro-ubuntu"
    assert icons.distro_icon("ubuntu") == "distro-ubuntu"
    assert icons.distro_icon("Debian") == "distro-debian"
    assert icons.distro_icon("kali-linux") == "distro-kali"
    assert icons.distro_icon("Alpine") == "distro-alpine"
    assert icons.distro_icon("openSUSE-Tumbleweed") == "distro-opensuse"
    assert icons.distro_icon("archlinux") == "distro-arch"
    assert icons.distro_icon("FedoraLinux-42") == "distro-fedora"
    # 兜底不为空：列表里不会出现空图标 / 问号。
    assert icons.distro_icon("SomeRandomDistro") == "distro"
    assert icons.distro_icon("") == "distro"
    for name in ("distro", "distro-ubuntu", "distro-debian", "distro-kali", "distro-alpine", "distro-opensuse"):
        assert name in icons.ICON_BODIES
        assert icons.pixmap(name, size=16).isNull() is False


def test_set_icon_can_render_on_labels() -> None:
    """``set_icon`` 也支持没有 ``setIcon`` 的控件（发行版图标挂在 QLabel 上）。"""
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    label = QLabel()
    try:
        icons.set_icon(label, "distro-ubuntu", tone="accent")
        assert label.property("fcmdIconName") == "distro-ubuntu"
        assert label.pixmap().isNull() is False
        icons.refresh_icons()
        assert label.pixmap().isNull() is False
    finally:
        label.deleteLater()


def _first_opaque_color(widget: QPushButton) -> str:
    image = widget.icon().pixmap(16, 16).toImage()
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() == 255:
                return color.name()
    return ""


def test_set_icon_recolors_when_theme_changes() -> None:
    """图标按调色板着色：切主题后 refresh_icons 要重绘，避免深底深色看不清。"""
    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    widget = QPushButton()
    try:
        icons.set_icon(widget, "refresh")
        assert widget.property("fcmdIconName") == "refresh"
        assert widget.iconSize().width() == 16
        dark = _first_opaque_color(widget)
        assert dark

        _qt_theme.apply_theme(app, _guitheme.resolve_theme("light"))
        light = _first_opaque_color(widget)
        assert light
        assert light != dark

        _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
        assert _first_opaque_color(widget) == dark
    finally:
        widget.deleteLater()
        icons.refresh_icons()


def test_button_icons_follow_variant_tones() -> None:
    """图标与按钮文字同色：主按钮 on_accent、危险按钮 danger、次要按钮 muted。"""
    from fcmd.gui.style import active_theme

    app = QApplication.instance() or QApplication([])
    _qt_theme.apply_theme(app, _guitheme.resolve_theme("dark-blue"))
    palette = active_theme().palette
    primary = button("启动服务", kind="primary")
    danger = button("删除", kind="danger")
    ghost = button("刷新", kind="ghost")
    try:
        assert _first_opaque_color(primary) == palette.on_accent
        assert _first_opaque_color(danger) == palette.danger
        assert _first_opaque_color(ghost) == palette.muted
    finally:
        for widget in (primary, danger, ghost):
            widget.deleteLater()


def test_export_icon_is_a_complete_file_shape() -> None:
    """导出图标必须是「闭合文件轮廓 + 向下箭头」。

    回归：旧路径只画了一条开口折线（``M4 7V4…``），渲染出来是个残缺图形，
    界面上「导出」按钮的图标看起来像坏图。
    """
    body = icons.ICON_BODIES["export"]
    # 文件轮廓从左下起笔并闭合，右侧带折角
    assert "M15 2H6" in body
    assert body.count("Z") == 1
    assert 'd="M14 2v4a2 2 0 0 0 2 2h4"' in body
    # 向下的导出箭头
    assert "M12 18v-6" in body
    assert "m9 15 3 3 3-3" in body


def test_disc_icon_exists_for_disk_image_pages() -> None:
    """「镜像」（磁盘映像）用 hard-drive 图标，不再借用照片图标 ``images``。"""
    assert "disc" in icons.ICON_BODIES
    QApplication.instance() or QApplication([])
    image = icons.pixmap("disc", size=16)
    assert not image.isNull()

    from fcmd.apps.wslman._wslmanw_qt import PAGES as WSL_PAGES

    page_icons = {key: icon for key, _label, _hint, icon in WSL_PAGES}
    assert page_icons["images"] == "disc"


def test_page_icons_are_all_registered() -> None:
    """侧边栏页面图标必须在 ``ICON_BODIES`` 里登记，避免运行期 KeyError / 空图标。"""
    from fcmd.apps.llm._llmw_qt_window import PAGES as LLM_PAGES
    from fcmd.apps.wslman._wslmanw_qt import PAGES as WSL_PAGES

    for key, _label, _hint, icon in (*LLM_PAGES, *WSL_PAGES):
        assert icon in icons.ICON_BODIES, f"页面 {key} 的图标 {icon!r} 未登记"
