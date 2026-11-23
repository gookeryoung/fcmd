"""参数解析：函数签名 / 类型注解 -> argparse parser。

只做 parser 构建与参数添加，不构建 schema、不执行任务。"""

from __future__ import annotations

import argparse
import inspect
import types
import typing
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .spec import ToolSpec


def _resolve_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """解析函数的类型注解（``from __future__ import annotations`` 的字符串注解求值）。

    项目要求 Python >= 3.10，``typing.get_type_hints`` 已能处理 PEP 604 / PEP 585
    泛型语法，不再需要 Python 3.8 时代的逐参数 ``eval`` 回退。
    """
    return typing.get_type_hints(func)


def _is_list_annotation(annotation: Any) -> bool:
    """判断注解是否为 ``list[X]`` / ``List[X]`` 类型。"""
    return typing.get_origin(annotation) is list


def _list_inner_type(annotation: Any) -> Any:
    """提取 ``list[X]`` 的内部类型 X，无法提取时返回 ``None``。"""
    args = typing.get_args(annotation)
    return args[0] if args else None


def _unwrap_optional(annotation: Any) -> Any:
    """从 ``X | None`` / ``Optional[X]`` 注解中提取非 None 类型 X。

    非 Optional 注解原样返回。
    """
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _is_literal_annotation(annotation: Any) -> bool:
    """判断注解是否为 ``Literal[X, Y, ...]`` 类型。"""
    origin = getattr(annotation, "__origin__", None)
    return origin is typing.Literal


def _literal_choices(annotation: Any) -> tuple[Any, ...]:
    """提取 ``Literal[X, Y, ...]`` 的选项值元组。"""
    return getattr(annotation, "__args__", ())


_PARAM_CLI_ALIASES: Mapping[str, tuple[str, ...]] = {"json_output": ("--json",), "from_path": ("--from",)}


def _add_optional_arg(  # noqa: PLR0913, PLR0917 - argparse 参数逐个生成，保持扁平签名
    parser: argparse.ArgumentParser,
    pname: str,
    annotation: Any,
    default: Any,
    help_text: str | None = None,
    aliases: Sequence[str] = (),
) -> None:
    """添加 --name 选项（有默认值的参数）。

    支持的注解类型：
    - ``bool``（默认 ``False``）→ ``--name`` store_true 启用
    - ``bool``（默认 ``True``）→ ``--no-name`` store_false 关闭
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``X | None`` / ``Optional[X]`` → 自动解包为 ``X``
    - ``Literal[X, Y, ...]`` → ``choices``（argparse 自动校验取值）
    - ``list[X]`` / ``List[X]`` → ``nargs="*"`` + 对应 ``type``

    ``help_text`` 为 DSL 声明式命令的参数帮助，``None`` 时退化为参数名；
    ``aliases`` 为同一参数的可选写法（如 ``--json``），由
    :data:`_PARAM_CLI_ALIASES` 按参数名约定给出。
    """
    annotation = _unwrap_optional(annotation)
    if annotation is bool or (isinstance(default, bool) and default is True):
        if isinstance(default, bool) and default is True:
            cli_name = f"--no-{pname.replace('_', '-')}"
            # dest=pname 保留原参数名：argparse 默认会把 --no-keep-ratio 映射到
            # no_keep_ratio 属性，导致函数调用时找不到 keep_ratio 形参。
            parser.add_argument(
                cli_name,
                *aliases,
                dest=pname,
                action="store_false",
                default=True,
                help=f"关闭 {help_text or pname}",
            )
        else:
            cli_name = f"--{pname.replace('_', '-')}"
            parser.add_argument(cli_name, *aliases, action="store_true", default=False, help=help_text or pname)
        return
    cli_name = f"--{pname.replace('_', '-')}"
    kwargs: dict[str, Any] = {"default": default, "help": help_text or pname}
    if annotation in (int, float, str):
        kwargs["type"] = annotation
    elif annotation is Path:
        kwargs["type"] = Path
    elif _is_literal_annotation(annotation):
        # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
        kwargs["choices"] = list(_literal_choices(annotation))
    elif _is_list_annotation(annotation):
        kwargs["nargs"] = "*"
        inner_type = _resolve_list_inner_type(_list_inner_type(annotation))
        if inner_type is not None:
            kwargs["type"] = inner_type
    parser.add_argument(cli_name, *aliases, **kwargs)


def _resolve_list_inner_type(inner: Any) -> type | None:
    """将 list 注解的内部类型映射为 argparse ``type``。"""
    if inner is Path:
        return Path
    if inner in (int, float, str):
        return inner
    return None


