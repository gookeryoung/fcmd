"""filescan 扫描配置：导入 / 导出与逐字段容错。

配置文件（JSON，``version`` 为 :data:`CONFIG_VERSION`）::

    {
      "version": 1,
      "roots": ["D:/data"],
      "rules": {"version": 1, "name": "…", "rules": [...], "expression": "…"},
      "options": {
        "recursive": true, "max_depth": 0, "follow_symlinks": false,
        "ext_whitelist": [".txt", ".log"], "include_exts": [], "force_exts": [],
        "exclude_exts": [], "min_size": 0, "max_size": null,
        "modified_after": "2026-01-01T00:00:00", "modified_before": null,
        "skip_binary": true, "scan_documents": true, "max_file_bytes": 8388608,
        "encodings": ["utf-8", "gb18030", "latin-1"],
        "workers": 4, "context_chars": 40, "max_hits_per_file": 200,
        "use_processes": true
      },
      "report": {"mask": true, "format": "text"}
    }

设计口径：

- **规则内嵌**：导出时把规则集整份写入 ``rules``，配置文件单文件即可复用，
  不依赖外部路径；手工编写时也可用 ``rules_path`` 引用外部规则集（相对配置
  文件所在目录），``rules`` 优先。取舍见 ADR 0019 的 D10。
- **逐字段容错**：每个字段独立校验，非法值回落默认值并记录一条中文提示，
  不因单个字段作废整份配置；只有「文件读不到 / 不是合法 JSON / 顶层不是对象」
  才整份失败。
- **向前兼容**：未知字段忽略，缺字段按默认值补齐；``version`` 高于本程序支持的
  版本时给出提示但仍尽力加载。

本模块只依赖标准库与同包的 ``_engine`` / ``_rules``，不导入 Tk。
"""

from __future__ import annotations

import codecs
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from fcmd.apps.filescan._engine import (
    DEFAULT_ENCODINGS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_SCAN_EXTENSIONS,
    ScanOptions,
    normalize_exts,
    parse_ext_text,
)
from fcmd.apps.filescan._rules import (
    RuleSet,
    builtin_ruleset,
    load_ruleset,
    parse_expression,
    ruleset_from_mapping,
    ruleset_to_mapping,
)

__all__ = [
    "CONFIG_VERSION",
    "REPORT_FORMATS",
    "ScanConfig",
    "config_from_mapping",
    "config_to_mapping",
    "dump_config",
    "format_timestamp",
    "load_config",
    "parse_timestamp",
    "save_config",
]

CONFIG_VERSION = 1
"""当前配置文件版本号。"""

REPORT_FORMATS: tuple[str, ...] = ("text", "json", "csv")
"""``report.format`` 的合法取值。"""

_MAX_SHOWN = 40
"""提示信息中展示非法值时的最大字符数。"""


