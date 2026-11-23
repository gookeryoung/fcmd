"""Infras 主题子包；保留旧模块导入路径（re-export）。"""

from __future__ import annotations

from .align import *  # noqa: F403
from .ascii import *  # noqa: F403
from .case import *  # noqa: F403
from .color import *  # noqa: F403
from .color import _require_coloraide  # noqa: F401
from .diff import *  # noqa: F403
from .math import *  # noqa: F403
from .regex import *  # noqa: F403
from .textops import *  # noqa: F403

__all__ = [  # noqa: F405
    "PRINTABLE_END",
    "PRINTABLE_START",
    "align_center",
    "align_justify",
    "align_left",
    "align_right",
    "build_ascii_table",
    "char_to_code",
    "code_to_char",
    "colorize_diff",
    "compare_directories",
    "compare_directories_result",
    "compare_files",
    "compare_files_result",
    "convert_case",
    "count_text",
    "diff_stats",
    "display_width",
    "eval_expr",
    "factorial_value",
    "find_all",
    "format_directory_result",
    "hex_to_rgb",
    "hsl_to_rgb",
    "match_pattern",
    "pad_display",
    "power",
    "read_lines",
    "read_text_file",
    "replace_pattern",
    "rgb_to_hex",
    "rgb_to_hsl",
    "sort_lines",
    "split_pattern",
    "sqrt_value",
    "to_camel",
    "to_kebab",
    "to_pascal",
    "to_snake",
    "unique_lines",
]
