"""发行版模型：合并 ``wsl -l -v`` 与注册表，并提供人读 / 机读两种呈现。

合并原则：``-l -v`` 负责运行状态与默认 ``*`` 标记，注册表负责 ``BasePath`` / 版本 /
默认用户；两边冲突不自动修，只在 ``warnings`` 里说清楚。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from fcmd.apps.wslman._decode import DistroRow
from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._registry import RegistryDistro, RegistrySnapshot

__all__ = [
    "Distro",
    "DistroView",
    "distro_data",
    "format_info",
    "format_list",
    "merge",
    "state_label",
    "to_data",
    "with_disk",
]

_STATE_LABELS = {
    "running": "运行中",
    "stopped": "已停止",
    "installing": "安装中",
    "uninstalling": "卸载中",
    "converting": "转换中",
    "starting": "启动中",
    "stopping": "停止中",
    "installed": "已安装",
    "unknown": "未知",
}


@dataclass(frozen=True, slots=True)
class Distro:
    """一个发行版的合并视图。"""

    name: str
    state: str
    state_raw: str
    version: int | None
    default: bool
    base_path: str | None = None
    default_uid: int | None = None
    package_family: str | None = None
    flags: int | None = None
    disk_bytes: int | None = None
    disk_estimated: bool = False
    listed: bool = True
    registered: bool = True


@dataclass(frozen=True, slots=True)
class DistroView:
    distros: tuple[Distro, ...]
    default: str | None
    warnings: tuple[str, ...]
    registry: RegistrySnapshot

    def find(self, name: str) -> Distro | None:
        wanted = name.casefold()
        for distro in self.distros:
            if distro.name.casefold() == wanted:
                return distro
        return None


def merge(
    rows: Sequence[DistroRow],
    snapshot: RegistrySnapshot,
    *,
    disk_sizes: Mapping[str, tuple[int | None, bool]] | None = None,
) -> DistroView:
    warnings: list[str] = []
    registry_index: dict[str, RegistryDistro] = {}
    for registry_distro in snapshot.distros:
        key = registry_distro.name.casefold()
        if key in registry_index:
            warnings.append(f"注册表中存在重名发行版 {registry_distro.name}，仅采用第一条")
            continue
        registry_index[key] = registry_distro

    sizes = {key.casefold(): value for key, value in (disk_sizes or {}).items()}
    distros: list[Distro] = []
    seen: set[str] = set()
    for row in rows:
        key = row.name.casefold()
        if key in seen:
            warnings.append(f"wsl --list --verbose 中发行版 {row.name} 重复，仅采用第一条")
            continue
        seen.add(key)
        registered = registry_index.get(key)
        if registered is None and snapshot.available:
            warnings.append(f"{row.name} 未在 Lxss 注册表中找到，BasePath 等信息不可用")
        disk_bytes, disk_estimated = sizes.get(key, (None, False))
        distros.append(
            Distro(
                name=row.name,
                state=row.state,
                state_raw=row.state_raw,
                version=row.version,
                default=row.default,
                base_path=getattr(registered, "base_path", None),
                default_uid=getattr(registered, "default_uid", None),
                package_family=getattr(registered, "package_family", None),
                flags=getattr(registered, "flags", None),
                disk_bytes=disk_bytes,
                disk_estimated=disk_estimated,
                listed=True,
                registered=registered is not None,
            )
        )

    for registry_distro in snapshot.distros:
        key = registry_distro.name.casefold()
        if key in seen:
            continue
        seen.add(key)
        disk_bytes, disk_estimated = sizes.get(key, (None, False))
        warnings.append(f"{registry_distro.name} 已注册但未出现在 wsl --list --verbose 中，状态未知")
        distros.append(
            Distro(
                name=registry_distro.name,
                state="unknown",
                state_raw="未列出",
                version=registry_distro.version,
                default=False,
                base_path=registry_distro.base_path,
                default_uid=registry_distro.default_uid,
                package_family=registry_distro.package_family,
                flags=registry_distro.flags,
                disk_bytes=disk_bytes,
                disk_estimated=disk_estimated,
                listed=False,
                registered=True,
            )
        )

    marked = next((distro.name for distro in distros if distro.default), None)
    registry_default = snapshot.default_name
    if marked and registry_default and marked.casefold() != registry_default.casefold():
        warnings.append(f"默认发行版不一致：wsl 标记为 {marked}，注册表为 {registry_default}")
    if not marked and registry_default:
        warnings.append(f"wsl 输出中没有默认标记，按注册表补全为 {registry_default}")
    default = marked or registry_default

    return DistroView(distros=tuple(distros), default=default, warnings=tuple(warnings), registry=snapshot)


def with_disk(view: DistroView, sizes: Mapping[str, tuple[int | None, bool]]) -> DistroView:
    """把磁盘估算结果填进已有视图（键按不区分大小写匹配）。"""
    normalized = {key.casefold(): value for key, value in sizes.items()}
    updated = tuple(
        replace(
            distro,
            disk_bytes=normalized.get(distro.name.casefold(), (None, False))[0],
            disk_estimated=normalized.get(distro.name.casefold(), (None, False))[1],
        )
        for distro in view.distros
    )
    return replace(view, distros=updated)


def state_label(distro: Distro) -> str:
    # 未知状态保留 ``-l -v`` 原文，避免把新版本新增的状态词吞成「未知」。
    if distro.state == "unknown" and distro.state_raw:
        return distro.state_raw
    return _STATE_LABELS.get(distro.state, distro.state_raw or "未知")


def distro_data(distro: Distro) -> dict[str, object]:
    return {
        "name": distro.name,
        "state": distro.state,
        "state_raw": distro.state_raw,
        "version": distro.version,
        "default": distro.default,
        "base_path": distro.base_path,
        "default_uid": distro.default_uid,
        "package_family": distro.package_family,
        "flags": distro.flags,
        "disk_bytes": distro.disk_bytes,
        "disk_estimated": distro.disk_estimated,
        "listed": distro.listed,
        "registered": distro.registered,
    }


def to_data(view: DistroView) -> dict[str, object]:
    registry = view.registry
    return {
        "schema": "wslman/list/1",
        "distros": [distro_data(distro) for distro in view.distros],
        "default": view.default,
        "count": len(view.distros),
        "warnings": list(view.warnings),
        "registry": {
            "available": registry.available,
            "error": registry.error,
            "default_distribution": registry.default_distribution,
            "default_version": registry.default_version,
            "count": len(registry.distros),
        },
    }


def format_list(view: DistroView, *, disk: bool) -> str:
    if not view.distros:
        return "没有已安装的 WSL 发行版。\n提示：运行 fcmd wslman available 查看可安装列表。"

    header = ["", "名称", "状态", "版本"]
    rows = [
        [
            "*" if distro.default else "",
            distro.name,
            state_label(distro),
            f"WSL {distro.version}" if distro.version else "未知",
        ]
        for distro in view.distros
    ]
    if disk:
        header.append("占用")
        for row, distro in zip(rows, view.distros, strict=True):
            row.append(format_bytes(distro.disk_bytes, estimated=distro.disk_estimated))

    widths = [max(len(row[index]) for row in [header, *rows]) for index in range(len(header))]
    lines = [
        "  ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)).rstrip()
        for row in [header, *rows]
    ]
    if view.default:
        lines.append("")
        lines.append(f"默认发行版：{view.default}")
    for warning in view.warnings:
        lines.append(f"[警告] {warning}")
    return "\n".join(lines)


def format_info(distro: Distro, *, wslconfig_lines: Sequence[str] | None = None) -> str:
    lines = [
        f"名称: {distro.name}",
        f"状态: {state_label(distro)}{_raw_suffix(distro)}",
        f"版本: {f'WSL {distro.version}' if distro.version else '未知'}",
        f"默认: {'是' if distro.default else '否'}",
        f"BasePath: {distro.base_path or '未知'}",
        f"默认用户 UID: {distro.default_uid if distro.default_uid is not None else '未知'}",
        f"包族: {distro.package_family or '无（非 Store 安装）'}",
        f"Flags: {f'0x{distro.flags:08x}' if distro.flags is not None else '未知'}",
        f"占用: {format_bytes(distro.disk_bytes, estimated=distro.disk_estimated)}",
        f"来源: {_source_text(distro)}",
    ]
    if wslconfig_lines is not None:
        lines.append("/etc/wsl.conf:")
        lines.extend(f"  {line}" for line in wslconfig_lines)
    return "\n".join(lines)


def _raw_suffix(distro: Distro) -> str:
    label = state_label(distro)
    if distro.state_raw and label.casefold() != distro.state_raw.casefold():
        return f" ({distro.state_raw})"
    return ""


def _source_text(distro: Distro) -> str:
    if distro.listed and distro.registered:
        return "wsl --list --verbose + Lxss 注册表"
    if distro.listed:
        return "仅 wsl --list --verbose"
    return "仅 Lxss 注册表"
