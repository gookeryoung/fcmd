"""wslmanw - WSL 发行版管理的 Qt 窗口。

窗口只做编排：业务判断仍走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出核心、预检、注销前备份），
避免 GUI 与 CLI 两套口径。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``。
"""

from __future__ import annotations

import ntpath
import platform
import unicodedata
import urllib.parse
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.wslman import _images, _mirrors
from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._distro import Distro, state_label
from fcmd.apps.wslman._errors import WslmanError
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _geometry

__all__ = [
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SETTINGS_NAME",
    "WslmanwSettings",
    "action_states",
    "column_widths",
    "config_path",
    "detail_fields",
    "detail_lines",
    "distro_row",
    "elide_name",
    "image_details",
    "image_filename",
    "image_row",
    "load_settings",
    "main",
    "pick_download",
    "resolve_geometry",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "shutdown_confirm_enabled",
    "summary_text",
    "text_units",
    "unregister_confirm_enabled",
]

SETTINGS_NAME = "wslmanw"
DEFAULT_GEOMETRY = "980x660"
MIN_WIDTH = 900
MIN_HEIGHT = 600

_DESCRIPTION = "WSL 发行版管理的图形界面：列表/详情、打开终端、启动停止、导出导入、镜像下载、注销与关闭全部（Qt）"

_SOURCE_LABELS = {
    "cli": "CLI 安装（wsl --install）",
    "manifest": "官方清单 URL",
    "store": "Store 包（wsl --install）",
}
"""下载来源的展示文案；与 :mod:`fcmd.apps.wslman._images` 的 sources 取值一一对应。"""

POLL_INTERVAL_MS = 3000
"""固定 3 秒轮询运行状态（不做成设置项）。

依据：本机 Windows 11 + WSL 2 实测一次 ``wsl.exe --list --verbose`` 约 50–70 ms
（5 次平均 51 ms，冷启动 67 ms），3 秒间隔的后台占用约 2%，且 wsl.exe 是短命
进程、不常驻；停止 / 启动通常在 1–3 秒内完成状态迁移，3 秒足够让用户看到变化，
又不会把 wsl.exe 拉得太频繁。窗口最小化时暂停、失焦时按 ``HIDDEN_FACTOR``
放大间隔，空闲开销进一步收敛。
"""

HIDDEN_FACTOR = 3
"""窗口失焦时把轮询间隔放大到 3 倍；最小化时直接暂停。"""

_DOWNLOAD_HINT = "wsl --install 会触发系统级安装、可能弹 UAC，且不能指定自定义镜像"

_IMAGE_DETAIL_FIELDS: tuple[str, ...] = (
    "名称",
    "友好名",
    "可用架构",
    "下载来源",
    "国内镜像",
    "校验和",
    "已安装",
    "已备份",
)
"""下载页详情区的短字段；下载地址单独用可滚动的只读文本域渲染。"""

_COLUMNS: tuple[tuple[str, str, int, int], ...] = (
    ("name", "名称", 160, 360),
    ("state", "状态", 72, 120),
    ("version", "版本", 56, 84),
    ("disk", "占用", 96, 180),
    ("default", "默认", 50, 80),
)
"""列定义：``(列 id, 表头, 最小宽度, 最大宽度)``，宽度按内容自适应后夹在两者之间。"""

_CHAR_PX = 7
"""窄字符的估算宽度（9pt 界面字体）；宽字符按 2 个窄字符计。"""

_CELL_PAD_PX = 16
"""单元格左右留白与排序指示器的估算宽度。"""

_DETAIL_LABELS: tuple[str, ...] = (
    "名称",
    "状态",
    "版本",
    "默认",
    "BasePath",
    "虚拟磁盘",
    "占用",
    "默认用户",
    "来源",
)
"""详情页字段顺序：前 5 项为身份信息，后 4 项为存储信息。"""

_DETAIL_ROWS = 5
"""详情页左右两列各占的行数，长路径换行后仍不会压叠。"""


