"""根目录解析与布局。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fcmd.apps.devman import _paths
from fcmd.apps.devman._errors import DevmanError


def test_explicit_root_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_paths.ROOT_ENV, str(tmp_path / "env"))
    assert _paths.resolve_root(tmp_path / "explicit") == (tmp_path / "explicit").resolve()


def test_blank_explicit_falls_back_to_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_paths.ROOT_ENV, str(tmp_path / "env"))
    assert _paths.resolve_root("  ") == (tmp_path / "env").resolve()
    assert _paths.resolve_root(None) == (tmp_path / "env").resolve()


def test_missing_env_uses_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(_paths.ROOT_ENV, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    root = _paths.resolve_root()
    assert root == (tmp_path / "local" / "devman").resolve()

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(_paths.Path, "home", lambda: tmp_path / "home")
    assert _paths.resolve_root() == tmp_path / "home" / ".local" / "share" / "devman"


def test_default_root_without_localappdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(_paths.Path, "home", lambda: tmp_path / "home")
    assert _paths._default_root() == tmp_path / "home" / "AppData" / "Local" / "devman"


def test_normalize_tool_aliases() -> None:
    assert _paths.normalize_tool("NodeJS") == "node"
    assert _paths.normalize_tool("Python") == "python"
    with pytest.raises(DevmanError) as info:
        _paths.normalize_tool("ruby")
    assert info.value.code == "INVALID_ARGUMENT"


def test_layout_paths(tmp_path: Path) -> None:
    layout = _paths.Layout(tmp_path / "root")
    assert layout.tool_root("node") == layout.root / "node"
    assert layout.version_dir("python", "cpython-3.12.15").name == "cpython-3.12.15"
    assert layout.active_file("node").name == "node.txt"
    assert layout.cache_dir.name == "downloads"
    layout.ensure_bootstrap()
    assert layout.shim_dir.is_dir()
    assert layout.active_dir.is_dir()
    assert layout.cache_dir.is_dir()
