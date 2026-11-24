"""filescan 匹配器：类型、字段校验、编译与 JSON 映射（纯逻辑）。

匹配器类型：

- ``keyword`` / ``regex``：匹配文件内容、文件名或完整路径（``target``）；
- ``time``：按修改 / 创建 / 访问时间比较；
- ``size``：按文件大小比较。

``reason`` 字段（正则校验器）只作用于 ``regex``，命中片段通过 Luhn / 身份证等
校验后才算有效命中。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from fcmd.apps.filescan._filetype import FILE_TYPES
from fcmd.apps.filescan._metadata import METADATA_KEYS, METADATA_LABELS
from fcmd.apps.filescan._sizeutil import SIZE_BETWEEN, SIZE_LT, SIZE_OPS, parse_size_value
from fcmd.apps.filescan._timeutil import (
    TIME_AFTER,
    TIME_BEFORE,
    TIME_BETWEEN,
    TIME_OPS,
    TIME_TARGET_LABELS,
    TIME_TARGETS,
    parse_time_value,
)
from fcmd.apps.filescan._validators import VALIDATORS

MATCH_KEYWORD = "keyword"
MATCH_REGEX = "regex"
MATCH_TIME = "time"
MATCH_SIZE = "size"
MATCH_FILETYPE = "filetype"
MATCH_METADATA = "metadata"
MATCH_DICTIONARY = "dictionary"
MATCH_HASH = "hash"
MATCH_ENTROPY = "entropy"
MATCH_KINDS: tuple[str, ...] = (
    MATCH_KEYWORD,
    MATCH_REGEX,
    MATCH_TIME,
    MATCH_SIZE,
    MATCH_FILETYPE,
    MATCH_METADATA,
    MATCH_DICTIONARY,
    MATCH_HASH,
    MATCH_ENTROPY,
)

TARGET_CONTENT = "content"
TARGET_FILENAME = "filename"
TARGET_PATH = "path"
TARGET_ARCHIVE_PATH = "archive_path"
CONTENT_TARGETS: tuple[str, ...] = (TARGET_CONTENT, TARGET_FILENAME, TARGET_PATH, TARGET_ARCHIVE_PATH)
MATCH_TARGETS: tuple[str, ...] = (*CONTENT_TARGETS, *TIME_TARGETS)
"""匹配对象：内容 / 文件名 / 路径 / 修改、创建、访问时间；合法组合由 ``Matcher.validate`` 约束。"""

_TIME_OP_LABELS = {TIME_BEFORE: "早于", TIME_AFTER: "晚于", TIME_BETWEEN: "介于"}
_SIZE_OP_LABELS = {SIZE_LT: "小于", SIZE_BETWEEN: "介于", "gt": "大于"}


@dataclass(frozen=True)
class Matcher:
    """单条规则的匹配器；不同 ``kind`` 只使用各自相关字段。"""

    kind: str
    text: str = ""
    pattern: str = ""
    case_sensitive: bool | None = None
    whole_word: bool = False
    target: str = TARGET_CONTENT
    compare: str = TIME_BEFORE
    value: str = ""
    value2: str = ""
    validator: str = ""

    def is_case_sensitive(self) -> bool:
        """返回实际生效的大小写口径（时间 / 大小规则不区分大小写）。"""
        if self.case_sensitive is not None:
            return self.case_sensitive
        return self.kind == MATCH_REGEX

    def describe(self) -> str:  # noqa: PLR0911, PLR0912 - 逐类型摘要
        """返回界面摘要，如 ``关键词: password（忽略大小写）``。"""
        if self.kind == MATCH_TIME:
            field = TIME_TARGET_LABELS.get(self.target, self.target)
            op = _TIME_OP_LABELS.get(self.compare, self.compare)
            if self.compare == TIME_BETWEEN:
                return f"{field} {op} {self.value} ~ {self.value2}"
            return f"{field} {op} {self.value}"
        if self.kind == MATCH_FILETYPE:
            return f"文件类型: {self.value}"
        if self.kind == MATCH_METADATA:
            return f"元数据 {METADATA_LABELS.get(self.value, self.value)} 包含: {self.text}"
        if self.kind == MATCH_DICTIONARY:
            terms = split_terms(self.value)
            return f"词典: {len(terms)} 项（{'、'.join(terms[:3])}{'…' if len(terms) > 3 else ''}）"
        if self.kind == MATCH_HASH:
            return f"文件哈希: {len(split_terms(self.value))} 个"
        if self.kind == MATCH_ENTROPY:
            return f"高熵串: ≥ {self.value}（长度 ≥ {self.value2 or '24'}）"
        if self.kind == MATCH_SIZE:
            op = _SIZE_OP_LABELS.get(self.compare, self.compare)
            if self.compare == SIZE_BETWEEN:
                return f"文件大小 {op} {self.value} ~ {self.value2}"
            return f"文件大小 {op} {self.value}"
        if self.kind == MATCH_KEYWORD:
            text = f"关键词: {self.text}"
            if self.whole_word:
                text += "（整词）"
        else:
            text = f"正则: {self.pattern}"
        if not self.is_case_sensitive():
            text += "（忽略大小写）"
        if self.validator:
            from fcmd.apps.filescan._validators import VALIDATOR_LABELS

            text += f"（{VALIDATOR_LABELS.get(self.validator, self.validator)}）"
        if self.target == TARGET_FILENAME:
            text = f"文件名 · {text}"
        elif self.target == TARGET_PATH:
            text = f"路径 · {text}"
        return text

    def matches_filename(self) -> bool:
        """是否针对文件名求值。"""
        return self.target == TARGET_FILENAME

    def matches_path(self) -> bool:
        """是否针对完整路径求值。"""
        return self.target == TARGET_PATH

    def is_time(self) -> bool:
        """是否时间规则（按文件时间戳比较，不读内容）。"""
        return self.kind == MATCH_TIME

    def is_size(self) -> bool:
        """是否文件大小规则。"""
        return self.kind == MATCH_SIZE

    def validate(self) -> None:  # noqa: PLR0911, PLR0912 - 逐类型校验字段
        """校验匹配器字段；非法时抛 ``ValueError``（中文说明）。"""
        if self.kind not in MATCH_KINDS:
            raise ValueError(f"未知匹配器类型: {self.kind!r}，只支持 {'/'.join(MATCH_KINDS)}")
        if self.kind == MATCH_TIME:
            self._validate_time()
            return
        if self.kind == MATCH_SIZE:
            self._validate_size()
            return
        if self.kind == MATCH_METADATA:
            if self.value not in METADATA_KEYS:
                raise ValueError(f"未知元数据字段: {self.value!r}，只支持 {'/'.join(METADATA_KEYS)}")
            if not self.text:
                raise ValueError("元数据规则需要填写要匹配的文本")
            return
        if self.kind == MATCH_DICTIONARY:
            if not split_terms(self.value):
                raise ValueError("词典规则需要至少一个词条")
            return
        if self.kind == MATCH_HASH:
            hashes = split_terms(self.value)
            if not hashes:
                raise ValueError("哈希规则需要至少一个 SHA-256 值")
            bad = [
                item for item in hashes if len(item) != 64 or any(char not in "0123456789abcdefABCDEF" for char in item)
            ]
            if bad:
                raise ValueError("哈希必须是 64 位十六进制 SHA-256")
            return
        if self.kind == MATCH_ENTROPY:
            try:
                threshold = float(self.value)
            except ValueError:
                raise ValueError("高熵阈值必须是数字（0–8）") from None
            if not 0 < threshold <= 8:
                raise ValueError("高熵阈值必须在 (0, 8] 之间")
            try:
                length = int(self.value2 or "24")
            except ValueError:
                raise ValueError("最小长度必须是整数") from None
            if length < 4:
                raise ValueError("最小长度必须 ≥ 4")
            return
        if self.kind == MATCH_FILETYPE:
            types = [item.strip() for item in self.value.split(",") if item.strip()]
            if not types:
                raise ValueError("文件类型规则需要至少一个类型（如 pdf,zip）")
            unknown = [item for item in types if item not in FILE_TYPES]
            if unknown:
                raise ValueError(f"未知文件类型: {'/'.join(unknown)}，只支持 {'/'.join(FILE_TYPES)}")
            return
        if self.target not in CONTENT_TARGETS:
            raise ValueError(
                f"关键词 / 正则规则的匹配对象必须是 {'/'.join(CONTENT_TARGETS)} 之一，实际为 {self.target!r}"
            )
        if self.validator not in VALIDATORS:
            raise ValueError(f"未知校验器: {self.validator!r}，只支持 {'/'.join(VALIDATORS)}")
        if self.kind == MATCH_KEYWORD:
            if self.validator:
                raise ValueError("关键词规则不支持校验器，请改用正则")
            if not self.text:
                raise ValueError("关键词不能为空")
            return
        if self.kind == MATCH_REGEX:
            if not self.pattern:
                raise ValueError("正则表达式不能为空")
            try:
                re.compile(self.pattern)
            except re.error as exc:
                raise ValueError(f"正则表达式无效（{self.pattern!r}）: {exc}") from exc

    def _validate_time(self) -> None:
        if self.target not in TIME_TARGETS:
            raise ValueError(f"时间规则的匹配对象必须是 {'/'.join(TIME_TARGETS)} 之一，实际为 {self.target!r}")
        if self.compare not in TIME_OPS:
            raise ValueError(f"未知时间条件: {self.compare!r}，只支持 {'/'.join(TIME_OPS)}")
        start = parse_time_value(self.value)
        if self.compare == TIME_BETWEEN and parse_time_value(self.value2) <= start:
            raise ValueError("时间区间的结束时间必须晚于开始时间")

    def _validate_size(self) -> None:
        if self.compare not in SIZE_OPS:
            raise ValueError(f"未知大小条件: {self.compare!r}，只支持 {'/'.join(SIZE_OPS)}")
        start = parse_size_value(self.value)
        if self.compare == SIZE_BETWEEN and parse_size_value(self.value2) <= start:
            raise ValueError("大小区间的结束值必须大于开始值")


def split_terms(value: str) -> list[str]:
    """把词典 / 哈希文本按换行或逗号拆成非空条目（去重保序）。"""
    items: list[str] = []
    for part in value.replace(",", "\n").splitlines():
        item = part.strip()
        if item and item not in items:
            items.append(item)
    return items


def compile_matcher(matcher: Matcher) -> re.Pattern[str]:
    """把匹配器编译成正则对象；关键词按需转义并加词边界，时间 / 大小规则不编译。"""
    matcher.validate()
    if matcher.kind in (MATCH_TIME, MATCH_SIZE):
        raise ValueError("时间 / 大小规则不编译正则，请使用 compile_rule 预解析窗口")
    flags = 0 if matcher.is_case_sensitive() else re.IGNORECASE
    if matcher.kind == MATCH_KEYWORD:
        body = re.escape(matcher.text)
        if matcher.whole_word:
            body = rf"(?<!\w){body}(?!\w)"
        return re.compile(body, flags)
    return re.compile(matcher.pattern, flags)


def matcher_to_mapping(matcher: Matcher) -> dict[str, Any]:  # noqa: PLR0912 - 逐类型映射
    """匹配器转字典（省略与类型默认一致的字段）。"""
    payload: dict[str, Any] = {"type": matcher.kind}
    if matcher.kind == MATCH_TIME:
        payload["target"] = matcher.target
        payload["compare"] = matcher.compare
        payload["value"] = matcher.value
        if matcher.compare == TIME_BETWEEN:
            payload["value2"] = matcher.value2
        return payload
    if matcher.kind == MATCH_FILETYPE:
        payload["value"] = matcher.value
        return payload
    if matcher.kind in (MATCH_METADATA, MATCH_DICTIONARY, MATCH_HASH, MATCH_ENTROPY):
        payload["value"] = matcher.value
        if matcher.kind == MATCH_METADATA:
            payload["text"] = matcher.text
        if matcher.kind == MATCH_ENTROPY:
            payload["value2"] = matcher.value2 or "24"
        return payload
    if matcher.kind == MATCH_SIZE:
        payload["compare"] = matcher.compare
        payload["value"] = matcher.value
        if matcher.compare == SIZE_BETWEEN:
            payload["value2"] = matcher.value2
        return payload
    if matcher.kind == MATCH_KEYWORD:
        payload["text"] = matcher.text
        if matcher.whole_word:
            payload["whole_word"] = True
    else:
        payload["pattern"] = matcher.pattern
        if matcher.validator:
            payload["validator"] = matcher.validator
    if matcher.case_sensitive is not None:
        payload["case_sensitive"] = matcher.case_sensitive
    if matcher.target != TARGET_CONTENT:
        payload["target"] = matcher.target
    return payload


def matcher_from_mapping(raw: Any) -> Matcher:  # noqa: PLR0912 - 逐类型校验字段
    """从字典解析匹配器；字段类型不符或取值非法时抛 ``ValueError``。"""
    if not isinstance(raw, dict):
        raise ValueError("匹配器必须是 JSON 对象")
    kind = raw.get("type")
    if kind not in MATCH_KINDS:
        raise ValueError(f"未知匹配器类型: {kind!r}，只支持 {'/'.join(MATCH_KINDS)}")
    target = raw.get("target", TARGET_CONTENT)
    if target not in MATCH_TARGETS:
        raise ValueError(f"未知匹配对象 target: {target!r}，只支持 {'/'.join(MATCH_TARGETS)}")
    if kind == MATCH_TIME:
        compare = raw.get("compare", TIME_BEFORE)
        value = raw.get("value", "")
        value2 = raw.get("value2", "")
        if not isinstance(compare, str):
            raise ValueError("时间条件 compare 必须是字符串")
        if not isinstance(value, str) or not isinstance(value2, str):
            raise ValueError("时间规则的 value / value2 必须是字符串")
        if target == TARGET_CONTENT:
            target = "modified"
        matcher = Matcher(kind=kind, target=target, compare=compare, value=value, value2=value2)
        matcher.validate()
        return matcher
    if kind in (MATCH_METADATA, MATCH_DICTIONARY, MATCH_HASH, MATCH_ENTROPY):
        value = raw.get("value", "")
        value2 = raw.get("value2", "24")
        text = raw.get("text", "")
        if not isinstance(value, str) or not isinstance(value2, str) or not isinstance(text, str):
            raise ValueError("高级规则的 value / value2 / text 必须是字符串")
        matcher = Matcher(kind=kind, value=value, value2=value2, text=text)
        matcher.validate()
        return matcher
    if kind == MATCH_FILETYPE:
        value = raw.get("value", "")
        if not isinstance(value, str):
            raise ValueError("文件类型规则的 value 必须是字符串")
        matcher = Matcher(kind=kind, value=value)
        matcher.validate()
        return matcher
    if kind == MATCH_SIZE:
        compare = raw.get("compare", SIZE_LT)
        value = raw.get("value", "")
        value2 = raw.get("value2", "")
        if not isinstance(compare, str):
            raise ValueError("大小条件 compare 必须是字符串")
        if not isinstance(value, str) or not isinstance(value2, str):
            raise ValueError("大小规则的 value / value2 必须是字符串")
        matcher = Matcher(kind=kind, compare=compare, value=value, value2=value2)
        matcher.validate()
        return matcher
    case_sensitive = raw.get("case_sensitive")
    if case_sensitive is not None and not isinstance(case_sensitive, bool):
        raise ValueError("case_sensitive 必须是布尔值")
    whole_word = raw.get("whole_word", False)
    if not isinstance(whole_word, bool):
        raise ValueError("whole_word 必须是布尔值")
    validator = raw.get("validator", "")
    if not isinstance(validator, str):
        raise ValueError("validator 必须是字符串")
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
        matcher = Matcher(
            kind=kind,
            pattern=pattern,
            case_sensitive=case_sensitive,
            target=target,
            validator=validator,
        )
    matcher.validate()
    return matcher