def text_units(text: str) -> int:
    """按显示宽度计字符数：CJK / 全角字符算 2，其余算 1。"""
    return sum(2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1 for char in text)


def elide_name(name: str, limit: int = 24) -> str:
    """按显示宽度截断过长的发行版名，末尾补 ``…``；未超长时原样返回。"""
    if text_units(name) <= limit:
        return name
    kept: list[str] = []
    used = 0
    for char in name:
        width = 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        if used + width > limit - 1:
            break
        kept.append(char)
        used += width
    return "".join(kept) + "…"


def column_widths(rows: Sequence[Sequence[str]], available: int | None = None) -> dict[str, int]:
    """按表头与各行显示宽度估算列宽，夹在每列的最小 / 最大宽度之间。

    ``available`` 为列表可用总宽时，把富余宽度按各列基础宽度的比例分摊，
    而不是全部塞给最后一列；分摊后仍以每列最大宽度为上限，剩余留白留在右侧。
    """
    widths: dict[str, int] = {}
    for index, (key, title, minimum, maximum) in enumerate(_COLUMNS):
        widest = max([text_units(title), *(text_units(str(row[index])) for row in rows if index < len(row))])
        widths[key] = min(maximum, max(minimum, widest * _CHAR_PX + _CELL_PAD_PX))
    if available is None:
        return widths
    extra = available - sum(widths.values())
    total = sum(widths.values())
    if extra <= 0 or total <= 0:
        return widths
    for key, _title, _minimum, maximum in _COLUMNS:
        share = int(extra * widths[key] / total)
        widths[key] = min(maximum, widths[key] + share)
    return widths


def detail_fields(distro: Distro, size: int | None, estimated: bool) -> tuple[tuple[str, str], ...]:
    """详情页的「字段 → 值」；字段口径与 ``detail_lines`` 完全一致。"""
    # BasePath 来自 Lxss 注册表，始终是 Windows 路径；用 ntpath 拼接保持反斜杠。
    vhdx = ntpath.join(distro.base_path, "ext4.vhdx") if distro.base_path else "未知"
    return (
        ("名称", distro.name),
        ("状态", state_label(distro)),
        ("版本", str(distro.version) if distro.version else "未知"),
        ("默认", "是" if distro.default else "否"),
        ("BasePath", distro.base_path or "未知"),
        ("虚拟磁盘", vhdx),
        ("占用", format_bytes(size, estimated=estimated)),
        ("默认用户", str(distro.default_uid) if distro.default_uid is not None else "未知"),
        ("来源", "Lxss 注册表" if distro.registered else "wsl --list"),
    )


def summary_text(distro: Distro, size: int | None, estimated: bool) -> str:
    """状态栏的完整发行版摘要；列表里截断的名称在这里显示全名。"""
    return (
        f"{distro.name} · {state_label(distro)} · "
        f"v{distro.version or '未知'} · {format_bytes(size, estimated=estimated)}"
    )


def action_states(state: str | None) -> tuple[bool, bool]:
    """按选中行状态返回「启动可用, 停止可用」；未选中时两个都禁用。"""
    if state == "running":
        return False, True
    return (state is not None), False


@dataclass(frozen=True, slots=True)
class WslmanwSettings:
    geometry: str | None = None
    download_dir: str = ""
    mirror: str = "auto"


def settings_to_mapping(settings: WslmanwSettings) -> dict[str, object]:
    return {
        "geometry": settings.geometry,
        "download_dir": settings.download_dir,
        "mirror": settings.mirror,
    }


def settings_from_mapping(raw: Mapping[str, Any]) -> WslmanwSettings:
    # 旧版本写过 manifest_url / download_source / auto_refresh：按未知字段忽略。
    mirror = _str(raw, "mirror")
    return WslmanwSettings(
        geometry=_guiconfig.valid_geometry(_str(raw, "geometry")),
        download_dir=_str(raw, "download_dir"),
        mirror=mirror if mirror in _mirrors.identifiers() else "auto",
    )