@dataclass(frozen=True)
class ScanConfig:
    """一份可复用的扫描配置。

    ``ruleset`` 为 ``None`` 表示配置没有携带/未能解析规则集，调用方回落到内置
    规则；``mask`` 为真表示报告打码；``warnings`` 是加载时逐字段容错产生的
    中文提示（导出时不写回文件）。
    """

    roots: tuple[Path, ...] = ()
    options: ScanOptions = field(default_factory=ScanOptions)
    ruleset: RuleSet | None = None
    format: str = "text"
    mask: bool = True
    version: int = CONFIG_VERSION
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------- #
# 时间文本
# ---------------------------------------------------------------------- #
def parse_timestamp(value: Any) -> float | None:
    """把配置里的时间值解析成 epoch 秒。

    接受 ``null``、epoch 秒（数字或纯数字字符串）与 ISO-8601 文本
    （``2026-01-01`` / ``2026-01-01T09:30:00`` / 带时区偏移）；无时区的文本
    按本机时区解释。

    Raises
    ------
    ValueError
        类型不支持或文本无法解析（中文说明，可直接展示给用户）。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"时间过滤不能是布尔值（{_show(value)}）")
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        raise ValueError(f"时间过滤应为 ISO-8601 文本或 epoch 秒，实际为 {_show(value)}")
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(f"时间 {text!r} 不是合法的 ISO-8601 时间（例 2026-01-01 或 2026-01-01T09:30:00）") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return parsed.timestamp()


def format_timestamp(value: float | None) -> str | None:
    """把 epoch 秒格式化为本机时区的 ISO-8601 文本；``None`` 原样返回。"""
    if value is None:
        return None
    return datetime.fromtimestamp(value).isoformat(timespec="seconds")


# ---------------------------------------------------------------------- #
# 导出
# ---------------------------------------------------------------------- #
def config_to_mapping(config: ScanConfig) -> dict[str, Any]:
    """把配置转成可写入 JSON 的字典（规则集整份内嵌）。"""
    options = config.options
    payload: dict[str, Any] = {
        "version": CONFIG_VERSION,
        "roots": [str(root) for root in config.roots],
        "options": {
            "recursive": options.recursive,
            "max_depth": options.max_depth,
            "follow_symlinks": options.follow_symlinks,
            "ext_whitelist": list(options.ext_whitelist),
            "include_exts": list(options.include_exts),
            "force_exts": list(options.force_exts),
            "exclude_exts": list(options.exclude_exts),
            "min_size": options.min_size,
            "max_size": options.max_size,
            "modified_after": format_timestamp(options.modified_after),
            "modified_before": format_timestamp(options.modified_before),
            "skip_binary": options.skip_binary,
            "scan_documents": options.scan_documents,
            "max_file_bytes": options.max_file_bytes,
            "encodings": list(options.encodings),
            "workers": options.workers,
            "context_chars": options.context_chars,
            "max_hits_per_file": options.max_hits_per_file,
            "use_processes": options.use_processes,
        },
        "report": {"mask": config.mask, "format": config.format},
    }
    if config.ruleset is not None:
        payload["rules"] = ruleset_to_mapping(config.ruleset)
    return payload


def dump_config(config: ScanConfig) -> str:
    """序列化为 JSON 文本（含末尾换行）。"""
    return json.dumps(config_to_mapping(config), ensure_ascii=False, indent=2) + "\n"


def save_config(config: ScanConfig, path: Path) -> None:
    """把配置写入文件；父目录自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config(config), encoding="utf-8")


# ---------------------------------------------------------------------- #
# 加载
# ---------------------------------------------------------------------- #
def load_config(path: Path) -> ScanConfig:
    """读取配置文件。

    Raises
    ------
    ValueError
        文件读不到、不是合法 JSON 或顶层不是对象（中文说明含文件名）。
    """
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"无法读取配置 {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except ValueError as exc:
        raise ValueError(f"配置不是合法 JSON（{path}）: {exc}") from exc
    try:
        return config_from_mapping(raw, base_dir=path.parent)
    except ValueError as exc:
        raise ValueError(f"配置内容无效（{path}）: {exc}") from exc


