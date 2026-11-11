"""renamer - 批量重命名（规则流水线，支持正则重排与文本搬移）。

规则用 JSON 数组描述，按顺序作用在名称主干（默认）或扩展名上：

- ``replace``：正则替换，``replacement`` 支持 ``\\1`` 反向引用。
- ``reorder``：正则匹配后用 ``{0}`` / ``{1}`` / ``{name}`` 重排捕获片段。
- ``move``：把匹配文本搬到 ``start`` / ``end`` / ``before`` / ``after`` /
  ``index``。
- ``insert``：在锚点位置插入 ``text``。
- ``case``：``lower`` / ``upper`` / ``title`` / ``sentence``。
- ``sequence``：按批内排序加计数器（``start`` / ``step`` / ``digits``）。

``--rules`` 可传内联 JSON，也可用 ``@规则文件.json``。``preview`` 只打印计划，
``apply`` 先打印计划再执行；执行采用两阶段改名，支持互换与仅大小写改名。

示例
----
    fcmd renamer preview ./photos --rules '[{"kind":"replace","pattern":"^IMG_","replacement":""}]'
    fcmd renamer preview ./photos --rules @rename.json --recursive
    fcmd renamer apply ./photos --rules @rename.json --recursive

``rename.json`` 示例::

    [
      {"kind": "reorder", "pattern": "^(\\\\d{4})-(.+)$", "replacement": "{2}-{1}"},
      {"kind": "move", "pattern": "\\\\[(.+?)\\\\]", "anchor": "end"},
      {"kind": "case", "mode": "sentence"}
    ]
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import fcmd
from fcmd.apps.renamer._core import (
    RenamePreview,
    RenameRule,
    RuleError,
    build_preview,
    collect_targets,
    execute_plan,
    format_preview,
    format_report,
    parse_rules,
    preview_to_plan,
)
from fcmd.console import print_error

__all__ = ["apply_command", "load_rules", "preview_command"]

__version__ = "0.1.0"

_DESCRIPTION = (
    "批量重命名（规则流水线）：replace 正则替换、reorder 捕获组重排、move 把匹配文本搬到开头/结尾/锚点、"
    "insert / case / sequence；规则用 JSON 或 @文件传入，preview 预览、apply 两阶段执行"
)


def load_rules(text: str) -> tuple[RenameRule, ...]:
    """解析 ``--rules``：内联 JSON，或以 ``@`` 开头的规则文件路径。"""
    if not text.strip():
        raise RuleError("未提供规则：请用 --rules 传入 JSON 或 @文件路径")
    source = text
    if text.startswith("@"):
        path = Path(text[1:])
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuleError(f"读取规则文件失败：{path}：{exc}") from exc
    try:
        raw = json.loads(source)
    except json.JSONDecodeError as exc:
        raise RuleError(f"规则 JSON 解析失败：{exc}") from exc
    return parse_rules(raw)


def _prepare(
    files: list[Path],
    rules_text: str,
    recursive: bool,
    dirs: bool,
    sort_key: str,
) -> RenamePreview | None:
    """加载规则并展开目标，生成预览；失败时打印错误并返回 ``None``。"""
    try:
        rules = load_rules(rules_text)
        targets = collect_targets(files, recursive=recursive, include_dirs=dirs)
    except (RuleError, FileNotFoundError, OSError) as exc:
        print_error(str(exc))
        return None
    preview = build_preview(targets, rules, sort_key)
    if preview.error:
        print_error(preview.error)
        return None
    return preview


@fcmd.tool("renamer", subcommand="preview", help="预览批量重命名结果", description=_DESCRIPTION)
def preview_command(
    files: list[Path],
    rules: str = "",
    recursive: bool = False,
    dirs: bool = False,
    sort_key: Literal["name", "modified", "created"] = "name",
) -> None:
    """打印批量重命名计划，不修改文件系统。

    Parameters
    ----------
    files:
        待改名的文件或目录列表
    rules:
        规则 JSON，或 ``@规则文件.json``
    recursive:
        目录是否递归展开子目录
    dirs:
        是否把目录本身也纳入改名
    sort_key:
        计数器排序方式：name=名称（默认）、modified=修改时间、created=创建时间
    """
    preview = _prepare(files, rules, recursive, dirs, sort_key)
    if preview is not None:
        print(format_preview(preview))


@fcmd.tool("renamer", subcommand="apply", help="执行批量重命名", description=_DESCRIPTION)
def apply_command(
    files: list[Path],
    rules: str = "",
    recursive: bool = False,
    dirs: bool = False,
    sort_key: Literal["name", "modified", "created"] = "name",
) -> None:
    """先打印计划，再执行可执行项；冲突与非法项自动跳过。

    Parameters
    ----------
    files:
        待改名的文件或目录列表
    rules:
        规则 JSON，或 ``@规则文件.json``
    recursive:
        目录是否递归展开子目录
    dirs:
        是否把目录本身也纳入改名
    sort_key:
        计数器排序方式：name=名称（默认）、modified=修改时间、created=创建时间
    """
    preview = _prepare(files, rules, recursive, dirs, sort_key)
    if preview is None:
        return
    print(format_preview(preview))
    plan = preview_to_plan(preview)
    if not plan.changes:
        print("没有可执行的改名。")
        return
    result = execute_plan(plan)
    print(format_report(result))
    if result.errors:
        print_error(f"有 {len(result.errors)} 项改名失败，详见上方清单。")


@fcmd.main("renamer")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
