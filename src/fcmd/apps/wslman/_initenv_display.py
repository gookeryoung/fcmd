"""wslmanw「初始化环境」的显示依赖库与字体条目。

单独成模块：:mod:`fcmd.apps.wslman._initenv` 负责任务编排、探测与解析，行数已接近
800 行上限；这里集中放包名、库名、任务定义、状态文案与脚本片段，主模块只做拼装。

字体口径：默认只装 ``fonts-wqy-microhei``（下载 ≈1.8 MB），够显示常用中文；完整字形
（繁体 / 日韩 / 生僻字）与彩色 Emoji 拆成单独可选项，避免默认下载 57 MB 的 Noto CJK。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fcmd.apps.wslman._initenv_probe import EnvStatus

__all__ = [
    "CJK_FONT_PACKAGES",
    "DISPLAY_LIBS",
    "DISPLAY_PACKAGES",
    "EMOJI_FONT_PACKAGES",
    "NOTO_FONT_PACKAGES",
    "OPTIONAL_KEYS",
    "display_present",
    "display_status_rows",
    "display_summary_parts",
    "font_cache_script",
    "init_tasks",
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

CJK_FONT_PACKAGES: tuple[str, ...] = ("fontconfig", "fonts-wqy-microhei")
"""默认中文字体：文泉驿微米黑（下载 ≈1.8 MB），覆盖常用简体中文。"""

NOTO_FONT_PACKAGES: tuple[str, ...] = ("fontconfig", "fonts-noto-cjk")
"""完整中文字体（≈57 MB）：繁体 / 日韩 / 生僻字字形更全，作为可选项。"""

EMOJI_FONT_PACKAGES: tuple[str, ...] = ("fontconfig", "fonts-noto-color-emoji")
"""彩色 Emoji（≈10 MB）：与中文显示无关，作为可选项。"""

OPTIONAL_KEYS: frozenset[str] = frozenset({"noto", "emoji"})
"""可选字体项目：默认不勾选，避免首跑下载大包。"""


def init_tasks(
    *,
    apt_script: Callable[..., str],
    step: Callable[..., Any],
    task: Callable[..., Any],
) -> tuple[Any, ...]:
    """构造显示 / 字体相关的初始化任务（注入主模块的 ``InitStep`` / ``InitTask`` 与 apt 脚本生成器）。"""
    return (
        task(
            "gui",
            "安装显示依赖库",
            "补装 Qt / GTK 窗口在 WSLg 下显示所需的 EGL、XCB、Wayland 与字体配置库；"
            "PySide6 的 wheel 自带 Qt 库，但缺这些系统库时窗口会报「Qt 前端已安装但加载失败」。",
            (step("安装显示依赖库", apt_script(*DISPLAY_PACKAGES), root=True),),
        ),
        task(
            "fonts",
            "安装中文字体",
            "安装 fontconfig 与文泉驿微米黑（下载约 1.8 MB），并刷新系统字体缓存，"
            "避免窗口中文显示成方块；需要繁体 / 日韩 / 生僻字可另选「完整中文字体」。",
            (
                step("安装中文字体", apt_script(*CJK_FONT_PACKAGES), root=True),
                step("刷新字体缓存", font_cache_script(), root=True),
            ),
        ),
        task(
            "noto",
            "安装完整中文字体（Noto CJK）",
            "可选：安装 fonts-noto-cjk（下载约 57 MB，安装后约 91 MB），字形覆盖繁体 / 日韩 / "
            "生僻字，加载与字体缓存都比微米黑慢；只在需要时勾选。",
            (
                step("安装 Noto CJK", apt_script(*NOTO_FONT_PACKAGES), root=True),
                step("刷新字体缓存", font_cache_script(), root=True),
            ),
        ),
        task(
            "emoji",
            "安装彩色 Emoji",
            "可选：安装 fonts-noto-color-emoji（下载约 10 MB），让界面里的 Emoji 显示为彩色字形。",
            (
                step("安装彩色 Emoji", apt_script(*EMOJI_FONT_PACKAGES), root=True),
                step("刷新字体缓存", font_cache_script(), root=True),
            ),
        ),
    )


def font_cache_script() -> str:
    """刷新系统字体缓存并回显前几个中文字体。

    用 ``fc-cache -s``（系统目录、增量）而不是 ``fc-cache -f``：``-f`` 会把已缓存的
    全部字体重新扫一遍，WSL 上（尤其挂了 Windows 字体目录时）可能耗时几十秒；新装字体
    所在目录 mtime 已变，增量扫描会处理。以 root 执行，写共享的
    ``/var/cache/fontconfig``，不必每个用户再建一遍缓存。
    """
    return "\n".join(
        (
            "set -e",
            "if command -v fc-cache >/dev/null 2>&1; then",
            "    fc-cache -s >/dev/null 2>&1 || true",
            "fi",
            'echo "中文字体："',
            "fc-list :lang=zh family 2>/dev/null | head -3 || true",
        )
    )


def probe_lines() -> tuple[str, ...]:
    """探测显示库、中文字体、Noto CJK 与 Emoji 的行（``WSLMAN_*`` 前缀）。"""
    return (
        "libs_present=\" $(ldconfig -p 2>/dev/null | awk '{print $1}' | tr '\\n' ' ') \"",
        'gui_missing=""',
        f"for lib in {' '.join(DISPLAY_LIBS)}; do",
        '    case "$libs_present" in *" $lib "*) ;; *) gui_missing="$gui_missing $lib" ;; esac',
        "done",
        'gui_missing="${gui_missing# }"',
        'printf "WSLMAN_GUI_LIBS=%s\\n" "$([ -z "$gui_missing" ] && echo ok || echo "$gui_missing")"',
        'printf "WSLMAN_CJK_FONT=%s\\n" "$(fc-list :lang=zh family 2>/dev/null | head -1)"',
        'printf "WSLMAN_NOTO_CJK=%s\\n" "$(fc-list :lang=zh family 2>/dev/null | grep -qi "Noto.*CJK" && echo yes)"',
        'printf "WSLMAN_EMOJI_FONT=%s\\n" "$(fc-list : family 2>/dev/null | grep -qi "Noto Color Emoji" && echo yes)"',
    )


def display_present(key: str, status: EnvStatus) -> bool | None:
    """显示 / 字体项目的就绪判定；不属于本模块的 key 返回 ``None``。"""
    if key == "gui":
        return status.gui_libs == "ok"
    if key == "fonts":
        return bool(status.cjk_font)
    if key == "noto":
        return bool(status.noto_cjk)
    if key == "emoji":
        return bool(status.emoji_font)
    return None


def _gui_libs_value(value: str) -> str:
    """显示依赖库的状态文案：齐全 / 未检测 / 缺 N 个库（前缀若干库名）。"""
    if value == "ok":
        return "已就绪"
    if not value:
        return "未检测"
    missing = value.split()
    shown = " ".join(missing[:3]) + (" …" if len(missing) > 3 else "")
    return f"缺 {len(missing)} 个：{shown}"


def _optional_fonts_value(status: EnvStatus) -> str:
    noto = "Noto CJK ✓" if status.noto_cjk else "Noto CJK ✗"
    emoji = "Emoji ✓" if status.emoji_font else "Emoji ✗"
    return f"{noto} · {emoji}"


def display_status_rows(status: EnvStatus) -> tuple[tuple[str, str, bool], ...]:
    """显示 / 字体项目的状态行，供界面统一着色。"""
    return (
        ("显示依赖库", _gui_libs_value(status.gui_libs), status.gui_libs == "ok"),
        ("中文字体", status.cjk_font or "未安装", bool(status.cjk_font)),
        (
            "可选字体",
            _optional_fonts_value(status),
            bool(status.noto_cjk) and bool(status.emoji_font),
        ),
    )


def display_summary_parts(status: EnvStatus) -> tuple[str, ...]:
    """摘要行里的显示库 / 字体状态片段。"""
    return (
        f"显示库 {'✓' if status.gui_libs == 'ok' else '✗'}",
        f"字体 {'✓' if status.cjk_font else '✗'}",
    )
