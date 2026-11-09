"""img2icow 工具测试。

验证 ``fcmd.tools.media.img2icow`` 模块：
- 工具注册
- 尺寸文本解析 / 质量档位规范化
- 设置文件读写与异常回退
- 窗口 geometry 校验
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.media import img2icow
from fcmd.tools.media.img2icow import (
    DEFAULT_QUALITY,
    DEFAULT_SIZES_TEXT,
    Img2IcoSettings,
    config_path,
    load_settings,
    normalize_quality,
    parse_sizes_text,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """img2icow 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["img2icow"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "img2icow.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "img2icow.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == Img2IcoSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == Img2IcoSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == Img2IcoSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"input_path": "a.png", "output_path": "out.ico", "sizes": "16,32", '
            '"quality": "LOW", "geometry": "800x600+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == Img2IcoSettings(
            input_path="a.png",
            output_path="out.ico",
            sizes="16,32",
            quality="low",
            geometry="800x600+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        """非法尺寸 / 质量 / geometry 逐项回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text(
            '{"input_path": 1, "output_path": null, "sizes": "abc", "quality": "ultra", "geometry": "bad"}',
            encoding="utf-8",
        )
        assert load_settings(path) == Img2IcoSettings()

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = Img2IcoSettings(
            input_path="a.png",
            output_path="out.ico",
            sizes="16,32",
            quality="medium",
            geometry="800x600",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(Img2IcoSettings(), tmp_path / "x.json")  # 不应抛出


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["720x480", "720x480+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "720", "720x480abc", "720 x 480"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


# ============================================================================ #
# 尺寸与质量解析
# ============================================================================ #
class TestParseSizes:
    """尺寸文本解析。"""

    def test_comma_separated(self) -> None:
        assert parse_sizes_text("16, 32, 256") == [16, 32, 256]

    def test_space_separated(self) -> None:
        assert parse_sizes_text("16 32 64") == [16, 32, 64]

    def test_deduplicate(self) -> None:
        assert parse_sizes_text("32,16,32") == [16, 32]

    @pytest.mark.parametrize("text", ["", "   ", ","])
    def test_empty_raises(self, text: str) -> None:
        with pytest.raises(ValueError, match="尺寸"):
            parse_sizes_text(text)

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            parse_sizes_text("16,abc")

    def test_non_positive_raises(self) -> None:
        with pytest.raises(ValueError, match="正整数"):
            parse_sizes_text("0,16")


class TestQuality:
    """质量档位规范化。"""

    @pytest.mark.parametrize(("raw", "expected"), [("high", "high"), (" LOW ", "low"), ("Medium", "medium")])
    def test_valid(self, raw: str, expected: str) -> None:
        assert normalize_quality(raw) == expected

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的质量"):
            normalize_quality("ultra")


def test_defaults_are_valid() -> None:
    """默认尺寸与质量应能被自身解析。"""
    assert parse_sizes_text(DEFAULT_SIZES_TEXT)
    assert normalize_quality(DEFAULT_QUALITY) == DEFAULT_QUALITY


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(img2icow, "Img2IcoWindow", fake_window)
        img2icow.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(img2icow, "Img2IcoWindow", lambda _path: _Window())
        img2icow.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(img2icow, "run_gui", lambda: calls.append(True))
        img2icow.launch_cmd()
        assert calls == [True]