def _add_positional_arg(
    parser: argparse.ArgumentParser,
    pname: str,
    annotation: Any,
    help_text: str | None = None,
) -> None:
    """添加 positional 参数（无默认值的参数）。

    支持的注解类型：
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``X | None`` / ``Optional[X]`` → 自动解包为 ``X``
    - ``Literal[X, Y, ...]`` → ``choices``
    - ``list[X]`` / ``List[X]`` → ``nargs="+"`` + 对应 ``type``

    ``help_text`` 为 DSL 声明式命令的参数帮助，``None`` 时退化为参数名。
    """
    annotation = _unwrap_optional(annotation)
    if _is_list_annotation(annotation):
        kwargs: dict[str, Any] = {"nargs": "+", "help": help_text or pname}
        inner_type = _resolve_list_inner_type(_list_inner_type(annotation))
        if inner_type is not None:
            kwargs["type"] = inner_type
        parser.add_argument(pname, **kwargs)
    elif annotation in (int, float, str):
        parser.add_argument(pname, type=annotation, help=help_text or pname)
    elif annotation is Path:
        parser.add_argument(pname, type=Path, help=help_text or pname)
    elif _is_literal_annotation(annotation):
        # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
        kwargs = {"help": help_text or pname, "choices": list(_literal_choices(annotation))}
        parser.add_argument(pname, **kwargs)
    else:
        parser.add_argument(pname, help=help_text or pname)


def _build_parser_for_tool(spec: ToolSpec) -> argparse.ArgumentParser:
    """为单个 ToolSpec 构建 argparse parser。

    函数签名映射规则：
    - 有默认值 → ``--name`` 选项
    - 无默认值 → positional 参数
    - ``bool`` 且默认 ``False`` → ``--name`` store_true 启用
    - ``bool`` 且默认 ``True`` → ``--no-name`` store_false 关闭
    - ``X | None`` / ``Optional[X]`` → 自动解包为 ``X``
    - ``list[X]`` / ``List[X]`` → ``nargs="+"`` (positional) 或 ``nargs="*"`` (optional)
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``Literal[X, Y, ...]`` → ``choices``（argparse 自动校验取值）
    """
    hints = _resolve_hints(spec.func)
    sig = inspect.signature(spec.func)
    prog = spec.name if spec.subcommand is None else f"{spec.name} {spec.subcommand}"
    description = spec.help or inspect.getdoc(spec.func) or ""
    param_help = _extract_param_help(spec.func)
    if spec.param_help:
        param_help.update(spec.param_help)
    epilog = None
    if spec.examples:
        # 示例按行原样展示，不参与 argparse 的换行折叠
        epilog = "示例:\n" + "\n".join(f"  {example}" for example in spec.examples)
    parser = argparse.ArgumentParser(
        prog=prog,
        description=description,
        epilog=epilog,
        formatter_class=argparse.RawDescriptionHelpFormatter if epilog else argparse.HelpFormatter,
    )
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, param.annotation)
        if param.default is inspect.Parameter.empty:
            _add_positional_arg(parser, pname, annotation, help_text=param_help.get(pname))
        else:
            _add_optional_arg(
                parser,
                pname,
                annotation,
                param.default,
                help_text=param_help.get(pname),
                aliases=_PARAM_CLI_ALIASES.get(pname, ()),
            )
    # 工具自己声明 dry_run 时由工具生成结构化计划，不再添加框架级 --dry-run
    _add_global_options(
        parser,
        include_dry_run="dry_run" not in sig.parameters,
        include_confirm=spec.requires_confirmation and "confirm" not in sig.parameters,
    )
    return parser


def _add_global_options(
    parser: argparse.ArgumentParser, *, include_dry_run: bool = True, include_confirm: bool = False
) -> None:
    """为 parser 添加全局选项（``--dry-run`` / ``-q`` / ``--strategy``，按需 ``--confirm``）。"""
    if include_dry_run:
        parser.add_argument("--dry-run", action="store_true", default=False, help="仅打印执行计划，不执行")
    if include_confirm:
        parser.add_argument("--confirm", action="store_true", default=False, help="确认执行会修改文件/系统状态的操作")
    parser.add_argument("-q", "--quiet", action="store_true", default=False, help="减少输出")
    parser.add_argument(
        "--strategy",
        default=None,
        help="执行策略 (sequential/thread/async/dependency)",
    )


_PARAM_SECTIONS = frozenset({"parameters", "参数"})


def _extract_param_help(func: Callable[..., Any]) -> dict[str, str]:
    """从 NumPy 风格 docstring 的 ``Parameters`` / ``参数`` 小节提取参数帮助。

    条目形如 ``name:`` 后跟缩进说明，说明可跨行（拼接为一行）。遇到
    ``Returns`` / ``Raises`` / ``示例`` 等其它小节即停止提取，避免误吞正文。
    RST 行内标记（`` ``x`` ``）会被去掉，便于在 ``--help`` 中直接阅读。
    """
    lines = (inspect.getdoc(func) or "").splitlines()
    help_map: dict[str, str] = {}
    names: list[str] = []
    buffer: list[str] = []
    in_section = False

    def flush() -> None:
        if names:
            text = "".join(buffer).strip().replace("``", "")
            if text:
                for name in names:
                    help_map[name] = text
        names.clear()
        buffer.clear()

    for index, raw in enumerate(lines):
        text = raw.strip()
        underline = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if text and len(underline) >= 3 and set(underline) == {"-"}:
            flush()
            in_section = text.lower() in _PARAM_SECTIONS
            continue
        if not in_section or not text or set(text) == {"-"}:
            continue
        if raw[:1] in (" ", "\t"):
            buffer.append(text)
            continue
        flush()
        name_part, sep, description = text.partition(":")
        if not sep:
            buffer.append(text)
            continue
        names = [item.strip() for item in name_part.split(",") if item.strip()]
        buffer.append(description.strip())
    flush()
    return help_map
