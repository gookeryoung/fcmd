"""renamer 纯逻辑测试：规则求值、JSON 解析、预览、冲突、两阶段执行与撤销。"""

from __future__ import annotations

from pathlib import Path

import pytest

from fcmd.apps.renamer._core import (
    ANCHOR_AFTER,
    ANCHOR_BEFORE,
    ANCHOR_END,
    ANCHOR_INDEX,
    ANCHOR_START,
    FIELD_EXT,
    STATUS_CONFLICT,
    STATUS_INVALID,
    STATUS_OK,
    STATUS_UNCHANGED,
    RenamePlan,
    RenamePreview,
    RenamePreviewItem,
    RenameRule,
    RenameTarget,
    RuleError,
    apply_rule,
    build_preview,
    collect_targets,
    execute_plan,
    format_preview,
    format_report,
    join_name,
    order_targets,
    parse_rules,
    preview_to_plan,
    rules_to_json,
    split_name,
    undo_report,
    validate_rules,
)


def _target(path: Path, *, is_dir: bool = False, mtime: float = 0.0, ctime: float = 0.0) -> RenameTarget:
    return RenameTarget(path, is_dir, mtime, ctime)


def _touch(root: Path, name: str, mtime: float | None = None) -> Path:
    path = root / name
    path.write_text("x", encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


# ---------------------------------------------------------------------- #
# 名称拆分与基础规则
# ---------------------------------------------------------------------- #
def test_split_and_join_name() -> None:
    assert split_name("report.tar.gz") == ("report.tar", "gz")
    assert split_name("noext") == ("noext", "")
    assert split_name(".gitignore") == (".gitignore", "")
    assert split_name("dir.name", True) == ("dir.name", "")
    assert join_name("a", "txt") == "a.txt"
    assert join_name("a", "") == "a"


def test_replace_all_and_backreference() -> None:
    rule = RenameRule(kind="replace", pattern=r"(\w+)-(\w+)", replacement=r"\2_\1")
    assert apply_rule("foo-bar", rule) == "bar_foo"


def test_replace_occurrence_and_ignore_case() -> None:
    rule = RenameRule(kind="replace", pattern="a", replacement="X", occurrence=2)
    assert apply_rule("aaa", rule) == "aXa"
    assert apply_rule("a", rule) == "a"
    upper = RenameRule(kind="replace", pattern="img", replacement="photo", ignore_case=True)
    assert apply_rule("IMG_001", upper) == "photo_001"


def test_replace_empty_replacement_deletes() -> None:
    rule = RenameRule(kind="replace", pattern=r"^IMG_")
    assert apply_rule("IMG_001", rule) == "001"


def test_replace_invalid_pattern_and_template() -> None:
    with pytest.raises(RuleError, match="正则表达式无效"):
        apply_rule("a", RenameRule(kind="replace", pattern="("))
    with pytest.raises(RuleError, match="替换模板无效"):
        apply_rule("a", RenameRule(kind="replace", pattern="(a)", replacement=r"\2"))
    with pytest.raises(RuleError, match="替换模板无效"):
        apply_rule("a", RenameRule(kind="replace", pattern="(a)", replacement=r"\2", occurrence=1))


def test_reorder_positions_groups() -> None:
    rule = RenameRule(kind="reorder", pattern=r"^(\d{4})-(.+)$", replacement="{2}-{1}")
    assert apply_rule("2024-report", rule) == "report-2024"


def test_reorder_named_group_and_braces() -> None:
    rule = RenameRule(kind="reorder", pattern=r"(?P<title>.+)-(\d+)$", replacement="{2}·{title}")
    assert apply_rule("report-42", rule) == "42·report"
    braces = RenameRule(kind="reorder", pattern=r"(a)", replacement="{{{1}}}")
    assert apply_rule("a", braces) == "{a}"


def test_reorder_optional_group_and_occurrence() -> None:
    rule = RenameRule(kind="reorder", pattern=r"(a)(b)?", replacement="[{1}{2}]", occurrence=1)
    assert apply_rule("ab ab", rule) == "[ab] ab"
    optional = RenameRule(kind="reorder", pattern=r"(a)(b)?", replacement="[{2}]")
    assert apply_rule("a", optional) == "[]"


@pytest.mark.parametrize(
    ("template", "message"),
    [
        ("{3}", "不存在的捕获组"),
        ("{missing}", "不存在的命名捕获组"),
        ("{}", "占位符为空"),
        ("{1", "未闭合"),
        ("{1}}", "多余的 }"),
    ],
)
def test_reorder_template_errors(template: str, message: str) -> None:
    rule = RenameRule(kind="reorder", pattern=r"(a)", replacement=template)
    with pytest.raises(RuleError, match=message):
        apply_rule("a", rule)


def test_reorder_without_match_is_unchanged() -> None:
    rule = RenameRule(kind="reorder", pattern=r"^x(\d+)$", replacement="{1}")
    assert apply_rule("abc", rule) == "abc"


def test_move_to_end_and_start() -> None:
    to_end = RenameRule(kind="move", pattern=r"\d{4}", anchor=ANCHOR_END)
    assert apply_rule("2024_report", to_end) == "_report2024"
    to_start = RenameRule(kind="move", pattern=r"\[(.+?)\]", anchor=ANCHOR_START)
    assert apply_rule("a[b]c", to_start) == "[b]ac"


def test_move_to_index_and_negative_index() -> None:
    rule = RenameRule(kind="move", pattern=r"\d", anchor=ANCHOR_INDEX, position=2)
    assert apply_rule("a1bc", rule) == "ab1c"
    negative = RenameRule(kind="move", pattern=r"\d", anchor=ANCHOR_INDEX, position=-1)
    assert apply_rule("a1bc", negative) == "ab1c"


def test_move_before_and_after_anchor_text() -> None:
    before = RenameRule(kind="move", pattern=r"^\d{4}", anchor=ANCHOR_BEFORE, anchor_text="END")
    assert apply_rule("2024_report_END", before) == "_report_2024END"
    after = RenameRule(kind="move", pattern=r"^\d{4}", anchor=ANCHOR_AFTER, anchor_text="START")
    assert apply_rule("2024_START_report", after) == "_START2024_report"


def test_move_missing_anchor_and_occurrence() -> None:
    missing = RenameRule(kind="move", pattern=r"\d", anchor=ANCHOR_BEFORE, anchor_text="NOPE")
    with pytest.raises(RuleError, match="未找到锚点文本"):
        apply_rule("a1", missing)
    none = RenameRule(kind="move", pattern=r"\d", occurrence=3, anchor=ANCHOR_END)
    assert apply_rule("a1b2", none) == "a1b2"


def test_move_all_matches_and_zero_length() -> None:
    rule = RenameRule(kind="move", pattern=r"\[\w\]", anchor=ANCHOR_END)
    assert apply_rule("[a]x[b]y", rule) == "xy[a][b]"
    zero = RenameRule(kind="move", pattern=r"(?=x)", anchor=ANCHOR_END)
    assert apply_rule("x", zero) == "x"


def test_insert_positions() -> None:
    assert apply_rule("abc", RenameRule(kind="insert", text="X")) == "Xabc"
    assert apply_rule("abc", RenameRule(kind="insert", text="X", anchor=ANCHOR_END)) == "abcX"
    assert apply_rule("abc", RenameRule(kind="insert", text="X", anchor=ANCHOR_INDEX, position=1)) == "aXbc"
    assert apply_rule("abc", RenameRule(kind="insert", text="X", anchor=ANCHOR_INDEX, position=-1)) == "abXc"
    assert apply_rule("abc", RenameRule(kind="insert", text="X", anchor=ANCHOR_INDEX, position=99)) == "abcX"


def test_insert_errors() -> None:
    with pytest.raises(RuleError, match="需要提供 text"):
        apply_rule("a", RenameRule(kind="insert"))
    with pytest.raises(RuleError, match="需要提供 anchor_text"):
        apply_rule("a", RenameRule(kind="insert", text="x", anchor=ANCHOR_BEFORE))


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("keep", "aB c"),
        ("lower", "ab c"),
        ("upper", "AB C"),
        ("title", "Ab C"),
        ("sentence", "AB c"),
    ],
)
def test_case_modes(mode: str, expected: str) -> None:
    assert apply_rule("aB c", RenameRule(kind="case", mode=mode)) == expected


