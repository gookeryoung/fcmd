"""兼容 facade：参数解析模块已拆到 :mod:`parser` / :mod:`schema` / :mod:`spec`。

保留旧导入路径（``fcmd.core.toolkit.args`` 仍可用）。
"""

from __future__ import annotations

from .parser import (  # noqa: F401
    _PARAM_CLI_ALIASES,
    _add_global_options,
    _add_optional_arg,
    _add_positional_arg,
    _build_parser_for_tool,
    _is_list_annotation,
    _is_literal_annotation,
    _list_inner_type,
    _literal_choices,
    _resolve_hints,
    _resolve_list_inner_type,
    _unwrap_optional,
)
from .schema import (  # noqa: F401
    _extract_param_help,
    _jsonable_default,
    _type_label,
    param_schema,
)
from .spec import (  # noqa: F401
    ToolExitCode,
    ToolSpec,
    ToolStep,
    _noop,
    _normalize_steps,
    step,
)
