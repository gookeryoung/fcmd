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

__all__ = [
    "DEFAULT_GROUP",
    "DEFAULT_RULESET_NAME",
    "DEFAULT_SEVERITY",
    "MATCH_KEYWORD",
    "MATCH_KINDS",
    "MATCH_REGEX",
    "MATCH_TARGETS",
    "SEVERITY_CRITICAL",
    "SEVERITY_HIGH",
    "SEVERITY_INFO",
    "SEVERITY_LABELS",
    "SEVERITY_LEVELS",
    "SEVERITY_LOW",
    "SEVERITY_MEDIUM",
    "TARGET_CONTENT",
    "TARGET_FILENAME",
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

MATCH_KEYWORD = "keyword"
MATCH_REGEX = "regex"
MATCH_KINDS: tuple[str, ...] = (MATCH_KEYWORD, MATCH_REGEX)

TARGET_CONTENT = "content"
TARGET_FILENAME = "filename"
MATCH_TARGETS: tuple[str, ...] = (TARGET_CONTENT, TARGET_FILENAME)
"""匹配对象：``content`` 为文件正文（默认），``filename`` 为文件名（含后缀）。"""

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
# 匹配器
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class Matcher:
    """单条规则的匹配器。

    ``kind`` 为 ``keyword`` 时使用 ``text``（可配 ``whole_word``）；为 ``regex``
    时使用 ``pattern``。``case_sensitive`` 为 ``None`` 时按类型取默认值：
    关键词默认不区分大小写，正则默认区分大小写。``target`` 决定匹配文件名还是
    文件正文，文件名规则由引擎在读取内容前对 ``path.name`` 求值。
    """

    kind: str
    text: str = ""
    pattern: str = ""
    case_sensitive: bool | None = None
    whole_word: bool = False
    target: str = TARGET_CONTENT

    def is_case_sensitive(self) -> bool:
        """返回实际生效的大小写口径。"""
        if self.case_sensitive is not None:
            return self.case_sensitive
        return self.kind == MATCH_REGEX

    def describe(self) -> str:
        """返回界面摘要，如 ``关键词: password（忽略大小写）``。"""
        if self.kind == MATCH_KEYWORD:
            text = f"关键词: {self.text}"
            if self.whole_word:
                text += "（整词）"
        else:
            text = f"正则: {self.pattern}"
        if not self.is_case_sensitive():
            text += "（忽略大小写）"
        if self.target == TARGET_FILENAME:
            text = f"文件名 · {text}"
        return text

    def matches_filename(self) -> bool:
        """是否针对文件名求值。"""
        return self.target == TARGET_FILENAME

    def validate(self) -> None:
        """校验匹配器字段；非法时抛 ``ValueError``（中文说明）。"""
        if self.kind not in MATCH_KINDS:
            raise ValueError(f"未知匹配器类型: {self.kind!r}，只支持 {'/'.join(MATCH_KINDS)}")
        if self.target not in MATCH_TARGETS:
            raise ValueError(f"未知匹配对象: {self.target!r}，只支持 {'/'.join(MATCH_TARGETS)}")
        if self.kind == MATCH_KEYWORD:
            if not self.text:
                raise ValueError("关键词不能为空")
        elif not self.pattern:
            raise ValueError("正则表达式不能为空")
        else:
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"正则表达式无效（{self.pattern!r}）: {exc}") from exc


