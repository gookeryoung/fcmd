"""regexvalidatew 工具测试。

验证 ``fcmd.tools.text.regexvalidatew`` 模块：
- 工具注册
- 设置文件读写与异常回退（含历史记录解析与上限）
- 历史去重 / 截断与下拉项文本
- 窗口 geometry 校验
- GUI 启动入口的降级行为
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from fcmd.core.toolkit import _TOOL_REGISTRY
from fcmd.tools.text import regexvalidatew
from fcmd.tools.text.regexvalidate import RegexValidator, build_flags
from fcmd.tools.text.regexvalidatew import (
    DEFAULT_GEOMETRY,
    MAX_HISTORY_ITEMS,
    REGEX_EXAMPLES,
    RegexExample,
    RegexHistoryEntry,
    RegexValidateSettings,
    config_path,
    example_label,
    history_label,
    load_settings,
    push_history,
    resolve_geometry,
    save_settings,
    valid_geometry,
)


# ============================================================================ #
# 注册验证
# ============================================================================ #
def test_tool_registered() -> None:
    """regexvalidatew 应以单命令形态注册。"""
    assert set(_TOOL_REGISTRY["regexvalidatew"]) == {None}
    assert "regexvalidate" in _TOOL_REGISTRY


# ============================================================================ #
# 配置路径与设置文件
# ============================================================================ #
class TestSettings:
    """设置文件读写。"""

    def test_config_path_uses_fcmd_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """FCMD_HOME 优先。"""
        monkeypatch.setenv("FCMD_HOME", str(tmp_path))
        assert config_path() == tmp_path / "ui" / "regexvalidate.json"

    def test_config_path_falls_back_to_home(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """未设置 FCMD_HOME 时使用 ``~/.fcmd``。"""
        monkeypatch.delenv("FCMD_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert config_path() == tmp_path / ".fcmd" / "ui" / "regexvalidate.json"

    def test_default_geometry_is_valid(self) -> None:
        assert valid_geometry(DEFAULT_GEOMETRY) == DEFAULT_GEOMETRY

    def test_save_load_roundtrip(self, tmp_path: Path) -> None:
        """保存后能原样读回（父目录自动创建）。"""
        path = tmp_path / "nested" / "regexvalidate.json"
        settings = RegexValidateSettings(
            pattern="\\d+",
            test_string="abc123",
            ignorecase=True,
            multiline=True,
            dotall=True,
            history=[RegexHistoryEntry(pattern="a+", test_string="aaa", ignorecase=True)],
            geometry="920x680+10+20",
        )
        save_settings(settings, path)
        assert path.is_file()
        assert load_settings(path) == settings

    def test_load_missing_file_returns_defaults(self, tmp_path: Path) -> None:
        assert load_settings(tmp_path / "missing.json") == RegexValidateSettings()

    def test_load_invalid_json_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("{oops", encoding="utf-8")
        assert load_settings(path) == RegexValidateSettings()

    def test_load_non_object_returns_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "list.json"
        path.write_text("[]", encoding="utf-8")
        assert load_settings(path) == RegexValidateSettings()

    def test_load_wrong_types_are_filtered(self, tmp_path: Path) -> None:
        """字段类型不符时逐项回退默认值。"""
        path = tmp_path / "wrong.json"
        path.write_text(
            '{"pattern": 1, "test_string": null, "ignorecase": "yes", '
            '"multiline": 0, "dotall": true, "geometry": "bad", "history": "no"}',
            encoding="utf-8",
        )
        assert load_settings(path) == RegexValidateSettings(dotall=True)

    def test_load_history_skips_invalid_entries(self, tmp_path: Path) -> None:
        path = tmp_path / "history.json"
        path.write_text(
            '{"history": [{"pattern": "a"}, {"pattern": ""}, "bad", '
            '{"pattern": 5}, {"pattern": "b", "test_string": 3, "ignorecase": true}]}',
            encoding="utf-8",
        )
        assert load_settings(path).history == [
            RegexHistoryEntry(pattern="a"),
            RegexHistoryEntry(pattern="b", ignorecase=True),
        ]

    def test_load_history_is_capped(self, tmp_path: Path) -> None:
        path = tmp_path / "many.json"
        entries = [RegexHistoryEntry(pattern=f"p{i}") for i in range(MAX_HISTORY_ITEMS + 5)]
        save_settings(RegexValidateSettings(history=entries), path)
        assert len(load_settings(path).history) == MAX_HISTORY_ITEMS

    def test_save_ignores_oserror(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """写入失败时静默忽略，不影响窗口关闭。"""

        def boom(*args: Any, **kwargs: Any) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "write_text", boom)
        save_settings(RegexValidateSettings(), tmp_path / "x.json")  # 不应抛出


class TestValidGeometry:
    """窗口 geometry 校验。"""

    @pytest.mark.parametrize("text", ["720x480", "720x480+300+200", "800x600-10-20"])
    def test_valid(self, text: str) -> None:
        assert valid_geometry(text) == text

    @pytest.mark.parametrize("text", [None, "", "720", "720x480abc", "720 x 480"])
    def test_invalid(self, text: str | None) -> None:
        assert valid_geometry(text) is None


class TestResolveGeometry:
    """启动时的 geometry 恢复：可见范围夹紧与默认回落。"""

    def test_keeps_visible_position(self) -> None:
        assert resolve_geometry("920x680+100+80", 1920, 1080) == "920x680+100+80"

    def test_pulls_offscreen_back(self) -> None:
        assert resolve_geometry("920x680+5000+5000", 1920, 1080) == "920x680+1000+400"

    def test_falls_back_to_default(self) -> None:
        assert resolve_geometry(None, 1920, 1080) == DEFAULT_GEOMETRY
        assert resolve_geometry("bad", 1920, 1080) == DEFAULT_GEOMETRY

    def test_enforces_minimum_size(self) -> None:
        assert resolve_geometry("200x100+0+0", 1920, 1080) == "800x540+0+0"


# ============================================================================ #
# 历史记录
# ============================================================================ #
class TestHistoryEntry:
    """历史条目序列化。"""

    def test_to_dict_roundtrip(self) -> None:
        entry = RegexHistoryEntry(pattern="a+", test_string="aaa", ignorecase=True, dotall=True)
        assert RegexHistoryEntry.from_dict(entry.to_dict()) == entry

    @pytest.mark.parametrize("raw", [None, "x", 1, [], {}])
    def test_from_dict_invalid(self, raw: object) -> None:
        assert RegexHistoryEntry.from_dict(raw) is None

    def test_from_dict_empty_pattern(self) -> None:
        assert RegexHistoryEntry.from_dict({"pattern": ""}) is None

    def test_from_dict_defaults(self) -> None:
        assert RegexHistoryEntry.from_dict({"pattern": "x"}) == RegexHistoryEntry(pattern="x")


class TestPushHistory:
    """历史入栈规则。"""

    def test_empty_pattern_is_ignored(self) -> None:
        history = [RegexHistoryEntry(pattern="a")]
        result = push_history(history, RegexHistoryEntry(pattern=""))
        assert result == history
        assert result is not history

    def test_new_entry_goes_first(self) -> None:
        history = [RegexHistoryEntry(pattern="a")]
        result = push_history(history, RegexHistoryEntry(pattern="b"))
        assert [entry.pattern for entry in result] == ["b", "a"]

    def test_duplicate_moves_to_front(self) -> None:
        history = [RegexHistoryEntry(pattern="a"), RegexHistoryEntry(pattern="b")]
        result = push_history(history, RegexHistoryEntry(pattern="b"))
        assert [entry.pattern for entry in result] == ["b", "a"]

    def test_limit_truncates(self) -> None:
        result = push_history([RegexHistoryEntry(pattern="a")], RegexHistoryEntry(pattern="b"), limit=2)
        assert [entry.pattern for entry in result] == ["b", "a"]
        result = push_history(result, RegexHistoryEntry(pattern="c"), limit=2)
        assert [entry.pattern for entry in result] == ["c", "b"]


class TestHistoryLabel:
    """下拉项文本。"""

    def test_short_pattern(self) -> None:
        assert history_label(0, RegexHistoryEntry(pattern="\\d+")) == "1. \\d+"

    def test_long_pattern_is_truncated(self) -> None:
        label = history_label(2, RegexHistoryEntry(pattern="a" * 60))
        assert label.startswith("3. ")
        assert label.endswith("…")
        assert len(label) == len("3. ") + 51


# ============================================================================ #
# 内置示例
# ============================================================================ #
class TestExamples:
    """20 个典型示例。"""

    def test_example_count(self) -> None:
        assert len(REGEX_EXAMPLES) == 20

    def test_titles_are_unique(self) -> None:
        titles = [example.title for example in REGEX_EXAMPLES]
        assert len(set(titles)) == len(REGEX_EXAMPLES)

    @pytest.mark.parametrize("example", REGEX_EXAMPLES, ids=[example.title for example in REGEX_EXAMPLES])
    def test_example_matches_its_text(self, example: RegexExample) -> None:
        """每个示例的正则都能匹配自带的测试文本。"""
        flags = build_flags(
            ignorecase=example.ignorecase,
            multiline=example.multiline,
            dotall=example.dotall,
        )
        assert RegexValidator(example.pattern, flags).is_valid(example.text)

    def test_example_label(self) -> None:
        assert example_label(0, REGEX_EXAMPLES[0]) == f"1. {REGEX_EXAMPLES[0].title}"


# ============================================================================ #
# GUI 入口
# ============================================================================ #
class TestGuiEntry:
    """GUI 启动入口（不真正创建窗口）。"""

    def test_run_gui_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """tkinter 不可用时打印错误并退出。"""

        def fake_window(_path: Path | None) -> None:
            raise ImportError("no tkinter")

        monkeypatch.setattr(regexvalidatew, "RegexValidateWindow", fake_window)
        regexvalidatew.run_gui()  # 不应抛出

    def test_run_gui_starts_mainloop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """正常创建窗口后进入 mainloop。"""
        calls: list[str] = []

        class _Root:
            def mainloop(self) -> None:
                calls.append("mainloop")

        class _Window:
            root = _Root()

        monkeypatch.setattr(regexvalidatew, "RegexValidateWindow", lambda _path: _Window())
        regexvalidatew.run_gui()
        assert calls == ["mainloop"]

    def test_launch_cmd_calls_run_gui(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(regexvalidatew, "run_gui", lambda: calls.append(True))
        regexvalidatew.launch_cmd()
        assert calls == [True]