def test_case_unknown_mode() -> None:
    with pytest.raises(RuleError, match="未知的大小写模式"):
        apply_rule("a", RenameRule(kind="case", mode="weird"))


def test_sequence_prefix_and_suffix() -> None:
    prefix = RenameRule(kind="sequence", text="_", start=1, digits=3)
    assert apply_rule("a", prefix, 1) == "001_a"
    assert apply_rule("a", prefix, 3) == "003_a"
    suffix = RenameRule(kind="sequence", text="_", start=5, step=2, digits=2, anchor=ANCHOR_END)
    assert apply_rule("a", suffix, 2) == "a_07"


def test_apply_rule_unknown_kind() -> None:
    with pytest.raises(RuleError, match="未知的规则类型"):
        apply_rule("a", RenameRule(kind="nope"))


# ---------------------------------------------------------------------- #
# 规则校验与 JSON
# ---------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("rules", "message"),
    [
        ([RenameRule(kind="nope")], "未知的规则类型"),
        ([RenameRule(kind="case", field="body")], "未知的作用域"),
        ([RenameRule(kind="replace", pattern="a", occurrence=-1)], "occurrence 不能为负数"),
        ([RenameRule(kind="replace", pattern="")], "需要正则表达式"),
        ([RenameRule(kind="insert")], "需要插入文本"),
        ([RenameRule(kind="move", pattern="a", anchor="middle")], "未知的锚点"),
        ([RenameRule(kind="move", pattern="a", anchor=ANCHOR_BEFORE)], "需要 anchor_text"),
        ([RenameRule(kind="case", mode="weird")], "未知的大小写模式"),
        ([RenameRule(kind="sequence", anchor="before")], "计数器锚点"),
        ([RenameRule(kind="sequence", digits=0)], "计数器位数"),
        ([RenameRule(kind="sequence", digits=33)], "计数器位数"),
    ],
)
def test_validate_rules_rejects_bad_config(rules: list[RenameRule], message: str) -> None:
    with pytest.raises(RuleError, match=message):
        validate_rules(rules)


