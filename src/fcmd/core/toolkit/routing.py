"""argv 路由与参数解析：确定 target、构建 parser、返回变量字典。"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import io
from collections.abc import Sequence
from typing import Any

from ..exit_codes import ExitCode
from ..result import emit_error
from .parser import _add_global_options, _build_parser_for_tool
from .plan import _is_aggregate
from .present import _print_subcommands
from .spec import ToolSpec


def _json_requested(argv: Sequence[str]) -> bool:
    """判断 argv 是否显式请求机读输出。

    逐 token 精确匹配 ``--json``（不匹配 ``--json-output`` 的前缀），遇到
    ``--`` 即停止——其后的内容属于位置参数，不作为选项解释。
    """
    for token in argv:
        if token == "--":
            return False
        if token == "--json":
            return True
    return False


def _argparse_error_detail(captured: str) -> str:
    """从 argparse 写入 stderr 的文本中取出最后一条 ``error:`` 说明。"""
    for line in reversed(captured.splitlines()):
        _, separator, detail = line.partition("error: ")
        if separator:
            return detail.strip()
    return "参数解析失败"


def _resolve_tool_target(
    name: str, subs: dict[str | None, ToolSpec], argv: Sequence[str]
) -> tuple[str | None, list[str]] | int:
    """确定工具子命令 ``target`` 与剩余参数 ``argv_rest``。

    纯单命令工具（仅有 None 子命令）透传全部 argv；否则取 argv[0] 为 target
    （非 ``-`` 开头时），或回退到 None 子命令；无匹配时列出子命令。
    """
    # 纯单命令工具（仅有 None 子命令）：target=None，全部 argv 透传给 parser
    if None in subs and len(subs) == 1:
        return None, list(argv)
    if argv and not argv[0].startswith("-"):
        return argv[0], list(argv[1:])
    if None in subs:
        return None, list(argv)
    # 显式请求工具级帮助（fcmd <tool> --help）：打印子命令表并正常退出（0）
    if argv and argv[0] in ("-h", "--help"):
        _print_subcommands(name, subs)
        return int(ExitCode.SUCCESS)
    # 列出工具的所有子命令：缺少子命令属于用法错误（退出码 2）
    from fcmd.console import print_error

    if _json_requested(argv):
        emit_error(
            name,
            None,
            code="USAGE",
            message=f"工具 {name!r} 需要指定子命令",
            hint=f"运行 fcmd {name} --help 查看可用子命令",
        )
        return int(ExitCode.USAGE)
    print_error(
        f"工具 {name!r} 需要指定子命令",
        code="USAGE",
        hint=f"运行 fcmd {name} --help，或从下面的子命令表中选择",
    )
    _print_subcommands(name, subs)
    return int(ExitCode.USAGE)


def _parse_arguments(
    parser: argparse.ArgumentParser,
    name: str,
    target: str | None,
    argv_rest: Sequence[str],
    usage: str,
) -> dict[str, Any] | int:
    """解析参数；用法错误按模式输出文本或信封，并返回退出码。"""
    if _json_requested(argv_rest):
        # argparse 失败会向 stderr 打英文 usage + error；机读模式改由信封承载，
        # 避免 stderr 混入非 JSON 文本。
        captured = io.StringIO()
        try:
            with contextlib.redirect_stderr(captured):
                parsed = parser.parse_args(argv_rest)
        except SystemExit as e:
            if e.code == 0:
                return int(ExitCode.SUCCESS)
            emit_error(
                name,
                target,
                code="USAGE",
                message=f"参数解析失败: {_argparse_error_detail(captured.getvalue())}",
                hint=f"运行 {usage} 查看参数用法",
            )
            return int(ExitCode.USAGE)
    else:
        try:
            parsed = parser.parse_args(argv_rest)
        except SystemExit as e:
            # argparse 解析失败（unrecognized args / 类型错误）按用法错误处理；
            # --help 正常退出仍为 0。argparse 自己已打印具体原因（英文），这里补
            # 一句中文修正提示，避免非零退出只剩一段英文用法文本。
            if e.code == 0:
                return int(ExitCode.SUCCESS)
            from fcmd.console import get_console

            get_console().print(f"[yellow]提示:[/yellow] 运行 {usage} 查看参数用法")
            return int(ExitCode.USAGE)
    return {k: v for k, v in vars(parsed).items() if v is not None}


def _parse_tool_args(
    name: str, target: str | None, argv_rest: list[str], subs: dict[str | None, ToolSpec]
) -> tuple[dict[str, Any], ToolSpec] | int:
    """校验 target、构建 parser 解析 ``argv_rest``，返回变量字典与 ``target_spec``。"""
    json_output = _json_requested(argv_rest)
    if target is not None and target not in subs:
        from fcmd.console import print_error

        hint = f"运行 fcmd {name} --help 查看可用子命令"
        if json_output:
            emit_error(
                name, target, code="UNKNOWN_SUBCOMMAND", message=f"工具 {name!r} 没有子命令 {target!r}", hint=hint
            )
            return int(ExitCode.USAGE)
        print_error(f"工具 {name!r} 没有子命令 {target!r}", code="UNKNOWN_SUBCOMMAND", hint=hint)
        _print_subcommands(name, subs)
        return int(ExitCode.USAGE)

    target_spec = subs[target]

    # 聚合任务：无参签名保持裸 parser（仅全局选项，既有行为）；带参签名
    # 复用完整 parser，参数经共享 variables 流入子任务插值
    if _is_aggregate(target_spec) and not inspect.signature(target_spec.func).parameters:
        parser = argparse.ArgumentParser(prog=f"{name} {target}", description=target_spec.help)
        _add_global_options(parser)
    else:
        parser = _build_parser_for_tool(target_spec)

    usage = f"fcmd {name} {target} --help" if target is not None else f"fcmd {name} --help"
    variables = _parse_arguments(parser, name, target, argv_rest, usage)
    if isinstance(variables, int):
        return variables
    return variables, target_spec
