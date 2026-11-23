"""wslmanw - WSL 实例管理的 Qt 窗口。

窗口只做编排：业务判断仍走 :mod:`fcmd.apps.wslman.wslman` 与
:mod:`fcmd.apps.wslman._source` 的共享实现（导出核心、预检、删除实例前备份），
避免 GUI 与 CLI 两套口径。

设置文件：``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``。
"""

from __future__ import annotations

import ntpath
import os
import platform
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.wslman import _images, _initenv, _install, _mirrors, _open, _sshfiles
from fcmd.apps.wslman._config import work_root
from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._distro import Distro, DistroView, state_label
from fcmd.apps.wslman._errors import WslmanError
from fcmd.gui import config as _guiconfig
from fcmd.gui import geometry as _geometry
from fcmd.gui.settings import SettingsStore
from fcmd.infras.download import format_speed

__all__ = [
    "DEFAULT_MAX_DOWNLOADS",
    "DEFAULT_TERMINAL_CWD",
    "IMAGE_FILTER_HINTS",
    "IMAGE_MARK_HINTS",
    "MAX_DOWNLOADS",
    "MIN_DOWNLOADS",
    "MIN_HEIGHT",
    "MIN_WIDTH",
    "SETTINGS_NAME",
    "WslmanwSettings",
    "action_states",
    "backup_names",
    "config_path",
    "copy_default_dir",
    "copy_default_name",
    "copy_name_problem",
    "derived_dirs",
    "detail_fields",
    "dialog_geometry",
    "distro_image_text",
    "download_limit",
    "download_metrics_text",
    "download_progress_text",
    "download_queue_text",
    "env_status_for",
    "export_progress_text",
    "filter_images",
    "has_distro_files",
    "hidden_openers",
    "image_counts",
    "image_details",
    "image_filename",
    "image_item_lines",
    "image_marks",
    "image_pin_rank",
    "image_sort_key",
    "image_source_text",
    "image_status_text",
    "init_task_keys",
    "legacy_dir_note",
    "load_settings",
    "local_arch",
    "main",
    "mirror_lines",
    "mirror_sources",
    "opener_paths_for",
    "openers_for",
    "package_mirror_for",
    "pick_download",
    "reconcile_distros",
    "resolve_geometry",
    "same_dir",
    "save_settings",
    "scan_install_dirs",
    "selected_opener",
    "settings_from_mapping",
    "settings_to_mapping",
    "shutdown_confirm_enabled",
    "sort_images",
    "ssh_status_for",
    "summary_text",
    "terminal_cwd",
    "unregister_confirm_enabled",
    "with_dialog_geometry",
    "with_env_status",
    "with_init_task_keys",
    "with_max_downloads",
    "with_opener_paths",
    "with_package_mirror",
    "with_ssh_status",
    "with_terminal_cwd",
    "with_work_dir",
    "with_work_dirs",
    "work_dir_of",
    "work_dir_path",
    "work_dir_paths",
]

SETTINGS_NAME = "wslmanw"
DEFAULT_TERMINAL_CWD = "~"
"""「打开终端」的默认工作目录：Linux 用户主目录（对应 ``wsl --cd ~``）。"""
DEFAULT_GEOMETRY = "1160x760"
MIN_WIDTH = 900
MIN_HEIGHT = 600

MIN_DOWNLOADS = 1
MAX_DOWNLOADS = 4
DEFAULT_MAX_DOWNLOADS = 2
"""镜像并发下载数：默认 2 个，够吃带宽又不会把磁盘 / 网络打满。

上限刻意保守（4 个）：镜像归档动辄几百 MB，开太多会互相抢带宽、拖慢每个任务，
也让「取消」与进度展示更难读。设置页可调，越界值读入时收敛到这个区间。
"""

_DESCRIPTION = "WSL 实例管理的图形界面：列表/详情、打开终端、启动重启停止、导出导入、镜像下载、删除实例与关闭全部（Qt）"

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
    ("download", "可下载"),
    ("installed", "已安装"),
    ("backed_up", "已备份"),
)
"""镜像页的筛选按钮。

只保留四个互不重叠的口径：能否下载到本地、是否已安装、是否已备份。
「可直链下载 / 仅安装」原来是一对互补项——「仅安装」只是「可下载」的取反，
既不能缩小范围也不表示用户的一种意图，已合并：没有直链归档的条目仍出现在
「全部」里，行内标注「仅 wsl --install」，下载能力不再单独做成筛选项。
"""

IMAGE_FILTER_HINTS: dict[str, str] = {
    "all": "官方清单与 wsl --list --online 列出的全部镜像",
    "download": "有可直链归档：能下载到本地后再安装（没有直链的条目改用 wsl --install）",
    "installed": "本机已注册的 WSL 实例",
    "backed_up": "备份目录里有同名导出包（<镜像名>.tar / .tar.gz）；可在实例页用「备份」生成并恢复",
}
"""筛选按钮的 tooltip：把每个口径写清楚，避免「可直链下载 / 仅安装」这类歧义。"""