def test_parse_rules_array_and_object() -> None:
    payload = [{"kind": "replace", "pattern": "a", "replacement": "b", "occurrence": 2}]
    assert parse_rules(payload)[0].occurrence == 2
    assert parse_rules({"rules": payload}) == parse_rules(payload)
    assert parse_rules([]) == ()


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-a-list", "必须是 JSON 数组"),
        ({"rules": "x"}, "必须是 JSON 数组"),
        ([1], "必须是 JSON 对象"),
        ([{"kind": "replace", "unknown": 1}], "未知字段"),
        ([{"kind": 1}], "kind 必须是字符串"),
        ([{"kind": "replace", "pattern": "a", "ignore_case": "yes"}], "必须是布尔值"),
        ([{"kind": "replace", "pattern": "a", "occurrence": True}], "必须是整数"),
        ([{"kind": "replace", "pattern": "a", "occurrence": 1.5}], "必须是整数"),
    ],
)
def test_parse_rules_rejects_bad_json(payload: object, message: str) -> None:
    with pytest.raises(RuleError, match=message):
        parse_rules(payload)


def test_rules_json_roundtrip() -> None:
    rules = (RenameRule(kind="reorder", pattern="(a)", replacement="{1}x"),)
    assert parse_rules(rules_to_json(rules)) == rules


# ---------------------------------------------------------------------- #
# 排序与预览
# ---------------------------------------------------------------------- #
def test_order_targets(tmp_path: Path) -> None:
    first = _target(tmp_path / "b.txt", mtime=2, ctime=3)
    second = _target(tmp_path / "a.txt", mtime=1, ctime=4)
    assert [item.path.name for item in order_targets([first, second])] == ["a.txt", "b.txt"]
    assert [item.path.name for item in order_targets([first, second], "modified")] == ["a.txt", "b.txt"]
    assert [item.path.name for item in order_targets([first, second], "created")] == ["b.txt", "a.txt"]
    with pytest.raises(RuleError, match="未知的排序方式"):
        order_targets([first], "size")


