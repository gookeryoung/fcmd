"""wslmanw - WSL 发行版管理的 Qt 窗口。

窗口只做编排：业务判断仍走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出核心、预检、注销前备份），
避免 GUI 与 CLI 两套口径。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``。
"""

from __future__ import annotations

import ntpath
import platform
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
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
    "config_path",
    "detail_fields",
    "distro_image_text",
    "download_progress_text",
    "filter_images",
    "image_counts",
    "image_details",
    "image_filename",
    "image_item_lines",
    "image_marks",
    "image_pin_rank",
    "image_sort_key",
    "image_source_text",
    "image_status_text",
    "load_settings",
    "main",
    "pick_download",
    "resolve_geometry",
    "save_settings",
    "settings_from_mapping",
    "settings_to_mapping",
    "shutdown_confirm_enabled",
    "sort_images",
    "summary_text",
    "unregister_confirm_enabled",
]

SETTINGS_NAME = "wslmanw"
DEFAULT_GEOMETRY = "1160x760"
MIN_WIDTH = 900
MIN_HEIGHT = 600

_DESCRIPTION = "WSL 发行版管理的图形界面：列表/详情、打开终端、启动停止、导出导入、镜像下载、注销与关闭全部（Qt）"

_SOURCE_LABELS = {
    "cli": "CLI 安装（wsl --install）",
    "manifest": "官方清单 URL",
    "store": "Store 包（wsl --install）",
}
"""下载来源的展示文案；与 :mod:`fcmd.apps.wslman._images` 的 sources 取值一一对应。"""

_SOURCE_SHORT = {"cli": "CLI", "manifest": "清单", "store": "Store"}
"""列表「来源」列的短文案；详情区用 ``_SOURCE_LABELS`` 的完整说明。"""

IMAGE_FILTERS: tuple[tuple[str, str], ...] = (
    ("all", "全部"),
    ("download", "可直链下载"),
    ("install", "仅安装"),
    ("installed", "已安装"),
    ("backed_up", "已备份"),
)
"""镜像页的筛选按钮：``download`` 有可直链归档，``install`` 只能走 ``wsl --install``。"""

DEFAULT_IMAGE_FILTER = "all"

IMAGE_SORTS: tuple[tuple[str, str], ...] = (
    ("name", "按名称"),
    ("friendly", "按友好名"),
    ("status", "已备份优先"),
)
"""镜像页排序选项；默认按名称，与 llmw 模型列表的「排序下拉」同构。

「已安装 / 已下载」在任何排序下都置顶（见 :func:`image_pin_rank`），所以
``status`` 只额外决定同档位内「已备份」排前面。
"""

DEFAULT_IMAGE_SORT = "name"

IMAGE_SOURCES: tuple[tuple[str, str], ...] = (
    ("all", "全部来源"),
    ("manifest", "官方清单"),
    ("cli", "CLI 清单"),
    ("store", "Store 包"),
)
"""镜像页来源筛选；``all`` 之外的值对应 ``ImageEntry.sources`` 的元素。"""

DEFAULT_IMAGE_SOURCE = "all"

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


def detail_fields(distro: Distro, size: int | None, estimated: bool) -> tuple[tuple[str, str], ...]:
    """详情区的「字段 → 值」：行内展开与状态栏摘要共用同一份口径。"""
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
    sidebar_collapsed: bool = False
    image_filter: str = DEFAULT_IMAGE_FILTER
    image_sort: str = DEFAULT_IMAGE_SORT
    image_source: str = DEFAULT_IMAGE_SOURCE


def settings_to_mapping(settings: WslmanwSettings) -> dict[str, object]:
    return {
        "geometry": settings.geometry,
        "download_dir": settings.download_dir,
        "mirror": settings.mirror,
        "sidebar_collapsed": settings.sidebar_collapsed,
        "image_filter": settings.image_filter,
        "image_sort": settings.image_sort,
        "image_source": settings.image_source,
    }


def settings_from_mapping(raw: Mapping[str, Any]) -> WslmanwSettings:
    # 旧版本写过 manifest_url / download_source / auto_refresh：按未知字段忽略。
    mirror = _str(raw, "mirror")
    collapsed = raw.get("sidebar_collapsed")
    return WslmanwSettings(
        geometry=_guiconfig.valid_geometry(_str(raw, "geometry")),
        download_dir=_str(raw, "download_dir"),
        mirror=mirror if mirror in _mirrors.identifiers() else "auto",
        sidebar_collapsed=collapsed is True,
        image_filter=_choice(raw, "image_filter", IMAGE_FILTERS, DEFAULT_IMAGE_FILTER),
        image_sort=_choice(raw, "image_sort", IMAGE_SORTS, DEFAULT_IMAGE_SORT),
        image_source=_choice(raw, "image_source", IMAGE_SOURCES, DEFAULT_IMAGE_SOURCE),
    )


