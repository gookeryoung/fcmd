"""工具参数解析：函数签名 → argparse parser。

承载 ``@fx.tool`` 框架中**参数解析**的关注点：

* :class:`ToolExitCode` —— 工具执行退出码枚举。
* :class:`ToolSpec` —— 工具描述符（函数 + CLI 元数据 + DAG 编排参数）。
* 类型注解解析（:func:`_resolve_hints` / :func:`_is_list_annotation` /
  :func:`_list_inner_type` / :func:`_unwrap_optional` /
  :func:`_annotation_str_to_type` / :func:`_is_literal_annotation` /
  :func:`_literal_choices`）—— 处理 ``from __future__ import annotations``
  的字符串注解、PEP 604 ``X | None``、PEP 585 ``list[X]``、``Literal`` 等。
* 参数添加（:func:`_add_optional_arg` / :func:`_add_positional_arg` /
  :func:`_resolve_list_inner_type`）—— 按注解类型映射为 argparse 参数。
* parser 构建（:func:`_build_parser_for_tool` / :func:`_add_global_options`）——
  从 :class:`ToolSpec` 的函数签名构建完整 argparse parser。

本模块不依赖 :mod:`fcmd.apis.toolkit` 的注册表/执行/输出，由后者 import 复用。
"""

from __future__ import annotations

import argparse
import enum
import inspect
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .task import RetryPolicy


class ToolExitCode(enum.IntEnum):
    """工具执行退出码。"""

    SUCCESS = 0
    FAILURE = 1
    INTERRUPTED = 130  # 与 POSIX 信号中断一致


def _noop() -> None:
    """聚合任务的占位函数。"""


# ---------------------------------------------------------------------- #
# ToolSpec: 工具描述符
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class ToolSpec:
    """工具描述符：由 ``@fx.tool`` 装饰器注册。

    封装函数 + CLI 元数据 + DAG 编排参数，运行时映射为 :class:`TaskSpec`。

    参数
    ----
    name:
        工具名（如 ``"pymake"``）；多个 ``@fx.tool`` 共用同名即多 subcommand 工具
    subcommand:
        子命令名；``None`` 表示单命令工具（整个工具仅一个函数）
    func:
        被装饰的函数（签名驱动 CLI，函数体即逻辑）
    help:
        子命令帮助文本；默认用函数 docstring
    description:
        工具描述，用于 fcmd 工具列表
    cmd:
        命令列表或 shell 字符串；有 ``cmd`` 执行命令，函数体不执行（签名仅驱动 CLI）
    needs:
        依赖任务名（引用同 tool 的其他 subcommand）
    strategy:
        执行策略：``"sequential"`` / ``"thread"`` / ``"async"`` / ``"dependency"``
    cwd:
        工作目录（cmd 任务装饰器级默认）；若函数签名有 ``cwd`` 参数则被 CLI 值覆盖
    allow_upstream_skip:
        上游 SKIPPED 时本任务仍执行
    hidden:
        不暴露为 subcommand（内部 job，仅被 needs 引用）
    env / retry / timeout:
        透传 :class:`TaskSpec` 对应字段
    """

    name: str
    subcommand: str | None
    func: Callable[..., Any]
    help: str = ""
    description: str = ""
    cmd: tuple[str, ...] | str | None = None
    needs: tuple[str, ...] = ()
    strategy: Literal["sequential", "thread", "async", "dependency"] | None = None
    cwd: str | Path | None = None
    allow_upstream_skip: bool = False
    hidden: bool = False
    env: Mapping[str, str] | None = None
    retry: RetryPolicy | None = None
    timeout: float | None = None


