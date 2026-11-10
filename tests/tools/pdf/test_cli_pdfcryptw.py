"""pdfcryptw 工具测试。

验证 ``fcmd.tools.pdf.pdfcryptw`` 模块：
- 工具注册
- 设置文件读写与异常回退（密码不落盘）
- 并发线程解析
- 窗口 geometry 校验与恢复
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.pdf import pdfcryptw
from fcmd.tools.pdf.pdfcryptw import (
    MAX_WORKERS,
    MIN_WORKERS,
    PdfCryptSettings,
    config_path,
    load_settings,
    parse_workers,
    resolve_geometry,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """pdfcryptw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["pdfcryptw"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "pdfcryptw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "pdfcryptw.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == PdfCryptSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == PdfCryptSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == PdfCryptSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"directory": "docs", "workers": 8, "recursive": false, "geometry": "800x600+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == PdfCryptSettings(
            directory="docs",
            workers=8,
            recursive=False,
            geometry="800x600+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        """非法目录 / 线程数 / 递归 / geometry 逐项回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text(
            '{"directory": 1, "workers": 5000, "recursive": "yes", "geometry": "bad"}',
            encoding="utf-8",
        )
        assert load_settings(path) == PdfCryptSettings()

    def test_load_worker_bool_falls_back(self, tmp_path: Path) -> None:
        path = tmp_path / "bool.json"
        path.write_text('{"workers": true}', encoding="utf-8")
        assert load_settings(path).workers == PdfCryptSettings().workers

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = PdfCryptSettings(
            directory="docs",
            workers=6,
            recursive=False,
            geometry="800x600",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(PdfCryptSettings(), tmp_path / "x.json")  # 不应抛出


class TestParseWorkers:
    """并发线程数解析。"""

    @pytest.mark.parametrize(("raw", "expected"), [(4, 4), ("8", 8), (" 2 ", 2)])
    def test_valid(self, raw: int | str, expected: int) -> None:
        assert parse_workers(raw) == expected

    @pytest.mark.parametrize("raw", ["abc", None])
    def test_not_integer(self, raw: object) -> None:
        with pytest.raises(ValueError, match="整数"):
            parse_workers(raw)  # type: ignore[arg-type]

    @pytest.mark.parametrize("raw", [MIN_WORKERS - 1, MAX_WORKERS + 1])
    def test_out_of_range(self, raw: int) -> None:
        with pytest.raises(ValueError, match="并发线程数"):
            parse_workers(raw)


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["760x560", "760x560+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "760", "760x560abc", "760 x 560"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


class TestResolveGeometry:
    """启动时 geometry 恢复：位置夹回屏幕、尺寸不小于下限。"""

    def test_keeps_visible_position(self) -> None:
        assert resolve_geometry("820x600+100+80", 1920, 1080) == "820x600+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert resolve_geometry("900x680+5000+5000", 1920, 1080) == "900x680+1020+400"

    def test_enforces_minimum_size(self) -> None:
        assert resolve_geometry("200x100+0+0", 1920, 1080) == "760x560+0+0"

    def test_falls_back_to_default_geometry(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == "860x680+240+140"
        assert resolve_geometry("bad", 1920, 1080) == "860x680+240+140"

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

        monkeypatch.setattr(pdfcryptw, "PdfCryptWindow", fake_window)
        pdfcryptw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(pdfcryptw, "PdfCryptWindow", lambda _path: _Window())
        pdfcryptw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(pdfcryptw, "run_gui", lambda: calls.append(True))
        pdfcryptw.launch_cmd()
        assert calls == [True]
