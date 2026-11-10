"""img2pdfw 工具测试。

验证 ``fcmd.tools.pdf.img2pdfw`` 模块：
- 工具注册
- 设置文件读写与异常回退
- 页面尺寸显示 / DPI 解析 / 默认输出路径
- 窗口 geometry 校验
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.pdf import img2pdfw
from fcmd.tools.pdf.img2pdfw import (
    DEFAULT_DPI,
    Img2PdfSettings,
    config_path,
    default_output_path,
    format_page_size,
    load_settings,
    parse_dpi,
    resolve_geometry,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """img2pdfw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["img2pdfw"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "img2pdfw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "img2pdfw.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == Img2PdfSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == Img2PdfSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == Img2PdfSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"input_path": "pics", "output_path": "out.pdf", "page_size": "A4", '
            '"dpi": 300, "normalize": false, "geometry": "800x600+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == Img2PdfSettings(
            input_path="pics",
            output_path="out.pdf",
            page_size="a4",
            dpi=300,
            normalize=False,
            geometry="800x600+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        """非法输入 / 页面尺寸 / DPI / geometry 逐项回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text(
            '{"input_path": 1, "output_path": null, "page_size": "A0", '
            '"dpi": 5000, "normalize": "yes", "geometry": "bad"}',
            encoding="utf-8",
        )
        assert load_settings(path) == Img2PdfSettings()

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = Img2PdfSettings(
            input_path="pics",
            output_path="out.pdf",
            page_size="a4",
            dpi=300,
            normalize=False,
            geometry="800x600",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(Img2PdfSettings(), tmp_path / "x.json")  # 不应抛出


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["720x480", "720x480+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "720", "720x480abc", "720 x 480"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


class TestResolveGeometry:
    """启动时 geometry 恢复：位置夹回屏幕、尺寸不小于下限。"""

    def test_keeps_visible_position(self) -> None:
        assert resolve_geometry("820x600+100+80", 1920, 1080) == "820x600+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert resolve_geometry("900x680+5000+5000", 1920, 1080) == "900x680+1020+400"

    def test_enforces_minimum_size(self) -> None:
        assert resolve_geometry("200x100+0+0", 1920, 1080) == "780x580+0+0"

    def test_falls_back_to_default_geometry(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == "900x680+240+140"
        assert resolve_geometry("bad", 1920, 1080) == "900x680+240+140"

    def test_clamps_default_into_small_screen(self) -> None:
        assert resolve_geometry(None, 800, 600) == "800x600+0+0"


# ============================================================================ #
# 页面尺寸 / DPI / 默认输出
# ============================================================================ #
class TestPageSize:
    """页面尺寸显示值。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("auto", "auto"), ("a4", "A4"), (" A3 ", "A3"), ("letter", "letter"), ("Legal", "legal")],
    )
    def test_format(self, raw: str, expected: str) -> None:
        assert format_page_size(raw) == expected

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="页面尺寸"):
            format_page_size("A0")


class TestParseDpi:
    """DPI 解析。"""

    @pytest.mark.parametrize(("raw", "expected"), [(150, 150), ("300", 300), (" 72 ", 72)])
    def test_valid(self, raw: int | str, expected: int) -> None:
        assert parse_dpi(raw) == expected

    @pytest.mark.parametrize("raw", ["abc", 0, 5000])
    def test_invalid_raises(self, raw: int | str) -> None:
        with pytest.raises(ValueError, match="DPI"):
            parse_dpi(raw)

    def test_default_is_valid(self) -> None:
        assert parse_dpi(DEFAULT_DPI) == DEFAULT_DPI


class TestDefaultOutput:
    """默认输出路径。"""

    def test_directory(self, tmp_path: Path) -> None:
        source = tmp_path / "pics"
        source.mkdir()
        assert default_output_path(source) == source / "pics.pdf"

    def test_file(self, tmp_path: Path) -> None:
        image = tmp_path / "a.png"
        image.write_bytes(b"x")
        assert default_output_path(image) == tmp_path / "a.pdf"


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(img2pdfw, "Img2PdfWindow", fake_window)
        img2pdfw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(img2pdfw, "Img2PdfWindow", lambda _path: _Window())
        img2pdfw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(img2pdfw, "run_gui", lambda: calls.append(True))
        img2pdfw.launch_cmd()
        assert calls == [True]
