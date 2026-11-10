"""docdiffw 工具测试。

验证 ``fcmd.tools.office.docdiffw`` 模块：
- 工具注册
- 设置文件读写与字段级回退
- 窗口 geometry 校验
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.office import docdiffw
from fcmd.tools.office.docdiffw import (
    DocDiffSettings,
    config_path,
    load_settings,
    resolve_geometry,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """docdiffw 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["docdiffw"]) == {None}


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "docdiffw.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "docdiffw.json"

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == DocDiffSettings()

    def test_load_corrupt_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        assert load_settings(path) == DocDiffSettings()

    def test_load_non_dict_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == DocDiffSettings()

    def test_load_restores_values(self, tmp_path: Path) -> None:
        path = tmp_path / "ok.json"
        path.write_text(
            '{"old_doc": "old.docx", "new_doc": "new.docx", "output": "out.docx", '
            '"title": "合同对比", "show_changes": false, "compare_mode": " REVISED ", '
            '"geometry": "800x600+10+20"}',
            encoding="utf-8",
        )
        assert load_settings(path) == DocDiffSettings(
            old_doc="old.docx",
            new_doc="new.docx",
            output="out.docx",
            title="合同对比",
            show_changes=False,
            compare_mode="revised",
            geometry="800x600+10+20",
        )

    def test_load_invalid_fields_fall_back(self, tmp_path: Path) -> None:
        """非法字段逐项回退默认值。"""
        path = tmp_path / "invalid.json"
        path.write_text(
            '{"old_doc": 1, "new_doc": null, "output": [], "title": "", "compare_mode": 1, "geometry": "bad"}',
            encoding="utf-8",
        )
        assert load_settings(path) == DocDiffSettings()

    def test_load_invalid_compare_mode_falls_back(self, tmp_path: Path) -> None:
        """未知对比模式回退 original。"""
        path = tmp_path / "mode.json"
        path.write_text('{"compare_mode": "ultra"}', encoding="utf-8")
        assert load_settings(path).compare_mode == "original"

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "nested" / "settings.json"
        settings = DocDiffSettings(
            old_doc="old.docx",
            new_doc="new.docx",
            output="out.docx",
            title="合同对比",
            show_changes=False,
            compare_mode="revised",
            geometry="800x600+10+20",
        )
        save_settings(settings, path)
        assert load_settings(path) == settings

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(DocDiffSettings(), tmp_path / "x.json")  # 不应抛出


# ============================================================================ #
# 窗口 geometry
# ============================================================================ #
class TestGeometry:
    """geometry 校验与启动恢复。"""

    @pytest.mark.parametrize("text", ["720x480", "720x480+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "720", "720x480abc", "720 x 480"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None

    def test_keeps_visible_position(self) -> None:
        assert resolve_geometry("800x600+100+80", 1920, 1080) == "800x600+100+80"

    def test_pulls_back_offscreen(self) -> None:
        assert resolve_geometry("880x640+5000+5000", 1920, 1080) == "880x640+1040+440"

    def test_enforces_minimum_size(self) -> None:
        assert resolve_geometry("200x100+0+0", 1920, 1080) == "780x560+0+0"

    def test_falls_back_to_default_geometry(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == "900x620+240+160"
        assert resolve_geometry("bad", 1920, 1080) == "900x620+240+160"

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

        monkeypatch.setattr(docdiffw, "DocDiffWindow", fake_window)
        docdiffw.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(docdiffw, "DocDiffWindow", lambda _path: _Window())
        docdiffw.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(docdiffw, "run_gui", lambda: calls.append(True))
        docdiffw.launch_cmd()
        assert calls == [True]