IMAGE_MARK_HINTS: dict[str, str] = {
    "已安装": "本机已有同名的 WSL 实例",
    "已下载": "下载目录里已有该镜像的归档文件",
    "已备份": "备份目录里有同名导出包（<镜像名>.tar / .tar.gz）；在实例页点「备份」生成",
    "sha256": "清单提供校验和：下载后会自动校验",
    "无 sha256": "清单没有校验和：下载后无法标记 verified",
}
"""行内状态胶囊的含义说明（挂在胶囊 tooltip 上）。"""

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
    "镜像源",
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
    """状态栏的完整实例摘要；列表里截断的名称在这里显示全名。"""
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
    work_dir: str = ""
    """工作目录；留空用 ``${FCMD_HOME:-~/.fcmd}/wsl``。

    下载 / 安装 / 备份都按它推演子目录，不再单独配置下载目录与备份目录。
    """
    work_dirs: tuple[str, ...] = ()
    """全部工作目录；首项是默认目录，新下载 / 新安装落在它的子目录。

    留空表示只有 ``work_dir``（或回落的默认目录）这一个；旧版单目录配置读入时
    会迁移成一条记录并设为默认，不让老用户丢设置。
    """
    download_dir: str = ""
    """旧版的下载目录覆盖值：仅用于读入旧设置并给迁移提示，界面不再写入。"""
    backup_dir: str = ""
    """旧版的备份目录覆盖值：仅用于读入旧设置并给迁移提示，界面不再写入。"""
    mirror: str = "auto"
    max_downloads: int = DEFAULT_MAX_DOWNLOADS
    """镜像并发下载上限；队列里超出上限的条目保持「排队中」。"""
    sidebar_collapsed: bool = False
    image_filter: str = DEFAULT_IMAGE_FILTER
    image_sort: str = DEFAULT_IMAGE_SORT
    image_source: str = DEFAULT_IMAGE_SOURCE
    distro_terminal_dirs: Mapping[str, str] = field(default_factory=dict)
    """按实例名保存的终端默认目录；缺省 / ``~`` 时由 :data:`DEFAULT_TERMINAL_CWD` 接管。"""
    dialog_geometry: Mapping[str, str] = field(default_factory=dict)
    """按对话框名保存的几何：下次打开还原上次的尺寸与位置。"""
    distro_init_tasks: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """按实例名保存的初始化勾选；未记录时按探测结果默认勾选。"""
    distro_package_mirrors: Mapping[str, str] = field(default_factory=dict)
    """按实例名保存的「软件包源」：独立于 APT / YUM 系统镜像源，只影响 pip / uv。"""
    ide_openers: tuple[_open.Opener, ...] = field(default_factory=lambda: _open.default_openers(_open.KIND_IDE))
    """IDE 打开方式条目；默认是内置的 VSCode / Trae / Qoder / WorkBuddy。"""
    agent_openers: tuple[_open.Opener, ...] = field(default_factory=lambda: _open.default_openers(_open.KIND_AGENT))
    """AI Agent 打开方式条目；默认是内置的 PI / OpenCode。"""
    ide_opener: str = "vscode"
    """当前选用的 IDE 条目 key。"""
    agent_opener: str = "opencode"
    """当前选用的 AI Agent 条目 key。"""
    hidden_openers: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    """用户显式删除的内置打开方式 key（按 IDE / Agent 分类），防止新版本迁移时又加回来。"""
    distro_env_status: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    """按实例名持久化的「初始化环境」上次探测结果；打开配置时先回显，避免重复启动。"""
    distro_opener_paths: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    """按实例名持久化的 WSL 内可执行文件路径（``command -v`` 原样结果）。"""
    distro_ssh_status: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    """按实例名持久化的「迁移 SSH 文件」上次扫描状态；打开配置时直接回显，不再重扫。"""


def settings_to_mapping(settings: WslmanwSettings) -> dict[str, object]:
    return {
        "geometry": settings.geometry,
        "work_dir": settings.work_dir,
        "work_dirs": list(settings.work_dirs),
        "download_dir": settings.download_dir,
        "backup_dir": settings.backup_dir,
        "mirror": settings.mirror,
        "max_downloads": settings.max_downloads,
        "sidebar_collapsed": settings.sidebar_collapsed,
        "image_filter": settings.image_filter,
        "image_sort": settings.image_sort,
        "image_source": settings.image_source,
        "distro_terminal_dirs": dict(settings.distro_terminal_dirs),
        "dialog_geometry": dict(settings.dialog_geometry),
        "distro_init_tasks": {name: list(keys) for name, keys in settings.distro_init_tasks.items()},
        "distro_package_mirrors": dict(settings.distro_package_mirrors),
        "distro_env_status": {name: dict(values) for name, values in settings.distro_env_status.items()},
        "distro_opener_paths": {name: dict(values) for name, values in settings.distro_opener_paths.items()},
        "distro_ssh_status": {name: dict(values) for name, values in settings.distro_ssh_status.items()},
        "openers": {
            "ide": [_open.to_mapping(item) for item in settings.ide_openers],
            "agent": [_open.to_mapping(item) for item in settings.agent_openers],
            "selected": {"ide": settings.ide_opener, "agent": settings.agent_opener},
            "hidden": {kind: list(keys) for kind, keys in settings.hidden_openers.items() if keys},
        },
    }


def settings_from_mapping(raw: Mapping[str, Any]) -> WslmanwSettings:
    # 旧版本写过 manifest_url / download_source / auto_refresh：按未知字段忽略。
    mirror = _str(raw, "mirror")
    collapsed = raw.get("sidebar_collapsed")
    openers = raw.get("openers")
    openers_map = openers if isinstance(openers, Mapping) else {}
    hidden = _hidden_openers(openers_map.get("hidden"))
    ide_openers = _openers(openers_map.get("ide"), _open.KIND_IDE, hidden.get(_open.KIND_IDE, ()))
    agent_openers = _openers(openers_map.get("agent"), _open.KIND_AGENT, hidden.get(_open.KIND_AGENT, ()))
    selected = openers_map.get("selected")
    selected_map = selected if isinstance(selected, Mapping) else {}
    work_dir, work_dirs = _work_dirs(_str(raw, "work_dir"), raw.get("work_dirs"))
    return WslmanwSettings(
        geometry=_guiconfig.valid_geometry(_str(raw, "geometry")),
        work_dir=work_dir,
        work_dirs=work_dirs,
        download_dir=_str(raw, "download_dir"),
        backup_dir=_str(raw, "backup_dir"),
        mirror=mirror if mirror in _mirrors.identifiers() else "auto",
        max_downloads=download_limit(raw.get("max_downloads")),
        sidebar_collapsed=collapsed is True,
        image_filter=_choice(raw, "image_filter", IMAGE_FILTERS, DEFAULT_IMAGE_FILTER),
        image_sort=_choice(raw, "image_sort", IMAGE_SORTS, DEFAULT_IMAGE_SORT),
        image_source=_choice(raw, "image_source", IMAGE_SOURCES, DEFAULT_IMAGE_SOURCE),
        distro_terminal_dirs=_terminal_dirs(raw.get("distro_terminal_dirs")),
        dialog_geometry=_dialog_geometries(raw.get("dialog_geometry")),
        distro_init_tasks=_init_tasks(raw.get("distro_init_tasks")),
        distro_package_mirrors=_package_mirrors(raw.get("distro_package_mirrors")),
        distro_env_status=_status_cache(raw.get("distro_env_status")),
        distro_opener_paths=_opener_path_cache(raw.get("distro_opener_paths")),
        distro_ssh_status=_ssh_status_cache(raw.get("distro_ssh_status")),
        ide_openers=ide_openers,
        agent_openers=agent_openers,
        ide_opener=_selected_key(selected_map.get("ide"), ide_openers, kind=_open.KIND_IDE),
        agent_opener=_selected_key(selected_map.get("agent"), agent_openers, kind=_open.KIND_AGENT),
        hidden_openers=hidden,
    )


