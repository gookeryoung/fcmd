"""filesync - 文件夹比较与同步（CLI，参考 FreeFileSync）。

提供两个子命令：

- ``compare``：按「时间与大小 / 仅大小 / 内容」比较左右目录，列出仅左、
  仅右、不同与相同项，可选显示相同项。
- ``sync``：在比较结果上按「双向 / 镜像 / 更新」生成计划并执行；支持
  ``--preview`` 只预览不写盘，冲突项自动跳过并单独列出。

同步默认永久删除右侧多余文件（镜像模式）；双向模式无同步数据库，按修改
时间较新者覆盖，时间相同（2 秒容差）而内容不同记为冲突并跳过。同步粒度
为文件：空目录不参与扫描、创建或删除。

示例
----
    fcmd filesync compare D:/data E:/backup
    fcmd filesync compare D:/data E:/backup --compare content --show-equal
    fcmd filesync sync D:/data E:/backup --sync-mode mirror --preview
    fcmd filesync sync D:/data E:/backup --sync-mode update --include "*.py;*.md"
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import fcmd
from fcmd.apps.filesync._core import (
    CompareOptions,
    CompareResult,
    SyncPlan,
    build_sync_plan,
    compare_result_data,
    compare_trees,
    execute_plan,
    format_compare_report,
    format_scan_errors,
    format_size,
    format_sync_report,
    parse_filter_text,
)
from fcmd.console import print_error
from fcmd.core.result import emit_error, emit_result

__all__ = ["compare_command", "sync_command"]

__version__ = "0.1.0"

_DESCRIPTION = (
    "文件夹比较与同步（参考 FreeFileSync）：compare 按时间/大小/内容比较两侧，"
    "sync 支持双向/镜像/更新三种模式，可用 --preview 预览，冲突项自动跳过"
)


def _build_options(compare: str, include: str, exclude: str, skip_hidden: bool) -> CompareOptions:
    """把 CLI 参数转换为比较选项。"""
    return CompareOptions(
        mode=compare,
        include=parse_filter_text(include),
        exclude=parse_filter_text(exclude),
        skip_hidden=skip_hidden,
    )


def _emit_root_error(code: str, field: str, message: str, subcommand: str, json_output: bool) -> bool:
    """输出目录校验错误；``--json`` 时走错误信封，默认走既有错误文本。"""
    if json_output:
        if code == "DIR_NOT_FOUND":
            expected, hint = "已存在的目录", "确认目录路径存在"
        else:
            expected, hint = "与左侧不同的目录", "换一个不同的目录作为右侧"
        emit_error(
            "filesync",
            subcommand,
            code=code,
            message=message,
            field=field,
            expected=expected,
            hint=hint,
        )
    else:
        print_error(message)
    return False


def _validate_roots(left: Path, right: Path, subcommand: str = "compare", json_output: bool = False) -> bool:
    """校验左右目录存在且不是同一个目录；失败时输出错误并返回 ``False``。"""
    if not left.is_dir():
        return _emit_root_error("DIR_NOT_FOUND", "left", f"左侧目录不存在: {left}", subcommand, json_output)
    if not right.is_dir():
        return _emit_root_error("DIR_NOT_FOUND", "right", f"右侧目录不存在: {right}", subcommand, json_output)
    if left.resolve() == right.resolve():
        return _emit_root_error("SAME_DIRECTORY", "right", "左右目录不能相同", subcommand, json_output)
    return True


def _format_plan(plan: SyncPlan) -> str:
    """格式化同步计划：概要 + 逐项动作 + 冲突项。"""
    lines = [
        (
            f"计划：复制 {plan.copy_count} 项（{format_size(plan.bytes_to_copy)}），"
            f"删除 {plan.delete_count} 项，冲突 {len(plan.conflicts)} 项"
        )
    ]
    lines.extend(f"  [{action.action_text}] {action.relative}（{action.reason}）" for action in plan.actions)
    lines.extend(f"  [冲突跳过] {item.relative}（无法判断较新一侧）" for item in plan.conflicts)
    return "\n".join(lines)


@fcmd.tool(
    "filesync",
    subcommand="compare",
    help="比较两个文件夹并列出差异",
    description=_DESCRIPTION,
    param_help={
        "left": "左侧目录",
        "right": "右侧目录",
        "compare": "比较方式：time_size=时间与大小（默认）、size=仅大小、content=内容",
        "include": "包含通配符，分号分隔（如 *.py;*.md）",
        "exclude": "排除通配符，分号分隔，优先于包含规则",
        "skip_hidden": "跳过以 . 开头的隐藏文件与目录",
        "show_equal": "在结果中包含相同的文件",
        "json_output": "以 JSON 输出（--json 同义）",
    },
    examples=[
        "fcmd filesync compare D:/data E:/backup",
        "fcmd filesync compare D:/data E:/backup --compare content --show-equal",
        "fcmd filesync compare D:/data E:/backup --json",
    ],
)
def compare_command(  # noqa: PLR0913, PLR0917 - CLI 参数由 argparse 逐个生成，保持扁平签名
    left: Path,
    right: Path,
    compare: Literal["time_size", "size", "content"] = "time_size",
    include: str = "",
    exclude: str = "",
    skip_hidden: bool = False,
    show_equal: bool = False,
    json_output: bool = False,
) -> None:
    """比较左右两个目录并输出差异表。

    Parameters
    ----------
    left:
        左侧目录
    right:
        右侧目录
    compare:
        比较方式：time_size=时间与大小（默认）、size=仅大小、content=内容
    include:
        包含通配符，分号分隔（如 ``*.py;*.md``），留空表示全部
    exclude:
        排除通配符，分号分隔，优先于包含规则
    skip_hidden:
        跳过以 ``.`` 开头的隐藏文件与目录
    show_equal:
        在结果中包含相同的文件
    json_output:
        以 JSON 输出（``--json`` 同义）
    """
    if not _validate_roots(left, right, "compare", json_output):
        return
    result = compare_trees(left, right, _build_options(compare, include, exclude, skip_hidden))
    if json_output:
        emit_result("filesync", "compare", compare_result_data(result))
        return
    print(format_compare_report(result, show_equal=show_equal))


@fcmd.tool("filesync", subcommand="sync", help="按同步模式执行文件夹同步", description=_DESCRIPTION)
def sync_command(  # noqa: PLR0913, PLR0917 - CLI 参数由 argparse 逐个生成，保持扁平签名
    left: Path,
    right: Path,
    sync_mode: Literal["two_way", "mirror", "update"] = "two_way",
    compare: Literal["time_size", "size", "content"] = "time_size",
    include: str = "",
    exclude: str = "",
    skip_hidden: bool = False,
    preview: bool = False,
) -> None:
    """比较左右目录后按模式生成计划并执行同步。

    Parameters
    ----------
    left:
        左侧目录
    right:
        右侧目录
    sync_mode:
        同步模式：two_way=双向（默认）、mirror=以左侧为准并删除右侧多余文件、
        update=以左侧为准只复制新增与较新文件、从不删除
    compare:
        比较方式：time_size=时间与大小（默认）、size=仅大小、content=内容
    include:
        包含通配符，分号分隔（如 ``*.py;*.md``），留空表示全部
    exclude:
        排除通配符，分号分隔，优先于包含规则
    skip_hidden:
        跳过以 ``.`` 开头的隐藏文件与目录
    preview:
        只打印计划与统计，不修改文件系统
    """
    if not _validate_roots(left, right):
        return
    result = compare_trees(left, right, _build_options(compare, include, exclude, skip_hidden))
    plan = build_sync_plan(result, sync_mode)
    print(_format_plan(plan))
    if plan.actions:
        report = execute_plan(plan, dry_run=preview)
        print(format_sync_report(report))
    elif plan.conflicts:
        print("没有可执行动作（全部为冲突项）。")
    else:
        print("没有需要同步的项。")
    _print_scan_errors(result)


def _print_scan_errors(result: CompareResult) -> None:
    """扫描错误单独输出，便于区分「未比较」与「比较后无差异」。"""
    if result.errors:
        print_error(f"扫描错误 {len(result.errors)} 项：\n{format_scan_errors(result.errors)}")


@fcmd.main("filesync")
def main() -> None:
    pass  # pragma: no cover - @fcmd.main 装饰器替换函数体，pass 永不执行


if __name__ == "__main__":  # pragma: no cover
    main()