def test_build_preview_marks_ok_unchanged_and_invalid(tmp_path: Path) -> None:
    ok = _touch(tmp_path, "a.txt")
    unchanged = _touch(tmp_path, "keep.txt")
    empty = _touch(tmp_path, "empty.txt")
    targets = [_target(ok), _target(unchanged), _target(empty)]
    preview = build_preview(
        targets,
        [RenameRule(kind="replace", pattern=r"^a$", replacement="b")],
    )
    assert [item.status for item in preview.items] == [STATUS_OK, STATUS_UNCHANGED, STATUS_UNCHANGED]

    preview = build_preview(
        [targets[0]],
        [RenameRule(kind="replace", pattern=r"^a$", replacement="b")],
    )
    assert preview.items[0].status == STATUS_OK
    assert preview.items[0].new_name == "b.txt"

    preview = build_preview(
        [targets[2]],
        [
            RenameRule(kind="replace", pattern=r".*", replacement=""),
            RenameRule(kind="replace", pattern=r".*", replacement="", field="ext"),
        ],
    )
    assert preview.items[0].status == STATUS_INVALID
    assert "不能为空" in preview.items[0].note


def test_build_preview_rejects_invalid_names(tmp_path: Path) -> None:
    path = _touch(tmp_path, "a.txt")
    for replacement, message in (("a/b", "路径分隔符"), ("a<b", "不能包含"), ("x" * 300, "名称过长")):
        preview = build_preview(
            [_target(path)],
            [RenameRule(kind="replace", pattern=r"a", replacement=replacement)],
        )
        assert preview.items[0].status == STATUS_INVALID
        assert message in preview.items[0].note


@pytest.mark.parametrize("name", [".", ".."])
def test_build_preview_rejects_dot_names(tmp_path: Path, name: str) -> None:
    path = _touch(tmp_path, "a")
    preview = build_preview([_target(path)], [RenameRule(kind="replace", pattern=r"^a$", replacement=name)])
    assert preview.items[0].status == STATUS_INVALID
    assert "名称不能是" in preview.items[0].note


def test_build_preview_applies_ext_rule_to_file(tmp_path: Path) -> None:
    path = _touch(tmp_path, "report.txt")
    preview = build_preview(
        [_target(path)],
        [RenameRule(kind="case", mode="upper", field=FIELD_EXT)],
    )
    assert preview.items[0].status == STATUS_OK
    assert preview.items[0].new_name == "report.TXT"


def test_build_preview_marks_rule_runtime_error_invalid(tmp_path: Path) -> None:
    path = _touch(tmp_path, "a.txt")
    preview = build_preview(
        [_target(path)],
        [RenameRule(kind="move", pattern=r"a", anchor=ANCHOR_BEFORE, anchor_text="NOPE")],
    )
    assert preview.items[0].status == STATUS_INVALID
    assert "未找到锚点文本" in preview.items[0].note


def test_build_preview_conflicts(tmp_path: Path) -> None:
    first = _touch(tmp_path, "a.txt")
    second = _touch(tmp_path, "b.txt")
    duplicate = build_preview(
        [_target(first), _target(second)],
        [RenameRule(kind="replace", pattern=r".+", replacement="same")],
    )
    assert duplicate.items[0].status == STATUS_OK
    assert duplicate.items[1].status == STATUS_CONFLICT
    assert "同名" in duplicate.items[1].note

    existing = _touch(tmp_path, "target.txt")
    conflict = build_preview(
        [_target(first)],
        [RenameRule(kind="replace", pattern=r"^a$", replacement=existing.stem)],
    )
    assert conflict.items[0].status == STATUS_CONFLICT
    assert conflict.items[0].note == "目标已存在"


def test_build_preview_allows_reusing_moving_source(tmp_path: Path) -> None:
    first = _touch(tmp_path, "a.txt")
    second = _touch(tmp_path, "b.txt")
    preview = build_preview(
        [_target(first), _target(second)],
        [
            RenameRule(kind="replace", pattern=r"^b$", replacement="b2"),
            RenameRule(kind="replace", pattern=r"^a$", replacement="b"),
        ],
    )
    assert [item.new_name for item in preview.items] == ["b.txt", "b2.txt"]
    assert all(item.status == STATUS_OK for item in preview.items)
    report = execute_plan(preview_to_plan(preview))
    assert report.ok
    assert (tmp_path / "b.txt").exists()
    assert (tmp_path / "b2.txt").exists()

    unchanged = _touch(tmp_path, "keep.txt")
    blocked = build_preview(
        [_target(first), _target(unchanged)],
        [RenameRule(kind="replace", pattern=r"^a$", replacement="keep")],
    )
    statuses = {item.old_name: item.status for item in blocked.items}
    assert statuses["a.txt"] == STATUS_CONFLICT


