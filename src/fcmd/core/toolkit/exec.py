"""兼容 facade：执行模块已拆到 :mod:`plan` / :mod:`routing` / :mod:`execute` / :mod:`present`。

保留旧导入路径（``fcmd.core.toolkit.exec`` 仍可用）。
"""

from __future__ import annotations

from .execute import (  # noqa: F401
    _build_task_spec,
    _execute_tool_tasks,
    _expand_cmd_placeholders,
    _expand_value,
    _value_to_cmd_str,
)
from .plan import (  # noqa: F401
    _collect_order,
    _collect_with_deps,
    _has_function_logic,
    _is_aggregate,
    _merge_step_kwargs,
    _plan_steps,
    _run_preconditions,
)
from .present import (  # noqa: F401
    _describe_exception,
    _display_label,
    _make_progress_printer,
    _print_subcommands,
    _print_task_summary,
    _report_tool_failure,
)
from .routing import (  # noqa: F401
    _argparse_error_detail,
    _json_requested,
    _parse_arguments,
    _parse_tool_args,
    _resolve_tool_target,
)
from .spec import ToolSpec, _task_name  # noqa: F401
