"""多重命名工具纯逻辑测试：掩码、查找替换、大小写、计数器、预览与预设。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from fcmd.tools.system._cmderw_rename import (
    RENAME_PRESET_LIMIT,
    MaskError,
    RenameInput,
    RenameOptions,
    RenamePreset,
    apply_case,
    apply_find_replace,
    build_rename_preview,
    expand_mask,
    normalize_rename_presets,
    order_inputs,
    preview_to_plan,
    rename_presets_to_json,
    split_name,
)


class TestMask:
    def test_split_name(self) -> None:
        assert split_name("a.txt") == ("a", "txt")
        assert split_name("a.b.txt") == ("a.b", "txt")
        assert split_name("README") == ("README", "")
        assert split_name(".bashrc") == (".bashrc", "")
        assert split_name("dir.with.dot", is_dir=True) == ("dir.with.dot", "")

    def test_expand_common_placeholders(self) -> None:
        mask = "[N]_[C].[E]"
        text = expand_mask(mask, stem="photo", ext="jpg", counter=7, digits=3)
        assert text == "photo_007.jpg"

    def test_expand_name_fragment(self) -> None:
        assert expand_mask("[N1-3]", stem="abcdef", ext="") == "abc"
        assert expand_mask("[N2-99]", stem="abcdef", ext="") == "bcdef"
        assert expand_mask("[N9-20]", stem="abcdef", ext="") == ""

    def test_expand_date_fields(self) -> None:
        moment = datetime(2020, 5, 6, 7, 8, 9)
        text = expand_mask("[Y][M][D]_[h][m][s]", stem="x", ext="", moment=moment)
        assert text == "20200506_070809"

    def test_expand_escaped_bracket(self) -> None:
        assert expand_mask("[[N]", stem="x", ext="") == "[N]"

    @pytest.mark.parametrize("mask", ["[X]", "[N1]", "[N-2]", "[N]x["])
    def test_expand_rejects_invalid_mask(self, mask: str) -> None:
        with pytest.raises(MaskError):
            expand_mask(mask, stem="x", ext="")

    def test_expand_reports_token_and_position(self) -> None:
        with pytest.raises(MaskError, match=r"位置 3.*\[X\]"):
            expand_mask("ab[X]", stem="x", ext="")

    def test_fragment_range_must_start_at_one(self) -> None:
        with pytest.raises(MaskError, match="从 1 开始"):
            expand_mask("[N0-2]", stem="abc", ext="")

    def test_fragment_range_must_ascend(self) -> None:
        with pytest.raises(MaskError, match="起点大于终点"):
            expand_mask("[N3-1]", stem="abc", ext="")


class TestFindReplace:
    def test_plain_replacement_is_literal(self) -> None:
        assert apply_find_replace("IMG_1.jpg", find="IMG_", replace=r"\1") == r"\11.jpg"

    def test_plain_case_insensitive_by_default(self) -> None:
        assert apply_find_replace("Photo.JPG", find="photo", replace="image") == "image.JPG"

    def test_plain_case_sensitive(self) -> None:
        assert apply_find_replace("Photo.JPG", find="photo", replace="image", case_sensitive=True) == "Photo.JPG"

    def test_regex_with_backreference(self) -> None:
        text = apply_find_replace("file-12.txt", find=r"file-(\d+)", replace=r"item-\1", use_regex=True)
        assert text == "item-12.txt"

    def test_regex_flags_case(self) -> None:
        assert apply_find_replace("ABC", find="abc", replace="x", use_regex=True) == "x"
        assert apply_find_replace("ABC", find="abc", replace="x", use_regex=True, case_sensitive=True) == "ABC"

    def test_invalid_regex_raises(self) -> None:
        with pytest.raises(MaskError, match="正则表达式无效"):
            apply_find_replace("a", find="(", replace="x", use_regex=True)

    def test_empty_find_keeps_text(self) -> None:
        assert apply_find_replace("abc", find="", replace="x") == "abc"


class TestCaseModes:
    @pytest.mark.parametrize(
        ("mode", "expected"),
        [
            ("keep", "my file.txt"),
            ("lower", "my file.txt"),
            ("upper", "MY FILE.TXT"),
            ("title", "My File.Txt"),
            ("sentence", "My file.txt"),
        ],
    )
    def test_apply_case(self, mode: str, expected: str) -> None:
        assert apply_case("my file.txt", mode) == expected

    def test_unknown_case_mode(self) -> None:
        with pytest.raises(MaskError):
            apply_case("x", "weird")


class TestOrdering:
    def test_order_by_name(self, tmp_path: Path) -> None:
        inputs = [
            RenameInput(tmp_path / "b.txt"),
            RenameInput(tmp_path / "A.txt"),
            RenameInput(tmp_path / "c.txt"),
        ]
        assert [item.path.name for item in order_inputs(inputs, "name")] == ["A.txt", "b.txt", "c.txt"]

    def test_order_by_time(self, tmp_path: Path) -> None:
        early, late = (
            RenameInput(tmp_path / "a.txt", mtime=1.0, ctime=2.0),
            RenameInput(tmp_path / "b.txt", mtime=3.0, ctime=1.0),
        )
        assert [item.path.name for item in order_inputs([early, late], "modified")] == ["a.txt", "b.txt"]
        assert [item.path.name for item in order_inputs([early, late], "created")] == ["b.txt", "a.txt"]

    def test_order_rejects_unknown_key(self, tmp_path: Path) -> None:
        with pytest.raises(MaskError):
            order_inputs([RenameInput(tmp_path / "a.txt")], "size")


class TestPreview:
    def test_default_mask_marks_unchanged(self, tmp_path: Path) -> None:
        preview = build_rename_preview(RenameOptions(), [RenameInput(tmp_path / "a.txt")])
        assert preview.error == ""
        assert preview.items[0].status == "unchanged"

    def test_counter_follows_sorted_names(self, tmp_path: Path) -> None:
        inputs = [RenameInput(tmp_path / name) for name in ("b.txt", "a.txt", "c.txt")]
        options = RenameOptions(name_mask="[N]_[C]", start=5, step=2, digits=3)
        preview = build_rename_preview(options, inputs)
        assert preview.error == ""
        names = {item.old_name: item.new_name for item in preview.items}
        assert names == {
            "a.txt": "a_005.txt",
            "b.txt": "b_007.txt",
            "c.txt": "c_009.txt",
        }

    def test_existing_target_is_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        preview = build_rename_preview(
            RenameOptions(name_mask="b"),
            [RenameInput(tmp_path / "a.txt")],
        )
        assert preview.items[0].status == "conflict"
        assert preview.items[0].note == "目标已存在"

    def test_duplicate_target_is_conflict(self, tmp_path: Path) -> None:
        preview = build_rename_preview(
            RenameOptions(name_mask="same"),
            [RenameInput(tmp_path / "a.txt"), RenameInput(tmp_path / "b.txt")],
        )
        assert [item.status for item in preview.items] == ["ok", "conflict"]
        assert "第 1 项" in preview.items[1].note

    def test_same_target_in_different_directories_is_allowed(self, tmp_path: Path) -> None:
        first, second = tmp_path / "one", tmp_path / "two"
        first.mkdir()
        second.mkdir()
        preview = build_rename_preview(
            RenameOptions(name_mask="same"),
            [RenameInput(first / "a.txt"), RenameInput(second / "b.txt")],
        )
        assert [item.status for item in preview.items] == ["ok", "ok"]

    def test_illegal_character_is_invalid(self, tmp_path: Path) -> None:
        preview = build_rename_preview(
            RenameOptions(name_mask="bad:name"),
            [RenameInput(tmp_path / "a.txt")],
        )
        assert preview.items[0].status == "invalid"

    def test_empty_result_is_invalid(self, tmp_path: Path) -> None:
        preview = build_rename_preview(
            RenameOptions(name_mask="[N5-9]", ext_mask="-"),
            [RenameInput(tmp_path / "abc.txt")],
        )
        assert preview.items[0].status == "invalid"

    def test_too_long_name_is_invalid(self, tmp_path: Path) -> None:
        preview = build_rename_preview(
            RenameOptions(name_mask="x" * 260),
            [RenameInput(tmp_path / "a.txt")],
        )
        assert preview.items[0].status == "invalid"
        assert "过长" in preview.items[0].note

    def test_extension_mask_can_remove(self, tmp_path: Path) -> None:
        preview = build_rename_preview(
            RenameOptions(name_mask="[N]", ext_mask="-"),
            [RenameInput(tmp_path / "a.txt")],
        )
        assert preview.items[0].new_name == "a"

    def test_empty_extension_mask_keeps_original(self, tmp_path: Path) -> None:
        preview = build_rename_preview(
            RenameOptions(name_mask="[N]_x", ext_mask=""),
            [RenameInput(tmp_path / "a.txt")],
        )
        assert preview.items[0].new_name == "a_x.txt"

    def test_only_extension_find_replace(self, tmp_path: Path) -> None:
        options = RenameOptions(find="txt", replace="md", only_extension=True)
        preview = build_rename_preview(options, [RenameInput(tmp_path / "txt-a.txt")])
        assert preview.items[0].new_name == "txt-a.md"

    def test_regex_find_replace_applies_to_name(self, tmp_path: Path) -> None:
        options = RenameOptions(find=r"(\d+)", replace=r"v\1", use_regex=True)
        preview = build_rename_preview(options, [RenameInput(tmp_path / "file-12.txt")])
        assert preview.items[0].new_name == "file-v12.txt"

    def test_date_mask_uses_modified_time(self, tmp_path: Path) -> None:
        stamp = datetime(2021, 2, 3, 4, 5, 6).timestamp()
        preview = build_rename_preview(
            RenameOptions(name_mask="[Y][M][D]"),
            [RenameInput(tmp_path / "a.txt", mtime=stamp)],
        )
        assert preview.items[0].new_name == "20210203.txt"

    @pytest.mark.parametrize(
        "options",
        [
            RenameOptions(digits=0),
            RenameOptions(digits=40),
            RenameOptions(name_mask="  "),
            RenameOptions(case_mode="weird"),
            RenameOptions(sort_key="size"),
        ],
    )
    def test_rule_errors_are_reported(self, options: RenameOptions) -> None:
        preview = build_rename_preview(options, [RenameInput(Path("a.txt"))])
        assert preview.error
        assert preview.items == ()

    def test_invalid_mask_reports_error_without_dropping_silently(self, tmp_path: Path) -> None:
        preview = build_rename_preview(
            RenameOptions(name_mask="[Q]"),
            [RenameInput(tmp_path / "a.txt")],
        )
        assert "未知占位符" in preview.error

    def test_preview_to_plan_respects_selection(self, tmp_path: Path) -> None:
        first, second = tmp_path / "a.txt", tmp_path / "b.txt"
        preview = build_rename_preview(
            RenameOptions(name_mask="[N]_x"),
            [RenameInput(first), RenameInput(second)],
        )
        plan = preview_to_plan(preview, [second])
        assert [item.source for item in plan.items] == [second]


class TestPresets:
    def test_normalize_drops_invalid_entries(self) -> None:
        raw = [
            {"name": "good", "name_mask": "[N]_[C]"},
            "bad",
            {"name": ""},
            {"name": 3},
            {"name": "good", "name_mask": "dup"},
        ]
        presets = normalize_rename_presets(raw)
        assert [preset.name for preset in presets] == ["good"]
        assert presets[0].options.name_mask == "[N]_[C]"

    def test_normalize_fills_defaults_for_missing_fields(self) -> None:
        presets = normalize_rename_presets([{"name": "p", "start": "x", "digits": 0}])
        assert presets[0].options.start == RenameOptions().start
        assert presets[0].options.digits == RenameOptions().digits

    def test_normalize_rejects_wrong_container(self) -> None:
        assert normalize_rename_presets(None) == []
        assert normalize_rename_presets({"name": "p"}) == []

    def test_normalize_caps_preset_count(self) -> None:
        raw = [{"name": f"p{index}"} for index in range(RENAME_PRESET_LIMIT + 5)]
        assert len(normalize_rename_presets(raw)) == RENAME_PRESET_LIMIT

    def test_roundtrip_keeps_all_fields(self) -> None:
        preset = RenamePreset(
            "photo",
            RenameOptions(
                name_mask="[N]_[C]",
                ext_mask="-",
                find="IMG",
                replace="photo",
                use_regex=False,
                case_sensitive=True,
                only_extension=True,
                case_mode="lower",
                start=10,
                step=2,
                digits=4,
                sort_key="modified",
            ),
        )
        rows = rename_presets_to_json([preset])
        assert rows[0]["name"] == "photo"
        assert "options" not in rows[0]
        restored = normalize_rename_presets(rows)
        assert restored == [preset]

    def test_blank_name_mask_falls_back(self) -> None:
        presets = normalize_rename_presets([{"name": "p", "name_mask": "   "}])
        assert presets[0].options.name_mask == RenameOptions().name_mask

    def test_empty_extension_mask_is_preserved(self) -> None:
        presets = normalize_rename_presets([{"name": "p", "ext_mask": ""}])
        assert presets[0].options.ext_mask == ""


def test_empty_preset_list_serializes_to_empty() -> None:
    """没有预设时序列化结果为空列表，便于设置文件保持简洁。"""
    assert rename_presets_to_json([]) == []
    assert normalize_rename_presets([]) == []