def test_build_preview_skips_ext_rule_for_dirs(tmp_path: Path) -> None:
    folder = tmp_path / "dir.name"
    folder.mkdir()
    preview = build_preview(
        [_target(folder, is_dir=True)],
        [RenameRule(kind="case", mode="upper", field=FIELD_EXT)],
    )
    assert preview.items[0].status == STATUS_UNCHANGED


def test_build_preview_rule_error_and_sequence_order(tmp_path: Path) -> None:
    bad = build_preview([], [RenameRule(kind="replace", pattern="(")])
    assert "正则表达式无效" in bad.error
    assert bad.items == ()

    first = _touch(tmp_path, "b.txt", mtime=2)
    second = _touch(tmp_path, "a.txt", mtime=1)
    preview = build_preview(
        [_target(first, mtime=2), _target(second, mtime=1)],
        [RenameRule(kind="sequence", text="_", digits=2)],
        "modified",
    )
    assert [item.new_name for item in preview.items] == ["01_a.txt", "02_b.txt"]


def test_preview_properties_and_plan(tmp_path: Path) -> None:
    first = _touch(tmp_path, "a.txt")
    preview = build_preview([_target(first)], [RenameRule(kind="replace", pattern="a", replacement="b")])
    assert len(preview.ok) == 1
    assert preview.unchanged == preview.invalid == preview.conflicts == ()
    plan = preview_to_plan(preview)
    assert plan.changes == preview.ok
    assert preview_to_plan(preview, [first]).items
    assert preview_to_plan(preview, [tmp_path / "other"]).items == ()
    assert RenamePreview().error == ""
    assert RenamePlan(()).changes == ()


# ---------------------------------------------------------------------- #
# 目标收集
# ---------------------------------------------------------------------- #
def test_collect_targets_files_dirs_recursive(tmp_path: Path) -> None:
    file_a = _touch(tmp_path, "a.txt")
    folder = tmp_path / "sub"
    folder.mkdir()
    _touch(folder, "b.txt")
    nested = folder / "deep"
    nested.mkdir()
    _touch(nested, "c.txt")

    direct = collect_targets([tmp_path])
    assert {item.path.name for item in direct} == {"a.txt"}

    files_only = collect_targets([tmp_path], include_dirs=False)
    assert {item.path.name for item in files_only} == {"a.txt"}

    recursive = collect_targets([tmp_path], recursive=True)
    assert {item.path.name for item in recursive} == {"a.txt", "b.txt", "c.txt"}

    with_dirs = collect_targets([tmp_path], recursive=True, include_dirs=True)
    assert {item.path.name for item in with_dirs} == {"a.txt", "b.txt", "c.txt", "sub", "deep"}

    deduped = collect_targets([file_a, tmp_path / "a.txt"])
    assert len(deduped) == 1
    assert deduped[0].mtime > 0


def test_collect_targets_missing_path(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="路径不存在"):
        collect_targets([tmp_path / "nope"])


# ---------------------------------------------------------------------- #
# 执行与撤销
# ---------------------------------------------------------------------- #
def _plan(*pairs: tuple[Path, str]) -> RenamePlan:
    return RenamePlan(tuple(RenamePreviewItem(source, source.name, new_name, STATUS_OK) for source, new_name in pairs))


def test_execute_plan_renames_and_undo(tmp_path: Path) -> None:
    first = _touch(tmp_path, "a.txt")
    second = _touch(tmp_path, "b.txt")
    report = execute_plan(_plan((first, "a2.txt"), (second, "b2.txt")))
    assert report.ok
    assert (tmp_path / "a2.txt").exists()
    assert (tmp_path / "b2.txt").exists()
    assert not first.exists()

    undone = undo_report(report)
    assert undone.ok
    assert first.exists() and second.exists()


