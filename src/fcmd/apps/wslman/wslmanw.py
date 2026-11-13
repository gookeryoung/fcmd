"""wslmanw - WSL 发行版管理的 Tk 窗口。

窗口只做编排：业务判断仍走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出核心、预检、注销前备份），
避免 GUI 与 CLI 两套口径。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``。
"""

from __future__ import annotations

import contextlib
import platform
import queue
import threading
import time
import unicodedata
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.wslman import _images, _mirrors
from fcmd.apps.wslman import wslman as cli
from fcmd.apps.wslman._config import WslmanConfig, load_config
from fcmd.apps.wslman._disk import distro_disk_bytes, format_bytes, recommended_backup_dir
from fcmd.apps.wslman._distro import Distro, state_label
from fcmd.apps.wslman._errors import WslmanError, invalid_argument, runtime_error
from fcmd.apps.wslman._terminal import LaunchError, SubprocessLauncher, build_command
from fcmd.apps.wslman._transfer import sha256_file
from fcmd.gui import config as _guiconfig
from fcmd.gui import theme as _guitheme
from fcmd.gui import window as _guiwindow

__all__ = [
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SETTINGS_NAME",
    "WslmanWindow",
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

_DESCRIPTION = "WSL 发行版管理的图形界面：列表/详情、打开终端、启动停止、导出导入、镜像下载、注销与关闭全部"

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
    vhdx = str(Path(distro.base_path) / "ext4.vhdx") if distro.base_path else "未知"
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
    return _guiwindow.resolve_geometry(
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


class WslmanWindow:  # pragma: no cover - 需要真实 Tk 窗口
    """主窗口：左侧发行版列表 + 详情，右侧分页放下载与日志。"""

    def __init__(self, settings_path: Path | None = None) -> None:
        import tkinter as tk
        from tkinter import filedialog, messagebox, simpledialog, ttk

        self.tk = tk
        self.ttk = ttk
        self.filedialog = filedialog
        self.messagebox = messagebox
        self.simpledialog = simpledialog
        self.settings_path = settings_path or config_path()
        self.settings = load_settings(self.settings_path)
        self.config_data: WslmanConfig = load_config()
        self.launcher = SubprocessLauncher()
        self.view: Any = None
        self.sizes: dict[str, tuple[int | None, bool]] = {}
        self._events: queue.Queue[tuple[str, str, Any]] = queue.Queue()
        self._busy = False
        self._cancel = threading.Event()
        self._label = ""
        self._rows: list[tuple[str, ...]] = []
        self._poll_after: str | None = None
        self._status_busy = False
        self._images_busy = False
        self.image_entries: tuple[_images.ImageEntry, ...] = ()
        self._filter_after: str | None = None
        self._closing = False

        self.root, self.theme = _guiwindow.create_root(
            title="WSL 管理",
            saved_geometry=self.settings.geometry,
            default_geometry=DEFAULT_GEOMETRY,
            min_width=MIN_WIDTH,
            min_height=MIN_HEIGHT,
            on_close=self._on_close,
        )
        # 主题间距已按 DPI 缩放（apply_theme 内走 scale_spacing），窗口只引用不另算。
        self.space = self.theme.spacing
        self._build()
        _guiwindow.build_menu(self.root, on_close=self._on_close)
        _guitheme.install_theme_switcher(self.root)
        self.root.bind("<Map>", self._on_window_active)
        self.root.bind("<FocusIn>", self._on_window_active)
        self.root.after(100, self._poll)
        self.refresh()
        self._schedule_poll()

    # -- 布局 ---------------------------------------------------------- #

    def _build(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=3)
        self.root.rowconfigure(2, weight=2)
        self._build_toolbar()
        self._build_content()
        self._build_notebook()
        self._build_status()

    def _build_toolbar(self) -> None:
        """按钮按危险等级分组：安全 / 变更 / 传输 / 破坏，破坏性用 danger 样式。"""
        ttk = self.ttk
        space = self.space
        bar = ttk.Frame(self.root, padding=(space.xs, space.xxs))
        bar.grid(row=0, column=0, sticky="ew")

        self._buttons: list[Any] = []
        self._button_by_text: dict[str, Any] = {}
        groups: tuple[tuple[tuple[str, Callable[[], None], str | None], ...], ...] = (
            (
                ("打开终端", self._open_selected, "Accent.TButton"),
                ("启动", self._start_selected, None),
                ("停止", self._terminate_selected, None),
            ),
            (
                ("导出", self._export_selected, None),
                ("安装本地镜像…", self._install_local, None),
            ),
            (
                ("注销", self._unregister, "Danger.TButton"),
                ("关闭全部", self._shutdown, "Danger.TButton"),
            ),
        )
        for index, group in enumerate(groups):
            if index:
                ttk.Separator(bar, orient="vertical").pack(side="left", fill="y", padx=space.xs, pady=space.xxs)
            for text, command, style in group:
                button = ttk.Button(bar, text=text, command=command, style=style or "TButton")
                button.pack(side="left", padx=(0, space.xxs))
                self._buttons.append(button)
                self._button_by_text[text] = button

        self.start_button = self._button_by_text["启动"]
        self.stop_button = self._button_by_text["停止"]

    def _build_content(self) -> None:
        """发行版列表独占整幅宽度；详情移到笔记本页，不再挤占右栏。"""
        ttk = self.ttk
        space = self.space
        tree_wrap = ttk.Frame(self.root)
        tree_wrap.grid(row=1, column=0, sticky="nsew", padx=space.xs, pady=(0, space.xxs))
        tree_wrap.columnconfigure(0, weight=1)
        tree_wrap.rowconfigure(0, weight=1)

        columns = tuple(key for key, _title, _minimum, _maximum in _COLUMNS)
        self.tree = ttk.Treeview(tree_wrap, columns=columns, show="headings", selectmode="browse")
        for column, title, minimum, _maximum in _COLUMNS:
            self.tree.heading(column, text=title)
            # 宽度由 column_widths 统一计算，stretch 关闭后不会把多余宽度堆给最后一列。
            self.tree.column(column, width=minimum, minwidth=minimum, anchor="w", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(tree_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._show_details())
        self.tree.bind("<Motion>", self._on_hover)
        self.tree.bind("<Leave>", lambda _event: self.hover.set(""))
        self.tree.bind("<Double-1>", lambda _event: self._open_selected())
        self.tree.bind("<Configure>", self._on_tree_resize)

    def _build_notebook(self) -> None:
        ttk = self.ttk
        space = self.space
        notebook = ttk.Notebook(self.root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=space.xs, pady=(0, space.xxs))
        self.notebook = notebook
        self._build_detail_tab(notebook)
        self._build_download_tab(notebook)
        log_frame = ttk.Frame(notebook, padding=space.xs)
        notebook.add(log_frame, text="日志")
        self.log = _guiwindow.create_log(log_frame, height=8)
        notebook.select(0)
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _build_detail_tab(self, notebook: Any) -> None:
        """详情页：两列「字段 / 值」，窄高度下靠两列排布避免裁切与压叠。"""
        tk, ttk = self.tk, self.ttk
        space = self.space
        frame = ttk.Frame(notebook, padding=(space.sm, space.xs))
        notebook.add(frame, text="详情")
        frame.columnconfigure(1, weight=1)
        frame.columnconfigure(3, weight=1)

        self.detail_value_labels: list[Any] = []
        self.detail_vars: dict[str, Any] = {}
        for index, label in enumerate(_DETAIL_LABELS):
            group, row = divmod(index, _DETAIL_ROWS)
            column = group * 2
            ttk.Label(frame, text=label, style="Muted.TLabel", anchor="nw").grid(
                row=row, column=column, sticky="nw", padx=(0, space.xs), pady=space.xxs
            )
            variable = tk.StringVar(value="—")
            value = ttk.Label(frame, textvariable=variable, anchor="w", justify="left")
            value.grid(row=row, column=column + 1, sticky="ew", padx=(0, space.lg), pady=space.xxs)
            self.detail_vars[label] = variable
            self.detail_value_labels.append(value)

        self.detail_hint = tk.StringVar(value="选择上方列表中的发行版查看详情")
        ttk.Label(frame, textvariable=self.detail_hint, style="Hint.TLabel").grid(
            row=_DETAIL_ROWS, column=0, columnspan=4, sticky="w", pady=(space.xs, 0)
        )
        frame.bind("<Configure>", self._on_detail_resize)

    def _on_detail_resize(self, event: Any) -> None:
        """按当前半栏宽度设置换行点，长路径不会横向溢出。"""
        half = max(160, (event.width - int(self.space.lg) * 2) // 2)
        for label in self.detail_value_labels:
            label.configure(wraplength=max(120, half - 80))

    def _build_status(self) -> None:
        ttk = self.ttk
        space = self.space
        bar = ttk.Frame(self.root)
        bar.grid(row=3, column=0, sticky="ew")
        ttk.Separator(bar, orient="horizontal").pack(fill="x")
        inner = ttk.Frame(bar, padding=(space.xs, space.xxs))
        inner.pack(fill="x")
        self.hover = self.tk.StringVar(value="")
        ttk.Label(inner, textvariable=self.hover, style="Muted.TLabel", anchor="w").pack(
            side="left", fill="x", expand=True
        )
        self.status = self.tk.StringVar(value="就绪")
        ttk.Label(inner, textvariable=self.status, anchor="e").pack(side="right")

    def _build_download_tab(self, notebook: Any) -> None:
        ttk = self.ttk
        space = self.space
        frame = ttk.Frame(notebook, padding=(space.sm, space.xs))
        notebook.add(frame, text="下载")
        frame.columnconfigure(0, weight=3)
        frame.columnconfigure(1, weight=2)
        frame.rowconfigure(1, weight=1)

        search = ttk.Frame(frame)
        search.grid(row=0, column=0, sticky="ew")
        ttk.Label(search, text="搜索").pack(side="left")
        self.image_query = self.tk.StringVar(value="")
        self.image_search = ttk.Entry(search, textvariable=self.image_query)
        self.image_search.pack(side="left", fill="x", expand=True, padx=(space.xs, space.xs))
        self.image_search.bind("<KeyRelease>", self._schedule_image_filter)

        mirror_box = ttk.Frame(frame)
        mirror_box.grid(row=0, column=1, sticky="e")
        ttk.Label(mirror_box, text="下载源", style="Muted.TLabel").pack(side="left", padx=(space.xs, space.xs))
        self.mirror_ids = _mirrors.identifiers()
        self.mirror_var = self.tk.StringVar(value=self.settings.mirror)
        self.mirror_combo = ttk.Combobox(
            mirror_box,
            state="readonly",
            width=18,
            values=[_mirrors.label(ident) for ident in self.mirror_ids],
        )
        self.mirror_combo.current(self.mirror_ids.index(self.settings.mirror))
        self.mirror_combo.pack(side="left")
        self.mirror_combo.bind("<<ComboboxSelected>>", self._on_mirror_changed)
        self.images_status = self.tk.StringVar(value="打开本页时自动获取镜像清单")
        ttk.Label(mirror_box, textvariable=self.images_status, style="Muted.TLabel").pack(
            side="left", padx=(space.xs, 0)
        )

        listing = ttk.Frame(frame)
        listing.grid(row=1, column=0, sticky="nsew", pady=(space.xs, 0))
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        self.image_tree = ttk.Treeview(
            listing,
            columns=("name", "friendly", "arches", "marks"),
            show="headings",
            selectmode="browse",
            height=8,
        )
        for column, title, width, anchor in (
            ("name", "名称", 130, "w"),
            ("friendly", "友好名", 170, "w"),
            ("arches", "架构", 90, "w"),
            ("marks", "标记", 120, "w"),
        ):
            self.image_tree.heading(column, text=title)
            self.image_tree.column(column, width=width, minwidth=60, anchor=anchor, stretch=column == "friendly")
        self.image_tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=self.image_tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.image_tree.configure(yscrollcommand=scroll.set)
        self.image_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_image_details())
        self.image_tree.bind("<Double-1>", lambda _event: self._download())

        detail = ttk.Frame(frame, padding=(space.md, space.xs, 0, 0))
        detail.grid(row=1, column=1, sticky="nsew", pady=(space.xs, 0))
        detail.columnconfigure(1, weight=1)
        detail.rowconfigure(len(_IMAGE_DETAIL_FIELDS), weight=1)
        self.image_detail_vars: dict[str, Any] = {}
        self.image_detail_labels: list[Any] = []
        for row, label in enumerate(_IMAGE_DETAIL_FIELDS):
            ttk.Label(detail, text=label, style="Muted.TLabel", anchor="nw").grid(
                row=row, column=0, sticky="nw", padx=(0, space.xs)
            )
            variable = self.tk.StringVar(value="—")
            value = ttk.Label(detail, textvariable=variable, justify="left", anchor="nw")
            value.grid(row=row, column=1, sticky="ew")
            self.image_detail_vars[label] = variable
            self.image_detail_labels.append(value)

        # 下载地址可能很长：用只读文本域按字符折行 + 纵向滚动，既不撑宽列也不静默截断，
        # 文本还能选中复制；配色由主题的 ``*Text.*`` 选项统一给出。
        url_row = len(_IMAGE_DETAIL_FIELDS)
        ttk.Label(detail, text="下载地址", style="Muted.TLabel", anchor="nw").grid(
            row=url_row, column=0, sticky="nw", padx=(0, space.xs)
        )
        self.image_url_text = self.tk.Text(detail, height=4, width=20, wrap="char", relief="flat")
        self.image_url_text.grid(row=url_row, column=1, sticky="nsew")
        url_scroll = ttk.Scrollbar(detail, orient="vertical", command=self.image_url_text.yview)
        url_scroll.grid(row=url_row, column=2, sticky="ns")
        self.image_url_text.configure(yscrollcommand=url_scroll.set, state="disabled", borderwidth=0)
        detail.bind("<Configure>", self._on_image_detail_resize)

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(space.xs, 0))
        ttk.Button(buttons, text="开始下载", command=self._download, style="Accent.TButton").pack(side="left")
        ttk.Button(buttons, text="取消", command=self._cancel_task).pack(side="left", padx=(space.xs, 0))
        self.install_after = self.tk.BooleanVar(value=False)
        ttk.Checkbutton(buttons, text="下载后立即安装", variable=self.install_after).pack(
            side="left", padx=(space.sm, 0)
        )
        ttk.Label(buttons, text=_DOWNLOAD_HINT, style="Hint.TLabel", wraplength=380).pack(
            side="left", padx=(space.md, 0)
        )

        advanced = ttk.Frame(frame)
        advanced.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(space.xs, 0))
        container, body = _guiwindow.collapsible(
            advanced,
            "高级：下载目录 / 校验",
            padding=(space.sm, space.xs, space.sm, space.xs),
            open_state=False,
        )
        container.pack(fill="x")
        body.columnconfigure(1, weight=1)
        self.download_dir = self._field(body, 0, 0, "下载目录", self.settings.download_dir)
        self.expected_sha = self._field(body, 1, 0, "期望 sha256", "")

    def _field(self, parent: Any, row: int, column: int, label: str, default: str) -> Any:
        """紧凑的「标签 + 输入框」；列坐标以 ``column`` 为标签列。"""
        space = self.space
        self.ttk.Label(parent, text=label).grid(row=row, column=column, sticky="w", padx=(0, space.xs), pady=space.xxs)
        variable = self.tk.StringVar(value=default)
        self.ttk.Entry(parent, textvariable=variable).grid(row=row, column=column + 1, sticky="ew", pady=space.xxs)
        return variable

    # -- 任务编排 ------------------------------------------------------ #

    def _start(self, label: str, work: Callable[[threading.Event], str]) -> None:
        if self._busy:
            self.messagebox.showinfo("请稍候", "已有任务在执行")
            return
        self._busy = True
        self._label = label
        self._cancel = threading.Event()
        self.status.set(f"{label}…")
        self._set_buttons(False)
        cancel = self._cancel

        def runner() -> None:
            try:
                message = work(cancel)
            except KeyboardInterrupt:
                self._events.put(("cancelled", label, None))
            except WslmanError as exc:
                self._events.put(("error", label, exc))
            except Exception as exc:
                self._events.put(("error", label, exc))
            else:
                self._events.put(("done", label, message))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, label, payload = self._events.get_nowait()
                if self._handle_event(kind, label, payload):
                    break
        except queue.Empty:
            pass
        if not self._closing:
            self.root.after(100, self._poll)

    def _handle_event(self, kind: str, label: str, payload: Any) -> bool:
        """处理一个后台事件；返回 ``True`` 表示任务收尾后应停止本轮批量处理。"""
        if self._handle_poll_event(kind, payload):
            return False
        self._busy = False
        self._set_buttons(True)
        if kind == "done":
            self._log(f"[{label}] {payload}")
            self.status.set(f"{label}完成")
            if label == "刷新":
                self._refresh_rows()
            else:
                self.refresh()
            return True
        if kind == "cancelled":
            self._log(f"[{label}] 已取消；未完成的 .part 按配置保留或清理")
            self.status.set(f"{label}已取消")
            return False
        self._log(f"[{label}] 失败：{_error_text(payload)}")
        self.status.set(f"{label}失败")
        self.messagebox.showerror(label, _error_text(payload))
        return False

    def _handle_poll_event(self, kind: str, payload: Any) -> bool:
        """状态轮询与镜像清单的后台事件；返回 ``True`` 表示已消费。"""
        if kind == "log":
            self._log(payload)
            return True
        if kind == "status":
            self._status_busy = False
            if not self._busy:
                self._apply_status(payload)
            return True
        if kind == "status_error":
            self._status_busy = False
            if not self._busy:
                self.status.set(f"状态刷新失败：{_error_text(payload)}")
            return True
        if kind == "images":
            self._images_busy = False
            if not self._closing:
                self._apply_images(*payload)
            return True
        if kind == "images_error":
            self._images_busy = False
            self.images_status.set(f"镜像清单获取失败：{_error_text(payload)}")
            self._log(f"[镜像列表] {_error_text(payload)}")
            return True
        return False

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._buttons:
            button.configure(state=state)
        if enabled:
            self._update_action_states()

    # -- 运行状态自动刷新 ---------------------------------------------- #

    def _poll_delay(self) -> int:
        """本次轮询应等待的毫秒数：最小化返回 0（暂停），失焦放大 ``HIDDEN_FACTOR`` 倍。"""
        if self.root.state() == "iconic":
            return 0
        if not self.root.focus_displayof():
            return POLL_INTERVAL_MS * HIDDEN_FACTOR
        return POLL_INTERVAL_MS

    def _schedule_poll(self) -> None:
        """重置单次定时器；间隔固定 3 秒，窗口状态只影响暂停 / 降频。"""
        self._cancel_poll()
        if self._closing:
            return
        delay = self._poll_delay()
        if delay <= 0:
            return
        self._poll_after = self.root.after(delay, self._poll_tick)

    def _cancel_poll(self) -> None:
        if self._poll_after is None:
            return
        with contextlib.suppress(self.tk.TclError):
            self.root.after_cancel(self._poll_after)
        self._poll_after = None

    def _poll_tick(self) -> None:
        self._poll_after = None
        if self._closing:
            return
        self._request_status()
        self._schedule_poll()

    def _request_status(self) -> None:
        """发起一次后台状态查询；上一次未完成时直接跳过，避免任务叠加。"""
        if self._status_busy or self._busy:
            return
        self._status_busy = True
        threading.Thread(target=self._status_worker, daemon=True).start()

    def _status_worker(self) -> None:
        try:
            view = cli._view(cli._build_environment())
        except WslmanError as exc:
            self._events.put(("status_error", "状态刷新", exc))
        except Exception as exc:
            self._events.put(("status_error", "状态刷新", exc))
        else:
            self._events.put(("status", "状态刷新", view))

    def _apply_status(self, view: Any) -> None:
        """只改状态列与按钮态，不重建整表：选中行、滚动位置、详情页输入都不受影响。"""
        previous = self.view
        self.view = view
        names = [distro.name for distro in view.distros]
        if previous is None or names != [distro.name for distro in previous.distros]:
            # 增删发行版属于结构性变化，此时才整表重建（内部保留选中行）。
            self._refresh_rows()
            return
        for distro in view.distros:
            if self.tree.exists(distro.name):
                self.tree.set(distro.name, "state", state_label(distro))
        self._update_action_states()
        self._show_details()

    def _on_window_active(self, event: Any) -> None:
        """窗口恢复或重新获得焦点：立即刷新一次，再按当前状态重排定时器。"""
        if self._closing or event.widget is not self.root:
            return
        if self.root.state() == "iconic":
            self._cancel_poll()
            return
        self._cancel_poll()
        self._request_status()
        self._schedule_poll()

    def _update_action_states(self) -> None:
        """按选中行的运行状态切换「启动 / 停止」；未选中或任务进行中时都禁用。"""
        if self._busy:
            return
        distro = self._selected()
        can_start, can_stop = action_states(distro.state if distro is not None else None)
        for button, enabled in ((self.start_button, can_start), (self.stop_button, can_stop)):
            button.configure(state="normal" if enabled else "disabled")

    def _save_settings(self) -> None:
        save_settings(
            WslmanwSettings(
                geometry=self.root.geometry(),
                download_dir=self.download_dir.get().strip(),
                mirror=self.mirror_var.get(),
            ),
            self.settings_path,
        )

    def _cancel_task(self) -> None:
        if self._busy:
            self._cancel.set()
            self.status.set(f"{self._label}取消中…")

    def _on_close(self) -> None:
        if self._closing:
            return
        if self._busy:
            if not self.messagebox.askyesno("任务进行中", "有任务正在执行，取消并退出？"):
                return
            self._cancel.set()
        self._closing = True
        self._cancel_poll()
        self._cancel_image_filter()
        self._save_settings()
        self.root.destroy()

    def _cancel_image_filter(self) -> None:
        if self._filter_after is None:
            return
        with contextlib.suppress(self.tk.TclError):
            self.root.after_cancel(self._filter_after)
        self._filter_after = None

    # -- 列表与详情 ---------------------------------------------------- #

    def refresh(self) -> None:
        if self._busy:
            return
        self._start("刷新", lambda _cancel: self._load())

    def _load(self) -> str:
        env = cli._build_environment()
        view = cli._view(env)
        self.view = view
        sizes: dict[str, tuple[int | None, bool]] = {}
        for distro in view.distros:
            sizes[distro.name] = distro_disk_bytes(distro.base_path, distro.version)
        self.sizes = sizes
        return f"已加载 {len(view.distros)} 个发行版"

    def _selected(self) -> Distro | None:
        selection = self.tree.selection()
        if not selection or self.view is None:
            return None
        return self.view.find(selection[0])

    def _show_details(self) -> None:
        """只刷新详情页与状态栏摘要，不切换当前页签。"""
        distro = self._selected()
        if distro is None:
            for variable in self.detail_vars.values():
                variable.set("—")
            self.detail_hint.set("选择上方列表中的发行版查看详情")
            self.hover.set("")
            self._update_action_states()
            return
        size, estimated = self.sizes.get(distro.name, (None, False))
        for label, value in detail_fields(distro, size, estimated):
            self.detail_vars[label].set(value)
        self.detail_hint.set("")
        self.hover.set(summary_text(distro, size, estimated))
        self._update_action_states()

    def _on_hover(self, event: Any) -> None:
        """悬浮显示完整发行版名（列表里过长会截断）。"""
        name = self.tree.identify_row(event.y)
        distro = self.view.find(name) if name and self.view is not None else None
        if distro is None:
            self.hover.set("")
            return
        self.hover.set(summary_text(distro, *self.sizes.get(distro.name, (None, False))))

    def _refresh_rows(self) -> None:
        view = self.view
        if view is None:
            self._rows = []
            self._apply_column_widths(self.tree.winfo_width())
            return
        selected = self.tree.selection()
        self.tree.delete(*self.tree.get_children())
        row_values: list[tuple[str, ...]] = []
        for distro in view.distros:
            row = distro_row(distro, *self.sizes.get(distro.name, (None, False)))
            row_values.append(row)
            self.tree.insert("", "end", iid=distro.name, values=row)
        self._rows = row_values
        self._apply_column_widths(self.tree.winfo_width())
        if selected and self.tree.exists(selected[0]):
            self.tree.selection_set(selected[0])
        self._show_details()

    def _on_tree_resize(self, event: Any) -> None:
        self._apply_column_widths(event.width)

    def _apply_column_widths(self, available: int) -> None:
        """按列表可用宽度分摊列宽；``available`` 无效时只按内容夹取。"""
        widths = column_widths(self._rows, available if available > 1 else None)
        for column, _title, minimum, _maximum in _COLUMNS:
            if int(self.tree.column(column, "width")) != widths[column]:
                self.tree.column(column, width=widths[column], minwidth=minimum)

    def _log(self, text: str) -> None:
        _guiwindow.append_log(self.log, text, timestamp=True)

    # -- 操作 ---------------------------------------------------------- #

    def _open_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        try:
            env = cli._build_environment()
            command = build_command(
                wsl_path=cli._require_wsl(env),
                distro=distro.name,
                cwd=None,
                user=None,
                program=env.config.terminal_program,
                extra_args=env.config.terminal_extra_args,
            )
            self.launcher.launch(command)
        except (LaunchError, WslmanError) as exc:
            self.messagebox.showerror("打开失败", _error_text(exc))
            return
        self._log(f"[打开] 已启动 {distro.name}")

    def _start_selected(self) -> None:
        """后台启动发行版：``--exec`` 执行完即退出，不弹终端窗口。"""
        distro = self._selected()
        if distro is None:
            return
        if distro.state == "running":
            self.status.set(f"{distro.name} 已在运行")
            self._update_action_states()
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            target = cli._require_target(env, distro.name)
            if target.state == "running":
                return f"{target.name} 已在运行"
            # 用一次性命令把发行版带起来；直接 wsl -d 会进交互式 shell 并挂住线程。
            cli._run_step(
                env,
                ["-d", target.name, "--exec", "/bin/true"],
                action=f"启动 {target.name}",
                hint="确认发行版仍已注册；若 WSL 未启用请以管理员运行 wsl --install",
                timeout=120.0,
            )
            current = cli._require_target(env, target.name)
            if current.state != "running":
                raise runtime_error(
                    f"{target.name} 启动后状态仍为 {state_label(current)}",
                    hint="运行 fcmd wslman doctor 检查 WSL 状态，稍后可点「刷新」再次确认",
                )
            return f"已启动 {target.name}"

        self._start("启动", work)

    def _terminate_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        if not self.messagebox.askyesno("停止发行版", f"停止 {distro.name}？正在运行的任务会被中断。"):
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            # CLI 子命令仍是 terminate，只有展示层文案叫「停止」。
            cli._run_step(env, ["--terminate", distro.name], action=f"停止 {distro.name}")
            return f"已停止 {distro.name}"

        self._start("停止", work)

    def _export_selected(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        target = self.filedialog.asksaveasfilename(
            title=f"导出发行版 {distro.name}",
            defaultextension=".tar",
            filetypes=[("tar 归档", "*.tar")],
            initialfile=f"{distro.name}.tar",
        )
        if not target:
            return
        terminate_first = False
        if distro.state == "running":
            answer = self.messagebox.askyesnocancel(
                "发行版运行中", "运行中导出可能不一致。选择「是」先停止，选择「否」接受风险继续？"
            )
            if answer is None:
                return
            terminate_first = answer

        path = Path(target)

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            current = cli._require_target(env, distro.name)
            cli._guard_running_export(
                current,
                terminate_first=terminate_first,
                allow_running=not terminate_first,
                command=f"fcmd wslman export {current.name} {path}",
            )
            estimate, estimated = distro_disk_bytes(current.base_path, current.version)
            check = cli._preflight_for(env, path, estimate, estimated=estimated)
            if not check.ok:
                raise runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个位置或清理磁盘")
            parts = cli._export_parts(path, "none")
            try:
                size, digest = cli._export_stream(
                    env,
                    current.name,
                    path,
                    parts,
                    level=6,
                    terminate_first=terminate_first,
                    estimate=estimate,
                )
            except BaseException:
                cli._abort_parts([item for item in parts if item is not None], keep=env.config.keep_partial)
                raise
            return f"已导出 {path}（{format_bytes(size)}，sha256 {digest}）"

        self._start("导出", work)

    def _install_local(self) -> None:
        """安装磁盘上已有的镜像包：扫描下载目录或手动选包，确认信息后走 ``wsl --import``。

        这是 GUI 里唯一的本地安装入口（工具栏「安装本地镜像…」）；下载页不再重复放按钮。
        """
        if self._busy:
            self.messagebox.showinfo("请稍候", "已有任务在执行")
            return
        tk, ttk, space = self.tk, self.ttk, self.space
        entries = self._catalog_entries()
        window = tk.Toplevel(self.root)
        window.title("安装本地镜像")
        window.transient(self.root)
        window.geometry("820x620")
        window.columnconfigure(0, weight=1)
        window.rowconfigure(2, weight=1)

        top = ttk.Frame(window, padding=space.sm)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="扫描目录").grid(row=0, column=0, sticky="w")
        directory = self.tk.StringVar(value=self._local_start_dir())
        ttk.Entry(top, textvariable=directory).grid(row=0, column=1, sticky="ew", padx=space.xs)

        listing = ttk.Frame(window, padding=(space.sm, 0))
        listing.grid(row=2, column=0, sticky="nsew")
        listing.rowconfigure(0, weight=1)
        listing.columnconfigure(0, weight=1)
        tree = ttk.Treeview(listing, columns=("file", "size", "distro"), show="headings", height=7)
        for column, title, width in (("file", "镜像包", 420), ("size", "大小", 100), ("distro", "识别为", 160)):
            tree.heading(column, text=title)
            tree.column(column, width=width, minwidth=80, anchor="w", stretch=column == "file")
        tree.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(listing, orient="vertical", command=tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)

        info = ttk.Frame(window, padding=(space.sm, space.xs))
        info.grid(row=3, column=0, sticky="ew")
        info.columnconfigure(1, weight=1)
        info_vars = {key: self.tk.StringVar(value="—") for key in ("发行版", "大小", "来源", "校验状态")}
        for row, (key, variable) in enumerate(info_vars.items()):
            ttk.Label(info, text=key, style="Muted.TLabel").grid(row=row, column=0, sticky="nw", padx=(0, space.xs))
            ttk.Label(info, textvariable=variable, justify="left", anchor="nw", wraplength=640).grid(
                row=row, column=1, sticky="ew"
            )

        form = ttk.Frame(window, padding=(space.sm, space.xs))
        form.grid(row=4, column=0, sticky="ew")
        form.columnconfigure(1, weight=1)
        ttk.Label(form, text="发行版名称").grid(row=0, column=0, sticky="w")
        name_var = self.tk.StringVar(value="")
        ttk.Entry(form, textvariable=name_var).grid(row=0, column=1, sticky="ew", padx=space.xs)
        ttk.Label(form, text="安装目录").grid(row=1, column=0, sticky="w")
        dir_var = self.tk.StringVar(value="")
        ttk.Entry(form, textvariable=dir_var).grid(row=1, column=1, sticky="ew", padx=space.xs)
        replace_var = self.tk.BooleanVar(value=False)
        ttk.Checkbutton(form, text="替换同名发行版（会先注销，必要时先备份）", variable=replace_var).grid(
            row=2, column=1, sticky="w", pady=(space.xxs, 0)
        )

        footer = ttk.Frame(window, padding=(space.sm, space.xs))
        footer.grid(row=5, column=0, sticky="ew")

        archives: dict[str, LocalArchive] = {}

        def add(archive: LocalArchive) -> None:
            iid = tree.insert("", "end", values=(str(archive.path), format_bytes(archive.size), archive.distro))
            archives[iid] = archive

        def scan() -> None:
            tree.delete(*tree.get_children())
            archives.clear()
            for path in local_archives(Path(directory.get().strip() or ".")):
                add(inspect_archive(path, entries))
            if not archives:
                info_vars["校验状态"].set("该目录没有 .tar / .tar.gz / .tgz / .wsl 镜像包，可用「选择文件…」指定")
            else:
                first = tree.get_children()[0]
                tree.selection_set(first)
                tree.focus(first)

        def add_file() -> None:
            chosen = self.filedialog.askopenfilename(
                title="选择本地镜像包",
                filetypes=[("镜像包", "*.tar;*.tar.gz;*.tgz;*.wsl"), ("全部文件", "*.*")],
            )
            if not chosen:
                return
            archive = inspect_archive(Path(chosen), entries)
            add(archive)
            tree.selection_set(tree.get_children()[-1])

        def choose_dir() -> None:
            chosen = self.filedialog.askdirectory(title="选择安装目录（必须为空）")
            if chosen:
                dir_var.set(chosen)

        def on_select(_event: object = None) -> None:
            selection = tree.selection()
            if not selection:
                return
            archive = archives[selection[0]]
            arch = archive.arch if archive.arch != "未知" else "架构未知"
            info_vars["发行版"].set(f"{archive.distro}（{arch}）")
            info_vars["大小"].set(format_bytes(archive.size))
            info_vars["来源"].set(archive.source)
            if archive.expected_sha256 is None:
                info_vars["校验状态"].set(verification_label(None, None))
            else:
                info_vars["校验状态"].set("正在计算 sha256…")
                window.update_idletasks()
                info_vars["校验状态"].set(verification_label(archive.expected_sha256, sha256_file(archive.path)))
            name_var.set(archive.distro)
            dir_var.set(str(archive.path.parent / f"{archive.distro}-wsl"))

        def start() -> None:
            selection = tree.selection()
            if not selection:
                self.messagebox.showerror("安装本地镜像", "请先选择一个镜像包")
                return
            archive = archives[selection[0]]
            name = name_var.get().strip()
            target_text = dir_var.get().strip()
            if not name:
                self.messagebox.showerror("安装本地镜像", "请填写发行版名称")
                return
            if not target_text:
                self.messagebox.showerror("安装本地镜像", "请选择安装目录")
                return
            replace = replace_var.get()
            if replace and not self.messagebox.askyesno(
                "替换发行版", f"替换 {name} 会先注销现有发行版（运行中会先停止），继续？"
            ):
                return
            backup: Path | None = None
            if replace and load_config().require_backup_on_replace:
                chosen = self.filedialog.asksaveasfilename(
                    title="替换前先备份到", defaultextension=".tar", initialfile=f"{name}.tar"
                )
                if not chosen:
                    return
                backup = Path(chosen)
            window.destroy()
            self._install_archive(archive.path, name, Path(target_text), replace=replace, backup=backup)

        ttk.Button(top, text="选择文件…", command=add_file).grid(row=0, column=2, padx=space.xxs)
        ttk.Button(top, text="重新扫描", command=scan).grid(row=0, column=3)
        ttk.Button(form, text="选择…", command=choose_dir).grid(row=1, column=2)
        ttk.Button(footer, text="开始安装", command=start, style="Accent.TButton").pack(side="left")
        ttk.Button(footer, text="取消", command=window.destroy).pack(side="left", padx=(space.xs, 0))

        tree.bind("<<TreeviewSelect>>", on_select)
        tree.bind("<Double-1>", lambda _event: start())
        scan()

    def _install_archive(self, archive: Path, name: str, target: Path, *, replace: bool, backup: Path | None) -> None:
        """工作线程执行 ``wsl --import``；替换沿用 CLI 的 ``_guard_replace`` 与备份顺序。"""

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            existing = cli._view(env).find(name)
            command = f'fcmd wslman import {name} "{archive}" --dir "{target}"'
            if existing is not None:
                if not replace:
                    raise invalid_argument(
                        f"发行版 {name} 已存在",
                        hint=f"勾选「替换同名发行版」后重试，或改用 CLI：{command} --replace --yes",
                    )
                running = existing.state == "running"
                steps = [
                    step for step, flag in (("停止", running), ("备份", backup is not None), ("注销", True)) if flag
                ]
                self._events.put(("log", "安装", f"替换 {existing.name}：" + " → ".join(steps) + " → 重新导入"))
                cli._guard_replace(
                    env, existing=existing, yes=True, backup=backup, terminate_first=running, command=command
                )
                if running:
                    cli._run_step(env, ["--terminate", existing.name], action=f"停止 {existing.name}")
                if backup is not None:
                    cli._run_step(
                        env,
                        ["--export", existing.name, str(backup)],
                        action=f"备份 {existing.name}",
                        hint="备份失败时不会注销旧发行版",
                        timeout=3600.0,
                    )
                cli._run_step(env, ["--unregister", existing.name], action=f"注销 {existing.name}")
            # 目录校验放在替换处理之后：替换会注销并清空旧目录，先校验会把「已存在」误报成目录问题。
            problem = cli._dir_problem(target)
            if problem:
                raise invalid_argument(f"安装目录不可用：{problem}", hint="选择一个空目录（不存在会自动创建）")
            self._events.put(("log", "安装", f"导入 {archive.name} → {target}"))
            cli._run_step(
                env,
                ["--import", name, str(target), str(archive), "--version", "2"],
                action=f"安装 {name}",
                hint="目标目录必须为空；WSL1 镜像请改用 CLI 指定 --version 1",
                timeout=3600.0,
            )
            return f"已安装 {name} 到 {target}（{archive.name}，{format_bytes(archive.stat().st_size)}）"

        self._start("安装", work)

    def _catalog_entries(self) -> tuple[_images.ImageEntry, ...]:
        """已知镜像清单：优先本次会话从下载页加载的结果，否则只读缓存。"""
        if self.image_entries:
            return self.image_entries
        cache = _images.load_cache()
        return cache.entries if cache is not None else ()

    def _local_start_dir(self) -> str:
        """首次扫描目录：高级里填的下载目录 → 上次保存的设置 → wslman.json → 系统下载目录。"""
        configured = self.download_dir.get().strip() or self.settings.download_dir
        if configured:
            return configured
        fallback = load_config().download_dir.strip()
        return fallback or str(Path.home() / "Downloads")

    def _download(self) -> None:
        entry = self._selected_image()
        if entry is None:
            self.messagebox.showerror("下载", "请先在列表中选择镜像")
            return
        directory_text = self.download_dir.get().strip()
        expected = self.expected_sha.get().strip() or None
        install_after = self.install_after.get()
        # Tk 变量只能在主线程读；工作线程里再读会抛 "main thread is not in main loop"。
        mirror_policy = self.mirror_var.get()
        install_name = entry.name
        install_dir: Path | None = None
        if install_after:
            # 命名与安装位置必须在主线程问完：工作线程里不能再弹对话框。
            chosen_name = self.simpledialog.askstring("下载后安装", "新发行版名称", initialvalue=entry.name)
            if not chosen_name:
                return
            chosen_dir = self.filedialog.askdirectory(title="选择安装目录（必须为空）")
            if not chosen_dir:
                return
            install_name, install_dir = chosen_name, Path(chosen_dir)

        def work(cancel: threading.Event) -> str:
            env = cli._build_environment()
            if not entry.downloads:
                # 只有 CLI / Store 来源：走 wsl --install，不下载归档。
                cli._run_step(
                    env,
                    ["--install", "-d", entry.name, "--no-launch"],
                    action=f"安装 {entry.name}",
                    hint="系统级安装可能弹 UAC；失败时检查网络或 WSL 版本",
                    timeout=3600.0,
                )
                return f"已通过 wsl --install 安装 {entry.name}；用「打开终端」启动"

            from fcmd.apps.wslman import _source

            download = pick_download(entry)
            directory = Path(directory_text) if directory_text else Path(env.config.download_dir or ".")
            target = directory / image_filename(entry.name, download)
            if target.exists():
                raise invalid_argument(f"目标文件已存在：{target}", hint="换一个目录或先删除旧文件")
            check = cli._preflight_for(env, target, None, estimated=True)
            if not check.ok:
                raise runtime_error(f"目标卷空间不足：{check.detail}", hint="换一个目录")
            result_data = _source.download_mirrored(
                target,
                candidates=_mirrors.candidates(
                    download.url,
                    policy=mirror_policy,
                    verifiable=bool(expected or download.sha256),
                ),
                expected_sha256=expected or download.sha256,
                cancelled=cancel.is_set,
            )
            summary = (
                f"已下载 {target}（{format_bytes(result_data.size)}，"
                f"来源 {_mirrors.label(result_data.source)}，sha256 {result_data.sha256}）"
            )
            if not install_after:
                return summary
            if install_dir is None:  # 上面已要求选择目录，这里只做类型收窄
                raise invalid_argument("未选择安装目录")
            # 下载完直接装：目录必须为空，同名发行版已存在时明确报错并给出 CLI 替换命令，
            # 不静默覆盖；进度通过日志行进到界面（导入期间 wsl.exe 不产出可读进度）。
            self._events.put(("log", "下载", f"下载完成，开始安装为 {install_name}…"))
            problem = cli._dir_problem(install_dir)
            if problem:
                raise invalid_argument(
                    f"安装目录不可用：{problem}",
                    hint=f'删空或换目录后重试；也可只下载，再运行 fcmd wslman import {install_name} "{target}" --dir <空目录>',
                )
            view = cli._view(env)
            if view.find(install_name) is not None:
                raise invalid_argument(
                    f"发行版 {install_name} 已存在，未自动覆盖",
                    hint=(
                        f'如需替换：fcmd wslman import {install_name} "{target}" --dir "{install_dir}" '
                        "--replace --yes --backup <备份>"
                    ),
                )
            cli._run_step(
                env,
                ["--import", install_name, str(install_dir), str(target), "--version", "2"],
                action=f"安装 {install_name}",
                hint="目标目录必须为空；失败时用工具栏的「安装本地镜像…」重试",
                timeout=3600.0,
            )
            return f"{summary}；已安装为 {install_name}（{install_dir}），刷新后可在列表启动"

        self._start("下载", work)

    # -- 镜像列表（下载页） -------------------------------------------- #

    def _on_mirror_changed(self, _event: Any) -> None:
        """切换下载源：只改本次会话的策略，关窗时随其它设置一起落盘。"""
        index = self.mirror_combo.current()
        self.mirror_var.set(self.mirror_ids[index] if 0 <= index < len(self.mirror_ids) else "auto")
        self.status.set(f"下载源：{_mirrors.label(self.mirror_var.get())}")

    def _on_tab_changed(self, _event: Any) -> None:
        if self.notebook.tab("current", "text") == "下载":
            self._load_images()

    def _load_images(self) -> None:
        """后台拉取镜像清单；本页重复进入时复用本次会话结果。"""
        if self._images_busy or self.image_entries or self._closing:
            return
        self._images_busy = True
        self.images_status.set("正在获取镜像清单…（wsl --list --online 首次较慢，可用缓存兜底）")
        threading.Thread(target=self._images_worker, daemon=True).start()

    def _images_worker(self) -> None:
        try:
            env = cli._build_environment()
            catalog = _images.collect_images(
                fetch_cli=lambda: cli._online_rows(env),
                fetch_manifest=cli._manifest_images,
            )
            entries = _images.mark_local_state(
                catalog.entries,
                installed=cli._installed_names(env),
                backups=cli._backup_names(env),
            )
        except Exception as exc:
            self._events.put(("images_error", "镜像列表", exc))
        else:
            self._events.put(("images", "镜像列表", (catalog, entries)))

    def _apply_images(self, catalog: Any, entries: tuple[_images.ImageEntry, ...]) -> None:
        self.image_entries = entries
        stamp = time.strftime("%H:%M", time.localtime(catalog.fetched_at))
        marks = f"{len(entries)} 个镜像 · {(catalog.sources and '+'.join(catalog.sources)) or '缓存'} · {stamp}"
        if catalog.from_cache:
            marks += "（过期缓存）" if catalog.stale else "（缓存）"
        self.images_status.set(marks)
        for warning in catalog.warnings:
            self._log(f"[镜像列表] {warning}")
        self._render_images()

    def _render_images(self) -> None:
        """按搜索词重建可见行；条目量级只有几十条，重建比逐行 diff 更简单可靠。"""
        query = self.image_query.get().strip().casefold()
        selected = self.image_tree.selection()
        keep = selected[0] if selected else None
        self.image_tree.delete(*self.image_tree.get_children())
        for entry in self.image_entries:
            if query and query not in entry.name.casefold() and query not in entry.friendly_name.casefold():
                continue
            self.image_tree.insert("", "end", iid=entry.name, values=image_row(entry))
        if keep and self.image_tree.exists(keep):
            self.image_tree.selection_set(keep)
            self.image_tree.see(keep)
        else:
            self._show_image_details()

    def _schedule_image_filter(self, _event: Any) -> None:
        """按键后 150 ms 再过滤，连续输入时只重绘一次。"""
        self._cancel_image_filter()
        self._filter_after = self.root.after(150, self._apply_image_filter)

    def _apply_image_filter(self) -> None:
        self._filter_after = None
        self._render_images()

    def _selected_image(self) -> _images.ImageEntry | None:
        selected = self.image_tree.selection()
        if not selected:
            return None
        name = selected[0]
        return next((entry for entry in self.image_entries if entry.name == name), None)

    def _on_image_detail_resize(self, event: Any) -> None:
        """详情区跟随分配宽度换行；下载地址走文本域滚动，不参与宽度竞争。"""
        wrap = max(120, event.width - 96)
        for label in self.image_detail_labels:
            label.configure(wraplength=wrap)

    def _show_image_details(self) -> None:
        entry = self._selected_image()
        values = dict(image_details(entry)) if entry is not None else {}
        for label, variable in self.image_detail_vars.items():
            variable.set(values.get(label, "—"))
        self.image_url_text.configure(state="normal")
        self.image_url_text.delete("1.0", "end")
        self.image_url_text.insert("1.0", values.get("下载地址", "—"))
        self.image_url_text.configure(state="disabled")

    def _unregister(self) -> None:
        distro = self._selected()
        if distro is None:
            return
        dialog = _UnregisterDialog(self, distro)
        self.root.wait_window(dialog.window)
        if not dialog.confirmed:
            return
        backup = dialog.backup_path()
        terminate_first = dialog.terminate_first.get()

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            target = cli._require_target(env, distro.name)
            if target.state == "running" and not terminate_first:
                raise invalid_argument(f"{target.name} 正在运行，请勾选「先停止」")
            estimate, estimated = distro_disk_bytes(target.base_path, target.version)
            with cli.TransferLock(target.name, directory=env.lock_dir):
                if backup is not None:
                    cli._export_for_backup(env, target.name, backup, estimate, estimated=estimated)
                if terminate_first:
                    cli._run_step(env, ["--terminate", target.name], action=f"停止 {target.name}")
                cli._run_step(env, ["--unregister", target.name], action=f"注销 {target.name}")
            return f"已注销 {target.name}" + (f"；备份在 {backup}" if backup else "")

        self._start("注销", work)

    def _shutdown(self) -> None:
        dialog = _ShutdownDialog(self)
        self.root.wait_window(dialog.window)
        if not dialog.confirmed:
            return

        def work(_cancel: threading.Event) -> str:
            env = cli._build_environment()
            cli._run_step(env, ["--shutdown"], action="wsl --shutdown")
            return "已关闭全部发行版与 WSL 虚拟机"

        self._start("关闭全部", work)


class _UnregisterDialog:  # pragma: no cover - 需要真实 Tk 窗口
    """注销确认：手输发行版名、可选先停止、可选先备份。"""

    def __init__(self, owner: WslmanWindow, distro: Distro) -> None:
        tk, ttk = owner.tk, owner.ttk
        self.confirmed = False
        self.distro = distro
        self.filedialog = owner.filedialog
        window = tk.Toplevel(owner.root)
        self.window = window
        window.title(f"注销 {distro.name}")
        window.transient(owner.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)

        estimate, estimated = owner.sizes.get(distro.name, (None, False))
        ttk.Label(
            frame,
            text=(
                f"BasePath: {distro.base_path or '未知'}\n"
                f"占用: {format_bytes(estimate, estimated=estimated)}\n"
                "虚拟磁盘删除后无回收站。"
            ),
            justify="left",
        ).pack(anchor="w")

        self.terminate_first = tk.BooleanVar(value=distro.state == "running")
        ttk.Checkbutton(frame, text="先停止发行版", variable=self.terminate_first).pack(anchor="w", pady=4)

        self.backup_enabled = tk.BooleanVar(value=owner.config_data.require_backup_on_replace)
        ttk.Checkbutton(frame, text="先导出备份到", variable=self.backup_enabled).pack(anchor="w")
        default_backup = recommended_backup_dir() / f"{distro.name}.tar"
        self.backup = tk.StringVar(value=str(default_backup))
        row = ttk.Frame(frame)
        row.pack(fill="x", pady=4)
        ttk.Entry(row, textvariable=self.backup).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="选择…", command=self._choose_backup).pack(side="left", padx=(4, 0))

        ttk.Label(frame, text=f"手输发行版名以确认：{distro.name}").pack(anchor="w", pady=(8, 2))
        self.typed = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=self.typed)
        entry.pack(fill="x")
        entry.focus_set()

        self.confirm_button = ttk.Button(frame, text="注销", command=self._confirm, state="disabled")
        self.confirm_button.pack(anchor="e", pady=(10, 0))
        ttk.Button(frame, text="取消", command=window.destroy).pack(anchor="e")
        self.typed.trace_add("write", lambda *_: self._sync())
        self._sync()

    def _sync(self) -> None:
        self.confirm_button.configure(
            state="normal" if unregister_confirm_enabled(self.typed.get(), self.distro.name) else "disabled"
        )

    def _choose_backup(self) -> None:
        chosen = self.filedialog.asksaveasfilename(
            parent=self.window,
            title="选择备份文件",
            defaultextension=".tar",
            filetypes=[("tar 归档", "*.tar")],
            initialfile=f"{self.distro.name}.tar",
        )
        if chosen:
            self.backup.set(chosen)

    def backup_path(self) -> Path | None:
        return Path(self.backup.get().strip()) if self.backup_enabled.get() and self.backup.get().strip() else None

    def _confirm(self) -> None:
        if not unregister_confirm_enabled(self.typed.get(), self.distro.name):
            return
        self.confirmed = True
        self.window.destroy()