# ---------------------------------------------------------------------- #
# argparse parser 构建（函数签名 → argparse 参数）
# ---------------------------------------------------------------------- #
def _resolve_hints(func: Callable[..., Any]) -> dict[str, Any]:
    """解析函数的类型注解（处理 from __future__ import annotations 的字符串注解）。

    Python 3.8 下 ``typing.get_type_hints`` 对 PEP 604 (``X | Y``) 和
    PEP 585 (``list[X]``) 泛型语法会抛 :class:`TypeError`，导致整个函数的
    注解解析失败。本函数在 ``get_type_hints`` 失败时逐参数回退：用 ``eval``
    求值字符串注解（类型注解是开发者代码，非用户输入，安全），单个参数失败
    不影响其他参数，失败参数保留字符串形式供下游处理。
    """
    try:
        return typing.get_type_hints(func)
    except Exception:
        # get_type_hints 整体失败（如返回类型用了 X|Y），逐参数 eval 回退
        sig = inspect.signature(func)
        globalns = getattr(func, "__globals__", {})
        hints: dict[str, Any] = {}
        for pname, param in sig.parameters.items():
            if param.annotation is inspect.Parameter.empty:
                continue
            if not isinstance(param.annotation, str):
                hints[pname] = param.annotation
                continue
            try:
                # 类型注解求值是 typing.get_type_hints 的标准做法，非用户输入
                hints[pname] = eval(param.annotation, globalns)
            except Exception:
                # eval 失败（如 list[X] 在 3.8），保留字符串供下游处理
                hints[pname] = param.annotation
        return hints


def _is_list_annotation(annotation: Any) -> bool:
    """判断注解是否为 list[X] / List[X] 类型。"""
    origin = getattr(annotation, "__origin__", None)
    if origin is list:
        return True
    ann_str = str(annotation)
    return ann_str.startswith("list[") or ann_str.startswith("List[")


def _list_inner_type(annotation: Any) -> Any:
    """提取 list[X] 的内部类型 X，无法提取时返回 None。"""
    args = getattr(annotation, "__args__", None)
    if args:
        return args[0]
    ann_str = str(annotation)
    for prefix in ("list[", "List["):
        if ann_str.startswith(prefix) and ann_str.endswith("]"):
            return ann_str[len(prefix) : -1].strip()
    return None


def _unwrap_optional(annotation: Any) -> Any:
    """从 ``X | None`` / ``Optional[X]`` 注解中提取非 None 类型 X。

    Python 3.8 下 ``int | None`` 字符串注解无法被 ``eval`` 求值（PEP 604 需 3.10+），
    本函数同时处理实际 typing 对象（``typing.Union[X, None]``）和字符串形式
    （``"int | None"`` / ``"Optional[int]"``），提取其中的非 None 类型。

    非 Optional 注解原样返回。
    """
    # 实际 typing 对象：typing.Union[X, None] 或 types.UnionType (3.10+)
    origin = typing.get_origin(annotation)
    # Python 3.10+ 的 X | None 是 types.UnionType（origin 为 types.UnionType）
    # Python 3.8/3.9 的 Optional[X] 是 typing.Union（origin 为 typing.Union）
    # 注意：origin 对字符串/普通类型返回 None，必须用 `is not None` 守卫，
    # 否则 Python 3.8 下 `origin is getattr(types, "UnionType", None)` 退化为
    # `None is None` → True，字符串注解会被误判为 Union 并原样返回。
    union_type = getattr(types, "UnionType", None)
    if origin is typing.Union or (union_type is not None and origin is union_type):
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return annotation
    # 字符串注解
    if isinstance(annotation, str):
        ann = annotation.strip()
        # "Optional[X]"
        if ann.startswith("Optional[") and ann.endswith("]"):
            inner = ann[len("Optional[") : -1].strip()
            return _annotation_str_to_type(inner)
        # "X | None" / "None | X" / "X | None | Y" (多参数不处理)
        if "|" in ann:
            parts = [p.strip() for p in ann.split("|")]
            non_none = [p for p in parts if p != "None"]
            if len(non_none) == 1:
                return _annotation_str_to_type(non_none[0])
    return annotation


def _annotation_str_to_type(ann_str: str) -> Any:
    """将基本类型名字符串映射到实际类型对象，未识别的返回原字符串。"""
    type_map = {"int": int, "float": float, "str": str, "bool": bool, "Path": Path, "pathlib.Path": Path}
    return type_map.get(ann_str, ann_str)


def _is_literal_annotation(annotation: Any) -> bool:
    """判断注解是否为 ``Literal[X, Y, ...]`` 类型。"""
    origin = getattr(annotation, "__origin__", None)
    return origin is typing.Literal


def _literal_choices(annotation: Any) -> tuple[Any, ...]:
    """提取 ``Literal[X, Y, ...]`` 的选项值元组。"""
    return getattr(annotation, "__args__", ())