def _str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def config_path() -> Path:
    """返回设置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> WslmanwSettings:
    raw = _guiconfig.read_settings(path or config_path())
    if raw is None:
        return WslmanwSettings()
    return settings_from_mapping(raw)


def save_settings(settings: WslmanwSettings, path: Path | None = None) -> None:
    _guiconfig.write_settings(settings_to_mapping(settings), path or config_path())


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    return _geometry.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def unregister_confirm_enabled(typed: str, name: str) -> bool:
    """注销按钮只在手输名称与目标完全一致（忽略大小写与首尾空格）时可用。"""
    return bool(name) and typed.strip().casefold() == name.strip().casefold()


def shutdown_confirm_enabled(checked: bool) -> bool:
    """``wsl --shutdown`` 必须显式勾选影响说明。"""
    return checked is True


def distro_row(distro: Distro, size: int | None, estimated: bool) -> tuple[str, ...]:
    """Treeview 一行：名称（过长截断）、状态、版本、磁盘、默认标记。"""
    return (
        elide_name(distro.name),
        state_label(distro),
        str(distro.version) if distro.version else "未知",
        format_bytes(size, estimated=estimated),
        "是" if distro.default else "",
    )


def detail_lines(distro: Distro, size: int | None, estimated: bool) -> list[str]:
    """详情面板文本行；与 :func:`detail_fields` 同源，未知字段统一显示 ``未知``。"""
    return [f"{label}: {value}" for label, value in detail_fields(distro, size, estimated)]


def image_row(entry: _images.ImageEntry) -> tuple[str, str, str, str]:
    """下载页列表一行：名称、友好名、架构、标记（sha256 / 已安装 / 已备份）。"""
    marks = " · ".join(
        label
        for label, flag in (("sha256", entry.has_sha256), ("已安装", entry.installed), ("已备份", entry.backed_up))
        if flag
    )
    return (entry.name, entry.friendly_name or "-", "/".join(entry.arches) or "任意", marks)


def mirror_choices(entry: _images.ImageEntry) -> str:
    """该条目可用的国内镜像（按优先顺序去重）；没有 sha256 时只列官方源。"""
    if not entry.has_sha256:
        return "无（缺 sha256，仅官方源）"
    labels: list[str] = []
    for download in entry.downloads:
        for candidate in _mirrors.candidates(download.url, policy=_mirrors.AUTO, verifiable=True):
            if candidate.ident != _mirrors.OFFICIAL and candidate.label not in labels:
                labels.append(candidate.label)
    return "、".join(labels) or "无（官方源）"


def image_details(entry: _images.ImageEntry) -> tuple[tuple[str, str], ...]:
    """下载页的「字段 → 值」：来源、架构、校验和与每个架构的下载地址。"""
    sources = "、".join(_SOURCE_LABELS.get(source, source) for source in entry.sources) or "未知"
    downloads = [
        f"{download.arch}: {download.url}" + ("（有 sha256）" if download.sha256 else "（无 sha256）")
        for download in entry.downloads
    ]
    return (
        ("名称", entry.name),
        ("友好名", entry.friendly_name or "未知"),
        ("可用架构", "/".join(entry.arches) or "任意（由 WSL 选择）"),
        ("下载来源", sources),
        ("国内镜像", mirror_choices(entry)),
        ("校验和", "有 sha256" if entry.has_sha256 else "无 sha256（不标 verified）"),
        ("已安装", "是" if entry.installed else "否"),
        ("已备份", "是" if entry.backed_up else "否"),
        ("下载地址", "\n".join(downloads) or "无直接下载地址；请用 wsl --install"),
    )


def pick_download(entry: _images.ImageEntry) -> _images.ImageDownload:
    """按本机架构挑下载项：ARM 优先 aarch64，其余优先 x86_64，都没有时取第一个。"""
    preferred = "aarch64" if "arm" in platform.machine().casefold() else "x86_64"
    for download in entry.downloads:
        if download.arch == preferred:
            return download
    return entry.downloads[0]


def image_filename(name: str, download: _images.ImageDownload) -> str:
    """下载落盘名：沿用官方 URL 的文件名（``.wsl`` / ``.tar`` 等），否则按名称与架构生成。"""
    candidate = Path(urllib.parse.urlparse(download.url).path).name
    if candidate.casefold().endswith((".wsl", ".tar", ".tar.gz", ".tgz", ".appx", ".appxbundle")):
        return candidate
    return f"{name}-{download.arch}.tar"


_ARCHIVE_SUFFIXES: tuple[str, ...] = (".tar", ".tar.gz", ".tgz", ".wsl")
"""可交给 ``wsl --import`` 的本地镜像包后缀；``.wsl`` 是官方清单里的未压缩 tar。"""


@dataclass(frozen=True, slots=True)
class LocalArchive:
    """磁盘上已有的镜像包，以及它与官方清单的对应关系。"""

    path: Path
    size: int
    distro: str
    arch: str
    source: str
    expected_sha256: str | None = None


def local_archives(directory: Path) -> tuple[Path, ...]:
    """列出目录（不递归）里的镜像包，按名称排序；目录不存在或不可读时返回空。"""
    if not directory.is_dir():
        return ()
    try:
        found = [
            path for path in directory.iterdir() if path.is_file() and path.name.casefold().endswith(_ARCHIVE_SUFFIXES)
        ]
    except OSError:
        return ()
    return tuple(sorted(found, key=lambda path: path.name.casefold()))


def guess_distro_name(path: Path) -> str:
    """从文件名推测发行版名：去掉归档后缀与架构尾巴，非法字符换成 ``_``。"""
    name = path.name
    for suffix in _ARCHIVE_SUFFIXES:
        if name.casefold().endswith(suffix):
            name = name[: -len(suffix)]
            break
    for tail in ("-x86_64", "-aarch64", "-amd64", "-arm64", "-x64"):
        if name.casefold().endswith(tail):
            name = name[: -len(tail)]
            break
    cleaned = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in name).strip("._-")
    return cleaned or "distro"


def match_archive(
    path: Path, entries: Sequence[_images.ImageEntry]
) -> tuple[_images.ImageEntry, _images.ImageDownload] | None:
    """按落盘文件名匹配官方清单：GUI 下载用 URL 原名，CLI 下载用 ``名称-架构.tar``。"""
    name = path.name.casefold()
    for entry in entries:
        for download in entry.downloads:
            official = Path(urllib.parse.urlparse(download.url).path).name.casefold()
            if name in {official, f"{entry.name}-{download.arch}.tar".casefold()}:
                return entry, download
    return None


def inspect_archive(path: Path, entries: Sequence[_images.ImageEntry]) -> LocalArchive:
    """汇总本地包信息：发行版、架构、来源与官方 sha256（未收录时为 ``None``）。"""
    size = path.stat().st_size
    matched = match_archive(path, entries)
    if matched is None:
        return LocalArchive(path, size, guess_distro_name(path), "未知", "本地文件（未匹配官方清单）")
    entry, download = matched
    return LocalArchive(
        path,
        size,
        entry.name,
        download.arch,
        f"本地文件 · 官方清单 {entry.name}（{download.arch}）",
        download.sha256,
    )


def verification_label(expected_sha256: str | None, actual: str | None) -> str:
    """包校验状态；没有官方摘要时只说明「未比对」，不把文件说成可信。"""
    if expected_sha256 is None:
        return "无官方 sha256（未比对，不标可信）"
    if actual is None:
        return "待校验"
    return "sha256 与官方清单一致" if actual == expected_sha256.strip().casefold() else "sha256 与官方清单不一致"


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, WslmanError):
        text = str(exc)
        return f"{text}（{exc.hint}）" if exc.hint else text
    return f"{type(exc).__name__}: {exc}"


def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.apps.wslman._wslmanw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("wslmanw", help="打开 WSL 发行版管理窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 WSL 发行版管理窗口。"""
    run_gui()


@fcmd.main("wslmanw")
def main() -> None:
    """``wslmanw`` 命令入口。"""
    launch_cmd()