def test_execute_plan_swap_and_case_only(tmp_path: Path) -> None:
    first = _touch(tmp_path, "a.txt")
    second = _touch(tmp_path, "b.txt")
    first.write_text("A", encoding="utf-8")
    second.write_text("B", encoding="utf-8")
    report = execute_plan(_plan((first, "b.txt"), (second, "a.txt")))
    assert report.ok
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "B"
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "A"

    case = _touch(tmp_path, "Foo.txt")
    report = execute_plan(_plan((case, "foo.txt")))
    assert report.ok
    assert (tmp_path / "foo.txt").exists()


def test_execute_plan_skips_occupied_target(tmp_path: Path) -> None:
    source = _touch(tmp_path, "a.txt")
    _touch(tmp_path, "b.txt")
    report = execute_plan(_plan((source, "b.txt")))
    assert not report.ok
    assert report.errors[0][1] == "目标已存在"
    assert source.exists()


def test_execute_plan_missing_source(tmp_path: Path) -> None:
    missing = tmp_path / "gone.txt"
    report = execute_plan(_plan((missing, "new.txt")))
    assert not report.ok
    assert "源路径不存在" in report.errors[0][1]


def test_two_phase_rolls_back_on_phase_one_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _touch(tmp_path, "a.txt")
    second = _touch(tmp_path, "b.txt")
    original = Path.rename

    def flaky(self: Path, target: Path) -> Path:
        if self.name == "b.txt":
            raise OSError("boom")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    report = execute_plan(_plan((first, "a2.txt"), (second, "b2.txt")))
    monkeypatch.setattr(Path, "rename", original)
    assert not report.ok
    assert "移动到临时名失败" in report.errors[0][1]
    assert first.exists() and second.exists()


def test_two_phase_rolls_back_on_phase_two_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _touch(tmp_path, "a.txt")
    second = _touch(tmp_path, "b.txt")
    original = Path.rename

    def flaky(self: Path, target: Path) -> Path:
        if target.name == "b2.txt":
            raise OSError("boom")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", flaky)
    report = execute_plan(_plan((first, "a2.txt"), (second, "b2.txt")))
    monkeypatch.setattr(Path, "rename", original)
    assert not report.ok
    assert "重命名失败" in report.errors[0][1]
    assert first.exists() and second.exists()


def test_execute_plan_noop_same_path(tmp_path: Path) -> None:
    source = _touch(tmp_path, "a.txt")
    assert execute_plan(_plan((source, "a.txt"))).ok


# ---------------------------------------------------------------------- #
# 文本报告
# ---------------------------------------------------------------------- #
def test_format_preview_and_report(tmp_path: Path) -> None:
    source = _touch(tmp_path, "a.txt")
    preview = build_preview(
        [_target(source)],
        [RenameRule(kind="replace", pattern="a", replacement="b")],
    )
    text = format_preview(preview)
    assert "[ok] a.txt -> b.txt" in text
    assert "可执行 1" in text
    assert "规则错误：" in format_preview(RenamePreview(error="坏规则"))
    assert format_preview(RenamePreview()).startswith("没有匹配")

    kept = build_preview([_target(source)], [RenameRule(kind="case", mode="lower")])
    assert "[未变化]" in format_preview(kept)
    report = execute_plan(preview_to_plan(preview))
    assert "已完成 1 项" in format_report(report)
    report.errors.append((source, "演示错误"))
    assert "失败 1 项" in format_report(report)

    mixed = RenamePreview(
        (
            RenamePreviewItem(source, "a.txt", "b.txt", STATUS_CONFLICT, "目标已存在"),
            RenamePreviewItem(source, "a.txt", "a/b.txt", STATUS_INVALID, "名称不能包含路径分隔符"),
        )
    )
    text = format_preview(mixed)
    assert "[conflict] a.txt -> b.txt（目标已存在）" in text
    assert "[invalid]" in text


def test_collect_targets_accepts_broken_symlink(tmp_path: Path) -> None:
    import os

    if os.name == "nt":
        pytest.skip("Windows 创建符号链接需要额外权限")
    link = tmp_path / "link.txt"
    link.symlink_to(tmp_path / "missing.txt")
    targets = collect_targets([link])
    assert len(targets) == 1
    assert targets[0].path == link
