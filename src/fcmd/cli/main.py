"""fcmd CLI 主入口：FcmdApp 路由。

通过 ``fcmd <tool> [command] [options]`` 调用所有工具，
工具定义在 ``fcmd.cli`` 包中，每个模块用 ``@fx.tool`` 装饰器注册。

工具发现
--------
``fcmd.cli`` 包下每个非 ``main`` / 非 ``_`` 前缀的模块即一个工具，
模块名即工具名。模块内可选定义 ``__tool_aliases__: list[str]`` 声明别名。
首次调用 ``FcmdApp.run()`` 时用 ``pkgutil.iter_modules`` 扫描并导入所有
工具模块，``import fcmd`` 冷启动不受影响。

用法
----
    fcmd                  # 列出所有可用工具
    fcmd pymake           # 查看 pymake 工具的子命令
    fcmd pymake b         # 调用 pymake 的 b 子命令
    fcmd --version        # 输出版本号
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import importlib
import pkgutil
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from fcmd import __version__
from fcmd.apis.toolkit import _TOOL_REGISTRY, build_tool_graph, run_tool
from fcmd.console import get_console
from fcmd.errors import FcmdError

__all__ = ["FcmdApp", "main"]


# 工具别名 → 规范名（由 _ensure_tools_discovered 懒填充）
_TOOL_ALIASES: dict[str, str] = {}

# 规范工具名 → 模块路径（由 _ensure_tools_discovered 懒填充）
_TOOL_MODULES: dict[str, str] = {}

# 发现标志：True 表示已扫描过 fcmd.cli 包
_TOOLS_DISCOVERED = False

# 内建命令名（不通过 @fx.tool 注册，由 FcmdApp 直接处理）
_BUILTIN_COMMANDS: tuple[str, ...] = ("graph", "info", "completion", "yaml", "env", "doctor")


def _ensure_tools_discovered() -> None:
    """首次调用时扫描 ``fcmd.cli`` 包，发现工具模块并填充注册表。

    幂等：后续调用直接返回。用 ``setdefault`` 填充，不覆盖测试通过
    ``monkeypatch.setitem`` 注入的键。扫描时导入模块以读取
    ``__tool_aliases__`` 并触发 ``@fx.tool`` 注册。
    """
    global _TOOLS_DISCOVERED  # noqa: PLW0603
    if _TOOLS_DISCOVERED:
        return
    _TOOLS_DISCOVERED = True

    # 懒导入 fcmd.cli 以访问 __path__，避免 import fcmd 时触发
    import fcmd.cli as cli_pkg

    for _finder, name, _ispkg in pkgutil.iter_modules(cli_pkg.__path__):
        # 排除入口模块、私有模块、包自身
        if name.startswith("_") or name == "main":
            continue
        module_path = f"fcmd.cli.{name}"
        tool_name = name
        _TOOL_MODULES.setdefault(tool_name, module_path)
        _TOOL_ALIASES.setdefault(tool_name, tool_name)
        try:
            mod = importlib.import_module(module_path)
        except ImportError:
            continue
        # 读取模块声明的别名
        aliases = getattr(mod, "__tool_aliases__", ())
        for alias in aliases:
            _TOOL_ALIASES.setdefault(alias, tool_name)


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器（保留 P0 兼容）。"""
    parser = argparse.ArgumentParser(
        prog="fcmd",
        description="极速 Python 工具集应用：DAG 任务调度 + 组合 CLI。",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    return parser


class FcmdApp:
    """fcmd 统一入口应用。

    路由 ``fcmd <tool> [command]`` 到 ``@fx.tool`` 注册的工具。
    """

    def __init__(self, argv: Sequence[str] | None = None) -> None:
        self._argv: list[str] = list(argv) if argv is not None else sys.argv[1:]

    def run(self) -> int:
        """主入口，返回退出码。"""
        _ensure_tools_discovered()
        if not self._argv or self._argv[0] in ("--help", "-h"):
            self._list_tools()
            return 0

        first = self._argv[0]
        if first in ("--version", "-V"):
            get_console().print(f"fcmd [bold cyan]{__version__}[/bold cyan]")
            return 0

        # 内建命令（graph/info/...）优先于工具路由
        if first in _BUILTIN_COMMANDS:
            return self._run_builtin(first, self._argv[1:])

        rest = self._argv[1:]
        resolved = self._resolve_tool(first)
        if resolved is None:
            self._print_unknown_tool(first)
            return 1

        return self._run_tool(resolved, rest)

    # ------------------------------------------------------------------ #
    # 内建命令
    # ------------------------------------------------------------------ #
    def _run_builtin(self, name: str, argv: list[str]) -> int:  # noqa: PLR0911
        """分发内建命令。"""
        if name == "graph":
            return self._builtin_graph(argv)
        if name == "info":
            return self._builtin_info(argv)
        if name == "completion":
            return self._builtin_completion(argv)
        if name == "yaml":
            return self._builtin_yaml(argv)
        if name == "env":
            return self._builtin_env(argv)
        if name == "doctor":
            return self._builtin_doctor(argv)
        get_console().print(f"[red]错误:[/red] 未知内建命令 {name!r}")
        return 1

    def _builtin_graph(self, argv: list[str]) -> int:
        """``fcmd graph <tool> <subcommand> [--format=mermaid|layers|describe]``。

        可视化工具子命令的 DAG 执行计划，不执行任务。

        格式：
        - ``mermaid``（默认）：Mermaid graph 定义，可粘贴到 mermaid.live
        - ``layers``：拓扑分层列表（每层可并行）
        - ``describe``：人类可读多行摘要（Graph.describe）
        """
        parser = argparse.ArgumentParser(
            prog="fcmd graph",
            description="可视化工具子命令的 DAG 执行计划",
        )
        parser.add_argument("tool", help="工具名（如 pymake）")
        parser.add_argument("subcommand", nargs="?", default=None, help="目标子命令（如 tc/all）")
        parser.add_argument(
            "--format",
            choices=("mermaid", "layers", "describe"),
            default="mermaid",
            help="输出格式（默认 mermaid）",
        )
        if not argv:
            parser.print_help()
            return 1
        parsed = parser.parse_args(argv)

        resolved = self._resolve_tool(parsed.tool)
        if resolved is None:
            self._print_unknown_tool(parsed.tool)
            return 1

        try:
            graph = build_tool_graph(resolved, parsed.subcommand)
        except FcmdError as e:
            get_console().print(f"[red]错误:[/red] {e}")
            return 1

        if parsed.format == "mermaid":
            get_console().print(graph.to_mermaid(), end="")
        elif parsed.format == "layers":
            layers = graph.layers()
            for idx, layer in enumerate(layers, 1):
                get_console().print(f"Layer {idx}: {layer}")
        else:  # describe
            get_console().print(graph.describe())
        return 0

    def _builtin_info(self, argv: list[str]) -> int:
        """``fcmd info <tool> [subcommand]``。

        展示工具或子命令的元信息（不执行）：

        - ``fcmd info``：列出全部内建命令与已注册工具
        - ``fcmd info <tool>``：列出工具的所有子命令（含 hidden）及
          help / needs / strategy / cmd 摘要
        - ``fcmd info <tool> <subcommand>``：展示该子命令的完整 ToolSpec
          字段（cmd / needs / strategy / cwd / hidden / env / retry / timeout）
        """
        parser = argparse.ArgumentParser(
            prog="fcmd info",
            description="展示工具或子命令的元信息",
        )
        parser.add_argument("tool", nargs="?", default=None, help="工具名（如 pymake）")
        parser.add_argument("subcommand", nargs="?", default=None, help="子命令名（如 tc）")
        if not argv:
            self._info_overview()
            return 0
        parsed = parser.parse_args(argv)

        resolved = self._resolve_tool(parsed.tool)
        if resolved is None:
            self._print_unknown_tool(parsed.tool)
            return 1

        subs = self._load_tool_subs(resolved)
        if subs is None:
            return 1

        if parsed.subcommand is None:
            self._info_tool(resolved, subs)
            return 0

        # 子命令详情：subcommand 可能是 None（单命令工具），用 .get 兼容
        spec = subs.get(parsed.subcommand)
        if spec is None:
            get_console().print(f"[red]错误:[/red] 工具 {resolved!r} 没有子命令 {parsed.subcommand!r}")
            return 1
        self._info_subcommand(resolved, spec)
        return 0

    def _load_tool_subs(self, tool_name: str) -> dict[str | None, Any] | None:
        """加载工具模块并返回子命令字典，失败时打印错误并返回 None。"""
        if tool_name in _TOOL_MODULES:
            try:
                importlib.import_module(_TOOL_MODULES[tool_name])
            except ImportError as e:
                get_console().print(f"[red]错误:[/red] 加载工具 {tool_name!r} 失败: {e}")
                return None
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        if tool_name not in _TOOL_REGISTRY:
            get_console().print(f"[red]错误:[/red] 工具 {tool_name!r} 未注册")
            return None
        return _TOOL_REGISTRY[tool_name]

    def _info_overview(self) -> None:
        """``fcmd info`` 无参数：列出内建命令与工具。"""
        from rich.table import Table

        console = get_console()
        console.print("[bold]内建命令:[/bold]")
        for cmd_name in _BUILTIN_COMMANDS:
            console.print(f"  [cyan]fcmd {cmd_name}[/cyan]")

        console.print("\n[bold]已注册工具:[/bold]")
        table = Table(show_header=True, header_style="bold", show_lines=False)
        table.add_column("工具", style="cyan", no_wrap=True)
        table.add_column("别名", style="dim", no_wrap=True)
        table.add_column("子命令数", justify="right", no_wrap=True)
        table.add_column("说明")
        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = self._aliases_for(tool_name)
            # 触发模块导入以统计子命令数
            if tool_name in _TOOL_MODULES:
                with contextlib.suppress(ImportError):
                    importlib.import_module(_TOOL_MODULES[tool_name])
            from fcmd.apis.toolkit import _TOOL_REGISTRY

            subs = _TOOL_REGISTRY.get(tool_name, {})
            visible = sum(1 for sc in subs if sc is not None and not subs[sc].hidden)
            hidden = sum(1 for sc in subs if sc is not None and subs[sc].hidden)
            count_str = f"{visible} (+{hidden} hidden)" if hidden else str(visible)
            table.add_row(tool_name, ", ".join(aliases), count_str, self._tool_description(tool_name))
        console.print(table)
        console.print("\n[dim]用法: fcmd info <tool> [subcommand][/dim]")

    def _info_tool(self, tool_name: str, subs: dict[str | None, Any]) -> None:
        """``fcmd info <tool>``：列出工具的全部子命令。"""
        from rich.table import Table

        console = get_console()
        console.print(f"[bold cyan]{tool_name}[/bold cyan] 子命令:")
        table = Table(show_header=True, header_style="bold", show_lines=False)
        table.add_column("子命令", style="cyan", no_wrap=True)
        table.add_column("类型", style="dim", no_wrap=True)
        table.add_column("needs", style="yellow", no_wrap=True)
        table.add_column("strategy", style="magenta", no_wrap=True)
        table.add_column("说明")
        # 排序：visible 在前（按名排序），hidden 在后（按名排序）
        visible = sorted((sc, spec) for sc, spec in subs.items() if sc is not None and not spec.hidden)
        hidden = sorted((sc, spec) for sc, spec in subs.items() if sc is not None and spec.hidden)
        for sc, spec in visible + hidden:
            kind = self._spec_kind(spec)
            needs_str = ", ".join(spec.needs) if spec.needs else "-"
            strategy_str = spec.strategy or "-"
            help_str = spec.help or ""
            if spec.hidden:
                sc_str = f"[dim]{sc} (hidden)[/dim]"
                help_str = f"[dim]{help_str}[/dim]"
            else:
                sc_str = str(sc)
            table.add_row(sc_str, kind, needs_str, strategy_str, help_str)
        console.print(table)

    def _info_subcommand(self, tool_name: str, spec: Any) -> None:
        """``fcmd info <tool> <subcommand>``：展示 ToolSpec 完整字段。"""
        console = get_console()
        sc_name = spec.subcommand if spec.subcommand is not None else "(single)"
        console.print(f"[bold cyan]{tool_name}[/bold cyan] / [cyan]{sc_name}[/cyan]")
        fields: list[tuple[str, str]] = [
            ("help", spec.help or "-"),
            ("description", spec.description or "-"),
            ("kind", self._spec_kind(spec)),
            ("cmd", " ".join(spec.cmd) if isinstance(spec.cmd, tuple) else (spec.cmd or "-")),
            ("needs", ", ".join(spec.needs) if spec.needs else "-"),
            ("strategy", spec.strategy or "-"),
            ("cwd", str(spec.cwd) if spec.cwd is not None else "-"),
            ("hidden", "yes" if spec.hidden else "no"),
            ("allow_upstream_skip", "yes" if spec.allow_upstream_skip else "no"),
            ("timeout", str(spec.timeout) if spec.timeout is not None else "-"),
            ("env", ", ".join(f"{k}=..." for k in spec.env) if spec.env else "-"),
            ("retry", str(spec.retry) if spec.retry is not None else "-"),
        ]
        from rich.table import Table

        table = Table(show_header=False, box=None, show_lines=False)
        table.add_column("字段", style="bold", no_wrap=True)
        table.add_column("值")
        for name, value in fields:
            table.add_row(name, value)
        console.print(table)

    @staticmethod
    def _spec_kind(spec: Any) -> str:
        """判断 ToolSpec 类型：cmd / aggregate / fn。"""
        if spec.cmd is not None:
            return "cmd"
        if spec.needs:
            return "aggregate"
        return "fn"

    # ------------------------------------------------------------------ #
    # completion 内建命令
    # ------------------------------------------------------------------ #
    def _builtin_completion(self, argv: list[str]) -> int:
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
        tools_data = self._collect_completion_data()

        if parsed.shell == "bash":
            script = self._gen_bash_script(tools_data)
        elif parsed.shell == "zsh":
            script = self._gen_zsh_script(tools_data)
        else:
            script = self._gen_fish_script(tools_data)
        sys.stdout.write(script)
        return 0

    def _collect_completion_data(self) -> list[dict[str, Any]]:
        """收集全部工具的补全数据：名称、别名、子命令列表。"""
        from fcmd.apis.toolkit import _TOOL_REGISTRY

        # 触发全部工具模块导入
        for _tool_name, module_path in list(_TOOL_MODULES.items()):
            with contextlib.suppress(ImportError):
                importlib.import_module(module_path)

        result: list[dict[str, Any]] = []
        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = self._aliases_for(tool_name)
            subs: list[tuple[str, str]] = []
            registry = _TOOL_REGISTRY.get(tool_name, {})
            for sc, spec in sorted(
                ((sc, spec) for sc, spec in registry.items() if sc is not None and not spec.hidden),
                key=lambda x: str(x[0]),
            ):
                subs.append((str(sc), spec.help or ""))
            result.append({"name": tool_name, "aliases": aliases, "subs": subs})
        return result

    @staticmethod
    def _gen_bash_script(tools_data: list[dict[str, Any]]) -> str:
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

    @staticmethod
    def _gen_zsh_script(tools_data: list[dict[str, Any]]) -> str:
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

    @staticmethod
    def _gen_fish_script(tools_data: list[dict[str, Any]]) -> str:
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

    # ------------------------------------------------------------------ #
    # yaml 内建命令
    # ------------------------------------------------------------------ #
    def _builtin_yaml(self, argv: list[str]) -> int:
        """``fcmd yaml <file> [job] [--dry-run] [--strategy S] [--verbose]``。

        从 YAML 文件加载 GitHub Actions 风格任务图并执行。

        - ``fcmd yaml deploy.yaml``：执行全部 jobs
        - ``fcmd yaml deploy.yaml build``：仅执行 build 及其依赖
        - ``fcmd yaml deploy.yaml --dry-run``：打印执行计划不执行
        - ``fcmd yaml deploy.yaml --strategy thread``：覆盖执行策略
        """
        parser = argparse.ArgumentParser(
            prog="fcmd yaml",
            description="从 YAML 文件加载并执行任务图",
        )
        parser.add_argument("file", help="YAML 文件路径")
        parser.add_argument("job", nargs="?", default=None, help="仅执行该 job 及其依赖（默认全部）")
        parser.add_argument("--dry-run", action="store_true", help="打印执行计划不执行")
        parser.add_argument(
            "--strategy",
            choices=("sequential", "thread", "async", "dependency"),
            default="dependency",
            help="执行策略（默认 dependency）",
        )
        parser.add_argument("--verbose", action="store_true", help="打印详细执行过程")
        if not argv:
            parser.print_help()
            return 1
        parsed = parser.parse_args(argv)

        from fcmd.errors import FcmdError
        from fcmd.executors import run
        from fcmd.yaml_loader import load_yaml

        try:
            graph = load_yaml(parsed.file)
        except (OSError, ValueError) as e:
            get_console().print(f"[red]错误:[/red] 加载 YAML 失败: {e}")
            return 1

        only = [parsed.job] if parsed.job else None
        try:
            report = run(
                graph,
                strategy=parsed.strategy,
                dry_run=parsed.dry_run,
                verbose=parsed.verbose,
                only=only,
            )
        except FcmdError as e:
            get_console().print(f"[red]错误:[/red] {e}")
            return 1

        if report.success:
            get_console().print("[green]YAML 任务图执行成功[/green]")
            return 0
        get_console().print("[red]YAML 任务图执行失败[/red]")
        return 1

    def _builtin_env(self, argv: list[str]) -> int:
        """``fcmd env``。

        展示当前运行环境信息（只读，用于调试与问题排查）：

        - fcmd 版本与安装路径
        - Python 版本、平台、解释器路径
        - 已注册工具数与子命令总数
        - 可选依赖（img/pdf/ocr）的安装状态与版本
        """
        parser = argparse.ArgumentParser(
            prog="fcmd env",
            description="展示当前运行环境信息",
        )
        parser.add_argument("--json", action="store_true", help="以 JSON 格式输出")
        # argv 非空但首参数为 --help 时 argparse 自动处理；空 argv 时打印信息（env 无必需参数）
        parsed = parser.parse_args(argv) if argv else parser.parse_args([])

        import platform
        import sys as _sys

        from fcmd import __version__ as fcmd_version

        # 触发全部工具模块导入以统计准确数字
        for _tool_name, module_path in list(_TOOL_MODULES.items()):
            with contextlib.suppress(ImportError):
                importlib.import_module(module_path)

        tool_count = len(set(_TOOL_ALIASES.values()))
        subcommand_total = sum(len(subs) for subs in _TOOL_REGISTRY.values())

        optional_deps = self._collect_optional_deps_status()

        fcmd_pkg_path = str(Path(__file__).parent.parent)
        cli_pkg_path = str(Path(__file__).parent)

        if parsed.json:
            import json as json_mod

            data = {
                "fcmd_version": fcmd_version,
                "fcmd_path": fcmd_pkg_path,
                "cli_path": cli_pkg_path,
                "python_version": _sys.version,
                "python_executable": _sys.executable,
                "platform": _sys.platform,
                "platform_info": platform.platform(),
                "tool_count": tool_count,
                "subcommand_total": subcommand_total,
                "optional_deps": optional_deps,
            }
            sys.stdout.write(json_mod.dumps(data, ensure_ascii=False, indent=2))
            return 0

        console = get_console()
        console.print(f"[bold cyan]fcmd v{fcmd_version}[/bold cyan] 环境信息\n")

        console.print("[bold]项目[/bold]")
        console.print(f"  fcmd 版本     [cyan]{fcmd_version}[/cyan]")
        console.print(f"  fcmd 路径     [dim]{fcmd_pkg_path}[/dim]")
        console.print(f"  工具发现路径  [dim]{cli_pkg_path}[/dim]")

        console.print("\n[bold]运行时[/bold]")
        console.print(f"  Python 版本   [cyan]{_sys.version.split()[0]}[/cyan]")
        console.print(f"  平台         [cyan]{_sys.platform}[/cyan]")
        console.print(f"  平台信息     [dim]{platform.platform()}[/dim]")
        console.print(f"  解释器       [dim]{_sys.executable}[/dim]")

        console.print("\n[bold]工具[/bold]")
        console.print(f"  已注册工具数  [cyan]{tool_count}[/cyan]")
        console.print(f"  已注册子命令  [cyan]{subcommand_total}[/cyan]")

        console.print("\n[bold]可选依赖[/bold]")
        if not optional_deps:
            console.print("  [dim](无)[/dim]")
        else:
            from rich.table import Table

            table = Table(show_header=True, header_style="bold", show_lines=False)
            table.add_column("extra", style="cyan", no_wrap=True)
            table.add_column("包名", no_wrap=True)
            table.add_column("状态", justify="center", no_wrap=True)
            table.add_column("版本")
            for dep in optional_deps:
                status_str = "[green]已安装[/green]" if dep["installed"] else "[red]未安装[/red]"
                table.add_row(dep["extra"], dep["package"], status_str, dep.get("version", ""))
            console.print(table)

        console.print("\n[dim]提示: 运行 'fcmd doctor' 进行环境健康检查[/dim]")
        return 0

    def _builtin_doctor(self, argv: list[str]) -> int:
        """``fcmd doctor``。

        环境健康诊断（只读，输出 OK/FAIL 状态表格）：

        - Python 版本 ≥ 3.8
        - fcmd 核心模块导入正常
        - 工具模块全部可正常导入（统计失败数）
        - 可选依赖（img/pdf/ocr）状态
        - PATH 中的常用外部命令（git/uv/python）可用性

        退出码：全部通过返回 0，有失败项返回 1。
        """
        parser = argparse.ArgumentParser(
            prog="fcmd doctor",
            description="环境健康诊断",
        )
        parser.parse_args(argv) if argv else parser.parse_args([])

        checks: list[dict[str, Any]] = []

        # 1. Python 版本 ≥ 3.8
        py_ok = sys.version_info >= (3, 8)
        checks.append(
            {
                "item": "Python 版本 ≥ 3.8",
                "ok": py_ok,
                "detail": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "fix": "升级 Python 至 3.8+" if not py_ok else "",
            }
        )

        # 2. fcmd 核心模块导入
        try:
            import fcmd  # noqa: F401

            core_ok = True
            core_detail = ""
        except ImportError as e:  # pragma: no cover - fcmd 已导入才能执行到此处，分支不可达
            core_ok = False
            core_detail = str(e)
        checks.append(
            {
                "item": "fcmd 核心导入",
                "ok": core_ok,
                "detail": core_detail,
                "fix": "重装 fcmd: pip install --force-reinstall fcmd" if not core_ok else "",
            }
        )

        # 3. 工具模块全部可导入
        _ensure_tools_discovered()
        failed_tools: list[str] = []
        for tool_name, module_path in list(_TOOL_MODULES.items()):
            try:
                importlib.import_module(module_path)
            except ImportError:
                failed_tools.append(tool_name)
        tool_total = len(_TOOL_MODULES)
        tool_ok = not failed_tools
        checks.append(
            {
                "item": "工具模块扫描",
                "ok": tool_ok,
                "detail": f"{tool_total} 个工具" + (f"，失败: {', '.join(failed_tools)}" if failed_tools else ""),
                "fix": "检查失败工具模块的依赖与语法" if failed_tools else "",
            }
        )

        # 4. 可选依赖检查
        optional_deps = self._collect_optional_deps_status()
        for dep in optional_deps:
            checks.append(
                {
                    "item": f"可选依赖 {dep['extra']} ({dep['package']})",
                    "ok": dep["installed"],
                    "detail": dep.get("version", "") or "未安装",
                    "fix": f"pip install fcmd[{dep['extra']}]" if not dep["installed"] else "",
                }
            )

        # 5. PATH 中的常用外部命令
        import shutil

        for cmd in ("git", "uv", "python", "pip"):
            cmd_path = shutil.which(cmd)
            checks.append(
                {
                    "item": f"PATH: {cmd}",
                    "ok": cmd_path is not None,
                    "detail": cmd_path or "未找到",
                    "fix": f"安装 {cmd} 并加入 PATH" if cmd_path is None else "",
                }
            )

        # 输出
        from rich.table import Table

        console = get_console()
        console.print("[bold cyan]fcmd 环境诊断[/bold cyan]\n")
        table = Table(show_header=True, header_style="bold", show_lines=False)
        table.add_column("检查项", style="cyan", no_wrap=True)
        table.add_column("状态", justify="center", no_wrap=True)
        table.add_column("详情")
        for c in checks:
            status = "[green]OK[/green]" if c["ok"] else "[red]FAIL[/red]"
            detail = c["detail"]
            if not c["ok"] and c["fix"]:
                detail = f"{detail}\n[dim]修复: {c['fix']}[/dim]"
            table.add_row(c["item"], status, detail)
        console.print(table)

        passed = sum(1 for c in checks if c["ok"])
        total = len(checks)
        if passed == total:
            console.print(f"\n[green]诊断结果: {passed}/{total} 全部通过[/green]")
            return 0
        console.print(f"\n[red]诊断结果: {passed}/{total} 通过，{total - passed} 项失败[/red]")
        return 1

    def _collect_optional_deps_status(self) -> list[dict[str, Any]]:
        """收集可选依赖的安装状态与版本。

        返回列表，每项包含 extra / package / installed / version（已安装时）。
        """
        deps: list[dict[str, Any]] = []
        for extra, package in (
            ("img", "PIL"),
            ("pdf", "fitz"),
            ("pdf", "pypdf"),
            ("ocr", "pytesseract"),
        ):
            try:
                mod = __import__(package)
                version = getattr(mod, "__version__", "")
                deps.append({"extra": extra, "package": package, "installed": True, "version": version})
            except ImportError:
                deps.append({"extra": extra, "package": package, "installed": False, "version": ""})
        return deps

    def _list_tools(self) -> None:
        """rich 表格列出所有可用工具。"""
        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text

        console = get_console()
        console.print(
            Panel(
                Text(f"fcmd v{__version__}", style="bold cyan", justify="center"),
                subtitle="[dim]fcmd <tool> [command] [options][/dim]",
            )
        )

        table = Table(title="可用工具", show_header=True, header_style="bold", show_lines=False)
        table.add_column("命令", style="cyan", no_wrap=True)
        table.add_column("别名", style="dim", no_wrap=True)
        table.add_column("说明")

        for tool_name in sorted(set(_TOOL_ALIASES.values())):
            aliases = self._aliases_for(tool_name)
            table.add_row(f"fcmd {tool_name}", ", ".join(aliases), self._tool_description(tool_name))

        console.print(table)

        console.print("\n[bold]示例:[/bold]")
        console.print("  [cyan]fcmd pymake[/cyan]              # 查看 pymake 子命令")
        console.print("  [cyan]fcmd pymake b[/cyan]            # 构建项目")
        console.print("  [cyan]fcmd pymake tc[/cyan]           # 类型检查（聚合）")
        console.print("  [cyan]fcmd info pymake[/cyan]         # 查看 pymake 元信息")
        console.print("  [cyan]fcmd graph pymake tc[/cyan]     # 可视化 DAG（Mermaid）")
        console.print("  [cyan]fcmd env[/cyan]                 # 查看环境信息")
        console.print("  [cyan]fcmd doctor[/cyan]              # 环境健康诊断")
        console.print("  [cyan]fcmd --version[/cyan]           # 查看版本")

    def _aliases_for(self, canonical: str) -> list[str]:
        """获取工具的别名（不含规范名本身）。"""
        return sorted(a for a, t in _TOOL_ALIASES.items() if t == canonical and a != canonical)

    def _tool_description(self, tool_name: str) -> str:
        """获取工具描述（从 _TOOL_REGISTRY 中已注册 ToolSpec 的 description/help）。"""
        # 触发模块导入以注册工具
        if tool_name in _TOOL_MODULES:
            try:
                importlib.import_module(_TOOL_MODULES[tool_name])
            except ImportError:
                return ""

        if tool_name not in _TOOL_REGISTRY:
            return ""

        subs = _TOOL_REGISTRY[tool_name]
        for spec in subs.values():
            if spec.description:
                return spec.description
        for spec in subs.values():
            if not spec.hidden and spec.help:
                return spec.help
        return ""

    # ------------------------------------------------------------------ #
    # 路由
    # ------------------------------------------------------------------ #
    def _resolve_tool(self, name: str) -> str | None:
        """解析工具名，返回规范名或 None。"""
        return _TOOL_ALIASES.get(name)

    def _print_unknown_tool(self, name: str) -> None:
        """打印未知工具错误 + 模糊匹配建议。"""
        console = get_console()
        console.print(f"[red]错误:[/red] 未知工具 [yellow]{name!r}[/yellow]")
        suggestions = difflib.get_close_matches(name, list(_TOOL_ALIASES), n=3, cutoff=0.5)
        if suggestions:
            console.print(f"[dim]是否想用: {', '.join(suggestions)}[/dim]")
        console.print("[dim]运行 'fcmd' 查看可用工具列表[/dim]")

    def _run_tool(self, tool_name: str, argv: list[str]) -> int:
        """运行工具：importlib 懒加载模块触发 @tool 注册，再调 run_tool。"""
        module_path = _TOOL_MODULES.get(tool_name)
        if module_path is None:
            get_console().print(f"[red]错误:[/red] 工具 {tool_name!r} 无模块映射")
            return 1

        try:
            importlib.import_module(module_path)
        except ImportError as e:
            get_console().print(f"[red]错误:[/red] 加载工具 {tool_name!r} 失败: {e}")
            return 1

        return run_tool(tool_name, argv)


def main() -> None:
    """主入口：解析参数并执行。"""
    sys.exit(FcmdApp().run())


if __name__ == "__main__":
    main()