def config_from_mapping(raw: Any, *, base_dir: Path | None = None) -> ScanConfig:
    """从字典解析配置；非法字段逐项回落默认值并记入 ``warnings``。

    ``base_dir`` 用于解析 ``rules_path`` 的相对路径（默认当前目录）。
    """
    if not isinstance(raw, Mapping):
        raise ValueError("配置必须是 JSON 对象")
    warnings: list[str] = []
    version = _version_value(raw, warnings)
    roots = _roots_value(raw, warnings)
    options = _options_value(raw.get("options"), warnings)
    ruleset = _rules_value(raw, base_dir, warnings)
    ruleset = _expression_value(ruleset, raw, warnings)
    report = _report_value(raw.get("report"), warnings)
    mask = _bool_field(report, "mask", True, "report.", warnings)
    fmt = _format_value(report, warnings)
    return ScanConfig(
        roots=roots,
        options=options,
        ruleset=ruleset,
        format=fmt,
        mask=mask,
        version=version,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------- #
# 逐字段读取（非法值 → 默认值 + 提示）
# ---------------------------------------------------------------------- #
def _show(value: Any) -> str:
    """把非法值渲染成简短文本，用于提示信息。"""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= _MAX_SHOWN else f"{text[: _MAX_SHOWN - 3]}..."


def _warn(warnings: list[str], path: str, message: str) -> None:
    """追加一条中文提示。"""
    warnings.append(f"{path} {message}")


def _version_value(raw: Mapping[str, Any], warnings: list[str]) -> int:
    """读取版本号；缺失按 1 处理，非整数或高于当前版本只提示不报错。"""
    if "version" not in raw:
        return CONFIG_VERSION
    value = raw["version"]
    if isinstance(value, bool) or not isinstance(value, int):
        _warn(warnings, "version", f"应为整数，实际为 {_show(value)}，已按版本 {CONFIG_VERSION} 处理")
        return CONFIG_VERSION
    if value > CONFIG_VERSION:
        _warn(
            warnings,
            "version",
            f"为 {value}，高于本程序支持的 {CONFIG_VERSION}；未知字段将忽略，缺失字段用默认值",
        )
    return value


def _roots_value(raw: Mapping[str, Any], warnings: list[str]) -> tuple[Path, ...]:
    """读取根目录列表；非列表整体回落为空，非字符串条目逐项丢弃。"""
    stored = raw.get("roots", [])
    if stored is None:
        return ()
    if not isinstance(stored, list):
        _warn(warnings, "roots", f"应为目录数组，实际为 {_show(stored)}，已忽略")
        return ()
    roots: list[Path] = []
    invalid = 0
    for item in stored:
        if isinstance(item, str) and item.strip():
            roots.append(Path(item.strip()))
        else:
            invalid += 1
    if invalid:
        _warn(warnings, "roots", f"有 {invalid} 项不是非空字符串，已忽略")
    return tuple(roots)


def _bool_field(raw: Mapping[str, Any], key: str, default: bool, prefix: str, warnings: list[str]) -> bool:
    """读取布尔字段；类型不符时回落默认值。"""
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool):
        return value
    _warn(warnings, f"{prefix}{key}", f"应为布尔值，实际为 {_show(value)}，已使用默认值 {str(default).lower()}")
    return default


def _int_field(  # noqa: PLR0913 - 字段读取器，参数与校验口径一一对应
    raw: Mapping[str, Any],
    key: str,
    default: int | None,
    prefix: str,
    warnings: list[str],
    *,
    minimum: int = 0,
    zero_none: bool = False,
) -> int | None:
    """读取整数字段；类型不符或低于下限时回落默认值，``0`` 可按需转成「不限」。"""
    if key not in raw:
        return default
    value = raw[key]
    if value is None:
        # ``null`` 对可空字段（如 max_size）是合法取值：表示不限。
        return None if zero_none else default
    if isinstance(value, bool) or not isinstance(value, int):
        _warn(warnings, f"{prefix}{key}", f"应为整数，实际为 {_show(value)}，已使用默认值 {_show(default)}")
        return default
    if value < minimum:
        _warn(
            warnings,
            f"{prefix}{key}",
            f"不能小于 {minimum}，实际为 {value}，已使用默认值 {_show(default)}",
        )
        return default
    if zero_none and value == 0:
        return None
    return value


