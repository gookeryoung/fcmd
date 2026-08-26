"""shell 补全脚本生成器。

为 ``fcmd completion --shell bash|zsh|fish`` 提供三种 shell 的静态补全脚本生成。
本模块下划线开头，``ensure_tools_discovered`` 会跳过它（非工具模块）。

每个生成函数接收 ``tools_data``：由
:func:`fcmd.cli._builtins.completion_cmd._collect_completion_data` 收集的
工具数据列表，每项形如::

    {"name": "pymake", "aliases": ["pm"], "subs": [("b", "构建分发包"), ...]}

生成脚本为静态（嵌入当前工具/子命令名），新增工具后需重新生成。
"""

from __future__ import annotations

from typing import Any

from fcmd.cli._common import _BUILTIN_COMMANDS

__all__ = ["gen_bash_script", "gen_fish_script", "gen_zsh_script"]


def gen_bash_script(tools_data: list[dict[str, Any]]) -> str:
    """生成 bash 补全脚本。"""
    # 第一层：内建命令 + 工具名 + 别名 + 全局选项
    first_words: list[str] = [*list(_BUILTIN_COMMANDS), "--version", "-V"]
    for tool in tools_data:
        first_words.append(tool["name"])
        first_words.extend(tool["aliases"])
    first_words_str = " ".join(first_words)

    # 每个工具的子命令 case 分支
    case_branches: list[str] = []
    for tool in tools_data:
        if not tool["subs"]:
            continue
        # 工具名 + 别名共用同一组子命令
        names = [tool["name"]] + tool["aliases"]
        pattern = "|".join(names)
        subs_str = " ".join(sc for sc, _ in tool["subs"])
        case_branches.append(
            f'            {pattern})\n                COMPREPLY=($(compgen -W "{subs_str}" -- "$cur")) ;;'
        )
    case_body = "\n".join(case_branches) if case_branches else "            *) ;;"

    return (
        "# fcmd bash 补全脚本\n"
        '# 安装: eval "$(fcmd completion --shell bash)"\n'
        "_fcmd_complete() {\n"
        '    local cur="${COMP_WORDS[COMP_CWORD]}"\n'
        "    if [ $COMP_CWORD -eq 1 ]; then\n"
        f'        COMPREPLY=($(compgen -W "{first_words_str}" -- "$cur"))\n'
        "    elif [ $COMP_CWORD -ge 2 ]; then\n"
        '        local tool="${COMP_WORDS[1]}"\n'
        '        case "$tool" in\n'
        f"{case_body}\n"
        "        esac\n"
        "    fi\n"
        "}\n"
        "complete -F _fcmd_complete fcmd\n"
    )


def gen_zsh_script(tools_data: list[dict[str, Any]]) -> str:
    """生成 zsh 补全脚本。"""
    # 第一层命令列表
    cmd_lines: list[str] = []
    for cmd in _BUILTIN_COMMANDS:
        cmd_lines.append(f"'{cmd}'")
    for tool in tools_data:
        desc = tool["name"]
        cmd_lines.append(f"'{tool['name']}:{desc}'")
        for alias in tool["aliases"]:
            cmd_lines.append(f"'{alias}:{desc}'")
    cmd_lines.append("'--version:版本号'")
    commands_str = "\n        ".join(cmd_lines)

    # 子命令分支
    sub_blocks: list[str] = []
    for tool in tools_data:
        if not tool["subs"]:
            continue
        names = [tool["name"]] + tool["aliases"]
        pattern = "|".join(names)
        sub_lines = []
        for sc, help_text in tool["subs"]:
            sub_lines.append(f"'{sc}:{help_text}'")
        subs_str = "\n                ".join(sub_lines)
        sub_blocks.append(
            f"            ({pattern})\n"
            f"                local -a subs=({subs_str})\n"
            f"                _describe 'subcommand' subs ;;"
        )
    sub_body = "\n".join(sub_blocks) if sub_blocks else "            (*) ;;"

    return (
        "#compdef fcmd\n"
        "# fcmd zsh 补全脚本\n"
        '# 安装: eval "$(fcmd completion --shell zsh)"\n'
        "_fcmd() {\n"
        "    local -a commands\n"
        "    commands=(\n"
        f"        {commands_str}\n"
        "    )\n"
        "    _arguments -C \\\n"
        "        '1: :->cmd' \\\n"
        "        '*::arg:->args'\n"
        "    case $state in\n"
        "        cmd)\n"
        "            _describe 'command' commands ;;\n"
        "        args)\n"
        "            case ${words[1]} in\n"
        f"{sub_body}\n"
        "            esac ;;\n"
        "    esac\n"
        "}\n"
        '_fcmd "$@"\n'
    )


def gen_fish_script(tools_data: list[dict[str, Any]]) -> str:
    """生成 fish 补全脚本。"""
    lines: list[str] = ["# fcmd fish 补全脚本", "# 安装: fcmd completion --shell fish | source"]
    # 第一层
    for cmd in _BUILTIN_COMMANDS:
        lines.append(f"complete -c fcmd -f -n '__fish_use_subcommand' -a '{cmd}'")
    for tool in tools_data:
        lines.append(f"complete -c fcmd -f -n '__fish_use_subcommand' -a '{tool['name']}'")
        for alias in tool["aliases"]:
            lines.append(f"complete -c fcmd -f -n '__fish_use_subcommand' -a '{alias}'")
    # 子命令层
    for tool in tools_data:
        if not tool["subs"]:
            continue
        names = [tool["name"]] + tool["aliases"]
        seen_cond = "__fish_seen_subcommand_from " + " ".join(names)
        for sc, help_text in tool["subs"]:
            lines.append(f"complete -c fcmd -f -n '{seen_cond}' -a '{sc}' -d '{help_text}'")
    return "\n".join(lines) + "\n"
