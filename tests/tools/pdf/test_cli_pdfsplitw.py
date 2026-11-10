"""pdfsplitw 工具测试。

验证 ``fcmd.tools.pdf.pdfsplitw`` 模块：
- 工具注册
- 设置文件读写与异常回退
- 模式 / 正整数解析与默认输出路径
- 窗口 geometry 校验与恢复
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.pdf import pdfsplitw
from fcmd.tools.pdf.pdfsplitw import (
    PdfSplitSettings,
    config_path,
    default_output_path,
    load_settings,
    normalize_mode,
    parse_positive_int,
    resolve_geometry,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """pdfsplitw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["pdfsplitw"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "pdfsplitw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "pdfsplitw.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == PdfSplitSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == PdfSplitSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == PdfSplitSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"input_path": "in.pdf", "output_dir": "out", "output_path": "picked.pdf", '
            '"mode": "RANGE", "parts": 3, "size": 5, "pages": "1-3", "geometry": "800x600+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == PdfSplitSettings(
            input_path="in.pdf",
            output_dir="out",
            output_path="picked.pdf",
            mode="range",
            parts=3,
            size=5,
            pages="1-3",
            geometry="800x600+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        """非法路径 / 模式 / 参数 / geometry 逐项回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text(
            '{"input_path": 1, "output_dir": 2, "output_path": 3, "mode": "bogus", '
            '"parts": 0, "size": -2, "pages": 9, "geometry": "bad"}',
            encoding="utf-8",
        )
        assert load_settings(path) == PdfSplitSettings()

    def test_load_bool_numbers_fall_back(self, tmp_path: Path) -> None:
        path = tmp_path / "bool.json"
        path.write_text('{"parts": true, "size": true}', encoding="utf-8")
        assert load_settings(path) == PdfSplitSettings()

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = PdfSplitSettings(
            input_path="in.pdf",
            output_dir="out",
            output_path="picked.pdf",
            mode="range",
            parts=4,
            size=8,
            pages="1,3-5",
            geometry="800x600",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(PdfSplitSettings(), tmp_path / "x.json")  # 不应抛出


class TestParsing:
    """模式与参数解析。"""

    @pytest.mark.parametrize(("raw", "expected"), [("parts", "parts"), (" SIZE ", "size"), ("Range", "range")])
    def test_normalize_mode_valid(self, raw: str, expected: str) -> None:
        assert normalize_mode(raw) == expected

    def test_normalize_mode_invalid(self) -> None:
        with pytest.raises(ValueError, match="无效的分割模式"):
            normalize_mode("bogus")

    @pytest.mark.parametrize(("raw", "expected"), [(2, 2), (" 3 ", 3)])
    def test_positive_int_valid(self, raw: int | str, expected: int) -> None:
        assert parse_positive_int(raw, "分割份数") == expected

    def test_positive_int_not_number(self) -> None:
        with pytest.raises(ValueError, match="须为整数"):
            parse_positive_int("abc", "分割份数")

    @pytest.mark.parametrize("raw", [0, -3])
    def test_positive_int_not_positive(self, raw: int) -> None:
        with pytest.raises(ValueError, match="须为正整数"):
            parse_positive_int(raw, "分割份数")

    def test_default_output_path(self, tmp_path: Path) -> None:
        assert default_output_path(tmp_path / "a.pdf") == tmp_path / "a_extracted.pdf"


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["780x580", "780x580+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "780", "780x580abc", "780 x 580"])
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
        assert resolve_geometry(None, 1920, 1080) == "880x700+240+130"
        assert resolve_geometry("bad", 1920, 1080) == "880x700+240+130"

    def test_clamps_default_into_small_screen(self) -> None:
        assert resolve_geometry(None, 800, 600) == "800x600+0+0"


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(pdfsplitw, "PdfSplitWindow", fake_window)
        pdfsplitw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(pdfsplitw, "PdfSplitWindow", lambda _path: _Window())
        pdfsplitw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(pdfsplitw, "run_gui", lambda: calls.append(True))
        pdfsplitw.launch_cmd()
        assert calls == [True]
