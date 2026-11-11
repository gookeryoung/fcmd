"""标签页锁定、选择记忆与持久化过滤器纯逻辑测试。"""

from __future__ import annotations

import pytest

from fcmd.tools.system._cmderw_tabs import (
    MAX_SAVED_SELECTION,
    MAX_TAB_TITLE,
    FilterDefinition,
    apply_filter_definition,
    close_other_indices,
    normalize_filter_definition,
    normalize_selection,
    normalize_tab_title,
    restore_selection,
    tab_bar_label,
    tab_label,
)


class TestFilterDefinition:
    def test_active(self) -> None:
        assert not FilterDefinition().active
        assert FilterDefinition(include="*.txt").active
        assert FilterDefinition(exclude="*.log").active

    def test_to_dict_roundtrip(self) -> None:
        definition = FilterDefinition(include="*.py", exclude="test_*")
        assert normalize_filter_definition(definition.to_dict()) == definition

    def test_describe(self) -> None:
        assert FilterDefinition().describe() == "未定义"
        assert FilterDefinition(include="*.py").describe() == "包含 *.py"
        assert FilterDefinition(exclude="*.log").describe() == "排除 *.log"
        assert FilterDefinition(include="*.py", exclude="test_*").describe() == "包含 *.py；排除 test_*"

    def test_normalize_invalid(self) -> None:
        assert normalize_filter_definition(None) == FilterDefinition()
        assert normalize_filter_definition("*.py") == FilterDefinition()
        assert normalize_filter_definition({"include": 1, "exclude": []}) == FilterDefinition()

    def test_normalize_whitespace_and_semicolons(self) -> None:
        definition = normalize_filter_definition({"include": "  *.py ;; *.md ; ", "exclude": " *.log "})
        assert definition == FilterDefinition(include="*.py;*.md", exclude="*.log")

    def test_normalize_truncates_long_mask(self) -> None:
        definition = normalize_filter_definition({"include": "x" * 600})
        assert len(definition.include) == 512

    def test_apply_include_only(self) -> None:
        names = ["a.py", "b.txt", "c.md"]
        assert apply_filter_definition(names, FilterDefinition(include="*.py;*.md")) == ["a.py", "c.md"]

    def test_apply_exclude_only(self) -> None:
        names = ["a.py", "test_a.py", "b.py"]
        definition = FilterDefinition(exclude="test_*")
        assert apply_filter_definition(names, definition) == ["a.py", "b.py"]

    def test_apply_both(self) -> None:
        names = ["a.py", "test_a.py", "b.md"]
        definition = FilterDefinition(include="*.py", exclude="test_*")
        assert apply_filter_definition(names, definition) == ["a.py"]

    def test_apply_case_insensitive(self) -> None:
        names = ["README.MD", "notes.txt"]
        assert apply_filter_definition(names, FilterDefinition(include="*.md")) == ["README.MD"]

    def test_apply_empty_definition_keeps_order(self) -> None:
        names = ["b", "a", "c"]
        assert apply_filter_definition(names, FilterDefinition()) == names


class TestTabTitle:
    def test_normalize_title(self) -> None:
        assert normalize_tab_title(None) == ""
        assert normalize_tab_title(42) == ""
        assert normalize_tab_title("  项目  工作区 ") == "项目 工作区"
        assert len(normalize_tab_title("x" * 200)) == MAX_TAB_TITLE

    def test_tab_label_custom_wins(self) -> None:
        assert tab_label("/home/user/project", "我的项目") == "我的项目"
        assert tab_label("/home/user/project") == "project"
        assert tab_label("") == ""
        assert tab_label("/") == "/"

    def test_tab_bar_label(self) -> None:
        assert tab_bar_label("/home/user/project") == "project"
        assert tab_bar_label("/home/user/project", locked=True) == "[锁] project"
        assert tab_bar_label("") == "（空）"
        long_name = "x" * 40
        label = tab_bar_label(f"/tmp/{long_name}")
        assert len(label) == 24
        assert label.startswith("…")

    def test_tab_bar_label_lock_after_truncate(self) -> None:
        label = tab_bar_label(f"/tmp/{'x' * 40}", locked=True)
        assert label.startswith("[锁] …")


class TestSelection:
    def test_normalize_selection(self) -> None:
        assert normalize_selection(None) == []
        assert normalize_selection("a") == []
        assert normalize_selection(["a", "", 1, "a", "b"]) == ["a", "b"]

    def test_normalize_selection_limit(self) -> None:
        names = [f"file{i}" for i in range(MAX_SAVED_SELECTION + 10)]
        assert len(normalize_selection(names)) == MAX_SAVED_SELECTION

    def test_restore_selection_filters_missing(self) -> None:
        assert restore_selection(["a", "c"], ["a", "b", "c"]) == ["a", "c"]
        assert restore_selection([], ["a"]) == []


class TestCloseOthers:
    def test_keeps_active_and_locked(self) -> None:
        assert close_other_indices(4, 1, [2]) == [0, 3]

    def test_ignore_out_of_range_locks(self) -> None:
        assert close_other_indices(3, 0, [-1, 5]) == [1, 2]

    def test_empty(self) -> None:
        assert close_other_indices(0, 0, []) == []

    def test_single_tab(self) -> None:
        assert close_other_indices(1, 0, []) == []

    def test_all_others_locked(self) -> None:
        assert close_other_indices(3, 0, [1, 2]) == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
