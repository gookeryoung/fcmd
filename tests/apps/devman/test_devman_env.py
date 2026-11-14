"""PATH 处理、shim 生成、shell 片段与冲突检测测试。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from fcmd.apps.devman import _env
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import set_active
from tests.apps.devman import _fakes


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


def test_path_entry_add_and_remove(tmp_path: Path) -> None:
    entry = str(tmp_path / "bin")
    assert _env.add_path_entry("C:\\A;C:\\B", entry) == f"{entry};C:\\A;C:\\B"
    assert _env.add_path_entry("C:\\A;C:\\B", entry, append=True) == f"C:\\A;C:\\B;{entry}"
    assert _env.add_path_entry(f"C:\\A;{entry}", entry) == f"C:\\A;{entry}"
    assert _env.add_path_entry(None, entry) == entry
    assert _env.remove_path_entry(f"C:\\A;{entry};C:\\B", entry) == ("C:\\A;C:\\B", 1)
    assert _env.remove_path_entry("C:\\A", entry) == ("C:\\A", 0)
    assert _env.remove_path_entry("", entry) == ("", 0)
    assert _env.remove_path_entry(f"{entry};;C:\\A;", entry) == (";C:\\A;", 1)
    assert _env.add_path_entry("C:\\A;;", entry) == f"{entry};C:\\A;;"
    assert _env.path_entry_present(f"C:\\A;{entry.upper()}", entry) is True
    assert _env.path_entry_present(None, entry) is False
    assert _env.path_entries("C:\\A;; ;C:\\B") == ["C:\\A", "C:\\B"]


def test_command_candidates_order(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "node.cmd").write_text("@echo off", encoding="utf-8")
    (second / "node.exe").write_bytes(b"MZ")
    found = _env.command_candidates("node", f"{first};{second}")
    assert [item.name for item in found] == ["node.cmd", "node.exe"]
    assert "node.exe" in _env.command_candidates("node.exe", f"{first};{second}")[0].name


def test_shim_scripts_are_crlf_wrappers() -> None:
    scripts = _env.shim_scripts()
    assert set(scripts) == {"node.cmd", "npm.cmd", "npx.cmd", "python.cmd", "python3.cmd"}
    node = scripts["node.cmd"]
    assert "\r\n" in node
    assert "active\\node.txt" in node
    assert "node.exe" in node
    assert node.rstrip().endswith("exit /b %ERRORLEVEL%")
    assert "call " in scripts["npm.cmd"]
    assert "python.exe" in scripts["python3.cmd"]


def test_write_shims_idempotent(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    written, unchanged = _env.write_shims(layout)
    assert set(written) == set(_env.shim_scripts())
    assert unchanged == []
    written, unchanged = _env.write_shims(layout)
    assert written == []
    assert len(unchanged) == len(_env.shim_scripts())


def test_shell_snippet_variants(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert "没有活动版本" in _env.shell_snippet(layout, "pwsh")

    _fakes.install_fake_node(layout, "20.11.1")
    _fakes.install_fake_python(layout, _fakes.PY_KEY, "3.12.15")
    set_active(layout, "node", "20.11.1")
    set_active(layout, "python", _fakes.PY_KEY)

    pwsh = _env.shell_snippet(layout, "pwsh")
    assert pwsh.startswith("$env:PATH = '") and "20.11.1" in pwsh and "python" in pwsh
    cmd = _env.shell_snippet(layout, "cmd")
    assert cmd.startswith('set "PATH=') and cmd.endswith(';%PATH%"')
    bash = _env.shell_snippet(layout, "bash")
    assert bash.startswith("export PATH='") and "\\" not in bash
    with pytest.raises(DevmanError) as info:
        _env.shell_snippet(layout, "fish")
    assert info.value.code == "INVALID_ARGUMENT"


def test_child_env_prepends_active_dirs(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_node(layout, "20.11.1")
    set_active(layout, "node", "20.11.1")
    env = _env.child_env(layout, {"PATH": "C:\\Windows"})
    assert env["PATH"].startswith(str(layout.version_dir("node", "20.11.1")))
    assert env["PATH"].endswith("C:\\Windows")
    assert _env.child_env(_layout(tmp_path / "other"), {"PATH": ""})["PATH"] == ""


def test_detect_environment_reports_resolution_and_markers(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _env.write_shims(layout)
    fake_bin = tmp_path / "external"
    fake_bin.mkdir()
    (fake_bin / "node.exe").write_bytes(b"MZ")
    env = {
        "PATH": f"{layout.shim_dir};{fake_bin}",
        "VIRTUAL_ENV": "C:/venv",
        "CONDA_PREFIX": "C:/conda",
        "NVM_HOME": "C:/nvm",
    }
    detected = _env.detect_environment(layout, env=env)
    assert detected["shim_on_path"] is True
    assert detected["shim_index"] == 0
    assert detected["path_resolution"]["node"] == [str(layout.shim_dir / "node.cmd"), str(fake_bin / "node.exe")]
    assert detected["markers"] == {"VIRTUAL_ENV": "C:/venv", "CONDA_PREFIX": "C:/conda", "NVM_HOME": "C:/nvm"}
    assert any("首先解析到" in item for item in detected["warnings"])
    assert any("虚拟环境" in item for item in detected["warnings"])
    assert any("conda" in item for item in detected["warnings"])
    assert detected["active"] == {"node": None, "python": None}


def test_shadowed_commands_lists_external_only(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _env.write_shims(layout)
    other = tmp_path / "other"
    other.mkdir()
    (other / "python.exe").write_bytes(b"MZ")
    shadowed = _env.shadowed_commands(layout, env={"PATH": f"{layout.shim_dir};{other}"})
    assert shadowed == {"python": [str(other / "python.exe")]}
    assert _env.shadowed_commands(layout, env={"PATH": str(layout.shim_dir)}) == {}


def test_install_and_remove_user_path(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    store = _fakes.FakeEnvStore({"Path": "C:\\Windows"})
    first = _env.install_path(layout, store=store)
    assert first["changed"] is True
    assert store.get("Path") == f"{layout.shim_dir};C:\\Windows"
    second = _env.install_path(layout, store=store)
    assert second["changed"] is False

    second_layout = _layout(tmp_path / "r2")
    appended = _env.install_path(second_layout, store=store, append=True)
    assert appended["changed"] is True
    assert store.get("Path") == f"{layout.shim_dir};C:\\Windows;{second_layout.shim_dir}"

    before_removal = store.get("Path")
    removed = _env.remove_path(layout, store=store)
    assert removed == {"changed": True, "removed": 1, "previous": before_removal}
    assert store.get("Path") == f"C:\\Windows;{second_layout.shim_dir}"
    again = _env.remove_path(layout, store=store)
    assert again["changed"] is False
    assert again["removed"] == 0


def test_python_hint_and_describe() -> None:
    layout = Layout(Path("C:/Dev/devman"))
    hint = _env.python_hint(layout, "cpython-3.12.15-windows-x86_64-none")
    assert hint.startswith("uv venv --python ")
    assert _env.describe_version("node", "20.11.1") == "node 20.11.1"


def test_default_env_store_returns_memory_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_env.sys, "platform", "linux")
    store = _env.default_env_store()
    assert isinstance(store, _env.MemoryEnvStore)
    store.set("Path", "X")
    assert store.get("Path") == "X"


@pytest.mark.skipif(os.name != "nt", reason="Windows 注册表实现")
def test_windows_env_store_reads_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    """只验证 ``WindowsEnvStore.get`` 的错误分支，不写真实注册表。"""
    import winreg

    store = _env.WindowsEnvStore()

    def missing(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr(winreg, "OpenKey", missing)
    assert store.get("Path") is None

    def denied(*args: object, **kwargs: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr(winreg, "OpenKey", denied)
    with pytest.raises(DevmanError) as info:
        store.get("Path")
    assert info.value.code == "PATH_WRITE_FAILED"


@pytest.mark.skipif(os.name != "nt", reason="Windows 注册表实现")
def test_windows_env_store_get_success_and_set(monkeypatch: pytest.MonkeyPatch) -> None:
    """用假的 winreg 覆盖读写成功路径，不触碰真实注册表。"""
    import winreg

    class Key:
        def __enter__(self) -> Key:
            return self

        def __exit__(self, *args: object) -> bool:
            return False

    monkeypatch.setattr(winreg, "OpenKey", lambda *args: Key())
    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: ("C:\\Windows", 1))
    store = _env.WindowsEnvStore()
    assert store.get("Path") == "C:\\Windows"

    monkeypatch.setattr(winreg, "QueryValueEx", lambda key, name: (1, 4))
    assert store.get("Path") is None

    written: list[tuple[str, str]] = []
    broadcasts: list[int] = []
    monkeypatch.setattr(winreg, "CreateKeyEx", lambda *args: Key())
    monkeypatch.setattr(winreg, "SetValueEx", lambda key, name, reserved, kind, value: written.append((name, value)))
    monkeypatch.setattr(_env, "_broadcast_environment_change", lambda: broadcasts.append(1))
    store.set("Path", "C:\\devman\\bin")
    assert written == [("Path", "C:\\devman\\bin")]
    assert broadcasts == [1]


@pytest.mark.skipif(os.name != "nt", reason="Windows 注册表实现")
def test_windows_env_store_set_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import winreg

    def denied(*args: object, **kwargs: object) -> None:
        raise OSError("denied")

    monkeypatch.setattr(winreg, "CreateKeyEx", denied)
    with pytest.raises(DevmanError) as info:
        _env.WindowsEnvStore().set("Path", "x")
    assert info.value.code == "PATH_WRITE_FAILED"
    assert "管理员" in (info.value.hint or "")


@pytest.mark.skipif(os.name != "nt", reason="Windows API")
def test_broadcast_environment_change_is_best_effort() -> None:
    assert _env._broadcast_environment_change() is None


def test_command_candidates_with_explicit_suffix(tmp_path: Path) -> None:
    (tmp_path / "node.exe").write_bytes(b"MZ")
    assert _env.command_candidates("node.exe", str(tmp_path)) == [tmp_path / "node.exe"]


def test_command_candidates_ignores_unreadable_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "node.exe").write_bytes(b"MZ")
    original = Path.is_file

    def broken(self: Path) -> bool:
        if self.name.lower() == "node.exe":
            raise OSError("locked")
        return original(self)

    monkeypatch.setattr(Path, "is_file", broken)
    assert _env.command_candidates("node", str(tmp_path)) == []