def _hidden_openers(raw: Any) -> dict[str, tuple[str, ...]]:
    """解析已删除的内置条目 key；只接受已知分类与字符串列表。"""
    if not isinstance(raw, Mapping):
        return {}
    hidden: dict[str, tuple[str, ...]] = {}
    for kind in _open.KINDS:
        values = raw.get(kind)
        if not isinstance(values, list):
            continue
        keys = tuple(dict.fromkeys(item.strip() for item in values if isinstance(item, str) and item.strip()))
        if keys:
            hidden[kind] = keys
    return hidden


def _openers(raw: Any, kind: str, hidden: Sequence[str] = ()) -> tuple[_open.Opener, ...]:
    """缺字段（旧设置文件）回落内置预设；显式空列表表示用户删光了。

    旧设置里的列表如果还保留任意内置条目，就把新版本新增的内置预设补到末尾：
    TRAE CN / QODER CN / DSH 这类后加的预设不该因为老设置文件而永久缺席。
    用户显式删除过的 key 记录在 ``hidden``，迁移时跳过，避免删了又自己回来。
    """
    if not isinstance(raw, list):
        return _open.default_openers(kind)
    parsed = _open.parse_mappings(raw, kind)
    if not parsed or not any(item.builtin for item in parsed):
        return parsed
    existing = {item.key for item in parsed}
    hidden_set = set(hidden)
    return (
        *parsed,
        *(
            blueprint
            for blueprint in _open.default_openers(kind)
            if blueprint.key not in existing and blueprint.key not in hidden_set
        ),
    )


def _selected_key(raw: Any, openers: Sequence[_open.Opener], *, kind: str) -> str:
    """选中项：保存值有效就用它；否则优先内置默认条目，再回落第一个。"""
    keys = [item.key for item in openers]
    if isinstance(raw, str) and raw in keys:
        return raw
    preferred = _open.DEFAULT_KEYS.get(kind, "")
    if preferred in keys:
        return preferred
    return keys[0] if keys else ""


def openers_for(settings: WslmanwSettings, kind: str) -> tuple[_open.Opener, ...]:
    return settings.ide_openers if kind == _open.KIND_IDE else settings.agent_openers


def hidden_openers(settings: WslmanwSettings, kind: str) -> tuple[str, ...]:
    """用户显式删除过的内置条目 key；设置页删除时写入，迁移新增预设时跳过。"""
    return settings.hidden_openers.get(kind, ())


def selected_opener(settings: WslmanwSettings, kind: str) -> _open.Opener | None:
    """当前选用的条目；设置里的 key 失效时回落到第一个，列表为空时返回 ``None``。"""
    openers = openers_for(settings, kind)
    key = settings.ide_opener if kind == _open.KIND_IDE else settings.agent_opener
    return next((item for item in openers if item.key == key), openers[0] if openers else None)


def with_openers(
    settings: WslmanwSettings,
    kind: str,
    openers: Sequence[_open.Opener],
    selected: str,
    hidden: Sequence[str] | None = None,
) -> WslmanwSettings:
    """写回某一类的条目与选中项；选中项失效时回落到第一个（列表为空则留空）。

    ``hidden`` 为该分类显式删除的内置 key；``None`` 表示保持原记录不变。
    """
    items = tuple(openers)
    key = _selected_key(selected, items, kind=kind)
    hidden_map = dict(settings.hidden_openers)
    if hidden is not None:
        keys = tuple(dict.fromkeys((*hidden_map.get(kind, ()), *(item.strip() for item in hidden if item.strip()))))
        if keys:
            hidden_map[kind] = keys
        else:
            hidden_map.pop(kind, None)
    if kind == _open.KIND_IDE:
        return replace(settings, ide_openers=items, ide_opener=key, hidden_openers=hidden_map)
    return replace(settings, agent_openers=items, agent_opener=key, hidden_openers=hidden_map)


def env_status_for(settings: WslmanwSettings, name: str) -> _initenv.EnvStatus | None:
    """回显上次持久化的环境探测结果；没有缓存时返回 ``None``。"""
    return _initenv.status_from_mapping(settings.distro_env_status.get(name))


def with_env_status(settings: WslmanwSettings, name: str, status: _initenv.EnvStatus) -> WslmanwSettings:
    """写入实例环境探测缓存（按实例名覆盖）。"""
    cached = dict(settings.distro_env_status)
    cached[name] = _initenv.status_to_mapping(status)
    return replace(settings, distro_env_status=cached)


def ssh_status_for(settings: WslmanwSettings, name: str) -> _sshfiles.MigrationSnapshot | None:
    """回显上次持久化的 SSH 迁移状态；没有缓存时返回 ``None``。"""
    return _sshfiles.snapshot_from_mapping(settings.distro_ssh_status.get(name))


def with_ssh_status(settings: WslmanwSettings, name: str, snapshot: _sshfiles.MigrationSnapshot) -> WslmanwSettings:
    """写入实例的 SSH 迁移状态缓存（按实例名覆盖）。"""
    cached = dict(settings.distro_ssh_status)
    cached[name] = _sshfiles.snapshot_to_mapping(snapshot)
    return replace(settings, distro_ssh_status=cached)


