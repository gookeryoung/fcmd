"""filescan 场景模板测试：清单、加载、表达式可解析与 CLI 接线。"""

from __future__ import annotations

import pytest

from fcmd.apps.filescan._rules import resolve_expression
from fcmd.apps.filescan._scenarios import load_scenario, scenario_templates

_EXPECTED = {
    "code-security",
    "compliance-audit",
    "data-classification",
    "dependency-audit",
    "dev-taboo",
    "devops-pipeline",
    "finance-budget",
    "iac-config",
    "ip-protection",
    "log-analysis",
    "privacy-contact",
    "security-audit",
}


def test_scenario_catalog_is_complete() -> None:
    templates = scenario_templates()
    assert {item.slug for item in templates} == _EXPECTED
    assert all(item.rule_count >= 3 for item in templates)
    assert all(item.name.startswith("场景：") for item in templates)


def test_every_scenario_loads_and_resolves() -> None:
    for item in scenario_templates():
        ruleset = load_scenario(item.slug)
        assert ruleset.rules
        assert ruleset.name == item.name
        # 组合式引用的规则都存在；near 窗口合法。
        resolve_expression(ruleset)
        for rule in ruleset.rules:
            assert rule.severity in ("info", "low", "medium", "high", "critical")


def test_unknown_and_illegal_slug_raise() -> None:
    with pytest.raises(ValueError, match="未知场景模板"):
        load_scenario("no-such-scene")
    with pytest.raises(ValueError, match="只能包含小写字母"):
        load_scenario("../etc/passwd")
