"""``fcmd.tools._guiconfig`` 共享设置骨架测试（geometry 夹紧与设置读写）。"""

from __future__ import annotations

import json
from pathlib import Path

from fcmd.tools import _guiconfig


class TestClampGeometry:
    def test_invalid_inputs_return_none(self) -> None:
        assert _guiconfig.clamp_geometry(None, 1920, 1080) is None
        assert _guiconfig.clamp_geometry("abc", 1920, 1080) is None
        assert _guiconfig.clamp_geometry("100x200+1", 1920, 1080) is None

    def test_size_only_is_preserved(self) -> None:
        assert _guiconfig.clamp_geometry("900x700", 1920, 1080) == "900x700"

    def test_size_is_clamped_to_minimum(self) -> None:
        assert _guiconfig.clamp_geometry("200x100+0+0", 1920, 1080, min_width=560, min_height=520) == "560x520+0+0"

    def test_size_is_clamped_to_screen(self) -> None:
        assert _guiconfig.clamp_geometry("2000x1500+10+10", 1920, 1080) == "1920x1080+0+0"

    def test_position_is_pulled_back_on_screen(self) -> None:
        assert _guiconfig.clamp_geometry("700x600+5000+5000", 1920, 1080) == "700x600+1220+480"

    def test_negative_position_is_clamped_to_origin(self) -> None:
        assert _guiconfig.clamp_geometry("700x600-100-50", 1920, 1080) == "700x600+0+0"

    def test_zero_screen_falls_back_to_minimum(self) -> None:
        assert _guiconfig.clamp_geometry("900x700+10+10", 0, 0) == "480x360+0+0"

    def test_whitespace_is_trimmed(self) -> None:
        assert _guiconfig.clamp_geometry(" 800x600+20+30 ", 1920, 1080) == "800x600+20+30"


class TestSettingsIO:
    def test_write_then_read_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "ui" / "demo.json"
        _guiconfig.write_settings({"geometry": "800x600"}, path)
        assert _guiconfig.read_settings(path) == {"geometry": "800x600"}
        assert not path.with_suffix(".json.tmp").exists()
        assert not _guiconfig._backup_path(path).exists()

    def test_write_keeps_previous_content_as_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.json"
        _guiconfig.write_settings({"v": 1}, path)
        _guiconfig.write_settings({"v": 2}, path)
        assert _guiconfig.read_settings(path) == {"v": 2}
        assert json.loads(_guiconfig._backup_path(path).read_text(encoding="utf-8")) == {"v": 1}

    def test_corrupt_main_falls_back_to_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.json"
        _guiconfig.write_settings({"v": 1}, path)
        _guiconfig.write_settings({"v": 2}, path)
        path.write_text("{broken", encoding="utf-8")
        assert _guiconfig.read_settings(path) == {"v": 1}

    def test_non_object_main_falls_back_to_backup(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.json"
        _guiconfig.write_settings({"v": 1}, path)
        _guiconfig.write_settings({"v": 2}, path)
        path.write_text("[]", encoding="utf-8")
        assert _guiconfig.read_settings(path) == {"v": 1}

    def test_missing_files_return_none(self, tmp_path: Path) -> None:
        assert _guiconfig.read_settings(tmp_path / "demo.json") is None

    def test_corrupt_main_is_not_backed_up(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.json"
        _guiconfig.write_settings({"v": 1}, path)
        _guiconfig.write_settings({"v": 2}, path)
        path.write_text("{broken", encoding="utf-8")
        _guiconfig.write_settings({"v": 3}, path)
        assert json.loads(_guiconfig._backup_path(path).read_text(encoding="utf-8")) == {"v": 1}

    def test_write_failure_to_directory_target_is_silent(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.json"
        path.mkdir()
        _guiconfig.write_settings({"v": 1}, path)
        assert path.is_dir()
        assert not path.with_suffix(".json.tmp").exists()

    def test_write_failure_creating_directory_is_silent(self, tmp_path: Path) -> None:
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        _guiconfig.write_settings({"v": 1}, blocker / "ui" / "demo.json")

    def test_backup_write_failure_does_not_block_main_write(self, tmp_path: Path) -> None:
        path = tmp_path / "demo.json"
        _guiconfig.write_settings({"v": 1}, path)
        _guiconfig._backup_path(path).mkdir()
        _guiconfig.write_settings({"v": 2}, path)
        assert _guiconfig.read_settings(path) == {"v": 2}
