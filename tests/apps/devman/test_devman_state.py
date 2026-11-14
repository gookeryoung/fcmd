"""状态 / 配置 / 标记文件的读写与容错测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.devman import _state
from fcmd.apps.devman._errors import DevmanError
from fcmd.apps.devman._paths import Layout


def _layout(tmp_path: Path) -> Layout:
    return Layout(tmp_path / "root")


def test_config_defaults_when_missing(tmp_path: Path) -> None:
    config, warnings = _state.load_config(_layout(tmp_path))
    assert config.node_mirror == "auto"
    assert config.timeout_s == 120
    assert warnings == []


def test_config_field_level_fallback(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.root.mkdir(parents=True)
    layout.config_file.write_text(
        json.dumps(
            {
                "node": {"mirror": 123},
                "python": {"uv": "", "mirror": "   "},
                "network": {"timeout_s": 0, "progress": "yes"},
            }
        ),
        encoding="utf-8",
    )
    config, warnings = _state.load_config(layout)
    assert config.node_mirror == "auto"
    assert config.python_uv == "auto"
    assert config.python_mirror is None
    assert config.timeout_s == 120
    assert config.progress is True
    assert len(warnings) == 5


def test_config_corrupt_file(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.root.mkdir(parents=True)
    layout.config_file.write_text("{not json", encoding="utf-8")
    config, warnings = _state.load_config(layout)
    assert config.node_mirror == "auto"
    assert warnings and "无法解析" in warnings[0]


def test_config_custom_values_roundtrip(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    config = _state.Config(node_mirror="https://mirror.example/node", python_mirror="https://m", timeout_s=5)
    _state.write_atomic(layout.config_file, json.dumps(config.to_data()))
    loaded, warnings = _state.load_config(layout)
    assert loaded == config
    assert warnings == []


def test_state_missing_and_corrupt(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    state, warnings = _state.load_state(layout)
    assert state.active == {}
    assert warnings == []

    layout.root.mkdir(parents=True)
    layout.state_file.write_text("[]", encoding="utf-8")
    state, warnings = _state.load_state(layout)
    assert state.active == {}
    assert warnings and "不是 JSON 对象" in warnings[0]

    layout.state_file.write_text("{bad", encoding="utf-8")
    state, warnings = _state.load_state(layout)
    assert warnings and "已按「无活动版本」处理" in warnings[0]


def test_state_roundtrip_and_sanitize(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    state = _state.State(active={"node": "20.11.1", "python": "", "ruby": "3"}, path_added=["C:/bin"], path_previous_user_path="X")
    _state.save_state(layout, state)
    loaded, warnings = _state.load_state(layout)
    assert loaded.active == {"node": "20.11.1"}
    assert loaded.path_added == ["C:/bin"]
    assert loaded.path_previous_user_path == "X"
    assert warnings == []


def test_state_bad_types(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    layout.root.mkdir(parents=True)
    layout.state_file.write_text(json.dumps({"active": "oops", "path_added": "oops", "path_previous_user_path": 5}), encoding="utf-8")
    state, warnings = _state.load_state(layout)
    assert state.active == {}
    assert state.path_added == []
    assert state.path_previous_user_path is None
    assert warnings


def test_active_version_file(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    assert _state.active_version(layout, "node") is None
    _state.set_active(layout, "node", "20.11.1")
    assert _state.active_version(layout, "node") == "20.11.1"
    assert layout.active_file("node").read_text(encoding="utf-8") == "20.11.1\n"
    _state.remove_active(layout, "node")
    assert _state.active_version(layout, "node") is None
    _state.set_active(layout, "node", "20.12.0")
    _state.set_active(layout, "node", None)
    assert not layout.active_file("node").exists()


def test_marker_io(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    directory = layout.version_dir("node", "20.11.1")
    directory.mkdir(parents=True)
    assert _state.read_marker(directory) is None
    _state.write_marker(directory, {"schema": "x"})
    assert _state.read_marker(directory) == {"schema": "x"}
    assert (directory / _state.MARKER_NAME).read_text(encoding="utf-8").endswith("\n")
    (directory / _state.MARKER_NAME).write_text("[]", encoding="utf-8")
    assert _state.read_marker(directory) is None
    (directory / _state.MARKER_NAME).write_text("{bad", encoding="utf-8")
    assert _state.read_marker(directory) is None


def test_now_iso_has_timezone() -> None:
    text = _state.now_iso()
    assert "T" in text
    assert text[-6] in "+-" or text.endswith("Z")


def test_write_atomic_reports_unwritable_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = Path.replace

    def broken(self: Path, target: Path) -> Path:
        if self.name.endswith(".tmp"):
            raise OSError("sharing violation")
        return original(self, target)

    monkeypatch.setattr(Path, "replace", broken)
    with pytest.raises(DevmanError) as info:
        _state.write_atomic(tmp_path / "out.json", "{}")
    assert info.value.code == "ROOT_NOT_WRITABLE"
    assert not (tmp_path / "out.json").exists()
    assert list(tmp_path.glob("*.tmp")) == []
