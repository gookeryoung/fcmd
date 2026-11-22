"""pdfcryptw - PDF 批量加密 / 解密窗口版（Qt）。

选择目录后一键加密或解密其中的 PDF（可递归子目录），输出与源文件同目录：
加密写 ``<原名>.enc.pdf``，解密写 ``<原名>.dec.pdf``。处理在后台线程池执行，
进度与日志实时回传界面；目录、并发线程数与递归选项保存在
``${FCMD_HOME:-~/.fcmd}/ui/pdfcryptw.json``（密码不落盘）。

示例
----
    fcmd pdfcryptw        # 打开 PDF 加密解密窗口
    pdfcryptw             # 全局命令等价入口
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path

import fcmd
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _guiwindow
from fcmd.tools.pdf.pdfcrypt import DEFAULT_WORKERS

__all__ = [
    "DEFAULT_GEOMETRY",
    "DEFAULT_WORKERS",
    "MAX_WORKERS",
    "MIN_WORKERS",
    "PdfCryptSettings",
    "config_path",
    "load_settings",
    "parse_workers",
    "resolve_geometry",
    "save_settings",
    "valid_geometry",
]

_DESCRIPTION = (
    "PDF 批量加密解密窗口版（Qt）：选择目录、输入密码，一键加密或解密，"
    "支持递归子目录与并发线程；设置持久化到 ~/.fcmd/ui/pdfcryptw.json（不含密码）"
)

DEFAULT_GEOMETRY = "860x680+240+140"
MIN_WIDTH = 760
MIN_HEIGHT = 560
MIN_WORKERS = 1
MAX_WORKERS = 32


def config_path() -> Path:
    """返回配置文件路径：``${FCMD_HOME:-~/.fcmd}/ui/pdfcryptw.json``。"""
    return _guiconfig.config_path("pdfcryptw")


@dataclass
class PdfCryptSettings:
    """加密解密窗口的持久化设置（不保存密码）。"""

    directory: str = ""
    workers: int = DEFAULT_WORKERS
    recursive: bool = True
    geometry: str | None = None


valid_geometry = _guiconfig.valid_geometry


def parse_workers(value: int | str) -> int:
    """把界面输入解析为合法并发线程数（1-32）。

    Raises
    ------
    ValueError
        非整数或超出范围。
    """
    try:
        workers = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"并发线程数须为整数: {value!r}") from exc
    if not MIN_WORKERS <= workers <= MAX_WORKERS:
        raise ValueError(f"并发线程数须为 {MIN_WORKERS}-{MAX_WORKERS}: {workers}")
    return workers


def load_settings(path: Path | None = None) -> PdfCryptSettings:
    """读取设置文件；缺失、损坏或字段非法时逐项回退默认值。"""
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return PdfCryptSettings()

    settings = PdfCryptSettings()
    directory = raw.get("directory")
    workers = raw.get("workers")
    recursive = raw.get("recursive")
    geometry = raw.get("geometry")
    if isinstance(directory, str):
        settings.directory = directory
    if isinstance(workers, int) and not isinstance(workers, bool):
        with contextlib.suppress(ValueError):
            settings.workers = parse_workers(workers)
    if isinstance(recursive, bool):
        settings.recursive = recursive
    if isinstance(geometry, str) and valid_geometry(geometry):
        settings.geometry = geometry
    return settings


def save_settings(settings: PdfCryptSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    payload = {
        "directory": settings.directory,
        "workers": settings.workers,
        "recursive": settings.recursive,
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


def run_gui() -> None:
    """创建并运行 Qt 主窗口（高 DPI 与主题由 ``gui.app`` 统一处理）。"""
    try:
        from fcmd.tools.pdf._pdfcryptw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 安装完整时不会触发
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc

    run_qt()


@fcmd.tool("pdfcryptw", help="打开 PDF 加密解密窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 PDF 加密解密窗口。"""
    run_gui()


@fcmd.main("pdfcryptw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
