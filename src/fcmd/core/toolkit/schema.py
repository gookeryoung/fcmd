"""参数 schema：把函数签名投影成人类 / 机器共读的结构（fcmd info 使用）。"""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .parser import (
    _PARAM_CLI_ALIASES,
    _extract_param_help,
    _is_list_annotation,
    _is_literal_annotation,
    _list_inner_type,
    _literal_choices,
    _resolve_hints,
    _unwrap_optional,
)
from .spec import ToolSpec

_TYPE_LABELS: Mapping[Any, str] = {bool: "bool", int: "int", float: "float", str: "str", Path: "path"}


def _type_label(annotation: Any) -> str:
    """把类型注解映射为 schema 中的粗粒度类型名。"""
    annotation = _unwrap_optional(annotation)
    if _is_literal_annotation(annotation):
        return "literal"
    if _is_list_annotation(annotation):
        return f"list[{_type_label(_list_inner_type(annotation))}]"
    return _TYPE_LABELS.get(annotation, "str")


def _jsonable_default(value: Any) -> Any:
    """把参数默认值转换为 JSON 可序列化的形式。"""
    if value is inspect.Parameter.empty or value is None:
        return None
    if isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable_default(item) for item in value]
    return str(value)


def param_schema(spec: ToolSpec) -> list[dict[str, Any]]:
    """从函数签名推导参数 schema（与 :func:`_build_parser_for_tool` 同口径）。

    每条记录包含参数名、命令行写法（含别名）、类型、是否必填、默认值、
    ``Literal`` 取值枚举、是否可重复，以及 docstring / ``param_help`` 中的说明。
    人类可读的 ``fcmd info`` 与机器可读的 ``fcmd info --json`` 共用这份数据。
    """
    hints = _resolve_hints(spec.func)
    sig = inspect.signature(spec.func)
    param_help = _extract_param_help(spec.func)
    if spec.param_help:
        param_help.update(spec.param_help)
    params: list[dict[str, Any]] = []
    for pname, param in sig.parameters.items():
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        annotation = hints.get(pname, param.annotation)
        bare = _unwrap_optional(annotation)
        required = param.default is inspect.Parameter.empty
        default = param.default if not required else None
        dashed = f"--{pname.replace('_', '-')}"
        if isinstance(default, bool) and default is True:
            dashed = f"--no-{pname.replace('_', '-')}"
        aliases = list(_PARAM_CLI_ALIASES.get(pname, ()))
        cli = aliases[0] if aliases else (pname if required else dashed)
        params.append(
            {
                "name": pname,
                "cli": cli,
                "aliases": [dashed, *aliases[1:]] if aliases else [],
                "type": _type_label(annotation),
                "required": required,
                "default": _jsonable_default(default),
                "choices": list(_literal_choices(bare)) if _is_literal_annotation(bare) else None,
                "repeatable": _is_list_annotation(bare),
                "help": param_help.get(pname, ""),
            }
        )
    return params
