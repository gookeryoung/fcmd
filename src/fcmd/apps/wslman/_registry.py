"""``Lxss`` 注册表只读访问。

注册表是发行版 ``BasePath`` / 版本 / 默认用户的权威来源，但可能因权限或策略不可读。
因此读取失败只降级，不让只读命令失败；也绝不写注册表。

Windows 模块通过构造参数注入，测试用假模块覆盖全部分支，不依赖测试机上的真实注册表。
"""

from __future__ import annotations

import contextlib
import os
import sys
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "RegistryDistro",
    "RegistryReader",
    "RegistrySnapshot",
    "WindowsRegistryReader",
    "unavailable_snapshot",
]

_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Lxss"


@dataclass(frozen=True, slots=True)
class RegistryDistro:
    """``Lxss\\{GUID}`` 下的一个发行版登记项。"""

    name: str
    key: str
    base_path: str | None
    version: int | None
    default_uid: int | None
    package_family: str | None
    flags: int | None
    state: int | None


@dataclass(frozen=True, slots=True)
class RegistrySnapshot:
    """一次注册表读取的完整快照。"""

    available: bool
    default_distribution: str | None
    default_version: int | None
    distros: tuple[RegistryDistro, ...]
    error: str | None

    @property
    def default_name(self) -> str | None:
        """把 ``DefaultDistribution``（通常是 GUID）解析成发行版名。"""
        target = self.default_distribution
        if not target:
            return None
        wanted = target.casefold()
        for distro in self.distros:
            if distro.key.casefold() == wanted or distro.name.casefold() == wanted:
                return distro.name
        return None

    def by_name(self) -> dict[str, RegistryDistro]:
        """按小写名称建索引，供与 ``-l -v`` 合并。"""
        return {distro.name.casefold(): distro for distro in self.distros}


class RegistryReader(Protocol):
    def snapshot(self) -> RegistrySnapshot: ...


def unavailable_snapshot(error: str) -> RegistrySnapshot:
    return RegistrySnapshot(
        available=False,
        default_distribution=None,
        default_version=None,
        distros=(),
        error=error,
    )


class WindowsRegistryReader:
    """读取 ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Lxss``。"""

    def __init__(self, *, winreg_module: Any = None, platform: str | None = None) -> None:
        self._winreg = winreg_module
        self._platform = platform

    def snapshot(self) -> RegistrySnapshot:
        if (self._platform or sys.platform) != "win32":
            return unavailable_snapshot("非 Windows 平台，无法读取 Lxss 注册表")

        module = self._winreg
        if module is None:
            try:
                import winreg as module
            except ImportError as exc:
                return unavailable_snapshot(f"导入 winreg 失败：{exc}")

        try:
            root = module.OpenKey(module.HKEY_CURRENT_USER, _KEY_PATH, 0, module.KEY_READ)
        except OSError as exc:
            return unavailable_snapshot(f"读取 Lxss 注册表失败：{_error_text(exc)}")

        try:
            default_distribution = _read_str(module, root, "DefaultDistribution")
            default_version = _read_int(module, root, "DefaultVersion")
            distros = _read_distros(module, root)
        finally:
            with contextlib.suppress(OSError):
                module.CloseKey(root)

        distros = tuple(sorted(distros, key=lambda item: item.name.casefold()))
        return RegistrySnapshot(
            available=True,
            default_distribution=default_distribution,
            default_version=default_version,
            distros=distros,
            error=None,
        )


def _read_distros(module: Any, root: Any) -> list[RegistryDistro]:
    distros: list[RegistryDistro] = []
    index = 0
    while True:
        try:
            guid = module.EnumKey(root, index)
        except OSError:
            break
        index += 1
        try:
            key = module.OpenKey(root, guid, 0, module.KEY_READ)
        except OSError:
            continue
        try:
            name = _read_str(module, key, "DistributionName")
            if not name:
                continue
            distros.append(
                RegistryDistro(
                    name=name,
                    key=guid,
                    base_path=_read_str(module, key, "BasePath"),
                    version=_read_int(module, key, "Version"),
                    default_uid=_read_int(module, key, "DefaultUid"),
                    package_family=_read_str(module, key, "PackageFamilyName"),
                    flags=_read_int(module, key, "Flags"),
                    state=_read_int(module, key, "State"),
                )
            )
        finally:
            with contextlib.suppress(OSError):
                module.CloseKey(key)
    return distros


def _read_value(module: Any, key: Any, name: str) -> Any:
    try:
        value, _kind = module.QueryValueEx(key, name)
    except OSError:
        return None
    return value


def _read_str(module: Any, key: Any, name: str) -> str | None:
    value = _read_value(module, key, name)
    if not isinstance(value, str) or not value:
        return None
    return os.path.expandvars(value)


def _read_int(module: Any, key: Any, name: str) -> int | None:
    value = _read_value(module, key, name)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _error_text(exc: OSError) -> str:
    return exc.strerror or str(exc)
