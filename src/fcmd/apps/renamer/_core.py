"""renamer 纯逻辑：规则流水线、预览、两阶段执行与撤销。

规则按数组顺序作用于名称主干（默认）或扩展名，支持：

- ``replace``：正则查找替换，模板支持 ``\\1`` / ``\\g<name>`` 反向引用。
- ``reorder``：正则匹配后用 ``{0}`` / ``{1}`` / ``{name}`` 模板重排捕获片段，
  即「调整捕获内容出现的位置」。
- ``move``：把匹配文本整体搬到 ``start`` / ``end`` / ``before`` / ``after`` /
  ``index`` 指定位置。
- ``insert``：在指定位置插入字面文本。
- ``case``：大小写转换。
- ``sequence``：按批内排序追加 / 前置计数器。

预览把每条结果标为 ``ok`` / ``unchanged`` / ``invalid`` / ``conflict``；
执行采用「先全部改临时名、再改目标名」的两阶段策略，支持互换命名与仅大小写
改名，失败时尽力回滚。口径见 ADR 0014。
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "ANCHORS",
    "ANCHOR_AFTER",
    "ANCHOR_BEFORE",
    "ANCHOR_END",
    "ANCHOR_INDEX",
    "ANCHOR_START",
    "CASE_MODES",
    "FIELD_EXT",
    "FIELD_STEM",
    "RULE_FIELDS",
    "RULE_KINDS",
    "SORT_CREATED",
    "SORT_KEYS",
    "SORT_MODIFIED",
    "SORT_NAME",
    "STATUS_CONFLICT",
    "STATUS_INVALID",
    "STATUS_OK",
    "STATUS_UNCHANGED",
    "RenamePlan",
    "RenamePreview",
    "RenamePreviewItem",
    "RenameReport",
    "RenameRule",
    "RenameTarget",
    "RuleError",
    "apply_rule",
    "build_preview",
    "collect_targets",
    "execute_plan",
    "format_preview",
    "format_report",
    "join_name",
    "order_targets",
    "parse_rules",
    "preview_to_plan",
    "rules_to_json",
    "split_name",
    "undo_report",
    "validate_rules",
]

RULE_KINDS = ("replace", "reorder", "move", "insert", "case", "sequence")
RULE_FIELDS = ("stem", "ext")
FIELD_STEM = "stem"
FIELD_EXT = "ext"

ANCHORS = ("start", "end", "before", "after", "index")
ANCHOR_START = "start"
ANCHOR_END = "end"
ANCHOR_BEFORE = "before"
ANCHOR_AFTER = "after"
ANCHOR_INDEX = "index"

CASE_MODES = ("keep", "lower", "upper", "title", "sentence")

SORT_KEYS = ("name", "modified", "created")
SORT_NAME = "name"
SORT_MODIFIED = "modified"
SORT_CREATED = "created"

STATUS_OK = "ok"
STATUS_UNCHANGED = "unchanged"
STATUS_INVALID = "invalid"
STATUS_CONFLICT = "conflict"

_NAME_MAX_LENGTH = 255
_INVALID_NAME_RE = re.compile(r'[<>:"|?*\x00-\x1f]')
_TEMP_PREFIX = ".fcmd-renamer-"
_STR_FIELDS = ("kind", "pattern", "replacement", "field", "anchor", "anchor_text", "mode", "text")
_BOOL_FIELDS = ("ignore_case",)
_INT_FIELDS = ("occurrence", "position", "start", "step", "digits")
_JSON_FIELDS = set(_STR_FIELDS) | set(_BOOL_FIELDS) | set(_INT_FIELDS)


class RuleError(ValueError):
    """规则非法或求值失败；消息面向用户，CLI / GUI 直接展示。"""


@dataclass(frozen=True)
class RenameTarget:
    """一条待改名目标；时间用于 ``sequence`` 的批内排序。"""

    path: Path
    is_dir: bool = False
    mtime: float = 0.0
    ctime: float = 0.0


@dataclass(frozen=True)
class RenameRule:
    """单条规则；字段按 ``kind`` 取用，未用字段保留默认值。

    字段说明
    --------
    kind:
        ``replace`` / ``reorder`` / ``move`` / ``insert`` / ``case`` / ``sequence``
    pattern:
        正则表达式（Python ``re`` 语法），``replace`` / ``reorder`` / ``move`` 必填
    replacement:
        ``replace`` 的替换模板（支持 ``\\1``）；``reorder`` 的重排模板
        （支持 ``{0}`` / ``{1}`` / ``{name}``）
    field:
        作用域：``stem``（名称主干，默认）或 ``ext``（扩展名）
    ignore_case:
        正则是否忽略大小写
    occurrence:
        仅处理第 N 个匹配（1 起）；``0`` 表示全部
    anchor:
        ``move`` / ``insert`` 的落点：``start`` / ``end`` / ``before`` /
        ``after`` / ``index``
    anchor_text:
        ``before`` / ``after`` 的锚点文本
    position:
        ``index`` 落点或 ``insert`` 的绝对位置；负数从末尾计算
    mode:
        ``case`` 的转换模式：``keep`` / ``lower`` / ``upper`` / ``title`` /
        ``sentence``
    text:
        ``insert`` 的插入文本；``sequence`` 的计数器分隔符
    start / step / digits:
        ``sequence`` 的起始值、步长与补零位数
    """

    kind: str = "replace"
    pattern: str = ""
    replacement: str = ""
    field: str = FIELD_STEM
    ignore_case: bool = False
    occurrence: int = 0
    anchor: str = ANCHOR_START
    anchor_text: str = ""
    position: int = 0
    mode: str = "lower"
    text: str = ""
    start: int = 1
    step: int = 1
    digits: int = 3


@dataclass(frozen=True)
class RenamePreviewItem:
    """单条预览；``status`` 取 ok / unchanged / invalid / conflict。"""

    source: Path
    old_name: str
    new_name: str
    status: str
    note: str = ""


@dataclass(frozen=True)
class RenamePreview:
    """整批预览；``error`` 非空表示规则级错误（此时 ``items`` 为空）。"""

    items: tuple[RenamePreviewItem, ...] = ()
    error: str = ""

    @property
    def ok(self) -> tuple[RenamePreviewItem, ...]:
        """可执行的行。"""
        return tuple(item for item in self.items if item.status == STATUS_OK)

    @property
    def unchanged(self) -> tuple[RenamePreviewItem, ...]:
        """名称未变化的行。"""
        return tuple(item for item in self.items if item.status == STATUS_UNCHANGED)

    @property
    def invalid(self) -> tuple[RenamePreviewItem, ...]:
        """求值失败或名称非法的行。"""
        return tuple(item for item in self.items if item.status == STATUS_INVALID)

    @property
    def conflicts(self) -> tuple[RenamePreviewItem, ...]:
        """重名或目标已存在的行。"""
        return tuple(item for item in self.items if item.status == STATUS_CONFLICT)


@dataclass(frozen=True)
class RenamePlan:
    """可执行计划；只包含预览中 ``ok`` 的行。"""

    items: tuple[RenamePreviewItem, ...]

    @property
    def changes(self) -> tuple[RenamePreviewItem, ...]:
        """需要实际执行的行。"""
        return self.items


@dataclass
class RenameReport:
    """执行 / 撤销结果；``completed`` 为 (原路径, 新路径)。"""

    completed: list[tuple[Path, Path]] = field(default_factory=list)
    errors: list[tuple[Path, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """是否没有失败项。"""
        return not self.errors


# ---------------------------------------------------------------------- #
# 规则求值
# ---------------------------------------------------------------------- #
def split_name(name: str, is_dir: bool = False) -> tuple[str, str]:
    """拆成不含点的名称与扩展名；目录视为没有扩展名。"""
    if is_dir:
        return name, ""
    index = name.rfind(".")
    if index <= 0:
        return name, ""
    return name[:index], name[index + 1 :]


def join_name(stem: str, ext: str) -> str:
    """拼接名称主干与扩展名；空扩展名不补点。"""
    return stem if not ext else f"{stem}.{ext}"


def _compile(rule: RenameRule, prefix: str) -> re.Pattern[str]:
    """编译规则正则；非法时抛 :class:`RuleError`（带规则序号）。"""
    try:
        return re.compile(rule.pattern, re.IGNORECASE if rule.ignore_case else 0)
    except re.error as exc:
        raise RuleError(f"{prefix}：正则表达式无效：{exc}") from exc


def _select_matches(matches: list[re.Match[str]], occurrence: int) -> list[re.Match[str]]:
    """按 ``occurrence`` 选匹配：0 表示全部，N 表示第 N 个（越界返回空）。"""
    if occurrence <= 0:
        return matches
    if occurrence > len(matches):
        return []
    return [matches[occurrence - 1]]


def _expand_reorder(match: re.Match[str], template: str) -> str:
    """展开 ``{0}`` / ``{1}`` / ``{name}`` 模板；``{{`` / ``}}`` 输出字面括号。"""
    parts: list[str] = []
    index = 0
    while index < len(template):
        if template.startswith("{{", index):
            parts.append("{")
            index += 2
            continue
        if template.startswith("}}", index):
            parts.append("}")
            index += 2
            continue
        char = template[index]
        if char == "}":
            raise RuleError(f"重排模板位置 {index + 1} 出现多余的 }}")
        if char != "{":
            parts.append(char)
            index += 1
            continue
        end = template.find("}", index + 1)
        if end < 0:
            raise RuleError(f"重排模板位置 {index + 1} 的 {{ 未闭合")
        parts.append(_group_text(match, template[index + 1 : end], index))
        index = end + 1
    return "".join(parts)


def _group_text(match: re.Match[str], token: str, offset: int) -> str:
    """取单个模板占位符对应文本；未知捕获组抛错。"""
    if not token:
        raise RuleError(f"重排模板位置 {offset + 1} 的占位符为空")
    if token.isdigit():
        group_index = int(token)
        try:
            return match.group(group_index) or ""
        except IndexError as exc:
            raise RuleError(f"重排模板引用了不存在的捕获组：{{{token}}}") from exc
    if token in match.re.groupindex:
        return match.group(token) or ""
    raise RuleError(f"重排模板引用了不存在的命名捕获组：{{{token}}}")


def apply_rule(text: str, rule: RenameRule, position: int = 1) -> str:
    """把单条规则应用到文本；``position`` 为批内 1 起序号（供计数器使用）。"""
    if rule.kind == "replace":
        return _apply_replace(text, rule)
    if rule.kind == "reorder":
        return _apply_reorder(text, rule)
    if rule.kind == "move":
        return _apply_move(text, rule)
    if rule.kind == "insert":
        return _apply_insert(text, rule)
    if rule.kind == "case":
        return _apply_case(text, rule)
    if rule.kind == "sequence":
        return _apply_sequence(text, rule, position)
    raise RuleError(f"未知的规则类型：{rule.kind}")


def _apply_replace(text: str, rule: RenameRule) -> str:
    """正则替换；``occurrence`` 为 0 时替换全部，否则只替换第 N 个。"""
    compiled = _compile(rule, "replace")
    if rule.occurrence <= 0:
        try:
            return compiled.sub(lambda match: match.expand(rule.replacement), text)
        except (re.error, IndexError) as exc:
            raise RuleError(f"替换模板无效：{exc}") from exc
    counter = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal counter
        counter += 1
        if counter != rule.occurrence:
            return match.group(0)
        try:
            return match.expand(rule.replacement)
        except (re.error, IndexError) as exc:
            raise RuleError(f"替换模板无效：{exc}") from exc

    return compiled.sub(repl, text)


def _apply_reorder(text: str, rule: RenameRule) -> str:
    """按捕获组模板重排匹配片段；未匹配时原样返回。"""
    compiled = _compile(rule, "reorder")
    matches = _select_matches(list(compiled.finditer(text)), rule.occurrence)
    if not matches:
        return text
    starts = {match.start() for match in matches}

    def repl(match: re.Match[str]) -> str:
        if match.start() not in starts:
            return match.group(0)
        return _expand_reorder(match, rule.replacement)

    return compiled.sub(repl, text)


def _anchor_index(text: str, rule: RenameRule) -> int:
    """把锚点解析为 ``text`` 中的插入下标；锚点文本缺失时抛错。"""
    if rule.anchor == ANCHOR_START:
        return 0
    if rule.anchor == ANCHOR_END:
        return len(text)
    if rule.anchor == ANCHOR_INDEX:
        if rule.position < 0:
            return max(0, len(text) + rule.position)
        return min(rule.position, len(text))
    if not rule.anchor_text:
        raise RuleError("锚点规则需要提供 anchor_text")
    found = text.find(rule.anchor_text)
    if found < 0:
        raise RuleError(f"未找到锚点文本：{rule.anchor_text}")
    if rule.anchor == ANCHOR_BEFORE:
        return found
    return found + len(rule.anchor_text)


def _apply_move(text: str, rule: RenameRule) -> str:
    """把匹配文本整体搬到锚点位置；多个匹配按原顺序拼接。"""
    compiled = _compile(rule, "move")
    matches = _select_matches(list(compiled.finditer(text)), rule.occurrence)
    if not matches:
        return text
    moved = "".join(match.group(0) for match in matches)
    if not moved:
        return text
    pieces: list[str] = []
    last = 0
    for match in matches:
        pieces.append(text[last : match.start()])
        last = match.end()
    pieces.append(text[last:])
    rest = "".join(pieces)
    index = _anchor_index(rest, rule)
    return rest[:index] + moved + rest[index:]


def _apply_insert(text: str, rule: RenameRule) -> str:
    """在锚点位置插入字面文本。"""
    if not rule.text:
        raise RuleError("insert 规则需要提供 text")
    index = _anchor_index(text, rule)
    return text[:index] + rule.text + text[index:]


def _apply_case(text: str, rule: RenameRule) -> str:
    """大小写转换；``sentence`` 只把首字母转大写。"""
    if rule.mode == "keep":
        return text
    if rule.mode == "lower":
        return text.lower()
    if rule.mode == "upper":
        return text.upper()
    if rule.mode == "title":
        return text.title()
    if rule.mode == "sentence":
        return text[:1].upper() + text[1:]
    raise RuleError(f"未知的大小写模式：{rule.mode}")


def _apply_sequence(text: str, rule: RenameRule, position: int) -> str:
    """按批内序号插入计数器；``text`` 作为分隔符，前置时在计数器之后。"""
    counter = rule.start + (max(position, 1) - 1) * rule.step
    token = str(counter).zfill(rule.digits)
    if rule.anchor == ANCHOR_END:
        return f"{text}{rule.text}{token}"
    return f"{token}{rule.text}{text}"


def validate_rules(rules: Sequence[RenameRule]) -> None:
    """校验规则集；任何非法项立即抛 :class:`RuleError`。"""
    for index, rule in enumerate(rules, start=1):
        _validate_rule(rule, f"第 {index} 条规则")


def _validate_rule(rule: RenameRule, prefix: str) -> None:
    """校验单条规则的公共字段与类型专属约束。"""
    if rule.kind not in RULE_KINDS:
        raise RuleError(f"{prefix}：未知的规则类型 {rule.kind!r}")
    if rule.field not in RULE_FIELDS:
        raise RuleError(f"{prefix}：未知的作用域 {rule.field!r}")
    if rule.occurrence < 0:
        raise RuleError(f"{prefix}：occurrence 不能为负数")
    _validate_pattern(rule, prefix)
    _validate_anchor(rule, prefix)
    _validate_kind_fields(rule, prefix)


def _validate_pattern(rule: RenameRule, prefix: str) -> None:
    """正则类规则必须有可编译的 pattern。"""
    if rule.kind not in ("replace", "reorder", "move"):
        return
    if not rule.pattern:
        raise RuleError(f"{prefix}：{rule.kind} 规则需要正则表达式 pattern")
    _compile(rule, prefix)


def _validate_anchor(rule: RenameRule, prefix: str) -> None:
    """``move`` / ``insert`` 校验锚点；``sequence`` 只允许开头或结尾。"""
    if rule.kind == "sequence":
        if rule.anchor not in (ANCHOR_START, ANCHOR_END):
            raise RuleError(f"{prefix}：计数器锚点只能是 start 或 end")
        return
    if rule.kind not in ("move", "insert"):
        return
    if rule.anchor not in ANCHORS:
        raise RuleError(f"{prefix}：未知的锚点 {rule.anchor!r}")
    if rule.anchor in (ANCHOR_BEFORE, ANCHOR_AFTER) and not rule.anchor_text:
        raise RuleError(f"{prefix}：{rule.anchor} 锚点需要 anchor_text")


def _validate_kind_fields(rule: RenameRule, prefix: str) -> None:
    """校验各类型专属字段（插入文本 / 大小写模式 / 计数器位数）。"""
    if rule.kind == "insert" and not rule.text:
        raise RuleError(f"{prefix}：insert 规则需要插入文本 text")
    if rule.kind == "case" and rule.mode not in CASE_MODES:
        raise RuleError(f"{prefix}：未知的大小写模式 {rule.mode!r}")
    if rule.kind == "sequence" and not 1 <= rule.digits <= 32:
        raise RuleError(f"{prefix}：计数器位数必须在 1..32 之间")


# ---------------------------------------------------------------------- #
# 规则序列化
# ---------------------------------------------------------------------- #
def parse_rules(raw: object) -> tuple[RenameRule, ...]:
    """解析 JSON 规则（数组，或含 ``rules`` 数组的对象）；非法时抛错。"""
    if isinstance(raw, dict):
        raw = raw.get("rules", [])
    if not isinstance(raw, list):
        raise RuleError("规则必须是 JSON 数组")
    rules: list[RenameRule] = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise RuleError(f"第 {index} 条规则必须是 JSON 对象")
        rules.append(_rule_from_mapping(entry, index))
    validate_rules(rules)
    return tuple(rules)


def _rule_from_mapping(raw: Mapping[str, Any], index: int) -> RenameRule:
    """把单条 JSON 对象转成规则；字段类型不符或未知字段抛错。"""
    unknown = sorted(set(raw) - _JSON_FIELDS)
    if unknown:
        raise RuleError(f"第 {index} 条规则包含未知字段：{', '.join(unknown)}")
    kwargs: dict[str, Any] = {}
    for name in _STR_FIELDS:
        if name in raw:
            value = raw[name]
            if not isinstance(value, str):
                raise RuleError(f"第 {index} 条规则的 {name} 必须是字符串")
            kwargs[name] = value
    for name in _BOOL_FIELDS:
        if name in raw:
            value = raw[name]
            if not isinstance(value, bool):
                raise RuleError(f"第 {index} 条规则的 {name} 必须是布尔值")
            kwargs[name] = value
    for name in _INT_FIELDS:
        if name in raw:
            value = raw[name]
            if isinstance(value, bool) or not isinstance(value, int):
                raise RuleError(f"第 {index} 条规则的 {name} 必须是整数")
            kwargs[name] = value
    return RenameRule(**kwargs)


def rules_to_json(rules: Sequence[RenameRule]) -> list[dict[str, Any]]:
    """把规则集序列化为可写入 JSON 的字典列表。"""
    return [asdict(rule) for rule in rules]


# ---------------------------------------------------------------------- #
# 预览
# ---------------------------------------------------------------------- #
def order_targets(targets: Sequence[RenameTarget], sort_key: str = SORT_NAME) -> list[RenameTarget]:
    """按名称 / 修改时间 / 创建时间升序排序；同值按名称稳定排序。"""
    if sort_key == SORT_NAME:
        return sorted(targets, key=lambda item: item.path.name.casefold())
    if sort_key == SORT_MODIFIED:
        return sorted(targets, key=lambda item: (item.mtime, item.path.name.casefold()))
    if sort_key == SORT_CREATED:
        return sorted(targets, key=lambda item: (item.ctime, item.path.name.casefold()))
    raise RuleError(f"未知的排序方式：{sort_key}")


def _validate_name(name: str) -> str:
    """校验最终名称；返回去除首尾空白后的名称，非法时抛 ``ValueError``。"""
    cleaned = name.strip()
    if not cleaned:
        raise ValueError("名称不能为空")
    if cleaned in (".", ".."):
        raise ValueError("名称不能是 . 或 ..")
    if "/" in cleaned or "\\" in cleaned:
        raise ValueError("名称不能包含路径分隔符")
    if _INVALID_NAME_RE.search(cleaned):
        raise ValueError('名称不能包含 < > : " | ? * 等字符')
    return cleaned


def _compose_name(target: RenameTarget, rules: Sequence[RenameRule], position: int) -> str:
    """按规则顺序生成新名称（未做合法性校验）。"""
    stem, ext = split_name(target.path.name, target.is_dir)
    for rule in rules:
        if rule.field == FIELD_EXT:
            if not target.is_dir:
                ext = apply_rule(ext, rule, position)
        else:
            stem = apply_rule(stem, rule, position)
    return join_name(stem, ext)


def _preview_item(target: RenameTarget, rules: Sequence[RenameRule], position: int) -> RenamePreviewItem:
    """计算单条预览（尚未做批内冲突检测）。"""
    old_name = target.path.name
    try:
        generated = _compose_name(target, rules, position)
    except RuleError as exc:
        return RenamePreviewItem(target.path, old_name, old_name, STATUS_INVALID, str(exc))
    try:
        new_name = _validate_name(generated)
    except ValueError as exc:
        return RenamePreviewItem(target.path, old_name, generated, STATUS_INVALID, str(exc))
    if len(new_name) > _NAME_MAX_LENGTH:
        return RenamePreviewItem(
            target.path,
            old_name,
            new_name,
            STATUS_INVALID,
            f"名称过长（{len(new_name)} > {_NAME_MAX_LENGTH} 字符）",
        )
    if new_name == old_name:
        return RenamePreviewItem(target.path, old_name, new_name, STATUS_UNCHANGED, "名称未变化")
    return RenamePreviewItem(target.path, old_name, new_name, STATUS_OK)


def build_preview(
    targets: Sequence[RenameTarget],
    rules: Sequence[RenameRule],
    sort_key: str = SORT_NAME,
) -> RenamePreview:
    """计算整批预览并标记未变化 / 非法 / 冲突。"""
    try:
        validate_rules(rules)
        ordered = order_targets(targets, sort_key)
    except RuleError as exc:
        return RenamePreview(error=str(exc))
    items = [_preview_item(target, rules, position) for position, target in enumerate(ordered, start=1)]
    # 只有确定会改名的源才能被别的目标复用；未变化的源若被占用仍算冲突。
    moving = {os.path.normcase(str(item.source)) for item in items if item.status == STATUS_OK}
    seen: dict[tuple[str, str], int] = {}
    for position, item in enumerate(items, start=1):
        if item.status != STATUS_OK:
            continue
        parent = os.path.normcase(str(item.source.parent))
        key = (parent, os.path.normcase(item.new_name))
        if key in seen:
            items[position - 1] = RenamePreviewItem(
                item.source,
                item.old_name,
                item.new_name,
                STATUS_CONFLICT,
                f"与第 {seen[key]} 项目标同名",
            )
            continue
        seen[key] = position
        target_path = item.source.with_name(item.new_name)
        same_file = os.path.normcase(str(target_path)) == os.path.normcase(str(item.source))
        reusable = os.path.normcase(str(target_path)) in moving
        if not same_file and not reusable and (target_path.exists() or target_path.is_symlink()):
            items[position - 1] = RenamePreviewItem(
                item.source,
                item.old_name,
                item.new_name,
                STATUS_CONFLICT,
                "目标已存在",
            )
    return RenamePreview(tuple(items))


def preview_to_plan(preview: RenamePreview, selected: Sequence[Path] | None = None) -> RenamePlan:
    """把预览中 ``ok`` 的行转成可执行计划；``selected`` 可限定路径集合。"""
    wanted = None if selected is None else {os.path.normcase(str(path)) for path in selected}
    items = tuple(
        item
        for item in preview.items
        if item.status == STATUS_OK and (wanted is None or os.path.normcase(str(item.source)) in wanted)
    )
    return RenamePlan(items)


# ---------------------------------------------------------------------- #
# 目标收集
# ---------------------------------------------------------------------- #
def collect_targets(
    paths: Sequence[Path],
    *,
    recursive: bool = False,
    include_dirs: bool = False,
) -> tuple[RenameTarget, ...]:
    """展开目标：文件直接收集，目录按需展开；路径不存在时抛 ``FileNotFoundError``。"""
    collected: list[RenameTarget] = []
    seen: set[str] = set()

    def add(path: Path, is_dir: bool) -> None:
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        try:
            stat = path.stat()
        except OSError:
            mtime = ctime = 0.0
        else:
            mtime, ctime = stat.st_mtime, stat.st_ctime
        collected.append(RenameTarget(path, is_dir, mtime, ctime))

    for path in paths:
        if path.is_dir():
            entries = path.rglob("*") if recursive else path.iterdir()
            for entry in sorted(entries, key=lambda item: str(item).casefold()):
                is_dir = entry.is_dir()
                if is_dir and not include_dirs:
                    continue
                add(entry, is_dir)
            continue
        if not path.exists() and not path.is_symlink():
            raise FileNotFoundError(f"路径不存在：{path}")
        add(path, False)
    return tuple(collected)


# ---------------------------------------------------------------------- #
# 执行与撤销
# ---------------------------------------------------------------------- #
def execute_plan(plan: RenamePlan) -> RenameReport:
    """执行计划；目标被外部占用时跳过并记入错误，其余按两阶段改名。"""
    sources = {os.path.normcase(str(item.source)) for item in plan.items}
    pairs: list[tuple[Path, Path]] = []
    errors: list[tuple[Path, str]] = []
    for item in plan.items:
        target = item.source.with_name(item.new_name)
        if str(target) == str(item.source):
            continue
        occupied = target.exists() or target.is_symlink()
        if occupied and os.path.normcase(str(target)) not in sources:
            errors.append((item.source, "目标已存在"))
            continue
        pairs.append((item.source, target))
    report = _two_phase_rename(pairs)
    report.errors = errors + report.errors
    return report


def undo_report(report: RenameReport) -> RenameReport:
    """按相反顺序撤销一次执行结果；只对本次运行内的记录有效。"""
    pairs = [(target, source) for source, target in reversed(report.completed)]
    return _two_phase_rename(pairs)


def _two_phase_rename(pairs: Sequence[tuple[Path, Path]]) -> RenameReport:
    """两阶段改名：先全部改临时名，再改目标名；失败时尽力回滚。"""
    report = RenameReport()
    prepared: list[tuple[Path, Path, Path]] = []
    token = uuid.uuid4().hex[:8]
    for index, (source, target) in enumerate(pairs):
        if not source.exists() and not source.is_symlink():
            report.errors.append((source, "源路径不存在"))
            report.errors.extend(_rollback(prepared, ()))
            return report
        temp = source.with_name(f"{_TEMP_PREFIX}{token}-{index}")
        try:
            source.rename(temp)
        except OSError as exc:
            report.errors.append((source, f"移动到临时名失败：{exc}"))
            report.errors.extend(_rollback(prepared, ()))
            return report
        prepared.append((source, target, temp))
    finalized: list[tuple[Path, Path, Path]] = []
    for source, target, temp in prepared:
        try:
            temp.rename(target)
        except OSError as exc:
            report.errors.append((source, f"重命名失败：{exc}"))
            report.errors.extend(_rollback(prepared, finalized))
            return report
        finalized.append((source, target, temp))
        report.completed.append((source, target))
    return report


def _rollback(
    prepared: Sequence[tuple[Path, Path, Path]],
    finalized: Sequence[tuple[Path, Path, Path]],
) -> list[tuple[Path, str]]:
    """回滚：已到目标名的先退回临时名，再把所有临时名退回原名。"""
    errors: list[tuple[Path, str]] = []
    for _source, target, temp in reversed(finalized):
        try:
            target.rename(temp)
        except OSError as exc:
            errors.append((target, f"回滚到临时名失败：{exc}"))
    for source, _target, temp in prepared:
        if not (temp.exists() or temp.is_symlink()):
            continue
        try:
            temp.rename(source)
        except OSError as exc:
            errors.append((source, f"回滚原名失败：{exc}"))
    return errors


# ---------------------------------------------------------------------- #
# 文本报告
# ---------------------------------------------------------------------- #
def format_preview(preview: RenamePreview) -> str:
    """格式化预览：概要 + 逐项「旧名 → 新名」与状态说明。"""
    if preview.error:
        return f"规则错误：{preview.error}"
    if not preview.items:
        return "没有匹配的目标。"
    lines = [
        (
            f"共 {len(preview.items)} 项：可执行 {len(preview.ok)}，"
            f"未变化 {len(preview.unchanged)}，冲突 {len(preview.conflicts)}，"
            f"非法 {len(preview.invalid)}"
        )
    ]
    for item in preview.items:
        if item.status == STATUS_OK:
            lines.append(f"  [ok] {item.old_name} -> {item.new_name}")
            continue
        suffix = f"（{item.note}）" if item.note else ""
        if item.status == STATUS_UNCHANGED:
            lines.append(f"  [未变化] {item.old_name}{suffix}")
        else:
            lines.append(f"  [{item.status}] {item.old_name} -> {item.new_name}{suffix}")
    return "\n".join(lines)


def format_report(report: RenameReport) -> str:
    """格式化执行 / 撤销结果。"""
    lines = [f"已完成 {len(report.completed)} 项"]
    lines.extend(f"  {source.name} -> {target.name}" for source, target in report.completed)
    if report.errors:
        lines.append(f"失败 {len(report.errors)} 项")
        lines.extend(f"  {path}：{message}" for path, message in report.errors)
    return "\n".join(lines)
