"""screenshot - 跨平台截图工具。

Windows 用 PowerShell + System.Drawing，macOS 用 screencapture，Linux 用 gnome-screenshot/scrot。

示例
----
    fcmd screenshot full                    # 全屏截图，自动命名
    fcmd screenshot full --filename x.png   # 全屏截图，指定文件名
    fcmd screenshot area                     # 区域截图（交互选择）
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import fcmd
from fcmd.models import run_command

__all__ = ["get_screenshot_path", "take_screenshot_area", "take_screenshot_full"]


def get_screenshot_path(filename: str | None = None) -> Path:
    """获取截图保存路径（默认 ``~/Pictures/screenshots/screenshot_<时间戳>.png``）。

    Parameters
    ----------
    filename:
        文件名，``None`` 时自动生成带时间戳的文件名

    Returns
    -------
    Path
        截图保存路径（父目录自动创建）
    """
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"screenshot_{timestamp}.png"
    screenshots_dir = Path.home() / "Pictures" / "screenshots"
    screenshots_dir.mkdir(parents=True, exist_ok=True)
    return screenshots_dir / filename


def _take_windows_screenshot(output_path: Path, *, area: bool) -> None:
    """Windows 平台截图（PowerShell + System.Drawing）。

    ``area=True`` 时退化为全屏截图（PowerShell 原生不支持交互区域选择，
    用户可改用 Win+Shift+S 等系统快捷键）。
    """
    del area  # Windows 下 area 与 full 脚本相同，参数保留以统一接口
    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$screen = [System.Windows.Forms.Screen]::PrimaryScreen
$bounds = $screen.Bounds
$bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
$bitmap.Save('{output_path.as_posix()}')
$graphics.Dispose()
$bitmap.Dispose()
"""
    run_command(["powershell", "-Command", ps_script])


def _take_macos_screenshot(output_path: Path, *, area: bool) -> None:
    """macOS 平台截图（screencapture）。"""
    cmd = ["screencapture", "-i", str(output_path)] if area else ["screencapture", "-x", str(output_path)]
    run_command(cmd)


def _take_linux_screenshot(output_path: Path, *, area: bool) -> None:
    """Linux 平台截图（gnome-screenshot 或 scrot）。"""
    # 优先 gnome-screenshot，失败时回退 scrot
    gnome_cmd = (
        ["gnome-screenshot", "-a", "-f", str(output_path)] if area else ["gnome-screenshot", "-f", str(output_path)]
    )
    result = run_command(gnome_cmd)
    if result.succeeded:
        return
    # 回退 scrot
    scrot_cmd = ["scrot", "-s", str(output_path)] if area else ["scrot", str(output_path)]
    run_command(scrot_cmd)


@fcmd.tool("screenshot", subcommand="full", help="全屏截图")
def take_screenshot_full(filename: str | None = None) -> None:
    """全屏截图，保存到 ``~/Pictures/screenshots/``。

    Parameters
    ----------
    filename:
        文件名（``None`` 时自动生成带时间戳的文件名）
    """
    output_path = get_screenshot_path(filename)
    if sys.platform == "win32":
        _take_windows_screenshot(output_path, area=False)
    elif sys.platform == "darwin":
        _take_macos_screenshot(output_path, area=False)
    else:
        _take_linux_screenshot(output_path, area=False)
    print(f"截图已保存: {output_path}")


@fcmd.tool("screenshot", subcommand="area", help="区域截图")
def take_screenshot_area(filename: str | None = None) -> None:
    """区域截图（交互选择区域），保存到 ``~/Pictures/screenshots/``。

    Windows 下退化为全屏截图（PowerShell 原生不支持交互区域选择）。

    Parameters
    ----------
    filename:
        文件名（``None`` 时自动生成带时间戳的文件名）
    """
    output_path = get_screenshot_path(filename)
    if sys.platform == "win32":
        _take_windows_screenshot(output_path, area=True)
    elif sys.platform == "darwin":
        _take_macos_screenshot(output_path, area=True)
    else:
        _take_linux_screenshot(output_path, area=True)
    print(f"截图已保存: {output_path}")
