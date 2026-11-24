"""filescan 规则与表达式测试：匹配器、表达式语法、规则集与 JSON 往返。"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from fcmd.apps.filescan._rules import (
    DEFAULT_GROUP,
    DEFAULT_SEVERITY,
    MATCH_KEYWORD,
    MATCH_REGEX,
    MATCH_SIZE,
    MATCH_TIME,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LEVELS,
    SEVERITY_LOW,
    SIZE_BETWEEN,
    SIZE_LT,
    TARGET_CONTENT,
    TARGET_FILENAME,
    TARGET_PATH,
    TIME_AFTER,
    TIME_BEFORE,
    TIME_BETWEEN,
    TIME_CREATED,
    TIME_MODIFIED,
    TIME_TARGET_LABELS,
    AndExpr,
    Expression,
    Matcher,
    NearExpr,
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
    effective_expression_text,
    evaluate_expression,
    group_names,
    load_ruleset,
    matcher_from_mapping,
    matcher_to_mapping,
    near_rule_ids,
    parse_expression,
    positive_rule_ids,
    referenced_rule_ids,
    resolve_expression,
    rule_from_mapping,
    rule_to_mapping,
    ruleset_from_mapping,
    ruleset_to_mapping,
    save_ruleset,
    severity_label,
    validate_expression,
    validate_ruleset,
)


def _expr(text: str, *, expression_id: str = "expr", enabled: bool = True) -> Expression:
    return Expression(id=expression_id, name=f"组合式{expression_id}", expression=text, enabled=enabled)


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
    assert compiled.regex is not None
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


def test_parse_near_expression() -> None:
    expr = parse_expression("a near b", ["a", "b"])
    assert expr == NearExpr((RuleRef("a"), RuleRef("b")), 1)
    expr = parse_expression("a within[3] b", ["a", "b"])
    assert expr == NearExpr((RuleRef("a"), RuleRef("b")), 3)
    expr = parse_expression("a and b near[2] c", ["a", "b", "c"])
    assert isinstance(expr, AndExpr)
    assert expr.items[1] == NearExpr((RuleRef("b"), RuleRef("c")), 2)


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("a near[0] b", "必须 ≥ 1"),
        ("a near[x] b", "正整数"),
        ("a near[2 b", "右中括号"),
        ("(a or b) near c", "必须是规则 id"),
        ("a near[1] b near[2] c", "窗口行数必须一致"),
    ],
)
def test_parse_near_rejects_invalid_input(text: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_expression(text, ["a", "b", "c"])


def test_evaluate_near_uses_line_sets() -> None:
    expr = parse_expression("a near[2] b", ["a", "b"])
    assert evaluate_expression(expr, {"a": {2}, "b": {4}}) is True
    assert evaluate_expression(expr, {"a": {2}, "b": {5}}) is False
    assert evaluate_expression(expr, {"a": {2}}) is False
    expr3 = parse_expression("a near b", ["a", "b"])
    assert evaluate_expression(expr3, {"a": {2}, "b": {3}}) is True
    assert evaluate_expression(expr3, {"a": {2}, "b": {4}}) is False


def test_evaluate_near_requires_line_information() -> None:
    expr = parse_expression("a near b", ["a", "b"])
    with pytest.raises(ValueError, match="行号"):
        evaluate_expression(expr, frozenset({"a", "b"}))


def test_near_rule_ids_collects_references() -> None:
    expr = parse_expression("(a near[2] b) or not c", ["a", "b", "c"])
    assert near_rule_ids(expr) == ("a", "b")


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
    ruleset = RuleSet(name="n", rules=(_rule("a"), _rule("b", enabled=False)))
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
    ruleset = RuleSet(rules=(_rule("a"), _rule("b")), expressions=(_expr("a and b"),))
    with pytest.raises(ValueError, match="不存在的规则: b"):
        ruleset.remove_rule("b")


def test_disable_all_and_clear_rules() -> None:
    ruleset = RuleSet(rules=(_rule("a"), _rule("b")))
    disabled = disable_all(ruleset)
    assert disabled.enabled_count() == 0
    assert disabled.rule_ids() == ("a", "b")
    cleared = clear_rules(ruleset)
    assert cleared.rules == () and cleared.expressions == ()


def test_resolve_expression_prefers_explicit_and_handles_disabled() -> None:
    ruleset = RuleSet(rules=(_rule("a"), _rule("b", enabled=False)), expressions=(_expr("a or b"),))
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
        expressions=(_expr("a or not b"),),
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


def test_matcher_target_roundtrip_and_describe() -> None:
    matcher = Matcher(kind=MATCH_KEYWORD, text="secret", target=TARGET_FILENAME)
    payload = matcher_to_mapping(matcher)
    assert payload["target"] == TARGET_FILENAME
    assert matcher_from_mapping(payload).target == TARGET_FILENAME
    assert matcher.matches_filename() is True
    assert matcher.describe().startswith("文件名 · ")
    assert "target" not in matcher_to_mapping(Matcher(kind=MATCH_KEYWORD, text="x"))


def test_matcher_target_rejects_unknown_value() -> None:
    with pytest.raises(ValueError, match="未知匹配对象"):
        matcher_from_mapping({"type": MATCH_KEYWORD, "text": "x", "target": "unknown_target"})
    with pytest.raises(ValueError, match="匹配对象必须是"):
        Matcher(kind=MATCH_KEYWORD, text="x", target="unknown_target").validate()


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
    assert rule.severity == DEFAULT_SEVERITY
    assert rule_to_mapping(rule)["id"] == "a"
    assert rule_to_mapping(rule)["severity"] == DEFAULT_SEVERITY


def test_rule_severity_roundtrip_and_validation() -> None:
    payload = {"id": "a", "name": "x", "severity": SEVERITY_CRITICAL, "matcher": {"type": MATCH_KEYWORD, "text": "x"}}
    rule = rule_from_mapping(payload)
    assert rule.severity == SEVERITY_CRITICAL
    assert rule_to_mapping(rule)["severity"] == SEVERITY_CRITICAL
    with pytest.raises(ValueError, match="severity 必须是"):
        rule_from_mapping({**payload, "severity": "urgent"})
    with pytest.raises(ValueError, match="severity 必须是"):
        rule_from_mapping({**payload, "severity": 1})
    with pytest.raises(ValueError, match="严重级非法"):
        Rule(id="b", name="y", matcher=Matcher(kind=MATCH_KEYWORD, text="y"), severity="urgent").validate()
    assert severity_label(SEVERITY_HIGH) == "高"
    assert severity_label("urgent") == "urgent"


def test_builtin_rules_carry_severity() -> None:
    ruleset = builtin_ruleset()
    levels = {rule.id: rule.severity for rule in ruleset.rules}
    assert levels["aws_key"] == SEVERITY_CRITICAL
    assert levels["private_key"] == SEVERITY_CRITICAL
    assert levels["github_token"] == SEVERITY_CRITICAL
    assert levels["aliyun_ak"] == SEVERITY_CRITICAL
    assert levels["cn_id"] == SEVERITY_HIGH
    assert levels["db_conn"] == SEVERITY_HIGH
    assert levels["credit_code"] == DEFAULT_SEVERITY
    assert levels["email"] == SEVERITY_LOW
    assert levels["wechat_id"] == SEVERITY_LOW
    assert all(level in SEVERITY_LEVELS for level in levels.values())


def test_ruleset_mapping_rejects_invalid() -> None:
    with pytest.raises(ValueError, match="规则集必须是 JSON 对象"):
        ruleset_from_mapping([])
    with pytest.raises(ValueError, match="name 必须是非空字符串"):
        ruleset_from_mapping({"name": ""})
    with pytest.raises(ValueError, match="rules 必须是数组"):
        ruleset_from_mapping({"rules": {}})
    with pytest.raises(ValueError, match="expressions 必须是数组"):
        ruleset_from_mapping({"rules": [], "expressions": 1})
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
            {
                "rules": [{"id": "a", "name": "x", "matcher": {"type": MATCH_KEYWORD, "text": "x"}}],
                "expressions": [{"id": "e", "name": "E", "expression": "b"}],
            }
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
    assert {
        "cn_id",
        "cn_phone",
        "email",
        "bank_card",
        "api_key",
        "aws_key",
        "private_key",
        "db_conn",
        "password_assign",
        "jwt_token",
        "password_hash",
        "github_token",
        "slack_token",
        "aliyun_ak",
        "credit_code",
        "wechat_id",
    } <= ids
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
        ("db_conn", "postgres://user:pass@db.example.com:5432/app"),
        ("password_assign", 'password = "hunter2pass"'),
        ("jwt_token", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"),
        ("password_hash", "$2b$12$" + "a" * 53),
        ("github_token", "ghp_" + "A1b2C3d4E5f6G7h8I9j0" + "K1l2M3n4O5p6Q7r8"),
        ("slack_token", "xoxb-123456789012-abcdefghij"),
        ("aliyun_ak", "LTAI5tAbCdEfGhIjKlMn"),
        ("credit_code", "91350100M000100Y43"),
        ("wechat_id", "微信号：abc_12345"),
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


def test_expression_list_and_nesting() -> None:
    ruleset = RuleSet(
        rules=(_rule("a"), _rule("b"), _rule("c")),
        expressions=(
            _expr("a and not b", expression_id="base"),
            _expr("base or c", expression_id="top"),
        ),
    )
    assert ruleset.expression_ids() == ("base", "top")
    assert ruleset.all_ids() == ("a", "b", "c", "base", "top")
    expr = resolve_expression(ruleset)
    assert positive_rule_ids(expr) == ("a", "c")
    assert referenced_rule_ids(expr) == ("a", "b", "c")
    assert effective_expression_text(ruleset) == "(a and not b) or (base or c)"


def test_disabled_expression_can_be_building_block() -> None:
    ruleset = RuleSet(
        rules=(_rule("a"), _rule("b")),
        expressions=(
            _expr("a and b", expression_id="base", enabled=False),
            _expr("base", expression_id="top"),
        ),
    )
    expr = resolve_expression(ruleset)
    assert evaluate_expression(expr, frozenset({"a", "b"}))
    assert not evaluate_expression(expr, frozenset({"a"}))


def test_expression_cycle_is_rejected() -> None:
    ruleset = RuleSet(
        rules=(_rule("a"),),
        expressions=(
            _expr("top", expression_id="base"),
            _expr("base", expression_id="top"),
        ),
    )
    with pytest.raises(ValueError, match="循环引用"):
        validate_ruleset(ruleset)


def test_validate_expression_rejects_duplicate_id_and_bad_near() -> None:
    ruleset = RuleSet(rules=(_rule("a"), _rule("b")), expressions=(_expr("a", expression_id="base"),))
    with pytest.raises(ValueError, match="id 重复"):
        validate_expression(ruleset, _expr("a", expression_id="a"))
    with pytest.raises(ValueError, match="near 的每一项必须是规则 id"):
        validate_expression(ruleset, _expr("a near[2] base", expression_id="near_one"))


def test_remove_expression_guards_references() -> None:
    ruleset = RuleSet(
        rules=(_rule("a"),),
        expressions=(
            _expr("a", expression_id="base"),
            _expr("base", expression_id="top"),
        ),
    )
    with pytest.raises(ValueError, match="仍引用"):
        ruleset.remove_expression("base")
    removed = ruleset.remove_expression("top")
    assert removed.expression_ids() == ("base",)


def _time_matcher(**kwargs: object) -> Matcher:
    params: dict[str, object] = {
        "kind": MATCH_TIME,
        "target": TIME_MODIFIED,
        "compare": TIME_BEFORE,
        "value": "2026-01-01",
    }
    params.update(kwargs)
    return Matcher(**params)  # type: ignore[arg-type]


def test_time_matcher_describe_and_validate() -> None:
    before = _time_matcher()
    assert before.describe() == "修改时间 早于 2026-01-01"
    assert _time_matcher(target=TIME_CREATED, compare=TIME_AFTER).describe() == "创建时间 晚于 2026-01-01"
    between = _time_matcher(compare=TIME_BETWEEN, value2="2027-01-01")
    assert between.describe() == "修改时间 介于 2026-01-01 ~ 2027-01-01"

    with pytest.raises(ValueError, match="匹配对象必须是"):
        _time_matcher(target=TARGET_CONTENT).validate()
    with pytest.raises(ValueError, match="未知时间条件"):
        _time_matcher(compare="around").validate()
    with pytest.raises(ValueError, match="时间值不能为空"):
        _time_matcher(value="").validate()
    with pytest.raises(ValueError, match="不是合法的 ISO-8601"):
        _time_matcher(value="not-a-time").validate()
    with pytest.raises(ValueError, match="结束时间必须晚于开始时间"):
        _time_matcher(compare=TIME_BETWEEN, value="2027-01-01", value2="2026-01-01").validate()


def test_time_matcher_mapping_roundtrip() -> None:
    for matcher in (
        _time_matcher(),
        _time_matcher(target=TIME_CREATED, compare=TIME_AFTER, value="2026-06-01T09:30:00"),
        _time_matcher(compare=TIME_BETWEEN, value="2026-01-01", value2="2027-01-01"),
    ):
        payload = matcher_to_mapping(matcher)
        assert payload["type"] == MATCH_TIME
        assert matcher_from_mapping(payload) == matcher
    before_payload = matcher_to_mapping(_time_matcher())
    assert "value2" not in before_payload
    # 缺 target 时时间规则默认修改时间。
    assert (
        matcher_from_mapping({"type": MATCH_TIME, "compare": TIME_AFTER, "value": "2026-01-01"}).target == TIME_MODIFIED
    )
    with pytest.raises(ValueError, match="value / value2 必须是字符串"):
        matcher_from_mapping({"type": MATCH_TIME, "value": 1})


def test_time_matcher_compiles_to_window() -> None:
    compiled = compile_rule(Rule(id="t", name="时间", matcher=_time_matcher()))
    assert compiled.regex is None
    assert compiled.time_start is not None
    assert compiled.time_end is None
    between = compile_rule(Rule(id="b", name="区间", matcher=_time_matcher(compare=TIME_BETWEEN, value2="2027-01-01")))
    assert between.time_end is not None and between.time_end > between.time_start  # type: ignore[operator]
    with pytest.raises(ValueError, match="不编译正则"):
        compile_matcher(_time_matcher())


def test_ruleset_time_rule_roundtrip_and_near_guard() -> None:
    ruleset = RuleSet(
        rules=(_rule("a"), Rule(id="old", name="旧文件", matcher=_time_matcher())),
        expressions=(_expr("a or old"),),
    )
    restored = ruleset_from_mapping(ruleset_to_mapping(ruleset))
    assert restored.find("old") is not None
    assert restored.find("old").matcher.target == TIME_MODIFIED  # type: ignore[union-attr]
    assert ruleset_to_mapping(restored)["version"] == 4

    bad = RuleSet(
        rules=(Rule(id="old", name="旧文件", matcher=_time_matcher()),),
        expressions=(_expr("old near[2] old"),),
    )
    with pytest.raises(ValueError, match="near 只支持内容 / 文件名规则"):
        validate_ruleset(bad)


def test_time_target_labels_cover_all_targets() -> None:
    assert set(TIME_TARGET_LABELS) == {TIME_MODIFIED, TIME_CREATED, "accessed"}


def test_size_matcher_describe_validate_and_mapping() -> None:
    matcher = Matcher(kind=MATCH_SIZE, compare=SIZE_LT, value="10MB")
    assert matcher.describe() == "文件大小 小于 10MB"
    between = Matcher(kind=MATCH_SIZE, compare=SIZE_BETWEEN, value="1KB", value2="2MB")
    assert between.describe() == "文件大小 介于 1KB ~ 2MB"
    payload = matcher_to_mapping(between)
    assert matcher_from_mapping(payload) == between
    compiled = compile_rule(Rule(id="big", name="大文件", matcher=between))
    assert compiled.regex is None
    assert compiled.size_start == 1024 and compiled.size_end == 2 * 1024**2
    with pytest.raises(ValueError, match="大小区间的结束值必须大于开始值"):
        Matcher(kind=MATCH_SIZE, compare=SIZE_BETWEEN, value="2MB", value2="1KB").validate()
    with pytest.raises(ValueError, match="大小条件"):
        Matcher(kind=MATCH_SIZE, compare="before", value="1KB").validate()
    with pytest.raises(ValueError, match="大小值不能为空"):
        Matcher(kind=MATCH_SIZE, compare=SIZE_LT, value="").validate()


def test_path_target_and_validator_mapping() -> None:
    path_rule = Matcher(kind=MATCH_REGEX, pattern="财务", target=TARGET_PATH)
    payload = matcher_to_mapping(path_rule)
    assert payload["target"] == TARGET_PATH
    assert matcher_from_mapping(payload) == path_rule
    assert path_rule.matches_path() is True
    assert path_rule.describe().startswith("路径 · ")

    checked = Matcher(kind=MATCH_REGEX, pattern=r"\d{16}", target=TARGET_CONTENT, validator="luhn")
    payload = matcher_to_mapping(checked)
    assert payload["validator"] == "luhn"
    assert matcher_from_mapping(payload) == checked
    assert "Luhn" in checked.describe()
    with pytest.raises(ValueError, match="未知校验器"):
        Matcher(kind=MATCH_REGEX, pattern="x", validator="nope").validate()
    with pytest.raises(ValueError, match="关键词规则不支持校验器"):
        Matcher(kind=MATCH_KEYWORD, text="x", validator="luhn").validate()


def test_rule_hit_thresholds_defaults_and_mapping() -> None:
    rule = Rule(id="many", name="多次命中", matcher=Matcher(kind=MATCH_REGEX, pattern="x"), min_hits=3, max_hits=10)
    payload = rule_to_mapping(rule)
    assert payload["min_hits"] == 3
    assert payload["max_hits"] == 10
    assert rule_from_mapping(payload).min_hits == 3
    default_payload = rule_to_mapping(_rule("a"))
    assert "min_hits" not in default_payload and "max_hits" not in default_payload
    with pytest.raises(ValueError, match="min_hits"):
        Rule(id="bad", name="bad", matcher=Matcher(kind=MATCH_KEYWORD, text="x"), min_hits=0).validate()
    with pytest.raises(ValueError, match="max_hits"):
        Rule(id="bad", name="bad", matcher=Matcher(kind=MATCH_KEYWORD, text="x"), min_hits=5, max_hits=3).validate()
    with pytest.raises(ValueError, match="max_hits 必须是"):
        rule_from_mapping(
            {
                "id": "bad",
                "name": "bad",
                "matcher": {"type": MATCH_KEYWORD, "text": "x"},
                "max_hits": -1,
            }
        )
