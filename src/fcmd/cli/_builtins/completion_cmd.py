"""``fcmd completion`` 内建命令：生成 shell 补全脚本。"""

from __future__ import annotations

import argparse
import sys
from typing import Any

from fcmd.cli._completion_scripts import gen_bash_script, gen_fish_script, gen_zsh_script
from fcmd.cli._discovery import aliases_for, import_all_tool_modules, tool_names

__all__ = ["run"]


def run(argv: list[str]) -> int:
    """``fcmd completion --shell bash|zsh|fish``。

    生成 shell 补全脚本到 stdout，可重定向安装::

        eval "$(fcmd completion --shell bash)"       # bash
        eval "$(fcmd completion --shell zsh)"         # zsh
        fcmd completion --shell fish | source         # fish

    脚本为静态生成（嵌入当前工具/子命令名），新增工具后需重新生成。
    """
    parser = argparse.ArgumentParser(
        prog="fcmd completion",
        description="生成 shell 补全脚本",
    )
    parser.add_argument(
        "--shell",
        choices=("bash", "zsh", "fish"),
        default="bash",
        help="目标 shell（默认 bash）",
    )
    if not argv:
        parser.print_help()
        return 1
    parsed = parser.parse_args(argv)

    # 收集所有工具数据：触发模块导入以填充注册表
    tools_data = _collect_completion_data()

    if parsed.shell == "bash":
        script = gen_bash_script(tools_data)
    elif parsed.shell == "zsh":
        script = gen_zsh_script(tools_data)
    else:
        script = gen_fish_script(tools_data)
    sys.stdout.write(script)
    sys.stdout.flush()
    return 0


def _collect_completion_data() -> list[dict[str, Any]]:
    """收集全部工具的补全数据：名称、别名、子命令列表。"""
    from fcmd.apis.toolkit import _TOOL_REGISTRY

    # 触发全部工具模块导入
    import_all_tool_modules()

    result: list[dict[str, Any]] = []
    for tool_name in tool_names():
        aliases = aliases_for(tool_name)
        subs: list[tuple[str, str]] = []
        registry = _TOOL_REGISTRY.get(tool_name, {})
        for sc, spec in sorted(
            ((sc, spec) for sc, spec in registry.items() if sc is not None and not spec.hidden),
            key=lambda x: str(x[0]),
        ):
            subs.append((str(sc), spec.help or ""))
        result.append({"name": tool_name, "aliases": aliases, "subs": subs})
    return result