def _choice(raw: Mapping[str, Any], key: str, options: Sequence[tuple[str, str]], default: str) -> str:
    """只接受 ``options`` 里登记过的取值，非法 / 缺失时回落 ``default``。"""
    value = _str(raw, key)
    return value if any(ident == value for ident, _label in options) else default


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


def image_source_text(entry: _images.ImageEntry) -> str:
    """列表「来源」列：把 ``sources`` 折成短文案（``CLI`` / ``清单`` / ``Store``）。"""
    return " · ".join(_SOURCE_SHORT.get(source, source) for source in entry.sources) or "未知"


def image_item_lines(entry: _images.ImageEntry) -> tuple[str, str, str]:
    """列表行三段文案（与 llmw 的 ``model_item_lines`` 同构）：标题、副标题、右侧关键状态。

    行卡只承载关键信息；完整字段与下载地址在行内展开区显示，不再占用右侧面板。
    """
    arches = "/".join(entry.arches) or "任意架构"
    subtitle = " · ".join(part for part in (entry.friendly_name, arches, image_source_text(entry)) if part)
    metrics = image_action_text(entry)
    return entry.name, subtitle or "—", metrics


def image_action_text(entry: _images.ImageEntry) -> str:
    """右侧关键状态：已安装 / 可安装（本地已有归档）/ 可下载 / 仅 ``wsl --install``。"""
    if entry.installed:
        return "已安装"
    if entry.downloaded:
        return "可安装"
    return "可下载" if entry.downloads else "仅 wsl --install"


def image_marks(entry: _images.ImageEntry) -> tuple[tuple[str, str], ...]:
    """行内状态胶囊 ``(文案, 色调)``：已安装 / 已备份 / 有 sha256 / 缺摘要。"""
    marks: list[tuple[str, str]] = []
    if entry.installed:
        marks.append(("已安装", "success"))
    elif entry.downloaded:
        marks.append(("已下载", "accent"))
    if entry.backed_up:
        marks.append(("已备份", "accent"))
    if entry.has_sha256:
        marks.append(("sha256", "muted"))
    elif entry.downloads:
        marks.append(("无 sha256", "warning"))
    return tuple(marks)


def filter_images(
    entries: Sequence[_images.ImageEntry],
    *,
    query: str = "",
    filter_key: str = DEFAULT_IMAGE_FILTER,
    source: str = DEFAULT_IMAGE_SOURCE,
) -> tuple[_images.ImageEntry, ...]:
    """按名称搜索、状态筛选与来源筛选收窄镜像列表（大小写不敏感）。"""
    text = query.strip().casefold()
    selected: list[_images.ImageEntry] = []
    for entry in entries:
        if source != "all" and source not in entry.sources:
            continue
        if filter_key == "download" and not entry.downloads:
            continue
        if filter_key == "install" and entry.downloads:
            continue
        if filter_key == "installed" and not entry.installed:
            continue
        if filter_key == "backed_up" and not entry.backed_up:
            continue
        if text and text not in entry.name.casefold() and text not in entry.friendly_name.casefold():
            continue
        selected.append(entry)
    return tuple(selected)


def image_pin_rank(entry: _images.ImageEntry) -> int:
    """置顶档位：已安装 ``0`` / 已下载 ``1`` / 其它 ``2``。

    本地已有的镜像永远排在最前面：在线清单动辄上百条，已安装 / 已下载的条目
    被排到中间就找不到（与 llmw 模型列表「已安装置顶」同一口径）。
    """
    if entry.installed:
        return 0
    if entry.downloaded:
        return 1
    return 2


def image_sort_key(key: str) -> Callable[[_images.ImageEntry], Any]:
    """排序键函数：先按置顶档位，再按所选口径；未知 key 回落按名称。

    置顶与排序口径无关：切换「按名称 / 按友好名 / 已备份优先」都不会把已安装 /
    已下载的镜像挤到列表中间；同档位内才比较名称 / 友好名 / 备份状态。
    """
    if key == "friendly":
        return lambda entry: (image_pin_rank(entry), (entry.friendly_name or entry.name).casefold())
    if key == "status":
        return lambda entry: (image_pin_rank(entry), not entry.backed_up, entry.name.casefold())
    return lambda entry: (image_pin_rank(entry), entry.name.casefold())


def sort_images(entries: Sequence[_images.ImageEntry], key: str = DEFAULT_IMAGE_SORT) -> tuple[_images.ImageEntry, ...]:
    """稳定排序；同键时保留来源合并顺序（CLI 清单在前）。"""
    return tuple(sorted(entries, key=image_sort_key(key)))


