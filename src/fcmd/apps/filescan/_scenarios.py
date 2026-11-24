"""filescan 场景规则模板：按场景整理的 JSON 规则集，随包发布、可直接加载。

模板改写自 uniscan 的 ``examples/rules/*.yaml``（MIT，见同目录 ``NOTICE``），
只做格式与语义映射，不改规则口径：

- ``literal`` → ``dictionary``（多词任一命中、忽略大小写）；
- ``regex`` → ``regex``；``filename-*`` → 匹配对象改为 ``filename``；
- ``composite``（``all_of`` + ``within`` 字符窗口）→ ``near[k]``，按每行约 80
  字符折算行窗口（``k = max(1, round(within / 80))``），是近似而非等价；
- ``expr`` → 同名表达式，运算符改小写；``severity: none`` 的隐藏叶子在 fcmd
  没有对应概念，降级为 ``info``；
- 组合式在 fcmd 不单独携带严重级，命中级别由参与规则的严重级决定。

本模块只依赖标准库与同包 ``_rules``，不导入 GUI 框架。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from fcmd.apps.filescan._rules import RuleSet, load_ruleset

__all__ = [
    "SCENARIO_DIR",
    "ScenarioTemplate",
    "load_scenario",
    "scenario_templates",
]

SCENARIO_DIR = Path(__file__).with_name("rules") / "scenarios"
"""场景模板目录（``filescan/rules/scenarios/*.json``）。"""

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")


@dataclass(frozen=True)
class ScenarioTemplate:
    """一个可加载的场景模板。"""

    slug: str
    name: str
    rule_count: int
    path: Path


def scenario_templates() -> tuple[ScenarioTemplate, ...]:
    """按文件名排序列出全部场景模板；模板损坏时抛 ``ValueError``。"""
    items: list[ScenarioTemplate] = []
    for path in sorted(SCENARIO_DIR.glob("*.json")):
        ruleset = load_ruleset(path)
        items.append(ScenarioTemplate(slug=path.stem, name=ruleset.name, rule_count=len(ruleset.rules), path=path))
    return tuple(items)


def load_scenario(slug: str) -> RuleSet:
    """按 slug 加载场景模板；未知或非法 slug 抛带可用列表的 ``ValueError``。"""
    if not _SLUG_RE.match(slug):
        raise ValueError(f"场景模板名只能包含小写字母、数字与连字符，实际为 {slug!r}")
    path = SCENARIO_DIR / f"{slug}.json"
    if not path.is_file():
        available = "、".join(item.slug for item in scenario_templates())
        raise ValueError(f"未知场景模板 {slug!r}，可用：{available or '（无）'}")
    return load_ruleset(path)
