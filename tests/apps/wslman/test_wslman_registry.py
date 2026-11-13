"""``wslman._registry``：用假 winreg 模块覆盖全部读取分支。"""

from __future__ import annotations

from dataclasses import dataclass, field

from fcmd.apps.wslman._registry import WindowsRegistryReader, unavailable_snapshot


@dataclass
class FakeKey:
    name: str
    values: dict[str, object] = field(default_factory=dict)
    children: dict[str, FakeKey] = field(default_factory=dict)


class FakeWinreg:
    HKEY_CURRENT_USER = "HKCU"
    KEY_READ = 1

    def __init__(
        self,
        root: FakeKey,
        *,
        fail_root: bool = False,
        fail_children: frozenset[str] = frozenset(),
    ) -> None:
        self.root = root
        self.fail_root = fail_root
        self.fail_children = fail_children

    def OpenKey(self, parent: object, path: str, reserved: int = 0, access: int = 0) -> FakeKey:
        if parent == self.HKEY_CURRENT_USER:
            if self.fail_root:
                raise OSError(5, "拒绝访问")
            return self.root
        if path in self.fail_children:
            raise OSError(5, "拒绝访问")
        child = self.root.children.get(path) if parent is self.root else None
        if child is None:
            raise OSError(2, "找不到指定的注册表项")
        return child

    def EnumKey(self, key: FakeKey, index: int) -> str:
        names = list(key.children)
        if index >= len(names):
            raise OSError(259, "没有更多数据可用")
        return names[index]

    def QueryValueEx(self, key: FakeKey, name: str) -> tuple[object, int]:
        if name not in key.values:
            raise OSError(2, "找不到指定的注册表项")
        return key.values[name], 1

    def CloseKey(self, key: FakeKey) -> None:
        return None


def _root() -> FakeKey:
    ubuntu = FakeKey(
        "{A}",
        values={
            "DistributionName": "Ubuntu",
            "BasePath": r"C:\Users\u\Ubuntu",
            "Version": 2,
            "DefaultUid": 1000,
            "Flags": 15,
            "State": 1,
            "PackageFamilyName": "Ubuntu_123",
        },
    )
    debian = FakeKey(
        "{B}",
        values={
            "DistributionName": "debian",
            "BasePath": r"C:\Users\u\Debian",
            "Version": "1",
            "DefaultUid": "1000",
            "Flags": True,
            "State": "abc",
        },
    )
    return FakeKey(
        "root",
        values={"DefaultDistribution": "{A}", "DefaultVersion": 2},
        children={"{A}": ubuntu, "{B}": debian, "{C}": FakeKey("{C}", values={})},
    )


class TestWindowsRegistryReader:
    def test_non_windows_platform_is_unavailable(self) -> None:
        snapshot = WindowsRegistryReader(platform="linux").snapshot()
        assert snapshot.available is False
        assert "非 Windows" in (snapshot.error or "")

    def test_root_open_failure_degrades(self) -> None:
        reader = WindowsRegistryReader(winreg_module=FakeWinreg(_root(), fail_root=True), platform="win32")
        snapshot = reader.snapshot()
        assert snapshot.available is False
        assert "Lxss" in (snapshot.error or "")

    def test_reads_root_values_and_sorts_distros(self) -> None:
        reader = WindowsRegistryReader(winreg_module=FakeWinreg(_root()), platform="win32")
        snapshot = reader.snapshot()
        assert snapshot.available is True
        assert snapshot.error is None
        assert snapshot.default_distribution == "{A}"
        assert snapshot.default_version == 2
        assert [distro.name for distro in snapshot.distros] == ["debian", "Ubuntu"]
        assert snapshot.default_name == "Ubuntu"
        assert snapshot.by_name()["ubuntu"].base_path == r"C:\Users\u\Ubuntu"

    def test_string_and_bool_values_are_normalized(self) -> None:
        reader = WindowsRegistryReader(winreg_module=FakeWinreg(_root()), platform="win32")
        debian = reader.snapshot().by_name()["debian"]
        assert debian.version == 1
        assert debian.default_uid == 1000
        assert debian.flags == 1
        assert debian.state is None

    def test_child_open_failure_and_missing_name_are_skipped(self) -> None:
        reader = WindowsRegistryReader(
            winreg_module=FakeWinreg(_root(), fail_children=frozenset({"{B}"})),
            platform="win32",
        )
        snapshot = reader.snapshot()
        assert [distro.name for distro in snapshot.distros] == ["Ubuntu"]

    def test_default_name_falls_back_to_name_match(self) -> None:
        root = _root()
        root.values["DefaultDistribution"] = "ubuntu"
        reader = WindowsRegistryReader(winreg_module=FakeWinreg(root), platform="win32")
        assert reader.snapshot().default_name == "Ubuntu"

    def test_default_name_unknown(self) -> None:
        root = _root()
        root.values["DefaultDistribution"] = "{missing}"
        reader = WindowsRegistryReader(winreg_module=FakeWinreg(root), platform="win32")
        assert reader.snapshot().default_name is None
        root.values.pop("DefaultDistribution")
        assert WindowsRegistryReader(winreg_module=FakeWinreg(root), platform="win32").snapshot().default_name is None

    def test_unavailable_snapshot_helper(self) -> None:
        snapshot = unavailable_snapshot("原因")
        assert snapshot.available is False
        assert snapshot.default_name is None
        assert snapshot.by_name() == {}
