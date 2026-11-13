"""``wslman._distro``：合并规则与两种呈现。"""

from __future__ import annotations

from fcmd.apps.wslman._decode import DistroRow
from fcmd.apps.wslman._disk import format_bytes
from fcmd.apps.wslman._distro import (
    Distro,
    format_info,
    format_list,
    merge,
    state_label,
    to_data,
    with_disk,
)
from tests.apps.wslman._fakes import registry_distro, snapshot


def _row(name: str, *, state: str = "running", version: int = 2, default: bool = False) -> DistroRow:
    return DistroRow(name=name, state=state, state_raw=state.title(), version=version, default=default)


class TestMerge:
    def test_attaches_registry_details(self) -> None:
        snap = snapshot(
            distros=(registry_distro("Ubuntu", base_path=r"C:\Users\u\Ubuntu"),),
            default_distribution="{Ubuntu}",
        )
        view = merge((_row("Ubuntu", default=True),), snap)
        distro = view.distros[0]
        assert distro.base_path == r"C:\Users\u\Ubuntu"
        assert distro.default_uid == 1000
        assert distro.registered is True
        assert distro.listed is True
        assert view.default == "Ubuntu"
        assert view.warnings == ()

    def test_row_without_registry_entry_warns(self) -> None:
        view = merge((_row("Alpine"),), snapshot())
        assert any("未在 Lxss 注册表" in warning for warning in view.warnings)
        assert view.distros[0].base_path is None

    def test_registry_only_distro_is_added_as_unknown(self) -> None:
        view = merge((), snapshot(distros=(registry_distro("Ubuntu"),)))
        distro = view.distros[0]
        assert distro.state == "unknown"
        assert distro.listed is False
        assert any("未出现在" in warning for warning in view.warnings)

    def test_default_mismatch_warns_and_prefers_marker(self) -> None:
        snap = snapshot(
            distros=(registry_distro("Ubuntu"), registry_distro("Debian")),
            default_distribution="{Debian}",
        )
        view = merge((_row("Ubuntu", default=True), _row("Debian", state="stopped")), snap)
        assert view.default == "Ubuntu"
        assert any("默认发行版不一致" in warning for warning in view.warnings)

    def test_default_falls_back_to_registry(self) -> None:
        snap = snapshot(distros=(registry_distro("Debian"),), default_distribution="{Debian}")
        view = merge((_row("Debian"),), snap)
        assert view.default == "Debian"
        assert any("按注册表补全" in warning for warning in view.warnings)

    def test_duplicate_registry_names_keep_first(self) -> None:
        snap = snapshot(distros=(registry_distro("Ubuntu"), registry_distro("ubuntu")))
        view = merge((), snap)
        assert len(view.distros) == 1
        assert any("重名" in warning for warning in view.warnings)

    def test_duplicate_rows_keep_first(self) -> None:
        view = merge((_row("Ubuntu"), _row("ubuntu", state="stopped")), snapshot())
        assert len(view.distros) == 1
        assert view.distros[0].state == "running"

    def test_disk_sizes_are_case_insensitive(self) -> None:
        view = merge((_row("Ubuntu"),), snapshot(), disk_sizes={"UBUNTU": (1024, True)})
        assert view.distros[0].disk_bytes == 1024
        assert view.distros[0].disk_estimated is True

    def test_find_is_case_insensitive(self) -> None:
        view = merge((_row("Ubuntu"),), snapshot())
        assert view.find("ubuntu") is not None
        assert view.find("Debian") is None


class TestWithDisk:
    def test_updates_matching_distro(self) -> None:
        view = merge((_row("Ubuntu"), _row("Debian")), snapshot())
        updated = with_disk(view, {"ubuntu": (2048, False)})
        assert updated.distros[0].disk_bytes == 2048
        assert updated.distros[1].disk_bytes is None


class TestFormatting:
    def test_empty_list(self) -> None:
        text = format_list(merge((), snapshot()), disk=False)
        assert "没有已安装的 WSL 发行版" in text

    def test_list_with_disk_and_warnings(self) -> None:
        view = merge((_row("Ubuntu", default=True),), snapshot())
        view = with_disk(view, {"Ubuntu": (1024**3, True)})
        text = format_list(view, disk=True)
        assert "*" in text
        assert "运行中" in text
        assert "≈1.0 GiB" in text
        assert "默认发行版：Ubuntu" in text

    def test_list_without_disk_hides_size(self) -> None:
        view = merge((_row("Ubuntu"),), snapshot())
        text = format_list(view, disk=False)
        assert "占用" not in text

    def test_state_label_uses_raw_when_unknown(self) -> None:
        distro = Distro(name="X", state="unknown", state_raw="Paused", version=2, default=False)
        assert state_label(distro) == "Paused"

    def test_info_full(self) -> None:
        distro = Distro(
            name="Ubuntu",
            state="running",
            state_raw="Running",
            version=2,
            default=True,
            base_path=r"C:\Users\u\Ubuntu",
            default_uid=1000,
            package_family="Ubuntu_123",
            flags=15,
            disk_bytes=1024**3,
            disk_estimated=True,
            listed=True,
            registered=True,
        )
        text = format_info(distro, wslconfig_lines=["[boot]", "systemd=true"])
        assert "名称: Ubuntu" in text
        assert "Flags: 0x0000000f" in text
        assert "≈1.0 GiB" in text
        assert "/etc/wsl.conf:" in text
        assert "  systemd=true" in text
        assert "wsl --list --verbose + Lxss 注册表" in text

    def test_info_unknown_fields(self) -> None:
        distro = Distro(name="X", state="unknown", state_raw="未列出", version=None, default=False, listed=False)
        text = format_info(distro)
        assert "版本: 未知" in text
        assert "BasePath: 未知" in text
        assert "Flags: 未知" in text
        assert "仅 Lxss 注册表" in text
        assert "状态: 未列出" in text

    def test_info_listed_only_source(self) -> None:
        distro = Distro(name="X", state="stopped", state_raw="Stopped", version=1, default=False, registered=False)
        assert "仅 wsl --list --verbose" in format_info(distro)

    def test_to_data_shape(self) -> None:
        snap = snapshot(distros=(registry_distro("Ubuntu"),), default_distribution="{Ubuntu}")
        data = to_data(merge((_row("Ubuntu", default=True),), snap))
        assert data["schema"] == "wslman/list/1"
        assert data["count"] == 1
        assert data["default"] == "Ubuntu"
        registry = data["registry"]
        assert isinstance(registry, dict)
        assert registry["count"] == 1
        distros = data["distros"]
        assert isinstance(distros, list)
        assert distros[0]["name"] == "Ubuntu"

    def test_format_bytes_units(self) -> None:
        assert format_bytes(None) == "未知"
        assert format_bytes(512) == "512 B"
        assert format_bytes(1536) == "1.5 KiB"
        assert format_bytes(2 * 1024**3, estimated=True) == "≈2.0 GiB"
