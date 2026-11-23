"""docdiffw - 文档对比工具（Qt GUI）。

交互参考 ``bitool_office.docdiff``：选择旧 / 新 Word 文档，设置结果标题、
对比模式与是否显示修订，一键调用本机 Word 生成对比文档。对比在后台线程
执行，日志实时回传界面；设置（上次文件、输出、标题、模式与窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/docdiffw.json``，下次启动自动恢复。

依赖 Windows + Microsoft Word + ``pywin32``（``pip install fcmd[win32]``）。
与原工具一致，对比结束后会强制结束 ``WINWORD.EXE``，运行前请保存其他
正在编辑的 Word 文档。

示例
----
    fcmd docdiffw        # 打开文档对比窗口
    docdiffw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow
from fcmd.tools.office.docdiff import DEFAULT_TITLE, normalize_compare_mode

__all__ = [
    "DEFAULT_GEOMETRY",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "DocDiffSettings",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

_DESCRIPTION = (
    "文档对比工具窗口版（Qt）：选择旧/新 doc/docx 文档，设置标题、original/revised 对比模式与"
    "是否显示修订，调用本机 Word 生成对比文档；设置持久化到 ~/.fcmd/ui/docdiffw.json，"
    "依赖 Windows + Word + fcmd[win32]"
)

DEFAULT_GEOMETRY = "900x620+240+160"
MIN_WIDTH = 780
MIN_HEIGHT = 560


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/docdiffw.json``。"""
    return _guiconfig.config_path("docdiffw")


@dataclass
class DocDiffSettings:
    """文档对比窗口的持久化设置。"""

    old_doc: str = ""
    new_doc: str = ""
    output: str = ""
    title: str = DEFAULT_TITLE
    show_changes: bool = True
    compare_mode: str = "original"
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


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


def load_settings(path: Path | None = None) -> DocDiffSettings:
    """读取设置文件；文件缺失、JSON 损坏或字段类型不符时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return DocDiffSettings()

    settings = DocDiffSettings()
    old_doc = raw.get("old_doc")
    new_doc = raw.get("new_doc")
    output = raw.get("output")
    title = raw.get("title")
    compare_mode = raw.get("compare_mode")
    geometry = raw.get("geometry")
    if isinstance(old_doc, str):
        settings.old_doc = old_doc
    if isinstance(new_doc, str):
        settings.new_doc = new_doc
    if isinstance(output, str):
        settings.output = output
    if isinstance(title, str) and title:
        settings.title = title
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(compare_mode, str):
        with contextlib.suppress(ValueError):
            settings.compare_mode = normalize_compare_mode(compare_mode)
    settings.show_changes = raw.get("show_changes") is not False
    return settings


def save_settings(settings: DocDiffSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略（不影响窗口关闭）。"""
    payload = {
        "old_doc": settings.old_doc,
        "new_doc": settings.new_doc,
        "output": settings.output,
        "title": settings.title,
        "show_changes": settings.show_changes,
        "compare_mode": settings.compare_mode,
        "geometry": settings.geometry,
    }
    _guiconfig.write_settings(payload, path or config_path())


def run_gui() -> None:
    """创建并运行 Qt 主窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.tools.office._docdiffw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        from fcmd.gui.compat import qt_import_failure

        raise SystemExit(qt_import_failure(exc)) from exc

    run_qt()


@fcmd.tool("docdiffw", help="打开文档对比窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开文档对比窗口。"""
    run_gui()


@fcmd.main("docdiffw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