def image_counts(entries: Sequence[_images.ImageEntry]) -> dict[str, int]:
    """各筛选口径的条目数，供状态行显示「已安装 N / 可直链 M」。"""
    counts = {ident: 0 for ident, _label in IMAGE_FILTERS}
    counts["all"] = len(entries)
    for entry in entries:
        if entry.downloads:
            counts["download"] += 1
        else:
            counts["install"] += 1
        if entry.installed:
            counts["installed"] += 1
        if entry.backed_up:
            counts["backed_up"] += 1
    return counts


def image_status_text(
    entries: Sequence[_images.ImageEntry],
    shown: int,
    *,
    filter_key: str = DEFAULT_IMAGE_FILTER,
    source: str = DEFAULT_IMAGE_SOURCE,
    note: str = "",
) -> str:
    """镜像页状态行：总量、当前筛选、显示条数与本地状态，末尾附缓存 / 来源备注。"""
    counts = image_counts(entries)
    parts = [f"共 {counts['all']} 个镜像"]
    if filter_key != "all":
        label = next((label for ident, label in IMAGE_FILTERS if ident == filter_key), filter_key)
        parts.append(f"{label} {counts.get(filter_key, 0)} 个")
    if source != "all":
        parts.append(next((label for ident, label in IMAGE_SOURCES if ident == source), source))
    parts.append(f"显示 {shown} 个")
    parts.append(f"已安装 {counts['installed']}")
    parts.append(f"可直链 {counts['download']}")
    text = " · ".join(parts)
    if shown == 0 and counts["all"]:
        text += "（没有匹配项，可调整筛选或搜索词）"
    return f"{text} · {note}" if note else text


def download_progress_text(name: str, done: int, total: int | None, speed: float = 0.0) -> str:
    """状态栏的下载进度文案：``下载 X · 42% · 1.2 GB / 2.6 GB · 3.2 MB/s · 剩余 12s``。

    总量未知（服务端没给 Content-Length）时只给已下载字节与速度；速度为 0
    （刚开始 / 断流）时速度显示 ``--`` 且不估算剩余时间。
    """
    from fcmd.infras.download import format_eta, format_speed

    parts = [f"下载 {name}"]
    if total and total > 0:
        percent = max(0, min(100, int(done * 100 / total)))
        parts.append(f"{percent}%")
        parts.append(f"{format_bytes(done)} / {format_bytes(total)}")
    else:
        parts.append(format_bytes(done))
    parts.append(format_speed(speed))
    if total and total > done and speed > 0:
        parts.append(f"剩余 {format_eta((total - done) / speed)}")
    return " · ".join(parts)


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


def mark_downloaded(entries: Sequence[_images.ImageEntry], directory: Path) -> tuple[_images.ImageEntry, ...]:
    """按下载目录里的归档标注「已下载」，并记住归档路径供行内「安装」直接用。

    同一发行版有多个架构时优先本机架构（:func:`pick_download`）；归档命名既认
    官方 URL 原名，也认 CLI 的 ``名称-架构.tar``（:func:`match_archive`）。
    """
    found: dict[str, Path] = {}
    for path in local_archives(directory):
        matched = match_archive(path, entries)
        if matched is None:
            continue
        entry, download = matched
        key = entry.name.casefold()
        preferred = pick_download(entry).arch
        current = found.get(key)
        if current is None or download.arch == preferred:
            found[key] = path
    return tuple(
        replace(
            entry,
            downloaded=entry.name.casefold() in found,
            archive=str(found[entry.name.casefold()]) if entry.name.casefold() in found else "",
        )
        for entry in entries
    )


def match_distro_image(distro: Distro, entries: Sequence[_images.ImageEntry]) -> _images.ImageEntry | None:
    """把已安装发行版对应到在线镜像条目（大小写不敏感）。

    依次尝试：名称全等 → 友好名全等 → 名称是发行版名的「词首前缀」
    （如 ``Ubuntu-24.04`` 对镜像 ``Ubuntu``，分隔符 ``-`` / ``_`` / 空格 / ``.``），
    多个前缀命中时取最长（更具体）的一个；都没有时返回 ``None``（自定义导入 / 已下架）。
    """
    if not entries:
        return None
    key = distro.name.strip().casefold()
    if not key:
        return None
    for entry in entries:
        if entry.name.casefold() == key:
            return entry
    for entry in entries:
        if entry.friendly_name and entry.friendly_name.casefold() == key:
            return entry
    prefixes = [
        entry
        for entry in entries
        if key.startswith(entry.name.casefold()) and key[len(entry.name) : len(entry.name) + 1] in {"-", "_", " ", "."}
    ]
    return max(prefixes, key=lambda entry: len(entry.name), default=None)


def distro_image_text(distro: Distro, entries: Sequence[_images.ImageEntry], *, known: bool = True) -> str:
    """行内「镜像」说明：命中给镜像友好名，未命中给「本地导入」，清单未知时留空。"""
    entry = match_distro_image(distro, entries)
    if entry is not None:
        return f"镜像 {entry.friendly_name or entry.name}"
    return "本地导入" if known else ""


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