def opener_paths_for(settings: WslmanwSettings, name: str) -> Mapping[str, str]:
    """WSL 内智能检测上次找到的可执行文件路径；实例未运行时直接复用。"""
    return settings.distro_opener_paths.get(name, {})


def with_opener_paths(settings: WslmanwSettings, name: str, found: Mapping[str, str]) -> WslmanwSettings:
    """写入实例内 ``command -v`` 检测缓存。"""
    cached = dict(settings.distro_opener_paths)
    values = {key: value for key, value in found.items() if key and value}
    if values:
        cached[name] = values
    else:
        cached.pop(name, None)
    return replace(settings, distro_opener_paths=cached)


def _status_cache(raw: Any) -> dict[str, dict[str, str]]:
    """解析实例 → EnvStatus 映射；坏行跳过。"""
    if not isinstance(raw, Mapping):
        return {}
    cached: dict[str, dict[str, str]] = {}
    for name, values in raw.items():
        status = _initenv.status_from_mapping(values)
        if isinstance(name, str) and name.strip() and status is not None:
            cached[name.strip()] = _initenv.status_to_mapping(status)
    return cached


def _ssh_status_cache(raw: Any) -> dict[str, dict[str, str]]:
    """解析实例 → SSH 迁移状态映射；坏行跳过。"""
    if not isinstance(raw, Mapping):
        return {}
    cached: dict[str, dict[str, str]] = {}
    for name, values in raw.items():
        snapshot = _sshfiles.snapshot_from_mapping(values)
        if isinstance(name, str) and name.strip() and snapshot is not None:
            cached[name.strip()] = _sshfiles.snapshot_to_mapping(snapshot)
    return cached


def _opener_path_cache(raw: Any) -> dict[str, dict[str, str]]:
    """解析实例 → 可执行文件路径映射；只保留非空字符串。"""
    if not isinstance(raw, Mapping):
        return {}
    cached: dict[str, dict[str, str]] = {}
    for name, values in raw.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(values, Mapping):
            continue
        paths = {
            key: value.strip()
            for key, value in values.items()
            if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip()
        }
        if paths:
            cached[name.strip()] = paths
    return cached


def _choice(raw: Mapping[str, Any], key: str, options: Sequence[tuple[str, str]], default: str) -> str:
    """只接受 ``options`` 里登记过的取值，非法 / 缺失时回落 ``default``。"""
    value = _str(raw, key)
    return value if any(ident == value for ident, _label in options) else default


