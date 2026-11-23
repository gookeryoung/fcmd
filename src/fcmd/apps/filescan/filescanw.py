"""filescanw - 目录敏感内容扫描（Qt GUI）。

三个分页（沿用原 llmdownw / 现 llmw 模型页的分页口径，避免表单堆叠）：

- **扫描**：多目录列表、递归/深度/符号链接、后缀白名单表格（类型 / 后缀两列，
  每行一个类型，后缀用英文逗号分隔，可新增 / 编辑 / 删除类型（支持多选批量删除）
  并逐项校验）、
  后缀包含/排除、强制后缀、大小与修改时间上下限、跳过二进制、解析文档、
  单文件上限、并发数；开始 / 取消与进度条；导入 / 导出扫描配置。
- **结果**：结果按文件聚合（父节点为路径，子节点为规则 / 行号 / 打码上下文），
  可按规则与路径筛选，导出文本 / JSON / CSV（默认打码，勾选「导出原文」才输出
  敏感原文）。
- **规则**：规则表、新增 / 编辑 / 删除、启用 / 禁用、载入内置、清空、
  导入 / 导出规则集，以及 ``and`` / ``or`` / ``not`` 组合表达式输入与校验。

窗口设置（目录、选项、规则集、过滤条件、窗口位置）保存在
``${FCMD_HOME:-~/.fcmd}/ui/filescanw.json``，下次启动自动恢复。

扫描在后台线程执行，取消走 :class:`CancelToken`，关窗时置位取消并等待 worker
退出，不留下悬挂线程。

示例
----
    fcmd filescanw        # 打开敏感内容扫描窗口
    filescanw             # 全局命令等价入口
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import fcmd
from fcmd.apps.filescan._config import (
    REPORT_FORMATS,
)
from fcmd.apps.filescan._engine import (
    DEFAULT_ENCODINGS,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_SCAN_EXTENSIONS,
    ScanResult,
    normalize_exts,
)
from fcmd.apps.filescan._extract import DOCUMENT_EXTENSIONS
from fcmd.apps.filescan._report import (
    no_hit_hint,
    scan_summary,
)
from fcmd.apps.filescan._rules import (
    RuleSet,
    builtin_ruleset,
    parse_expression,
    ruleset_from_mapping,
    ruleset_to_mapping,
)
from fcmd.gui import config as _guiconfig
from fcmd.gui.settings import SettingsStore

__all__ = [
    "SETTINGS_NAME",
    "WHITELIST_GROUPS",
    "FilescanSettings",
    "apply_expression",
    "group_whitelist",
    "load_settings",
    "parse_and_validate_ext_line",
    "parse_whitelist_text",
    "run_gui",
    "save_settings",
    "scan_status_text",
    "settings_from_mapping",
    "settings_to_mapping",
    "validate_exts",
]

SETTINGS_NAME = "filescanw"
DEFAULT_GEOMETRY = "1120x760+140+70"
MIN_WIDTH = 960
MIN_HEIGHT = 620

_DESCRIPTION = "目录敏感内容扫描：规则表达式、并行扫描、按文件聚合、打码与多格式报告（Qt）"


# ---------------------------------------------------------------------- #
# 设置
# ---------------------------------------------------------------------- #
@dataclass
class FilescanSettings:
    """窗口与设置文件共用的配置。"""

    paths: tuple[str, ...] = ()
    recursive: bool = True
    max_depth: int = 0
    follow_symlinks: bool = False
    ext_whitelist: str = field(default_factory=lambda: ";".join(DEFAULT_SCAN_EXTENSIONS))
    whitelist_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    include_ext: str = ""
    force_ext: str = ""
    exclude_ext: str = ""
    min_size: int = 0
    max_size: int = 0
    modified_after: str = ""
    modified_before: str = ""
    skip_binary: bool = True
    scan_documents: bool = True
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    workers: int = 4
    context_chars: int = 40
    max_hits_per_file: int = 200
    use_processes: bool = True
    encodings: tuple[str, ...] = DEFAULT_ENCODINGS
    use_cache: bool = True
    ruleset: RuleSet = field(default_factory=builtin_ruleset)
    filter_rule: str = ""
    filter_path: str = ""
    report_format: str = "text"
    geometry: str | None = None


def settings_to_mapping(settings: FilescanSettings) -> dict[str, Any]:
    """把设置转成可写入 JSON 的字典（含完整规则集）。"""
    return {
        "paths": list(settings.paths),
        "recursive": settings.recursive,
        "max_depth": settings.max_depth,
        "follow_symlinks": settings.follow_symlinks,
        "ext_whitelist": settings.ext_whitelist,
        "whitelist_groups": [[label, list(exts)] for label, exts in settings.whitelist_groups],
        "include_ext": settings.include_ext,
        "force_ext": settings.force_ext,
        "exclude_ext": settings.exclude_ext,
        "min_size": settings.min_size,
        "max_size": settings.max_size,
        "modified_after": settings.modified_after,
        "modified_before": settings.modified_before,
        "skip_binary": settings.skip_binary,
        "scan_documents": settings.scan_documents,
        "max_file_bytes": settings.max_file_bytes,
        "workers": settings.workers,
        "context_chars": settings.context_chars,
        "max_hits_per_file": settings.max_hits_per_file,
        "use_processes": settings.use_processes,
        "encodings": list(settings.encodings),
        "use_cache": settings.use_cache,
        "ruleset": ruleset_to_mapping(settings.ruleset),
        "filter_rule": settings.filter_rule,
        "filter_path": settings.filter_path,
        "report_format": settings.report_format,
        "geometry": settings.geometry,
    }


def _bool_value(raw: Mapping[str, Any], key: str, default: bool) -> bool:
    """读取布尔字段；类型不符时回落默认值。"""
    value = raw.get(key, default)
    return value if isinstance(value, bool) else default


def _int_value(raw: Mapping[str, Any], key: str, default: int, *, minimum: int = 0) -> int:
    """读取整数字段；类型不符或越界时回落默认值。"""
    value = raw.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return max(minimum, value)


def _str_value(raw: Mapping[str, Any], key: str, default: str = "") -> str:
    """读取字符串字段；类型不符时回落默认值。"""
    value = raw.get(key, default)
    return value if isinstance(value, str) else default


def _format_value(raw: Mapping[str, Any]) -> str:
    """读取导出报告格式；非法取值回落 ``text``。"""
    value = raw.get("report_format", "text")
    return value if value in REPORT_FORMATS else "text"


def _encodings_value(raw: Mapping[str, Any]) -> tuple[str, ...]:
    """读取编码候选；非列表或全为空时回落默认值。"""
    stored = raw.get("encodings")
    if not isinstance(stored, list):
        return DEFAULT_ENCODINGS
    items = tuple(item for item in stored if isinstance(item, str) and item.strip())
    return items or DEFAULT_ENCODINGS


def _groups_value(raw: Mapping[str, Any]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """读取白名单「类型 → 后缀」分组；结构非法、重名或内容为空的条目逐项丢弃。"""
    stored = raw.get("whitelist_groups")
    if not isinstance(stored, list):
        return ()
    groups: list[tuple[str, tuple[str, ...]]] = []
    seen: set[str] = set()
    for item in stored:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        label, exts = item
        if not isinstance(label, str) or not isinstance(exts, (list, tuple)):
            continue
        name = label.strip()
        if not name or name in seen:
            continue
        valid, _ = validate_exts([ext for ext in exts if isinstance(ext, str)])
        if not valid:
            continue
        groups.append((name, valid))
        seen.add(name)
    return tuple(groups)


def settings_from_mapping(raw: Mapping[str, Any]) -> FilescanSettings:
    """从字典恢复设置；缺失或非法字段逐项回落默认值，规则集损坏时载入内置规则。"""
    paths_raw = raw.get("paths", [])
    paths = tuple(item for item in paths_raw if isinstance(item, str)) if isinstance(paths_raw, list) else ()
    ruleset = builtin_ruleset()
    stored = raw.get("ruleset")
    if isinstance(stored, Mapping):
        try:
            ruleset = ruleset_from_mapping(stored)
        except ValueError:
            ruleset = builtin_ruleset()
    return FilescanSettings(
        paths=paths,
        recursive=_bool_value(raw, "recursive", True),
        max_depth=_int_value(raw, "max_depth", 0),
        follow_symlinks=_bool_value(raw, "follow_symlinks", False),
        ext_whitelist=_str_value(raw, "ext_whitelist", ";".join(DEFAULT_SCAN_EXTENSIONS)),
        whitelist_groups=_groups_value(raw),
        include_ext=_str_value(raw, "include_ext"),
        force_ext=_str_value(raw, "force_ext"),
        exclude_ext=_str_value(raw, "exclude_ext"),
        min_size=_int_value(raw, "min_size", 0),
        max_size=_int_value(raw, "max_size", 0),
        modified_after=_str_value(raw, "modified_after"),
        modified_before=_str_value(raw, "modified_before"),
        skip_binary=_bool_value(raw, "skip_binary", True),
        scan_documents=_bool_value(raw, "scan_documents", True),
        max_file_bytes=_int_value(raw, "max_file_bytes", DEFAULT_MAX_FILE_BYTES, minimum=1),
        workers=_int_value(raw, "workers", 4, minimum=1),
        context_chars=_int_value(raw, "context_chars", 40),
        max_hits_per_file=_int_value(raw, "max_hits_per_file", 200, minimum=1),
        use_processes=_bool_value(raw, "use_processes", True),
        encodings=_encodings_value(raw),
        use_cache=_bool_value(raw, "use_cache", True),
        ruleset=ruleset,
        filter_rule=_str_value(raw, "filter_rule"),
        filter_path=_str_value(raw, "filter_path"),
        report_format=_format_value(raw),
        geometry=_guiconfig.valid_geometry(_str_value(raw, "geometry") or None),
    )


def config_path() -> Path:
    """返回设置文件路径（``${FCMD_HOME:-~/.fcmd}/ui/filescanw.json``）。"""
    return _guiconfig.config_path(SETTINGS_NAME)


def load_settings(path: Path | None = None) -> FilescanSettings:
    """读取设置；文件缺失或损坏时返回默认设置。"""
    raw = SettingsStore("filescanw", path).load()
    if raw is None:
        return FilescanSettings()
    return settings_from_mapping(raw)


def save_settings(settings: FilescanSettings, path: Path | None = None) -> None:
    """保存设置；父目录自动创建，写入失败时静默忽略。"""
    SettingsStore("filescanw", path).save(settings_to_mapping(settings))


def resolve_geometry(saved: str | None, screen_width: int, screen_height: int) -> str:
    """优先恢复记忆位置，越界或非法时回落到默认 geometry。"""
    from fcmd.gui import geometry as _geometry

    return _geometry.resolve_geometry(
        saved,
        screen_width,
        screen_height,
        default=DEFAULT_GEOMETRY,
        min_width=MIN_WIDTH,
        min_height=MIN_HEIGHT,
    )


def apply_expression(ruleset: RuleSet, text: str) -> RuleSet:
    """校验并应用表达式文本；非法时抛 ``ValueError``，空串表示恢复默认 OR。"""
    expression = text.strip()
    if expression:
        parse_expression(expression, ruleset.rule_ids())
    return replace(ruleset, expression=expression)


def _ext_group(text: str) -> tuple[str, ...]:
    """把空格分隔的后缀串转成元组（仅用于白名单分组定义）。"""
    return normalize_exts(text.split())


WHITELIST_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("文档", DOCUMENT_EXTENSIONS),
    ("文本/日志", _ext_group(".txt .text .md .markdown .rst .log")),
    (
        "数据/结构化",
        _ext_group(".csv .tsv .json .jsonl .xml .yaml .yml .toml .proto .graphql .gql .hcl .tf .tfvars"),
    ),
    ("配置", _ext_group(".ini .cfg .conf .config .env .properties .plist .reg .lock")),
    (
        "Web/前端",
        _ext_group(".html .htm .css .scss .sass .less .js .mjs .cjs .jsx .ts .tsx .vue .svelte"),
    ),
    (
        "编程语言",
        _ext_group(
            ".py .pyi .pyw .java .kt .kts .c .h .cc .cpp .cxx .hpp .hh .cs .go .rs .rb .php .pl .pm "
            ".lua .swift .m .mm .scala .dart .r .jl .ex .exs .erl .hrl .clj .cljs .hs .lisp .el .asm "
            ".s .sql .vbs .ahk"
        ),
    ),
    (
        "脚本/构建",
        _ext_group(".sh .bash .zsh .fish .ps1 .psm1 .psd1 .bat .cmd .gradle .groovy .cmake .mk"),
    ),
    ("出版/补丁", _ext_group(".tex .bib .diff .patch")),
)
"""白名单展示分组：仅用于界面排版，扫描仍按白名单整体匹配。"""

_WHITELIST_SPLIT = re.compile(r"[;,\s]+")


def parse_whitelist_text(text: str) -> tuple[str, ...]:
    """解析白名单编辑器文本：``#`` 开头为分组注释，其余按空白/分号/逗号切分。"""
    items: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        items.extend(_WHITELIST_SPLIT.split(stripped))
    return normalize_exts(items)


