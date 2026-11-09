"""videoconvw 工具测试。

验证 ``fcmd.tools.media.videoconvw`` 模块：
- 工具注册
- 设置文件读写与非法字段回退
- 帧率 / 缩放 / geometry 解析
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.media import videoconvw
from fcmd.tools.media.videoconvw import (
    DEFAULT_GEOMETRY,
    VideoConvSettings,
    config_path,
    load_settings,
    parse_frame_rate,
    parse_scale,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """videoconvw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["videoconvw"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "videoconvw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "videoconvw.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == VideoConvSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == VideoConvSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == VideoConvSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"input_path": "in.mp4", "output_dir": "out", "output_format": "MKV", '
            '"video_codec": "libx265", "audio_codec": "opus", "video_bitrate": 5000, '
            '"audio_bitrate": 192, "frame_rate": 60, "scale": 0.5, "crf": 20, '
            '"preset": "slow", "remove_audio": true, "audio_file": "bg.mp3", '
            '"geometry": "900x700+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == VideoConvSettings(
            input_path="in.mp4",
            output_dir="out",
            output_format="mkv",
            video_codec="libx265",
            audio_codec="opus",
            video_bitrate=5000,
            audio_bitrate=192,
            frame_rate=60,
            scale=0.5,
            crf=20,
            preset="slow",
            remove_audio=True,
            audio_file="bg.mp3",
            geometry="900x700+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        path = tmp_path / "invalid.json"
        path.write_text(
            '{"input_path": 1, "output_dir": null, "output_format": "mp5", '
            '"video_codec": " ", "audio_codec": "", "video_bitrate": "x", '
            '"audio_bitrate": 0, "frame_rate": 999, "scale": 0, "crf": 99, '
            '"preset": "turbo", "remove_audio": "yes", "geometry": "bad"}',
            encoding="utf-8",
        )
        assert load_settings(path) == VideoConvSettings()

    def test_load_zero_frame_rate_means_source(self, tmp_path: Path) -> None:
        path = tmp_path / "zero.json"
        path.write_text('{"frame_rate": 0}', encoding="utf-8")
        assert load_settings(path).frame_rate == 0

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = VideoConvSettings(
            input_path="in.mp4",
            output_dir="out",
            output_format="webm",
            video_codec="libvpx-vp9",
            video_bitrate=3000,
            frame_rate=24,
            scale=0.75,
            remove_audio=True,
            geometry="900x700",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(VideoConvSettings(), tmp_path / "x.json")  # 不应抛出


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["720x480", "720x480+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "720", "720x480abc", "720 x 480"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None

    def test_default_geometry_valid(self) -> None:
        assert valid_geometry(DEFAULT_GEOMETRY) == DEFAULT_GEOMETRY


# ============================================================================ #
# 帧率 / 缩放解析
# ============================================================================ #
class TestParseFrameRate:
    """帧率输入解析。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("", None), ("0", None), (0, None), ("30", 30), (240, 240)],
    )
    def test_valid(self, raw: int | str, expected: int | None) -> None:
        assert parse_frame_rate(raw) == expected

    @pytest.mark.parametrize("raw", ["abc", -1, 241, None])
    def test_invalid_raises(self, raw: Any) -> None:
        with pytest.raises(ValueError, match="帧率"):
            parse_frame_rate(raw)


class TestParseScale:
    """缩放因子解析。"""

    @pytest.mark.parametrize(("raw", "expected"), [(1, 1.0), ("0.5", 0.5), (10, 10.0)])
    def test_valid(self, raw: int | float | str, expected: float) -> None:
        assert parse_scale(raw) == expected

    @pytest.mark.parametrize("raw", ["abc", 0, -1, 11, None])
    def test_invalid_raises(self, raw: Any) -> None:
        with pytest.raises(ValueError, match="缩放"):
            parse_scale(raw)


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(videoconvw, "VideoConvWindow", fake_window)
        videoconvw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(videoconvw, "VideoConvWindow", lambda _path: _Window())
        videoconvw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(videoconvw, "run_gui", lambda: calls.append(True))
        videoconvw.launch_cmd()
        assert calls == [True]