def _add_optional_arg(
    parser: argparse.ArgumentParser,
    pname: str,
    annotation: Any,
    default: Any,
) -> None:
    """添加 --name 选项（有默认值的参数）。

    支持的注解类型：
    - ``bool``（默认 ``False``）→ ``--name`` store_true 启用
    - ``bool``（默认 ``True``）→ ``--no-name`` store_false 关闭
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``X | None`` / ``Optional[X]`` → 自动解包为 ``X``
    - ``Literal[X, Y, ...]`` → ``choices``（argparse 自动校验取值）
    - ``list[X]`` / ``List[X]`` → ``nargs="*"`` + 对应 ``type``
    """
    annotation = _unwrap_optional(annotation)
    if annotation is bool or (isinstance(default, bool) and default is True):
        if isinstance(default, bool) and default is True:
            cli_name = f"--no-{pname.replace('_', '-')}"
            # dest=pname 保留原参数名：argparse 默认会把 --no-keep-ratio 映射到
            # no_keep_ratio 属性，导致函数调用时找不到 keep_ratio 形参。
            parser.add_argument(cli_name, dest=pname, action="store_false", default=True, help=f"关闭 {pname}")
        else:
            cli_name = f"--{pname.replace('_', '-')}"
            parser.add_argument(cli_name, action="store_true", default=False, help=pname)
        return
    cli_name = f"--{pname.replace('_', '-')}"
    kwargs: dict[str, Any] = {"default": default, "help": pname}
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
    parser.add_argument(cli_name, **kwargs)


def _resolve_list_inner_type(inner: Any) -> type | None:
    """将 list 注解的内部类型（类型对象或字符串注解）映射为 argparse ``type``。"""
    if inner in (Path, "Path", "pathlib.Path"):
        return Path
    if inner in (int, "int"):
        return int
    if inner in (float, "float"):
        return float
    if inner in (str, "str"):
        return str
    return None


def _add_positional_arg(
    parser: argparse.ArgumentParser,
    pname: str,
    annotation: Any,
) -> None:
    """添加 positional 参数（无默认值的参数）。

    支持的注解类型：
    - ``int`` / ``float`` / ``str`` / ``Path`` → 对应 ``type``
    - ``X | None`` / ``Optional[X]`` → 自动解包为 ``X``
    - ``Literal[X, Y, ...]`` → ``choices``
    - ``list[X]`` / ``List[X]`` → ``nargs="+"`` + 对应 ``type``
    """
    annotation = _unwrap_optional(annotation)
    if _is_list_annotation(annotation):
        inner = _list_inner_type(annotation)
        kwargs: dict[str, Any] = {"nargs": "+", "help": pname}
        if inner in (Path, "Path", "pathlib.Path"):
            kwargs["type"] = Path
        elif inner in (int, "int"):
            kwargs["type"] = int
        elif inner in (float, "float"):
            kwargs["type"] = float
        elif inner in (str, "str"):
            kwargs["type"] = str
        parser.add_argument(pname, **kwargs)
    elif annotation in (int, float, str):
        parser.add_argument(pname, type=annotation, help=pname)
    elif annotation is Path:
        parser.add_argument(pname, type=Path, help=pname)
    elif _is_literal_annotation(annotation):
        # _is_literal_annotation 为 True 时 __args__ 一定存在且非空
        kwargs = {"help": pname, "choices": list(_literal_choices(annotation))}
        parser.add_argument(pname, **kwargs)
    else:
        parser.add_argument(pname, help=pname)


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
    parser = argparse.ArgumentParser(prog=prog, description=description)
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, param.annotation)
        if param.default is inspect.Parameter.empty:
            _add_positional_arg(parser, pname, annotation)
        else:
            _add_optional_arg(parser, pname, annotation, param.default)
    _add_global_options(parser)
    return parser


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    """为 parser 添加全局选项（--dry-run / --quiet / --strategy）。"""
    parser.add_argument("--dry-run", action="store_true", default=False, help="仅打印执行计划，不执行")
    parser.add_argument("-q", "--quiet", action="store_true", default=False, help="减少输出")
    parser.add_argument(
        "--strategy",
        default=None,
        help="执行策略 (sequential/thread/async/dependency)",
    )
