"""pevieww 测试：设置持久化、几何恢复、注册与入口降级。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.peview import pevieww
from fcmd.apps.peview.pevieww import (
    PeviewSettings,
    add_recent_file,
    config_path,
    load_settings,
    resolve_geometry,
    save_settings,
    valid_geometry,
)
from fcmd.core.toolkit import _TOOL_REGISTRY


def test_tool_registered() -> None:
    """pevieww 应为单命令 GUI 工具。"""
    assert set(_TOOL_REGISTRY["pevieww"]) == {None}


class TestConfig:
    """设置文件路径与字段容错。"""

    def test_config_path_uses_fcmd_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "pevieww.json"

    def test_config_path_default_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("FCMD_HOME", raising=False)
        assert config_path() == Path.home() / ".fcmd" / "ui" / "pevieww.json"

    @pytest.mark.parametrize(
        ("text", "valid"),
        [
            ("1180x760", True),
            ("1180x760+120+80", True),
            ("1180x760-120-80", True),
            ("1180", False),
            ("abc", False),
            ("1180x760+", False),
            (None, False),
        ],
    )
    def test_valid_geometry(self, text: str | None, valid: bool) -> None:
        assert (valid_geometry(text) is not None) is valid

    def test_add_recent_file(self) -> None:
        a, b, c = (str(Path("/a.exe")), str(Path("/b.exe")), str(Path("/c.exe")))
        assert add_recent_file([a, b], Path(c)) == [c, a, b]

    def test_add_recent_file_dedupes(self) -> None:
        a, b = str(Path("/a.exe")), str(Path("/b.exe"))
        assert add_recent_file([a, b], Path(b)) == [b, a]

    def test_add_recent_file_limit(self) -> None:
        files = [str(Path(f"/{index}.exe")) for index in range(3)]
        expected = str(Path("/new.exe"))
        assert add_recent_file(files, Path("/new.exe"), limit=2) == [expected, files[0]]

    def test_load_settings_missing(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "none.json") == PeviewSettings()

    def test_load_settings_bad_json(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{bad", encoding="utf-8")
        assert load_settings(path) == PeviewSettings()

    def test_load_settings_non_object(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[1, 2]", encoding="utf-8")
        assert load_settings(path) == PeviewSettings()

    def test_load_settings_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            json.dumps(
                {
                    "file_path": "/app.exe",
                    "recent_files": ["/a.exe", 42],
                    "geometry": "1000x700+1+2",
                }
            ),
            encoding="utf-8",
        )
        settings = load_settings(path)
        assert settings.file_path == "/app.exe"
        assert settings.recent_files == ["/a.exe"]
        assert settings.geometry == "1000x700+1+2"

    def test_load_settings_invalid_geometry(self, tmp_path: Path) -> None:
        path = tmp_path / "geo.json"
        path.write_text(json.dumps({"geometry": "bad"}), encoding="utf-8")
        assert load_settings(path).geometry is None

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "pevieww.json"
        settings = PeviewSettings(
            file_path="/app.exe",
            recent_files=["/app.exe", "/b.dll"],
            geometry="1180x760+10+20",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings


class TestResolveGeometry:
    """启动时 geometry 恢复：位置夹回屏幕、尺寸不小于下限。"""

    def test_keeps_visible_position(self) -> None:
        assert resolve_geometry("1000x680+100+80", 1920, 1080) == "1000x680+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert resolve_geometry("1180x760+5000+5000", 1920, 1080) == "1180x760+740+320"

    def test_enforces_minimum_size(self) -> None:
        assert resolve_geometry("200x100+0+0", 1920, 1080) == "960x620+0+0"

    def test_falls_back_to_default_geometry(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == "1180x760+160+100"
        assert resolve_geometry("bad", 1920, 1080) == "1180x760+160+100"

    def test_clamps_default_into_small_screen(self) -> None:
        assert resolve_geometry(None, 800, 600) == "800x600+0+0"


def test_run_gui_import_error(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """缺少 tkinter 时输出中文提示且不抛堆栈。"""

    def boom(_settings_path: Path | None = None, _file: Path | None = None) -> None:
        raise ImportError("no tkinter")

    monkeypatch.setattr(pevieww, "PeviewWindow", boom)
    pevieww.run_gui()
    assert "tkinter 不可用" in capsys.readouterr().out


def test_run_gui_starts_mainloop(monkeypatch: pytest.MonkeyPatch) -> None:
    """窗口创建成功后进入 mainloop，并转发路径。"""
    calls: list[tuple[Path | None, Path | None]] = []

    class FakeWindow:
        def __init__(self, settings_path: Path | None = None, file: Path | None = None) -> None:
            self.root = self
            self.settings_path = settings_path
            self.file = file

        def mainloop(self) -> None:
            calls.append((self.settings_path, self.file))

    monkeypatch.setattr(pevieww, "PeviewWindow", FakeWindow)
    pevieww.run_gui(None, Path("x.exe"))
    assert calls == [(None, Path("x.exe"))]


def test_launch_cmd_forwards(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[Path | None] = []

    def fake_run_gui(settings_path: Path | None = None, file: Path | None = None) -> None:
        del settings_path
        calls.append(file)

    monkeypatch.setattr(pevieww, "run_gui", fake_run_gui)
    pevieww.launch_cmd(Path("x.exe"))
    pevieww.launch_cmd()
    assert calls == [Path("x.exe"), None]
