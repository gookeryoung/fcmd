"""renamerw 纯逻辑测试：规则文本、表格行、设置持久化、主题 geometry 与注册。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from fcmd.apps.renamer import _examples, renamerw
from fcmd.apps.renamer._core import (
    ANCHOR_BEFORE,
    ANCHOR_END,
    RULE_KINDS,
    RenameRule,
    RenameTarget,
    RuleError,
    build_preview,
)
from fcmd.core.toolkit import _TOOL_REGISTRY

_EXAMPLES_DIR = Path(__file__).resolve().parents[3] / "docs" / "help" / "examples"


def test_tool_registration() -> None:
    """renamerw 注册为无子命令的窗口工具。"""
    assert None in _TOOL_REGISTRY["renamerw"]


def test_rules_text_roundtrip() -> None:
    rules = [
        RenameRule(kind="reorder", pattern=r"^(\d+)-(.+)$", replacement="{2}-{1}"),
        RenameRule(kind="sequence", text="_", digits=2),
    ]
    text = renamerw.rules_to_text(rules)
    assert "reorder" in text
    assert renamerw.rules_from_text(text) == tuple(rules)
    assert renamerw.rules_from_text("") == ()
    assert renamerw.rules_to_text([]) == "[]"


def test_rules_text_rejects_bad_json() -> None:
    with pytest.raises(RuleError, match="规则 JSON 解析失败"):
        renamerw.rules_from_text("{oops")


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        (RenameRule(kind="replace", pattern="a", replacement="b", occurrence=2), "仅第 2 处"),
        (RenameRule(kind="replace", pattern="a", replacement="b", ignore_case=True), "忽略大小写"),
        (RenameRule(kind="replace", pattern="a", replacement="b"), "正则替换"),
        (RenameRule(kind="reorder", pattern="(a)", replacement="{1}x"), "{1}x"),
        (RenameRule(kind="move", pattern=r"\d", anchor=ANCHOR_BEFORE, anchor_text="X"), "锚点前"),
        (RenameRule(kind="move", pattern=r"\d", anchor=ANCHOR_END), "结尾"),
        (RenameRule(kind="insert", text="_", anchor=ANCHOR_END), "插入"),
        (RenameRule(kind="case", mode="upper"), "大写"),
        (RenameRule(kind="sequence", text="_", start=2, step=3, digits=4), "步长 3"),
    ],
)
def test_rule_to_row_covers_all_kinds(rule: RenameRule, expected: str) -> None:
    row = renamerw.rule_to_row(rule)
    assert len(row) == 4
    assert expected in " ".join(row)


def test_settings_mapping_roundtrip() -> None:
    settings = renamerw.RenamerSettings(
        targets=["/tmp/a.txt"],
        rules_json="[]",
        recursive=True,
        include_dirs=True,
        sort_key="modified",
        geometry="1000x700+10+10",
    )
    restored = renamerw.settings_from_mapping(renamerw.settings_to_mapping(settings))
    assert restored == settings


def test_settings_from_mapping_tolerates_bad_values() -> None:
    base = renamerw.RenamerSettings(sort_key="created")
    restored = renamerw.settings_from_mapping(
        {
            "targets": ["ok", 1],
            "rules_json": "{broken",
            "recursive": "yes",
            "include_dirs": True,
            "sort_key": "size",
            "geometry": "bad",
        },
        base,
    )
    assert restored.targets == ["ok"]
    assert restored.rules_json == "[]"
    assert restored.recursive is False
    assert restored.include_dirs is True
    assert restored.sort_key == "created"
    assert restored.geometry is None


def test_settings_from_mapping_ignores_wrong_types() -> None:
    restored = renamerw.settings_from_mapping(
        {
            "targets": "not-a-list",
            "rules_json": 42,
            "recursive": 1,
            "include_dirs": 0,
            "sort_key": 3,
            "geometry": 5,
        }
    )
    assert restored == renamerw.RenamerSettings()


def test_config_path_honours_fcmd_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FCMD_HOME", str(tmp_path))
    assert renamerw.config_path() == tmp_path / "ui" / "renamerw.json"


def test_load_and_save_settings(tmp_path: Path) -> None:
    path = tmp_path / "ui" / "renamerw.json"
    assert renamerw.load_settings(path) == renamerw.RenamerSettings()
    settings = renamerw.RenamerSettings(
        targets=["/tmp/x"], rules_json=renamerw.rules_to_text([RenameRule(kind="case")])
    )
    renamerw.save_settings(settings, path)
    assert json.loads(path.read_text(encoding="utf-8"))["targets"] == ["/tmp/x"]
    assert renamerw.load_settings(path) == settings


def test_load_settings_ignores_corrupt_file(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    assert renamerw.load_settings(path) == renamerw.RenamerSettings()


def test_resolve_geometry_default_and_clamp() -> None:
    default = renamerw.resolve_geometry(None, 1920, 1080)
    assert "x" in default
    clamped = renamerw.resolve_geometry("500x400+0+0", 1920, 1080)
    width = int(clamped.split("x", 1)[0])
    assert width >= renamerw.MIN_WIDTH


def test_examples_produce_expected_names() -> None:
    """每个内置示例都能通过校验，并对演示名称生成预期结果。"""
    for example in _examples.EXAMPLES:
        preview = build_preview(
            [RenameTarget(Path(old)) for old, _new in example.demo],
            example.rules,
        )
        assert preview.error == "", example.name
        assert {item.old_name: item.new_name for item in preview.items} == dict(example.demo)


def test_doc_example_json_matches_embedded() -> None:
    """docs/help/examples 下的 JSON 与内置示例一致，避免文档与代码漂移。"""
    expected = {
        f"{index:02d}-{example.slug}.json": _examples.example_to_mapping(example)
        for index, example in enumerate(_examples.EXAMPLES, start=1)
    }
    actual = {path.name: json.loads(path.read_text(encoding="utf-8")) for path in _EXAMPLES_DIR.glob("*.json")}
    assert actual == expected


def test_help_text_mentions_all_rule_kinds() -> None:
    for kind in RULE_KINDS:
        assert kind in _examples.HELP_TEXT
