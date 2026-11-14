"""版本模型与规格解析的纯逻辑测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.devman import _errors, _toolchain
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout
from fcmd.apps.devman._state import MARKER_SCHEMA, write_marker
from tests.apps.devman import _fakes


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


def test_parse_spec_variants() -> None:
    assert _toolchain.parse_spec("node").raw == ""
    assert _toolchain.parse_spec("nodejs@20").tool == "node"
    assert _toolchain.parse_spec("Python@3.12.15").raw == "3.12.15"
    assert _toolchain.parse_spec("node@LTS").channel == "lts"
    assert _toolchain.parse_spec("node@latest").channel == "latest"
    assert _toolchain.parse_spec("node@20.11.1").text == "node@20.11.1"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "INVALID_ARGUMENT"),
        ("ruby@3", "INVALID_ARGUMENT"),
        ("node@../evil", "INVALID_ARGUMENT"),
        ("node@/abs", "INVALID_ARGUMENT"),
        ("python@lts", "INVALID_ARGUMENT"),
    ],
)
def test_parse_spec_rejects_invalid(text: str, code: str) -> None:
    with pytest.raises(DevmanError) as info:
        _toolchain.parse_spec(text)
    assert info.value.code == code


def test_version_helpers() -> None:
    assert _toolchain.version_of("node", "20.11.1") == "20.11.1"
    assert _toolchain.version_of("python", _fakes.PY_KEY) == "3.12.15"
    assert _toolchain.version_of("python", "weird") == "weird"
    assert _toolchain.sort_key("3.12.15") == (3, 12, 15)
    assert _toolchain.matches("20", "20.11.1")
    assert _toolchain.matches("20.11", "20.11.1")
    assert not _toolchain.matches("21", "20.11.1")
    assert _toolchain.matches("", "20.11.1")
    assert _toolchain.matches(_fakes.PY_KEY, "3.12.15", name=_fakes.PY_KEY)
    assert _toolchain.matches("cpython-3.12", "3.12.15", name=_fakes.PY_KEY)
    assert not _toolchain.matches("cpython-3.13", "3.12.15", name=_fakes.PY_KEY)


def test_list_installed_empty(tmp_path: Path) -> None:
    assert _toolchain.list_installed(_layout(tmp_path), "node") == []


def test_list_installed_marks_incomplete(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_node(layout, "20.11.1")
    broken = _fakes.install_fake_node(layout, "20.12.0", complete=False)
    (broken / "node.exe").write_bytes(b"x")
    rows = _toolchain.list_installed(layout, "node")
    assert [row.name for row in rows] == ["20.11.1", "20.12.0"]
    assert [row.complete for row in rows] == [True, False]
    assert _toolchain.executable_path(layout, "node", "20.11.1").is_file()
    assert _toolchain.executable_path(layout, "python", _fakes.PY_KEY).name == "python.exe"


def test_is_complete_rejects_bad_markers(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    directory = layout.version_dir("node", "20.11.1")
    directory.mkdir(parents=True)
    (directory / "node.exe").write_bytes(b"x")
    assert not _toolchain.is_complete(layout, "node", "20.11.1")

    for payload in (
        {"schema": "wrong", "tool": "node", "version": "20.11.1"},
        {"schema": MARKER_SCHEMA, "tool": "python", "version": "20.11.1"},
        {"schema": MARKER_SCHEMA, "tool": "node", "version": "20.0.0"},
    ):
        write_marker(directory, payload)
        assert not _toolchain.is_complete(layout, "node", "20.11.1")

    write_marker(directory, {"schema": MARKER_SCHEMA, "tool": "node", "version": "20.11.1"})
    assert _toolchain.is_complete(layout, "node", "20.11.1")
    (directory / "node.exe").unlink()
    assert not _toolchain.is_complete(layout, "node", "20.11.1")


def test_resolve_installed_prefers_highest(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_node(layout, "20.11.1")
    _fakes.install_fake_node(layout, "20.12.0")
    row = _toolchain.resolve_installed(layout, _toolchain.parse_spec("node@20"))
    assert row.version == "20.12.0"
    with pytest.raises(DevmanError) as info:
        _toolchain.resolve_installed(layout, _toolchain.parse_spec("node@21"))
    assert info.value.code == "VERSION_NOT_FOUND"
    assert info.value.kind == "not_found"


def test_resolve_installed_incomplete_hint(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    _fakes.install_fake_node(layout, "20.11.1", complete=False)
    with pytest.raises(DevmanError) as info:
        _toolchain.resolve_installed(layout, _toolchain.parse_spec("node@20"))
    assert info.value.code == "INCOMPLETE_INSTALL"
    assert "install" in (info.value.hint or "")
    row = _toolchain.resolve_installed(layout, _toolchain.parse_spec("node@20"), require_complete=False)
    assert row.name == "20.11.1"


def test_list_installed_tolerates_missing_or_broken_root(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert _toolchain.list_installed(layout, "node") == []
    layout.node_root.parent.mkdir(parents=True)
    layout.node_root.write_text("not a directory", encoding="utf-8")
    assert _toolchain.list_installed(layout, "node") == []


def test_list_installed_ignores_alias_links(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """uv 的 ``<impl>-<major>.<minor>`` junction 别名不是真实版本。"""
    layout = _layout(tmp_path)
    _fakes.install_fake_python(layout, _fakes.PY_KEY, "3.12.15")
    alias = layout.python_root / "cpython-3.12-windows-x86_64-none"
    alias.mkdir()
    original = Path.is_symlink

    def is_symlink(self: Path) -> bool:
        return self == alias or original(self)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    rows = _toolchain.list_installed(layout, "python")
    assert [row.name for row in rows] == [_fakes.PY_KEY]


def test_usage_error_factory() -> None:
    exc = _errors.usage_error("缺少参数", hint="看帮助")
    assert exc.code == "USAGE"
    assert exc.kind == "usage"
    assert exc.hint == "看帮助"
