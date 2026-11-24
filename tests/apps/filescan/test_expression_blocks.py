"""组合式列表：未启用的规则 / 组合式可作为构件，启用集合按 OR 生效。"""

from __future__ import annotations

from pathlib import Path

from fcmd.apps.filescan._engine import ScanOptions, scan_paths
from fcmd.apps.filescan._rules import (
    MATCH_REGEX,
    Expression,
    Matcher,
    Rule,
    RuleSet,
    referenced_rule_ids,
    resolve_expression,
)


def _rule(rule_id: str, pattern: str, *, enabled: bool) -> Rule:
    return Rule(
        id=rule_id,
        name=rule_id,
        matcher=Matcher(kind=MATCH_REGEX, pattern=pattern),
        enabled=enabled,
    )


def _ruleset(*expressions: Expression) -> RuleSet:
    # 两条规则都未启用：只作为组合式的构件存在。
    return RuleSet(
        rules=(
            _rule("phone", r"1\d{10}", enabled=False),
            _rule("marker", r"test_phone", enabled=False),
        ),
        expressions=expressions,
    )


def test_referenced_rule_ids_include_negated_rules() -> None:
    expression = resolve_expression(_ruleset(Expression(id="base", name="基础", expression="phone and not marker")))
    assert referenced_rule_ids(expression) == ("phone", "marker")


def test_disabled_rule_can_be_building_block(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("客户电话 13800138000", encoding="utf-8")
    (tmp_path / "b.txt").write_text("test_phone 13900139000", encoding="utf-8")
    (tmp_path / "c.txt").write_text("test_phone 13800138000", encoding="utf-8")

    ruleset = _ruleset(Expression(id="base", name="基础", expression="phone and not marker"))
    result = scan_paths([tmp_path], ruleset, ScanOptions(recursive=True))

    # b 命中 phone 但没有 marker；c 命中 marker 被排除；a 命中 phone 且没有 marker。
    assert [hit.path.name for hit in result.hits] == ["a.txt"]
    assert result.stats.hit_files == 1


def test_empty_expression_list_still_ignores_disabled_rules(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("客户电话 13800138000", encoding="utf-8")

    result = scan_paths([tmp_path], _ruleset(), ScanOptions(recursive=True))

    # 没有启用的组合式时按默认 OR 全部启用规则；两条都未启用，因此没有命中。
    assert result.hits == []


def test_disabled_expression_is_building_block(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("客户电话 13800138000", encoding="utf-8")
    (tmp_path / "b.txt").write_text("nothing", encoding="utf-8")

    ruleset = _ruleset(
        Expression(id="base", name="基础", expression="phone and not marker", enabled=False),
        Expression(id="top", name="上层", expression="base"),
    )
    result = scan_paths([tmp_path], ruleset, ScanOptions(recursive=True))

    assert [hit.path.name for hit in result.hits] == ["a.txt"]