def _ext_field(  # noqa: PLR0913 - 字段读取器，参数与校验口径一一对应
    raw: Mapping[str, Any],
    key: str,
    default: tuple[str, ...],
    prefix: str,
    warnings: list[str],
    *,
    required: bool,
) -> tuple[str, ...]:
    """读取后缀数组；也接受分号/逗号分隔的字符串（便于手写）。

    ``required`` 为真（白名单）时空结果回落默认值并提示，否则空列表合法。
    """
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, str):
        parsed = parse_ext_text(value)
        if parsed or not required:
            return parsed
        _warn(warnings, f"{prefix}{key}", "为空或没有有效后缀，已使用默认白名单")
        return default
    if not isinstance(value, list):
        _warn(
            warnings,
            f"{prefix}{key}",
            f"应为后缀数组，实际为 {_show(value)}，已使用默认值",
        )
        return default
    items = [item for item in value if isinstance(item, str) and item.strip()]
    invalid = len(value) - len(items)
    if invalid:
        _warn(warnings, f"{prefix}{key}", f"有 {invalid} 项不是非空字符串，已忽略")
    normalized = normalize_exts(items)
    if not normalized and required:
        _warn(warnings, f"{prefix}{key}", "为空或没有有效后缀，已使用默认白名单")
        return default
    return normalized


def _encodings_value(raw: Mapping[str, Any], key: str, prefix: str, warnings: list[str]) -> tuple[str, ...]:
    """读取编码候选；未知编码逐项丢弃，全部无效时回落默认候选。"""
    if key not in raw:
        return DEFAULT_ENCODINGS
    value = raw[key]
    if not isinstance(value, list):
        _warn(warnings, f"{prefix}{key}", f"应为编码数组，实际为 {_show(value)}，已使用默认值")
        return DEFAULT_ENCODINGS
    valid: list[str] = []
    invalid: list[str] = []
    for item in value:
        name = item.strip() if isinstance(item, str) else ""
        if not name:
            invalid.append(_show(item))
            continue
        try:
            codecs.lookup(name)
        except LookupError:
            invalid.append(name)
            continue
        if name not in valid:
            valid.append(name)
    if invalid:
        _warn(warnings, f"{prefix}{key}", f"未知或非字符串编码 {'、'.join(invalid)} 已忽略")
    return tuple(valid) or DEFAULT_ENCODINGS


def _time_field(raw: Mapping[str, Any], key: str, prefix: str, warnings: list[str]) -> float | None:
    """读取时间过滤；解析失败时忽略该条件并提示。"""
    if key not in raw:
        return None
    try:
        return parse_timestamp(raw[key])
    except ValueError as exc:
        _warn(warnings, f"{prefix}{key}", f"{exc}，已忽略该过滤条件")
        return None


def _options_value(raw: Any, warnings: list[str]) -> ScanOptions:
    """读取 ``options`` 对象；缺失或类型不符时整组用默认值并提示一次。"""
    prefix = "options."
    if raw is None:
        return ScanOptions()
    if not isinstance(raw, Mapping):
        _warn(warnings, "options", f"应为 JSON 对象，实际为 {_show(raw)}，已使用全部默认值")
        return ScanOptions()
    defaults = ScanOptions()
    modified_after = _time_field(raw, "modified_after", prefix, warnings)
    modified_before = _time_field(raw, "modified_before", prefix, warnings)
    if modified_after is not None and modified_before is not None and modified_after > modified_before:
        _warn(warnings, prefix + "modified_after", "晚于 modified_before，已忽略两端时间过滤")
        modified_after = modified_before = None
    return ScanOptions(
        recursive=_bool_field(raw, "recursive", defaults.recursive, prefix, warnings),
        max_depth=_int_field(raw, "max_depth", defaults.max_depth, prefix, warnings) or 0,
        follow_symlinks=_bool_field(raw, "follow_symlinks", defaults.follow_symlinks, prefix, warnings),
        ext_whitelist=_ext_field(
            raw,
            "ext_whitelist",
            DEFAULT_SCAN_EXTENSIONS,
            prefix,
            warnings,
            required=True,
        ),
        include_exts=_ext_field(raw, "include_exts", defaults.include_exts, prefix, warnings, required=False),
        force_exts=_ext_field(raw, "force_exts", defaults.force_exts, prefix, warnings, required=False),
        exclude_exts=_ext_field(raw, "exclude_exts", defaults.exclude_exts, prefix, warnings, required=False),
        min_size=_int_field(raw, "min_size", defaults.min_size, prefix, warnings) or 0,
        max_size=_int_field(raw, "max_size", defaults.max_size, prefix, warnings, zero_none=True),
        modified_after=modified_after,
        modified_before=modified_before,
        skip_binary=_bool_field(raw, "skip_binary", defaults.skip_binary, prefix, warnings),
        scan_documents=_bool_field(raw, "scan_documents", defaults.scan_documents, prefix, warnings),
        max_file_bytes=(
            _int_field(raw, "max_file_bytes", DEFAULT_MAX_FILE_BYTES, prefix, warnings, minimum=1)
            or DEFAULT_MAX_FILE_BYTES
        ),
        encodings=_encodings_value(raw, "encodings", prefix, warnings),
        workers=_int_field(raw, "workers", defaults.workers, prefix, warnings, minimum=1) or defaults.workers,
        context_chars=_int_field(raw, "context_chars", defaults.context_chars, prefix, warnings) or 0,
        max_hits_per_file=(
            _int_field(raw, "max_hits_per_file", defaults.max_hits_per_file, prefix, warnings, minimum=1)
            or defaults.max_hits_per_file
        ),
        use_processes=_bool_field(raw, "use_processes", defaults.use_processes, prefix, warnings),
    )


