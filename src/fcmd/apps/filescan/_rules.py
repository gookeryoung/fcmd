"""filescan 规则模型与表达式（纯逻辑，不注册工具）。

规则分两类匹配器：关键词（可选大小写敏感与整词）与正则；多条规则通过
``and`` / ``or`` / ``not`` / ``near[k]`` 与括号组成可嵌套表达式。表达式在
**文件级**求值：叶子规则为真表示该规则在文件任意一行命中，报告只列出参与表达式
且实际命中的正向规则明细。``near[k]`` 表示若干规则在跨度不超过 k 行的窗口内
同时出现（k 默认 1），需要按行号求值。

规则集以 JSON 保存（见 :func:`ruleset_to_mapping`），加载时严格校验字段、
正则与表达式引用，错误信息带规则名或位置，不静默丢弃。

本模块只依赖标准库，不导入 GUI 框架，也不参与工具发现。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

from fcmd.apps.filescan._expr import (
    _IDENT_RE,
    AndExpr,
    Expr,
    NearExpr,
    NotExpr,
    OrExpr,
    RuleRef,
    evaluate_expression,
    near_rule_ids,
    parse_expression,
    positive_rule_ids,
    referenced_rule_ids,
)
from fcmd.apps.filescan._matcher import (
    CONTENT_TARGETS,
    MATCH_FILETYPE,
    MATCH_KEYWORD,
    MATCH_KINDS,
    MATCH_REGEX,
    MATCH_SIZE,
    MATCH_TARGETS,
    MATCH_TIME,
    TARGET_ARCHIVE_PATH,
    TARGET_CONTENT,
    TARGET_FILENAME,
    TARGET_PATH,
    Matcher,
    compile_matcher,
    matcher_from_mapping,
    matcher_to_mapping,
)
from fcmd.apps.filescan._sizeutil import (
    SIZE_BETWEEN,
    SIZE_GT,
    SIZE_LT,
    SIZE_OP_LABELS,
    SIZE_OPS,
    parse_size_value,
)
from fcmd.apps.filescan._timeutil import (
    TIME_ACCESSED,
    TIME_AFTER,
    TIME_BEFORE,
    TIME_BETWEEN,
    TIME_CREATED,
    TIME_MODIFIED,
    TIME_OP_LABELS,
    TIME_OPS,
    TIME_TARGET_LABELS,
    TIME_TARGETS,
    parse_time_value,
)
from fcmd.apps.filescan._validators import VALIDATOR_LABELS, VALIDATORS

__all__ = [
    "CONTENT_TARGETS",
    "DEFAULT_GROUP",
    "DEFAULT_RULESET_NAME",
    "DEFAULT_SEVERITY",
    "MATCH_FILETYPE",
    "MATCH_KEYWORD",
    "MATCH_KINDS",
    "MATCH_REGEX",
    "MATCH_SIZE",
    "MATCH_TARGETS",
    "MATCH_TIME",
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_INFO",
    "SEVERITY_LABELS",
    "SEVERITY_LEVELS",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "SIZE_BETWEEN",
    "SIZE_GT",
    "SIZE_LT",
    "SIZE_OPS",
    "SIZE_OP_LABELS",
    "TARGET_ARCHIVE_PATH",
    "TARGET_CONTENT",
    "TARGET_FILENAME",
    "TARGET_PATH",
    "TIME_ACCESSED",
    "TIME_AFTER",
    "TIME_BEFORE",
    "TIME_BETWEEN",
    "TIME_CREATED",
    "TIME_MODIFIED",
    "TIME_OPS",
    "TIME_OP_LABELS",
    "TIME_TARGETS",
    "TIME_TARGET_LABELS",
    "VALIDATORS",
    "VALIDATOR_LABELS",
    "AndExpr",
    "CompiledRule",
    "Expr",
    "Expression",
    "Matcher",
    "NearExpr",
    "NotExpr",
    "OrExpr",
    "Rule",
    "RuleRef",
    "RuleSet",
    "builtin_ruleset",
    "clear_rules",
    "compile_matcher",
    "compile_rule",
    "default_expression",
    "disable_all",
    "dump_ruleset",
    "effective_expression_text",
    "evaluate_expression",
    "group_names",
    "load_ruleset",
    "near_rule_ids",
    "parse_expression",
    "positive_rule_ids",
    "referenced_rule_ids",
    "resolve_expression",
    "ruleset_from_mapping",
    "ruleset_to_mapping",
    "save_ruleset",
    "severity_label",
    "validate_expression",
    "validate_ruleset",
]

DEFAULT_GROUP = "内置"
DEFAULT_RULESET_NAME = "内置敏感规则"

SEVERITY_INFO = "info"
SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"
SEVERITY_LEVELS: tuple[str, ...] = (
    SEVERITY_INFO,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    SEVERITY_HIGH,
    SEVERITY_CRITICAL,
)
"""严重级由低到高；用于报告列、CSV / JSON 导出与 GUI 筛选 / 着色。"""

DEFAULT_SEVERITY = SEVERITY_MEDIUM

SEVERITY_LABELS: dict[str, str] = {
    SEVERITY_INFO: "提示",
    SEVERITY_LOW: "低",
    SEVERITY_MEDIUM: "中",
    SEVERITY_HIGH: "高",
    SEVERITY_CRITICAL: "严重",
}
"""严重级中文标签（文本报告、CSV 与 GUI 展示用；JSON 保留英文枚举值）。"""


def severity_label(value: str) -> str:
    """返回严重级的中文标签；未知值原样返回，避免展示层因脏数据崩溃。"""
    return SEVERITY_LABELS.get(value, value)


# ---------------------------------------------------------------------- #
# 规则
# ---------------------------------------------------------------------- #
@dataclass
class Rule:
    """一条命名、可分组、可启停的扫描规则。"""

    id: str
    name: str
    matcher: Matcher
    group: str = DEFAULT_GROUP
    enabled: bool = True
    severity: str = DEFAULT_SEVERITY
    min_hits: int = 1
    max_hits: int = 0

    def validate(self) -> None:
        """校验规则 id / 名称 / 严重级 / 阈值 / 匹配器。"""
        if not _IDENT_RE.fullmatch(self.id):
            raise ValueError(f"规则 id 非法（{self.id!r}）：只允许字母、数字、下划线、点与连字符，且以字母或下划线开头")
        if not self.name:
            raise ValueError(f"规则 {self.id!r} 缺少名称")
        if self.severity not in SEVERITY_LEVELS:
            raise ValueError(f"规则 {self.id!r} 的严重级非法（{self.severity!r}）：只支持 {'/'.join(SEVERITY_LEVELS)}")
        if self.min_hits < 1:
            raise ValueError(f"规则 {self.id!r} 的 min_hits 必须 ≥ 1")
        if self.max_hits and self.max_hits < self.min_hits:
            raise ValueError(f"规则 {self.id!r} 的 max_hits 必须 ≥ min_hits 或为 0（不限）")
        self.matcher.validate()


@dataclass(frozen=True)
class CompiledRule:
    """规则 + 已编译正则（或预解析的时间窗口），供扫描引擎复用。"""

    rule: Rule
    regex: re.Pattern[str] | None
    time_start: float | None = None
    time_end: float | None = None
    size_start: int | None = None
    size_end: int | None = None
    file_types: frozenset[str] = frozenset()

    @property
    def id(self) -> str:
        return self.rule.id

    @property
    def name(self) -> str:
        return self.rule.name

    @property
    def severity(self) -> str:
        return self.rule.severity

    @property
    def matches_filename(self) -> bool:
        """该规则是否针对文件名求值。"""
        return self.rule.matcher.matches_filename()

    @property
    def matches_path(self) -> bool:
        """该规则是否针对完整路径求值。"""
        return self.rule.matcher.matches_path()


def compile_rule(rule: Rule) -> CompiledRule:
    """编译单条规则；时间规则只预解析时间窗口，不编译正则。"""
    matcher = rule.matcher
    if matcher.kind == MATCH_TIME:
        matcher.validate()
        start = parse_time_value(matcher.value)
        end = parse_time_value(matcher.value2) if matcher.compare == TIME_BETWEEN else None
        return CompiledRule(rule=rule, regex=None, time_start=start, time_end=end)
    if matcher.kind == MATCH_FILETYPE:
        matcher.validate()
        types = frozenset(item.strip() for item in matcher.value.split(",") if item.strip())
        return CompiledRule(rule=rule, regex=None, file_types=types)
    if matcher.kind == MATCH_SIZE:
        matcher.validate()
        start = parse_size_value(matcher.value)
        end = parse_size_value(matcher.value2) if matcher.compare == SIZE_BETWEEN else None
        return CompiledRule(rule=rule, regex=None, size_start=start, size_end=end)
    return CompiledRule(rule=rule, regex=compile_matcher(matcher))


@dataclass(frozen=True)
class Expression:
    """一条命名、可启停的组合式；引用规则 id 或其它组合式 id。

    ``enabled`` 只决定「是否默认生效」：未启用的组合式仍可被其它组合式引用，
    作为构件参与展开。生效集合没有启用的组合式时回落到 OR 全部启用规则。
    """

    id: str
    name: str
    expression: str
    enabled: bool = True

    def validate(self) -> None:
        """校验 id / 名称 / 表达式文本非空；引用关系由规则集统一校验。"""
        if not _IDENT_RE.fullmatch(self.id):
            raise ValueError(
                f"组合式 id 非法（{self.id!r}）：只允许字母、数字、下划线、点与连字符，且以字母或下划线开头"
            )
        if not self.name:
            raise ValueError(f"组合式 {self.id!r} 缺少名称")
        if not self.expression.strip():
            raise ValueError(f"组合式 {self.id!r} 的表达式不能为空")


# ---------------------------------------------------------------------- #
# 规则集
# ---------------------------------------------------------------------- #
@dataclass
class RuleSet:
    """一组规则 + 组合式列表。

    ``expressions`` 中 ``enabled`` 的组合式按 OR 生效；没有启用的组合式时，
    生效表达式回落为 OR 全部启用规则。组合式可嵌套引用其它组合式，加载时做
    环检测。
    """

    name: str = DEFAULT_RULESET_NAME
    rules: tuple[Rule, ...] = ()
    expressions: tuple[Expression, ...] = ()

    def find(self, rule_id: str) -> Rule | None:
        """按 id 查规则。"""
        return next((rule for rule in self.rules if rule.id == rule_id), None)

    def find_expression(self, expression_id: str) -> Expression | None:
        """按 id 查组合式。"""
        return next((item for item in self.expressions if item.id == expression_id), None)

    def rule_ids(self) -> tuple[str, ...]:
        """全部规则 id（含未启用规则）。"""
        return tuple(rule.id for rule in self.rules)

    def expression_ids(self) -> tuple[str, ...]:
        """全部组合式 id（含未启用组合式）。"""
        return tuple(item.id for item in self.expressions)

    def all_ids(self) -> tuple[str, ...]:
        """规则与组合式共用的 id 命名空间（表达式里可引用任一）。"""
        return (*self.rule_ids(), *self.expression_ids())

    def enabled_rules(self) -> tuple[Rule, ...]:
        """启用的规则。"""
        return tuple(rule for rule in self.rules if rule.enabled)

    def enabled_expressions(self) -> tuple[Expression, ...]:
        """默认生效的组合式。"""
        return tuple(item for item in self.expressions if item.enabled)

    def replace_rule(self, rule: Rule) -> RuleSet:
        """替换同 id 规则（不存在时追加）。"""
        rules = tuple(rule if item.id == rule.id else item for item in self.rules)
        if rule.id not in self.rule_ids():
            rules = (*self.rules, rule)
        return replace(self, rules=rules)

    def replace_expression(self, expression: Expression) -> RuleSet:
        """替换同 id 组合式（不存在时追加）。"""
        items = tuple(expression if item.id == expression.id else item for item in self.expressions)
        if expression.id not in self.expression_ids():
            items = (*self.expressions, expression)
        return replace(self, expressions=items)

    def remove_rule(self, rule_id: str) -> RuleSet:
        """删除规则；仍有表达式引用已删规则时抛 ``ValueError``。"""
        rules = tuple(item for item in self.rules if item.id != rule_id)
        candidate = replace(self, rules=rules)
        validate_ruleset(candidate)
        return candidate

    def remove_expression(self, expression_id: str) -> RuleSet:
        """删除组合式；仍有其它组合式引用它时抛 ``ValueError``。"""
        for item in self.expressions:
            if item.id == expression_id:
                continue
            try:
                node = parse_expression(item.expression, self.all_ids())
            except ValueError as exc:
                raise ValueError(f"组合式 {item.id!r} 无效：{exc}") from exc
            if expression_id in referenced_rule_ids(node):
                raise ValueError(f"组合式 {item.id!r} 仍引用 {expression_id!r}，请先修改或删除引用")
        return replace(self, expressions=tuple(item for item in self.expressions if item.id != expression_id))

    def enabled_count(self) -> int:
        """启用规则数。"""
        return len(self.enabled_rules())


def default_expression(rules: RuleSet) -> str:
    """按启用规则生成默认 ``or`` 表达式；无启用规则时返回空串。"""
    ids = [rule.id for rule in rules.enabled_rules()]
    return " or ".join(ids)


def effective_expression_text(ruleset: RuleSet) -> str:
    """用于展示的生效表达式文本：启用组合式按 OR 拼接，否则默认 OR 启用规则。"""
    enabled = [item.expression.strip() for item in ruleset.enabled_expressions() if item.expression.strip()]
    if enabled:
        return " or ".join(f"({text})" for text in enabled)
    return default_expression(ruleset)


def resolve_expression(ruleset: RuleSet) -> Expr:
    """返回生效表达式 AST：启用组合式 OR，否则 OR 全部启用规则。

    组合式可引用规则 id 或其它组合式 id，这里递归展开并检测循环引用。
    ``enabled`` 只决定默认生效集合：被引用的未启用组合式（及未启用规则）
    仍参与展开，可以作为构件使用。
    """
    enabled = ruleset.enabled_expressions()
    if not enabled:
        return OrExpr(tuple(RuleRef(rule.id) for rule in ruleset.enabled_rules()))
    specs = {item.id: item for item in ruleset.expressions}
    allowed = ruleset.all_ids()
    cache: dict[str, Expr] = {}
    stack: list[str] = []

    def expand(node: Expr) -> Expr:
        if isinstance(node, RuleRef):
            nested = specs.get(node.rule_id)
            return expand_spec(nested.id) if nested is not None else node
        if isinstance(node, NotExpr):
            return NotExpr(expand(node.item))
        if isinstance(node, NearExpr):
            items: list[RuleRef] = []
            for item in node.items:
                if item.rule_id in specs:
                    raise ValueError(f"near 的每一项必须是规则 id，不能引用组合式 {item.rule_id!r}")
                items.append(item)
            return NearExpr(tuple(items), node.distance)
        if isinstance(node, AndExpr):
            return AndExpr(tuple(expand(item) for item in node.items))
        return OrExpr(tuple(expand(item) for item in node.items))

    def expand_spec(expression_id: str) -> Expr:
        if expression_id in cache:
            return cache[expression_id]
        if expression_id in stack:
            chain = " -> ".join([*stack, expression_id])
            raise ValueError(f"组合式存在循环引用：{chain}")
        stack.append(expression_id)
        try:
            expanded = expand(parse_expression(specs[expression_id].expression, allowed))
        finally:
            stack.pop()
        cache[expression_id] = expanded
        return expanded

    items = tuple(expand_spec(item.id) for item in enabled)
    return items[0] if len(items) == 1 else OrExpr(items)


def validate_expression(ruleset: RuleSet, expression: Expression) -> None:
    """校验一条组合式（含加入后的引用 / 循环）；不改动原规则集。"""
    expression.validate()
    others = [item.id for item in ruleset.expressions if item.id != expression.id]
    if expression.id in ruleset.rule_ids() or expression.id in others:
        raise ValueError(f"规则 / 组合式 id 重复: {expression.id}")
    validate_ruleset(ruleset.replace_expression(expression))


def validate_ruleset(ruleset: RuleSet) -> None:
    """校验 id 命名空间、全部组合式的引用与循环（含未启用的组合式）。"""
    ids = ruleset.all_ids()
    if len(set(ids)) != len(ids):
        raise ValueError("规则 / 组合式 id 重复")
    allowed = ids
    for item in ruleset.expressions:
        try:
            parse_expression(item.expression, allowed)
        except ValueError as exc:
            raise ValueError(f"组合式 {item.id!r} 无效：{exc}") from exc
    # 全部组合式临时启用，保证未启用的组合式里的环也能被发现。
    probe = replace(ruleset, expressions=tuple(replace(item, enabled=True) for item in ruleset.expressions))
    resolved = resolve_expression(probe)
    for rule_id in near_rule_ids(resolved):
        rule = ruleset.find(rule_id)
        if rule is not None and rule.matcher.is_time():
            raise ValueError(f"near 只支持内容 / 文件名规则，不能引用时间规则 {rule_id!r}")


def disable_all(ruleset: RuleSet) -> RuleSet:
    """一键禁用全部规则与组合式（保留内容）。"""
    return replace(
        ruleset,
        rules=tuple(replace(rule, enabled=False) for rule in ruleset.rules),
        expressions=tuple(replace(item, enabled=False) for item in ruleset.expressions),
    )


def clear_rules(ruleset: RuleSet) -> RuleSet:
    """一键清空：删除全部规则与组合式。"""
    return replace(ruleset, rules=(), expressions=())


def group_names(ruleset: RuleSet) -> tuple[str, ...]:
    """规则集内出现的分组名（保持首次出现顺序）。"""
    names: list[str] = []
    for rule in ruleset.rules:
        if rule.group not in names:
            names.append(rule.group)
    return tuple(names)


# ---------------------------------------------------------------------- #
# JSON 序列化
# ---------------------------------------------------------------------- #
def rule_to_mapping(rule: Rule) -> dict[str, Any]:
    """规则转字典。"""
    return {
        "id": rule.id,
        "name": rule.name,
        "group": rule.group,
        "enabled": rule.enabled,
        "severity": rule.severity,
        "matcher": matcher_to_mapping(rule.matcher),
        **({"min_hits": rule.min_hits} if rule.min_hits != 1 else {}),
        **({"max_hits": rule.max_hits} if rule.max_hits else {}),
    }


def rule_from_mapping(raw: Any, *, index: int = 0) -> Rule:
    """从字典解析规则；错误信息带条目序号。"""
    if not isinstance(raw, dict):
        raise ValueError(f"第 {index + 1} 条规则必须是 JSON 对象")
    rule_id = raw.get("id")
    name = raw.get("name")
    group = raw.get("group", DEFAULT_GROUP)
    enabled = raw.get("enabled", True)
    severity = raw.get("severity", DEFAULT_SEVERITY)
    min_hits = raw.get("min_hits", 1)
    max_hits = raw.get("max_hits", 0)
    if not isinstance(rule_id, str) or not rule_id:
        raise ValueError(f"第 {index + 1} 条规则缺少字符串 id")
    if not isinstance(name, str):
        raise ValueError(f"规则 {rule_id!r} 的 name 必须是字符串")
    if not isinstance(group, str) or not group:
        raise ValueError(f"规则 {rule_id!r} 的 group 必须是非空字符串")
    if not isinstance(enabled, bool):
        raise ValueError(f"规则 {rule_id!r} 的 enabled 必须是布尔值")
    if not isinstance(severity, str) or severity not in SEVERITY_LEVELS:
        raise ValueError(f"规则 {rule_id!r} 的 severity 必须是 {'/'.join(SEVERITY_LEVELS)} 之一，实际为 {severity!r}")
    if not isinstance(min_hits, int) or isinstance(min_hits, bool) or min_hits < 1:
        raise ValueError(f"规则 {rule_id!r} 的 min_hits 必须是 ≥ 1 的整数")
    if not isinstance(max_hits, int) or isinstance(max_hits, bool) or max_hits < 0:
        raise ValueError(f"规则 {rule_id!r} 的 max_hits 必须是 ≥ 0 的整数（0 表示不限）")
    if "matcher" not in raw:
        raise ValueError(f"规则 {rule_id!r} 缺少 matcher")
    rule = Rule(
        id=rule_id,
        name=name,
        matcher=matcher_from_mapping(raw["matcher"]),
        group=group,
        enabled=enabled,
        severity=severity,
        min_hits=min_hits,
        max_hits=max_hits,
    )
    rule.validate()
    return rule


def expression_to_mapping(expression: Expression) -> dict[str, Any]:
    """组合式转字典。"""
    return {
        "id": expression.id,
        "name": expression.name,
        "expression": expression.expression,
        "enabled": expression.enabled,
    }


def expression_from_mapping(raw: Any, *, index: int = 0) -> Expression:
    """从字典解析组合式；错误信息带条目序号。"""
    if not isinstance(raw, dict):
        raise ValueError(f"第 {index + 1} 条组合式必须是 JSON 对象")
    expression_id = raw.get("id")
    name = raw.get("name")
    text = raw.get("expression")
    enabled = raw.get("enabled", True)
    if not isinstance(expression_id, str) or not expression_id:
        raise ValueError(f"第 {index + 1} 条组合式缺少字符串 id")
    if not isinstance(name, str) or not name:
        raise ValueError(f"组合式 {expression_id!r} 的 name 必须是非空字符串")
    if not isinstance(text, str):
        raise ValueError(f"组合式 {expression_id!r} 的 expression 必须是字符串")
    if not isinstance(enabled, bool):
        raise ValueError(f"组合式 {expression_id!r} 的 enabled 必须是布尔值")
    expression = Expression(id=expression_id, name=name, expression=text, enabled=enabled)
    expression.validate()
    return expression


def ruleset_to_mapping(ruleset: RuleSet) -> dict[str, Any]:
    """规则集转可写入 JSON 的字典（``version`` 3：规则 + 组合式 + 时间规则）。"""
    return {
        "version": 5,
        "name": ruleset.name,
        "rules": [rule_to_mapping(rule) for rule in ruleset.rules],
        "expressions": [expression_to_mapping(item) for item in ruleset.expressions],
    }


def ruleset_from_mapping(raw: Any) -> RuleSet:
    """从字典解析规则集，校验重复 id、表达式引用、嵌套循环与字段类型。"""
    if not isinstance(raw, dict):
        raise ValueError("规则集必须是 JSON 对象")
    name = raw.get("name", DEFAULT_RULESET_NAME)
    if not isinstance(name, str) or not name:
        raise ValueError("规则集 name 必须是非空字符串")
    raw_rules = raw.get("rules", [])
    if not isinstance(raw_rules, list):
        raise ValueError("rules 必须是数组")
    rules: list[Rule] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_rules):
        rule = rule_from_mapping(item, index=index)
        if rule.id in seen:
            raise ValueError(f"规则 id 重复: {rule.id}")
        seen.add(rule.id)
        rules.append(rule)
    raw_expressions = raw.get("expressions", [])
    if not isinstance(raw_expressions, list):
        raise ValueError("expressions 必须是数组")
    expressions: list[Expression] = []
    for index, item in enumerate(raw_expressions):
        expression = expression_from_mapping(item, index=index)
        if expression.id in seen:
            raise ValueError(f"规则 / 组合式 id 重复: {expression.id}")
        seen.add(expression.id)
        expressions.append(expression)
    ruleset = RuleSet(name=name, rules=tuple(rules), expressions=tuple(expressions))
    validate_ruleset(ruleset)
    return ruleset


def dump_ruleset(ruleset: RuleSet) -> str:
    """序列化为 JSON 文本（含末尾换行）。"""
    return json.dumps(ruleset_to_mapping(ruleset), ensure_ascii=False, indent=2) + "\n"


def load_ruleset(path: Path) -> RuleSet:
    """读取规则集文件；解析失败时抛带文件名的 ``ValueError``。"""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"无法读取规则集 {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"规则集不是合法 JSON（{path}）: {exc}") from exc
    try:
        return ruleset_from_mapping(raw)
    except ValueError as exc:
        raise ValueError(f"规则集内容无效（{path}）: {exc}") from exc


def save_ruleset(ruleset: RuleSet, path: Path) -> None:
    """把规则集写入文件；父目录自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_ruleset(ruleset), encoding="utf-8")


# ---------------------------------------------------------------------- #
# 内置敏感规则
# ---------------------------------------------------------------------- #
def builtin_ruleset() -> RuleSet:
    """返回内置敏感规则集（无组合式，即 OR 全部启用规则）。

    规则数据在 :mod:`fcmd.apps.filescan._builtin`，此处延迟导入以避免循环依赖。
    """
    from fcmd.apps.filescan._builtin import builtin_rules

    return RuleSet(name=DEFAULT_RULESET_NAME, rules=builtin_rules())