def _str(raw: Mapping[str, Any], key: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else ""


def _terminal_dirs(value: Any) -> dict[str, str]:
    """解析实例 → 终端目录映射；忽略空名称 / 空目录与非字符串项。"""
    if not isinstance(value, Mapping):
        return {}
    dirs: dict[str, str] = {}
    for name, cwd in value.items():
        if isinstance(name, str) and name.strip() and isinstance(cwd, str) and cwd.strip():
            dirs[name.strip()] = cwd.strip()
    return dirs


def terminal_cwd(settings: WslmanwSettings, name: str) -> str:
    """返回实例的终端默认目录；未配置时回到 ``~``（大小写不敏感）。"""
    key = name.strip().casefold()
    for saved, cwd in settings.distro_terminal_dirs.items():
        if saved.strip().casefold() == key:
            return cwd.strip() or DEFAULT_TERMINAL_CWD
    return DEFAULT_TERMINAL_CWD


def with_terminal_cwd(settings: WslmanwSettings, name: str, cwd: str) -> WslmanwSettings:
    """返回写入实例终端目录后的设置；目录为 ``~`` 时删掉该项，保持设置文件干净。"""
    key = name.strip().casefold()
    target = cwd.strip() or DEFAULT_TERMINAL_CWD
    dirs = {saved: value for saved, value in settings.distro_terminal_dirs.items() if saved.strip().casefold() != key}
    if target != DEFAULT_TERMINAL_CWD:
        dirs[name.strip()] = target
    return replace(settings, distro_terminal_dirs=dirs)


def _strip_windows_extended_prefix(text: str) -> str:
    r"""去掉 Windows 扩展长度前缀（如 ``\\?\E:\wsl`` / ``\\?\UNC\server\...``）。

    ``wsl --import-in-place`` 会把注册表 ``BasePath`` 写成 ``\\?\`` 前缀；不归一
    就会和设置里的 ``E:\wsl`` 比较失败，所有实例都显示「未在已配置目录」。
    """
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\") :]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\") :]
    return text


def _dir_key(value: str | Path) -> str:
    """目录比较用的规范化 key：展开 ``~``、去扩展前缀，再按平台归一大小写与分隔符。"""
    text = _strip_windows_extended_prefix(str(value).strip())
    return os.path.normcase(str(Path(text).expanduser().resolve()))


def same_dir(left: str | Path, right: str | Path) -> bool:
    """两个目录是否是同一路径（按平台归一大小写与分隔符）。"""
    return _dir_key(left) == _dir_key(right)


def _work_dirs(legacy: str, raw: Any) -> tuple[str, tuple[str, ...]]:
    """解析工作目录列表与默认项。

    旧版只有 ``work_dir`` 单目录：读入时迁成一条记录并设为默认，老用户不丢设置；
    显式列表里默认项排在首位，找不到默认项时用第一项。
    """
    values: list[str] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                continue
            text = item.strip()
            key = _dir_key(text)
            if key in seen:
                continue
            seen.add(key)
            values.append(text)
    legacy_text = legacy.strip()
    if not values:
        return legacy_text, (legacy_text,) if legacy_text else ()
    default = legacy_text if legacy_text and _dir_key(legacy_text) in seen else values[0]
    ordered = [default, *(item for item in values if _dir_key(item) != _dir_key(default))]
    return ordered[0], tuple(ordered)


def work_dir_paths(settings: WslmanwSettings) -> tuple[Path, ...]:
    """全部工作目录，默认目录在首位且去重；没有配置列表时回落到单个默认路径。"""
    configured = [Path(item).expanduser() for item in settings.work_dirs if item.strip()]
    if not configured:
        return (work_dir_path(settings),)
    default = work_dir_path(settings)
    unique: list[Path] = []
    seen: set[str] = set()
    for item in configured:
        key = _dir_key(item)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    head = [item for item in unique if _dir_key(item) == _dir_key(default)]
    rest = [item for item in unique if _dir_key(item) != _dir_key(default)]
    return (*(head or [default]), *rest)


def work_dir_path(settings: WslmanwSettings) -> Path:
    """默认工作目录：``work_dir`` 优先；多目录未写单字段时取首项，留空回落默认根。"""
    value = settings.work_dir.strip()
    if value:
        return Path(value).expanduser()
    if settings.work_dirs:
        return Path(settings.work_dirs[0]).expanduser()
    return work_root()


def derived_dirs(settings: WslmanwSettings) -> dict[str, Path]:
    """按默认工作目录推演下载 / 安装 / 备份子目录（不再读单独的目录配置）。"""
    root = work_dir_path(settings)
    return {"download": root / "download", "install": root / "install", "backup": root / "backup"}


_BACKUP_SUFFIXES = (".tar.gz", ".tgz", ".tar")


def copy_default_name(source: str, taken: Sequence[str]) -> str:
    """复制实例的默认名称：``<源>-copy``，已被占用时依次递增 ``-copy2`` / ``-copy3``。"""
    existing = {name.strip().casefold() for name in taken if name.strip()}
    base = f"{source}-copy"
    if base.casefold() not in existing:
        return base
    index = 2
    while f"{base}{index}".casefold() in existing:
        index += 1
    return f"{base}{index}"


def copy_name_problem(name: str, taken: Sequence[str]) -> str | None:
    """校验复制实例的新名称；合法时返回 ``None``。"""
    text = name.strip()
    if not text:
        return "实例名称不能为空"
    if any(char in text for char in ("/", "\\", ":")):
        return "实例名称不能包含路径分隔符"
    if text.casefold() in {item.strip().casefold() for item in taken if item.strip()}:
        return f"实例名称已存在：{text}"
    return None


def copy_default_dir(settings: WslmanwSettings, base_path: str | None, name: str) -> Path:
    """复制实例的默认目录：源实例所属工作目录的 ``install/<新名称>``；找不到时用默认目录。"""
    root = work_dir_of(settings, base_path) or work_dir_path(settings)
    return root / "install" / name


def backup_names(directories: Sequence[Path]) -> frozenset[str]:
    """扫描备份目录里的 ``<名称>.tar`` / ``.tar.gz`` / ``.tgz``，返回小写发行版名。

    备份动作落盘在默认工作目录的 ``backup`` 子目录；这里接受多个目录，既覆盖默认
    目录，也覆盖其他已配置工作目录里留存的旧备份。读不了 / 不存在的目录直接跳过，
    不能因为一个目录不可读就把全部「已备份」状态抹掉。
    """
    names: set[str] = set()
    for directory in directories:
        try:
            entries = tuple(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not entry.is_file():
                continue
            lowered = entry.name.casefold()
            for suffix in _BACKUP_SUFFIXES:
                if lowered.endswith(suffix):
                    names.add(entry.name[: -len(suffix)].casefold())
                    break
    return frozenset(names)


def with_work_dirs(
    settings: WslmanwSettings,
    dirs: Sequence[str | Path],
    default: str | Path = "",
) -> WslmanwSettings:
    """写回全部工作目录与默认项（默认项排首位），并清掉旧版下载 / 备份覆盖值。"""
    values: list[str] = []
    seen: set[str] = set()
    for item in dirs:
        text = str(item).strip()
        if not text:
            continue
        key = _dir_key(text)
        if key in seen:
            continue
        seen.add(key)
        values.append(text)
    chosen = str(default).strip()
    if values:
        if not chosen or _dir_key(chosen) not in seen:
            chosen = values[0]
        values = [chosen, *(item for item in values if _dir_key(item) != _dir_key(chosen))]
    else:
        chosen = ""
    return replace(settings, work_dir=chosen, work_dirs=tuple(values), download_dir="", backup_dir="")


def with_work_dir(settings: WslmanwSettings, value: str | Path) -> WslmanwSettings:
    """改单个工作目录（兼容旧入口）：等价于只有一条工作目录记录。"""
    text = str(value).strip()
    return with_work_dirs(settings, (text,) if text else (), text)


def work_dir_of(settings: WslmanwSettings, base_path: str | None) -> Path | None:
    """实例磁盘位置所属的已配置工作目录；不在任何目录下时返回 ``None``。"""
    if not base_path:
        return None
    child = _dir_key(base_path)
    for root in work_dir_paths(settings):
        root_key = _dir_key(root)
        try:
            if os.path.commonpath([child, root_key]) == root_key:
                return root
        except ValueError:
            continue
    return None


def has_distro_files(base_path: str | None) -> bool:
    """BasePath 下是否已有 WSL2 ``ext4.vhdx`` 或 WSL1 ``rootfs``。

    只看目录是否存在不够：实例迁移 / 手动搬家后旧目录可能还在，但磁盘文件已经不在。
    """
    if not base_path:
        return False
    base = Path(base_path)
    return (base / "ext4.vhdx").is_file() or (base / "rootfs").is_dir()


def scan_install_dirs(settings: WslmanwSettings) -> dict[str, Path]:
    """扫描已配置工作目录的 ``install/<实例>``，返回含磁盘文件的实例目录。

    键是实例名的小写形式（与注册表 / ``wsl -l -v`` 的比对口径一致）。同名实例
    出现在多个工作目录时保留先扫描到的那个，与 ``work_dir_paths`` 的默认目录优先
    顺序一致。
    """
    found: dict[str, Path] = {}
    for root in work_dir_paths(settings):
        install_root = root / "install"
        try:
            children = tuple(install_root.iterdir())
        except OSError:
            continue
        for child in children:
            try:
                is_dir = child.is_dir()
            except OSError:
                continue
            if not is_dir or not has_distro_files(str(child)):
                continue
            found.setdefault(child.name.casefold(), child)
    return found


def reconcile_distros(
    view: DistroView,
    settings: WslmanwSettings | None = None,
) -> tuple[DistroView, tuple[str, ...], dict[str, Path]]:
    """标记注册表 BasePath 下已经没有磁盘文件的实例为「需修复」。

    返回 ``(新视图, 需修复的实例名, 同名残余磁盘)``：

    - ``BasePath`` 下没有 ``ext4.vhdx`` / ``rootfs`` 时实例无法启动，但**仍保留在
      列表里**，``needs_repair=True``；界面据此禁用启动并显示「需修复」标签。
    - 提供 ``settings`` 时扫描已配置工作目录，把同名 ``install/<实例>`` 填进
      ``repair_vhd``，供「修复注册」用 ``wsl --import-in-place`` 重新注册。
    - ``BasePath`` 为空（注册表不可读）时保持原样，避免误标仍可启动的实例。
    """
    stale = [distro for distro in view.distros if distro.base_path and not has_distro_files(distro.base_path)]
    if not stale:
        return view, (), {}

    found = scan_install_dirs(settings) if settings is not None else {}
    orphans = {distro.name: found[distro.name.casefold()] for distro in stale if distro.name.casefold() in found}
    stale_names = {distro.name.casefold() for distro in stale}
    distros: list[Distro] = []
    for distro in view.distros:
        if distro.name.casefold() not in stale_names:
            distros.append(distro)
            continue
        orphan = orphans.get(distro.name)
        vhd = orphan / "ext4.vhdx" if orphan is not None else None
        distros.append(
            replace(distro, needs_repair=True, repair_vhd=str(vhd) if vhd is not None and vhd.is_file() else None)
        )
    warnings = list(view.warnings)
    for distro in stale:
        warning = f"{distro.name} 的磁盘文件已不存在（注册表 BasePath：{distro.base_path}），需修复注册"
        orphan = orphans.get(distro.name)
        if orphan is not None:
            warning += f"；发现同名残余磁盘 {orphan / 'ext4.vhdx'}，可用「修复注册」重新导入"
        warnings.append(warning)
    return replace(view, distros=tuple(distros), warnings=tuple(warnings)), tuple(d.name for d in stale), orphans


def legacy_dir_note(settings: WslmanwSettings) -> str:
    """旧设置里单独配过下载 / 备份目录时给一句迁移说明；没有则返回空串。"""
    legacy = [item for item in (settings.download_dir.strip(), settings.backup_dir.strip()) if item]
    if not legacy:
        return ""
    return f"旧设置里的下载 / 备份目录已忽略（{'、'.join(legacy)}），改为按工作目录推演"


def _dialog_geometries(value: Any) -> dict[str, str]:
    """解析对话框 → 几何映射；忽略非法名称 / 几何文本。"""
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for name, geometry in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(geometry, str):
            continue
        valid = _guiconfig.valid_geometry(geometry)
        if valid is not None:
            out[name.strip()] = valid
    return out


def _init_tasks(value: Any) -> dict[str, tuple[str, ...]]:
    """解析实例 → 初始化勾选；只保留当前仍存在的任务 key，并按固定顺序排列。"""
    if not isinstance(value, Mapping):
        return {}
    order = _initenv.task_keys()
    known = set(order)
    out: dict[str, tuple[str, ...]] = {}
    for name, keys in value.items():
        if not isinstance(name, str) or not name.strip() or isinstance(keys, str) or not isinstance(keys, Sequence):
            continue
        chosen = {key for key in keys if isinstance(key, str) and key in known}
        out[name.strip()] = tuple(key for key in order if key in chosen)
    return out


def _package_mirrors(value: Any) -> dict[str, str]:
    """解析实例 → 软件包源；非法 / ``auto`` 值丢弃，保持设置文件干净。"""
    if not isinstance(value, Mapping):
        return {}
    out: dict[str, str] = {}
    for name, mirror in value.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(mirror, str):
            continue
        key = _initenv.normalize_package_mirror(mirror)
        if key != _initenv.PACKAGE_MIRROR_AUTO:
            out[name.strip()] = key
    return out


def package_mirror_for(settings: WslmanwSettings, name: str) -> str:
    """返回实例的「软件包源」；未配置时 ``auto``（跟随系统镜像源）。"""
    key = name.strip().casefold()
    for saved, mirror in settings.distro_package_mirrors.items():
        if saved.strip().casefold() == key:
            return _initenv.normalize_package_mirror(mirror)
    return _initenv.PACKAGE_MIRROR_AUTO


def with_package_mirror(settings: WslmanwSettings, name: str, mirror: str) -> WslmanwSettings:
    """返回写入软件包源后的设置；``auto`` 时删掉该实例项。"""
    key = name.strip().casefold()
    value = _initenv.normalize_package_mirror(mirror)
    mirrors = {
        saved: item for saved, item in settings.distro_package_mirrors.items() if saved.strip().casefold() != key
    }
    if value != _initenv.PACKAGE_MIRROR_AUTO:
        mirrors[name.strip()] = value
    return replace(settings, distro_package_mirrors=mirrors)


def dialog_geometry(settings: WslmanwSettings, name: str) -> str | None:
    """返回对话框上次的几何（大小写不敏感）；未记录时返回 ``None``。"""
    key = name.strip().casefold()
    for saved, geometry in settings.dialog_geometry.items():
        if saved.strip().casefold() == key:
            return geometry or None
    return None


def with_dialog_geometry(settings: WslmanwSettings, name: str, geometry: str) -> WslmanwSettings:
    """返回写入对话框几何后的设置；几何非法时删掉该项。"""
    key = name.strip().casefold()
    saved = {item: value for item, value in settings.dialog_geometry.items() if item.strip().casefold() != key}
    valid = _guiconfig.valid_geometry(geometry)
    if valid is not None:
        saved[name.strip()] = valid
    return replace(settings, dialog_geometry=saved)


def init_task_keys(settings: WslmanwSettings, name: str) -> tuple[str, ...] | None:
    """返回实例上次保存的初始化勾选；未记录时返回 ``None``（按默认勾选）。"""
    key = name.strip().casefold()
    for saved, keys in settings.distro_init_tasks.items():
        if saved.strip().casefold() == key:
            return keys
    return None


def with_init_task_keys(settings: WslmanwSettings, name: str, keys: Sequence[str]) -> WslmanwSettings:
    """返回写入实例初始化勾选后的设置；按任务固定顺序去重。"""
    key = name.strip().casefold()
    chosen = set(keys)
    tasks = {saved: value for saved, value in settings.distro_init_tasks.items() if saved.strip().casefold() != key}
    tasks[name.strip()] = tuple(item for item in _initenv.task_keys() if item in chosen)
    return replace(settings, distro_init_tasks=tasks)


def config_path() -> Path:
    """返回设置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/wslmanw.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> WslmanwSettings:
    raw = SettingsStore("wslmanw", path).load()
    if raw is None:
        return WslmanwSettings()
    return settings_from_mapping(raw)


def save_settings(settings: WslmanwSettings, path: Path | None = None) -> None:
    SettingsStore("wslmanw", path).save(settings_to_mapping(settings))


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
    """删除实例按钮只在手输名称与目标完全一致（忽略大小写与首尾空格）时可用。"""
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
    """各筛选口径的条目数，供状态行显示「已安装 N / 可下载 M / 已备份 K」。"""
    counts = {ident: 0 for ident, _label in IMAGE_FILTERS}
    counts["all"] = len(entries)
    for entry in entries:
        if entry.downloads:
            counts["download"] += 1
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
    parts.append(f"可下载 {counts['download']}")
    parts.append(f"已备份 {counts['backed_up']}")
    text = " · ".join(parts)
    if shown == 0 and counts["all"]:
        text += "（没有匹配项，可调整筛选或搜索词）"
    return f"{text} · {note}" if note else text


def download_limit(raw: Any) -> int:
    """并发下载上限：非整数或越界时收敛到 :data:`MIN_DOWNLOADS`–:data:`MAX_DOWNLOADS`。"""
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_DOWNLOADS
    return max(MIN_DOWNLOADS, min(MAX_DOWNLOADS, value))


def with_max_downloads(settings: WslmanwSettings, value: int | str) -> WslmanwSettings:
    """写回并发上限；越界值收敛，正在排队的下载会在下一次调度时生效。"""
    return replace(settings, max_downloads=download_limit(value))


def download_metrics_text(done: int, total: int | None, speed: float = 0.0) -> str:
    """镜像行内的进度短文案；总量未知时只给已下载量，速度为 0 时不追加。"""
    if total and total > 0:
        text = f"下载 {min(100, int(done * 100 / total))}%"
    elif done > 0:
        text = f"下载 {format_bytes(done)}"
    else:
        text = "下载中…"
    if speed > 0:
        text += f" · {format_speed(speed)}"
    return text


def download_queue_text(running: int, queued: int, limit: int) -> str:
    """镜像页状态行：并发几项、上限多少、还有几项排队；无任务时为空串。"""
    if running <= 0 and queued <= 0:
        return ""
    parts = [f"正在下载 {running} 项"]
    if limit > 0:
        parts.append(f"上限 {limit}")
    if queued > 0:
        parts.append(f"排队 {queued} 项")
    return " · ".join(parts)


def download_progress_text(name: str, done: int, total: int | None, speed: float = 0.0) -> str:
    """状态栏的下载进度文案：``下载 X · 42% · 1.2 GB / 2.6 GB · 3.2 MB/s · 剩余 12s``。

    总量未知（服务端没给 Content-Length）时只给已下载字节与速度；速度为 0
    （刚开始 / 断流）时速度显示 ``--`` 且不估算剩余时间。
    """
    return _progress_text("下载", name, done, total, speed)


def export_progress_text(name: str, done: int, total: int | None, speed: float = 0.0) -> str:
    """状态栏的导出进度文案，与下载同一口径（总量是发行版占用的估算值）。"""
    return _progress_text("导出", name, done, total, speed)


def _progress_text(prefix: str, name: str, done: int, total: int | None, speed: float) -> str:
    from fcmd.infras.download import format_eta, format_speed

    parts = [f"{prefix} {name}"]
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


def local_arch() -> str:
    """本机架构口径（与官方清单的 ``arch`` 字段一致）：``aarch64`` / ``x86_64``。"""
    return "aarch64" if "arm" in platform.machine().casefold() else "x86_64"


_ARM_TOKENS = ("aarch64", "arm64", "armhf", "armv7", "armv8")
_X86_TOKENS = ("amd64", "x86_64", "x86-64", "i386", "i686")


def _arch_in_url(url: str) -> str | None:
    """从 URL 认出制品架构；看不出来返回 ``None``（不猜就不过滤）。"""
    text = url.casefold()
    if any(token in text for token in _ARM_TOKENS):
        return "aarch64"
    if any(token in text for token in _X86_TOKENS):
        return "x86_64"
    return None


def mirror_sources(entry: _images.ImageEntry, sources: Sequence[Any] = ()) -> tuple[tuple[str, str], ...]:
    """该条目在本机架构下能用的下载源 ``(名称, URL)``，顺序即自动下载的尝试顺序。

    ``sources`` 是 WSLUI 清单匹配到的源（``_wslui.match_sources`` 的结果，带
    ``label`` / ``url``），没有 WSLUI 数据时只靠官方清单：

    1. WSLUI 多源（清华 / 腾讯 / 南大 / 北外等，与 WSL Dashboard 同一套接口）；
    2. 有官方 sha256 可校验的国内镜像（中科大 / 阿里云 / 华为云）；
    3. 官方源兜底。

    **按本机架构过滤**：官方清单的 ``arch`` 字段直接比对；WSLUI 源只有 URL 能看出
    架构（``/amd64/``、``*-arm64.wsl``），认得出且不匹配就丢掉（ARM 机器上 WSLUI 的
    ``amd64`` lxc rootfs 装了也跑不了），认不出就保留。全部官方下载都不是本机架构
    时不清空列表（宁可展示不可用的源，也不把镜像页变成 0 个源）。

    同名同 URL 只保留一条，避免折叠行数量虚高。
    """
    arch = local_arch()
    official = [download for download in entry.downloads if download.arch == arch]
    if not official:
        official = list(entry.downloads)
    pairs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(label: str, url: str) -> None:
        key = (label, url)
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    for source in sources:
        source_arch = _arch_in_url(source.url)
        if source_arch is not None and source_arch != arch:
            continue
        add(source.label, source.url)
    for download in official:
        if not download.sha256:
            # 没有摘要就不认镜像：未校验的第三方字节不能当成官方候选。
            continue
        for candidate in _mirrors.candidates(download.url, policy=_mirrors.AUTO, verifiable=True):
            if candidate.ident != _mirrors.OFFICIAL:
                add(candidate.label, candidate.url)
    for download in official:
        add(_mirrors.label(_mirrors.OFFICIAL), download.url)
    return tuple(pairs)


def mirror_lines(pairs: Sequence[tuple[str, str]]) -> str:
    """展开区 / tooltip 的镜像源明细：每个源一行「名称 · URL」。"""
    return "\n".join(f"{label} · {url}" for label, url in pairs)


def image_details(entry: _images.ImageEntry) -> tuple[tuple[str, str], ...]:
    """下载页的「字段 → 值」：来源、架构、校验和与每个架构的下载地址。

    「镜像源」由 :class:`_ImageDetailView` 展开时按行回填（需要 WSLUI 清单），
    这里只给与清单无关的字段。
    """
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
        ("校验和", "有 sha256" if entry.has_sha256 else "无 sha256（不标 verified）"),
        ("已安装", "是" if entry.installed else "否"),
        ("已备份", "是" if entry.backed_up else "否"),
        ("下载地址", "\n".join(downloads) or "无直接下载地址；请用 wsl --install"),
    )


def pick_download(entry: _images.ImageEntry) -> _images.ImageDownload:
    """按本机架构挑下载项：ARM 优先 aarch64，其余优先 x86_64，都没有时取第一个。"""
    preferred = local_arch()
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


_ARCHIVE_SUFFIXES: tuple[str, ...] = _install.INSTALL_SUFFIXES
"""可安装的本地镜像包后缀：tar / wsl / vhdx / zip / 7z / rar / gz / bz2 / xz。"""


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

    同一实例有多个架构时优先本机架构（:func:`pick_download`）；归档命名既认
    官方 URL 原名，也认 CLI 的 ``名称-架构.tar``（:func:`match_archive`）。

    多个条目共用同一个归档（如 ``Ubuntu`` 与 ``Ubuntu-26.04`` 指向同一个
    26.04.1 URL）时全部标为已下载：文件本身同时满足这些条目的下载项，只标第一个
    会让列表里出现「同名镜像一个已下载、一个未下载」的不一致状态。
    """
    found: dict[str, Path] = {}
    archives = local_archives(directory)
    for path in archives:
        matched_all = _match_archives(path, entries)
        for entry, download in matched_all:
            key = entry.name.casefold()
            preferred = pick_download(entry).arch
            current = found.get(key)
            if current is None or download.arch == preferred:
                found[key] = path
    # WSLUI 镜像源下载落盘名是 ``<实例名>-<arch>.<格式>``；官方文件名匹配不到时按前缀识别。
    for entry in sorted(entries, key=lambda item: len(item.name), reverse=True):
        key = entry.name.casefold()
        if key in found:
            continue
        prefix = f"{key}-"
        matched = next((path for path in archives if path.name.casefold().startswith(prefix)), None)
        if matched is not None:
            found[key] = matched
    return tuple(
        replace(
            entry,
            downloaded=entry.name.casefold() in found,
            archive=str(found[entry.name.casefold()]) if entry.name.casefold() in found else "",
        )
        for entry in entries
    )


def match_distro_image(distro: Distro, entries: Sequence[_images.ImageEntry]) -> _images.ImageEntry | None:
    """把已安装实例对应到在线镜像条目（大小写不敏感）。

    依次尝试：名称全等 → 友好名全等 → 名称是实例名的「词首前缀」
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
    """从文件名推测实例名：去掉归档后缀与架构尾巴，非法字符换成 ``_``。"""
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
    matched = _match_archives(path, entries)
    return matched[0] if matched else None


def _match_archives(
    path: Path, entries: Sequence[_images.ImageEntry]
) -> tuple[tuple[_images.ImageEntry, _images.ImageDownload], ...]:
    """返回落盘文件名能匹配到的全部 ``(条目, 下载项)``；同一条目只取第一个命中项。"""
    name = path.name.casefold()
    matched: list[tuple[_images.ImageEntry, _images.ImageDownload]] = []
    for entry in entries:
        for download in entry.downloads:
            official = Path(urllib.parse.urlparse(download.url).path).name.casefold()
            if name in {official, f"{entry.name}-{download.arch}.tar".casefold()}:
                matched.append((entry, download))
                break
    return tuple(matched)


def inspect_archive(path: Path, entries: Sequence[_images.ImageEntry]) -> LocalArchive:
    """汇总本地包信息：实例、架构、来源与官方 sha256（未收录时为 ``None``）。"""
    size = path.stat().st_size
    matched = match_archive(path, entries)
    if matched is None:
        lowered = path.name.casefold()
        if lowered.endswith(_install.VHDX_SUFFIXES):
            source = "本地磁盘（vhdx，原地注册）"
        elif lowered.endswith(_install.ARCHIVE_SUFFIXES):
            source = "本地压缩包（解压后安装）"
        else:
            source = "本地文件（未匹配官方清单）"
        return LocalArchive(path, size, guess_distro_name(path), "未知", source)
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
        from fcmd.gui.compat import qt_import_failure

        raise SystemExit(qt_import_failure(exc)) from exc
    run_qt(settings_path)


@fcmd.tool("wslmanw", help="打开 WSL 实例管理窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开 WSL 实例管理窗口。"""
    run_gui()


@fcmd.main("wslmanw")
def main() -> None:
    """``wslmanw`` 命令入口。"""
    launch_cmd()


if __name__ == "__main__":  # pragma: no cover
    # fspack 打包包装器经 runpy.run_module 执行本模块；缺这个守卫时 exe 静默
    # 退出 0（只执行了 import 与装饰器，没有调用入口）。
    main()
