"""filescan 规则与表达式测试：匹配器、表达式语法、规则集与 JSON 往返。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fcmd.apps.filescan._rules import (
    DEFAULT_GROUP,
    MATCH_KEYWORD,
    MATCH_REGEX,
    AndExpr,
    Matcher,
    NotExpr,
    OrExpr,
    Rule,
    RuleRef,
    RuleSet,
    builtin_ruleset,
    clear_rules,
    compile_matcher,
    compile_rule,
    default_expression,
    disable_all,
    dump_ruleset,
    evaluate_expression,
    group_names,
    load_ruleset,
    matcher_from_mapping,
    matcher_to_mapping,
    parse_expression,
    positive_rule_ids,
    resolve_expression,
    rule_from_mapping,
    rule_to_mapping,
    ruleset_from_mapping,
    ruleset_to_mapping,
    save_ruleset,
)


def _rule(rule_id: str = "a", *, enabled: bool = True, group: str = "g") -> Rule:
    return Rule(
        id=rule_id,
        name=f"规则{rule_id}",
        matcher=Matcher(kind=MATCH_KEYWORD, text=rule_id),
        group=group,
        enabled=enabled,
    )


# ---------------------------------------------------------------------- #
# 匹配器
# ---------------------------------------------------------------------- #
def test_keyword_default_ignores_case() -> None:
    regex = compile_matcher(Matcher(kind=MATCH_KEYWORD, text="Password"))
    assert regex.findall("PASSWORD here password")
    assert Matcher(kind=MATCH_KEYWORD, text="x").is_case_sensitive() is False


def test_keyword_case_sensitive_and_whole_word() -> None:
    regex = compile_matcher(Matcher(kind=MATCH_KEYWORD, text="cat", case_sensitive=True, whole_word=True))
    assert [m.group(0) for m in regex.finditer("a cat, category CAT")] == ["cat"]


def test_regex_default_is_case_sensitive() -> None:
    assert Matcher(kind=MATCH_REGEX, pattern="[A-Z]+").is_case_sensitive() is True
    regex = compile_matcher(Matcher(kind=MATCH_REGEX, pattern="abc", case_sensitive=False))
    assert regex.search("ABC")


def test_matcher_describe_covers_variants() -> None:
    assert "整词" in Matcher(kind=MATCH_KEYWORD, text="x", whole_word=True).describe()
    assert "忽略大小写" in Matcher(kind=MATCH_REGEX, pattern="x", case_sensitive=False).describe()
    assert Matcher(kind=MATCH_REGEX, pattern="x").describe() == "正则: x"


@pytest.mark.parametrize(
    ("matcher", "message"),
    [
        (Matcher(kind="unknown"), "未知匹配器类型"),
        (Matcher(kind=MATCH_KEYWORD, text=""), "关键词不能为空"),
        (Matcher(kind=MATCH_REGEX, pattern=""), "正则表达式不能为空"),
        (Matcher(kind=MATCH_REGEX, pattern="("), "正则表达式无效"),
    ],
)
def test_matcher_validate_rejects_invalid(matcher: Matcher, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        matcher.validate()


def test_rule_validate_rejects_bad_id_and_name() -> None:
    with pytest.raises(ValueError, match="规则 id 非法"):
        Rule(id="1bad", name="x", matcher=Matcher(kind=MATCH_KEYWORD, text="x")).validate()
    with pytest.raises(ValueError, match="缺少名称"):
        Rule(id="ok", name="", matcher=Matcher(kind=MATCH_KEYWORD, text="x")).validate()


def test_compile_rule_exposes_id_and_name() -> None:
    compiled = compile_rule(_rule("abc"))
    assert (compiled.id, compiled.name) == ("abc", "规则abc")
    assert compiled.regex.search("abc")


# ---------------------------------------------------------------------- #
# 表达式解析与求值
# ---------------------------------------------------------------------- #
def test_parse_precedence_and_nesting() -> None:
    expr = parse_expression("a or b and not c")
    assert isinstance(expr, OrExpr)
    assert isinstance(expr.items[1], AndExpr)
    assert isinstance(expr.items[1].items[1], NotExpr)


def test_parse_parentheses_and_keyword_case() -> None:
    expr = parse_expression("(a OR b) AND not c", ["a", "b", "c"])
    assert isinstance(expr, AndExpr)
    assert isinstance(expr.items[0], OrExpr)


def test_parse_rejects_unknown_rule() -> None:
    with pytest.raises(ValueError, match="不存在的规则: z"):
        parse_expression("z", ["a"])


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "表达式为空"),
        ("   ", "表达式为空"),
        ("a and", "表达式不完整"),
        ("(a", "括号不配对"),
        ("a )", "多余内容"),
        ("a $ b", "无法识别的字符"),
        ("()", "应为规则或左括号"),
    ],
)
def test_parse_rejects_malformed(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_expression(text, ["a"])


def test_evaluate_expression_nodes() -> None:
    matched = frozenset({"a"})
    assert evaluate_expression(RuleRef("a"), matched)
    assert not evaluate_expression(NotExpr(RuleRef("a")), matched)
    assert evaluate_expression(OrExpr((RuleRef("a"), RuleRef("b"))), matched)
    assert not evaluate_expression(AndExpr((RuleRef("a"), RuleRef("b"))), matched)
    assert evaluate_expression(AndExpr(()), matched)
    assert not evaluate_expression(OrExpr(()), matched)


def test_positive_rule_ids_skips_negated_branches() -> None:
    expr = parse_expression("a and not b or c and not (a or d)")
    assert positive_rule_ids(expr) == ("a", "c")
    assert positive_rule_ids(NotExpr(RuleRef("a"))) == ()


# ---------------------------------------------------------------------- #
# 规则集
# ---------------------------------------------------------------------- #
def test_ruleset_helpers() -> None:
    ruleset = RuleSet(name="n", rules=(_rule("a"), _rule("b", enabled=False)), expression="")
    assert ruleset.rule_ids() == ("a", "b")
    assert [rule.id for rule in ruleset.enabled_rules()] == ["a"]
    assert ruleset.enabled_count() == 1
    assert ruleset.find("b") is not None
    assert ruleset.find("z") is None
    assert default_expression(ruleset) == "a"
    assert group_names(ruleset) == ("g",)


def test_ruleset_replace_and_remove() -> None:
    ruleset = RuleSet(rules=(_rule("a"), _rule("b")))
    replaced = ruleset.replace_rule(_rule("a", enabled=False))
    replaced_a = replaced.find("a")
    assert replaced_a is not None and replaced_a.enabled is False
    grown = ruleset.replace_rule(_rule("c"))
    assert grown.rule_ids() == ("a", "b", "c")
    removed = ruleset.remove_rule("b")
    assert removed.rule_ids() == ("a",)


def test_remove_rule_guards_expression_reference() -> None:
    ruleset = RuleSet(rules=(_rule("a"), _rule("b")), expression="a and b")
    with pytest.raises(ValueError, match="不存在的规则: b"):
        ruleset.remove_rule("b")


def test_disable_all_and_clear_rules() -> None:
    ruleset = RuleSet(rules=(_rule("a"), _rule("b")))
    disabled = disable_all(ruleset)
    assert disabled.enabled_count() == 0
    assert disabled.rule_ids() == ("a", "b")
    cleared = clear_rules(ruleset)
    assert cleared.rules == () and cleared.expression == ""


def test_resolve_expression_prefers_explicit_and_handles_disabled() -> None:
    ruleset = RuleSet(rules=(_rule("a"), _rule("b", enabled=False)), expression="a or b")
    expr = resolve_expression(ruleset)
    assert evaluate_expression(expr, frozenset({"b"}))
    assert positive_rule_ids(expr) == ("a", "b")

    default = resolve_expression(RuleSet(rules=(_rule("a"), _rule("b", enabled=False))))
    assert positive_rule_ids(default) == ("a",)


def test_ruleset_json_roundtrip() -> None:
    ruleset = RuleSet(
        name="示例",
        rules=(
            _rule("a"),
            Rule(
                id="b",
                name="正则规则",
                matcher=Matcher(kind=MATCH_REGEX, pattern=r"\d+", case_sensitive=False),
                group="数字",
                enabled=False,
            ),
        ),
        expression="a or not b",
    )
    restored = ruleset_from_mapping(ruleset_to_mapping(ruleset))
    assert ruleset_to_mapping(restored) == ruleset_to_mapping(ruleset)
    assert dump_ruleset(restored).endswith("\n")


def test_matcher_mapping_defaults_and_overrides() -> None:
    assert matcher_to_mapping(Matcher(kind=MATCH_KEYWORD, text="x")) == {"type": MATCH_KEYWORD, "text": "x"}
    payload = matcher_to_mapping(Matcher(kind=MATCH_KEYWORD, text="x", whole_word=True, case_sensitive=True))
    assert payload["whole_word"] is True
    assert payload["case_sensitive"] is True
    assert matcher_from_mapping(payload) == Matcher(kind=MATCH_KEYWORD, text="x", whole_word=True, case_sensitive=True)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ([], "匹配器必须是 JSON 对象"),
        ({"type": "x"}, "未知匹配器类型"),
        ({"type": MATCH_KEYWORD, "text": 1}, "text 必须是字符串"),
        ({"type": MATCH_KEYWORD, "text": "x", "case_sensitive": "yes"}, "case_sensitive 必须是布尔值"),
        ({"type": MATCH_KEYWORD, "text": "x", "whole_word": 1}, "whole_word 必须是布尔值"),
        ({"type": MATCH_REGEX, "pattern": 1}, "pattern 必须是字符串"),
    ],
)
def test_matcher_from_mapping_rejects(raw: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        matcher_from_mapping(raw)


def test_rule_mapping_rejects_invalid_entries() -> None:
    with pytest.raises(ValueError, match="必须是 JSON 对象"):
        rule_from_mapping([])
    with pytest.raises(ValueError, match="缺少字符串 id"):
        rule_from_mapping({"name": "x", "matcher": {"type": MATCH_KEYWORD, "text": "x"}})
    with pytest.raises(ValueError, match="name 必须是字符串"):
        rule_from_mapping({"id": "a", "name": 1, "matcher": {"type": MATCH_KEYWORD, "text": "x"}})
    with pytest.raises(ValueError, match="group 必须是非空字符串"):
        rule_from_mapping({"id": "a", "name": "x", "group": "", "matcher": {"type": MATCH_KEYWORD, "text": "x"}})
    with pytest.raises(ValueError, match="enabled 必须是布尔值"):
        rule_from_mapping({"id": "a", "name": "x", "enabled": "yes", "matcher": {"type": MATCH_KEYWORD, "text": "x"}})
    with pytest.raises(ValueError, match="缺少 matcher"):
        rule_from_mapping({"id": "a", "name": "x"})


def test_rule_record_roundtrip_defaults() -> None:
    rule = rule_from_mapping({"id": "a", "name": "x", "matcher": {"type": MATCH_KEYWORD, "text": "x"}})
    assert rule.group == DEFAULT_GROUP and rule.enabled is True
    assert rule_to_mapping(rule)["id"] == "a"


def test_ruleset_mapping_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="规则集必须是 JSON 对象"):
        ruleset_from_mapping([])
    with pytest.raises(ValueError, match="name 必须是非空字符串"):
        ruleset_from_mapping({"name": ""})
    with pytest.raises(ValueError, match="rules 必须是数组"):
        ruleset_from_mapping({"rules": {}})
    with pytest.raises(ValueError, match="expression 必须是字符串"):
        ruleset_from_mapping({"rules": [], "expression": 1})
    with pytest.raises(ValueError, match="规则 id 重复"):
        ruleset_from_mapping(
            {
                "rules": [
                    {"id": "a", "name": "x", "matcher": {"type": MATCH_KEYWORD, "text": "x"}},
                    {"id": "a", "name": "y", "matcher": {"type": MATCH_KEYWORD, "text": "y"}},
                ]
            }
        )
    with pytest.raises(ValueError, match="不存在的规则"):
        ruleset_from_mapping(
            {"rules": [{"id": "a", "name": "x", "matcher": {"type": MATCH_KEYWORD, "text": "x"}}], "expression": "b"}
        )


def test_load_and_save_ruleset(tmp_path: Path) -> None:
    path = tmp_path / "rules" / "demo.json"
    save_ruleset(builtin_ruleset(), path)
    loaded = load_ruleset(path)
    assert loaded.rule_ids() == builtin_ruleset().rule_ids()


def test_load_ruleset_reports_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="无法读取规则集"):
        load_ruleset(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="不是合法 JSON"):
        load_ruleset(broken)
    invalid = tmp_path / "invalid.json"
    invalid.write_text(json.dumps({"rules": [{"id": "a"}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="内容无效"):
        load_ruleset(invalid)


# ---------------------------------------------------------------------- #
# 内置规则
# ---------------------------------------------------------------------- #
def test_builtin_ruleset_is_valid_and_compiles() -> None:
    ruleset = builtin_ruleset()
    ids = set(ruleset.rule_ids())
    assert {"cn_id", "cn_phone", "email", "bank_card", "api_key", "aws_key", "private_key"} <= ids
    for rule in ruleset.rules:
        rule.validate()
        assert isinstance(compile_matcher(rule.matcher), re.Pattern)


@pytest.mark.parametrize(
    ("rule_id", "sample"),
    [
        ("cn_id", "身份证 11010519491231002X"),
        ("cn_phone", "电话 13812345678"),
        ("email", "邮箱 user@example.com"),
        ("bank_card", "卡号 6222021234567890123"),
        ("api_key", 'api_key = "abcdefghijklmnop"'),
        ("aws_key", "AKIAIOSFODNN7EXAMPLE"),
        ("private_key", "-----BEGIN RSA PRIVATE KEY-----"),
    ],
)
def test_builtin_rules_match_samples(rule_id: str, sample: str) -> None:
    ruleset = builtin_ruleset()
    rule = ruleset.find(rule_id)
    assert rule is not None
    assert compile_matcher(rule.matcher).search(sample)


def test_builtin_rules_do_not_match_plain_text() -> None:
    ruleset = builtin_ruleset()
    text = "普通文本，没有任何敏感内容。"
    assert not any(compile_matcher(rule.matcher).search(text) for rule in ruleset.rules)