class _ShutdownDialog:  # pragma: no cover - 需要真实 Tk 窗口
    """``wsl --shutdown`` 确认：必须勾选影响说明。"""

    def __init__(self, owner: WslmanWindow) -> None:
        tk, ttk = owner.tk, owner.ttk
        self.confirmed = False
        window = tk.Toplevel(owner.root)
        self.window = window
        window.title("关闭全部 WSL")
        window.transient(owner.root)
        window.grab_set()
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="会停止全部发行版与 WSL 虚拟机，并连带停掉 Docker Desktop、IDE Remote 等后端。",
            wraplength=420,
            justify="left",
        ).pack(anchor="w")
        self.checked = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="我了解上述影响", variable=self.checked).pack(anchor="w", pady=8)
        self.confirm_button = ttk.Button(frame, text="关闭全部", command=self._confirm, state="disabled")
        self.confirm_button.pack(anchor="e")
        ttk.Button(frame, text="取消", command=window.destroy).pack(anchor="e")
        self.checked.trace_add("write", lambda *_: self._sync())

    def _sync(self) -> None:
        self.confirm_button.configure(state="normal" if shutdown_confirm_enabled(self.checked.get()) else "disabled")

    def _confirm(self) -> None:
        if not shutdown_confirm_enabled(self.checked.get()):
            return
        self.confirmed = True
        self.window.destroy()


def run_gui() -> None:
    """创建并运行窗口；tkinter 缺失时由 ``run_window`` 提示后返回。"""
    _guiwindow.run_window(WslmanWindow, "WSL 管理")


@fcmd.tool("wslmanw", help="打开 WSL 发行版管理窗口（Tk）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 WSL 发行版管理窗口。"""
    run_gui()


@fcmd.main("wslmanw")
def main() -> None:
    """``wslmanw`` 命令入口。"""
    launch_cmd()
