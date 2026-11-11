"""filescan 规则模型与表达式（纯逻辑，不注册工具）。

规则分两类匹配器：关键词（可选大小写敏感与整词）与正则；多条规则通过
``and`` / ``or`` / ``not`` 与括号组成可嵌套表达式。表达式在**文件级**求值：
叶子规则为真表示该规则在文件任意一行命中，报告只列出参与表达式且实际命中的
正向规则明细。

规则集以 JSON 保存（见 :func:`ruleset_to_mapping`），加载时严格校验字段、
正则与表达式引用，错误信息带规则名或位置，不静默丢弃。

本模块只依赖标准库，不导入 Tk，也不参与工具发现。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "DEFAULT_GROUP",
    "DEFAULT_RULESET_NAME",
    "MATCH_KEYWORD",
    "MATCH_KINDS",
    "MATCH_REGEX",
    "AndExpr",
    "CompiledRule",
    "Expr",
    "Matcher",
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
    "evaluate_expression",
    "group_names",
    "load_ruleset",
    "parse_expression",
    "positive_rule_ids",
    "resolve_expression",
    "ruleset_from_mapping",
    "ruleset_to_mapping",
    "save_ruleset",
]

DEFAULT_GROUP = "内置"
DEFAULT_RULESET_NAME = "内置敏感规则"

MATCH_KEYWORD = "keyword"
MATCH_REGEX = "regex"
MATCH_KINDS: tuple[str, ...] = (MATCH_KEYWORD, MATCH_REGEX)

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_KEYWORDS = frozenset({"and", "or", "not"})


# ---------------------------------------------------------------------- #
# 匹配器
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class Matcher:
    """单条规则的匹配器。

    ``kind`` 为 ``keyword`` 时使用 ``text``（可配 ``whole_word``）；为 ``regex``
    时使用 ``pattern``。``case_sensitive`` 为 ``None`` 时按类型取默认值：
    关键词默认不区分大小写，正则默认区分大小写。
    """

    kind: str
    text: str = ""
    pattern: str = ""
    case_sensitive: bool | None = None
    whole_word: bool = False

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
        return text

    def validate(self) -> None:
        """校验匹配器字段；非法时抛 ``ValueError``（中文说明）。"""
        if self.kind not in MATCH_KINDS:
            raise ValueError(f"未知匹配器类型: {self.kind!r}，只支持 {'/'.join(MATCH_KINDS)}")
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

    def validate(self) -> None:
        """校验规则 id / 名称 / 匹配器。"""
        if not _IDENT_RE.fullmatch(self.id):
            raise ValueError(f"规则 id 非法（{self.id!r}）：只允许字母、数字、下划线、点与连字符，且以字母或下划线开头")
        if not self.name:
            raise ValueError(f"规则 {self.id!r} 缺少名称")
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


def compile_rule(rule: Rule) -> CompiledRule:
    """编译单条规则。"""
    return CompiledRule(rule=rule, regex=compile_matcher(rule.matcher))


# ---------------------------------------------------------------------- #
# 表达式
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class RuleRef:
    """叶子：引用一条规则。"""

    rule_id: str


@dataclass(frozen=True)
class NotExpr:
    """逻辑非。"""

    item: Expr


@dataclass(frozen=True)
class AndExpr:
    """逻辑与（空与非真）。"""

    items: tuple[Expr, ...] = ()


@dataclass(frozen=True)
class OrExpr:
    """逻辑或（空或为假）。"""

    items: tuple[Expr, ...] = ()


Expr = RuleRef | NotExpr | AndExpr | OrExpr


def _tokenize(text: str) -> list[tuple[str, str]]:
    """把表达式拆成 ``(类型, 文本)`` 记号；未知字符直接报错。"""
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        char = text[pos]
        if char.isspace():
            pos += 1
            continue
        if char in "()":
            tokens.append((char, char))
            pos += 1
            continue
        match = _IDENT_RE.match(text, pos)
        if match is None:
            rest = text[pos:].strip()
            raise ValueError(f"表达式包含无法识别的字符: {rest[:20]!r}")
        word = match.group(0)
        lowered = word.lower()
        if lowered in _KEYWORDS:
            tokens.append((lowered, word))
        else:
            tokens.append(("ident", word))
        pos = match.end()
    return tokens


class _Parser:
    """递归下降解析：``or`` 优先级低于 ``and``，``not`` 为一元前缀。"""

    def __init__(self, tokens: list[tuple[str, str]], rule_ids: Sequence[str] | None) -> None:
        self._tokens = tokens
        self._pos = 0
        self._rule_ids = None if rule_ids is None else frozenset(rule_ids)

    def parse(self) -> Expr:
        if not self._tokens:
            raise ValueError("表达式为空")
        expr = self._parse_or()
        if self._pos != len(self._tokens):
            raise ValueError(f"表达式存在多余内容: {self._tokens[self._pos][1]!r}")
        return expr

    def _peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _take(self) -> tuple[str, str]:
        token = self._tokens[self._pos]
        self._pos += 1
        return token

    def _at(self, kind: str) -> bool:
        token = self._peek()
        return token is not None and token[0] == kind

    def _parse_or(self) -> Expr:
        items = [self._parse_and()]
        while self._at("or"):
            self._take()
            items.append(self._parse_and())
        return items[0] if len(items) == 1 else OrExpr(tuple(items))

    def _parse_and(self) -> Expr:
        items = [self._parse_unary()]
        while self._at("and"):
            self._take()
            items.append(self._parse_unary())
        return items[0] if len(items) == 1 else AndExpr(tuple(items))

    def _parse_unary(self) -> Expr:
        token = self._peek()
        if token is None:
            raise ValueError("表达式不完整：末尾缺少规则或右括号")
        if token[0] == "not":
            self._take()
            return NotExpr(self._parse_unary())
        return self._parse_primary()

    def _parse_primary(self) -> Expr:
        token = self._peek()
        if token is None:
            raise ValueError("表达式不完整：缺少规则或右括号")
        if token[0] == "(":
            self._take()
            expr = self._parse_or()
            closing = self._peek()
            if closing is None or closing[0] != ")":
                raise ValueError("表达式括号不配对：缺少右括号")
            self._take()
            return expr
        if token[0] == "ident":
            self._take()
            rule_id = token[1]
            if self._rule_ids is not None and rule_id not in self._rule_ids:
                known = "、".join(sorted(self._rule_ids)) or "（无）"
                raise ValueError(f"表达式引用了不存在的规则: {rule_id}（可用规则: {known}）")
            return RuleRef(rule_id)
        raise ValueError(f"表达式此处应为规则或左括号，实际为 {token[1]!r}")


def parse_expression(text: str, rule_ids: Sequence[str] | None = None) -> Expr:
    """解析表达式文本；``rule_ids`` 非空时校验引用是否合法。

    Raises
    ------
    ValueError
        空表达式、未知记号、括号不配对、引用不存在的规则等。
    """
    return _Parser(_tokenize(text), rule_ids).parse()


def evaluate_expression(expr: Expr, matched_ids: frozenset[str] | set[str]) -> bool:
    """在文件级求值：``matched_ids`` 为文件内命中的规则 id 集合。"""
    if isinstance(expr, RuleRef):
        return expr.rule_id in matched_ids
    if isinstance(expr, NotExpr):
        return not evaluate_expression(expr.item, matched_ids)
    if isinstance(expr, AndExpr):
        return all(evaluate_expression(item, matched_ids) for item in expr.items)
    return any(evaluate_expression(item, matched_ids) for item in expr.items)


def positive_rule_ids(expr: Expr) -> tuple[str, ...]:
    """收集正向上下文中的规则 id（``not`` 分支内的规则不产出命中明细）。"""
    found: list[str] = []

    def walk(node: Expr, negated: bool) -> None:
        if isinstance(node, RuleRef):
            if not negated and node.rule_id not in found:
                found.append(node.rule_id)
        elif isinstance(node, NotExpr):
            walk(node.item, not negated)
        else:
            for item in node.items:
                walk(item, negated)

    walk(expr, False)
    return tuple(found)


# ---------------------------------------------------------------------- #
# 规则集
# ---------------------------------------------------------------------- #
@dataclass
class RuleSet:
    """一组规则 + 组合表达式。``expression`` 为空表示 OR 全部启用规则。"""

    name: str = DEFAULT_RULESET_NAME
    rules: tuple[Rule, ...] = ()
    expression: str = ""

    def find(self, rule_id: str) -> Rule | None:
        """按 id 查规则。"""
        return next((rule for rule in self.rules if rule.id == rule_id), None)

    def rule_ids(self) -> tuple[str, ...]:
        """全部规则 id（含禁用规则）。"""
        return tuple(rule.id for rule in self.rules)

    def enabled_rules(self) -> tuple[Rule, ...]:
        """启用的规则。"""
        return tuple(rule for rule in self.rules if rule.enabled)

    def replace_rule(self, rule: Rule) -> RuleSet:
        """替换同 id 规则（不存在时追加）。"""
        rules = tuple(rule if item.id == rule.id else item for item in self.rules)
        if rule.id not in self.rule_ids():
            rules = (*self.rules, rule)
        return replace(self, rules=rules)

    def remove_rule(self, rule_id: str) -> RuleSet:
        """删除规则；表达式仍引用已删规则时抛 ``ValueError``。"""
        rules = tuple(item for item in self.rules if item.id != rule_id)
        expression = self.expression
        if expression.strip():
            parse_expression(expression, [item.id for item in rules])
        return replace(self, rules=rules, expression=expression)

    def enabled_count(self) -> int:
        """启用规则数。"""
        return len(self.enabled_rules())


def default_expression(rules: RuleSet) -> str:
    """按启用规则生成默认 ``or`` 表达式；无启用规则时返回空串。"""
    ids = [rule.id for rule in rules.enabled_rules()]
    return " or ".join(ids)


def resolve_expression(ruleset: RuleSet) -> Expr:
    """返回生效表达式：显式表达式优先，否则 OR 全部启用规则。

    显式表达式按规则集全部 id 校验（允许引用禁用的规则，求值时为假）。
    """
    text = ruleset.expression.strip()
    if text:
        return parse_expression(text, ruleset.rule_ids())
    return OrExpr(tuple(RuleRef(rule.id) for rule in ruleset.enabled_rules()))


def disable_all(ruleset: RuleSet) -> RuleSet:
    """一键禁用全部规则（保留规则与表达式）。"""
    return replace(ruleset, rules=tuple(replace(rule, enabled=False) for rule in ruleset.rules))


def clear_rules(ruleset: RuleSet) -> RuleSet:
    """一键清空：删除全部规则并清空表达式。"""
    return replace(ruleset, rules=(), expression="")


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
    if kind == MATCH_KEYWORD:
        text = raw.get("text", "")
        if not isinstance(text, str):
            raise ValueError("关键词 text 必须是字符串")
        matcher = Matcher(
            kind=kind,
            text=text,
            case_sensitive=case_sensitive,
            whole_word=whole_word,
        )
    else:
        pattern = raw.get("pattern", "")
        if not isinstance(pattern, str):
            raise ValueError("正则 pattern 必须是字符串")
        matcher = Matcher(kind=kind, pattern=pattern, case_sensitive=case_sensitive)
    matcher.validate()
    return matcher


def rule_to_mapping(rule: Rule) -> dict[str, Any]:
    """规则转字典。"""
    return {
        "id": rule.id,
        "name": rule.name,
        "group": rule.group,
        "enabled": rule.enabled,
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
    if not isinstance(rule_id, str) or not rule_id:
        raise ValueError(f"第 {index + 1} 条规则缺少字符串 id")
    if not isinstance(name, str):
        raise ValueError(f"规则 {rule_id!r} 的 name 必须是字符串")
    if not isinstance(group, str) or not group:
        raise ValueError(f"规则 {rule_id!r} 的 group 必须是非空字符串")
    if not isinstance(enabled, bool):
        raise ValueError(f"规则 {rule_id!r} 的 enabled 必须是布尔值")
    if "matcher" not in raw:
        raise ValueError(f"规则 {rule_id!r} 缺少 matcher")
    rule = Rule(
        id=rule_id,
        name=name,
        matcher=matcher_from_mapping(raw["matcher"]),
        group=group,
        enabled=enabled,
    )
    rule.validate()
    return rule


def ruleset_to_mapping(ruleset: RuleSet) -> dict[str, Any]:
    """规则集转可写入 JSON 的字典。"""
    return {
        "version": 1,
        "name": ruleset.name,
        "rules": [rule_to_mapping(rule) for rule in ruleset.rules],
        "expression": ruleset.expression,
    }


def ruleset_from_mapping(raw: Any) -> RuleSet:
    """从字典解析规则集，校验重复 id、表达式引用与字段类型。"""
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
    expression = raw.get("expression", "")
    if not isinstance(expression, str):
        raise ValueError("expression 必须是字符串")
    if expression.strip():
        parse_expression(expression, [rule.id for rule in rules])
    return RuleSet(name=name, rules=tuple(rules), expression=expression)


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
def _builtin_rules() -> tuple[Rule, ...]:
    """内置示例规则：覆盖常见个人信息、联系方式、财务与凭证。"""
    return (
        Rule(
            id="cn_id",
            name="身份证号",
            group="个人信息",
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"(?<![0-9A-Za-z])[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9A-Za-z])",
            ),
        ),
        Rule(
            id="cn_phone",
            name="手机号",
            group="联系方式",
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        ),
        Rule(
            id="email",
            name="邮箱地址",
            group="联系方式",
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        ),
        Rule(
            id="bank_card",
            name="银行卡号",
            group="财务信息",
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"(?<!\d)(?:62|4\d|5[1-5])\d{13,17}(?!\d)"),
        ),
        Rule(
            id="api_key",
            name="API Key / 访问令牌",
            group="凭证",
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\b\s*[:=]\s*[\"']?[A-Za-z0-9_\-.]{12,}",
                case_sensitive=False,
            ),
        ),
        Rule(
            id="aws_key",
            name="AWS 访问密钥",
            group="凭证",
            matcher=Matcher(kind=MATCH_REGEX, pattern=r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ),
        Rule(
            id="private_key",
            name="私钥头",
            group="凭证",
            matcher=Matcher(
                kind=MATCH_REGEX,
                pattern=r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY(?: BLOCK)?-----",
                case_sensitive=False,
            ),
        ),
    )


def builtin_ruleset() -> RuleSet:
    """返回内置敏感规则集（表达式为空，即 OR 全部启用规则）。"""
    return RuleSet(name=DEFAULT_RULESET_NAME, rules=_builtin_rules(), expression="")