def group_whitelist(
    exts: Iterable[str] = DEFAULT_SCAN_EXTENSIONS,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """把后缀按类别归类成列表分组；未归类项落入「其他」，空分组不返回。"""
    remaining = list(dict.fromkeys(normalize_exts(tuple(exts))))
    groups: list[tuple[str, tuple[str, ...]]] = []
    for label, known in WHITELIST_GROUPS:
        present = tuple(item for item in known if item in remaining)
        if present:
            groups.append((label, present))
            remaining = [item for item in remaining if item not in present]
    if remaining:
        groups.append(("其他", tuple(remaining)))
    return tuple(groups)


_EXT_TOKEN = re.compile(r"\.?[a-z0-9][a-z0-9._+-]*")
_EXT_SEPARATORS = re.compile(r"[,，]")


def _is_valid_ext_token(token: str) -> bool:
    """单个后缀是否有效：``*``、``.log``、``log``、``*.log`` 合法，含空格/中文等非法。"""
    value = token.strip().lower().removeprefix("*")
    if token.strip() == "*":
        return True
    return bool(_EXT_TOKEN.fullmatch(value))


def validate_exts(exts: Iterable[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """校验后缀条目，返回 ``(有效后缀, 非法条目)``；有效项已规范化去重。"""
    valid: list[str] = []
    invalid: list[str] = []
    for raw in exts:
        token = str(raw).strip()
        if not token:
            continue
        if _is_valid_ext_token(token):
            valid.append(token)
        else:
            invalid.append(token)
    return normalize_exts(valid), tuple(invalid)


def parse_and_validate_ext_line(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """按英文/中文逗号拆分一行后缀并校验，返回 ``(有效后缀, 非法条目)``。"""
    return validate_exts(_EXT_SEPARATORS.split(text))


def scan_status_text(result: ScanResult) -> str:
    """扫描页状态栏文案：与 CLI 共用「范围外 / 读取后跳过」口径。

    0 命中时追加可操作诊断（白名单排除了多少文件、文档无文本层多少），
    避免用户只看到「全是跳过」却不知道原因。
    """
    summary = scan_summary(result.stats, compact=True)
    hint = no_hit_hint(result.stats)
    return f"{summary}；{hint}" if hint is not None else summary


# ---------------------------------------------------------------------- #
# 启动入口
# ---------------------------------------------------------------------- #
def run_gui(settings_path: Path | None = None) -> None:
    """创建并运行 Qt 主窗口。"""
    try:
        from fcmd.apps.filescan._filescanw_qt import run_qt
    except ImportError as exc:  # pragma: no cover - 缺少可选依赖
        raise SystemExit("未安装 Qt 前端：pip install 'PySide6-Essentials'") from exc
    run_qt(settings_path)


@fcmd.tool("filescanw", help="打开敏感内容扫描窗口（Qt）", description=_DESCRIPTION)
def launch_cmd() -> None:
    """打开敏感内容扫描窗口。"""
    run_gui()


@fcmd.main("filescanw")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