def compile_matcher(matcher: Matcher) -> re.Pattern[str]:
    """把匹配器编译成正则对象；关键词按需转义并加词边界。"""
    matcher.validate()
    flags = 0 if matcher.is_case_sensitive() else re.IGNORECASE
    if matcher.kind == MATCH_KEYWORD:
        body = re.escape(matcher.text)
        if matcher.whole_word:
            body = rf"(?<!\w){body}(?!\w)"
        return re.compile(body, flags)
    return re.compile(matcher.pattern, flags)


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

    def validate(self) -> None:
        """校验规则 id / 名称 / 严重级 / 匹配器。"""
        if not _IDENT_RE.fullmatch(self.id):
            raise ValueError(f"规则 id 非法（{self.id!r}）：只允许字母、数字、下划线、点与连字符，且以字母或下划线开头")
        if not self.name:
            raise ValueError(f"规则 {self.id!r} 缺少名称")
        if self.severity not in SEVERITY_LEVELS:
            raise ValueError(f"规则 {self.id!r} 的严重级非法（{self.severity!r}）：只支持 {'/'.join(SEVERITY_LEVELS)}")
        self.matcher.validate()


@dataclass(frozen=True)
class CompiledRule:
    """规则 + 已编译正则，供扫描引擎复用。"""

    rule: Rule
    regex: re.Pattern[str]

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


def compile_rule(rule: Rule) -> CompiledRule:
    """编译单条规则。"""
    return CompiledRule(rule=rule, regex=compile_matcher(rule.matcher))


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
    resolve_expression(probe)


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
def matcher_to_mapping(matcher: Matcher) -> dict[str, Any]:
    """匹配器转字典（省略与类型默认一致的 ``case_sensitive``）。"""
    payload: dict[str, Any] = {"type": matcher.kind}
    if matcher.kind == MATCH_KEYWORD:
        payload["text"] = matcher.text
        if matcher.whole_word:
            payload["whole_word"] = True
    else:
        payload["pattern"] = matcher.pattern
    if matcher.case_sensitive is not None:
        payload["case_sensitive"] = matcher.case_sensitive
    if matcher.target != TARGET_CONTENT:
        payload["target"] = matcher.target
    return payload


def matcher_from_mapping(raw: Any) -> Matcher:
    """从字典解析匹配器；字段类型不符或取值非法时抛 ``ValueError``。"""
    if not isinstance(raw, dict):
        raise ValueError("匹配器必须是 JSON 对象")
    kind = raw.get("type")
    if kind not in MATCH_KINDS:
        raise ValueError(f"未知匹配器类型: {kind!r}，只支持 {'/'.join(MATCH_KINDS)}")
    case_sensitive = raw.get("case_sensitive")
    if case_sensitive is not None and not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
    whole_word = raw.get("whole_word", False)
    if not isinstance(whole_word, bool):
        raise ValueError("whole_word 必须是布尔值")
    target = raw.get("target", TARGET_CONTENT)
    if target not in MATCH_TARGETS:
        raise ValueError(f"未知匹配对象 target: {target!r}，只支持 {'/'.join(MATCH_TARGETS)}")
    if kind == MATCH_KEYWORD:
        text = raw.get("text", "")
        if not isinstance(text, str):
            raise ValueError("关键词 text 必须是字符串")
        matcher = Matcher(
            kind=kind,
            text=text,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
            target=target,
        )
    else:
        pattern = raw.get("pattern", "")
        if not isinstance(pattern, str):
            raise ValueError("正则 pattern 必须是字符串")
        matcher = Matcher(kind=kind, pattern=pattern, case_sensitive=case_sensitive, target=target)
    matcher.validate()
    return matcher


def rule_to_mapping(rule: Rule) -> dict[str, Any]:
    """规则转字典。"""
    return {
        "id": rule.id,
        "name": rule.name,
        "group": rule.group,
        "enabled": rule.enabled,
        "severity": rule.severity,
        "matcher": matcher_to_mapping(rule.matcher),
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
    if "matcher" not in raw:
        raise ValueError(f"规则 {rule_id!r} 缺少 matcher")
    rule = Rule(
        id=rule_id,
        name=name,
        matcher=matcher_from_mapping(raw["matcher"]),
        group=group,
        enabled=enabled,
        severity=severity,
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
    """规则集转可写入 JSON 的字典（``version`` 2：规则 + 组合式列表）。"""
    return {
        "version": 2,
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
