"""cmderw 多重命名工具：掩码求值、查找替换、大小写、计数器、预览与预设（纯逻辑）。

下划线前缀使其不参与 :mod:`fcmd` 的工具发现；Tk 层 ``cmderw.py`` 只做变量绑定、
预览渲染与执行调度。口径见 ADR 0009。
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from fcmd.tools.system._cmderw_ops import RenameItem, RenamePlan, validate_name

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "CASE_MODES",
    "RENAME_PRESET_LIMIT",
    "RENAME_SORT_KEYS",
    "MaskError",
    "RenameInput",
    "RenameOptions",
    "RenamePreset",
    "RenamePreview",
    "RenamePreviewItem",
    "apply_case",
    "apply_find_replace",
    "build_rename_preview",
    "expand_mask",
    "normalize_rename_presets",
    "order_inputs",
    "preview_to_plan",
    "rename_presets_to_json",
    "split_name",
]

CASE_MODES = ("keep", "lower", "upper", "title", "sentence")
RENAME_SORT_KEYS = ("name", "created", "modified")
RENAME_PRESET_LIMIT = 50
_NAME_MAX_LENGTH = 255


class MaskError(ValueError):
    """掩码 / 查找替换 / 参数非法；消息面向用户，带位置与占位符原文。"""


@dataclass(frozen=True)
class RenameInput:
    """一条待改名条目；时间用于日期占位符与计数排序。"""

    path: Path
    is_dir: bool = False
    mtime: float = 0.0
    ctime: float = 0.0


@dataclass(frozen=True)
class RenameOptions:
    """多重命名规则；字段与设置文件中的预设一一对应。"""

    name_mask: str = "[N]"
    ext_mask: str = "[E]"
    find: str = ""
    replace: str = ""
    use_regex: bool = False
    case_sensitive: bool = False
    only_extension: bool = False
    case_mode: str = "keep"
    start: int = 1
    step: int = 1
    digits: int = 3
    sort_key: str = "name"


@dataclass(frozen=True)
class RenamePreviewItem:
    """单个条目的预览行；``status`` 取 ok / unchanged / invalid / conflict。"""

    path: Path
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
        return tuple(item for item in self.items if item.status == "ok")

    @property
    def conflicts(self) -> tuple[RenamePreviewItem, ...]:
        """重名或目标已存在的行。"""
        return tuple(item for item in self.items if item.status == "conflict")

    @property
    def invalid(self) -> tuple[RenamePreviewItem, ...]:
        """名称非法或掩码求值失败的行。"""
        return tuple(item for item in self.items if item.status == "invalid")

    @property
    def unchanged(self) -> tuple[RenamePreviewItem, ...]:
        """名称未变化的行。"""
        return tuple(item for item in self.items if item.status == "unchanged")


@dataclass(frozen=True)
class RenamePreset:
    """命名的规则组合；只保存规则，不保存选择集。"""

    name: str
    options: RenameOptions = RenameOptions()


@dataclass(frozen=True)
class _MaskValues:
    stem: str
    ext: str
    counter: int
    digits: int
    moment: datetime


def split_name(name: str, is_dir: bool = False) -> tuple[str, str]:
    """拆成不含点的名称与扩展名；目录视为没有扩展名。"""
    if is_dir:
        return name, ""
    index = name.rfind(".")
    if index <= 0:
        return name, ""
    return name[:index], name[index + 1 :]


def _fragment_value(token: str, stem: str, offset: int) -> str | None:
    fragment = re.fullmatch(r"N(\d+)-(\d+)", token)
    if fragment is None:
        return None
    start, end = int(fragment.group(1)), int(fragment.group(2))
    if start < 1:
        raise MaskError(f"掩码位置 {offset + 1} 的片段起点必须从 1 开始：[{token}]")
    if start > end:
        raise MaskError(f"掩码位置 {offset + 1} 的片段范围起点大于终点：[{token}]")
    return stem[start - 1 : end]


_DATE_FORMATS = {"Y": "%Y", "M": "%m", "D": "%d", "h": "%H", "m": "%M", "s": "%S"}


def _token_text(token: str, values: _MaskValues, offset: int) -> str:
    if token == "[":
        return "["
    if token in ("N", "E"):
        return values.stem if token == "N" else values.ext
    if token == "C":
        return str(values.counter).zfill(max(values.digits, 1))
    if token in _DATE_FORMATS:
        return values.moment.strftime(_DATE_FORMATS[token])
    fragment = _fragment_value(token, values.stem, offset)
    if fragment is not None:
        return fragment
    raise MaskError(f"掩码位置 {offset + 1} 含未知占位符：[{token}]")


def expand_mask(  # noqa: PLR0913 - 占位符求值参数与掩码语法一一对应
    mask: str,
    *,
    stem: str,
    ext: str,
    counter: int = 1,
    digits: int = 3,
    moment: datetime | None = None,
) -> str:
    """展开掩码；``[[`` 输出字面 ``[``，未知占位符抛 :class:`MaskError`。"""
    values = _MaskValues(stem, ext, counter, digits, moment or datetime.now())
    parts: list[str] = []
    index = 0
    while index < len(mask):
        if mask.startswith("[[", index):
            parts.append("[")
            index += 2
            continue
        char = mask[index]
        if char != "[":
            parts.append(char)
            index += 1
            continue
        end = mask.find("]", index + 1)
        if end < 0:
            raise MaskError(f"掩码位置 {index + 1} 的 [ 未闭合")
        parts.append(_token_text(mask[index + 1 : end], values, index))
        index = end + 1
    return "".join(parts)


def apply_find_replace(
    text: str,
    *,
    find: str,
    replace: str,
    use_regex: bool = False,
    case_sensitive: bool = False,
) -> str:
    """查找替换；普通模式按字面替换，正则模式支持反向引用。"""
    if not find:
        return text
    if use_regex:
        try:
            return re.sub(find, replace, text, flags=0 if case_sensitive else re.IGNORECASE)
        except re.error as exc:
            raise MaskError(f"正则表达式无效：{exc}") from exc
    if case_sensitive:
        return text.replace(find, replace)
    return re.sub(re.escape(find), lambda _match: replace, text, flags=re.IGNORECASE)


def apply_case(text: str, mode: str) -> str:
    """大小写转换；``sentence`` 只把首字母转大写，其余保持。"""
    if mode == "keep":
        return text
    if mode == "lower":
        return text.lower()
    if mode == "upper":
        return text.upper()
    if mode == "title":
        return text.title()
    if mode == "sentence":
        return text[:1].upper() + text[1:]
    raise MaskError(f"未知的大小写模式：{mode}")


def order_inputs(inputs: Sequence[RenameInput], sort_key: str) -> list[RenameInput]:
    """按名称 / 创建时间 / 修改时间升序排序，同值时按名称稳定排序。"""
    if sort_key == "name":
        return sorted(inputs, key=lambda item: item.path.name.casefold())
    if sort_key == "created":
        return sorted(inputs, key=lambda item: (item.ctime, item.path.name.casefold()))
    if sort_key == "modified":
        return sorted(inputs, key=lambda item: (item.mtime, item.path.name.casefold()))
    raise MaskError(f"未知的计数排序方式：{sort_key}")


def _validate_options(options: RenameOptions) -> None:
    if options.digits < 1:
        raise MaskError("计数器位数至少为 1")
    if options.digits > 32:
        raise MaskError("计数器位数不能超过 32")
    if not options.name_mask.strip():
        raise MaskError("名称掩码不能为空")
    if options.case_mode not in CASE_MODES:
        raise MaskError(f"未知的大小写模式：{options.case_mode}")
    if options.sort_key not in RENAME_SORT_KEYS:
        raise MaskError(f"未知的计数排序方式：{options.sort_key}")


def _rename_one(
    item: RenameInput,
    options: RenameOptions,
    counter: int,
) -> RenamePreviewItem:
    old_name = item.path.name
    stem, ext = split_name(old_name, item.is_dir)
    if options.only_extension:
        ext = apply_find_replace(
            ext,
            find=options.find,
            replace=options.replace,
            use_regex=options.use_regex,
            case_sensitive=options.case_sensitive,
        )
    else:
        stem = apply_find_replace(
            stem,
            find=options.find,
            replace=options.replace,
            use_regex=options.use_regex,
            case_sensitive=options.case_sensitive,
        )
        ext = apply_find_replace(
            ext,
            find=options.find,
            replace=options.replace,
            use_regex=options.use_regex,
            case_sensitive=options.case_sensitive,
        )
    moment = datetime.fromtimestamp(item.mtime) if item.mtime else datetime.now()
    new_stem = apply_case(
        expand_mask(
            options.name_mask,
            stem=stem,
            ext=ext,
            counter=counter,
            digits=options.digits,
            moment=moment,
        ),
        options.case_mode,
    )
    ext_mask = options.ext_mask.strip()
    if item.is_dir or ext_mask == "-":
        new_ext = ""
    elif not ext_mask:
        new_ext = ext
    else:
        new_ext = expand_mask(
            options.ext_mask,
            stem=stem,
            ext=ext,
            counter=counter,
            digits=options.digits,
            moment=moment,
        ).lstrip(".")
    new_name = new_stem if not new_ext else f"{new_stem}.{new_ext}"
    if new_name == old_name:
        return RenamePreviewItem(item.path, old_name, new_name, "unchanged", "名称未变化")
    try:
        cleaned = validate_name(new_name)
    except ValueError as exc:
        return RenamePreviewItem(item.path, old_name, new_name, "invalid", str(exc))
    if len(cleaned) > _NAME_MAX_LENGTH:
        return RenamePreviewItem(
            item.path,
            old_name,
            cleaned,
            "invalid",
            f"名称过长（{len(cleaned)} > {_NAME_MAX_LENGTH} 字符）",
        )
    return RenamePreviewItem(item.path, old_name, cleaned, "ok")


def build_rename_preview(
    options: RenameOptions,
    inputs: Sequence[RenameInput],
) -> RenamePreview:
    """按排序后的位置计算新名称，并标记未变化 / 非法 / 冲突。"""
    try:
        _validate_options(options)
        ordered = order_inputs(inputs, options.sort_key)
    except MaskError as exc:
        return RenamePreview(error=str(exc))
    items: list[RenamePreviewItem] = []
    seen: dict[tuple[str, str], int] = {}
    for position, item in enumerate(ordered, start=1):
        counter = options.start + (position - 1) * options.step
        try:
            preview = _rename_one(item, options, counter)
        except MaskError as exc:
            return RenamePreview(error=str(exc))
        if preview.status == "ok":
            key = (os.path.normcase(str(preview.path.parent)), preview.new_name.casefold())
            if key in seen:
                preview = RenamePreviewItem(
                    preview.path,
                    preview.old_name,
                    preview.new_name,
                    "conflict",
                    f"与第 {seen[key]} 项目标同名",
                )
            else:
                seen[key] = position
                target = preview.path.with_name(preview.new_name)
                same_file = os.path.normcase(str(target)) == os.path.normcase(str(preview.path))
                if not same_file and (target.exists() or target.is_symlink()):
                    preview = RenamePreviewItem(
                        preview.path,
                        preview.old_name,
                        preview.new_name,
                        "conflict",
                        "目标已存在",
                    )
        items.append(preview)
    return RenamePreview(tuple(items))


def preview_to_plan(preview: RenamePreview, selected: Sequence[Path] | None = None) -> RenamePlan:
    """把预览中勾选且仍为 ``ok`` 的行转成可执行计划。"""
    wanted = None if selected is None else {os.path.normcase(str(path)) for path in selected}
    items = tuple(
        RenameItem(item.path, item.new_name, "ok")
        for item in preview.items
        if item.status == "ok" and (wanted is None or os.path.normcase(str(item.path)) in wanted)
    )
    return RenamePlan(items)


def _as_text(raw: Mapping[str, object], key: str, default: str) -> str:
    value = raw.get(key)
    return value if isinstance(value, str) else default


def _as_int(raw: Mapping[str, object], key: str, default: int) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def _as_bool(raw: Mapping[str, object], key: str, default: bool) -> bool:
    value = raw.get(key)
    return value if isinstance(value, bool) else default


def _options_from_mapping(raw: Mapping[str, object]) -> RenameOptions:
    defaults = RenameOptions()
    name_mask = _as_text(raw, "name_mask", defaults.name_mask).strip() or defaults.name_mask
    digits = _as_int(raw, "digits", defaults.digits)
    if not 1 <= digits <= 32:
        digits = defaults.digits
    case_mode = _as_text(raw, "case_mode", defaults.case_mode)
    if case_mode not in CASE_MODES:
        case_mode = defaults.case_mode
    sort_key = _as_text(raw, "sort_key", defaults.sort_key)
    if sort_key not in RENAME_SORT_KEYS:
        sort_key = defaults.sort_key
    return RenameOptions(
        name_mask=name_mask,
        ext_mask=_as_text(raw, "ext_mask", defaults.ext_mask),
        find=_as_text(raw, "find", defaults.find),
        replace=_as_text(raw, "replace", defaults.replace),
        use_regex=_as_bool(raw, "use_regex", defaults.use_regex),
        case_sensitive=_as_bool(raw, "case_sensitive", defaults.case_sensitive),
        only_extension=_as_bool(raw, "only_extension", defaults.only_extension),
        case_mode=case_mode,
        start=_as_int(raw, "start", defaults.start),
        step=_as_int(raw, "step", defaults.step),
        digits=digits,
        sort_key=sort_key,
    )


def normalize_rename_presets(raw: object) -> list[RenamePreset]:
    """解析持久化预设：丢弃非法条目与重名，保留首个，最多 50 条。"""
    if not isinstance(raw, list):
        return []
    presets: list[RenamePreset] = []
    seen: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        cleaned = name.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        presets.append(RenamePreset(cleaned, _options_from_mapping(entry)))
        if len(presets) >= RENAME_PRESET_LIMIT:
            break
    return presets


def rename_presets_to_json(presets: Sequence[RenamePreset]) -> list[dict[str, object]]:
    """序列化为设置文件中的扁平字典列表。"""
    rows: list[dict[str, object]] = []
    for preset in presets[:RENAME_PRESET_LIMIT]:
        row: dict[str, object] = {"name": preset.name}
        row.update(asdict(preset.options))
        rows.append(row)
    return rows
