"""pevieww - PE 文件查看器（Qt 前端入口与设置）。

左侧按「DOS 头 / NT 头 / 可选头 / 数据目录 / 节表 / 导入 / 导出 / 资源 /
重定位 / 调试 / 签名 / Overlay」分类展示结构，右侧提供详情、概览与十六进制
三种视图；选中节点后十六进制视图自动跳到对应文件偏移。

窗口实现在 :mod:`fcmd.apps.peview._pevieww_qt`，本模块保留设置持久化与
纯逻辑（最近文件、geometry 恢复），设置保存在
``${FCMD_HOME:-~/.fcmd}/ui/pevieww.json``。

示例
----
    fcmd pevieww
    fcmd pevieww --file app.exe
    pevieww --file app.exe
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow
from fcmd.gui.settings import SettingsStore
from fcmd.gui.uistate import merge_recent, normalize_recent

__all__ = [
    "DEFAULT_GEOMETRY",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "PeviewSettings",
    "add_recent_file",
    "config_path",
    "load_settings",
    "resolve_geometry",
    "run_gui",
    "save_settings",
    "valid_geometry",
]

_DESCRIPTION = (
    "PE 查看器窗口版（Qt）：按分类浏览 DOS/NT/可选头、数据目录、节表、导入导出、资源、"
    "重定位、调试与签名，详情与十六进制联动；设置持久化到 ~/.fcmd/ui/pevieww.json"
)

DEFAULT_GEOMETRY = "1180x760+160+100"
MIN_WIDTH = 960
MIN_HEIGHT = 620
RECENT_LIMIT = 10


# ---------------------------------------------------------------------- #
# 配置持久化
# ---------------------------------------------------------------------- #
def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/pevieww.json``。"""
    return _guiconfig.config_path("pevieww")


@dataclass
class PeviewSettings:
    """PE 查看器的持久化设置。"""

    file_path: str = ""
    recent_files: list[str] = field(default_factory=list)
    last_open_dir: str = ""
    geometry: str | None = None
    splitter: tuple[int, int] | None = None


valid_geometry = _guiconfig.valid_geometry


def add_recent_file(files: list[str], path: Path, limit: int = RECENT_LIMIT) -> list[str]:
    """把路径插入最近列表头部，去重（Windows 大小写不敏感）并截断到 ``limit`` 条。"""
    return merge_recent(files, path, limit=limit)


def load_settings(path: Path | None = None) -> PeviewSettings:
    """读取设置文件；缺失、JSON 损坏或字段非法时逐项回退默认值。"""
    raw = SettingsStore("pevieww", path).load()
    if raw is None:
        return PeviewSettings()
    settings = PeviewSettings()
    file_path = raw.get("file_path")
    recent = raw.get("recent_files")
    last_open_dir = raw.get("last_open_dir")
    geometry = raw.get("geometry")
    splitter = raw.get("splitter")
    if isinstance(file_path, str):
        settings.file_path = file_path
    if isinstance(recent, list):
        settings.recent_files = normalize_recent(recent, limit=RECENT_LIMIT)
    if isinstance(last_open_dir, str):
        settings.last_open_dir = last_open_dir
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    if isinstance(splitter, list) and len(splitter) == 2 and all(isinstance(item, int) for item in splitter):
        settings.splitter = (splitter[0], splitter[1])
    return settings


def save_settings(settings: PeviewSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "file_path": settings.file_path,
        "recent_files": settings.recent_files,
        "last_open_dir": settings.last_open_dir,
        "geometry": settings.geometry,
        "splitter": list(settings.splitter) if settings.splitter is not None else None,
    }
    SettingsStore("pevieww", path).save(payload)


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆的位置，越界/异常时回落到默认位置与尺寸。"""
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
def run_gui(file: Path | None = None) -> None:
    """启动 Qt 窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.apps.peview._pevieww_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        from fcmd.gui.compat import qt_import_failure

        raise SystemExit(qt_import_failure(exc)) from exc

    run_qt(file)


@fcmd.tool("pevieww", help="打开 PE 查看器窗口（Qt）", description=_DESCRIPTION)
def launch_cmd(file: Path | None = None) -> None:
    """打开 PE 查看器窗口，可传入要直接加载的文件。

    Parameters
    ----------
    file:
        启动时打开的 PE 文件路径；省略时打开空窗口或恢复上次文件
    """
    run_gui(file=file)


@fcmd.main("pevieww")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
