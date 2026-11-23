"""wslmanw「初始化环境」的显示依赖库与中文字体条目数据。

单独成模块：:mod:`fcmd.apps.wslman._initenv` 负责任务定义、探测与解析，行数已接近
800 行上限；这里只放包名、库名与两段脚本，保持纯数据、无副作用。
"""

from __future__ import annotations

__all__ = [
    "CJK_FONT_PACKAGES",
    "DISPLAY_LIBS",
    "DISPLAY_PACKAGES",
    "font_cache_script",
    "probe_lines",
]

# WSLg 下跑 Qt / GTK 窗口所需的系统库：PySide6 的 wheel 自带 Qt 库，但这些仍由发行版提供；
# 缺任何一个都会让 ``import PySide6.QtWidgets`` 直接抛共享库加载错误。
DISPLAY_LIBS: tuple[str, ...] = (
    "libEGL.so.1",
    "libGL.so.1",
    "libfontconfig.so.1",
    "libdbus-1.so.3",
    "libxkbcommon-x11.so.0",
    "libxcb-cursor.so.0",
    "libxcb-icccm.so.4",
    "libxcb-image.so.0",
    "libxcb-keysyms.so.1",
    "libxcb-render-util.so.0",
    "libxcb-render.so.0",
    "libxcb-util.so.1",
    "libxcb-xinerama.so.0",
    "libxcb-randr.so.0",
    "libxcb-shape.so.0",
    "libxcb-xfixes.so.0",
    "libxcb-sync.so.1",
    "libxcb-shm.so.0",
    "libwayland-cursor.so.0",
)

DISPLAY_PACKAGES: tuple[str, ...] = (
    "libegl1",
    "libgl1",
    "libfontconfig1",
    "libdbus-1-3",
    "libxkbcommon-x11-0",
    "libxcb-cursor0",
    "libxcb-icccm4",
    "libxcb-image0",
    "libxcb-keysyms1",
    "libxcb-render-util0",
    "libxcb-render0",
    "libxcb-util1",
    "libxcb-xinerama0",
    "libxcb-randr0",
    "libxcb-shape0",
    "libxcb-xfixes0",
    "libxcb-sync1",
    "libxcb-shm0",
    "libwayland-cursor0",
)

CJK_FONT_PACKAGES: tuple[str, ...] = ("fontconfig", "fonts-noto-cjk", "fonts-noto-color-emoji")
"""中文字体与彩色 Emoji：缺字体时 Qt 窗口中文显示为方块。"""


def font_cache_script() -> str:
    """刷新字体缓存并回显前几个中文字体，便于在日志里确认结果。"""
    return "\n".join(
        (
            "set -e",
            "if command -v fc-cache >/dev/null 2>&1; then",
            "    fc-cache -f >/dev/null 2>&1 || true",
            "fi",
            'echo "中文字体："',
            "fc-list :lang=zh family 2>/dev/null | head -3 || true",
        )
    )


def probe_lines() -> tuple[str, ...]:
    """探测脚本里判定显示依赖库与中文字体的行；输出 ``WSLMAN_GUI_LIBS`` / ``WSLMAN_CJK_FONT``。"""
    return (
        "libs_present=\" $(ldconfig -p 2>/dev/null | awk '{print $1}' | tr '\\n' ' ') \"",
        'gui_missing=""',
        f"for lib in {' '.join(DISPLAY_LIBS)}; do",
        '    case "$libs_present" in *" $lib "*) ;; *) gui_missing="$gui_missing $lib" ;; esac',
        "done",
        'gui_missing="${gui_missing# }"',
        'printf "WSLMAN_GUI_LIBS=%s\\n" "$([ -z "$gui_missing" ] && echo ok || echo "$gui_missing")"',
        'printf "WSLMAN_CJK_FONT=%s\\n" "$(fc-list :lang=zh family 2>/dev/null | head -1)"',
    )
