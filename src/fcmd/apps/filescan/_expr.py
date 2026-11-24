"""filescan 表达式 DSL：AST、解析、求值与引用集合（纯逻辑）。

``_rules`` 在这里之上构建规则集与组合式列表；拆成独立模块是为了让规则集模型
（规则 / 组合式 / 校验 / JSON）与表达式语法各自保持可读长度。

表达式在**文件级**求值：叶子规则为真表示该文件任意一行命中；``near[k]`` 需要
每个规则的行号集合，其它节点按匹配到的规则 id 集合判定。``not`` 分支只参与
判定，不产出命中明细。
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

__all__ = [
    "AndExpr",
    "Expr",
    "NearExpr",
    "NotExpr",
    "OrExpr",
    "RuleRef",
    "evaluate_expression",
    "near_rule_ids",
    "parse_expression",
    "positive_rule_ids",
    "referenced_rule_ids",
]

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_KEYWORDS = frozenset({"and", "or", "not"})


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


@dataclass(frozen=True)
class NearExpr:
    """邻近：``items`` 中的规则在 ``distance`` 行窗口内同时出现。

    形如 ``password near[3] cn_phone``：任一命中行与其余命中行的跨度不超过
    3 行即成立。``items`` 只允许规则引用（不允许嵌套 ``and`` / ``or`` / ``not``），
    以便按行号做集合运算；需要否定时请用 ``(a or b) and not c`` 另行组合。
    """

    items: tuple[RuleRef, ...]
    distance: int = 1


Expr = RuleRef | NotExpr | AndExpr | OrExpr | NearExpr


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
        pos = match.end()
        if lowered in _NEAR_KEYWORDS:
            distance, pos = _near_distance(text, pos)
            tokens.append(("near", str(distance)))
            continue
        if lowered in _KEYWORDS:
            tokens.append((lowered, word))
        else:
            tokens.append(("ident", word))
    return tokens


_NEAR_KEYWORDS = frozenset({"near", "within"})


def _near_distance(text: str, pos: int) -> tuple[int, int]:
    """读取 ``near`` / ``within`` 后可选的行数窗口 ``[k]``，默认 1。"""
    look = pos
    while look < len(text) and text[look].isspace():
        look += 1
    if look >= len(text) or text[look] != "[":
        return 1, pos
    close = text.find("]", look + 1)
    if close == -1:
        raise ValueError("near 的窗口行数缺少右中括号 ]")
    body = text[look + 1 : close].strip()
    if not body.isdigit():
        raise ValueError(f"near 的窗口行数必须是正整数，实际为 {body!r}")
    distance = int(body)
    if distance < 1:
        raise ValueError("near 的窗口行数必须 ≥ 1")
    return distance, close + 1


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
        items = [self._parse_near()]
        while self._at("and"):
            self._take()
            items.append(self._parse_near())
        return items[0] if len(items) == 1 else AndExpr(tuple(items))

    def _parse_near(self) -> Expr:
        first = self._parse_unary()
        items: list[Expr] = [first]
        distance: int | None = None
        while self._at("near"):
            _, raw = self._take()
            current = int(raw)
            if distance is None:
                distance = current
            elif distance != current:
                raise ValueError("同一 near 链的窗口行数必须一致")
            items.append(self._parse_unary())
        if distance is None:
            return first
        refs: list[RuleRef] = []
        for item in items:
            if not isinstance(item, RuleRef):
                raise ValueError("near 的每一项必须是规则 id（不支持嵌套表达式）")
            refs.append(item)
        return NearExpr(tuple(refs), distance)

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


def evaluate_expression(
    expr: Expr,
    matched_ids: frozenset[str] | set[str] | Mapping[str, Iterable[int]],
) -> bool:
    """在文件级求值。

    ``matched_ids`` 既可以是「命中规则 id 的集合」（旧调用），也可以是
    「规则 id → 命中行号集合」的映射；只有含 ``near`` 的表达式必须使用映射，
    因为邻近判定依赖行号。集合口径下 ``near`` 会抛 ``ValueError``，避免静默
    给出错误结论。
    """
    lines: Mapping[str, Iterable[int]] | None = matched_ids if isinstance(matched_ids, Mapping) else None
    if isinstance(expr, RuleRef):
        return expr.rule_id in matched_ids
    if isinstance(expr, NotExpr):
        return not evaluate_expression(expr.item, matched_ids)
    if isinstance(expr, NearExpr):
        if lines is None:
            raise ValueError("near 表达式需要按行号求值，请传入「规则 id → 行号集合」映射")
        return _near_holds(expr, lines)
    if isinstance(expr, AndExpr):
        return all(evaluate_expression(item, matched_ids) for item in expr.items)
    return any(evaluate_expression(item, matched_ids) for item in expr.items)


def _near_holds(expr: NearExpr, lines: Mapping[str, Iterable[int]]) -> bool:
    """滑动窗口：存在一段跨度 ≤ ``distance`` 的行区间，覆盖全部规则。"""
    points: list[tuple[int, str]] = []
    for ref in expr.items:
        rule_lines = list(lines.get(ref.rule_id) or ())
        if not rule_lines:
            return False
        points.extend((line, ref.rule_id) for line in rule_lines)
    points.sort()
    need = {ref.rule_id for ref in expr.items}
    counts: dict[str, int] = {}
    covered = 0
    left = 0
    for _right, (line, rule_id) in enumerate(points):
        counts[rule_id] = counts.get(rule_id, 0) + 1
        if counts[rule_id] == 1:
            covered += 1
        while line - points[left][0] > expr.distance:
            left_id = points[left][1]
            counts[left_id] -= 1
            if counts[left_id] == 0:
                covered -= 1
            left += 1
        if covered == len(need):
            return True
    return False


def near_rule_ids(expr: Expr) -> tuple[str, ...]:
    """收集 ``near`` 中引用的规则 id（引擎据此决定哪些规则要记录行号）。"""
    found: list[str] = []

    def walk(node: Expr) -> None:
        if isinstance(node, NearExpr):
            for ref in node.items:
                if ref.rule_id not in found:
                    found.append(ref.rule_id)
        elif isinstance(node, NotExpr):
            walk(node.item)
        elif isinstance(node, (AndExpr, OrExpr)):
            for item in node.items:
                walk(item)

    walk(expr)
    return tuple(found)


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


def referenced_rule_ids(expr: Expr) -> tuple[str, ...]:
    """表达式引用到的全部规则 id（含 ``not`` 分支，按首次出现去重）。

    与 :func:`positive_rule_ids` 的区别：这里不区分正负上下文，用于确定「需要
    参与匹配计算的规则集合」。因此显式表达式里引用的未启用规则同样会被扫描，
    可以只作为组合式的构件存在。
    """
    found: list[str] = []

    def walk(node: Expr) -> None:
        if isinstance(node, RuleRef):
            if node.rule_id not in found:
                found.append(node.rule_id)
        elif isinstance(node, NotExpr):
            walk(node.item)
        else:
            for item in node.items:
                walk(item)

    walk(expr)
    return tuple(found)