def _rules_value(raw: Mapping[str, Any], base_dir: Path | None, warnings: list[str]) -> RuleSet | None:
    """读取内嵌规则集或外部规则集引用；失败时提示并回落到 ``None``。"""
    inline = raw.get("rules")
    if inline is not None:
        try:
            return ruleset_from_mapping(inline)
        except ValueError as exc:
            _warn(warnings, "rules", f"规则集无效（{exc}），已忽略，将由调用方回落到默认规则")
            return None
    stored = raw.get("rules_path")
    if stored is None:
        return None
    if not isinstance(stored, str) or not stored.strip():
        _warn(warnings, "rules_path", f"应为非空字符串，实际为 {_show(stored)}，已忽略")
        return None
    path = Path(stored.strip()).expanduser()
    if not path.is_absolute():
        path = (base_dir or Path.cwd()) / path
    try:
        return load_ruleset(path)
    except ValueError as exc:
        _warn(warnings, "rules_path", f"无法加载（{exc}），已忽略，将由调用方回落到默认规则")
        return None


def _expression_value(ruleset: RuleSet | None, raw: Mapping[str, Any], warnings: list[str]) -> RuleSet | None:
    """用顶层 ``expression`` 覆盖规则集表达式；非法时保留原表达式并提示。"""
    if "expression" not in raw:
        return ruleset
    value = raw["expression"]
    if not isinstance(value, str):
        _warn(warnings, "expression", f"应为字符串，实际为 {_show(value)}，已忽略")
        return ruleset
    text = value.strip()
    if not text:
        return ruleset
    base = ruleset if ruleset is not None else builtin_ruleset()
    try:
        parse_expression(text, base.rule_ids())
    except ValueError as exc:
        _warn(warnings, "expression", f"无效（{exc}），已忽略")
        return ruleset
    return replace(base, expression=text)


def _report_value(raw: Any, warnings: list[str]) -> Mapping[str, Any]:
    """取出 ``report`` 对象；缺失或类型不符时回落空对象。"""
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        _warn(warnings, "report", f"应为 JSON 对象，实际为 {_show(raw)}，已使用默认值")
        return {}
    return raw


def _format_value(report: Mapping[str, Any], warnings: list[str]) -> str:
    """读取 ``report.format``；非法取值回落 ``text``。"""
    if "format" not in report:
        return "text"
    value = report["format"]
    if isinstance(value, str) and value in REPORT_FORMATS:
        return value
    _warn(warnings, "report.format", f"应为 {' / '.join(REPORT_FORMATS)}，实际为 {_show(value)}，已使用 text")
    return "text"
